"""全自動接續的防呆守衛。

橋接器偵測到任務停掉時會問這支：「現在該不該自動接續？」
只有全部檢查都過才回 GO，否則停下來讓人看。

全自動最怕的不是「跑不動」，是「一直跑但沒在前進」——
所以這裡的檢查大多在判斷「上一輪到底有沒有做出東西」。

用法：
    python _autoguard.py            → 印判斷結果，exit 0=可接續 / 1=該停
    python _autoguard.py --json     → 機器可讀
"""

import argparse
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "state.db")
STATE = os.path.join(HERE, "_autoguard_state.json")
LOG = os.path.join(HERE, "autoguard.log")

# ---- 防呆門檻 ----
MAX_CHAIN = 8            # 同一條自動接續鏈最多幾輪
MAX_HOURS = 12           # 連續自動跑最多幾小時
MIN_PROGRESS_CALLS = 5   # 一輪至少要有幾次工具呼叫才算「有在做事」
COOLDOWN_SEC = 60        # 兩次接續之間至少間隔多久（防抖動迴圈）
GATE_TIMEOUT = 900       # 跑驗收關卡最多等幾秒
ZOMBIE_IDLE_MIN = 5      # session 標記為「跑著」但這麼久沒動靜 = 行程已死

# 驗收關卡：專案根有 simcheck.json 時，它宣告完成必須通過才採信
SIMCHECK = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "hermes", "skills", "embedded",
    "rtl-sim-verification", "references", "scripts", "simcheck.py")


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"chain": 0, "started": 0, "last_sid": "", "last_run": 0}


def save_state(s):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except Exception:
        pass


def latest_session():
    try:
        c = sqlite3.connect(DB)
        r = c.execute(
            "SELECT id, api_call_count, started_at, ended_at "
            "FROM sessions WHERE source IN ('cli','desktop','tui') "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        c.close()
        return r
    except Exception:
        return None


def last_assistant(sid):
    try:
        c = sqlite3.connect(DB)
        r = c.execute(
            "SELECT coalesce(content,'') FROM messages "
            "WHERE session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
        c.close()
        return (r[0] or "") if r else ""
    except Exception:
        return ""


def files_touched(sid):
    """這一輪實際寫過幾個檔案 —— 沒寫東西就是沒進展。"""
    try:
        c = sqlite3.connect(DB)
        rows = c.execute(
            "SELECT coalesce(content,'') FROM messages "
            "WHERE session_id=? AND role='tool'", (sid,)).fetchall()
        c.close()
    except Exception:
        return 0
    seen = set()
    for (t,) in rows:
        for m in re.finditer(r'"resolved_path":\s*"([^"]+)"', t):
            seen.add(m.group(1))
    return len(seen)


def find_project(sid):
    """從它這輪寫過的檔案往上找專案根目錄。

    跟 hermes_bridge._find_project 同一套判準（有 HANDOFF.md 或
    ARCHITECTURE.md 的那層），不用 session 的 cwd —— 那是 CLI 的
    啟動目錄，通常不是專案所在。
    """
    try:
        c = sqlite3.connect(DB)
        rows = c.execute(
            "SELECT coalesce(content,'') FROM messages "
            "WHERE session_id=? AND role='tool' "
            "ORDER BY rowid DESC LIMIT 120", (sid,)).fetchall()
        c.close()
    except Exception:
        return ""
    roots = {}
    for (t,) in rows:
        for m in re.finditer(r'"resolved_path":\s*"([^"]+)"', t):
            d = os.path.dirname(m.group(1).replace("\\\\", "\\"))
            for _ in range(4):
                if not d or not os.path.isdir(d):
                    break
                if os.path.exists(os.path.join(d, "HANDOFF.md")) or \
                   os.path.exists(os.path.join(d, "ARCHITECTURE.md")):
                    roots[d] = roots.get(d, 0) + 1
                    break
                d = os.path.dirname(d)
    return max(roots, key=roots.get) if roots else ""


def _last_activity(sid):
    """這個 session 最後一次有動靜是什麼時候（訊息或 last_activity_at）。"""
    try:
        c = sqlite3.connect(DB)
        r = c.execute(
            "SELECT COALESCE(MAX(timestamp), 0) FROM messages "
            "WHERE session_id=?", (sid,)).fetchone()
        r2 = c.execute(
            "SELECT COALESCE(last_activity_at, started_at) FROM sessions "
            "WHERE id=?", (sid,)).fetchone()
        c.close()
        return max(r[0] if r else 0, r2[0] if r2 else 0)
    except Exception:
        return 0


def _upstream_busy():
    """上游 llama-server 有沒有在推理。連不到就當作沒在跑。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8001/slots",
                                    timeout=5) as r:
            slots = json.loads(r.read().decode("utf-8")) or []
        return any(x.get("is_processing") for x in slots)
    except Exception:
        return False


def _spec_only_check(cfg, project):
    """只做不需要跑模擬的檢查：規格書承諾的東西在不在原始碼裡。

    完整 gate 要編譯+模擬每個 block，大專案好幾分鐘；橋接器等不了。
    規格缺漏用 grep 就查得到，先擋這一關可以省掉絕大多數的等待。
    """
    try:
        with io.open(cfg, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    missing = []
    for bname, b in (d.get("blocks") or {}).items():
        if b.get("status") in ("pending", "skipped"):
            continue
        for label, item in (b.get("spec") or {}).items():
            if isinstance(item, str):
                item = {"pattern": item}
            if item.get("skip"):
                continue
            pat = item.get("pattern") or label
            files = item.get("in") or item.get("files") or []
            if isinstance(files, str):
                files = [files]
            found = False
            for rel in files:
                try:
                    txt = io.open(os.path.join(project, rel),
                                  encoding="utf-8", errors="replace").read()
                except (IOError, OSError):
                    continue
                if re.search(pat, txt):
                    found = True
                    break
            if not found:
                missing.append("%s 的 spec 項目 '%s' 在原始碼裡找不到"
                               % (bname, label))
    return missing


def run_gate(project):
    """跑專案的驗收關卡。

    回傳 (status, detail)：
      'pass'    全綠
      'fail'    有項目沒過（detail 是前幾條原因）
      'absent'  這個專案沒設 gate，或工具不在 —— 不做判斷
    """
    if not project:
        return "absent", "找不到專案目錄"
    cfg = os.path.join(project, "simcheck.json")
    if not os.path.exists(cfg):
        return "absent", "專案沒有 simcheck.json"
    if not os.path.exists(SIMCHECK):
        return "absent", "找不到 simcheck.py"
    # 完整 gate 要跑每個 block 的模擬，大專案可能好幾分鐘。
    # 橋接器等在這裡會拖住整個接續流程，所以先用不跑模擬的靜態檢查
    # （spec 項目、marker 有沒有寫）快速判斷；靜態就有問題的話直接回報，
    # 不必等模擬跑完。
    quick = _spec_only_check(cfg, project)
    if quick:
        return "fail", "；".join(quick[:4]) + (
            "（另有 %d 項）" % (len(quick) - 4) if len(quick) > 4 else "")

    try:
        r = subprocess.run(
            [sys.executable, SIMCHECK, "--config", cfg, "--all"],
            cwd=project, timeout=GATE_TIMEOUT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        return "fail", "驗收關卡跑超過 %d 秒沒結束" % GATE_TIMEOUT
    except Exception as e:
        return "absent", "跑不起來：%s" % e

    out = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    if r.returncode == 0:
        return "pass", "全部通過"

    reasons = [x.strip() for x in re.findall(r"^\s*FAIL: (.+)$", out, re.M)]
    # 多個 block 會重複同樣的原因，去重但保留順序
    seen, uniq = set(), []
    for x in reasons:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    reasons = uniq
    if not reasons:
        reasons = [ln.strip() for ln in out.splitlines()
                   if ": FAIL" in ln][:4]
    detail = "；".join(reasons[:4]) or "exit %d" % r.returncode
    if len(reasons) > 4:
        detail += "（另有 %d 項）" % (len(reasons) - 4)
    return "fail", detail


# 它講這些話代表任務真的收尾了，不該再自動接續
DONE_RE = re.compile(
    r"全部完成|都完成了|任務完成|全部通過|已全部|大功告成|"
    r"ALL_PASS|ALL_INDEPENDENT_CHECKS_PASS", re.I)

# 它在求助 —— 需要人介入，自動接續只會空轉
HELP_RE = re.compile(
    r"我卡住|需要你決定|請告訴我|無法繼續|需要更多資訊|"
    r"i'?m stuck|need your input|cannot proceed", re.I)


def decide():
    s = load_state()
    now = time.time()
    sess = latest_session()

    if not sess:
        return False, "讀不到任何 session", s
    sid, calls, started, ended = sess

    if not ended:
        # ended_at 是 NULL 不一定代表還在跑 —— 行程被砍掉（當機、關視窗、
        # 手動 kill）時沒機會寫結束時間，session 會永遠停在「跑著」，
        # 守衛就再也不會接續。用「上游有沒有在推理」和「多久沒動靜」
        # 交叉判斷，兩個都說沒有才當成僵屍。
        idle_min = (now - _last_activity(sid)) / 60
        if idle_min > ZOMBIE_IDLE_MIN and not _upstream_busy():
            log("[guard] session %s 標記為跑著，但已 %.0f 分鐘沒動靜"
                "且上游閒置 —— 當成中斷處理" % (sid[:22], idle_min))
        else:
            return False, "上一個任務還在跑，不需要接續", s

    # 1. 冷卻：避免兩次接續之間抖動
    if now - s.get("last_run", 0) < COOLDOWN_SEC:
        return False, "距離上次接續不到 %d 秒，先等一下" % COOLDOWN_SEC, s

    # 2. 同一個 session 不重複接續
    if sid == s.get("last_sid"):
        return False, "這個 session 已經接續過了，不重複", s

    # 3. 鏈長上限
    if s.get("chain", 0) >= MAX_CHAIN:
        return False, "已連續自動接續 %d 輪，停下來讓人看" % s["chain"], s

    # 4. 連續跑太久
    if s.get("started") and (now - s["started"]) > MAX_HOURS * 3600:
        return False, "連續自動跑超過 %d 小時，停下來" % MAX_HOURS, s

    # 5. 上一輪有沒有真的在做事
    nfiles = files_touched(sid)
    if calls is not None and calls < MIN_PROGRESS_CALLS and nfiles == 0:
        return False, ("上一輪只有 %s 次工具呼叫、沒寫任何檔案 —— "
                       "看起來沒進展，不要空轉" % calls), s

    # 6. 它說做完了 —— 但不採信，先跑驗收關卡
    last = last_assistant(sid)
    project = find_project(sid)
    if DONE_RE.search(last):
        status, detail = run_gate(project)
        if status == "fail":
            # 它說完成、關卡說沒有 —— 繼續接續，讓它自己去修
            log("[guard] 它宣告完成但驗收關卡沒過：%s" % detail)
            s["gate_override"] = s.get("gate_override", 0) + 1
            if s["gate_override"] > 3:
                return False, ("它連續 %d 次宣告完成但關卡都沒過 —— "
                               "自動接續解不了，停下來讓人看（%s）"
                               % (s["gate_override"], detail)), s
            return True, ("它說完成了，但驗收關卡沒過 —— 不採信，繼續接續："
                          "%s" % detail), s
        if status == "pass":
            return False, "它說完成了，而且驗收關卡全綠 —— 收工", s
        return False, "它自己說任務完成了，不再自動接續（%s）" % detail, s

    # 7. 它在求助
    if HELP_RE.search(last):
        return False, "它說卡住了/需要人決定 —— 自動接續解不了，停下來", s

    # 8. 沒有收尾報告就接不下去
    if len(last.strip()) < 80:
        return False, "上一輪沒留下像樣的收尾報告，接續會失去脈絡", s

    # 9. 沒宣告完成時也看一眼關卡，把狀態寫進 log 當脈絡
    gate_note = ""
    status, detail = run_gate(project)
    if status == "fail":
        gate_note = "；關卡未過：%s" % detail
    elif status == "pass":
        gate_note = "；關卡全綠"
        s["gate_override"] = 0    # 過了就把「宣告完成卻沒過」的計數清掉

    return True, "檢查通過（第 %d 輪，上一輪 %s 次呼叫、動了 %d 個檔%s）" % (
        s.get("chain", 0) + 1, calls, nfiles, gate_note), s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--commit", action="store_true",
                    help="判斷通過時把狀態記下來（真的要接續時才加）")
    ap.add_argument("--reset", action="store_true", help="清空接續鏈計數")
    args = ap.parse_args()

    if args.reset:
        save_state({"chain": 0, "started": 0, "last_sid": "", "last_run": 0})
        log("[guard] 已重置接續鏈")
        return 0

    ok, why, s = decide()

    if ok and args.commit:
        sess = latest_session()
        s["chain"] = s.get("chain", 0) + 1
        s["started"] = s.get("started") or time.time()
        s["last_sid"] = sess[0] if sess else ""
        s["last_run"] = time.time()
        save_state(s)

    if args.json:
        sys.stdout.buffer.write(
            (json.dumps({"go": ok, "reason": why}, ensure_ascii=False) + "\n")
            .encode("utf-8"))
    else:
        log("[guard] %s — %s" % ("GO" if ok else "STOP", why))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

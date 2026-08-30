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
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "state.db")
STATE = os.path.join(HERE, "_autoguard_state.json")
LOG = os.path.join(HERE, "autoguard.log")

# ---- 防呆門檻 ----
MAX_CHAIN = 8            # 同一條自動接續鏈最多幾輪
MAX_HOURS = 12           # 連續自動跑最多幾小時
MIN_PROGRESS_CALLS = 5   # 一輪至少要有幾次工具呼叫才算「有在做事」
COOLDOWN_SEC = 60        # 兩次接續之間至少間隔多久（防抖動迴圈）


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
            "FROM sessions WHERE source='cli' "
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

    # 6. 它說做完了
    last = last_assistant(sid)
    if DONE_RE.search(last):
        return False, "它自己說任務完成了，不再自動接續", s

    # 7. 它在求助
    if HELP_RE.search(last):
        return False, "它說卡住了/需要人決定 —— 自動接續解不了，停下來", s

    # 8. 沒有收尾報告就接不下去
    if len(last.strip()) < 80:
        return False, "上一輪沒留下像樣的收尾報告，接續會失去脈絡", s

    return True, "檢查通過（第 %d 輪，上一輪 %s 次呼叫、動了 %d 個檔）" % (
        s.get("chain", 0) + 1, calls, nfiles), s


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

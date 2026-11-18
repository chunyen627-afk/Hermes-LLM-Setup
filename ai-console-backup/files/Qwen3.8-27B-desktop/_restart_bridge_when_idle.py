"""等模型閒下來，再重啟橋接器。

橋接器改了程式碼要重啟才生效，但模型正在推理時重啟會切斷它的請求。
這支會等到安全時機（模型沒在推理、而且已經安靜一段時間）才動手。

它一輪接一輪時「完全閒置」可能永遠等不到，所以有兩條觸發路徑：
  A. 完全閒置 —— 零損失，最理想
  B. 正在推理但「剛起步」 —— 重算量還很小，切掉只丟幾百個 token

橋接器是 HTTP 代理，切斷的只是當下那一次請求，不是整個 session；
已經寫進磁碟的檔案完全不受影響。

安全檢查：
  1. 兩條路徑都要連續觀察數次才動手（避免抓到瞬間的假象）
  2. 目前只有一個 hermes_bridge 行程（多個就停手，讓人來看）
  3. 用 taskkill /PID 指定行程，絕不用 /IM（會波及其他 python）

用法：
    python _restart_bridge_when_idle.py            → 等到閒置就重啟
    python _restart_bridge_when_idle.py --check    → 只回報現在能不能重啟
    python _restart_bridge_when_idle.py --now      → 不等，立刻重啟（危險）
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "hermes_bridge.py")
LAUNCHER = os.path.join(HERE, "BRIDGE-AUTORESUME.bat")
UP = "http://127.0.0.1:8001"

POLL_SEC = 20
STABLE_CHECKS = 3          # 連續幾次都閒置才算真的閒下來
MAX_WAIT_HOURS = 12

# 「等完全閒置」在它一輪接一輪時可能永遠等不到。
# 橋接器是 HTTP 代理，切斷的只是「當下那一次請求」——
# 損失 = 那次請求已經生成了多少，不是整個 session。
#
# 判斷損失：n_prompt_tokens_processed 是這次真正重算的 prompt 量。
# 值很小 = 剛開始（prompt 幾乎全命中快取，還在 prefill），生成的內容還少 → 切了不痛。
# 值很大 = 正在處理大量新內容，或已經生成很久 → 等一下。
LOW_LOSS_PROCESSED = 8000   # 重算量低於這個就算「剛起步」
LOW_LOSS_CHECKS = 2         # 連續幾次都在低損失窗口


def log(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def slots_busy():
    """回傳 (busy, detail, processed)。

    processed = 忙碌 slot 這次重算的 prompt 量，用來估切斷的損失。
    連不到上游時回 (None, 原因, 0)。
    """
    try:
        with urllib.request.urlopen(UP + "/slots", timeout=8) as r:
            slots = json.loads(r.read().decode("utf-8")) or []
    except Exception as e:
        return None, "連不到 %s：%s" % (UP, e), 0
    if not slots:
        return None, "上游沒有回報任何 slot", 0
    busy = [s for s in slots if s.get("is_processing")]
    processed = max((s.get("n_prompt_tokens_processed") or 0)
                    for s in busy) if busy else 0
    detail = "、".join(
        "slot %d %s %d/%d%s" % (
            i, "推理中" if s.get("is_processing") else "閒置",
            s.get("n_prompt_tokens") or 0, s.get("n_ctx") or 0,
            "(重算 %d)" % (s.get("n_prompt_tokens_processed") or 0)
            if s.get("is_processing") else "")
        for i, s in enumerate(slots))
    return (len(busy) > 0), detail, processed


def bridge_pids():
    """找出目前在跑的 hermes_bridge 行程。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*hermes_bridge.py*' } | "
          "Select-Object -ExpandProperty ProcessId")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=30)
        out = r.stdout.decode("utf-8", "replace")
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def restart(pids):
    for pid in pids:
        log("停止橋接器 PID %d" % pid)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if not os.path.exists(LAUNCHER):
        log("找不到 %s —— 請手動啟動" % LAUNCHER)
        return 1
    log("重新啟動：%s" % os.path.basename(LAUNCHER))
    # 用新視窗開，這支腳本結束後橋接器仍留著
    subprocess.Popen(["cmd", "/c", "start", "", LAUNCHER], cwd=HERE)
    time.sleep(3)
    now = bridge_pids()
    if now:
        log("橋接器已啟動，PID %s" % ", ".join(str(p) for p in now))
        return 0
    log("啟動後找不到行程 —— 請確認視窗有沒有跳出錯誤")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只回報，不動作")
    ap.add_argument("--now", action="store_true", help="不等待，立刻重啟")
    a = ap.parse_args()

    pids = bridge_pids()
    busy, detail, processed = slots_busy()

    log("橋接器行程：%s" % (", ".join(str(p) for p in pids) or "（沒有）"))
    log("上游狀態：%s" % detail)

    if len(pids) > 1:
        log("⚠ 有 %d 個橋接器行程 —— 只能有一個。先手動處理，不自動重啟。"
            % len(pids))
        return 2

    if a.check:
        if busy is None:
            log("→ 無法判斷（上游連不到）")
        elif busy and processed <= LOW_LOSS_PROCESSED:
            log("→ 損失小可以重啟：正在推理但只重算了 %d 個 token"
                "（剛起步，切了不痛）" % processed)
        elif busy:
            log("→ 建議再等：重算量 %d 已超過 %d，切了會丟掉較多"
                % (processed, LOW_LOSS_PROCESSED))
        else:
            log("→ 完全閒置，隨時可重啟")
        return 0

    if a.now:
        log("--now：跳過等待直接重啟")
        return restart(pids)

    # 兩條路都可以觸發重啟：
    #   A. 完全閒置（最理想，零損失）
    #   B. 正在推理但重算量很小 = 剛起步，切掉只丟幾百個 token
    # 它一輪接一輪時 A 可能永遠等不到，B 讓等待有終點。
    deadline = time.time() + MAX_WAIT_HOURS * 3600
    stable = 0
    lowloss = 0
    while time.time() < deadline:
        busy, detail, processed = slots_busy()
        if busy is None:
            log("等待中（%s）" % detail)
            stable = lowloss = 0
        elif busy:
            stable = 0
            if processed <= LOW_LOSS_PROCESSED:
                lowloss += 1
                log("低損失窗口 %d/%d：重算才 %d 個 token（%s）"
                    % (lowloss, LOW_LOSS_CHECKS, processed, detail))
                if lowloss >= LOW_LOSS_CHECKS:
                    log("這一輪才剛起步，現在切損失最小 —— 開始重啟")
                    return restart(bridge_pids())
            else:
                if lowloss:
                    log("重算量升到 %d，離開低損失窗口" % processed)
                lowloss = 0
        else:
            lowloss = 0
            stable += 1
            log("閒置 %d/%d（%s）" % (stable, STABLE_CHECKS, detail))
            if stable >= STABLE_CHECKS:
                log("模型已完全閒置，零損失重啟")
                return restart(bridge_pids())
        time.sleep(POLL_SEC)

    log("等超過 %d 小時都沒有適合的時機，放棄。" % MAX_WAIT_HOURS)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("已取消")
        sys.exit(130)

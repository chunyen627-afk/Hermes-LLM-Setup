"""等模型閒下來，再重啟橋接器。

橋接器改了程式碼要重啟才生效，但模型正在推理時重啟會切斷它的請求。
這支會等到安全時機（模型沒在推理、而且已經安靜一段時間）才動手。

安全檢查：
  1. 上游 slot 全部 is_processing=False
  2. 連續 STABLE_CHECKS 次檢查都閒置（避免抓到兩輪之間的空檔）
  3. 目前只有一個 hermes_bridge 行程（多個就停手，讓人來看）

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

POLL_SEC = 30
STABLE_CHECKS = 4          # 連續幾次都閒置才算真的閒下來（4 x 30s = 2 分鐘）
MAX_WAIT_HOURS = 12


def log(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def slots_busy():
    """回傳 (busy, detail)。連不到上游時回 (None, 原因)。"""
    try:
        with urllib.request.urlopen(UP + "/slots", timeout=8) as r:
            slots = json.loads(r.read().decode("utf-8")) or []
    except Exception as e:
        return None, "連不到 %s：%s" % (UP, e)
    if not slots:
        return None, "上游沒有回報任何 slot"
    busy = [i for i, s in enumerate(slots) if s.get("is_processing")]
    detail = "、".join(
        "slot %d %s %d/%d" % (i, "推理中" if s.get("is_processing") else "閒置",
                              s.get("n_prompt_tokens") or 0, s.get("n_ctx") or 0)
        for i, s in enumerate(slots))
    return (len(busy) > 0), detail


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
    busy, detail = slots_busy()

    log("橋接器行程：%s" % (", ".join(str(p) for p in pids) or "（沒有）"))
    log("上游狀態：%s" % detail)

    if len(pids) > 1:
        log("⚠ 有 %d 個橋接器行程 —— 只能有一個。先手動處理，不自動重啟。"
            % len(pids))
        return 2

    if a.check:
        if busy is None:
            log("→ 無法判斷（上游連不到）")
        elif busy:
            log("→ 現在不能重啟：模型正在推理")
        else:
            log("→ 現在可以重啟")
        return 0

    if a.now:
        log("--now：跳過等待直接重啟")
        return restart(pids)

    deadline = time.time() + MAX_WAIT_HOURS * 3600
    stable = 0
    while time.time() < deadline:
        busy, detail = slots_busy()
        if busy is None:
            log("等待中（%s）" % detail)
            stable = 0
        elif busy:
            if stable:
                log("又開始推理了，重新計時")
            stable = 0
        else:
            stable += 1
            log("閒置 %d/%d（%s）" % (stable, STABLE_CHECKS, detail))
            if stable >= STABLE_CHECKS:
                log("模型已閒置 %d 分鐘，開始重啟"
                    % (STABLE_CHECKS * POLL_SEC // 60))
                return restart(bridge_pids())
        time.sleep(POLL_SEC)

    log("等超過 %d 小時仍未閒置，放棄。" % MAX_WAIT_HOURS)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("已取消")
        sys.exit(130)

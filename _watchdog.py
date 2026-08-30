# -*- coding: utf-8 -*-
"""看門狗：確保 llama-server 和橋接器一直活著，並回報狀態。

無人看管跑一整晚時，最怕的不是任務失敗，是**整條鏈默默斷掉**：
橋接器崩了、server 被 OOM 殺掉、開機後沒人啟動 —— 這些都沒有任何跡象，
你要等到發現「怎麼都沒進度」才知道，而那可能是幾小時後。

這支每分鐘檢查一次：
  1. llama-server（:8001）活著嗎？沒有就啟動
  2. 橋接器（:1234）活著嗎？沒有就啟動
  3. 把目前狀態寫進桌面的 27B-STATUS.txt，掃一眼就知道在幹嘛
  4. 順手清掉超過一天的 .vcd（模擬波形檔，單檔可以 40MB）

用法：
    python _watchdog.py           → 持續看守
    python _watchdog.py --once    → 檢查一次就結束（測試用）
    python _watchdog.py --install → 設定開機自動啟動
"""

import argparse
import json
import os
import socket
import subprocess
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop",
                           "27B-STATUS.txt")
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "state.db")
GUARD_STATE = os.path.join(HERE, "_autoguard_state.json")
LOG = os.path.join(HERE, "watchdog.log")

SERVER_BAT = os.path.join(HERE, "1-START-GPU-Server.bat")
BRIDGE_BAT = os.path.join(HERE, "BRIDGE-AUTORESUME.bat")

POLL_SEC = 60
VCD_MAX_AGE_H = 24
# 啟動之後給它多久暖機，這段時間不重複啟動（llama-server 載模型要幾分鐘）
STARTUP_GRACE_SEC = 300


def log(msg):
    line = "%s  %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
        sys.stdout.flush()
    except Exception:
        pass


def port_open(port, host="127.0.0.1", timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def server_ready():
    """:8001 有回應而且答得出 /slots 才算真的活著。"""
    if not port_open(8001):
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:8001/slots",
                                    timeout=5) as r:
            json.loads(r.read().decode("utf-8"))
        return True
    except Exception:
        return False


def bridge_alive():
    return port_open(1234)


def _pids(pattern):
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*%s*' } | "
          "Select-Object -ExpandProperty ProcessId" % pattern)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=30)
        return [int(x) for x in r.stdout.decode("utf-8", "replace").split()
                if x.strip().isdigit()]
    except Exception:
        return []


def launch(bat, what):
    if not os.path.exists(bat):
        log("找不到 %s —— 無法啟動 %s" % (bat, what))
        return False
    log("★ %s 沒在跑，啟動中：%s" % (what, os.path.basename(bat)))
    try:
        subprocess.Popen(["cmd", "/c", "start", "", bat], cwd=HERE)
        return True
    except Exception as e:
        log("啟動 %s 失敗：%s" % (what, e))
        return False


def task_state():
    """回傳 (狀態字串, 細節)。從 state.db 和守衛狀態檔推。"""
    try:
        c = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro",
                            uri=True)
        r = c.execute(
            "SELECT id, source, started_at, ended_at, tool_call_count "
            "FROM sessions WHERE source IN ('cli','desktop','tui') "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        c.close()
    except Exception as e:
        return "讀不到 state.db", str(e)[:60]
    if not r:
        return "沒有任何 session", ""
    sid, src, st, en, tools = r
    age_m = (time.time() - (en or st)) / 60
    if not en:
        return "跑著", "%s（%s）已 %.0f 分鐘、%s 次工具呼叫" % (
            sid[:22], src, (time.time() - st) / 60, tools)
    return "停著", "%s（%s）結束於 %s，已過 %.0f 分鐘" % (
        sid[:22], src, time.strftime("%H:%M", time.localtime(en)), age_m)


def chain_state():
    try:
        with open(GUARD_STATE, encoding="utf-8") as f:
            d = json.load(f)
        n = d.get("chain", 0)
        started = d.get("started", 0)
        hrs = (time.time() - started) / 3600 if started else 0
        return "自動接續 %d/8 輪，已連續 %.1f 小時" % (n, hrs)
    except Exception:
        return "尚未有過自動接續"


def write_status(server_ok, bridge_ok):
    st, detail = task_state()
    lines = [
        "27B 自動化狀態    " + time.strftime("%Y-%m-%d %H:%M:%S"),
        "=" * 46,
        "  llama-server : " + ("正常" if server_ok else "★ 掛了/啟動中"),
        "  橋接器       : " + ("正常" if bridge_ok else "★ 掛了/啟動中"),
        "  任務         : " + st,
        "                 " + detail,
        "  " + chain_state(),
        "",
        "（這個檔案由 _watchdog.py 每分鐘更新；停止看守就不會再變）",
    ]
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def clean_vcd():
    """清掉太舊的波形檔 —— 單檔可以 40MB，長跑會一直堆。"""
    cutoff = time.time() - VCD_MAX_AGE_H * 3600
    freed = 0
    for root in (os.path.join(os.path.expanduser("~"), "matmul_axi"),):
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".vcd"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getmtime(p) < cutoff:
                        sz = os.path.getsize(p)
                        os.remove(p)
                        freed += sz
                except OSError:
                    pass
    if freed:
        log("清掉舊的 .vcd，釋放 %.0f MB" % (freed / 1e6))


def install_autostart():
    """在 Startup 資料夾放一個捷徑，開機就跑這支。"""
    startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft",
                           "Windows", "Start Menu", "Programs", "Startup")
    if not os.path.isdir(startup):
        log("找不到 Startup 資料夾：%s" % startup)
        return 1
    bat = os.path.join(startup, "27B-watchdog.bat")
    # 用 pythonw.exe 跑，不開視窗 —— 看門狗沒有需要盯著看的輸出，
    # 狀態都在 27B-STATUS.txt 和 watchdog.log 裡，開個窗只是佔螢幕。
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = py
    body = (
        "@echo off\r\n"
        "REM Auto-started at logon: keeps llama-server and the bridge alive.\r\n"
        "REM Runs windowless via pythonw; state goes to 27B-STATUS.txt on the\r\n"
        "REM desktop and watchdog.log next to this script.\r\n"
        "REM ASCII only on purpose: cmd reads .bat in the OEM codepage.\r\n"
        "cd /d \"%s\"\r\n"
        "start \"\" /b \"%s\" \"%s\\_watchdog.py\"\r\n"
        % (HERE, pyw, HERE))
    try:
        with open(bat, "w", encoding="ascii", newline="") as f:
            f.write(body)
    except Exception as e:
        log("寫入失敗：%s" % e)
        return 1
    log("已設定開機自動啟動：%s" % bat)
    log("下次登入就會自動跑。現在也可以直接執行那個 .bat 測試。")
    return 0


def check_once():
    server_ok = server_ready()
    bridge_ok = bridge_alive()
    write_status(server_ok, bridge_ok)
    return server_ok, bridge_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="檢查一次就結束")
    ap.add_argument("--install", action="store_true",
                    help="設定開機自動啟動")
    a = ap.parse_args()

    if a.install:
        return install_autostart()

    if a.once:
        s, b = check_once()
        log("server=%s bridge=%s" % ("OK" if s else "DOWN",
                                     "OK" if b else "DOWN"))
        log("狀態寫進 " + STATUS_FILE)
        return 0

    log("看門狗啟動（每 %d 秒檢查一次）" % POLL_SEC)
    last_server_start = 0.0
    last_bridge_start = 0.0
    last_clean = 0.0

    while True:
        try:
            now = time.time()
            server_ok = server_ready()
            bridge_ok = bridge_alive()

            # server 要先起來，橋接器才有上游可轉發
            if not server_ok and now - last_server_start > STARTUP_GRACE_SEC:
                if launch(SERVER_BAT, "llama-server"):
                    last_server_start = now
            elif server_ok and not bridge_ok and \
                    now - last_bridge_start > STARTUP_GRACE_SEC:
                # 只有一個橋接器行程 —— 多開會互搶 :1234
                if not _pids("hermes_bridge"):
                    if launch(BRIDGE_BAT, "橋接器"):
                        last_bridge_start = now
                else:
                    log("橋接器行程在但 :1234 沒回應，先不重開（可能正在啟動）")

            write_status(server_ok, bridge_ok)

            if now - last_clean > 3600:
                clean_vcd()
                last_clean = now

        except Exception as e:
            log("檢查時出錯（繼續看守）：%s" % str(e)[:80])

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("看門狗停止")
        sys.exit(130)

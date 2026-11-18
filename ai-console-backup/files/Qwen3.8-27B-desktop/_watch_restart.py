# -*- coding: utf-8 -*-
"""偵測 llama-server 重啟，並回報有沒有把 27B 的對話一起帶走。

2026-09-01 踩到：看門狗把掛掉的 server 救回來了（那是它的職責），
但**正在跑的對話會跟著斷** —— 行程還在、卻卡住不會再產出，
要等人發現才處理。當天 server 掛過五次，每次都是這個下場。

判斷「重啟過」用的是行程的啟動時間，不是 log —— log 只說看門狗
「試圖啟動」，行程時間才是真的有沒有換過。

用法：
    python _watch_restart.py --json

輸出：
    restarted   這次檢查發現 server 換了新行程
    orphaned    server 活著，但沒有對話在跑（斷線了沒人接）
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "_watch_restart_state.json")


def _ps(script):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=25)
        return r.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def server_pid_and_start():
    out = _ps("Get-Process llama-server -ErrorAction SilentlyContinue | "
              "ForEach-Object { '{0} {1}' -f $_.Id, "
              "$_.StartTime.ToString('yyyyMMddHHmmss') }")
    parts = out.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None, None


def chat_count():
    out = _ps("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*hermes*chat*' } | "
              "Measure-Object).Count")
    try:
        return int(out or 0)
    except Exception:
        return -1


def probe_endpoints():
    """分別探 /health 和 /slots，因為它們會不一致。

    2026-09-01 實測：llama-server 連跑 72 小時後鎖死，
    `/health` 還是 200、1 毫秒回應，但 `/slots` **永久逾時**。
    只看 health 會誤判成正常 —— 那次讓 27B 卡在初始化、
    看門狗一直誤判、對話斷線，全部沒被抓到。
    """
    import urllib.request
    out = {}
    for name, path, limit in (("health", "/health", 5), ("slots", "/slots", 8)):
        t0 = time.time()
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8001" + path, timeout=limit) as resp:
                resp.read()
            out[name] = {"ok": True, "sec": round(time.time() - t0, 2)}
        except Exception:
            out[name] = {"ok": False, "sec": round(time.time() - t0, 2)}
    # health 好、slots 掛 = 典型的內部鎖死（不是模型還在載入，那會兩個都慢）
    out["deadlocked"] = out["health"]["ok"] and not out["slots"]["ok"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    pid, start = server_pid_and_start()
    chats = chat_count()

    try:
        st = json.load(io.open(STATE, encoding="utf-8"))
    except Exception:
        st = {}

    restarted = bool(pid and st.get("start") and st["start"] != start)
    if pid:
        try:
            io.open(STATE, "w", encoding="utf-8").write(
                json.dumps({"pid": pid, "start": start, "seen": time.time()},
                           ensure_ascii=False))
        except Exception:
            pass

    probes = probe_endpoints()
    r = {
        "health_ok": probes["health"]["ok"],
        "slots_ok": probes["slots"]["ok"],
        "slots_sec": probes["slots"]["sec"],
        "deadlocked": probes["deadlocked"],
        "server_pid": pid,
        "server_start": start,
        "server_up": pid is not None,
        "chats": chats,
        "restarted": restarted,
        # server 在跑但沒有對話 = 斷線了沒人接（要重派）
        "orphaned": bool(pid) and chats == 0,
    }

    if a.json:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        sys.stdout.buffer.write(
            ("server pid=%s | 對話 %s | 剛重啟=%s | 沒人接=%s\n"
             "health=%s slots=%s(%.2fs) 鎖死=%s\n"
             % (pid, chats, restarted, r["orphaned"],
                r["health_ok"], r["slots_ok"], r["slots_sec"],
                r["deadlocked"])
             ).encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""27B 健康度：突然變慢就是有鬼。

一次性檢查，印一行結論。給人看也給監控用（--json）。

看三件事：
  1. 生成速度 tok/s —— 取樣兩次算差。慢下來通常是有人搶 slot
  2. slot 佔用 —— 有沒有沒人接收卻還在燒的空轉 slot
  3. relay log 靜置多久 —— 配合截斷標記判斷卡死

用法：
    python _health.py            # 印人看的
    python _health.py --json     # 給腳本吃的
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SLOTS_URL = "http://127.0.0.1:8001/slots"

# 正常速度的下限。低於這個值就是有鬼（搶 slot、掉到 CPU、context 太長）。
# 26/08/31 實測 3.8-27B 三卡約 20-30 tok/s，抓一半當警戒線。
SLOW_TOKS = 10.0
SAMPLE_SEC = 6          # 兩次取樣間隔
STALL_MIN = 5           # relay log 靜置多久算可疑
STALL_MARKERS = ("finish_reason='length'", "Response truncated")


def _slots():
    try:
        with urllib.request.urlopen(SLOTS_URL, timeout=5) as r:
            return json.loads(r.read().decode("utf-8")) or []
    except Exception:
        return None


def _decoded(slots):
    """每個 slot 目前解碼到第幾個 token。"""
    out = {}
    for x in slots or []:
        nt = (x.get("next_token") or [{}])[0]
        out[x.get("id")] = nt.get("n_decoded") or 0
    return out


def _agents():
    """活著的 hermes chat「對話」數 (pid, 已跑幾分鐘)。

    一次 hermes chat 會有兩個 python.exe（launcher + 實際行程），
    但只佔一個 slot。用啟動時間分組，同一分鐘內的算同一次對話，
    不然會把 2 個行程當成 2 個 agent，剛好等於 2 個 slot 而漏判空轉。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*hermes*chat*' } | "
          "Sort-Object CreationDate | "
          "Group-Object { $_.CreationDate.ToString('yyyyMMddHHmm') } | "
          "ForEach-Object { $f=$_.Group[0]; '{0} {1:N1}' -f $f.ProcessId, "
          "((Get-Date) - $f.CreationDate).TotalMinutes }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=25)
        out = []
        for ln in r.stdout.decode("utf-8", "replace").splitlines():
            p = ln.split()
            if len(p) == 2:
                out.append((int(p[0]), float(p[1])))
        return out
    except Exception:
        return []


def _relay():
    """最新 relay log 的 (檔名, 靜置分鐘, 尾部有沒有截斷標記)。"""
    try:
        logs = glob.glob(os.path.join(HERE, "autorelay_*.log"))
        if not logs:
            return None, -1, False
        newest = max(logs, key=os.path.getmtime)
        quiet = (time.time() - os.path.getmtime(newest)) / 60
        with open(newest, "rb") as f:
            try:
                f.seek(-6000, os.SEEK_END)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", "replace")
        return (os.path.basename(newest), quiet,
                any(m in tail for m in STALL_MARKERS))
    except Exception:
        return None, -1, False


def check():
    r = {"t": time.strftime("%H:%M:%S"), "problems": []}

    s1 = _slots()
    if s1 is None:
        r["problems"].append("llama-server 連不上（:8001 沒回應）")
        r["verdict"] = "server 掛了"
        return r

    d1 = _decoded(s1)
    time.sleep(SAMPLE_SEC)
    s2 = _slots()
    d2 = _decoded(s2 or [])

    # 每個 slot 的速度
    speeds = {}
    for sid, n2 in d2.items():
        n1 = d1.get(sid, 0)
        speeds[sid] = max(0.0, (n2 - n1) / float(SAMPLE_SEC))
    r["tok_s"] = {str(k): round(v, 1) for k, v in speeds.items()}

    busy = [x.get("id") for x in (s2 or []) if x.get("is_processing")]
    r["busy_slots"] = busy
    r["n_slots"] = len(s2 or [])

    agents = _agents()
    r["agents"] = [{"pid": p, "min": round(m, 1)} for p, m in agents]

    name, quiet, trunc = _relay()
    r["relay"] = {"log": name, "quiet_min": round(quiet, 1), "truncated": trunc}

    # --- 判定 ---
    # 1. 有人在跑但速度掉下來
    active = [v for v in speeds.values() if v > 0]
    if active:
        fastest = max(active)
        r["fastest_tok_s"] = round(fastest, 1)
        if fastest < SLOW_TOKS:
            r["problems"].append(
                "生成只有 %.1f tok/s（正常 20-30）—— 可能有人搶 slot 或掉到 CPU"
                % fastest)

    # 2. slot 在燒但沒有對應的 agent —— 沒人接收的空轉
    if busy and not agents:
        r["problems"].append(
            "slot %s 還在生成，但沒有任何 hermes chat 行程在接收 —— "
            "空轉燒算力，重啟 server 才收得掉" % busy)
    elif len(busy) > len(agents) and agents:
        r["problems"].append(
            "佔用 %d 個 slot 但只有 %d 個 agent —— 可能有殘留的空轉 slot"
            % (len(busy), len(agents)))

    # 3. 截斷後靜置 = 卡死
    if trunc and quiet >= STALL_MIN:
        r["problems"].append(
            "輸出被截斷且 %s 已 %.0f 分鐘沒新增 —— 卡死了" % (name, quiet))
    elif quiet >= STALL_MIN and agents:
        r["problems"].append(
            "relay log 已 %.0f 分鐘沒新增（行程還在，可能在長推理）" % quiet)

    r["verdict"] = "；".join(r["problems"]) if r["problems"] else "正常"
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    a = ap.parse_args()

    r = check()
    if a.json:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        out = sys.stdout
        out.write("[%s] %s\n" % (r["t"], r["verdict"]))
        if "tok_s" in r:
            out.write("  速度 tok/s：%s\n" % r["tok_s"])
        out.write("  slot：%s 個，佔用 %s\n"
                  % (r.get("n_slots"), r.get("busy_slots")))
        out.write("  agent：%s\n" % (r.get("agents") or "無"))
        rl = r.get("relay", {})
        out.write("  relay：%s 靜置 %s 分，截斷=%s\n"
                  % (rl.get("log"), rl.get("quiet_min"), rl.get("truncated")))
    # 有問題回 exit 1，方便腳本判斷
    return 1 if r.get("problems") else 0


if __name__ == "__main__":
    sys.exit(main())

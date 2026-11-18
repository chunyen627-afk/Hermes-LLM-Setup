# -*- coding: utf-8 -*-
"""追蹤驗收主指標有沒有在動。

「有在改檔案」不等於「有進展」。2026-08-31 到 09-01 那次，
27B 一整夜都在改 RTL 和 tb，每個階段也真的解掉東西
（AXI timeout 24→0、xxxx 12→0），但主指標 `data_integrity 26/26`
**十六小時一動也沒動** —— 而那是唯一決定 gate 過不過的東西。

這支專門盯主指標，把「卡住多久」變成客觀數字，不靠人工比對。

用法：
    python _gatemetric.py                 # 印現況
    python _gatemetric.py --json          # 給監控吃
    python _gatemetric.py --reset         # 清掉歷史重新算

狀態存在 _gatemetric_state.json：{"sig": "...", "since": <epoch>}
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "_gatemetric_state.json")
PROJ = r"C:\Users\pjunm\matmul_axi"
IVERILOG = "/c/iverilog/bin/iverilog"
VVP = "/c/iverilog/bin/vvp"
TB = "tb/tb_xspi_slave.v"
TOP = "tb_xspi_slave"


# ⚠ 一定要指名 git-bash 的完整路徑。裸寫 "bash" 會跑到 Windows 內建的
# WSL bash.exe，那個吐的是 UTF-16 的「請使用 wsl.exe --list」訊息，
# 解析出來全是 None，看起來像測不到指標。
GITBASH = r"C:\Program Files\Git\bin\bash.exe"


def _sh(cmd, cwd=None, timeout=180):
    """用 git-bash 跑，因為 iverilog 那組路徑是 POSIX 形式。"""
    try:
        r = subprocess.run([GITBASH, "-lc", cmd], cwd=cwd,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        return r.stdout.decode("utf-8", "replace")
    except Exception as e:
        return "ERR %s" % e


def measure():
    """回傳當下的驗收指標。"""
    out = _sh("%s -o /tmp/_gm.out -g2012 -s %s %s rtl/*.v 2>&1"
              % (IVERILOG, TOP, TB), cwd=PROJ)
    errs = out.count("error")
    if errs:
        return {"compile_errors": errs, "bad": None, "checked": None,
                "covers": None, "simend": None}

    # 不要在這裡包 timeout：git-bash 下 timeout 會讓 vvp 的輸出收不到。
    # 逾時交給 _sh 的 subprocess timeout 處理。
    sim = _sh("cd /tmp && %s /tmp/_gm.out 2>&1" % VVP)
    m = re.search(r"CHECK\s+data_integrity\s+(\d+)\s+(\d+)", sim)
    checked, bad = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    covers = len(re.findall(r"^COVER\s", sim, re.M))
    simend = "ok" if re.search(r"SIMEND\s+ok", sim) else (
        "fail" if re.search(r"SIMEND\s+fail", sim) else None)
    return {"compile_errors": 0, "bad": bad, "checked": checked,
            "covers": covers, "simend": simend}


def signature(m):
    """能代表「驗收有沒有前進」的最小指紋。

    只放真正決定 gate 過不過的東西 —— 檔案行數、改了幾次都不算。
    """
    return "c%s|bad%s|chk%s|cov%s|%s" % (
        m.get("compile_errors"), m.get("bad"), m.get("checked"),
        m.get("covers"), m.get("simend"))


def load():
    try:
        return json.load(io.open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()

    if a.reset:
        try:
            os.remove(STATE)
        except Exception:
            pass
        print("已清掉指標歷史")
        return 0

    m = measure()
    sig = signature(m)
    st = load()
    now = time.time()

    if st.get("sig") != sig:
        st = {"sig": sig, "since": now}
        try:
            io.open(STATE, "w", encoding="utf-8").write(
                json.dumps(st, ensure_ascii=False))
        except Exception:
            pass
        changed = True
    else:
        changed = False

    stuck_h = (now - st.get("since", now)) / 3600.0
    m["stuck_hours"] = round(stuck_h, 2)
    m["changed"] = changed
    m["passed"] = (m.get("compile_errors") == 0 and m.get("bad") == 0
                   and m.get("simend") == "ok")

    if a.json:
        sys.stdout.write(json.dumps(m, ensure_ascii=False) + "\n")
    else:
        o = sys.stdout
        if m["compile_errors"]:
            o.write("編譯 %d 個錯誤\n" % m["compile_errors"])
        else:
            o.write("data_integrity %s/%s bad | cover %s | SIMEND %s\n"
                    % (m["bad"], m["checked"], m["covers"], m["simend"]))
        o.write("指標卡住 %.1f 小時%s\n"
                % (stuck_h, "（剛剛動了）" if changed else ""))
        if m["passed"]:
            o.write("★ 驗收指標全過\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

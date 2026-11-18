#!/usr/bin/env python3
"""階段進度探針 —— 回答「離目標近了沒有」，不是「有沒有在動」。

規劃者的監控用。26/09/02 踩過：系統整合階段沒有主指標，27B 在
create_bd_cell 語法上卡了 3 小時 48 分，而監控只看得到「它很忙」。
真正的訊號是 CELL_COUNT 0 —— 階段目標的完成度。
"""
import json, os, re, subprocess, sys, time

PROJ = r"C:\Users\pjunm\matmul_axi"
VIVADO = r"C:\Xilinx\Vivado\2024.2\bin\vivado.bat"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_progress_state.json")


def rtl_gate():
    """階段 4 指標：模組層 gate 的錯誤數。"""
    tb = os.path.join(PROJ, "tb", "tb_xspi_slave.v")
    out = os.path.join(PROJ, "out", "scratch", "_p.out")
    try:
        subprocess.run(["C:/iverilog/bin/iverilog", "-o", out, "-g2012", "-s",
                        "tb_xspi_slave", tb] + [os.path.join(PROJ, "rtl", f)
                        for f in os.listdir(os.path.join(PROJ, "rtl")) if f.endswith(".v")],
                       cwd=PROJ, capture_output=True, timeout=120)
        r = subprocess.run(["C:/iverilog/bin/vvp", out], cwd=PROJ,
                           capture_output=True, timeout=120)
        t = (r.stdout or b"").decode("utf-8", "replace")
        m = re.search(r"^CHECK data_integrity (\d+) (\d+)", t, re.M)
        return {"checked": int(m.group(1)), "bad": int(m.group(2))} if m else None
    except Exception:
        return None


def bd_cells():
    """階段 5 指標：block design 裡有幾個 IP。"""
    xpr = os.path.join(PROJ, "vivado", "sys_int", "sys_int.xpr")
    if not os.path.exists(xpr):
        return {"project": False, "cells": 0}
    tcl = os.path.join(os.environ.get("TEMP", "/tmp"), "_prog.tcl")
    with open(tcl, "w") as f:
        f.write(f'open_project {{{xpr}}}\n'
                'set b [get_files -quiet *.bd]\n'
                'if {[llength $b] > 0} {\n'
                '  open_bd_design [lindex $b 0]\n'
                '  puts "CELLS [llength [get_bd_cells -quiet]]"\n'
                '} else { puts "CELLS -1" }\n'
                'close_project\n')
    try:
        r = subprocess.run([VIVADO, "-mode", "batch", "-nojournal", "-nolog",
                            "-source", tcl], capture_output=True, timeout=280)
        t = (r.stdout or b"").decode("utf-8", "replace")
        m = re.search(r"^CELLS (-?\d+)", t, re.M)
        n = int(m.group(1)) if m else 0
        return {"project": True, "bd": n >= 0, "cells": max(n, 0)}
    except Exception:
        return {"project": True, "bd": None, "cells": 0}


def churn():
    """重複改同一處的偵測：近 20 個 commit 裡各檔案被改幾次。"""
    D = r"C:\Users\pjunm\OneDrive\Desktop\Hermes-LLM-Setup"
    try:
        r = subprocess.run(["git", "log", "--format=%H", "-20", "--name-only",
                            "--", "projects/matmul_axi"], cwd=D,
                           capture_output=True, timeout=60)
        lines = (r.stdout or b"").decode("utf-8", "replace").split("\n")
        counts = {}
        for l in lines:
            l = l.strip()
            if l and "/" in l and not re.match(r"^[0-9a-f]{40}$", l):
                counts[os.path.basename(l)] = counts.get(os.path.basename(l), 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:3])
    except Exception:
        return {}


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "5"
    now = time.time()
    cur = {"stage": stage, "churn": churn()}
    if stage == "4":
        cur["metric"] = rtl_gate()
    else:
        cur["metric"] = bd_cells()

    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:
            prev = {}

    same = prev.get("metric") == cur["metric"] and prev.get("stage") == stage
    since = prev.get("since", now) if same else now
    cur["since"] = since
    cur["stuck_hours"] = round((now - since) / 3600, 2)
    cur["changed"] = not same

    json.dump(cur, open(STATE, "w"))
    print(json.dumps(cur, ensure_ascii=False))


if __name__ == "__main__":
    main()

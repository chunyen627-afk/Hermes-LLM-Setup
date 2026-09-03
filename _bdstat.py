#!/usr/bin/env python3
"""階段 5 進度探針 —— 讀檔案，不開 Vivado（秒回、不撞專案鎖）。

輸出一行：bd=<cells> <nets> <intf_nets> <ports> <addressing> ip=<名字:.v數,...>

階段 5 沒有 CHECK data_integrity 那種主指標，這些數字就是完成度。
26/09/03：block design 重建期間 bd 會歸零，那是 create_bd_design -force
的正常行為，要看 ip_repo 還在不在才知道是不是誤刪。
"""
import glob
import json
import os
import re

PROJ = r"C:\Users\pjunm\matmul_axi"
BD = os.path.join(PROJ, "vivado", "sys_int", "sys_int.srcs",
                  "sources_1", "bd", "top_bd", "top_bd.bd")
IP_GLOB = os.path.join(PROJ, "ip_repo", "*", "component.xml")

KEYS = ("components", "nets", "interface_nets", "ports", "addressing")


def bd_stat():
    if not os.path.exists(BD):
        return "na"
    try:
        g = json.load(open(BD, encoding="utf-8"))["design"]
    except Exception:
        return "na"
    return " ".join(str(len(g.get(k, {}))) for k in KEYS)


def ip_stat():
    out = []
    for x in sorted(glob.glob(IP_GLOB)):
        name = os.path.basename(os.path.dirname(x))
        try:
            s = open(x, encoding="utf-8", errors="replace").read()
            n = len(set(re.findall(r">([a-z0-9_]+\.v)<", s)))
        except Exception:
            n = 0
        out.append(f"{name}:{n}")
    return ",".join(out) if out else "none"


if __name__ == "__main__":
    print(f"bd={bd_stat()} ip={ip_stat()}")

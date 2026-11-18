#!/usr/bin/env python3
"""matmul_axi 進度探針 —— 讀檔案，不開 Vivado（秒回、不撞專案鎖）。

⚠ 每個子階段的進展落在不同地方，只盯其中一項會一直誤報 STUCK
   （26/09/03 誤報五次）。所以除了具名指標，還加一個「產出檔案的
   最新 mtime」—— 不管它在做哪一步，只要有檔案被寫就算有進展。

輸出：
    bd=<cells nets intf ports addr> ip=<名字:.v數> wrap=<0/1> top=<名字> touch=<秒>
"""
import glob
import json
import os
import re
import time

PROJ = r"C:\Users\pjunm\matmul_axi"
BD = os.path.join(PROJ, "vivado", "sys_int", "sys_int.srcs",
                  "sources_1", "bd", "top_bd", "top_bd.bd")
XPR = os.path.join(PROJ, "vivado", "sys_int", "sys_int.xpr")

KEYS = ("components", "nets", "interface_nets", "ports", "addressing")

# 專案裡「27B 的產出」——任何一個被寫就代表有進展。
# 排除 Vivado 自己的暫存/log，那些會一直動、當不了進度訊號。
WATCH = [
    os.path.join(PROJ, "rtl", "*.v"),
    os.path.join(PROJ, "tb", "*.v"),
    os.path.join(PROJ, "vivado", "*.tcl"),
    os.path.join(PROJ, "constraints", "*.xdc"),
    os.path.join(PROJ, "ip_repo", "*", "component.xml"),
    os.path.join(PROJ, "CHANGELOG_*.md"),
    BD,
]


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
    for x in sorted(glob.glob(os.path.join(PROJ, "ip_repo", "*", "component.xml"))):
        name = os.path.basename(os.path.dirname(x))
        try:
            s = open(x, encoding="utf-8", errors="replace").read()
            n = len(set(re.findall(r">([a-z0-9_]+\.v)<", s)))
        except Exception:
            n = 0
        out.append(f"{name}:{n}")
    return ",".join(out) if out else "none"


def top_stat():
    try:
        s = open(XPR, encoding="utf-8", errors="replace").read()
        m = re.search(r'<Option Name="TopModule"\s+Val="([^"]+)"', s)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def touched_ago():
    """最近一次有產出檔案被寫，是幾秒前。"""
    newest = 0
    for pat in WATCH:
        for f in glob.glob(pat):
            try:
                newest = max(newest, os.path.getmtime(f))
            except OSError:
                pass
    return int(time.time() - newest) if newest else -1


if __name__ == "__main__":
    wrap = 1 if glob.glob(os.path.join(
        PROJ, "vivado", "sys_int", "sys_int.gen", "sources_1",
        "bd", "top_bd", "hdl", "*.v")) else 0
    print(f"bd={bd_stat()} ip={ip_stat()} wrap={wrap} "
          f"top={top_stat()} touch={touched_ago()}s")

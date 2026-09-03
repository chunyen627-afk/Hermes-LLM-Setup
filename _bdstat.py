#!/usr/bin/env python3
"""Block design 進度探針 —— 讀 .bd 的 JSON，不開 Vivado（秒回、不撞專案鎖）。

階段 5 沒有 CHECK data_integrity 那種主指標，用這個當完成度：
cells / nets / intf_nets / ports / addressing 五個數字。
"""
import json
import os

BD = (r"C:\Users\pjunm\matmul_axi\vivado\sys_int\sys_int.srcs"
      r"\sources_1\bd\top_bd\top_bd.bd")

KEYS = ("components", "nets", "interface_nets", "ports", "addressing")


def main():
    if not os.path.exists(BD):
        print("na")
        return
    try:
        g = json.load(open(BD, encoding="utf-8"))["design"]
    except Exception:
        print("na")
        return
    print(" ".join(str(len(g.get(k, {}))) for k in KEYS))


if __name__ == "__main__":
    main()

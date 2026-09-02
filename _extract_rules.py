#!/usr/bin/env python3
"""從 _stopmenu.py 的派工模板抽出行為規則，存成 _rules.txt。

規劃者用 _autorelay.py 直接派工時，_stopmenu.py 的規則不會自動帶上 ——
規則寫了卻沒送到。這個腳本把規則抽成共用文字檔，兩條派工路徑都能讀。

改了 _stopmenu.py 的規則之後跑一次這個。
"""
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "_stopmenu.py")
DST = os.path.join(HERE, "_rules.txt")

STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def main():
    lines = io.open(SRC, encoding="utf-8").read().split("\n")
    start = next(i for i, l in enumerate(lines) if "件提醒" in l and '"' in l)
    end = next(i for i, l in enumerate(lines) if "不要試第三次。**" in l)

    out = []
    for l in lines[start:end + 1]:
        m = STR.search(l)
        if m:
            out.append(m.group(1).replace("\\n", "").replace('\\"', '"'))

    text = "\n".join(out)
    text = re.sub(r"^⚠ .*件提醒：", "⚠ 行為規則 —— 每一輪都適用，不是只針對這次的題目：", text)
    io.open(DST, "w", encoding="utf-8").write(text + "\n")
    print(f"extracted {len(out)} lines -> {DST}")


if __name__ == "__main__":
    main()

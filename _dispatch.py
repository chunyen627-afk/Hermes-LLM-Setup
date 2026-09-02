#!/usr/bin/env python3
"""規劃者派工：題目 + 行為規則，一起送。

用法：
    python _dispatch.py <題目檔>

為什麼要這個：_stopmenu.py --auto 會自動附上行為規則，但它同時會用
自己的模板覆寫題目；_autorelay.py 能送自訂題目，卻不帶任何規則。
26/09/02 發現規劃者用 _autorelay.py 派的那幾輪，12 條規則全都沒送到。

這個腳本把兩者的好處合起來：自訂題目 + 每輪都帶規則。
規則來源是 _rules.txt（改了 _stopmenu.py 之後跑 _extract_rules.py 更新）。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "_rules.txt")
RELAY = os.path.join(HERE, "_autorelay.py")
COMBINED = os.path.join(HERE, "_dispatch_task.txt")


def main():
    if len(sys.argv) < 2:
        print("用法: python _dispatch.py <題目檔>")
        return 1
    task_file = sys.argv[1]
    if not os.path.isabs(task_file):
        task_file = os.path.join(HERE, task_file)
    if not os.path.exists(task_file):
        print(f"找不到題目檔: {task_file}")
        return 1

    task = io.open(task_file, encoding="utf-8").read()
    rules = ""
    if os.path.exists(RULES):
        rules = io.open(RULES, encoding="utf-8").read()
    else:
        print("⚠ 沒有 _rules.txt —— 先跑 python _extract_rules.py")

    parts = [task.rstrip(), ""]
    if rules.strip():
        parts += ["---", "", rules.rstrip(), ""]
    io.open(COMBINED, "w", encoding="utf-8").write("\n".join(parts))

    n_rules = len([l for l in rules.split("\n") if l[:2].strip().rstrip(".").isdigit()])
    print(f"題目 {len(task.splitlines())} 行 + 規則 {n_rules} 條 -> {COMBINED}")
    return subprocess.call([sys.executable, RELAY, "--task-file", COMBINED])


if __name__ == "__main__":
    sys.exit(main())

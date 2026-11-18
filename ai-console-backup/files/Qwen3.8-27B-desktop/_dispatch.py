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
import json
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

    # ⛔ 派工前防呆：已經有 agent 在跑就擋下來。
    # 26/09/03 規劃者犯兩次：看到 PROGRESS 通知就派工，沒確認上一輪結束，
    # 兩個 agent 搶兩個 slot，速度從 15 掉到 3 tok/s。
    # 加 --force 才能無視（真的要搶 slot 時）。
    force = "--force" in sys.argv
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "_health.py"),
                            "--json"], capture_output=True, timeout=90,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        info = json.loads((r.stdout or b"").decode("utf-8", "replace").splitlines()[-1])
        agents = info.get("agents", [])
        if agents and not force:
            print("⛔ 已經有 agent 在跑，不派工（避免搶 slot）：")
            for a in agents:
                print(f"     pid={a.get('pid')} 已跑 {a.get('min')} 分鐘")
            print("   要嘛等它結束，要嘛先按 PID 收掉（絕不用 /IM）。")
            print("   真的要並行就加 --force。")
            return 2
        if agents and force:
            print(f"⚠ --force：無視 {len(agents)} 個在跑的 agent，會搶 slot")
    except Exception as e:
        print(f"⚠ 防呆檢查失敗（{str(e)[:60]}）—— 繼續派工，請自己確認沒有對話在跑")

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

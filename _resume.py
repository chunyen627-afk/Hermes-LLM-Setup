#!/usr/bin/env python3
"""規劃者接手／重啟後的完整狀態檢查 —— 一行指令看完該看的所有東西。

用法：
    python _resume.py

Session 重啟、開機、換手時跑這個。它只「報告」不「動作」——
要不要收殭屍、要不要派工由規劃者判斷。

26/09/03 建立：session 重啟後 Monitor 全停，逐個重建時漏掉速度告警，
結果一個 23 小時的殭屍拖慢速度一整天沒人發現。
"""
import json
import os
import re
import subprocess
import sys
import time

# Windows 主控台預設 cp950，印中文會 UnicodeEncodeError。
# 26/09/03 踩過：_health.py 的 problems 有中文，整個檢查表當掉。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = r"C:\Users\pjunm\matmul_axi"
HERMES = r"C:\Users\pjunm\OneDrive\Desktop\Hermes-LLM-Setup"  # git -C 要 Windows 路徑

OK, WARN, BAD = "[ok ]", "[!! ]", "[XX ]"


def run(cmd, timeout=60, shell=True):
    # PYTHONIOENCODING 讓被呼叫的 python 腳本也用 UTF-8 輸出，
    # 否則 _health.py 的中文 problems 會變成 � 壞字元。
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True,
                           timeout=timeout, env=env)
        return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        return f"__ERR__ {e}"


def section(t):
    print(f"\n=== {t} " + "=" * max(0, 56 - len(t)))


def main():
    print("=" * 62)
    print(f"  規劃者狀態檢查   {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)
    todo = []

    # ---------- 1. llama-server ----------
    section("1. llama-server")
    for ep in ("health", "slots"):
        out = run(f'curl -s --max-time 8 -o /dev/null -w "%{{http_code}} %{{time_total}}" '
                  f'http://127.0.0.1:8001/{ep}')
        print(f"  {ep:8} {out}")
        if "__ERR__" in out or not out.startswith("200"):
            print(f"  {BAD} {ep} 異常")
            todo.append(f"llama-server 的 /{ep} 不正常 —— 可能鎖死，要重啟 server")

    # ---------- 2. 27B 健康 ----------
    section("2. 27B（速度 / slot / 截斷 / 殭屍）")
    h = run(f'python "{os.path.join(HERE, "_health.py")}" --json', timeout=90)
    try:
        d = json.loads(h.splitlines()[-1])
        busy = d.get("busy_slots", [])
        agents = d.get("agents", [])
        spd = d.get("fastest_tok_s")
        probs = d.get("problems", [])
        trunc = d.get("relay", {}).get("truncated")
        quiet = d.get("relay", {}).get("quiet_min")

        print(f"  busy_slots {busy}   agents {len(agents)}   "
              f"tok/s {spd}   quiet {quiet}min   truncated {trunc}")
        for a in agents:
            print(f"    agent pid={a.get('pid')} 已跑 {a.get('min')} 分鐘")

        if len(busy) > len(agents):
            print(f"  {WARN} slot 佔用 {len(busy)} 但只有 {len(agents)} 個 agent")
            todo.append("有殭屍殼佔著 slot —— 按 PID 收掉（絕不用 /IM）")
        if spd is not None and spd < 15:
            print(f"  {WARN} 速度 {spd} tok/s（正常 20-30）")
            todo.append(f"速度只有 {spd} tok/s —— 查搶 slot / 殭屍 / 掉到 CPU")
        for p in probs:
            print(f"  {WARN} {p}")
        if trunc:
            print(f"  {WARN} 上一輪撞截斷")
        if not busy and not agents:
            print(f"  {OK} 沒有 agent 在跑 —— 可以派工")
            todo.append("27B 閒置 —— 寫 _mytask.txt 後用 _dispatch.py 派工")
    except Exception as e:
        print(f"  {BAD} _health.py 解析失敗: {e}")
        print(f"  raw: {h[:200]}")

    # ---------- 3. 專案：RTL gate ----------
    section("3. RTL gate（模組層，11 run 應全 PASS）")
    tb = os.path.join(PROJ, "tb", "tb_xspi_slave.v")
    # md5 直接用 python 算，不靠 shell —— Windows 路徑的反斜線會被 shell 吃掉
    import hashlib
    try:
        keep = [l for l in open(tb, encoding="utf-8", errors="replace")
                if re.search(r"chk_bad *=|chk_checked *=|CHECK data_integrity", l)]
        fp = hashlib.md5("".join(keep).encode()).hexdigest()
    except Exception as e:
        fp = f"__ERR__ {e}"
    BASE_FP = "b8d20766db2b0402f81bc1f97de69750"
    if fp.startswith(BASE_FP):
        print(f"  {OK} tb 驗收邏輯指紋一致（沒被放寬）")
    else:
        print(f"  {WARN} tb 驗收指紋變了: {fp[:16]}（基準 {BASE_FP[:16]}）")
        todo.append("tb 驗收邏輯被改過 —— 看 diff 確認不是放寬標準")

    out = os.path.join(PROJ, "out", "scratch", "_r.out")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rtl = [os.path.join(PROJ, "rtl", f)
           for f in sorted(os.listdir(os.path.join(PROJ, "rtl")))
           if f.endswith(".v")]
    IVL = r"C:\iverilog\bin"
    run([os.path.join(IVL, "iverilog.exe"), "-o", out, "-g2012",
         "-s", "tb_xspi_slave", tb] + rtl, timeout=180, shell=False)
    sim = run([os.path.join(IVL, "vvp.exe"), out], timeout=180, shell=False)
    m = re.search(r"^CHECK data_integrity (\d+) (\d+)", sim, re.M)
    if m:
        checked, bad = m.group(1), m.group(2)
        mark = OK if bad == "0" else WARN
        print(f"  {mark} CHECK data_integrity {checked} {bad}")
        if bad != "0":
            todo.append(f"xspi_slave 有 {bad} 筆錯 —— RTL 退化了，查 git diff")
    else:
        print(f"  {BAD} 跑不出 CHECK —— 模擬可能掛了")
        todo.append("模擬跑不起來 —— 查是不是用了 iverilog 不支援的系統函式")

    # ---------- 4. 階段 5：block design ----------
    section("4. 階段 5（block design 完成度）")
    bd = run(f'python "{os.path.join(HERE, "_bdstat.py")}"')
    print(f"  cells/nets/intf_nets/ports/addressing = {bd}")
    if bd and bd != "na":
        nums = bd.split()
        if len(nums) == 5:
            c, n, i, p, a = (int(x) for x in nums)
            print(f"    cells {c}  nets {n}  intf_nets {i}  ports {p}  addressing {a}")
            if i == 0 and n > 100:
                print(f"  {WARN} 散腳連法（intf_nets 0）—— 位址路徑會不完整")
                todo.append("block design 要改用 IP 介面連法（方案 C），見 CHANGELOG_sysint.md")

    ip = run(f'ls -d "{PROJ}"/ip_repo/*/ 2>/dev/null')
    if ip and "__ERR__" not in ip:
        print(f"  ip_repo: {[os.path.basename(x.rstrip('/')) for x in ip.splitlines()]}")

    # ---------- 5. git ----------
    section("5. 存檔")
    last = run(["git", "-C", HERMES, "log", "--oneline", "-1",
                "--", "projects/matmul_axi"], shell=False)
    print(f"  最新 commit: {last[:90] or '（查不到）'}")
    dirty = run(["git", "-C", HERMES, "status", "--porcelain",
                 "projects/matmul_axi"], shell=False)
    n_dirty = len([l for l in dirty.splitlines() if l.strip()])
    print(f"  未存檔變更: {n_dirty} 個檔案")
    if n_dirty > 0:
        todo.append(f"有 {n_dirty} 個檔案沒存檔 —— 跑 _autosync.sh")

    # ---------- 6. 該做的事 ----------
    section("6. 接下來")
    if todo:
        for i, t in enumerate(todo, 1):
            print(f"  {i}. {t}")
    else:
        print("  沒有異常，27B 在跑 —— 繼續監控就好")

    print("""
--- 一定要重建的兩個 Monitor（session 重啟後會停）---
  A. 27B 健康：速度 / 截斷 / 殭屍 / IDLE   （45 秒一次）
  B. 存檔 + block design 進度              （5 分鐘一次）
     ⚠ 速度告警要讀 _health.py 的 problems，不要只看 deadlocked/truncated

--- 派工 ---
  寫 _mytask.txt -> python _dispatch.py _mytask.txt   （會自動帶 12 條行為規則）
  ⛔ 派工前確認 busy_slots 是空的，否則會搶 slot
""")


if __name__ == "__main__":
    sys.exit(main())

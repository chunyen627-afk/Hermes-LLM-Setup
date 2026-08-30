"""長期觀察 27B 的自主開發能力。

每次專案檔案變動就自動編譯 + 跑測試 + 跑 gate，把結果寫成一行時間軸。
重點不是「現在幾個錯」，而是**跨時間的趨勢**：

  - 有沒有退步（改完之後結果比上一次差）
  - 改了但結果完全沒變幾次（= 改的不是根因）
  - 編譯不過的狀態下連續改幾次（= 在疊改動）
  - gate 有沒有往前進（marker / cover 補了沒）

用法：
    python _watch_progress.py                 # 持續觀察，寫 progress.log
    python _watch_progress.py --once          # 只驗一次
    python _watch_progress.py --report        # 讀 log 印出摘要（趨勢、退步次數）
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "progress.log")
IVDIR = r"C:\iverilog\bin"
SIMCHECK = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "hermes", "skills", "embedded",
    "rtl-sim-verification", "references", "scripts", "simcheck.py")

POLL_SEC = 60

WATCH = [
    "rtl/axi4_master.v", "rtl/matmul_top.v", "rtl/matmul_core.v",
    "rtl/axi4_slave_reg.v", "rtl/axi4s_reg.v", "rtl/async_fifo.v",
    "tb/tb_axi4_master.v", "tb/tb_matmul_top_e2e.v", "tb/tb_matmul_core.v",
    "HANDOFF.md", "simcheck.json",
]

# 每個受測單元：名稱 -> (top, [檔案...], run_in)
UNITS = {
    "axi4_master": ("tb_axi4_master",
                    ["tb/tb_axi4_master.v", "rtl/axi4_master.v"], None),
    "matmul_core": ("tb_matmul_core",
                    ["tb/tb_matmul_core.v", "rtl/matmul_core.v",
                     "rtl/f32_mul.v", "rtl/f32_add.v"], "out/mmtest"),
    # e2e 才是它現在維護的整合測試（tb_matmul_top.v 是 04:18 的舊版）。
    # 資料檔用相對路徑，必須在 out/ 跑，不然全是 x —— 看起來完全像 RTL 壞掉。
    "matmul_top_e2e": ("tb_matmul_top_e2e",
                       ["tb/tb_matmul_top_e2e.v", "rtl/matmul_top.v",
                        "rtl/matmul_core.v", "rtl/axi4_slave_reg.v",
                        "rtl/axi4s_reg.v", "rtl/axi4_master.v",
                        "rtl/async_fifo.v",
                        "rtl/f32_mul.v", "rtl/f32_add.v"], "out"),
}


def tool(name):
    for d in (IVDIR, "/usr/bin"):
        for c in (os.path.join(d, name), os.path.join(d, name + ".exe")):
            if os.path.isfile(c):
                return c
    return name


def sig():
    h = hashlib.md5()
    for rel in WATCH:
        p = os.path.join(HERE, rel)
        try:
            st = os.stat(p)
            h.update(("%s:%d:%d" % (rel, st.st_mtime_ns, st.st_size)).encode())
        except OSError:
            h.update((rel + ":missing").encode())
    return h.hexdigest()[:12]


def run(cmd, cwd=None, timeout=600):
    try:
        r = subprocess.run(cmd, cwd=cwd, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 127, str(e)


def check_unit(name):
    """回傳這個單元的狀態 dict。"""
    top, files, run_in = UNITS[name]
    paths = [os.path.join(HERE, f) for f in files]
    missing = [f for f, p in zip(files, paths) if not os.path.exists(p)]
    if missing:
        return {"state": "missing", "detail": ",".join(missing)}

    out = os.path.join(os.environ.get("TEMP", HERE), "wp_%s.vvp" % name)
    rc, log = run([tool("iverilog"), "-g2012", "-o", out, "-s", top] + paths,
                  cwd=HERE)
    warns = len(re.findall(
        r"[Tt]runcated|[Ii]nferring latch|implicit|Pruning \d+ high bits", log))
    if rc != 0:
        nerr = len(re.findall(r"error", log))
        first = ""
        m = re.search(r"^(.+?:\d+:.*?(?:error|syntax).*)$", log, re.M)
        if m:
            first = m.group(1).strip()[:90]
        return {"state": "compile_fail", "errors": nerr, "warns": warns,
                "detail": first}

    rc, sim = run([tool("vvp"), out],
                  cwd=os.path.join(HERE, run_in) if run_in else HERE)
    sim = sim.replace("\r\n", "\n")
    if "WATCHDOG" in sim or rc == 124:
        return {"state": "hang", "warns": warns, "detail": "watchdog/timeout"}

    tests = re.findall(r"TEST (\d+) done \(errors=(\d+)\)", sim)
    total = None
    m = re.search(r"HAS_FAILURES \((\d+) errors\)", sim)
    if m:
        total = int(m.group(1))
    elif "ALL_PASS" in sim:
        total = 0
    if total is None:
        m = re.search(r"(\d+)\s*/\s*(\d+) bit-exact \((\d+) mismatch", sim)
        if m:
            total = int(m.group(3))
    markers = len(re.findall(r"^\s*(CHECK|COVER|SIMEND)\b", sim, re.M))
    return {"state": "ran", "errors": total, "warns": warns,
            "tests": len(tests), "markers": markers}


def gate_status():
    cfg = os.path.join(HERE, "simcheck.json")
    if not (os.path.exists(cfg) and os.path.exists(SIMCHECK)):
        return {"state": "absent"}
    rc, out = run([sys.executable, SIMCHECK, "--config", cfg, "--all"],
                  cwd=HERE, timeout=900)
    reasons = re.findall(r"^\s*FAIL: (.+)$", out.replace("\r\n", "\n"), re.M)
    uniq = []
    for r in reasons:
        r = r.strip()
        if r not in uniq:
            uniq.append(r)
    covers = len([r for r in uniq if "never reported a COVER" in r
                  or "never exercised" in r])
    # 編譯失敗時 gate 只會報 compile error，沒有 cover 可缺 ——
    # 那時候 missing_covers=0 是「沒跑」不是「補齊了」，要標出來。
    compile_broken = any("compile" in r for r in uniq)
    return {"state": "pass" if rc == 0 else "fail",
            "nfail": len(uniq), "missing_covers": covers,
            "not_run": compile_broken}


def snapshot():
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "sig": sig(), "units": {}}
    for name in UNITS:
        rec["units"][name] = check_unit(name)
    rec["gate"] = gate_status()
    return rec


def fmt(rec):
    parts = []
    for name, u in rec["units"].items():
        s = u.get("state")
        if s == "ran":
            e = u.get("errors")
            tag = "PASS" if e == 0 else ("err=%s" % e)
            extra = ""
            if u.get("warns"):
                extra += " w%d" % u["warns"]
            if u.get("markers"):
                extra += " m%d" % u["markers"]
            parts.append("%s:%s%s" % (name, tag, extra))
        elif s == "compile_fail":
            parts.append("%s:COMPILE_FAIL(%s)" % (name, u.get("errors")))
        elif s == "hang":
            parts.append("%s:HANG" % name)
        else:
            parts.append("%s:%s" % (name, s))
    g = rec["gate"]
    if g.get("state") == "fail":
        detail = "(%d fail,%d cover%s)" % (
            g.get("nfail", 0), g.get("missing_covers", 0),
            ",NOT_RUN" if g.get("not_run") else "")
    else:
        detail = ""
    parts.append("gate:%s%s" % (g.get("state"), detail))
    return "%s  %s" % (rec["t"], "  ".join(parts))


def append(rec):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def report():
    if not os.path.exists(LOG):
        print("還沒有 progress.log")
        return 1
    recs = []
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    if not recs:
        print("progress.log 是空的")
        return 1

    print("=" * 78)
    print("時間軸（%d 筆）" % len(recs))
    print("=" * 78)
    for r in recs:
        print(fmt(r))

    print()
    print("=" * 78)
    print("趨勢")
    print("=" * 78)
    for name in UNITS:
        seq = [(r["t"], r["units"].get(name, {})) for r in recs]
        ran = [(t, u.get("errors")) for t, u in seq
               if u.get("state") == "ran" and u.get("errors") is not None]
        regress, nochange, cf_streak, max_cf = 0, 0, 0, 0
        prev = None
        for _, e in ran:
            if prev is not None:
                if e > prev:
                    regress += 1
                elif e == prev:
                    nochange += 1
            prev = e
        for _, u in seq:
            if u.get("state") == "compile_fail":
                cf_streak += 1
                max_cf = max(max_cf, cf_streak)
            else:
                cf_streak = 0
        if ran:
            print("  %-14s 首次 err=%s → 最後 err=%s ；退步 %d 次、"
                  "改了沒變 %d 次、連續編不過最多 %d 次"
                  % (name, ran[0][1], ran[-1][1], regress, nochange, max_cf))
        else:
            print("  %-14s 從未成功跑起來（連續編不過最多 %d 次）"
                  % (name, max_cf))

    g = [r["gate"] for r in recs if r.get("gate")]
    if g:
        first, last = g[0], g[-1]
        print("  %-14s %s → %s（缺 cover %s → %s）"
              % ("gate", first.get("state"), last.get("state"),
                 first.get("missing_covers"), last.get("missing_covers")))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    if a.report:
        return report()

    if a.once:
        rec = snapshot()
        append(rec)
        print(fmt(rec))
        return 0

    last = None
    while True:
        s = sig()
        if s != last:
            rec = snapshot()
            append(rec)
            line = fmt(rec)
            sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
            last = rec["sig"]
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

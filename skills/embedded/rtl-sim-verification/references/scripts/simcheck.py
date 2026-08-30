#!/usr/bin/env python3
"""
simcheck.py - generic simulation / test acceptance gate.

Runs a testbench (or any test command), then decides PASS/FAIL from
machine-readable marker lines instead of from prose in the log.

Why: "no ERROR in the log" is not a pass. A run that checked zero cases,
or that silently never exercised a required scenario, must fail loudly.
Fail closed -- silence is never a pass.

--------------------------------------------------------------------------
MARKER PROTOCOL (what the testbench must print)
--------------------------------------------------------------------------
  CHECK  <name> <n_checked> <n_bad>   one per checker; n_checked==0 FAILS
  ASSERT <name> [<n_violations>]      any violation FAILS (count optional)
  COVER  <name> <n_hits>              scenario actually exercised; 0 FAILS
  SIMEND <ok|fail>                    printed once at the end; absent FAILS

Any other output is ignored, so human-readable $display lines can stay.

Verilog example:
    $display("CHECK data_integrity %0d %0d", n_checked, n_bad);
    $display("COVER back_to_back %0d", n_b2b);
    $display("ASSERT crosses_4kb %0d", n_viol);
    $display("SIMEND %s", (errors == 0) ? "ok" : "fail");

The same protocol works for cocotb, C, Python, or a shell test -- this
script only cares about the marker lines, not the language.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------
  # Icarus Verilog (built in)
  python simcheck.py --tb tb/tb_foo.v --src rtl/foo.v --top tb_foo

  # require scenarios that would otherwise silently never run
  python simcheck.py --tb ... --src ... \
      --require-cover back_to_back,boundary_cross,backpressure

  # parameter sweep (one run per value)
  python simcheck.py --tb ... --src ... --top tb_foo \
      --sweep DATA_WIDTH=32,64,128,256

  # any other toolchain / language
  python simcheck.py --cmd "pytest -q tests/" --require-cover slow_path
  python simcheck.py --cmd "make sim" --cwd build/

  # reusable config
  python simcheck.py --config simcheck.json

Exit 0 = PASS, 1 = FAIL, 2 = usage/tool error.
A machine-readable SIMCHECK_RESULT json line is always printed last.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

DEFAULT_TOOL_DIRS = [
    r"C:\iverilog\bin",
    "/c/iverilog/bin",
    "/usr/local/bin",
    "/usr/bin",
]


def find_tool(name):
    """Locate a tool even when it is not on PATH."""
    p = shutil.which(name)
    if p:
        return p
    for d in DEFAULT_TOOL_DIRS:
        for cand in (os.path.join(d, name), os.path.join(d, name + ".exe")):
            if os.path.isfile(cand):
                return cand
    return None


def run(cmd, cwd=None, timeout=600):
    try:
        r = subprocess.run(cmd, cwd=cwd, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        return 124, out + "\n*** TIMEOUT after %ds ***\n" % timeout
    except FileNotFoundError as e:
        return 127, "command not found: %s" % e


RE_CHECK = re.compile(r"^\s*CHECK\s+(\S+)\s+(\d+)\s+(\d+)\s*$", re.M)
RE_ASSERT = re.compile(r"^\s*ASSERT\s+(\S+)(?:\s+(\d+))?\s*$", re.M)
RE_COVER = re.compile(r"^\s*COVER\s+(\S+)\s+(\d+)\s*$", re.M)
RE_SIMEND = re.compile(r"^\s*SIMEND\s+(ok|fail)\s*$", re.M | re.I)

RE_FATAL = re.compile(
    r"(TIMEOUT|WATCHDOG|segmentation fault|core dumped|"
    r"\*\* Fatal|UVM_FATAL)", re.I)


def parse_log(text):
    checks = [(m.group(1), int(m.group(2)), int(m.group(3)))
              for m in RE_CHECK.finditer(text)]
    asserts = []
    for m in RE_ASSERT.finditer(text):
        asserts.append((m.group(1),
                        int(m.group(2)) if m.group(2) is not None else 1))
    covers = [(m.group(1), int(m.group(2))) for m in RE_COVER.finditer(text)]
    m = RE_SIMEND.search(text)
    return checks, asserts, covers, (m.group(1).lower() if m else None)


def _merge(pairs):
    """Sum duplicate names -- a checker may report once per sub-test."""
    d = {}
    for item in pairs:
        name = item[0]
        vals = item[1:]
        if name not in d:
            d[name] = [0] * len(vals)
        for i, v in enumerate(vals):
            d[name][i] += v
    return d


def judge(text, require_covers, allow_no_markers=False):
    checks, asserts, covers, simend = parse_log(text)
    reasons = []

    chk = _merge(checks)
    asr = _merge(asserts)
    cov = _merge(covers)

    if not chk and not allow_no_markers:
        reasons.append("no CHECK lines -- the test prints no machine-readable "
                       "results (see marker protocol at the top of this file)")

    for name in sorted(chk):
        n_checked, n_bad = chk[name]
        if n_checked == 0:
            reasons.append("check '%s' ran 0 cases "
                           "(a zero-case PASS is a failure)" % name)
        if n_bad:
            reasons.append("check '%s': %d of %d mismatched"
                           % (name, n_bad, n_checked))

    for name in sorted(asr):
        n = asr[name][0]
        if n:
            reasons.append("assertion '%s' violated %d time(s)" % (name, n))

    for name in require_covers:
        if name not in cov:
            reasons.append("required scenario '%s' never reported a COVER line"
                           % name)
        elif cov[name][0] == 0:
            reasons.append("required scenario '%s' was never exercised "
                           "(0 hits)" % name)

    for name in sorted(cov):
        if cov[name][0] == 0 and name not in require_covers:
            reasons.append("scenario '%s' never exercised (0 hits)" % name)

    if simend is None:
        reasons.append("no SIMEND line -- the run hung, crashed, or "
                       "ended early")
    elif simend == "fail":
        reasons.append("the test itself reported SIMEND fail")

    fatal = RE_FATAL.search(text)
    if fatal:
        reasons.append("fatal marker in log: %r" % fatal.group(0))

    stats = {
        "checks": dict((k, {"checked": v[0], "bad": v[1]})
                       for k, v in chk.items()),
        "asserts": dict((k, v[0]) for k, v in asr.items()),
        "covers": dict((k, v[0]) for k, v in cov.items()),
        "total_checked": sum(v[0] for v in chk.values()),
        "total_bad": sum(v[1] for v in chk.values()),
        "simend": simend,
    }
    return (not reasons), reasons, stats


DEFAULT_FATAL_WARNINGS = [
    r"[Tt]runcated",          # constant wider than its context -> folded branches
    r"implicit(ly)? declar",  # typo'd signal silently becomes a 1-bit wire
    r"[Ii]nferring latch",    # missing assignment in a combinational branch
]


def build_and_run_iverilog(tb, srcs, top, params, workdir, incdirs, defines,
                           timeout, extra_flags, keep, run_in=None,
                           fatal_warnings=None):
    """Compile with iverilog, then run vvp -- optionally from `run_in`.

    run_in matters whenever the testbench opens files with a relative path
    ($readmemh("expected.hex"), $fopen(...)): those resolve against the
    process CWD, not against the testbench's own directory. Running from
    the wrong place makes every case mismatch, which looks exactly like
    broken RTL.
    """
    iverilog = find_tool("iverilog")
    vvp = find_tool("vvp")
    if not iverilog or not vvp:
        return None, ("iverilog/vvp not found; looked on PATH and in %s"
                      % ", ".join(DEFAULT_TOOL_DIRS))

    out = os.path.join(workdir, "simcheck_%s.vvp" % (top or "tb"))
    cmd = [iverilog, "-g2012", "-o", out]
    for d in incdirs:
        cmd += ["-I", d]
    for d in defines:
        cmd += ["-D", d]
    if top:
        cmd += ["-s", top]
        for k in sorted(params):
            cmd += ["-P%s.%s=%s" % (top, k, params[k])]
    elif params:
        return None, "--param/--sweep needs --top (iverilog -P needs a scope)"
    cmd += list(extra_flags) + [tb] + list(srcs)

    rc, log = run(cmd, timeout=timeout)
    if rc != 0:
        return None, "compile failed (exit %d):\n%s" % (rc, log)

    # Some warnings mean a silently wrong design, not style noise. A truncated
    # constant in a case selector, for instance, folds branches on top of each
    # other -- it compiles, runs, and returns the wrong register.
    if fatal_warnings:
        hits = []
        for line in log.replace("\r\n", "\n").splitlines():
            for pat in fatal_warnings:
                if re.search(pat, line):
                    hits.append(line.strip())
                    break
        if hits:
            uniq = []
            for h in hits:
                if h not in uniq:
                    uniq.append(h)
            return None, ("compile warnings treated as fatal (%d):\n  %s"
                          % (len(hits), "\n  ".join(uniq[:8])))

    if run_in and not os.path.isdir(run_in):
        return None, ("run_in directory does not exist: %s "
                      "(the TB likely needs data files there)" % run_in)
    rc, simlog = run([vvp, out], cwd=run_in, timeout=timeout)
    if not keep:
        try:
            os.remove(out)
        except OSError:
            pass
    if rc == 124:
        simlog += "\nTIMEOUT\n"
    return simlog, None


def emit(title, ok, reasons, stats, log, verbose):
    bar = "=" * 68
    print(bar)
    print("%s : %s" % (title, "PASS" if ok else "FAIL"))
    print(bar)
    for name in sorted(stats["checks"]):
        d = stats["checks"][name]
        flag = "ok " if d["bad"] == 0 and d["checked"] > 0 else "BAD"
        print("  [%s] check  %-26s %7d checked, %d bad"
              % (flag, name, d["checked"], d["bad"]))
    for name in sorted(stats["covers"]):
        h = stats["covers"][name]
        print("  [%s] cover  %-26s %7d hits"
              % ("ok " if h > 0 else "BAD", name, h))
    for name in sorted(stats["asserts"]):
        n = stats["asserts"][name]
        print("  [%s] assert %-26s %7d violations"
              % ("ok " if n == 0 else "BAD", name, n))
    if not ok:
        print("-" * 68)
        for r in reasons:
            print("  FAIL: %s" % r)
    if verbose or not ok:
        tail = log.strip().splitlines()[-40:]
        if tail:
            print("-" * 68)
            print("  log tail:")
            for line in tail:
                print("    " + line)
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Generic simulation/test acceptance gate. "
                    "Fails closed: zero cases and missing scenarios are "
                    "failures, not passes.")
    ap.add_argument("--config", help="JSON config supplying any of these opts")
    ap.add_argument("--block", action="append", default=[],
                    help="run one named block from the config's \"blocks\" "
                         "map (repeatable)")
    ap.add_argument("--all", action="store_true",
                    help="run every block in the config that is not "
                         "status=pending")
    ap.add_argument("--list", action="store_true",
                    help="list the blocks in the config and exit")
    ap.add_argument("--tb", help="testbench file (iverilog mode)")
    ap.add_argument("--src", action="append", default=[],
                    help="RTL source, repeatable (iverilog mode)")
    ap.add_argument("--top", help="top module name (-s)")
    ap.add_argument("--cmd", help="run this command instead of iverilog")
    ap.add_argument("--cwd", help="working directory for --cmd")
    ap.add_argument("--param", action="append", default=[], metavar="K=V")
    ap.add_argument("--sweep", metavar="K=V1,V2,...",
                    help="run once per value of one parameter")
    ap.add_argument("--require-cover", default="",
                    help="comma-separated scenarios that MUST be exercised")
    ap.add_argument("--incdir", action="append", default=[])
    ap.add_argument("--define", action="append", default=[])
    ap.add_argument("--flag", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--workdir")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--fatal-warnings", action="store_true",
                    help="treat silently-wrong compile warnings (truncated "
                         "constants, implicit declarations, inferred latches) "
                         "as failures; on by default for config blocks unless "
                         "the block sets fatal_warnings:false")
    ap.add_argument("--no-fatal-warnings", action="store_true",
                    help="disable the above")
    ap.add_argument("--allow-no-markers", action="store_true",
                    help="tolerate a TB with no CHECK lines "
                         "(only while migrating an old testbench)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    cfg = {}
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)

    blocks = cfg.get("blocks") or {}
    base = os.path.dirname(os.path.abspath(a.config)) if a.config else "."

    if a.list:
        if not blocks:
            print("config has no \"blocks\" map")
            return 2
        print("%-18s %-12s %-15s %s"
              % ("BLOCK", "STATUS", "STAGE", "REQUIRED COVERS"))
        for name in blocks:
            b = blocks[name]
            print("%-18s %-12s %-15s %s"
                  % (name, b.get("status", "?"), b.get("stage", "-"),
                     ",".join(b.get("require_cover", [])) or "-"))
            for k in ("blocked_by", "_gotcha"):
                if b.get(k):
                    print("%-18s   ^ %s: %s" % ("", k.lstrip("_"), b[k]))
        stages = cfg.get("_stages")
        if stages:
            print()
            print("STAGES:")
            for k in sorted(stages):
                if not k.startswith("_"):
                    print("  %-15s %s" % (k, stages[k]))
            if stages.get("_rule"):
                print("  RULE: %s" % stages["_rule"])
        return 0

    # Decide which unit(s) of work to run.
    if blocks and (a.block or a.all):
        if a.all:
            names = [n for n in blocks
                     if blocks[n].get("status") != "pending"]
        else:
            names = []
            for n in a.block:
                if n not in blocks:
                    ap.error("no block named %r in %s (try --list)"
                             % (n, a.config))
                names.append(n)
        units = [(n, blocks[n]) for n in names]
    elif blocks and not (a.tb or a.cmd):
        ap.error("config defines blocks; pass --block <name>, --all, "
                 "or --list")
    else:
        units = [(None, cfg)]

    all_ok = True
    results = []

    for block_name, src_cfg in units:
        tb = a.tb or src_cfg.get("tb")
        srcs = a.src or src_cfg.get("src", [])
        top = a.top or src_cfg.get("top")
        cmd = a.cmd or src_cfg.get("cmd")
        cwd = a.cwd or src_cfg.get("cwd")
        run_in = src_cfg.get("run_in")

        # paths in a config are relative to the config file
        if a.config and not a.tb:
            if tb:
                tb = os.path.join(base, tb)
            srcs = [os.path.join(base, s) for s in srcs]
            if cwd:
                cwd = os.path.join(base, cwd)
            if run_in:
                run_in = os.path.join(base, run_in)

        if not cmd and (not tb or not srcs):
            ap.error("need --tb with --src (iverilog mode), or --cmd"
                     + (" [block %s]" % block_name if block_name else ""))

        params = dict(src_cfg.get("params", {}))
        for kv in a.param:
            k, _, v = kv.partition("=")
            params[k] = v

        req_raw = a.require_cover or ",".join(src_cfg.get("require_cover", []))
        req = [s.strip() for s in req_raw.split(",") if s.strip()]

        incdirs = a.incdir or src_cfg.get("incdir", [])
        defines = a.define or src_cfg.get("define", [])
        flags = a.flag or src_cfg.get("flags", [])
        workdir = (a.workdir or src_cfg.get("workdir")
                   or cfg.get("workdir") or tempfile.gettempdir())
        timeout = (a.timeout if a.timeout != 600
                   else src_cfg.get("timeout", cfg.get("timeout", 600)))

        # A silently-wrong compile is worse than a loud one: default to fatal
        # for config-driven runs, opt-in for ad-hoc command lines.
        fw_cfg = src_cfg.get("fatal_warnings", cfg.get("fatal_warnings"))
        if a.no_fatal_warnings:
            fatal_warnings = None
        elif a.fatal_warnings or fw_cfg is True or (
                fw_cfg is None and block_name is not None):
            fatal_warnings = DEFAULT_FATAL_WARNINGS
        elif isinstance(fw_cfg, list):
            fatal_warnings = fw_cfg
        else:
            fatal_warnings = None

        sweep = a.sweep or src_cfg.get("sweep")
        runs = []
        if sweep:
            key, _, vals = sweep.partition("=")
            for v in [x.strip() for x in vals.split(",") if x.strip()]:
                p = dict(params)
                p[key] = v
                runs.append(("%s=%s" % (key, v), p))
        else:
            runs.append(("run", params))

        for label, p in runs:
            if block_name:
                label = "%s [%s]" % (block_name, label)

            if cmd:
                if p:
                    label += " (params ignored in --cmd mode)"
                rc, log = run(cmd if isinstance(cmd, list)
                              else shlex.split(cmd),
                              cwd=cwd, timeout=timeout)
                err = log if rc == 127 else None
            else:
                log, err = build_and_run_iverilog(
                    tb, srcs, top, p, workdir, incdirs, defines, timeout,
                    flags, a.keep, run_in, fatal_warnings)

            if err:
                print("=" * 68)
                print("%s : FAIL" % label)
                print("=" * 68)
                print("  FAIL: %s" % err)
                print()
                all_ok = False
                results.append({"run": label, "block": block_name,
                                "ok": False, "error": err})
                continue

            ok, reasons, stats = judge(log, req, a.allow_no_markers)
            emit(label, ok, reasons, stats, log, a.verbose)
            all_ok = all_ok and ok
            results.append({"run": label, "block": block_name, "ok": ok,
                            "reasons": reasons, "stats": stats})

    if len(results) > 1:
        print("=" * 68)
        print("SUMMARY")
        print("=" * 68)
        for r in results:
            print("  %-4s %s" % ("PASS" if r["ok"] else "FAIL", r["run"]))
        print()

    print("SIMCHECK_RESULT " + json.dumps({"ok": all_ok, "runs": results},
                                          ensure_ascii=False))
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)

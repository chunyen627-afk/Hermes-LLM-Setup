"""撞到工具呼叫上限時自動接續，讓長任務不用人工介入。

Hermes 每輪有工具呼叫上限（`hermes chat --max-turns N`）。撞到時它會被要求
「寫一份總結、不要再呼叫工具」，然後那一輪就結束 —— 任務其實沒做完。

這支程式盯著 session，一偵測到那個情況就把它自己寫的交接報告當成新的
prompt 重新派工，直到任務真的完成或達到 --max-relays 上限。

用法：
    python _autorelay.py --task-file 題目.txt
    python _autorelay.py --task-file 題目.txt --max-turns 500 --max-relays 10
    python _autorelay.py --resume-last          # 接續最近一個撞上限的 session

為什麼要用 --query-file 而不是 -q：
    題目走命令列會被 shell 解讀，中文、換行、引號都可能壞掉。
    走檔案就是原文照送。
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time

DB = os.path.join(os.environ["LOCALAPPDATA"], "hermes", "state.db")
HERMES = os.path.join(os.environ["LOCALAPPDATA"], "hermes", "hermes-agent",
                      "venv", "Scripts", "hermes.exe")

# Hermes 撞到上限時塞進對話的那句話（agent/context_compressor.py）
LIMIT_MARK = "reached the maximum number of tool-calling iterations"

# 它講這些話代表任務真的做完了，不要再接續
DONE_MARK = re.compile(
    r"全部通過|都通過了|任務完成|已完成.{0,6}(全部|所有)|"
    r"ALL_PASS|ALL_INDEPENDENT_CHECKS_PASS|0 mismatches",
    re.I)


def out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def latest_cli_session():
    c = sqlite3.connect(DB)
    r = c.execute("SELECT id FROM sessions WHERE source='cli' "
                  "ORDER BY started_at DESC LIMIT 1").fetchone()
    c.close()
    return r[0] if r else None


def tail_messages(sid, n=4):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT role, coalesce(content,'') FROM messages "
        "WHERE session_id=? ORDER BY rowid DESC LIMIT ?", (sid, n)).fetchall()
    c.close()
    return list(reversed(rows))


def hit_limit(sid):
    """撞上限就回傳它的總結報告，否則回 None。

    特徵：倒數第二則是那句上限提示（role=user），最後一則是它的總結。
    """
    rows = tail_messages(sid, 4)
    if len(rows) < 2:
        return None
    for i in range(len(rows) - 1):
        role, content = rows[i]
        if role == "user" and LIMIT_MARK in content:
            last_role, last_content = rows[-1]
            if last_role == "assistant" and last_content.strip():
                return last_content.strip()
    return None


def run_once(query_file, max_turns, log_path):
    """跑一輪 hermes chat，回傳它結束後的 session id。"""
    before = latest_cli_session()
    cmd = [HERMES, "chat", "--query-file", query_file,
           "--max-turns", str(max_turns)]
    with open(log_path, "ab") as f:
        f.write(("\n=== relay start %s ===\n"
                 % time.strftime("%H:%M:%S")).encode())
        f.flush()
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    after = latest_cli_session()
    return after if after != before else after


def build_followup(report, original_task):
    """把它自己的交接報告變成下一輪的 prompt。"""
    return (
        "接續上一輪的工作。你上一輪撞到工具呼叫次數上限被中斷，"
        "但留下了下面這份交接說明。\n\n"
        "先讀專案檔案確認現況，再從卡住的地方繼續 —— "
        "不要從頭重做已經完成的部分。\n\n"
        "=== 你上一輪寫的交接說明 ===\n"
        + report
        + "\n\n=== 原本的任務 ===\n"
        + original_task
        + "\n\n⚠ 提醒：\n"
        "1. ctx 到 60% 就寫 HANDOFF.md（已確認行不通的做法最重要）\n"
        "2. 全程用繁體中文\n"
        "3. 驗收標準沒達成就誠實說，不要改標準遷就結果\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-file", help="題目檔案（UTF-8）")
    ap.add_argument("--resume-last", action="store_true",
                    help="接續最近一個撞上限的 session，不用給題目")
    ap.add_argument("--max-turns", type=int, default=500,
                    help="每輪的工具呼叫上限（預設 500，Hermes 內建預設只有 90）")
    ap.add_argument("--max-relays", type=int, default=10,
                    help="最多自動接續幾次（防無限迴圈）")
    ap.add_argument("--log", default=None, help="輸出檔")
    args = ap.parse_args()

    if not args.task_file and not args.resume_last:
        ap.error("要給 --task-file 或 --resume-last")

    log_path = args.log or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "autorelay_%s.log" % time.strftime("%Y%m%d_%H%M%S"))

    work_dir = os.path.dirname(log_path)
    followup_file = os.path.join(work_dir, "_autorelay_followup.txt")

    if args.resume_last:
        sid = latest_cli_session()
        report = hit_limit(sid) if sid else None
        if not report:
            out("最近一個 session 沒有撞上限的跡象，沒東西可接續。")
            return 1
        original = "（接續模式，原題不明 —— 依交接說明繼續）"
        out("接續 session %s" % sid)
    else:
        with open(args.task_file, encoding="utf-8") as f:
            original = f.read()
        out("開跑：%s（上限 %d，最多接續 %d 次）"
            % (args.task_file, args.max_turns, args.max_relays))
        sid = run_once(args.task_file, args.max_turns, log_path)
        out("  第 1 輪結束，session=%s" % sid)
        report = hit_limit(sid) if sid else None

    relays = 0
    while report and relays < args.max_relays:
        if DONE_MARK.search(report):
            out("  報告裡有完成訊號，不再接續。")
            break
        relays += 1
        out("  → 撞上限，自動接續第 %d 次" % relays)
        with open(followup_file, "w", encoding="utf-8") as f:
            f.write(build_followup(report, original))
        sid = run_once(followup_file, args.max_turns, log_path)
        out("  第 %d 輪結束，session=%s" % (relays + 1, sid))
        report = hit_limit(sid) if sid else None

    if relays >= args.max_relays and report:
        out("已達接續上限 %d 次，停止。任務可能還沒做完。" % args.max_relays)
    elif not report:
        out("最後一輪沒有撞上限 —— 任務應該跑完了。")
    out("log: %s" % log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

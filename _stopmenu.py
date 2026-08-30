# -*- coding: utf-8 -*-
"""任務停掉時跳出來的選單（也支援全自動模式）。

橋接器偵測到 27B 的任務停了，就會叫這支：
    python _stopmenu.py --reason limit --project "C:\\path\\to\\proj"
    python _stopmenu.py --reason done  --project "C:\\path\\to\\proj"

加 --auto 就不問人，直接照 reason 決定動作（防呆在 _autoguard.py，
這裡只負責執行）：
    limit  → 用它自己寫的交接報告接續（沒做完，被上限打斷）
    其他   → 用它自己寫的收尾報告做下一階段

--gate-reason 是驗收關卡不通過的理由，會寫進派工提示詞最前面。
只印在 console 沒有用 —— 它下一輪不會知道自己被擋在哪。
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTORELAY = os.path.join(HERE, "_autorelay.py")
TASK_FILE = os.path.join(HERE, "next_task.txt")
NEW_TASK_FILE = os.path.join(HERE, "task.txt")
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "state.db")

SIMCHECK = ("C:/Users/pjunm/AppData/Local/hermes/skills/embedded/"
            "rtl-sim-verification/references/scripts/simcheck.py")


def out(s=""):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def run_resume():
    out("\n接續中…（這個視窗會顯示進度，不要關）\n")
    subprocess.run([sys.executable, AUTORELAY, "--resume-last"])


def _latest_sid():
    try:
        c = sqlite3.connect(DB)
        r = c.execute(
            "SELECT id FROM sessions WHERE source IN ('cli','desktop','tui') "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        c.close()
        return r[0] if r else None
    except Exception:
        return None


def _last_report(sid=None):
    """撈它最後一則有內容的訊息 —— 那就是收尾報告。"""
    sid = sid or _latest_sid()
    if not sid:
        return ""
    try:
        c = sqlite3.connect(DB)
        rows = c.execute(
            "SELECT coalesce(content,'') FROM messages "
            "WHERE session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 12", (sid,)).fetchall()
        c.close()
    except Exception:
        return ""
    for (t,) in rows:
        if t and len(t.strip()) > 80:
            return t.strip()
    return ""


def _gate_block(gate_reason):
    """把守衛的判定理由變成提示詞的第一段。

    它宣告完成但關卡沒過時，「關卡說缺什麼」比它報告裡的「下一步」
    更該優先處理。
    """
    if not gate_reason:
        return ""
    lines = [
        "⛔ 你上一輪宣告完成，但驗收關卡沒過。**先處理這些，再談其他**：",
        "",
        gate_reason.strip(),
        "",
        "跑這個看完整結果：",
        "```",
        "python " + SIMCHECK + " --config simcheck.json --all",
        "```",
        "exit 0 才算完成。",
        "",
        "「spec 項目在原始碼裡找不到」的意思是：架構文件承諾了這個東西，",
        "但程式碼裡沒有 —— 要嘛實作，要嘛改文件說明為什麼不做",
        "（理由和日期寫進 HANDOFF 的「已確認行不通的做法」）。",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def run_next_stage(project, auto=False, gate_reason=""):
    """接著做下一階段：用它自己寫的「下一步」當題目。

    它每次收尾都會列「下一步（如果要繼續）」，那份比我們憑空想的準 ——
    它知道自己做到哪、什麼還沒做。
    """
    report = _last_report()
    if not report:
        if auto:
            out("讀不到上一輪的報告，自動模式不亂猜題目，停在這裡。")
            return
        out("\n讀不到上一輪的報告，改用『派新任務』。")
        run_new_task(project)
        return

    proj = project or "（專案目錄請自己補）"
    prompt = (
        _gate_block(gate_reason)
        + "接續 " + proj + "。先讀 HANDOFF.md、NOTE_FROM_USER.md 和 "
        "ARCHITECTURE.md 確認現況，再從下面這份你自己寫的收尾報告接著做。\n\n"
        "=== 你上一輪的收尾報告 ===\n"
        + report.strip()
        + "\n\n=== 這一輪要做的 ===\n"
        "照報告裡「下一步」那段往下做。做之前先確認那些步驟現在還合理"
        "（有沒有更該先做的、有沒有已經做掉的）。\n\n"
        "⚠ 三件提醒：\n"
        "1. 做完一個可驗證的單元就停下來報告，不要一路衝到撞上限\n"
        "2. ctx 到 60% 就更新 HANDOFF.md\n"
        "3. 全程用繁體中文；沒做到的誠實說，不要改標準遷就結果\n")

    with open(TASK_FILE, "w", encoding="utf-8") as f:
        f.write(prompt)

    out("\n題目已寫進 " + TASK_FILE)
    if not auto:
        ans = input("\n直接派工？(Y/n，Enter=是) ").strip().lower()
        if ans in ("n", "no"):
            out("已取消。題目留在 next_task.txt，你可以編輯後再派。")
            return

    out("\n派工中…（這個視窗會顯示進度，不要關）\n")
    subprocess.run([sys.executable, AUTORELAY, "--task-file", TASK_FILE])


def run_new_task(project):
    """派一個全新的題目 —— 讓使用者自己寫。"""
    if not os.path.exists(NEW_TASK_FILE):
        with open(NEW_TASK_FILE, "w", encoding="utf-8") as f:
            f.write("（把題目寫在這裡，存檔後重新選這個選項）\n")
    out("\n題目檔：" + NEW_TASK_FILE)
    out("\n用記事本打開、把題目寫進去、存檔，然後重新選這個選項。")
    try:
        subprocess.Popen(["notepad", NEW_TASK_FILE])
    except Exception:
        pass
    txt = ""
    try:
        with open(NEW_TASK_FILE, encoding="utf-8") as f:
            txt = f.read().strip()
    except Exception:
        pass
    if not txt or txt.startswith("（把題目"):
        out("\n題目還是空的，先寫好再來。")
        return
    out("\n=== 題目 ===")
    out(txt[:600])
    ans = input("\n確定用這個題目派工？(y/N) ").strip().lower()
    if ans not in ("y", "yes"):
        out("已取消。")
        return
    out("\n派工中…\n")
    subprocess.run([sys.executable, AUTORELAY, "--task-file", NEW_TASK_FILE])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="unknown",
                    help="limit=撞上限 / done=正常結束 / unknown")
    ap.add_argument("--project", default="", help="專案根目錄")
    ap.add_argument("--gate-reason", default="",
                    help="驗收關卡不通過的理由 —— 會寫進派工提示詞")
    ap.add_argument("--auto", action="store_true",
                    help="不問人，直接照 reason 決定動作")
    args = ap.parse_args()

    if args.auto:
        # 全自動：撞上限就接續，正常結束就做下一階段。
        # 防呆在 _autoguard.py，這裡只負責執行。
        out("[自動模式] reason=%s project=%s" % (args.reason, args.project))
        if args.gate_reason:
            out("[自動模式] 關卡理由會寫進題目：" + args.gate_reason[:80])
        if args.reason == "limit":
            run_resume()
        else:
            run_next_stage(args.project, auto=True,
                           gate_reason=args.gate_reason)
        return 0

    out("=" * 56)
    out("  27B 任務停了")
    out("=" * 56)
    if args.reason == "limit":
        out("  原因：撞到工具呼叫上限 —— 任務其實還沒做完")
    elif args.reason == "done":
        out("  原因：正常結束（不是撞上限）")
        out("  注意：")
        out("    - 它自己說有沒有做完的部分 —— 看一下最後那則訊息再決定")
    else:
        out("  原因：不明")
    if args.project:
        out("  專案：" + args.project)
        hd = os.path.join(args.project, "HANDOFF.md")
        out("  交接文件：" + ("有" if os.path.exists(hd) else
                              "沒有（接續前先叫它寫）"))
    if args.gate_reason:
        out("")
        out("  ⛔ 驗收關卡沒過：")
        for ln in args.gate_reason.split("；")[:4]:
            out("     - " + ln.strip()[:70])
    out("")

    opts = [
        ("1", "接續上一輪（撞上限用；用它自己的交接報告）"),
        ("2", "做下一階段（用它自己的收尾報告當題目）"),
        ("3", "派一個新題目"),
        ("q", "什麼都不做"),
    ]
    default = "1" if args.reason == "limit" else "2"
    for key, label in opts:
        mark = "  ← 預設" if key == default else ""
        out("  [%s] %s%s" % (key, label, mark))
    out("")

    pick = input("  選擇（直接按 Enter = 預設）： ").strip() or default
    if pick == "1":
        run_resume()
    elif pick == "2":
        run_next_stage(args.project, gate_reason=args.gate_reason)
    elif pick == "3":
        run_new_task(args.project)
    else:
        out("\n什麼都沒做。")
        return 0

    input("按 Enter 關閉…")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        out("\n已取消。")
        sys.exit(130)

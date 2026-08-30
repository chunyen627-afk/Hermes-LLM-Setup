"""任務停掉時跳出來的選單。由橋接器偵測到停止後自動開啟。

橋接器本身是背景執行的（Hermes 一直在打它的 API），視窗讀不到鍵盤，
所以選單要另開一個獨立的 console 視窗。

用法（橋接器會自己帶參數）：
    python _stopmenu.py --reason limit --project "C:\\path\\to\\proj"
    python _stopmenu.py --reason done  --project "C:\\path\\to\\proj"
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTORELAY = os.path.join(HERE, "_autorelay.py")
TASK_FILE = os.path.join(HERE, "task.txt")
HERMES = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes",
                      "hermes-agent", "venv", "Scripts", "hermes.exe")


def out(s=""):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def run_resume():
    out("\n接續中…（這個視窗會顯示進度，不要關）\n")
    subprocess.run([sys.executable, AUTORELAY, "--resume-last"])


def _last_report(sid=None):
    """抓它最後那則報告（通常含「下一步」）。"""
    import sqlite3
    db = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "state.db")
    try:
        c = sqlite3.connect(db)
        if not sid:
            r = c.execute("SELECT id FROM sessions WHERE source='cli' "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
            sid = r[0] if r else None
        if not sid:
            c.close()
            return ""
        r = c.execute("SELECT coalesce(content,'') FROM messages "
                      "WHERE session_id=? AND role='assistant' "
                      "ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
        c.close()
        return (r[0] or "") if r else ""
    except Exception:
        return ""


def run_next_stage(project, auto=False):
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
        "接續 " + proj + "。先讀 HANDOFF.md 和 ARCHITECTURE.md 確認現況，"
        "再從下面這份你自己寫的收尾報告接著做。\n\n"
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

    out("\n已根據它自己寫的「下一步」產生題目：")
    out("-" * 50)
    # 只印「這一輪要做的」那段，前面的報告太長
    out(prompt.split("=== 這一輪要做的 ===")[-1].strip())
    out("-" * 50)
    out("（完整題目含它的收尾報告，已寫進 task.txt）")

    if auto:
        out("\n[自動模式] 直接派工（--max-turns 500）\n")
        subprocess.run([HERMES, "chat", "--query-file", TASK_FILE,
                        "--max-turns", "500"])
        return

    ans = input("\n直接派工？(Y/n，Enter=是) ").strip().lower()
    if ans and ans != "y":
        out("已取消。題目留在 task.txt，你可以編輯後用選項 2 派工。")
        try:
            os.startfile(TASK_FILE)
        except Exception:
            pass
        return

    out("\n派工中（--max-turns 500）。這個視窗可以縮小，不要關。\n")
    subprocess.run([HERMES, "chat", "--query-file", TASK_FILE,
                    "--max-turns", "500"])


def run_new_task(project):
    if not os.path.exists(TASK_FILE):
        # 先給一個範本，順便把接續用的開頭填好
        head = ""
        if project:
            head = ("接續 " + project + "，先讀 HANDOFF.md 和 ARCHITECTURE.md "
                    "確認現況。\n\n")
        with open(TASK_FILE, "w", encoding="utf-8") as f:
            f.write(head + "（把題目寫在這裡，存檔後再跑一次）\n")
        out("\n已建立題目範本：")
        out("  " + TASK_FILE)
        out("\n用記事本打開、把題目寫進去、存檔，然後重新選這個選項。")
        try:
            os.startfile(TASK_FILE)          # 直接開起來給使用者編輯
        except Exception:
            pass
        return

    out("\n題目內容：")
    out("-" * 50)
    with open(TASK_FILE, encoding="utf-8") as f:
        out(f.read().rstrip())
    out("-" * 50)
    ans = input("\n確定用這個題目派工？(y/N) ").strip().lower()
    if ans != "y":
        out("已取消。")
        return
    out("\n派工中（--max-turns 500）。這個視窗可以縮小，不要關。\n")
    subprocess.run([HERMES, "chat", "--query-file", TASK_FILE,
                    "--max-turns", "500"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="unknown",
                    help="limit=撞工具上限 / done=正常結束")
    ap.add_argument("--project", default="", help="偵測到的專案目錄")
    ap.add_argument("--auto", action="store_true",
                    help="不問直接執行預設動作（全自動模式用）")
    args = ap.parse_args()

    if args.auto:
        # 全自動：撞上限就接續，正常結束就做下一階段。
        # 防呆在 _autoguard.py，這裡只負責執行。
        out("[自動模式] reason=%s project=%s" % (args.reason, args.project))
        if args.reason == "limit":
            run_resume()
        else:
            run_next_stage(args.project, auto=True)
        return 0

    out("=" * 56)
    out("  27B 任務停了")
    out("=" * 56)
    if args.reason == "limit":
        out("  原因：撞到工具呼叫上限 —— 任務其實還沒做完")
    elif args.reason == "done":
        out("  原因：正常結束（不是撞上限）")
    else:
        out("  原因：不明")
    if args.project:
        out("  專案：" + args.project)
        hd = os.path.join(args.project, "HANDOFF.md")
        out("  交接文件：" + ("有" if os.path.exists(hd) else "沒有（接續前先叫它寫）"))
    out("")

    # 絕大多數時候你要的就是「讓專案繼續往下做」，所以那個當預設 ——
    # 直接按 Enter 就走，不用選。
    if args.reason == "limit":
        # 撞上限 = 同一階段沒做完，接續它自己的交接報告
        default = "resume"
        opts = [("1", "接續上一個任務（用它自己寫的交接報告）★預設", "resume"),
                ("2", "派一個新任務（編輯 task.txt）", "new"),
                ("0", "什麼都不做", "quit")]
    else:
        # 正常結束 = 這階段做完了，通常是往下一個階段推進
        default = "next"
        opts = [("1", "讓它接著做下一階段（自動用它列的下一步）★預設", "next"),
                ("2", "派一個新任務（編輯 task.txt）", "new"),
                ("3", "接續上一個任務", "resume"),
                ("0", "什麼都不做", "quit")]

    for key, label, _ in opts:
        out("  [%s] %s" % (key, label))
    out("")

    try:
        pick = input("  選擇（直接按 Enter = 預設）： ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0

    if not pick:
        action = default          # 空白 = 用預設
    else:
        action = dict((k, a) for k, _, a in opts).get(pick, "quit")
    if action == "resume":
        run_resume()
    elif action == "next":
        run_next_stage(args.project)
    elif action == "new":
        run_new_task(args.project)
    else:
        out("\n什麼都沒做。")

    out("")
    try:
        input("按 Enter 關閉…")
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

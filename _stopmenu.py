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
    """撈最近一份像樣的收尾報告。

    先看指定（或最新）的 session；那一輪如果是被中斷的（當機、關視窗、
    被 kill），只有一兩則訊息、沒有交代，就往前找 —— 再前面那輪的
    報告一樣接得上，總比「讀不到報告，停在這裡」好。
    """
    try:
        c = sqlite3.connect(DB)
        sids = []
        if sid:
            sids.append(sid)
        for (x,) in c.execute(
                "SELECT id FROM sessions "
                "WHERE source IN ('cli','desktop','tui') "
                "ORDER BY started_at DESC LIMIT 6").fetchall():
            if x not in sids:
                sids.append(x)
        for s_id in sids:
            rows = c.execute(
                "SELECT coalesce(content,'') FROM messages "
                "WHERE session_id=? AND role='assistant' "
                "ORDER BY rowid DESC LIMIT 12", (s_id,)).fetchall()
            for (t,) in rows:
                if t and len(t.strip()) > 80:
                    c.close()
                    return t.strip()
        c.close()
    except Exception:
        pass
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
        "⚠ 七件提醒：\n"
        "1. 做完一個可驗證的單元就停下來報告，不要一路衝到撞上限\n"
        "2. ctx 到 60% 就更新 HANDOFF.md\n"
        "3. 全程用繁體中文；沒做到的誠實說，不要改標準遷就結果\n"
        "4. 單次 write_file 不要超過 400 行。長檔案分段寫：\n"
        "   先寫骨架，再一段一段補上去（用 edit 或再一次 write）。\n"
        "   一次吐 19KB 會撞單次輸出上限被截斷，然後整個卡死不會自己醒。\n"
        "5. 每次改完程式碼，**貼出編譯器/測試的完整輸出**，並明講錯誤數\n"
        "   從幾個變成幾個。沒有減少就不要往下做別的 —— 先解決眼前那一個。\n"
        "   26/08/31 踩過：同一行編譯錯誤改了五輪，每次只換變數名、\n"
        "   語法從沒動過，因為改完沒去看編譯器實際講什麼。\n"
        "   編譯器的訊息通常已經把原因講完了。\n"
        "6. 寫 testbench 時**一開始就加波形輸出**：\n"
        "     initial begin\n"
        "         $dumpfile(\"<top>.vcd\");\n"
        "         $dumpvars(0, <tb_top>);\n"
        "     end\n"
        "   然後遇到這幾種問題時，畫成波形圖用視覺看，不要只靠 $display：\n"
        "     - 訊號是 x/z，要找它「什麼時候該有值卻沒有」\n"
        "     - 握手不成立（valid 拉了 ready 沒來，或反過來）\n"
        "     - 跨時脈域、取樣時機對不上\n"
        "   你有視覺能力（mmproj 已掛）。畫圖的做法和「怎麼問具體問題」\n"
        "   在 skill embedded/rtl-sim-verification 裡。\n"
        "   26/08/31 踩過：tb 沒加 $dumpvars，於是只能一輪加幾個 $display\n"
        "   再跑一次，花了兩小時才把資料路徑打通一半。$display 看不到\n"
        "   時脈邊緣前後的變化，也看不出多個訊號的時間關係。\n"
        "7. 改**設計決定**（不是修語法錯）之前，先讀專案裡的改動記錄\n"
        "   （CHANGELOG_*.md），改完加一行進去：改了什麼、為什麼、結果。\n"
        "   裡面的「已經確認行不通的做法」那節，是你自己試過並否定的，\n"
        "   不要再試一次。\n"
        "   26/09/01 踩過：01:11 改了 ctl_push 的推送時機，01:26 又整個\n"
        "   改回去 —— 檔案雜湊跟 00:55 完全一樣，半小時白做，而且改回去\n"
        "   的正是自己剛推翻的做法。\n"
        "   **你不會記得自己十分鐘前推翻過什麼，所以要寫下來。**\n"
        "8. 時序／相位對不上時（資料晚一拍、高低 byte 錯位、首筆是 x），\n"
        "   **不要用推理去猜哪一級多半拍 —— 印出來比對。**\n"
        "   做法：在 driver（tb）送出的地方和 sampler（RTL）收的地方各加\n"
        "   一行 $display，都印 $time 和當下的值，然後兩邊逐拍並排看。\n"
        "   一次只改一級、改完立刻重跑並貼出前 12 筆，看它往哪個方向動。\n"
        "   26/09/01 踩過：低 byte 修對之後高 byte 還晚一拍，接連猜了\n"
        "   「push 延後一拍」和「加 loaded 閘門」兩種改法 —— 一個更糟、\n"
        "   一個完全沒作用，因為都是猜的，沒先把兩邊的相位印出來對。\n"
        "9. **編譯過不等於跑得起來。** 每次改完都要真的執行一次，貼出結果。\n"
        "   26/09/02 踩過：用了 $past()（iverilog 不支援），iverilog 回報\n"
        "   0 error，但 vvp 直接 not runnable —— 模擬完全沒輸出，卻以為\n"
        "   自己編譯乾淨。系統函式、$system、SystemVerilog 語法都是這樣。\n"
        "10. **一個訊號不要同時管兩件事。** 一改就顧此失彼、在兩個版本之間\n"
        "   來回跳，通常就是這個原因 —— 不是你選錯做法，是兩件事被綁在一起。\n"
        "   26/09/02 踩過：同一個閘門既要「擋掉第一次無效寫入」又要「決定\n"
        "   計數器的值」，擋得掉就算錯數量、算對數量就擋不掉，來回改了三輪。\n"
        "   **發現自己在兩個版本之間反覆時，先問：是不是該拆成兩個訊號？**\n"
        "11. **症狀在下游，不代表 bug 在下游。** 往下游修之前，先確認上游\n"
        "   送出來的東西是對的 —— 印出上游的實際輸出，不要假設它是對的。\n"
        "   26/09/02 踩過：讀回來的值一直是 0/x，差點去修讀取路徑，\n"
        "   但實際上是寫入引擎從沒送出過資料（AXI 寫通道握手 0 次），\n"
        "   讀取端的輸入本來就是未初始化的值。**在輸入是 x 的路徑上除錯，\n"
        "   怎麼修都不會對。**\n")

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


def _running_chats():
    """正在跑的 hermes chat 對話 PID 清單（一次對話有兩個行程，只算一個）。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*hermes*chat*' } | "
          "Sort-Object CreationDate | "
          "Group-Object { $_.CreationDate.ToString('yyyyMMddHHmm') } | "
          "ForEach-Object { $_.Group[0].ProcessId }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=25)
        return [int(x) for x in r.stdout.decode("utf-8", "replace").split()
                if x.strip().isdigit()]
    except Exception:
        return []    # 查不到就放行，不要因為查詢失敗就卡住派工


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="unknown",
                    help="limit=撞上限 / done=正常結束 / unknown")
    ap.add_argument("--project", default="", help="專案根目錄")
    ap.add_argument("--gate-reason", default="",
                    help="驗收關卡不通過的理由 —— 會寫進派工提示詞")
    ap.add_argument("--auto", action="store_true",
                    help="不問人，直接照 reason 決定動作")
    ap.add_argument("--force", action="store_true",
                    help="已經有對話在跑也照樣派（會搶 slot，兩邊都變慢）")
    args = ap.parse_args()

    if args.auto:
        # 全自動：撞上限就接續，正常結束就做下一階段。
        # 防呆在 _autoguard.py，這裡只負責執行。
        # 但有一道擋在這裡：已經有對話在跑就不准再開。
        # 26/08/31 手動派工時沒檢查，開出第二個 session 搶走另一個 slot，
        # 兩邊都慢一半 —— 這是最該避免的事，寧可漏派也不要互搶。
        busy = _running_chats()
        if busy and not args.force:
            out("[自動模式] 已經有 %d 個對話在跑（PID %s）—— 不派工。"
                % (len(busy), ",".join(str(p) for p in busy)))
            out("  真的要並行請加 --force；先確認 slot 夠，不然兩邊都會慢。")
            return
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

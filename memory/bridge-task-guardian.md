---
name: bridge-task-guardian
description: 橋接器變成任務守門員 —— 偵測任務停止、印出上一輪做了什麼、八道防呆後自動接續
metadata: 
  node_type: memory
  type: project
  originSessionId: 61c2c266-cae7-4af3-b4fe-cdbf6c231042
  modified: 2026-08-30T02:47:07.367Z
---

2026-08-30 做的。橋接器原本只在有請求時印一行，任務停了就整片安靜，
使用者分不出「還在想」和「已經停了」。

**三個檔案**（都在 `Desktop/Qwen3.8-27B/`）：

| 檔案 | 做什麼 |
|---|---|
| `hermes_bridge.py` | 偵測停止、印上一輪摘要、決定要不要接續 |
| `_autoguard.py` | 八道防呆，回 GO / STOP，記 `autoguard.log` |
| `_stopmenu.py` | 手動模式跳選單；`--auto` 給全自動用 |
| `_autorelay.py` | 抓它自己的交接報告當新 prompt 重跑 |

**兩種啟動**：
- `_bridge.bat` — 跳選單，按 Enter 就是最合理的動作
- `BRIDGE-AUTORESUME.bat` — 全自動（設 `HERMES_AUTORESUME=1`）

**怎麼判斷停止原因**：`end_reason` 一律是 `agent_close` 分不出來，
要看**倒數第二則是不是那句上限提示**（`reached the maximum number of
tool-calling iterations`），最後一則就是它的交接報告。

**八道防呆**（任何一道沒過就停）：冷卻 60 秒、同 session 不重複、
鏈長 8 輪、連續 12 小時、**上一輪有沒有真的寫檔案（防空轉）**、
它說「全部完成」、它說「我卡住」、收尾報告太短。

**專案目錄是動態偵測的**：掃它寫過的檔案（`resolved_path`）往上找
有 `HANDOFF.md` 或 `ARCHITECTURE.md` 的那層，不是寫死，換專案會自己抓對。

**工具呼叫上限的真相**：`agent_init.py:523` 寫死 `max_iterations=90`。
`config.yaml` 的 `agent.max_turns: 500` **對 `-z` 單發模式無效**，
環境變數 `HERMES_MAX_ITERATIONS` 也沒用（在 elif 鏈太後面）。
正解是 **`hermes chat --query-file X --max-turns 500`**。
`--query-file` 還能避開 shell 轉義（中文、換行、引號都不會壞）。

相關：[[27b-agent-workflow]]、[[ps1-encoding-traps]]

---
name: skills-context-diet
description: 用 Claude Code Skills 把本地 LLM 的規則拆成按需載入，常駐 context 省 83%
metadata:
  type: project
---

2026-08-15 把 hermes/CLAUDE.md（原 9657 tokens、每次都全載）改成 Skills 架構。

**做法**：通用規則留 CLAUDE.md（1546 tokens），專門知識拆到 `~/.claude/skills/<名>/SKILL.md`：
- `game-assets`（~1000 tokens）— Kenney 素材庫路徑表、pygame/網頁載入範例、禁 sin 波音效
- `pptx-deck`（~900）— pptx_style API、「先寫 .py 再跑 python」兩步驟
- `web-design`（~465）— 配色字體、手機優先、自動補 favicon/validation

**機制**：啟動只載入每個 skill 的 name + description（~60 tokens），模型判斷需要才呼叫
Skill 工具拉全文。所以 **description 決定命中率**，要寫使用者會講的詞（「做遊戲、要音效、
要 sprite」），不是寫給人看的摘要。實測「做一個俄羅斯方塊網頁遊戲」→ 模型自己載入了
web-design + game-assets 兩個，命中正確。

**結果**：常駐 1735 tokens（vs 原 9657），省 83%。

**限制**：skill 載入後不會卸載，同一對話從遊戲轉 PPT 會累積（1735+1000+900≈3600）。
切主題建議開新對話。

**跟雲端 Claude 隔離（使用者明確要求兩邊不共用）**：本地 skill 不放 `~/.claude/skills`
（那是全域的、雲端也讀得到），改放 `Desktop/Qwen3.8-27B/local-plugin/skills/`，
用 `claude --plugin-dir <path>` 以 plugin 形式載入，bat 已帶這參數。
載入後名稱是 `qwen38-local:game-assets` 形式。試過 junction 連結但那反而讓兩邊共用同一份，
不符需求，已改掉。

**另一個發現**：本地跑時 55K 的 prompt 裡，CLAUDE.md 只佔 ~10K，**其餘 45K 是 36 個工具
定義**（CronCreate/DesignSync/Worktree/TaskCreate 等雲端專用工具對本地模型完全沒用）。
真要再瘦身，關掉用不到的 MCP server 跟工具比精簡 CLAUDE.md 有效得多。

相關：[[qwen38-mtp-config]] [[feedback-test-prompts-stay-vague]]

**實戰驗證（2026-08-15 俄羅斯方塊）**：模型自己載入 game-assets + web-design 兩個 skill，
音效照規則先 `ls` 確認再從 shared_assets 複製 8 個真 .ogg（沒用 sin 波），
配色命中 skill 預設的 #0f172a/#10b981/#3b82f6，自動補上 favicon/SEO/OG/aria-label/CC0 致謝，
按規則拆成 index.html + style.css + game.js 三檔。
**還自己寫了 smoke_test.js**（vm 沙箱 + Proxy 假造 canvas/DOM 做 headless 驗證），
跑完自刪 —— 這是 CLAUDE.md 那條「寫完必須跑一次驗證」在生效，規則確實有效。

**另一個雷**：模型會用 `rtk` 指令（那是全域 CLAUDE.md 給雲端用的 RTK 規則洩漏過來的），
但本機沒裝 rtk，每次用都失敗白費一輪。已在本地 CLAUDE.md 明文禁用。

**★ 教原則 > 補洞（2026-08-18 使用者的關鍵指正）**：我原本針對模型犯的錯一條條補規則
（教它 wrap 公式、教它裝 gcc），使用者指出「他不懂的地方應該要自己上網查，不是缺啥就教啥」。
改成兩條通用原則後實測有效：

1. **「知道自己不知道」** — 版本號／規格數字／暫存器位址／2024 後新產品 → 先查再答；
   查到跟記憶不符以查到的為準；沒法查證就明說不確定。
2. **「寫完先自我審查」** — 邊界在哪？隱性假設？並行安全？失敗路徑？能證明它對嗎？
   發現前後不一致就停下來想哪個對。

**實測**：問 STM32H7S3 的 XSPI 時脈（2024 後新晶片），它不再硬掰，第一反應是
`ddgs text -q "STM32H7S3 XSPI max clock frequency MHz"`。對照組「H743 SPI DMA 錯位原因」
（觀念題）則直接答且品質 A+ —— **它能區分「我懂的原理」跟「我可能記錯的數字」**。

只花 312 tokens（2313→2625），涵蓋所有領域，比一個個補洞划算。

**定位結論**：使用者決定放棄本地離線代工（韌體錯了會燒板子，風險不值），
改用「本機當離線顧問 + 雲端代工」。本機在觀念題實測 A/A+，那才是它的甜蜜點。

---
name: web-design
description: 做網頁、網站、UI、前端介面時用。提供預設配色、字體、手機優先版型、互動細節、以及該自動補上的東西（favicon／SEO／validation）。當任務是做網頁、網站、表單、儀表板、前端 app 時載入。
---

# 網頁設計規則

**不用問使用者細節，自己挑下面的合理組合。**

## 視覺
- **配色**：深底 `#0f172a` 或 `#18181b` + 一個亮 accent（`#10b981` 綠 / `#3b82f6` 藍 / `#ec4899` 粉）
  亮色版：淺灰底 + 一個飽和 accent
- **字體**：`system-ui` 或 `'Inter', sans-serif`；標題粗體大字、內文 16px+
- **手機優先**：viewport meta、觸控按鈕 ≥44×44px、單欄 max-width 480px
- **動畫**：hover transition 0.2s、scroll fade-in、按鈕點擊微縮放（2-3 處就好，別過度）

## 互動細節（都要有）
localStorage 存設定、loading state、空狀態提示、error 提示、操作後 toast。

## 內容
自己編 3-5 筆合理範例資料填進去。
❌ 不要留空畫面、不要寫 TODO、不要 example.com / lorem ipsum / {{placeholder}}。

## 無障礙
alt、aria-label、tab focus 樣式（基本就好）。

## 自動補上（使用者沒講但會問「怎麼沒這個」）
- 網站 → favicon、SEO meta、Open Graph、404 處理
- 表單 → validation、disabled 狀態、success 回饋
- 資料處理 → 空值、編碼、CSV BOM

## 拆檔（重要）
單次 Write ~25KB 上限，超過會截斷。
網頁 app 拆成 `index.html`（<200 行純結構）/ `styles.css` / `script.js`。
❌ 不要一個 .html inline 塞 800 行 CSS+JS（會崩）。

## 需要音效／圖片素材
載入 `game-assets` skill，本機有 651 個 Kenney .ogg 跟多個 sprite pack。
❌ 不要用 Web Audio API OscillatorNode 生 sin 波嗶嗶聲。

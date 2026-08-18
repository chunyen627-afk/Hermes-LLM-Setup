---
name: pptx-deck
description: 做 PowerPoint 簡報／週報／.pptx 檔時用。提供 pptx_style helper 的 API、版面規則、以及「必須先寫 .py 再跑 python」的兩步驟流程。當任務提到 PPT、簡報、投影片、週報、.pptx 時載入。
---

# PPT 製作規則

## ⚠️ 一定是兩個 tool call
1. **Write 一個 `.py` 檔**（內容是 Python code）
2. **Bash 跑 `python make_pptx.py`** ← 這時才產出 .pptx

❌ **絕對不要 `Write(file_path='x.pptx', ...)`**
→ .pptx 是 ZIP 二進位，手寫的 PowerPoint 打不開會跳「需修復」。
❌ 寫完 .py 忘了跑 → PPT 根本沒生出來。

**檢查清單**：
- [ ] 有 Write 一個 `.py`（不是 .pptx）？
- [ ] 有 Bash 跑 `python xxx.py`？
- [ ] 有 `ls *.pptx` 確認產生了？
- [ ] 有跑驗證確認頁數 + notes 數？

## Helper API
`pptx_style.py` 已裝在 site-packages，直接 import。內建 16:9、品牌色（深藍 #1A1A2E + 青綠 #00D4AA）、Microsoft JhengHei 字型、自動頁碼。

```python
from pptx_style import Deck
deck = Deck('週報.pptx')
deck.cover(title, subtitle, version='v3.9 (2026-06-11)', kpis=[(num,desc)]*4)
deck.notes('這頁講...')          # ← 每頁都要，不可省略
deck.section(title, subtitle)
deck.kpi_grid(title, subtitle, kpis=[(num,desc)]*4, footer_note='...')
deck.table_page(title, subtitle, headers=[...], rows=[[...]], star='★ ...')
deck.flow_4(title, subtitle, boxes=['① ','② ','③ ','④ '], star='...')
deck.before_after(title, subtitle, before_title, before_text, after_title, after_text, star='...')
deck.bullet_page(title, subtitle, bullets=[...], star='...')
deck.save()
```

## 硬性規則
1. **每頁都要 `deck.notes()`**，寫得像跟同事講話（「這頁講…」「老闆關心…」）
2. 不要每頁 bullet_page，穿插 kpi_grid / table_page / flow_4 / before_after
3. 第一頁必用 cover。週報 10-12 頁、簡報 15-20 頁
4. 內容用**具體數字**（「3881 條」「77.6%」「47 分鐘」），不要「很多」「很快」
5. 用 emoji 分類（🔌🍳🍪 商品 / 🔴🟠🟡 警示 / ✓❌ Before-After）
6. ★ Callout 寫業務語意（「老闆要的」「展場路人」），不是「具有現代化設計」這種行話
7. 禁用 PowerPoint 預設模板（Title and Content）
8. **驗證**：
```bash
python -c "from pptx import Presentation; p=Presentation('x.pptx'); print(len(p.slides), sum(1 for s in p.slides if s.notes_slide.notes_text_frame.text.strip()))"
```
notes 數要等於頁數。

## 範例
```python
from pptx_style import Deck
deck = Deck('週報.pptx')
deck.cover('本週週報 — 倉管功能升級',
           subtitle='連帶推薦 + 補貨預測 + 保存期限警示',
           version='v3.9 (2026-06-11)',
           kpis=[('60','SKU'),('13','情境'),('186','連帶對'),('零','需重訓')])
deck.notes('本週把倉管升級成會主動發現連帶 + 預測補貨的智能助理。')

deck.kpi_grid('訓練成果', subtitle='第 6 個 function、重訓一輪',
              kpis=[('3881','訓練條'),('47分','耗時'),('77.6%','Q8 raw'),('84.2%','E2E')],
              footer_note='查庫存 18/18 滿分、新功能 9/11')
deck.notes('Q8 raw 77.6%、加校正層 E2E 84.2%。')
deck.save()
```

## 其他 Office
.docx → python-docx；.xlsx → openpyxl；PDF → reportlab（weasyprint 不能用，缺 GTK）。
都已裝好，**禁止說「我需要裝 X 套件」**。

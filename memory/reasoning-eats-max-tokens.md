---
name: reasoning-eats-max-tokens
description: 本地 27B 開著 reasoning 時，thinking 會先吃掉 max_tokens，額度給太小會回空字串
metadata: 
  node_type: memory
  type: project
  originSessionId: 61c2c266-cae7-4af3-b4fe-cdbf6c231042
  modified: 2026-08-29T07:44:36.596Z
---

模型跑在 `--reasoning on --reasoning-budget 2048` 之下，**thinking 會先消耗
`max_tokens` 額度**。額度給太小的話 `content` 直接是空字串，而且 HTTP 回 200、
`usage.completion_tokens` 也顯示用滿了 —— 看起來像成功，實際什麼都沒拿到。

**2026-08-29 同一天踩到兩次**：

1. 視覺測試設 `max_tokens: 900` → 900 個 token 全被 thinking 吃掉，回覆空白
2. 家人 GUI 的搜尋判斷設 `max_tokens: 60` → `content` 空字串，
   導致「要不要搜尋」永遠判定成「不用查」，搜尋功能等於沒開

**規則**：任何送給本地模型的請求，`max_tokens` 至少要
`reasoning_budget + 預期輸出`。判斷類的短回答也要給 2600 以上。

**另一個相關陷阱**：答案不一定在第一行。模型可能先講一段思路，
真正的答案夾在後面。解析回應時要掃過每一行找目標格式，
不要 `out.splitlines()[0]`。

**怎麼分辨是這個問題**：`content` 是空字串或只有思路沒有結論，
但 HTTP 200 且沒有錯誤訊息。把 `max_tokens` 調大重試就會正常。

相關：[[qwen38-mtp-config]]、[[local-vision-enabled]]

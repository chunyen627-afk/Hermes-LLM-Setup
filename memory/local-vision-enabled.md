---
name: local-vision-enabled
description: "27B 本地視覺開啟方法 —— mmproj + tensor-split 6,13,13 + Hermes auxiliary.vision 路由"
metadata: 
  node_type: memory
  type: project
  originSessionId: 61c2c266-cae7-4af3-b4fe-cdbf6c231042
  modified: 2026-08-29T05:45:31.099Z
---

2026-08-29 把 Qwen3.8-27B 的視覺打開，**ctx 完全不用犧牲**。

**模型原生就是多模態** —— chat template 早就有 `<|vision_start|><|image_pad|>`，
只是啟動時沒掛 mmproj，所以 `/props` 回報 `vision: false`。
**不需要換非 GGUF**：mmproj 就是原生 ViT + projector 本體（不是「文字描述」），
而且 GGUF 的 mmproj 是 **BF16 未量化**，精度比 bnb-4bit 那種整包量化的還高。

**四個必要條件，缺一不可**：

1. `_ensure_38.ps1` 掛 `--mmproj`（0.87GB）+ `--image-min-tokens 1024`
2. **`--tensor-split` 從 `8,12,10` 改成 `6,13,13`** ← 關鍵
3. **Hermes `auxiliary.vision` 從 vertex 改成 lmstudio/qwen38_mtp**
4. **`hermes_bridge.py` 的 `STRIP_IMAGES = False`，而且改完要重啟橋接器** ← 最容易漏

第 4 點踩過：橋接器有個 `_strip_images()` 會把圖片**整個抽掉**換成 Gemini 的文字描述
（mmproj 之前的權宜之計）。前三項都做了但沒重啟橋接器，記憶體裡跑的還是舊碼，
結果 mmproj 等於白掛、圖根本到不了模型眼前，還照樣燒 Gemini 額度。

**怎麼分辨圖有沒有真的到模型眼前**（都是實測過的判別法）：
- 走 Gemini：回覆以 `Analysis:` 開頭，是包裝過的描述
- 拿一張你知道答案的圖去問**視覺才答得出來的問題**（背景是深色還是白色？
  某一欄有沒有畫出東西？）。走 Gemini 那次答「white canvas」但圖是深色底，
  還描述了不存在的波形；本地視覺答「深藍灰/炭黑色」且正確指出
  「只有 clk/start/done 有波形，其餘五欄空白」。
- `prompt_tokens` 要接近 1024 以上（圖片 token）

第 2 點：mmproj 是**最後才配置**的，原本比例會讓 device0（3070 8GB）剛好塞不下
→ `cudaMalloc failed: out of memory`，錯誤訊息裡的 931127936 bytes 就是 mmproj 大小。
改成 6,13,13 之後 **200K ctx + 視覺同時成立**（實測 28.3/32.7 GB）。
184K 失敗過、160K/143K/122K 都可以，但改分配後 200K 直接過。

第 3 點：只改 llama-server 沒用，Hermes 的 `vision_analyze` 工具**另有路由**，
不改的話照樣走 Gemini、照樣燒免費額度（20 次/天/模型）。桌面版要**重開**才吃到。

**`--image-min-tokens 1024` 的實測效果**（llama.cpp 啟動時會警告 Qwen-VL 低於 1024
在 grounding 任務會失準）：
- 868 token 時：把渲染雜點誤判成訊號跳變（Gemini 也犯同樣的錯）
- 1277 token 時：自己排除雜點「那只是游標標記，不是訊號跳變」，
  還會說「解析度看不清楚的部分我無法確認」

**27B 自己看圖比 Gemini 準**（同一張波形圖 4 題）：27B 對 3.5，Gemini 對 2。
27B 抓到 Gemini 漏掉的顯示 bug，還會主動對照標題宣稱值和實際波形找不一致。

**但視覺不能單獨當驗證標準** —— 兩個不同架構的模型在同一個雜點上犯同樣的錯。
圖要跟數字交叉比對，矛盾時以數字為準。

相關：[[qwen38-mtp-config]]、[[llama-cpp-tuning-rules]]、[[gemini-quota-limits]]

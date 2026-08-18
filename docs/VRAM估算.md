# VRAM 估算與 tensor-split 調校

換新模型或新顯卡時用這份重算。

---

## 總量公式

```
需要的 VRAM = 模型檔大小 + KV cache + MTP(如有) + 運算緩衝(約 1.5-2.5GB)
```

---

## KV cache 怎麼算

```
每 token 的 KV = num_key_value_heads × head_dim × 2(K和V) × 量化位元組 × full_attention層數
```

**關鍵**：hybrid 架構（如 Qwen3.8）只有部分層是 full attention，
KV 只按那些層算，比一般 dense 模型便宜很多。

### Qwen3.8-27B 實例

從 HF repo 的 `config.json` 讀：
```json
"num_key_value_heads": 4,
"head_dim": 256,
"layer_types": [... 64 層，其中 16 個 "full_attention" ...]
```

| 量化 | 每 token | 176K ctx |
|---|---|---|
| q4_0 | 18 KB | **3.02 GB** |
| q8_0 | 34 KB | 5.71 GB |
| f16 | 64 KB | 10.75 GB |

對照：一般 27B dense 模型 64 層全是 full attention，
同樣條件下 KV 會是 **4 倍**。

### 怎麼查新模型的參數

```bash
curl -s "https://huggingface.co/<repo>/raw/main/config.json" | python -c "
import json,sys
d=json.load(sys.stdin)
t=d.get('text_config', d)
lt=t.get('layer_types',[])
print('總層數:', t.get('num_hidden_layers'))
print('full_attention:', lt.count('full_attention') or t.get('num_hidden_layers'))
print('kv_heads:', t.get('num_key_value_heads'))
print('head_dim:', t.get('head_dim'))
"
```

然後：
```python
kv = kv_heads * head_dim * 2 * bytes_per_elem * full_layers * ctx / 1024**3
# q4_0 → bytes_per_elem = 0.5625
# q8_0 → 1.0625
# f16  → 2.0
```

---

## MTP 的成本

Qwen3.8 內建 MTP（multi-token prediction），開啟後 decode 快約 1.5-2 倍。

**VRAM 成本跟 ctx 成正比**（不是官方說的固定 1GB）：

| ctx | MTP 佔用 |
|---|---|
| 128K | 1226 MiB |
| 160K | 1482 MiB |
| 192K | 1738 MiB |
| 256K | 3476 MiB |

啟動時 log 會印：
```
srv load_model: [spec] estimated memory usage of MTP context is 1482.06 MiB
```

---

## tensor-split 怎麼調

`--tensor-split A,B,C` 是**比例**不是絕對值，llama.cpp 依此分配層數。

### 原則

1. **VRAM 大的卡分多一點**
2. **但要留運算緩衝空間** —— 最小的那張卡容易在這裡 OOM
3. 從各卡 VRAM 比例開始，再依實測微調

### 實例（3070 8G + 3060 12G × 2）

| 嘗試 | 結果 |
|---|---|
| `8,12,12`（照 VRAM 比例） | ❌ 3070 上 compute buffer OOM |
| `5,12,13` | ⚠️ 可跑但 CUDA2 太緊 |
| **`6,12,13`** | ✅ 採用 |

**教訓**：8GB 的卡不能照比例分，要少給——因為 compute buffer 是固定成本，
小卡扣掉那塊就沒剩多少。

### OOM 時看哪張卡爆

```
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 2373.19 MiB on device 0: cudaMalloc failed
                                                                        ^^^^^^^^
```
`device 0` 就是第一張卡 → 把 tensor-split 第一個數字調小。

---

## batch size

```
--batch-size 2048 --ubatch-size 512
```

**ubatch 越大，prompt 處理越快**，但吃 VRAM。

實測（32GB、176K ctx 已吃滿）：

| 設定 | 結果 |
|---|---|
| batch 4096 / ubatch 2048 | ❌ OOM |
| batch 4096 / ubatch 1024 | ❌ OOM |
| **batch 2048 / ubatch 512** | ✅ 可用 |

**VRAM 有餘裕才調得動。** ctx 開太大就沒空間給 batch 了，這是取捨。

---

## 完整範例：32GB 跑 Qwen3.8-27B

```
模型 (UD-Q4_K_XL)     17.92 GB
KV cache (176K, q4_0)  3.02 GB
MTP                    1.60 GB
運算緩衝               ~2.5 GB
─────────────────────────────
合計                  ~25.0 GB  (32GB 中)
```

實測各卡佔用：
```
GPU0 (3070 8G)   5.5 / 8.0 GB   剩 2.7GB
GPU1 (3060 12G)  9.2 / 12.0 GB  剩 3.0GB
GPU2 (3060 12G)  11.9 / 12.0 GB 剩 0.4GB  ← 最緊
```

---

## 推到 1M ctx 需要多少

| 設定 | 需求 |
|---|---|
| q4_0 + MTP | ~48 GB |
| q4_0 不開 MTP | ~38 GB |
| q8_0 + MTP | ~64 GB |

⚠ Qwen3.8 原生訓練 262K，超過要開 YaRN 外推，品質會下降。

---

## 升級建議（實測結論）

| 方案 | VRAM | 頻寬 | 評價 |
|---|---|---|---|
| 3070 → 3060 12G | 36GB | ↓ 24% | ❌ 變慢 |
| 3070 → 第二張 3070 | 28GB | — | ❌ VRAM 反而少 |
| **3070 → 2080Ti 22G** | **46GB** | ↑ 37% | ✅ 兩邊都贏 |
| 3070 → 3080 20G | 44GB | ↑ 70% | ✅ 更好但貴 |
| 多台機器串網路 | 理論變大 | — | ❌ 網路延遲比 PCIe 慢 100 倍 |

**關鍵**：多卡要在同一台（走 PCIe 32000 MB/s），跨機器（走網路 125 MB/s）沒意義。

⚠ 2080Ti 22G / 3080 20G 都是**魔改卡**（原廠沒這規格），
買要注意保固，收到立刻壓測。

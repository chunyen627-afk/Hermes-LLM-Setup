---
name: matmul-axi-project
description: 27B 做的 BF16 matmul 加速器（AXI Full + bit-exact vs C），進度與驗證方式
metadata: 
  node_type: memory
  type: project
  originSessionId: 61c2c266-cae7-4af3-b4fe-cdbf6c231042
  modified: 2026-08-30T02:47:33.638Z
---

**專案在 `C:\Users\pjunm\matmul_axi\`**，倉庫存證在
`Hermes-LLM-Setup/projects/matmul_axi/`（只放 RTL + 兩份 md，完整專案太大）。

題目是使用者給的三行，**全部由 27B 完成，沒有人寫任何一行 Verilog**：
> 做一個矩陣乘法硬體加速器 IP，AXI Full 介面，配合 llama2.c 跑
> tinystories 15M、bf16 精度。跑模擬，證明跟 C 版本一致。

後補的系統約束：最終由 STM32 透過 **8-bit xSPI** 驅動，
AXI 要能跟 **VCU118 的 MIG DDR4** 介面互通。

## 已完成（我親自重編重跑驗證過，不是採信它的報告）

```
f32_mul.v / f32_add.v   bit-exact vs 編譯出來的 C oracle
matmul_core.v           40/40 bit-exact vs c_matmul_oracle.c
axi4_slave_reg.v        32/64/128/256 四種位寬全部 0 FAIL
```

**怎麼複驗**（我踩過坑：要在 `out/mmtest/` 跑，不然缺 `expected.hex`）：
```bash
cd /c/Users/pjunm/matmul_axi/out/mmtest
export PATH="/c/iverilog/bin:$PATH"
cp ../../ref/c_matmul_oracle.c .          # 它的腳本假設這支在同層
python -c "import sys,runpy; sys.path.insert(0,r'C:\Users\pjunm\matmul_axi\ref'); runpy.run_path('mmcheck.py',run_name='__main__')"
```
AXI 直接用 `-P` 覆寫參數跑四種寬度即可。

## 它自己做的關鍵決定

- **精度**：一開始追 Python `Fraction`（精確有理數，**比 C 還嚴格**）
  燒掉六小時。被引導式提問點醒後自己推出
  **「BF16×BF16 在 FP32 中是精確的」**（8+8=16 bits < 24-bit significand）
  —— 所以乘法可以位元精確，只有累加有誤差。驗證對象改成編譯的 C。
- **架構**：只給「8-bit xSPI」和「要接 MIG」兩個約束，它自己算出
  xSPI ~12.5MB/s、30MB 權重要 2.4 秒/token 不可接受 →
  權重放 FPGA 端 DDR4、xSPI 只傳 activation → AXI 選 256-bit 對齊 MIG。
  MIG 規格**誠實標注為假設**（本機無 Vivado）。

## 下一階段（進行中）

它自己列的：接 register map → 加 AXI master 讀 DDR4 → top-level + CDC。

## 環境限制

```
iverilog ✅（/c/iverilog/bin，不在 PATH）
vivado / verilator / yosys ❌ → 只能模擬，驗不了時序和資源
```
所以「能不能收在 75MHz」是**未驗證的假設**，不要當成已知事實。

相關：[[27b-agent-workflow]]、[[bridge-task-guardian]]

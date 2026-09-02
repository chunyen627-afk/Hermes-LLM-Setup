# 現況快照（規劃者維護，2026-09-02 18:35）

> 換 session 接續時**先讀這份**。

## 一句話

**RTL 驗證階段完成 —— 11 個 run 全 PASS，零 FAIL。**
下一階段是系統整合（Vivado + Xilinx IP + xsim）。

## 已完成的基準（改任何 .v 之前先跑一次比對）

```bash
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --all
```
```
f32_units / matmul_core / axi4_slave_reg[32,64,128,256] /
axi4_master / matmul_top / cdc / xspi_slave / matmul_top_cdc
→ 11 run 全 PASS，"ok": true × 12
xspi_slave: data_integrity 26 checked 0 bad，12 cover 全中
```

⛔ **RTL 凍結。** 退化就回退，不要「順手改」。

## xspi_slave 這關的解法（六個修正互相依賴）

寫入端四個：
1. `hw_pipe_lo <= xspi_io[7:0]`（不能讀同邊 NBA 的 `w_lo`）
2. 寫入 `dummy_cnt <= dummy_n - 2`
3. negedge 直接 commit `{w_hi, xspi_io}`
4. `wr_data_started` 閘門（丟掉每 frame 第一次 stale commit）

讀取端兩個：
5. 讀取 `dummy_cnt <= dummy_n - 3`（用 `is_read` 與寫入分開）
6. 首筆 stale 輸出閘門

tb 一個：第二次讀（`0x9001_0004`）的期望值補上 +2 偏移

## 下一步（順序不要跳）

```
✅ xspi_slave 過 gate
→ ⏳ 系統整合（Vivado block design + Xilinx IP + xsim）  ← 現在這裡
→ 合成 + 時序（skill embedded/xilinx-vcu118 第六節）
→ implement + bitstream
→ 上板量加速比 ← 「完工」的定義
```

**目標是「先通 15M 的 1 MAC 版本」**，不做平行化、不換 42M、不優化。
判斷任何提議時問：**這讓「能上板跑」更快出現，還是更慢？**

### 系統整合的既定決定（08-31 訂）
- interconnect、width converter、MIG 全用 **Xilinx 內建 IP**，不自己寫
- 那些是加密 SystemVerilog、只能用 xsim，**絕不進模組層的 iverilog gate**
- 時脈：`aclk` 100 MHz、`xspi_clk` 50 MHz，約束在 `constraints/timing.xdc`

### ⚠ 已知風險
`vivado-vcu118-setup` 記憶：**XCVU9P 要 Enterprise 授權**，
Standard 版只有 4 顆 Alveo。系統整合前要先確認授權，否則卡在合成。

## 規劃者工作守則
見 `HANDOFF_TO_NEXT.md` 第六之二節。只做四件事：簡單查證 / 防重複 /
寫行為規則 / 寫 CHANGELOG。⛔ 不自己下場除錯。

派工：`_planner_active` 旗標讓橋接器彈窗閉嘴；用
`python _autorelay.py --task-file _mytask.txt` 派自己寫的題目
（`_stopmenu.py --auto` 會覆寫 `next_task.txt`）。

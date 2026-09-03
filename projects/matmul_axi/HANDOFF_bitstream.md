# 交接：matmul_axi 產 bitstream（卡在 MIG 系統時脈接線）

> 2026-09-03 深夜。接手先讀這份，再讀 `CHANGELOG_sysint.md` 全部。

## 一句話

**RTL / 合成 / 時序 / 資源全部完成且驗證過，只差最後一步 bd 的 MIG 系統時脈接線。**
剩下的純粹是 Vivado block design 的工程問題，不是設計問題。

## ✅ 已完成（別重做）

| 項目 | 結果 |
|---|---|
| RTL 驗證 | 11 run 全 PASS（含兩個 DRC 修正後重驗）|
| 合成 | 通過、**300 MHz 時序 +2.3ns 餘裕**、資源 <3%（DSP 3/6840）|
| IP 快取問題 | 根除 —— xspi_slave/matmul_top 改用 module_ref |
| DDR4 資料腳位 | `apply_board_connection` 綁定成功 |
| DRC 多重驅動 | 修好（`wr_w_left`、`io_out`，見 CHANGELOG 21:15）|

**300MHz 收斂 = 46 TPS（1 MAC）可達，這是專案的核心價值，已證明。**

## ❌ 卡點：MIG 的 `c0_sys_clk_p/n` 懸空

換 module_ref + 我在既有 bd 上動 IP cell 的過程中，破壞了 MIG 的系統時脈結構。
現在的矛盾狀態：

- `mig_ddr4/C0_SYS_CLK`（intf）← 接了 external `default_250mhz_clk1` ✅
- `mig_ddr4/c0_sys_clk_p/n`（scalar 差動腳）← **懸空** ❌

合成 blackbox 檢查看 scalar 腳 → 報 `unconnected pin c0_sys_clk_p`；
implement 報 `Mig 66-99: c0_sys_clk_p/n not placed`。

MIG 同時有 intf 版和 scalar 版時脈輸入，只有一套會真正驅動 PHY，
兩者沒正確橋接。

## 🎯 建議做法：整個 bd 重建（不要繼續補洞）

在壞掉的 bd 上補了 4 次都沒對。根源是「在既有 bd 上動 IP cell 破壞了
board automation 的完整性」。**乾淨解法是從零重建**，讓 board automation
一次做完整。

### ⚠ 第一件事：查對 `apply_bd_automation` 的 config 語法（第 12 條規則）

我今晚一直卡在這個指令的參數格式，反覆試錯（`Board_Interface "ddr4_sdram_c1"`
時而 OK 時而被 undo）。**不要再猜 —— 先查**：
```tcl
help apply_bd_automation
# 或看 automation 規則接受的 config：
get_bd_automation_rules -of_objects [get_bd_cells mig_ddr4]
```
DDR4 的 board interface 名是 `ddr4_sdram_c1`，系統時脈是 `default_250mhz_clk1`
（不是 `default_sysclk1_300`，那個會被 MIG 拒絕，valid 值：
`Custom / default_250mhz_clk1 / default_250mhz_clk2`）。

### 重建步驟（骨架在 `vivado/08_rebuild_modref.tcl`，未跑過）

1. 砍舊 bd，`create_bd_design top_bd`
2. 5 個 IP cell：clk_wiz **不要建**（今晚證明它 0 sinks 是多餘的，見下）
   —— 只要 mig_ddr4 / axi_smc / rst_aclk / rst_xspi
3. **MIG 用 board automation 一次建好**（含 sysclk external + 腳位）：
   `apply_bd_automation -rule xilinx.com:bd_rule:ddr4 -config {...} [get_bd_cells mig_ddr4]`
   —— 先用 help 確認 config 格式再跑
4. xspi_slave / matmul_top 用 module reference：
   `create_bd_cell -type module -reference xspi_slave xspi_slave`
   （前置：`set_property source_mgmt_mode All`，否則找不到 RTL）
5. 時脈：aclk 域全接 `mig_ddr4/c0_ddr4_ui_clk`（300MHz），
   xspi_clk 接外部 port。**不需要 clk_wiz。**
6. AXI 用 `connect_bd_intf_net`：
   xspi_slave/m_ddr → axi_smc/S00_AXI；matmul_top/m_axi → axi_smc/S01_AXI；
   axi_smc/M00_AXI → mig_ddr4/C0_DDR4_S_AXI；xspi_slave/m_reg → matmul_top/s_axi
7. 位址：`assign_bd_address`，然後手動設 m_reg = 0x9001_0000
8. 11 個 xSPI external port（名字對 `constraints/pins_vcu118.xdc`，`xspi_io` 用 -dir IO）
9. wrapper + set top + 合成（06a）+ implement（07）

## 今晚踩過的坑（都在 CHANGELOG_sysint.md 有細節）

1. **`wait_on_run` 會讓 Vivado 崩潰**（EXCEPTION_ACCESS_VIOLATION）→
   用 `launch_runs` 後退出，看檔案判斷完成，不看行程
2. **`Spawn failed: Broken pipe`** = compile.bat 找不到 xvlog →
   一定要透過 `_runsim.bat`（帶 settings64.bat）跑，Vivado 不繼承 shell PATH
3. **判斷合成/impl 完成看檔案（.dcp/.bit）不看行程** —— 行程會被父行程帶走但 run 有跑完
4. **RTL 有八份副本** —— 但改用 module_ref 後只讀 rtl/，這個坑消失了
5. **clk_wiz 是多餘的** —— aclk 用 MIG ui_clk、xspi_clk 用外部 port
6. xsim 用 RTL 模型要跑 25 小時（DDR4 校準）→ 直接合成，模擬擱置

## 執行環境

```bash
# 所有 Vivado 指令都要透過這個（帶 settings64）：
cmd //c "C:\\Users\\pjunm\\matmul_axi\\_runsim.bat" "vivado/<script>.tcl"
```

## STM32 側（FPGA 完工後）

見 `SPEC_stm32_integration.md`：協定完全相容，只需改 base address + 加速器 API +
只加速 BF16（`matmul_bf16`）。上游 repo `chunyen627-afk/stm32h7-llama2` 唯讀參考，
clone 在 `C:\Users\pjunm\stm32_fpga\_upstream_readonly`。

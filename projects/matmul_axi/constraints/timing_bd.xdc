# timing_bd.xdc —— block design 版的時序約束（top = top_bd_wrapper）
#
# 2026-09-03 規劃者建立。取代 timing.xdc —— 那份是給「RTL 直接當 top」寫的，
# 對 wrapper 不適用：
#   - `get_ports aclk` 找不到 —— wrapper 沒有 aclk 這個 port，
#     AXI 側的時脈是 MIG 產生的 c0_ddr4_ui_clk（內部訊號）
#   - 那份寫 aclk 100 MHz，但 27B 把整個 AXI 域改接 MIG 的 ui_clk（300 MHz）
#     之後就不對了
#
# ⚠ block design 專案的時脈**由 IP 自己約束** —— MIG 和 clk_wiz 各自產生
# 自己的 XDC（`create_clock` / `create_generated_clock`）。這份只補外部
# 進來的、IP 管不到的部分。

# ---- 板子的系統時脈（進 clk_wiz + MIG）----
# VCU118 的 300 MHz 差動 sysclk。MIG 的 board preset 通常也會約束
# c0_sys_clk，重複約束會被 Vivado 抱怨，所以先檢查再決定要不要留。
# 若合成時報 "clock already exists"，把下面兩行註解掉。
create_clock -name sysclk -period 3.333 [get_ports sysclk_p]

# ---- xSPI 主機介面：50 MHz（STM32 OCTOSPI 送進來的 SCK）----
# 訊框是 DDR（雙緣取樣），資料率 100 Mbps/line。
create_clock -name xspi_clk -period 20.000 [get_ports xspi_clk]

# ---- 跨時脈域 ----
# xSPI 域和 AXI/DDR4 域完全非同步，真正的安全由 async_fifo 的
# gray pointer 保證，不是靠時序分析。沒有這行 Vivado 會報一堆假違例。
#
# ⚠ AXI 側的時脈名字要等合成後才知道（MIG 產生的，通常是
# c0_ddr4_ui_clk 或 clk_pll_i）。先只宣告 xspi_clk 與其他所有時脈非同步，
# 用萬用寫法避免寫死名字：
set_clock_groups -asynchronous \
  -group [get_clocks xspi_clk] \
  -group [get_clocks -quiet -filter {NAME != "xspi_clk"}]

# ---- 輸入/輸出延遲 ----
# xSPI 是 source-synchronous：SCK 跟資料一起進來。
# 上板實測 50 MHz 可用（見上游 repo 的 xspi-slave-burst-constraint 記憶），
# 這裡給保守的 ±2 ns 視窗。
set_input_delay  -clock xspi_clk -max 2.000 [get_ports {xspi_io[*] xspi_cs_n xspi_dqs}]
set_input_delay  -clock xspi_clk -min -2.000 [get_ports {xspi_io[*] xspi_cs_n xspi_dqs}]
set_output_delay -clock xspi_clk -max 2.000 [get_ports {xspi_io[*]}]
set_output_delay -clock xspi_clk -min -2.000 [get_ports {xspi_io[*]}]

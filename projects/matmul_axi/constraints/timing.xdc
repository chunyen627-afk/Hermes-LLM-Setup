# timing.xdc —— matmul_axi 時脈約束
# 2026-08-31 使用者定案：xSPI 50 MHz、FPGA 計算側 100 MHz
#
# 這份只有時脈約束，沒有接腳（pin）約束。
# 要產 bitstream 還需要 VCU118 master XDC 的接腳對應 —— 還沒做。

# ---- 計算 / AXI 側：100 MHz ----
create_clock -name aclk -period 10.000 [get_ports aclk]

# ---- 主機介面側：50 MHz（STM32 OCTOSPI 送進來的）----
# 註：這是 SCK 的頻率。訊框是 DDR（雙緣取樣），
# 所以資料率是 100 Mbps/line，8 條線 = 100 MB/s 理論值，
# 扣掉協定/CS 開銷實際約 25-35 MB/s（見 ARCHITECTURE.md 第 120 行的表）。
create_clock -name xspi_clk -period 20.000 [get_ports xspi_clk]

# ---- 兩個時脈互不相關 ----
# 沒有這行，Vivado 會去分析跨域路徑並報出一堆假違例。
# 真正的跨域安全由 async_fifo 的 gray pointer 保證，不是靠時序分析。
set_clock_groups -asynchronous \
  -group [get_clocks aclk] \
  -group [get_clocks xspi_clk]

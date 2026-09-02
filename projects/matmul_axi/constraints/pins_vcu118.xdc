# ============================================================
# VCU118 腳位約束 —— xSPI 實體介面
# 使用者提供（2026-09-02），已對應到 rtl/xspi_slave.v 的實際埠名。
#
# ⚠ 埠名對照（使用者原稿 → RTL 實際埠名）：
#     xspi_sclk    -> xspi_clk
#     xspi_dq[7:0] -> xspi_io[7:0]     （inout，八線 octal）
#     xspi_ce_i    -> xspi_cs_n        （低態有效）
#     xspi_dm_dqs  -> xspi_dqs
#
# ⚠ 這些 port 名字要跟「合成時的 top」一致。系統整合完成後 top 會變成
#   block design 的 wrapper，若 wrapper 把這些訊號改名，這份要跟著改。
# ============================================================

set_property -dict {PACKAGE_PIN AK29 IOSTANDARD LVCMOS18} [get_ports xspi_clk]

set_property -dict {PACKAGE_PIN AT35 IOSTANDARD LVCMOS18} [get_ports {xspi_io[0]}]
set_property -dict {PACKAGE_PIN AP35 IOSTANDARD LVCMOS18} [get_ports {xspi_io[1]}]
set_property -dict {PACKAGE_PIN AG31 IOSTANDARD LVCMOS18} [get_ports {xspi_io[2]}]
set_property -dict {PACKAGE_PIN V33  IOSTANDARD LVCMOS18} [get_ports {xspi_io[3]}]
set_property -dict {PACKAGE_PIN AP38 IOSTANDARD LVCMOS18} [get_ports {xspi_io[4]}]
set_property -dict {PACKAGE_PIN AJ33 IOSTANDARD LVCMOS18} [get_ports {xspi_io[5]}]
set_property -dict {PACKAGE_PIN AJ35 IOSTANDARD LVCMOS18} [get_ports {xspi_io[6]}]
set_property -dict {PACKAGE_PIN Y32  IOSTANDARD LVCMOS18} [get_ports {xspi_io[7]}]

set_property -dict {PACKAGE_PIN AT39 IOSTANDARD LVCMOS18} [get_ports xspi_cs_n]
set_property -dict {PACKAGE_PIN V32  IOSTANDARD LVCMOS18} [get_ports xspi_dqs]

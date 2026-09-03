puts "=== 13 remove clk_wiz [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]

# clk_wiz 完全沒用（clk_out 都 0 sinks；aclk 用 MIG ui_clk、xspi_clk 用外部 port）。
# 它還接著 sysclk_p/n 造成 sysclk 多驅動（clk_wiz 和 MIG 都吃同一個外部 sysclk）。
# 刪掉它，順便刪掉多餘的外部 sysclk scalar port（MIG 現在用 board 的 default_250mhz_clk1）。

delete_bd_objs [get_bd_cells clk_wiz_0]
puts "deleted clk_wiz_0"

# 刪掉沒用的外部 sysclk scalar port（若還在）
foreach p {sysclk_p sysclk_n} {
  set port [get_bd_ports -quiet $p]
  if {[llength $port]>0} { delete_bd_objs $port; puts "deleted external $p" }
}

# clk_wiz 的 locked 曾接 rst 的 dcm_locked -> 改接常數 1 或 MIG 的 calib_complete
# 先看 rst 的 dcm_locked 現在懸空了沒
foreach r {rst_aclk rst_xspi} {
  set p [get_bd_pins -quiet $r/dcm_locked]
  set n [get_bd_nets -quiet -of $p]
  if {[llength $n]==0} {
    # 懸空 -> 接 MIG 的 calib complete（aclk）或常數
    puts "  $r/dcm_locked floating"
  }
}
# aclk 域的 dcm_locked 接 MIG calib_complete
catch {connect_bd_net [get_bd_pins mig_ddr4/c0_init_calib_complete] [get_bd_pins rst_aclk/dcm_locked]}
# xspi 域沒有 locked 訊號 -> 接常數 1
if {[llength [get_bd_cells -quiet vcc_1]]==0} {
  create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 vcc_1
  set_property CONFIG.CONST_VAL 1 [get_bd_cells vcc_1]
}
catch {connect_bd_net [get_bd_pins vcc_1/dout] [get_bd_pins rst_xspi/dcm_locked]}

if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 250]" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 13 done ==="

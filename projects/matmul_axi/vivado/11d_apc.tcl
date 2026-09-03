open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]
# 刪掉沒關聯的 external port
foreach p [get_bd_intf_ports -quiet *C0_DDR4*] { delete_bd_objs $p }
# apply_board_connection：把 board 的 ddr4 介面接到 MIG 的 C0_DDR4 pin，自動建 external + 腳位
if {[catch {
  apply_board_connection -board_interface "ddr4_sdram_c1" -ip_intf "mig_ddr4/C0_DDR4" -diagram top_bd
} e]} { puts "APC_DDR4_ERR [string range $e 0 200]" } else { puts "APC_DDR4_OK" }
# sysclk
if {[catch {
  apply_board_connection -board_interface "default_sysclk1_300" -ip_intf "mig_ddr4/C0_SYS_CLK" -diagram top_bd
} e]} { puts "APC_CLK_ERR [string range $e 0 150]" } else { puts "APC_CLK_OK" }
puts "DDR4_PORT_BOARD [get_property -quiet CONFIG.BOARD_INTERFACE [get_bd_intf_ports -quiet *C0_DDR4*]]"
if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 150]" } else { puts "VALIDATE_OK" }
save_bd_design

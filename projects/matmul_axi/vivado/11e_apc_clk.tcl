open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]
if {[catch {
  apply_board_connection -board_interface "default_250mhz_clk1" -ip_intf "mig_ddr4/C0_SYS_CLK" -diagram top_bd
} e]} { puts "APC_CLK_ERR [string range $e 0 200]" } else { puts "APC_CLK_OK" }
puts "DDR4_BOARD [get_property -quiet CONFIG.BOARD_INTERFACE [get_bd_intf_ports -quiet *C0_DDR4*]]"
puts "INTF_PORTS [get_bd_intf_ports -quiet]"
if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 200]" } else { puts "VALIDATE_OK" }
save_bd_design

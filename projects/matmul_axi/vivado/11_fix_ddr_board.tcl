puts "=== 11 fix ddr board start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]

# 現在的 DDR4 external port 沒有 board 關聯 -> 腳位沒 site。
# 先刪掉現有的 external DDR4 / sysclk port，再用 board automation 重建。
foreach p {C0_DDR4_0 sysclk_p sysclk_n} {
  set port [get_bd_intf_ports -quiet $p]
  if {[llength $port]==0} { set port [get_bd_ports -quiet $p] }
  if {[llength $port]>0} { delete_bd_objs $port; puts "deleted external $p" }
}

# 用 board automation 套 DDR4（會自動建 external port + 腳位約束）
apply_bd_automation -rule xilinx.com:bd_rule:ddr4 \
  -config { Board_Interface {ddr4_sdram_c1 ( DDR4 SDRAM C1 ) } }  [get_bd_cells mig_ddr4]
puts "ddr4 automation applied"

# sysclk（300MHz）也用 board automation 接 MIG 的 c0_sys_clk
apply_bd_automation -rule xilinx.com:bd_rule:xilinx_device_clock \
  -config { CLK_DOMAIN {} } [get_bd_intf_pins -quiet mig_ddr4/C0_SYS_CLK] -quiet

puts "AFTER intf_ports=[llength [get_bd_intf_ports]] ports=[llength [get_bd_ports]]"
foreach p [get_bd_intf_ports -quiet] { puts "  IPORT $p board=[get_property -quiet CONFIG.BOARD_INTERFACE $p]" }

if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 200]" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 11 done [clock format [clock seconds] -format %H:%M:%S] ==="

puts "=== 11b set board interface [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]

# DDR4 external port -> 關聯 board 的 ddr4_sdram_c1
set ddr [get_bd_intf_ports -quiet *C0_DDR4*]
puts "DDR4 port: $ddr"
set_property CONFIG.BOARD_INTERFACE ddr4_sdram_c1 $ddr
puts "DDR4 board_if now: [get_property CONFIG.BOARD_INTERFACE $ddr]"

# sysclk external port -> default_sysclk1_300
foreach cand {sysclk_p sys_clk_p C0_SYS_CLK_0 default_sysclk1_300} {
  set sp [get_bd_intf_ports -quiet *$cand*]
  if {[llength $sp]>0} { puts "SYSCLK port: $sp"; set_property CONFIG.BOARD_INTERFACE default_sysclk1_300 $sp; break }
}
# 若 sysclk 是 scalar port 而非 intf
foreach cand {sysclk_p sysclk_n} {
  set sp [get_bd_ports -quiet $cand]
  if {[llength $sp]>0} { puts "SYSCLK scalar port exists: $cand" }
}

if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 250]" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 11b done ==="

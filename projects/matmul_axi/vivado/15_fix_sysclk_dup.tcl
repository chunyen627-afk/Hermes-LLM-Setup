puts "=== 15 fix duplicate sysclk [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]

# MIG 有兩組系統時脈輸入：C0_SYS_CLK(intf,已接 default_250mhz_clk1) +
# c0_sys_clk_p/n(scalar,懸空)。刪掉懸空的 scalar net。
foreach net {sysclk_p_1 sysclk_n_1} {
  set n [get_bd_nets -quiet $net]
  if {[llength $n]>0} { delete_bd_objs $n; puts "deleted dangling net $net" }
}
# 確認 c0_sys_clk_p/n 現在沒接（懸空 pin 在 module_ref 下無害，但要確認 MIG
# 的時脈確實走 intf 那條）
foreach p {c0_sys_clk_p c0_sys_clk_n} {
  set nn [get_bd_nets -quiet -of [get_bd_pins -quiet mig_ddr4/$p]]
  puts "  mig/$p net now: [llength $nn]"
}
puts "C0_SYS_CLK still connected: [expr {[llength [get_bd_intf_nets -quiet -of [get_bd_intf_pins -quiet mig_ddr4/C0_SYS_CLK]]]>0}]"

if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 250]" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 15 done ==="

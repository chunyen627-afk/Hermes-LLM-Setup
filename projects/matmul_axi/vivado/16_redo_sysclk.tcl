puts "=== 16 redo sysclk board conn [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]

# 現在 default_250mhz_clk1 intf port 存在但沒橋到 scalar 腳。
# 先清掉現有的 250mhz intf port 與相關 net，重來。
foreach p [get_bd_intf_ports -quiet *250mhz*] { delete_bd_objs $p }
foreach n [get_bd_intf_nets -quiet *250mhz*] { catch {delete_bd_objs $n} }
puts "cleared old 250mhz port"

# 用 apply_board_connection 對 c0_sys_clk（scalar 差動）重建 —
# MIG 的實體時脈輸入是 sys_clk（scalar），不是 C0_SYS_CLK intf。
# 先看 MIG 有哪些可接 board 的 pin
puts "=== MIG board-connectable ==="
foreach ci [get_bd_intf_pins -quiet mig_ddr4/*] {
  puts "  INTF $ci"
}

# 對 C0_SYS_CLK intf 做 board connection，這次確認建 external
apply_board_connection -board_interface "default_250mhz_clk1" -ip_intf "mig_ddr4/C0_SYS_CLK" -diagram "top_bd"
set ep [get_bd_intf_ports -quiet *250mhz*]
puts "EXTPORT_AFTER $ep"

# 確認 scalar c0_sys_clk_p/n 也連上了（board connection 應該會橋接）
foreach p {c0_sys_clk_p c0_sys_clk_n} {
  puts "  mig/$p connected=[expr {[llength [get_bd_nets -quiet -of [get_bd_pins -quiet mig_ddr4/$p]]]>0}]"
}
if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 200]" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 16 done ==="

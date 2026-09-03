# 06e_clear_cache_synth.tcl —— 清 IP cache 後重新合成
#
# 根因（2026-09-03，第四層）：IP 的 OOC 合成命中了 IPCACHE
#   INFO: [IP_Flow ...] IPCACHE: Running cache check for IP inst: top_bd_xspi_slave_0
# 於是直接用快取的 netlist，連 reset_run 都不會讓它重編。
# ip_repo/ 的 .v 已更新（io_out_hi ×5）但 cache 不認。
#
# config_ip_cache -clear_output_repo 清掉 cache，逼 IP 重新 OOC 合成。

puts "=== 06e start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr

# 清掉 IP output cache
if {[catch {config_ip_cache -clear_output_repo} e]} { puts "cache clear1: $e" }
if {[catch {config_ip_cache -clear_local_cache} e]} { puts "cache clear2: $e" }
puts "IP cache cleared"

# reset 三個 run
foreach run {synth_1 top_bd_xspi_slave_0_synth_1 top_bd_matmul_top_0_synth_1} {
    if {[llength [get_runs -quiet $run]] > 0} { reset_run $run; puts "RESET $run" }
}

# 約束
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} { set_property is_enabled false $old }
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
    if {[llength [get_files -quiet */[file tail $f]]] == 0} {
        add_files -fileset constrs_1 $f
    }
}

launch_runs synth_1 -jobs 8
puts "SYNTH launched (cache cleared, IP will recompile from ip_repo)"
puts "=== 06e done [clock format [clock seconds] -format %H:%M:%S] ==="

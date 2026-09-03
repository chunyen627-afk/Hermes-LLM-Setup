puts "=== 06g start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
config_ip_cache -disable_cache
puts "cache disabled"

set bd [get_files -quiet */top_bd.bd]
puts "BD $bd"
# 從母設計 (bd) 這一層重新產生 —— IP 是巢狀子設計，只能這樣重生
reset_target -quiet all $bd
puts "reset_target bd done"
generate_target all $bd
puts "generate_target bd done"

foreach run {synth_1 top_bd_xspi_slave_0_synth_1 top_bd_matmul_top_0_synth_1} {
    if {[llength [get_runs -quiet $run]] > 0} { reset_run $run }
}
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} { set_property is_enabled false $old }
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
    if {[llength [get_files -quiet */[file tail $f]]] == 0} { add_files -fileset constrs_1 $f }
}
launch_runs synth_1 -jobs 8
puts "SYNTH launched"
puts "=== 06g done [clock format [clock seconds] -format %H:%M:%S] ==="

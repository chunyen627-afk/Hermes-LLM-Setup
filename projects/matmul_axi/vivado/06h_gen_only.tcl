puts "=== 06h generate only start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
config_ip_cache -disable_cache
set bd [get_files -quiet */top_bd.bd]
generate_target all $bd
puts "GENERATE_DONE [clock format [clock seconds] -format %H:%M:%S]"

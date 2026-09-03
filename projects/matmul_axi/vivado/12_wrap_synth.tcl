puts "=== 12 wrap+synth [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
set_property source_mgmt_mode All [current_project]
update_compile_order -fileset sources_1
open_bd_design [get_files -quiet */top_bd.bd]
# 重生 wrapper（DDR4/sysclk port 名變了）
make_wrapper -files [get_files */top_bd.bd] -top -import -force
set_property top top_bd_wrapper [current_fileset]
update_compile_order -fileset sources_1
puts "TOP [get_property top [current_fileset]]"
# 約束：timing_bd（已移除 sysclk_p 那行）+ pins（xSPI）
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} { set_property is_enabled false $old }
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
  if {[llength [get_files -quiet */[file tail $f]]] == 0} { add_files -fileset constrs_1 $f }
}
reset_run synth_1
launch_runs synth_1 -jobs 8
puts "SYNTH launched [clock format [clock seconds] -format %H:%M:%S]"

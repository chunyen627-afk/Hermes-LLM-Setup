puts "=== 10 synth (module_ref) start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
set_property source_mgmt_mode All [current_project]
update_compile_order -fileset sources_1
reset_run synth_1
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} { set_property is_enabled false $old }
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
    if {[llength [get_files -quiet */[file tail $f]]] == 0} { add_files -fileset constrs_1 $f }
}
launch_runs synth_1 -jobs 8
puts "SYNTH launched [clock format [clock seconds] -format %H:%M:%S]"

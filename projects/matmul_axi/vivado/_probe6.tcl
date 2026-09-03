# _probe6.tcl — does generate_target make a saved BD re-openable?
open_project vivado/sys_int/sys_int.xpr
set stale [get_files -quiet -filter {NAME =~ *.bd}]
if {[llength $stale]} { remove_files $stale }
# clear gen dir to reset generation counters
file delete -force vivado/sys_int/sys_int.gen
create_bd_design top_bd
set c1 [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {300} \
    CONFIG.PRIM_SOURCE {Differential_clock_capable_pin} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {100} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {50} ] $c1

puts "P6: after create, .xci exists? [expr {[file exists vivado/sys_int/sys_int.gen/sources_1/bd/top_bd/ip] && [llength [glob -nocomplain vivado/sys_int/sys_int.gen/sources_1/bd/top_bd/ip/*/*.xci]] > 0}] (xml count=[llength [glob -nocomplain vivado/sys_int/sys_int.gen/sources_1/bd/top_bd/ip/*/*.xml]])"

# Force full IP generation
puts "P6: running generate_target all..."
catch {generate_target all [get_ips] } gerr
puts "P6: generate_target catch=$gerr"

puts "P6: after generate, .xci count = [llength [glob -nocomplain vivado/sys_int/sys_int.gen/sources_1/bd/top_bd/ip/*/*.xci]]"

save_bd_design top_bd
puts "P6: saved. xci_path in .bd:"
foreach line [split [read [open vivado/sys_int/sys_int.srcs/sources_1/bd/top_bd/top_bd.bd r]] "\n"] {
    if {[string match "*xci_path*" $line]} { puts "P6   $line" }
}

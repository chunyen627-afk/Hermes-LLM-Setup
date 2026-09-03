# _probe_intf2.tcl — find the exact reference for SC M00_AXI and MIG C0_DDR4_S_AXI.
puts "=== _probe_intf2 start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
open_bd_design $bdfile

puts "-- glob *M00_AXI* --"
foreach p [get_bd_pins -quiet *M00_AXI*] { puts "P: [get_property NAME $p]" }
puts "-- glob *C0_DDR4_S_AXI* --"
foreach p [get_bd_pins -quiet *C0_DDR4_S_AXI*] { puts "P: [get_property NAME $p]" }

# Try referencing the interface port directly (no leading cell) and with cell prefix.
puts "-- axi_smc/M00_AXI (exact) --"
puts "len=[llength [get_bd_pins -quiet axi_smc/M00_AXI]]"
puts "-- get_bd_cells axi_smc -> list its interface ports via report --"
# The BD stores IP interface pins; try the 'interface' property on cells.
foreach c {axi_smc mig_ddr4} {
    set cell [get_bd_cells $c]
    puts "CELL $c interfaces=[get_property INTERFACE_PORTS $cell]"
}
puts "=== _probe_intf2 done ==="

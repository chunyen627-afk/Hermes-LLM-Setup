# _probe_conn6.tcl — verify the .bd JSON edits (clock rewire) load correctly in Vivado.
puts "=== PROBE CONN6 ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

puts "--- net for xspi_slave/xspi_clk ---"
set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"

puts "--- net for clk_wiz_0/clk_out2 ---"
set n2 [get_bd_nets -quiet -of_objects [get_bd_pins clk_wiz_0/clk_out2]]
puts "net=$n2 conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n2]]"

puts "--- all xSPI-domain clock sinks ---"
foreach pin {xspi_slave/xspi_clk matmul_top/xspi_clk rst_xspi/slowest_sync_clk} {
    set nn [get_bd_nets -quiet -of_objects [get_bd_pins $pin]]
    puts "PIN $pin net=$nn"
}

puts "--- ports ---"
foreach p [get_bd_ports -quiet] { puts "PORT $p dir=[get_property -quiet DIR $p]" }

# Do NOT save (read-only check).
puts "--- PROBE CONN6 done ==="

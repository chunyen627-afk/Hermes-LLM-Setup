# _probe_conn5.tcl — try moving ONLY xspi_slave/xspi_clk to the port; list available net cmds.
puts "=== PROBE CONN5 ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

if {[llength [get_bd_ports -quiet xspi_clk]] == 0} { create_bd_port -dir I -type clk xspi_clk }

puts "--- available net-edit commands ---"
foreach c {disconnect_net connect_bd_net delete_bd_net rename_bd_net \
           disconnect_bd_net bd::disconnect_net remove_bd_net} {
    if {[info commands $c] ne ""} { puts "EXISTS $c" } else { puts "MISSING $c" }
}

puts "--- attempt A: connect port + ONLY xspi_slave/xspi_clk ---"
if {[catch {connect_bd_net [get_bd_ports xspi_clk] [get_bd_pins xspi_slave/xspi_clk]} e]} {
    puts "CONN_A_ERR: $e"
} else { puts "CONN_A_OK" }
set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "A result net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"

puts "--- attempt B: disconnect_net on BD (positional via -net only) ---"
set tn [get_bd_nets -quiet clk_wiz_0_clk_out2]
if {[catch {disconnect_net -net $tn} e]} { puts "DISC_B_ERR: $e" } else { puts "DISC_B_OK" }

save_bd_design
puts "--- PROBE CONN5 done ==="

# _probe_conn2.tcl — get exact syntax for disconnect_net and connect_bd_net force.
puts "=== PROBE CONN2 ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

puts "--- help disconnect_net ---"
catch {help disconnect_net} h
puts $h

puts "--- connect_bd_net force check ---"
catch {help connect_bd_net} h2
if {[string match "*-force*" $h2]} { puts "HAS_FORCE yes" } else { puts "HAS_FORCE no" }

# Try the -nets form of disconnect_net on the target net.
set tn [get_bd_nets -quiet clk_wiz_0_clk_out2]
puts "tn=$tn"
if {[catch {disconnect_net -nets $tn} e]} { puts "DISC_NETS_ERR: $e" } else { puts "DISC_NETS_OK" }

# Try connecting the 3 sinks to the port.
if {[llength [get_bd_ports -quiet xspi_clk]] == 0} { create_bd_port -dir I -type clk xspi_clk }
if {[catch {connect_bd_net [get_bd_ports xspi_clk] \
        [get_bd_pins xspi_slave/xspi_clk] \
        [get_bd_pins matmul_top/xspi_clk] \
        [get_bd_pins rst_xspi/slowest_sync_clk]} e]} {
    puts "CONN_ERR: $e"
} else { puts "CONN_OK" }

set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "result net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"
save_bd_design
puts "--- PROBE CONN2 done ==="

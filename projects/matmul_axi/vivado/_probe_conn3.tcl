# _probe_conn3.tcl — rewire xspi_clk: remove 3 sinks from clk_wiz out2, attach to port.
puts "=== PROBE CONN3 ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

if {[llength [get_bd_ports -quiet xspi_clk]] == 0} { create_bd_port -dir I -type clk xspi_clk }

set tn [get_bd_nets -quiet clk_wiz_0_clk_out2]
puts "tn=$tn"
# Remove the 3 sinks from that net (keep clk_out2 as orphaned output).
if {[catch {disconnect_net -net $tn \
        -objects [list xspi_slave/xspi_clk matmul_top/xspi_clk rst_xspi/slowest_sync_clk]} e]} {
    puts "DISC_ERR: $e"
} else { puts "DISC_OK" }

puts "--- after disconnect, net for each sink ---"
foreach pin {xspi_slave/xspi_clk matmul_top/xspi_clk rst_xspi/slowest_sync_clk clk_wiz_0/clk_out2} {
    set n [get_bd_nets -quiet -of_objects [get_bd_pins $pin]]
    puts "PIN $pin net=$n"
}

# Now connect port + 3 sinks onto one net.
if {[catch {connect_bd_net [get_bd_ports xspi_clk] \
        [get_bd_pins xspi_slave/xspi_clk] \
        [get_bd_pins matmul_top/xspi_clk] \
        [get_bd_pins rst_xspi/slowest_sync_clk]} e]} {
    puts "CONN_ERR: $e"
} else { puts "CONN_OK" }

set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "result net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"
save_bd_design
puts "--- PROBE CONN3 done ==="

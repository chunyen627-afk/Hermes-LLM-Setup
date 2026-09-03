# _probe_conn.tcl — verify: disconnect sinks from clk_wiz_0_clk_out2, then connect to xspi_clk port.
puts "=== PROBE CONN start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

puts "--- help connect_bd_net (force?) ---"
catch {help connect_bd_net} h
puts [string range $h 0 900]

# Ensure the xspi_clk port exists.
if {[llength [get_bd_ports -quiet xspi_clk]] == 0} { create_bd_port -dir I -type clk xspi_clk }

puts "--- current net for the three sinks ---"
foreach pin {xspi_slave/xspi_clk matmul_top/xspi_clk rst_xspi/slowest_sync_clk} {
    set n [get_bd_nets -quiet -of_objects [get_bd_pins $pin]]
    puts "PIN $pin net=$n"
}

# Disconnect the whole clk_wiz_0_clk_out2 net (frees all its sinks incl. the 3 we want).
set target_net [get_bd_nets -quiet clk_wiz_0_clk_out2]
puts "target_net=$target_net"
if {[llength $target_net] > 0} {
    if {[catch {disconnect_net $target_net} e]} { puts "DISC_ERR: $e" } else { puts "DISC_OK" }
}

# Now connect the port + 3 sinks onto one net.
if {[catch {connect_bd_net [get_bd_ports xspi_clk] \
        [get_bd_pins xspi_slave/xspi_clk] \
        [get_bd_pins matmul_top/xspi_clk] \
        [get_bd_pins rst_xspi/slowest_sync_clk]} e]} {
    puts "CONN_ERR: $e"
} else { puts "CONN_OK" }

puts "--- resulting net for xspi_slave/xspi_clk ---"
set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"

save_bd_design
puts "--- PROBE CONN done ==="

# _probe_conn4.tcl — delete clk_wiz_0_clk_out2 net, reconnect port+3 sinks.
puts "=== PROBE CONN4 ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

if {[llength [get_bd_ports -quiet xspi_clk]] == 0} { create_bd_port -dir I -type clk xspi_clk }

if {[info commands delete_bd_net] ne ""} { puts "delete_bd_net exists" } else { puts "delete_bd_net MISSING" }
set tn [get_bd_nets -quiet clk_wiz_0_clk_out2]
puts "tn=$tn conns=[get_bd_pins -quiet -of_objects [get_bd_nets $tn]]"

if {[catch {delete_bd_net $tn} e]} { puts "DELNET_ERR: $e" } else { puts "DELNET_OK" }

puts "--- after delete, net for each ---"
foreach pin {xspi_slave/xspi_clk matmul_top/xspi_clk rst_xspi/slowest_sync_clk clk_wiz_0/clk_out2} {
    set n [get_bd_nets -quiet -of_objects [get_bd_pins $pin]]
    puts "PIN $pin net=$n"
}

if {[catch {connect_bd_net [get_bd_ports xspi_clk] \
        [get_bd_pins xspi_slave/xspi_clk] \
        [get_bd_pins matmul_top/xspi_clk] \
        [get_bd_pins rst_xspi/slowest_sync_clk]} e]} {
    puts "CONN_ERR: $e"
} else { puts "CONN_OK" }

set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "result net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"
save_bd_design
puts "--- PROBE CONN4 done ==="

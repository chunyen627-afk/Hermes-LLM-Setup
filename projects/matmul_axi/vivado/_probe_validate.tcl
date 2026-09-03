# _probe_validate.tcl — open edited .bd, run validate, report rc + net/port sanity.
puts "=== PROBE VALIDATE ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

puts "--- sanity: clock net ---"
set n [get_bd_nets -quiet -of_objects [get_bd_pins xspi_slave/xspi_clk]]
puts "xspi_clk net=$n conns=[get_bd_pins -quiet -of_objects [get_bd_nets $n]]"
puts "--- addr segs now ---"
foreach m {/xspi_slave/m_reg /xspi_slave/m_ddr /matmul_top/m_axi} {
    puts "MASTER $m segs=[llength [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces -quiet $m]]]"
}

set vres [catch {validate_bd_design} verr]
puts "VALIDATE_RC $vres"
if {$vres != 0} { puts "VALIDATE_ERRTEXT: $verr" } else { puts "VALIDATE_CLEAN (rc=0)" }

save_bd_design
puts "--- PROBE VALIDATE done ==="

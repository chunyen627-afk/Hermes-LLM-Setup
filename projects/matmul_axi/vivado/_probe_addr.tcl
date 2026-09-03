# _probe_addr.tcl — diagnose "incomplete addressing path" [BD 41-1075].
puts "=== PROBE ADDR start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i" } else break
}

puts "--- address spaces ---"
foreach a [get_bd_addr_spaces -quiet] {
    puts "ADDRSPACE $a master=[get_property -quiet MASTER $a]"
}

puts "--- does xspi_slave/m_reg resolve as an interface? ---"
puts "m_reg pin query: [llength [get_bd_pins -quiet xspi_slave/m_reg*]] pins match m_reg*"
# Is there an AXI interface port named m_reg on xspi_slave?
foreach ip [get_bd_intf_ports -quiet] { puts "INTFPORT $ip" }

puts "--- master segment status ---"
foreach s [get_bd_addr_segs -quiet] {
    puts "SEG $s valid=[get_property -quiet VALID $s] offset=[get_property -quiet OFFSET $s]"
}

puts "--- how is xspi_slave/m_reg_awvalid connected (scattered vs interface)? ---"
set net [get_bd_nets -quiet -of_objects [get_bd_pins -quiet xspi_slave/m_reg_awvalid]]
puts "net for m_reg_awvalid: $net"
puts "  connections: [get_bd_pins -quiet -of_objects [get_bd_nets $net]]"

puts "--- interface nets in design ---"
puts "intf_nets count: [llength [get_bd_intf_nets -quiet]]"
foreach n [get_bd_intf_nets -quiet] { puts "INTFNET $n" }

puts "--- PROBE ADDR done ==="

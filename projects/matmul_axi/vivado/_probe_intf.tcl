# _probe_intf.tcl — figure out how to reference the SC M00_AXI and MIG C0_DDR4_S_AXI
# interface pins so connect_bd_intf_net works.
puts "=== _probe_intf start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
open_bd_design $bdfile
puts "opened bd=[current_bd_design]"

puts "-- A: get_bd_pins axi_smc/M00_AXI --"
puts "[llength [get_bd_pins -quiet axi_smc/M00_AXI]]"
puts "-- B: get_bd_pins mig_ddr4/C0_DDR4_S_AXI --"
puts "[llength [get_bd_pins -quiet mig_ddr4/C0_DDR4_S_AXI]]"

# Enumerate ALL pins of each cell with their IS_INTERFACE flag.
foreach c {axi_smc mig_ddr4} {
    puts "#### $c (interface-typed pins) ####"
    foreach p [get_bd_pins -quiet ${c}/*] {
        set isif [get_property IS_INTERFACE $p]
        if {$isif == 1} {
            puts "IFACE [get_property NAME $p]"
        }
    }
}

# Try the direct connect to see the exact error.
puts "-- try connect_bd_intf_net --"
if {[catch {connect_bd_intf_net [get_bd_pins axi_smc/M00_AXI] [get_bd_pins mig_ddr4/C0_DDR4_S_AXI]} e]} {
    puts "ERR: $e"
} else {
    puts "OK intf_net created"
}
puts "=== _probe_intf done ==="

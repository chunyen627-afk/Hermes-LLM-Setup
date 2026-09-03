# _probe_mig.tcl — inspect mig_ddr4 interfaces: which are connected, which need top-level export.
puts "=== PROBE MIG ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

puts "--- mig_ddr4 interface ports ---"
foreach ip [get_bd_intf_pins -quiet mig_ddr4/*] {
    set n [get_bd_nets -quiet -of_objects [get_bd_intf_pins $ip]]
    puts "INTFPIN $ip net=$n"
}

puts "--- current top-level interface ports ---"
foreach ip [get_bd_intf_ports -quiet] { puts "INTFPORT $ip" }
puts "(intf port count: [llength [get_bd_intf_ports -quiet]])"

puts "--- scalar ports (top-level) ---"
foreach p [get_bd_ports -quiet] { puts "PORT $p dir=[get_property -quiet DIR $p]" }

# How many DDR4 physical pins does C0_DDR4 have?
puts "--- C0_DDR4 pin count ---"
set c0ddr4 [get_bd_intf_pins -quiet mig_ddr4/C0_DDR4]
if {[llength $c0ddr4] > 0} {
    set npins [llength [get_pins -quiet [get_bd_intf_pins mig_ddr4/C0_DDR4]]]
    puts "C0_DDR4 present; physical pin count (approx) = $npins"
} else { puts "C0_DDR4 not found as intf pin" }

puts "--- PROBE MIG done ==="

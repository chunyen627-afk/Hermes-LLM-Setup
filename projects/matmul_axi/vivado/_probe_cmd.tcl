# _probe_cmd.tcl — find the correct command to remove an address segment / clear addressing.
puts "=== PROBE CMD ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} { if {[catch {open_bd_design $bdfile} e]} {} else break }

# List candidate command names.
foreach c {delete_bd_addr_seg remove_bd_addr_seg assign_bd_address delete_bd_addr_space \
           reset_bd_address clear_bd_address bd::reset_addressing} {
    if {[info commands $c] ne ""} { puts "EXISTS $c" } else { puts "MISSING $c" }
}

# Show help for assign_bd_address to see if there's a clear/remove option.
puts "--- help assign_bd_address ---"
catch {help assign_bd_address} h1
puts [string range $h1 0 2000]

puts "--- PROBE CMD done ==="

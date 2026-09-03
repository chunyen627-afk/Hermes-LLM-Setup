# _probe_props.tcl — what properties carry width info on a BD pin?
puts "=== _probe_props start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }

set p [get_bd_pins mig_ddr4/c0_ddr4_s_axi_wdata]
puts "PIN=$p"
puts "--- all properties on MIG wdata pin ---"
foreach pr [get_property -all $p] {
    set v [get_property $pr $p]
    if {[string match "*WIDTH*" $pr] || [string match "*width*" $pr]} {
        puts "PROP $pr = $v"
    }
}
puts "--- list property names containing 'idth' or 'PORT' ---"
foreach pr [get_property -all $p] {
    if {[string match "*IDTH*" $pr] || [string match "*PORT*" $pr]} { puts "NAME $pr" }
}
puts "=== _probe_props done ==="

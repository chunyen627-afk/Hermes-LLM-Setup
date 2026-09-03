# _probe_setw.tcl — does set_property CONFIG.C_M00_AXI_DATA_WIDTH resize the pin? NO SAVE.
puts "=== _probe_setw start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }

set sc [get_bd_cells axi_smc]

# Read the pin width BEFORE via the net/port object properties.
proc pinw {p} {
    set o [get_bd_pins -quiet $p]
    if {[llength $o]==0} { return "NOPIN" }
    # try several property names
    foreach pr {PORT_WIDTH LEFT_BIT RIGHT_BIT} {
        set v [catch {get_property $pr $o} val]
        if {$v==0 && [string length $val]>0} { return "$pr=$val" }
    }
    return "unknown"
}

puts "BEFORE M00_wdata=[pinw axi_smc/M00_AXI_wdata]"
puts "BEFORE S01_wdata=[pinw axi_smc/S01_AXI_wdata]"

# Try to set the config param (catch errors)
set r [catch {set_property CONFIG.C_M00_AXI_DATA_WIDTH 512 $sc} e]
puts "SET M00_DATA rc=$r err=[$e]"
puts "READBACK C_M00_AXI_DATA_WIDTH=[get_property CONFIG.C_M00_AXI_DATA_WIDTH $sc]"

# Does the pin width change after setting config (without regeneration)?
puts "AFTER  M00_wdata=[pinw axi_smc/M00_AXI_wdata]"

# Try update_ip to force re-customization and see if it resizes
set r2 [catch {update_ip -quiet $sc} e2]
puts "UPDATE rc=$r2 err=[$e2]"
puts "UPD    M00_wdata=[pinw axi_smc/M00_AXI_wdata]"
puts "=== _probe_setw done (NOT saved) ==="

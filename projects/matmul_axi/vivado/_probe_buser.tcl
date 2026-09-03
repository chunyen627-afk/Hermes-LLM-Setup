# _probe_buser.tcl — why do buser/ruser fail? NO SAVE.
puts "=== probe start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }

# For each of the 4 problem pins, inspect state on the freshly-opened design.
set pins {axi_smc/S00_AXI_buser axi_smc/S00_AXI_ruser \
          axi_smc/S01_AXI_buser axi_smc/S01_AXI_ruser}
foreach p $pins {
    set obj [get_bd_pins -quiet $p]
    if {[llength $obj] == 0} { puts "PIN_MISSING $p"; continue }
    set nets [get_bd_nets -quiet -of_objects $obj]
    set dir  [get_property DIRECTION $obj]
    set w    [get_property PORT_WIDTH $obj]
    puts "PIN $p dir=$dir width=$w nets=[llength $nets]"
    foreach n $nets { puts "   on net: $n" }
}

# Now try to create one constant and connect it to S00 buser, capturing the error.
set cname cconst_probe_buser
catch {delete_bd_obj [get_bd_cells -quiet $cname]}
set const [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 $cname]
set_property CONFIG.CONST_VAL 0 $const
set_property CONFIG.CONST_WIDTH 1 $const
if {[catch {connect_bd_net [get_bd_pins ${cname}/dout] [get_bd_pins axi_smc/S00_AXI_buser]} e]} {
    puts "CONNECT_ERR: $e"
} else {
    puts "CONNECT_OK S00 buser"
}
# Inspect the nets now touching the constant's dout and the target pin.
puts "dout nets: [get_bd_nets -quiet -of_objects [get_bd_pins ${cname}/dout]]"
puts "buser nets: [get_bd_nets -quiet -of_objects [get_bd_pins axi_smc/S00_AXI_buser]]"
# List any net whose name contains buser (case-insensitive) in the whole design.
foreach n [get_bd_nets -quiet] {
    if {[string match -nocase "*buser*" $n]} { puts "BUSER_NAMED_NET: $n ports=[get_bd_pins -of_objects $n]" }
}
puts "=== probe done ==="

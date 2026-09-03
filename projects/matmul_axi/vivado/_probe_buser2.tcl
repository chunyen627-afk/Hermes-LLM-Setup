# _probe_buser2.tcl — compare arcache vs buser with identical connect code. NO SAVE.
puts "=== probe2 start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL"; exit 1 }

proc trytie {cname pin} {
    catch {delete_bd_obj [get_bd_cells -quiet $cname]}
    set const [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 $cname]
    set_property CONFIG.CONST_VAL 0 $const
    set_property CONFIG.CONST_WIDTH 1 $const
    # How many nets does dout already sit on after create?
    set pre [get_bd_nets -quiet -of_objects [get_bd_pins ${cname}/dout]]
    if {[catch {connect_bd_net [get_bd_pins ${cname}/dout] [get_bd_pins $pin]} e]} {
        puts "FAIL  $pin (pre-nets on dout: [llength $pre]) : $e"
    } else {
        puts "OK    $pin (pre-nets on dout: [llength $pre])"
    }
}

trytie cconst_test_arcache axi_smc/S00_AXI_arcache
trytie cconst_test_buser   axi_smc/S00_AXI_buser
puts "=== probe2 done ==="

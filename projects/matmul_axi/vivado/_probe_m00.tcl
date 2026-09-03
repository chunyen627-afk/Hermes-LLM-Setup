# _probe_m00.tcl — does connect_bd_intf_net work for M00->MIG (both IPs)? NO SAVE.
puts "=== _probe_m00 start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }

# Are these interface pins?
puts "IFP_M00=[llength [get_bd_intf_pins -quiet axi_smc/M00_AXI]]"
puts "IFP_MIG=[llength [get_bd_intf_pins -quiet mig_ddr4/C0_DDR4_S_AXI]]"

# What width is M00 wdata right now (before any connection)?
puts "M00_wdata_before=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_wdata]]"

# Try the clean interface connection.
if {[catch {connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins mig_ddr4/C0_DDR4_S_AXI]} e]} {
    puts "IFNET_ERR: $e"
} else {
    puts "IFNET_OK M00->MIG"
    # After connection, what width did SC give M00?
    puts "M00_wdata_after=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_wdata]]"
    puts "M00_awaddr_after=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_awaddr]]"
}

# Also: what does get_property report for the width on a pin (find right prop name)?
set p [get_bd_pins -quiet axi_smc/M00_AXI_wdata]
puts "PIN_OBJ=$p"
foreach pr {PORT_WIDTH PORT_DIRECTION IS_CLK LEFT_BIT RIGHT_BIT} {
    puts "PROP $pr = [get_property $pr $p]"
}
puts "=== _probe_m00 done (NOT saved) ==="

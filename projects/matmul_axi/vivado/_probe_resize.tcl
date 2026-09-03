# _probe_resize.tcl — does setting SC CONFIG.C_M00_AXI_* resize its existing ports?
puts "=== _probe_resize start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }

set sc [get_bd_cells axi_smc]
puts "BEFORE M00 wdata=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_wdata]]"
# Set the master port to match MIG (512 data / 31 addr / 4 id) and slaves to 32/32/4.
set_property CONFIG.C_M00_AXI_DATA_WIDTH 512 $sc
set_property CONFIG.C_M00_AXI_ADDR_WIDTH 31 $sc
set_property CONFIG.C_M00_AXI_ID_WIDTH   4 $sc
set_property CONFIG.C_S00_AXI_DATA_WIDTH 32 $sc
set_property CONFIG.C_S00_AXI_ADDR_WIDTH 32 $sc
set_property CONFIG.C_S00_AXI_ID_WIDTH   4 $sc
set_property CONFIG.C_S01_AXI_DATA_WIDTH 32 $sc
set_property CONFIG.C_S01_AXI_ADDR_WIDTH 32 $sc
set_property CONFIG.C_S01_AXI_ID_WIDTH   4 $sc
puts "AFTER M00 wdata=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_wdata]]"
puts "AFTER M00 awaddr=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_awaddr]]"
puts "AFTER S00 wdata=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/S00_AXI_wdata]]"
puts "AFTER M00 rdata=[get_property PORT_WIDTH [get_bd_pins -quiet axi_smc/M00_AXI_rdata]]"
# MIG widths for comparison
puts "MIG c0 wdata=[get_property PORT_WIDTH [get_bd_pins -quiet mig_ddr4/c0_ddr4_s_axi_wdata]]"
puts "MIG c0 awaddr=[get_property PORT_WIDTH [get_bd_pins -quiet mig_ddr4/c0_ddr4_s_axi_awaddr]]"
puts "=== _probe_resize done ==="

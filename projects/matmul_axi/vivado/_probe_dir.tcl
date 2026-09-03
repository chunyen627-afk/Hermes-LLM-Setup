# _probe_dir.tcl — compare DIRECTION/WIDTH of working vs failing SC pins. NO SAVE.
puts "=== probe_dir start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL"; exit 1 }

set pins {axi_smc/S00_AXI_arcache axi_smc/S00_AXI_arlock \
          axi_smc/S00_AXI_buser  axi_smc/S00_AXI_ruser \
          axi_smc/S00_AXI_wid    axi_smc/S01_AXI_buser}
foreach p $pins {
    set o [get_bd_pins -quiet $p]
    if {[llength $o]==0} { puts "MISSING $p"; continue }
    puts "PIN $p dir=[get_property DIRECTION $o] width=[get_property PORT_WIDTH $o]"
}
puts "=== probe_dir done ==="

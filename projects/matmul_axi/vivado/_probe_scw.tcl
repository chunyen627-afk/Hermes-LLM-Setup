# _probe_scw.tcl — discover SmartConnect port-width CONFIG params + current values. NO SAVE.
puts "=== _probe_scw start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }

set sc [get_bd_cells axi_smc]
puts "SC_CELL=$sc"

# Dump every CONFIG param whose name contains AXI and WIDTH
puts "--- SC CONFIG params matching *AXI*WIDTH* ---"
foreach pr [get_property -all $sc] {
    if {[string match "*CONFIG*" $pr] && [string match "*WIDTH*" $pr]} {
        puts "P $pr = [get_property $pr $sc]"
    }
}

# Try setting M00 to 512/31/4 and read back
puts "--- try set M00 widths ---"
set r1 [catch {set_property CONFIG.C_M00_AXI_DATA_WIDTH 512 $sc} e1]
puts "SET_M00_DATA rc=$r1 err=$e1"
set r2 [catch {set_property CONFIG.C_M00_AXI_ADDR_WIDTH 31 $sc} e2]
puts "SET_M00_ADDR rc=$r2 err=$e2"
set r3 [catch {set_property CONFIG.C_M00_AXI_ID_WIDTH 4 $sc} e3]
puts "SET_M00_ID rc=$r3 err=$e3"
puts "READBACK M00_DATA=[get_property CONFIG.C_M00_AXI_DATA_WIDTH $sc]"
puts "READBACK M00_ADDR=[get_property CONFIG.C_M00_AXI_ADDR_WIDTH $sc]"
puts "=== _probe_scw done (NOT saved) ==="

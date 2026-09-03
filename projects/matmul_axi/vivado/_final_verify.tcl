# _final_verify.tcl — prove segment 3 can open the persisted top_bd and see its cells
open_project vivado/sys_int/sys_int.xpr
puts "FV: registered .bd files = [format %s [get_files -quiet -filter {NAME =~ *.bd}]]"
# This is what segment 3 will do to work on the existing design:
set r [catch {open_bd_design top_bd} err]
if {$r} { puts "FV: open_bd_design FAILED: $err" } else {
    set cells [get_bd_cells]
    puts "FV: opened top_bd, cell count = [llength $cells]"
    foreach c $cells { puts "FV CELL: [format %s $c]" }
    puts "FV clk_wiz NUM_OUT_CLKS = [get_property CONFIG.NUM_OUT_CLKS [get_bd_cells clk_wiz_0]]"
    puts "FV mig_ddr4 mempart     = [get_property CONFIG.C0.DDR4_MemoryPart [get_bd_cells mig_ddr4]]"
    puts "FV axi_smc NUM_SI/MI    = [get_property CONFIG.NUM_SI [get_bd_cells axi_smc]]/[get_property CONFIG.NUM_MI [get_bd_cells axi_smc]]"
}
puts "FV done ==="

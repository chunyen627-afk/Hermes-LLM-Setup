# _probe_pins.tcl — dump real BD pin names for the AXI connection round.
# Run: cd /c/Users/pjunm/matmul_axi && timeout 280 \
#   /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog -source vivado/_probe_pins.tcl
puts "=== _probe_pins start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
if {[llength $bdfile] == 0} { puts "FATAL: top_bd.bd not registered"; exit 1 }
open_bd_design $bdfile
puts "opened bd=[current_bd_design] cells=[llength [get_bd_cells]]"

# Dump every pin for the four AXI-relevant cells, grouped.
foreach c {xspi_slave matmul_top axi_smc mig_ddr4} {
    puts "#### CELL $c ####"
    set pins [get_bd_pins -quiet ${c}/*]
    foreach p $pins {
        # get the pin's name relative to cell + direction for readability
        set pname [get_property NAME $p]
        puts "PIN $pname"
    }
}
puts "=== _probe_pins done ==="

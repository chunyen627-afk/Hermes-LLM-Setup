# _probe_widths.tcl — dump port widths for SC M00_AXI and MIG C0_DDR4_S_AXI,
# plus the SC CONFIG params that control port sizing. Retries open_bd_design
# because it intermittently fails with an IP-GUI customization error.
puts "=== _probe_widths start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]

# Retry the open a few times (transient IP-customization failures).
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} {
        puts "OPEN_RETRY $i: [string range $e 0 80]"
        exec sleep 2
    } else {
        set opened 1; break
    }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }
puts "opened bd=[current_bd_design]"

proc showwidth {cell} {
    foreach p [get_bd_pins -quiet ${cell}/*] {
        set nm [get_property NAME $p]
        if {[string match "*M00_AXI*" $nm] || [string match "*C0_DDR4_S_AXI*" $nm]} {
            puts "W $nm = [get_property PORT_WIDTH $p]"
        }
    }
}
puts "#### SC M00 + MIG C0 widths ####"
showwidth axi_smc
showwidth mig_ddr4

puts "#### SC CONFIG (port-sizing params) ####"
set sc [get_bd_cells axi_smc]
foreach prop {C_S00_AXI_DATA_WIDTH C_S00_AXI_ADDR_WIDTH C_S00_AXI_ID_WIDTH \
             C_S01_AXI_DATA_WIDTH C_S01_AXI_ADDR_WIDTH C_S01_AXI_ID_WIDTH \
             C_M00_AXI_DATA_WIDTH C_M00_AXI_ADDR_WIDTH C_M00_AXI_ID_WIDTH} {
    puts "CFG $prop = [get_property CONFIG.$prop $sc]"
}
puts "=== _probe_widths done ==="

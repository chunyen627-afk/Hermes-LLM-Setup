# _probe_addr2.tcl — verify: does REMOVING the forced address segments get validate to zero ERROR?
puts "=== PROBE ADDR2 start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i" } else break
}

puts "--- before: master segments ---"
foreach m {/xspi_slave/m_reg /xspi_slave/m_ddr /matmul_top/m_axi} {
    foreach s [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces -quiet $m]] {
        puts "SEG $s off=[get_property -quiet OFFSET $s] range=[get_property -quiet RANGE $s]"
    }
}

# Delete each master-segment to clear the forced (invalid) assignments.
foreach m {/xspi_slave/m_reg /xspi_slave/m_ddr /matmul_top/m_axi} {
    foreach s [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces -quiet $m]] {
        if {[catch {delete_bd_addr_seg $s} e]} { puts "DEL_ERR $s: $e" } else { puts "DEL_OK $s" }
    }
}

puts "--- after: master segments (expect none) ---"
foreach m {/xspi_slave/m_reg /xspi_slave/m_ddr /matmul_top/m_axi} {
    set n [llength [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces -quiet $m]]]
    puts "MASTER $m segs=$n"
}

# Validate and report rc. ERROR:/WARNING: lines are printed to the log; operator greps them.
set vres [catch {validate_bd_design} verr]
puts "VALIDATE_RC $vres"
if {$vres != 0} { puts "VALIDATE_ERRTEXT: $verr" } else { puts "VALIDATE_CLEAN (rc=0)" }

puts "--- PROBE ADDR2 done ==="

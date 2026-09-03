# 03c_addr.tcl — Stage 5, segment 4: ADDRESS ASSIGNMENT ONLY.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 400 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/03c_addr.tcl
#
# SCOPE (planner: "only address assignment, then stop"): pin the three master
# address spaces to explicit offsets with assign_bd_address using an EXPLICIT
# -target_address_space. A bare `assign_bd_address` does nothing here because the
# masters are connected pin-by-pin (scattered nets) so Vivado cannot infer their
# address space from the connections — the planner verified this live:
#   MASTER /xspi_slave/m_reg  segs=0   ->  ASSIGN_OK but offset empty.
# Specifying -target_address_space + the target segment DOES work (planner-verified):
#   assign_bd_address -offset 0x90010000 -range 64K \
#     -target_address_space /xspi_slave/m_reg [get_bd_addr_segs matmul_top/s_axi/reg0] -force
#   -> SEG /xspi_slave/m_reg/SEG_matmul_top_reg0 off=0x90010000  (success)
#
# Three assignments:
#   master                    target segment                                   offset      range
#   /xspi_slave/m_reg         matmul_top/s_axi/reg0                            0x90010000  64K
#   /xspi_slave/m_ddr         mig_ddr4/C0_DDR4_MEMORY_MAP/C0_DDR4_ADDRESS_BLOCK 0x0       2G
#   /matmul_top/m_axi         mig_ddr4/C0_DDR4_MEMORY_MAP/C0_DDR4_ADDRESS_BLOCK 0x0       2G
# (m_ddr and m_axi both map shared DDR at 0x0 — normal for multiple masters on one MIG.)
#
# DO NOT: touch tie-off/xlconstant, port/wrapper/set_property top, or run validate_bd_design.
# No rtl/*.v modified. Idempotent (-force), safe to re-run.

puts "=== 03c_addr.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
if {[llength $bdfile] == 0} { puts "FATAL: top_bd.bd not registered"; exit 1 }

# open_bd_design is intermittently flaky in batch mode (IP_Flow 19-3428). Retry.
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }
puts "opened bd=[current_bd_design] cells=[llength [get_bd_cells]]"

# Helper: assign one master address space to a target segment. Reports ok/err.
proc do_addr {master target off range tag} {
    set segs [get_bd_addr_segs -quiet $target]
    if {[llength $segs] == 0} { puts "ADDR_MISS(target seg) $tag -> $target"; return }
    if {[catch {assign_bd_address -offset $off -range $range \
                  -target_address_space $master [get_bd_addr_segs $target] -force} e]} {
        puts "ADDR_ERR $tag: $e"
    } else {
        puts "ADDR_OK $tag master=$master target=$target off=$off range=$range"
    }
}

# (1) xspi_slave.m_reg -> matmul_top.s_axi register block @ 0x90010000, 64K.
do_addr /xspi_slave/m_reg   matmul_top/s_axi/reg0                             0x90010000 64K "m_reg->s_axi"

# (2) xspi_slave.m_ddr -> MIG DDR @ 0x0, 2G.
do_addr /xspi_slave/m_ddr   mig_ddr4/C0_DDR4_MEMORY_MAP/C0_DDR4_ADDRESS_BLOCK 0x0       2G  "m_ddr->DDR"

# (3) matmul_top.m_axi -> MIG DDR @ 0x0, 2G.
do_addr /matmul_top/m_axi   mig_ddr4/C0_DDR4_MEMORY_MAP/C0_DDR4_ADDRESS_BLOCK 0x0       2G  "m_axi->DDR"

save_bd_design
puts "saved bd after address assignment"

# ---------------------------------------------------------------------------
# ACCEPTANCE: dump every address segment with its offset + range.
# All three target segments must have a non-empty OFFSET, and m_reg must be 0x90010000.
# ---------------------------------------------------------------------------
set segs [get_bd_addr_segs -quiet]
puts "SEG_COUNT [llength $segs]"
foreach s $segs {
    puts "SEG $s off=[get_property -quiet OFFSET $s] range=[get_property -quiet RANGE $s]"
}

# Also show per-master address-space summary (how many segments each master now owns).
foreach m {/xspi_slave/m_reg /xspi_slave/m_ddr /matmul_top/m_axi} {
    set n [llength [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces -quiet $m]]]
    puts "MASTER $m segs=$n"
}

# Free-check mirror of the acceptance python: count serialized addressing entries.
puts "=== 03c_addr.tcl done ==="

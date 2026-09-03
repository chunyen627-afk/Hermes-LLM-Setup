# 04c_wrapper.tcl — Stage 5, round "generate wrapper + set top" (block design last step).
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 600 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/04c_wrapper.tcl
#
# WHY: 04b_bd.tcl left the project top as Vivado's auto-guess (xspi_slave). The real
# deliverable for synthesis is the block design's WRAPPER, which exposes every BD port
# (the xSPI pins + sysclk/rst + the externalized C0_DDR4 interface) as a single module.
# This round: generate the wrapper, import it, set it as top, and verify (a) TOP is the
# wrapper, (b) validate_bd_design stays zero ERROR, (c) the wrapper's 11 xSPI port names
# match constraints/pins_vcu118.xdc (xspi_clk / xspi_cs_n / xspi_dqs / xspi_io[7:0]).
#
# SCOPE this round (planner): make_wrapper + set_property top + update_compile_order.
# NO rtl/*.v or ip_repo/ changes. NO address/clock re-wiring (already validated in 04b).
# If the wrapper renamed any xSPI port, we do NOT rename it back in the BD — instead the
# XDC gets updated to match (planner option A), recorded in CHANGELOG_sysint.md.

puts "=== 04c_wrapper.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
puts "opened project part=[get_property PART [current_project]] top_before=[get_property -quiet top [current_fileset]]"

# --- Open the block design (the one with our validated netlist) --------------
set bdfile [lindex [get_files -quiet */top_bd.bd] 0]
if {$bdfile eq ""} { puts "FATAL: no */top_bd.bd found in project"; exit 1 }
open_bd_design $bdfile
puts "opened bd: [current_bd_design]"
save_bd_design

# --- Generate + import the wrapper -------------------------------------------
# -force makes this idempotent across re-runs. -top sets it as top; -import adds the
# generated .v to the sources_1 fileset so update_compile_order sees it.
make_wrapper -files [get_files */top_bd.bd] -top -import -force

# --- Detect the wrapper module name -------------------------------------------
# make_wrapper names the top-level module <bd_name>_wrapper, so for bd `top_bd` it is
# `top_bd_wrapper`. Compute that deterministically first (no file discovery needed), then
# CONFIRM by reading the generated .v's `module` line. The generated RTL lands at a known
# path under sys_int.gen; get_files globbing right after -import can come back empty, so we
# read the on-disk file directly instead of relying on get_files to see it yet.
set wname [format %s_wrapper [current_bd_design]]   ;# top_bd -> top_bd_wrapper
set cand_paths [list \
    "vivado/sys_int/sys_int.gen/sources_1/bd/top_bd/hdl/${wname}.v" \
    "vivado/sys_int/sys_int.srcs/sources_1/imports/hdl/${wname}.v" \
    "vivado/sys_int/sys_int.srcs/sources_1/bd/top_bd/hdl/${wname}.v"]
# Also try get_files in case the file object is visible (belt-and-suspenders).
# -quiet on get_property: a matched object may lack FILEPATH (generated refs) and we must
# not let that throw a fatal ERROR mid-script.
foreach f [get_files -quiet *${wname}*.v] {
    set fp [get_property -quiet FILEPATH $f]
    if {$fp ne ""} { lappend cand_paths $fp }
}

set wfile ""
foreach p $cand_paths {
    if {[file exists $p]} { set wfile $p; break }
}
if {$wfile ne ""} {
    set fh [open $wfile r]; set content [read $fh]; close $fh
    foreach line [split $content "\n"] {
        if {[regexp {^\s*module\s+([A-Za-z0-9_]+)} $line m wmod]} { set wname $wmod; break }
    }
}
if {$wfile eq ""} { puts "WARN: wrapper .v not found on disk at expected path (proceeding with computed name)" }
puts "WRAPPER_FILE $wfile"
puts "WRAPPER_MODULE $wname"

# --- Set the project top to the wrapper --------------------------------------
set_property top $wname [current_fileset]
update_compile_order -fileset sources_1
puts "TOP [get_property top [current_fileset]]"

# --- Acceptance 2: validate stays clean --------------------------------------
set vres [catch {validate_bd_design} verr]
puts "VALIDATE_RC $vres"
if {$vres != 0} { puts "VALIDATE_ERR: $verr" } else { puts "VALIDATE_CLEAN (rc=0)" }

# --- Acceptance 3: list BD ports, and print the wrapper's xSPI port lines ----
puts "=== BD PORTS ==="
foreach p [get_bd_ports -quiet] { puts "BDPORT $p dir=[get_property -quiet DIR $p]" }
puts "=== WRAPPER XSPI PORT LINES (from generated .v) ==="
if {$wfile ne ""} {
    set fh [open $wfile r]; set content [read $fh]; close $fh
    foreach line [split $content "\n"] {
        if {[regexp {xspi_(clk|cs_n|dqs|io)} $line]} { puts "WRLINE: $line" }
    }
} else { puts "WARN: no wrapper file to print xSPI port lines from" }
puts "=== 04c_wrapper.tcl done ==="

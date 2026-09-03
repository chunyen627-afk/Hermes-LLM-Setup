# 01_project.tcl — Stage 5, segment 1: create project + add RTL + constraints.
# Run from the matmul_axi repo root (so relative paths resolve):
#   cd /c/Users/pjunm/matmul_axi
#   timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/01_project.tcl
#
# Scope of THIS segment (SPEC section 4, item 1): build the project shell only.
# No IP, no block design yet.

puts "=== 01_project.tcl start ==="

# Fresh project. -force wipes any prior ./vivado/sys_int so re-runs are clean.
create_project -force sys_int ./vivado/sys_int -part xcvu9p-flga2104-1-e
puts "created project: [current_project]"

# Pin to the VCU118 board (drives part + available IP/board files).
set_property board_part xilinx.com:vcu118:part0:2.0 [current_project]
puts "board_part set"

# Add all frozen RTL (do NOT modify these; they are the iverilog gate source).
add_files [glob rtl/*.v]
puts "added rtl files: [llength [get_files -filter {NAME =~ *.v} -of [current_fileset]]]"

# Add timing constraints.
add_files -fileset constrs_1 constraints/timing.xdc
puts "added constraints"

update_compile_order -fileset sources_1
puts "compile order updated"

# Acceptance marker for this segment.
puts "PROJECT_OK [current_project] parts=[get_property PART [current_project]] board=[get_property BOARD_PART [current_project]]"
puts "=== 01_project.tcl done ==="

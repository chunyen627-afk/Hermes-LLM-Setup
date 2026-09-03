# 05a_sim.tcl — Stage 5, round 1: minimal xsim bring-up of top_bd_wrapper.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/05a_sim.tcl 2>&1 | tee _05a_run.log
#
# GOAL this round ONLY: prove the block design elaborates and runs in xsim without
# hanging or an elaboration error. The tb (tb/tb_system.v) instantiates
# top_bd_wrapper, feeds the board sysclk + deasserts rst_n, leaves xSPI idle, and
# prints "SYSCHECK boot ok" then $finish after a short window. MIG DDR4 calibration
# is NOT awaited this round (that is the next round).
#
# SCOPE: add tb_system.v to the sim_1 fileset, set it as the simulation top, launch
# behavioral simulation, run 20us. No rtl/*.v / ip_repo/ / BD / xdc changes.

puts "=== 05a_sim.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
puts "opened project part=[get_property PART [current_project]] top=[get_property -quiet top [current_fileset]]"

# --- Add the bring-up testbench to the simulation fileset -------------------
add_files -fileset sim_1 tb/tb_system.v
set_property top tb_system [get_filesets sim_1]
update_compile_order -fileset sim_1
puts "SIM_TOP [get_property top [get_filesets sim_1]]"

# --- Launch behavioral simulation -------------------------------------------
launch_simulation -mode behavioral

# The tb self-terminates with $finish after its short bring-up window (~21us), so
# `run all` stops exactly there (and guarantees the "SYSCHECK boot ok" line prints).
# MIG DDR4 calibration is intentionally NOT awaited this round.
run all
puts "RUN_DONE"

puts "SIM_DONE"
puts "=== 05a_sim.tcl done ==="

# 05b_sim.tcl — Stage 5, round 2: xSPI end-to-end read/write of DDR4 via top_bd_wrapper.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/05b_sim.tcl 2>&1 | tail -40
#
# GOAL this round: wait for MIG DDR4 calibration to complete, then drive an xSPI
# write of 8 halfwords into the DDR4 region and read them back over xSPI, printing
# "SYSCHECK data_flow <checked> <bad>". The tb (tb/tb_system.v) drives a real xSPI
# SCK + CS/IO bus, waits on c0_init_calib_complete, then does the write/read.
#
# SCOPE: same fileset wiring as 05a (add tb_system.v, set top), but close any prior
# sim first so re-runs don't hit "Spawn failed: Broken pipe" from a stale xsim
# holding files. run all (the tb self-terminates with $finish). No rtl/ip/BD/xdc changes.

puts "=== 05b_sim.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
puts "opened project part=[get_property PART [current_project]] top=[get_property -quiet top [current_fileset]]"

# --- Add the testbench to the simulation fileset (idempotent) ----------------
add_files -fileset sim_1 tb/tb_system.v
set_property top tb_system [get_filesets sim_1]
update_compile_order -fileset sim_1
puts "SIM_TOP [get_property top [get_filesets sim_1]]"

# --- Close any prior simulation so re-runs don't collide on held files -------
close_sim -quiet

# --- Verilog unisim libraries -------------------------------------------------
# MIG 的模擬檔案集含校準除錯用的 MicroBlaze，它用到 Verilog 原語
# （BUFG / FDRE / IBUFDS / GND …）。xelab 預設只帶 -L unisim，那是 VHDL 版；
# Verilog 版叫 unisims_ver。沒帶就會冒出 20 個 "Module <BUFG> not found"，
# 而 Vivado 對外只回報一句 "Spawn failed: Broken pipe" —— 完全看不出原因。
# 26/09/03：為此追了 1.6 小時的路徑問題，真正的答案在 xelab.log 裡。
set_property -name {xsim.elaborate.xelab.more_options} \
             -value {-L unisims_ver -L unimacro_ver -L secureip} \
             -objects [get_filesets sim_1]
puts "ELAB_OPTS [get_property xsim.elaborate.xelab.more_options [get_filesets sim_1]]"

# --- Launch behavioral simulation --------------------------------------------
launch_simulation -mode behavioral

# The tb self-terminates with $finish after calib + write/read. MIG DDR4
# calibration takes a while in sim time (minutes of wall clock); run all waits for it.
run all
puts "RUN_DONE"

puts "SIM_DONE"
puts "=== 05b_sim.tcl done ==="

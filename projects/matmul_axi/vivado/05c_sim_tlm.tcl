# 05c_sim_tlm.tcl —— 用 MIG 的 TLM 模型跑模擬（跳過 DDR4 完整校準）
#
# 為什麼：MIG 預設用 RTL 模型，會跑完整的 DDR4 init/校準序列 ——
# 實測 29 分鐘只跑到 1.9 ms 模擬時間，calib 還是 0，
# 推算要 25 小時才會完成。TLM（Transaction Level Model）跳過那一段。
#   ALLOWED_SIM_MODELS = tlm rtl   （MIG 自己宣告支援）

puts "=== 05c_sim_tlm.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [lindex [get_files -quiet */top_bd.bd] 0]

set m [get_bd_cells mig_ddr4]
puts "SIM_MODEL before = [get_property SELECTED_SIM_MODEL $m]"
set_property SELECTED_SIM_MODEL tlm $m
puts "SIM_MODEL after  = [get_property SELECTED_SIM_MODEL $m]"
save_bd_design

# 重新產生模擬用的檔案集
generate_target simulation [get_files */top_bd.bd]
export_ip_user_files -of_objects [get_files */top_bd.bd] -no_script -force -quiet

set_property top tb_system [get_filesets sim_1]
update_compile_order -fileset sim_1
puts "SIM_TOP [get_property top [get_filesets sim_1]]"

close_sim -quiet
launch_simulation -mode behavioral
run all
puts "RUN_DONE"
puts "=== 05c_sim_tlm.tcl done ==="

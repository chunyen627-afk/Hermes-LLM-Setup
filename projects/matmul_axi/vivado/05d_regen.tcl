# 05d_regen.tcl —— 重建 TLM 模式的模擬檔案集
# 05c 只跑 generate_target simulation，SmartConnect 的內部模組沒被帶出來
# （.prj 從 163 行掉到 65 行，少了 sc_exit / sc_node / xlconstant …）
puts "=== 05d_regen.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [lindex [get_files -quiet */top_bd.bd] 0]
puts "SIM_MODEL=[get_property SELECTED_SIM_MODEL [get_bd_cells mig_ddr4]]"
save_bd_design

# 對所有 IP 重新產生模擬目標，不只 bd 本身
set all_ip [get_ips -quiet]
puts "IPS [llength $all_ip]"
generate_target {simulation} [get_files */top_bd.bd] -force
export_simulation -of_objects [get_files */top_bd.bd] \
    -directory vivado/sim_scripts -simulator xsim -force -quiet

set_property top tb_system [get_filesets sim_1]
update_compile_order -fileset sim_1
puts "REGEN_DONE"

# 08_rebuild_modref.tcl —— 用 module reference 重建 bd，取代打包的 IP
#
# 根因（2026-09-03，纏鬥兩小時的結論）：
#   xspi_slave / matmul_top 打包成 IP 後，改 RTL 改不進去 —— IP 的 OOC
#   合成一直命中 IPCACHE，即使 config_ip_cache -disable_cache + bd 層
#   reset_target/generate_target 都繞不過。dcp 驗證確認還是舊 netlist
#   (MDRV_COUNT 2)。
#
# 解法：改用 create_bd_cell -type module -reference。實測 module reference
#   一樣會自動把 AXI 散腳推斷成 interface pin (m_ddr / m_reg / m_axi / s_axi)，
#   也就是當初打包成 IP 的唯一理由；但它直接讀 rtl/ 原檔，沒有 IP 快取層。
#
# 這支從頭重建整個 top_bd，其餘 IP (clk_wiz/mig/smc/rst) 維持不變。

puts "=== 08 rebuild start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr

# RTL 直接進 sources（module reference 從這裡讀）
set_property source_mgmt_mode All [current_project]
foreach f [glob rtl/*.v] {
    if {[llength [get_files -quiet [file tail $f]]] == 0} {
        add_files -norecurse $f
    }
}
update_compile_order -fileset sources_1
puts "REBUILD rtl added, compile order updated"

# 砍掉舊 bd，重建
set oldbd [get_files -quiet */top_bd.bd]
if {[llength $oldbd] > 0} {
    export_ip_user_files -of_objects $oldbd -no_script -force -quiet
    remove_files $oldbd
    file delete -force [file dirname $oldbd]
    puts "REBUILD old bd removed"
}

create_bd_design "top_bd"
puts "REBUILD new bd created"

# --- IP cells（維持原本的，這些沒問題）------------------------------------
set clk  [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0]
set mig  [create_bd_cell -type ip -vlnv xilinx.com:ip:ddr4:2.2 mig_ddr4]
set smc  [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set rsta [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_aclk]
set rstx [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_xspi]

# --- 兩個 RTL 模組用 module reference（不打包 IP）--------------------------
set xs [create_bd_cell -type module -reference xspi_slave xspi_slave]
set mm [create_bd_cell -type module -reference matmul_top matmul_top]
puts "REBUILD cells: [llength [get_bd_cells]]  intf on xspi_slave: [llength [get_bd_intf_pins -quiet xspi_slave/*]]"

# --- MIG board preset（自帶腳位約束）--------------------------------------
apply_bd_automation -rule xilinx.com:bd_rule:ddr4 -config {Board_Interface "ddr4_sdram_c0_082" } $mig -quiet

# --- SmartConnect：2 slave / 1 master -------------------------------------
set_property CONFIG.NUM_SI 2 $smc
set_property CONFIG.NUM_MI 1 $smc

puts "REBUILD_STAGE1_DONE [llength [get_bd_cells]] cells"
puts "=== 08 stage1 done [clock format [clock seconds] -format %H:%M:%S] ==="
# 連線在 stage2 做（分段，避免一支腳本太長出錯難查）
save_bd_design
puts "SAVED"

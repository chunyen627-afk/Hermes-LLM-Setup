# 08_swap_to_modref.tcl —— 在現有 bd 上把兩個 IP cell 換成 module reference
#
# 比砍掉重建 bd 安全：只動 xspi_slave / matmul_top 兩個 cell，
# 其餘 (clk_wiz/mig/smc/rst)、MIG board automation、位址指派全部保留。
#
# 根因見 08_rebuild 註解：IP 快取繞不過，module reference 直讀 rtl/ 無快取。
# module reference 一樣會推斷出 AXI interface pin（實測 m_ddr/m_reg 都認得）。

puts "=== 08 swap start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
set_property source_mgmt_mode All [current_project]

# RTL 進 sources
foreach f [glob rtl/*.v] {
    if {[llength [get_files -quiet [file tail $f]]] == 0} { add_files -norecurse $f }
}
update_compile_order -fileset sources_1

open_bd_design [get_files -quiet */top_bd.bd]
puts "BEFORE cells=[llength [get_bd_cells]] intf_nets=[llength [get_bd_intf_nets]] addr=[llength [get_bd_addr_segs]]"

# --- 記下每個 IP cell 的介面連線，之後照樣重接 ---------------------------
# xspi_slave: m_ddr->axi_smc/S00_AXI, m_reg->matmul_top/s_axi, aclk/aresetn/xspi_clk/xspi_rst_n + xSPI 外部埠
# matmul_top: m_axi->axi_smc/S01_AXI, s_axi<-xspi_slave/m_reg, aclk/aresetn
# 這些用 get_bd_intf_nets 的端點自動抓，不寫死。
proc endpoints {cell} {
    set res {}
    foreach net [get_bd_intf_nets -quiet -of [get_bd_intf_pins -quiet $cell/*]] {
        foreach pin [get_bd_intf_pins -quiet -of $net] {
            if {[string first "$cell/" $pin] != 0} { lappend res [list [file tail [get_bd_intf_pins -quiet -of $net -filter "PATH=~$cell/*"]] $pin] }
        }
    }
    return $res
}

# 抓 scalar net 連線（時脈/reset）
proc scalar_conns {cell} {
    set res {}
    foreach pin [get_bd_pins -quiet $cell/*] {
        set net [get_bd_nets -quiet -of $pin]
        if {[llength $net] == 0} continue
        foreach other [get_bd_pins -quiet -of $net] {
            if {[string first "$cell/" $other] != 0} {
                lappend res [list [file tail $pin] $other]
            }
        }
        foreach oport [get_bd_ports -quiet -of $net] {
            lappend res [list [file tail $pin] "PORT:$oport"]
        }
    }
    return $res
}

set xs_intf [endpoints xspi_slave]
set xs_scal [scalar_conns xspi_slave]
set mm_intf [endpoints matmul_top]
set mm_scal [scalar_conns matmul_top]
puts "SAVED_CONNS xs_intf=[llength $xs_intf] xs_scal=[llength $xs_scal] mm_intf=[llength $mm_intf] mm_scal=[llength $mm_scal]"
foreach e $xs_intf { puts "  XS_INTF $e" }
foreach e $xs_scal { puts "  XS_SCAL $e" }
foreach e $mm_intf { puts "  MM_INTF $e" }
foreach e $mm_scal { puts "  MM_SCAL $e" }

# 這一支只「記錄」連線就停 —— 先看清楚要重接什麼，下一支才動手替換
puts "=== 08 swap (record only) done [clock format [clock seconds] -format %H:%M:%S] ==="

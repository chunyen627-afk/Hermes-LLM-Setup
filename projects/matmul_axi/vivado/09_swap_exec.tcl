# 09_swap_exec.tcl —— 把 bd 裡兩個 IP cell 換成 module reference 並重接
#
# 前置驗證都過了：module reference 的埠名跟原 IP 完全一致
# (aclk/arst_n/xspi_*/m_ddr/m_reg/m_axi/s_axi)，所以刪掉 IP cell、
# 建 module cell、照原連線重接即可。module reference 直讀 rtl/ 無快取。
#
# 連線來源：08_swap 記錄的 endpoints（intf 4+4、scalar 19+15）。
# 這裡用「刪 cell 前先抓、刪後重接」的方式，不寫死。

puts "=== 09 swap exec start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
set_property source_mgmt_mode All [current_project]
foreach f [glob rtl/*.v] {
    if {[llength [get_files -quiet [file tail $f]]] == 0} { add_files -norecurse $f }
}
update_compile_order -fileset sources_1
open_bd_design [get_files -quiet */top_bd.bd]
puts "BEFORE cells=[llength [get_bd_cells]] intf_nets=[llength [get_bd_intf_nets]] addr=[llength [get_bd_addr_segs]]"

# --- 抓兩個 cell 的所有連線（intf + scalar + external port）--------------
proc grab {cell} {
    set intf {} ; set scal {}
    foreach pin [get_bd_intf_pins -quiet $cell/*] {
        set net [get_bd_intf_nets -quiet -of $pin]
        foreach o [get_bd_intf_pins -quiet -of $net] {
            if {[string first "$cell/" $o] != 0} { lappend intf [list [file tail $pin] $o] }
        }
    }
    foreach pin [get_bd_pins -quiet $cell/*] {
        set net [get_bd_nets -quiet -of $pin]
        if {[llength $net]==0} continue
        foreach o [get_bd_pins -quiet -of $net] {
            if {[string first "$cell/" $o] != 0} { lappend scal [list [file tail $pin] PIN $o] }
        }
        foreach o [get_bd_ports -quiet -of $net] { lappend scal [list [file tail $pin] PORT $o] }
    }
    return [list $intf $scal]
}
lassign [grab xspi_slave] xs_intf xs_scal
lassign [grab matmul_top] mm_intf mm_scal
puts "GRABBED xs: [llength $xs_intf] intf, [llength $xs_scal] scal | mm: [llength $mm_intf] intf, [llength $mm_scal] scal"

# --- 刪掉 IP cell，建 module reference cell（同名）------------------------
delete_bd_objs [get_bd_cells xspi_slave]
delete_bd_objs [get_bd_cells matmul_top]
create_bd_cell -type module -reference xspi_slave xspi_slave
create_bd_cell -type module -reference matmul_top matmul_top
puts "SWAPPED cells now=[llength [get_bd_cells]]"

# --- 重接 -----------------------------------------------------------------
proc reconnect {cell intf scal} {
    foreach e $intf {
        lassign $e p other
        if {[catch {connect_bd_intf_net [get_bd_intf_pins $cell/$p] [get_bd_intf_pins $other]} err]} {
            puts "  WARN intf $cell/$p -> $other : $err"
        }
    }
    foreach e $scal {
        lassign $e p kind other
        if {$kind eq "PORT"} { set dst [get_bd_ports -quiet $other] } else { set dst [get_bd_pins -quiet $other] }
        if {[llength $dst]==0} continue
        if {[catch {connect_bd_net [get_bd_pins $cell/$p] $dst} err]} {
            puts "  WARN net $cell/$p -> $other : $err"
        }
    }
}
reconnect xspi_slave $xs_intf $xs_scal
reconnect matmul_top $mm_intf $mm_scal

# --- 位址重指派 -----------------------------------------------------------
assign_bd_address -quiet
# m_reg 要 0x9001_0000（SPEC）
catch {assign_bd_address -offset 0x90010000 -range 64K \
       -target_address_space /xspi_slave/m_reg [get_bd_addr_segs matmul_top/s_axi/reg0] -force}

puts "AFTER cells=[llength [get_bd_cells]] intf_nets=[llength [get_bd_intf_nets]] addr=[llength [get_bd_addr_segs]]"
foreach s [get_bd_addr_segs -quiet] { puts "  SEG $s off=[get_property -quiet OFFSET $s]" }

if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL $e" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 09 done [clock format [clock seconds] -format %H:%M:%S] ==="

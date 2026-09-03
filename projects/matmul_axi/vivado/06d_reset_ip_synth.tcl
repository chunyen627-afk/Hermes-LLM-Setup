# 06d_reset_ip_synth.tcl —— 強制 IP 重新 OOC 合成後再合頂層
#
# 根因（2026-09-03）：xspi_slave / matmul_top 是打包成 IP 的，
# 頂層合成用它們的 _stub.v（黑盒）+ 各自 out-of-context 預先合好的 dcp。
# 那兩個 IP 的 OOC dcp 停在 20:39（改 RTL 之前），所以改 rtl/ 或 ipshared/
# 的 .v 都不會生效 —— 頂層合成根本不重讀原始碼，直接用舊 dcp。
#
# 解法：reset 那兩個 IP 的 synth run，讓它們用更新後的 RTL 重新 OOC 合成。

puts "=== 06d start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr

# reset 頂層 + 兩個 IP 的 OOC run
foreach run {synth_1 top_bd_xspi_slave_0_synth_1 top_bd_matmul_top_0_synth_1} {
    if {[llength [get_runs -quiet $run]] > 0} {
        reset_run $run
        puts "RESET $run"
    }
}

# 換 block design 版約束
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} { set_property is_enabled false $old }
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
    if {[llength [get_files -quiet */[file tail $f]]] == 0} {
        add_files -fileset constrs_1 $f
    }
}

# 啟動頂層合成 —— 它會自動先跑相依的 IP OOC run（因為剛 reset 了）
launch_runs synth_1 -jobs 8
puts "SYNTH launched (IP OOC runs will run first, then top)"
puts "=== 06d done [clock format [clock seconds] -format %H:%M:%S] ==="

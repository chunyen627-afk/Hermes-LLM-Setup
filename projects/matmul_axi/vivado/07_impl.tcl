# 07_impl.tcl —— implement + bitstream
#
# ⚠ 用 06a 那套模式：launch_runs 後直接退出，不用 wait_on_run
#    （wait_on_run 會讓這個 Vivado build 崩潰，見 CHANGELOG 20:55）
#    合成那次證實：即使主程序退出，run 還是會跑完並產生 .dcp。
#    判斷完成看檔案，不要看行程。
#
# 用法：cmd /c _runsim.bat vivado/07_impl.tcl
# 完成後用 07b_report_impl.tcl 讀結果。
#
# 合成的 WNS = −1.378 ns，違例在 MIG 內部的 360 MHz。
# place & route 常會改善這種路徑 —— 這一輪就是要看它收不收得了。

puts "=== 07_impl.tcl start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr

set sp [get_property PROGRESS [get_runs synth_1]]
puts "IMPL synth_1 progress=$sp"
if {$sp ne "100%"} { error "合成還沒完成（$sp），先跑 06a" }

if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    reset_run impl_1 -quiet
}

# to_step write_bitstream：一路跑到產生 bitstream
launch_runs impl_1 -to_step write_bitstream -jobs 8
puts "IMPL launched (不等待，wait_on_run 會崩)"
puts "=== 07_impl.tcl done [clock format [clock seconds] -format %H:%M:%S] ==="

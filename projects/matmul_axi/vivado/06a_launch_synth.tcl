# 06a_launch_synth.tcl —— 只「啟動」合成，不等待
#
# 為什麼分成兩支：Vivado 的 wait_on_run 在這台機器上會
# EXCEPTION_ACCESS_VIOLATION 崩掉主程序（20:41、20:46 各一次，
# hs_err_pid*.log 沒有內容）。但**子行程的合成本身是好的** ——
# 崩潰時 MIG 和 SmartConnect 的 IP 合成都已成功完成並產生 dcp。
#
# 所以：這支只 launch_runs 然後退出，讓合成在自己的行程跑完；
# 完成後用 06b_report_synth.tcl 讀結果。

puts "=== 06a_launch_synth.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
puts "SYNTH part=[get_property PART [current_project]] top=[get_property top [current_fileset]]"

# --- 換上 block design 版的約束 ----------------------------------------------
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} {
    set_property is_enabled false $old
    puts "SYNTH disabled old timing.xdc (get_ports aclk 對 wrapper 不適用)"
}
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
    set base [file tail $f]
    if {[llength [get_files -quiet */$base]] == 0} {
        add_files -fileset constrs_1 $f
        puts "SYNTH added $base"
    }
}

# --- 啟動，不等 -------------------------------------------------------------
# 一律 reset —— 改過 RTL 之後即使 PROGRESS 是 100%，launch_runs 也會拒絕
# （"Run 'synth_1' needs to be reset before launching"）。
reset_run synth_1
launch_runs synth_1 -jobs 8
puts "SYNTH launched, not waiting (wait_on_run crashes this Vivado build)"
puts "=== 06a done ==="

# 06c_synth_inline.tcl —— 在同一個 session 裡直接合成，不用 launch_runs
#
# 前兩種做法都不行：
#   06_synth.tcl  : launch_runs + wait_on_run -> Vivado 主程序
#                   EXCEPTION_ACCESS_VIOLATION 崩潰
#   06a_launch    : launch_runs 後退出 -> 子行程被父行程帶走，合成沒跑完
#
# synth_design 是在當前 session 直接跑，不經過 run 的排程/子行程機制，
# 兩個問題都避開。代價是這支會跑很久（20-40 分鐘），要背景執行。

set OUTDIR [file normalize "vivado_out"]
file mkdir $OUTDIR
proc jstr {s} { return "\"[string map {\\ \\\\ \" \\\"} $s]\"" }
set r [dict create status unknown]

puts "=== 06c_synth_inline.tcl start [clock format [clock seconds] -format %H:%M:%S] ==="

if {[catch {
    open_project vivado/sys_int/sys_int.xpr
    puts "SYNTH part=[get_property PART [current_project]] top=[get_property top [current_fileset]]"

    # --- 約束：換成 block design 版 ----------------------------------------
    set old [get_files -quiet */timing.xdc]
    if {[llength $old] > 0} { set_property is_enabled false $old }
    foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
        if {[llength [get_files -quiet */[file tail $f]]] == 0} {
            add_files -fileset constrs_1 $f
        }
    }
    puts "SYNTH constraints ready"

    # --- IP 的 dcp 要先有（那些 IP 合成先前已成功）--------------------------
    # 若 IP 還沒合成，synth_design 會自己 elaborate 它們（比較慢但可行）
    puts "SYNTH starting synth_design [clock format [clock seconds] -format %H:%M:%S]"
    synth_design -top top_bd_wrapper -part xcvu9p-flga2104-2L-e
    puts "SYNTH synth_design done [clock format [clock seconds] -format %H:%M:%S]"
    dict set r synth_status "completed"

    write_checkpoint -force $OUTDIR/post_synth.dcp
    puts "SYNTH checkpoint written"

    # --- 時序 --------------------------------------------------------------
    set worst 1e9
    foreach clk [get_clocks -quiet] {
        set nm [get_property NAME $clk]
        set per [get_property PERIOD $clk]
        set p [get_timing_paths -quiet -max_paths 1 -nworst 1 -setup -to [get_clocks $nm]]
        if {[llength $p] > 0} {
            set s [get_property SLACK $p]
            set mhz [expr {$per > 0 ? 1000.0/$per : 0}]
            puts [format "SYNTH clk %-30s %7.1f MHz  slack %8.3f ns" $nm $mhz $s]
            dict set r "clk_$nm" $s
            if {$s < $worst} { set worst $s }
        }
    }
    dict set r wns $worst
    puts "SYNTH WNS=$worst"

    report_timing_summary -file $OUTDIR/timing.rpt -quiet
    report_utilization    -file $OUTDIR/utilization.rpt -quiet

    # --- 資源（XCVU9P: LUT 1182240 / DSP 6840 / BRAM 2160）------------------
    set lut  [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == LUT}]]
    set ff   [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]
    set dsp  [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == ARITHMETIC}]]
    set bram [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == BLOCKRAM}]]
    dict set r lut $lut ; dict set r ff $ff
    dict set r dsp $dsp ; dict set r bram $bram
    puts [format "SYNTH util LUT=%d (%.2f%%) FF=%d DSP=%d (%.2f%%) BRAM=%d (%.2f%%)" \
          $lut [expr {$lut*100.0/1182240}] $ff $dsp [expr {$dsp*100.0/6840}] \
          $bram [expr {$bram*100.0/2160}]]

    dict set r status [expr {$worst >= 0 ? "timing_met" : "timing_violated"}]

} err]} {
    puts "SYNTH ERROR: $err"
    dict set r error $err
    if {[dict get $r status] eq "unknown"} { dict set r status "error" }
}

set fh [open $OUTDIR/synth_summary.json w]
puts $fh "\{"
set first 1
dict for {k v} $r {
    if {!$first} { puts $fh "," }
    set first 0
    if {[string is double -strict $v]} {
        puts -nonewline $fh "  [jstr $k]: $v"
    } else {
        puts -nonewline $fh "  [jstr $k]: [jstr $v]"
    }
}
puts $fh "\n\}"
close $fh
puts "SYNTH_SUMMARY_WRITTEN"
puts "=== 06c done [clock format [clock seconds] -format %H:%M:%S] ==="

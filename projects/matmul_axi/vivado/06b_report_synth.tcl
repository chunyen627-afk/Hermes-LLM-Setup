# 06b_report_synth.tcl —— 合成完成後讀結果（時序 / 資源）
#
# 分兩支的原因見 06a：wait_on_run 會讓這個 Vivado build 崩潰
# （EXCEPTION_ACCESS_VIOLATION），所以 06a 只啟動、不等待，
# 合成在自己的行程跑完後再跑這一支。
#
# 用法：cmd /c _runsim.bat vivado/06b_report_synth.tcl
# 產出：vivado_out/synth_summary.json + timing.rpt + utilization.rpt

set OUTDIR [file normalize "vivado_out"]
file mkdir $OUTDIR
proc jstr {s} { return "\"[string map {\\ \\\\ \" \\\"} $s]\"" }
set r [dict create status unknown]

if {[catch {
    open_project vivado/sys_int/sys_int.xpr

    set st   [get_property STATUS   [get_runs synth_1]]
    set prog [get_property PROGRESS [get_runs synth_1]]
    puts "SYNTH status=$st progress=$prog"
    dict set r synth_status $st
    dict set r synth_progress $prog

    if {$prog ne "100%"} {
        dict set r status "not_finished"
        error "synth_1 progress=$prog (還沒跑完，等一下再跑這支)"
    }

    open_run synth_1 -name synth_1

    # --- 時序：每個時脈各自的 slack ---------------------------------------
    # 300 MHz 那條（MIG 的 ui_clk）是重點 —— 它決定最終 TPS。
    set worst 1e9
    foreach clk [get_clocks -quiet] {
        set nm [get_property NAME $clk]
        set per [get_property PERIOD $clk]
        set p [get_timing_paths -quiet -max_paths 1 -nworst 1 -setup -to [get_clocks $nm]]
        if {[llength $p] > 0} {
            set s [get_property SLACK $p]
            set mhz [expr {$per > 0 ? 1000.0/$per : 0}]
            puts [format "SYNTH clk %-28s %7.1f MHz  slack %8.3f ns" $nm $mhz $s]
            dict set r "clk_$nm" $s
            if {$s < $worst} { set worst $s }
        }
    }
    dict set r wns $worst
    puts "SYNTH WNS=$worst"

    report_timing_summary -file $OUTDIR/timing.rpt -quiet
    report_utilization    -file $OUTDIR/utilization.rpt -quiet

    # --- 資源 --------------------------------------------------------------
    # XCVU9P 總量：LUT 1182240 / FF 2364480 / DSP 6840 / BRAM 2160
    foreach {k prop} {lut STATS.LUT ff STATS.FF dsp STATS.DSP bram STATS.BRAM} {
        set v [get_property $prop [get_runs synth_1]]
        dict set r $k $v
    }
    puts [format "SYNTH util LUT=%s FF=%s DSP=%s BRAM=%s" \
          [dict get $r lut] [dict get $r ff] [dict get $r dsp] [dict get $r bram]]
    puts [format "SYNTH util%% LUT=%.2f%% DSP=%.2f%% BRAM=%.2f%%" \
          [expr {[dict get $r lut]*100.0/1182240}] \
          [expr {[dict get $r dsp]*100.0/6840}] \
          [expr {[dict get $r bram]*100.0/2160}]]

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
puts "=== 06b done ==="

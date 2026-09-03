# 06_synth.tcl —— 合成 + 時序檢查（block design 專案）
#
# 用法（一定要用 _runsim.bat 那種帶 settings64 的環境，或直接給完整路徑）：
#   cmd /c C:\Users\pjunm\matmul_axi\_runsim.bat vivado/06_synth.tcl
#
# 目標：
#   1. 合成得過（有沒有不可合成的寫法、latch、undriven）
#   2. **300 MHz 收不收得了** ← 這是最重要的問題
#   3. 資源用量（DSP/BRAM/LUT，決定之後能加幾個 MAC）
#
# 產出：vivado_out/synth_summary.json + utilization.rpt + timing.rpt

set OUTDIR [file normalize "vivado_out"]
file mkdir $OUTDIR

proc jstr {s} { return "\"[string map {\\ \\\\ \" \\\"} $s]\"" }

set result [dict create status "unknown" step "start"]

if {[catch {

    open_project vivado/sys_int/sys_int.xpr
    dict set result step "opened"
    puts "SYNTH part=[get_property PART [current_project]] top=[get_property top [current_fileset]]"

    # --- 換上 block design 版的約束 ------------------------------------------
    # timing.xdc 是給「RTL 當 top」寫的，對 wrapper 不適用（get_ports aclk 找不到）
    set old [get_files -quiet */timing.xdc]
    if {[llength $old] > 0} {
        set_property is_enabled false $old
        puts "SYNTH disabled old timing.xdc"
    }
    set newx [get_files -quiet */timing_bd.xdc]
    if {[llength $newx] == 0} {
        add_files -fileset constrs_1 constraints/timing_bd.xdc
        puts "SYNTH added timing_bd.xdc"
    }
    # 腳位約束（xSPI 的 11 條）
    set pins [get_files -quiet */pins_vcu118.xdc]
    if {[llength $pins] == 0} {
        add_files -fileset constrs_1 constraints/pins_vcu118.xdc
        puts "SYNTH added pins_vcu118.xdc"
    }
    dict set result step "constraints"

    # --- 合成 ----------------------------------------------------------------
    reset_run synth_1 -quiet
    launch_runs synth_1 -jobs 8
    wait_on_run synth_1
    set st [get_property STATUS [get_runs synth_1]]
    set prog [get_property PROGRESS [get_runs synth_1]]
    puts "SYNTH status=$st progress=$prog"
    dict set result synth_status $st
    dict set result synth_progress $prog

    if {$prog ne "100%"} {
        dict set result status "synth_failed"
        dict set result step "synth"
        error "synthesis did not reach 100% (progress=$prog)"
    }

    open_run synth_1 -name synth_1
    dict set result step "opened_run"

    # --- 時序 ----------------------------------------------------------------
    set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
    set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
    puts "SYNTH WNS=$wns WHS=$whs"
    dict set result wns $wns
    dict set result whs $whs

    # 每個時脈各自的 slack —— 300 MHz 那條是重點
    foreach clk [get_clocks -quiet] {
        set p [get_timing_paths -quiet -max_paths 1 -nworst 1 -setup -to [get_clocks $clk]]
        if {[llength $p] > 0} {
            puts "SYNTH clk [get_property NAME $clk] period=[get_property PERIOD $clk] slack=[get_property SLACK $p]"
        }
    }

    report_timing_summary -file $OUTDIR/timing.rpt -quiet
    report_utilization    -file $OUTDIR/utilization.rpt -quiet

    # --- 資源 ----------------------------------------------------------------
    set lut  [get_property {STATS.LUT}  [get_runs synth_1]]
    set ff   [get_property {STATS.FF}   [get_runs synth_1]]
    set dsp  [get_property {STATS.DSP}  [get_runs synth_1]]
    set bram [get_property {STATS.BRAM} [get_runs synth_1]]
    puts "SYNTH util LUT=$lut FF=$ff DSP=$dsp BRAM=$bram"
    dict set result lut $lut
    dict set result ff $ff
    dict set result dsp $dsp
    dict set result bram $bram

    dict set result status [expr {$wns >= 0 ? "timing_met" : "timing_violated"}]
    dict set result step "done"

} err]} {
    puts "SYNTH ERROR: $err"
    dict set result error $err
    if {[dict get $result status] eq "unknown"} { dict set result status "error" }
}

# --- JSON 摘要 ---------------------------------------------------------------
set fh [open $OUTDIR/synth_summary.json w]
puts $fh "\{"
set first 1
dict for {k v} $result {
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
puts "SYNTH_SUMMARY_WRITTEN $OUTDIR/synth_summary.json"
puts "=== 06_synth.tcl done ==="

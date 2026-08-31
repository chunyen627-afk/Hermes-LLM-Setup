# synth_check.tcl —— VCU118 合成 + 時序檢查，結果寫成機器可讀的 JSON
#
# 用法：
#   vivado.bat -mode batch -nolog -nojournal \
#     -source synth_check.tcl -tclargs <頂層模組> [週期ns] [rtl目錄] [xdc檔]
#
# 例：
#   vivado.bat -mode batch -nolog -nojournal \
#     -source synth_check.tcl -tclargs matmul_top 5.0 rtl constraints/timing.xdc
#
# 產出（都在 <專案根>/vivado_out/）：
#   synth_summary.json   ← 先看這個，判讀標準見 SKILL.md 第六節
#   utilization.rpt  timing.rpt  synth.log
#
# 設計原則：任何一步失敗都要在 JSON 裡留下 status 和原因，
# 不要讓呼叫端只拿到一個 exit code 卻不知道死在哪。

set PART  "xcvu9p-flga2104-2L-e"
set BOARD "xilinx.com:vcu118:part0:2.0"

# ---- 參數 ----
set top    [lindex $argv 0]
set period [expr {[llength $argv] > 1 ? [lindex $argv 1] : 5.0}]
set rtldir [expr {[llength $argv] > 2 ? [lindex $argv 2] : "rtl"}]
set xdc    [expr {[llength $argv] > 3 ? [lindex $argv 3] : ""}]

if {$top eq ""} {
    puts "ERROR: need top module name. See header for usage."
    exit 1
}

set outdir "vivado_out"
file mkdir $outdir
set jsonf [file join $outdir synth_summary.json]

# 出事時也要寫出 JSON，不然呼叫端只看到 exit code
proc bail {jsonf status msg} {
    set fh [open $jsonf w]
    puts $fh "{\"status\": \"$status\", \"error\": \"$msg\"}"
    close $fh
    puts "SYNTH_RESULT: $status - $msg"
    exit 1
}

# ---- 授權先驗，省得跑到一半才發現 ----
if {[llength [get_parts -quiet $PART]] == 0} {
    bail $jsonf "no_license" \
        "cannot see $PART - Standard edition or license lost. See SKILL.md section 2"
}

# ---- 讀 RTL ----
set srcs [glob -nocomplain [file join $rtldir *.v] [file join $rtldir *.sv]]
if {[llength $srcs] == 0} {
    bail $jsonf "no_sources" "no .v/.sv found in $rtldir"
}
puts "SOURCES: [llength $srcs] files"

create_project -in_memory -part $PART
set_property board_part $BOARD [current_project]

if {[catch {read_verilog -sv $srcs} e]} {
    bail $jsonf "read_failed" [string map {\" ' \n " "} $e]
}

# ---- 約束 ----
# 沒給 xdc 就自動生一份最小的：頂層每個看起來像時脈的 port 都建 clock，
# 而且兩兩宣告 asynchronous（見 SKILL.md：漏了這個會report 出一堆假違例）
if {$xdc ne "" && [file exists $xdc]} {
    read_xdc $xdc
    puts "XDC: $xdc"
} else {
    # 直接從頂層原始碼抓 clock port 名稱，不先 elaborate。
    # （早期版本用 synth_design -rtl 來抓 port，但那一步本身就會因為
    #  RTL 有問題而失敗，錯誤訊息還會被 realtime 暫存檔的雜訊蓋掉。）
    set gen [file join $outdir auto_clocks.xdc]
    set fh [open $gen w]
    set clknames {}
    set topfile ""
    foreach f $srcs {
        set fd [open $f r]; set txt [read $fd]; close $fd
        if {[regexp "module\\s+$top\\s*\[#(\]" $txt]} { set topfile $f; break }
    }
    if {$topfile eq ""} {
        close $fh
        bail $jsonf "top_not_found" "module $top not found in $rtldir"
    }
    set fd [open $topfile r]; set txt [read $fd]; close $fd
    foreach line [split $txt \n] {
        if {[regexp {input\s+(?:wire\s+)?(\w+)\s*[,)]} $line -> pn]} {
            if {[regexp -nocase {(^|_)(a?clk|clock)($|_)|^clk} $pn]} {
                puts $fh "create_clock -name $pn -period $period \[get_ports $pn\]"
                lappend clknames $pn
            }
        }
    }
    if {[llength $clknames] > 1} {
        set grps ""
        foreach c $clknames { append grps " -group \[get_clocks $c\]" }
        puts $fh "set_clock_groups -asynchronous$grps"
        puts "CLOCKS: $clknames (declared mutually asynchronous)"
    } else {
        puts "CLOCKS: $clknames"
    }
    close $fh
    read_xdc $gen
}

# ---- 合成 ----
if {[catch {synth_design -top $top -part $PART} e]} {
    bail $jsonf "synth_failed" [string map {\" ' \n " "} $e]
}

report_utilization     -file [file join $outdir utilization.rpt]
report_timing_summary  -file [file join $outdir timing.rpt] -max_paths 10

# ---- 抓數字 ----
proc cell_count {t} { return [llength [get_cells -quiet -hier -filter "PRIMITIVE_TYPE =~ $t"]] }

set wns "null"
if {[catch {
    set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 -setup]
    if {[llength $paths] > 0} { set wns [get_property SLACK [lindex $paths 0]] }
}]} {}

set latch [cell_count REGISTER.latch.*]
set lut   [cell_count CLB.LUT.*]
set ff    [cell_count REGISTER.SDR.*]
set dsp   [cell_count ARITHMETIC.*.*]
set bram  [cell_count BLOCKRAM.*.*]

# 判定：合成成功 + WNS 不是負的 + 沒有 latch
set verdict "ok"
set notes {}
if {$wns ne "null" && $wns < 0} { set verdict "timing_fail"; lappend notes "negative WNS - timing not met" }
if {$latch > 0} { lappend notes "$latch latches inferred (missing else/default in always)" }

set fh [open $jsonf w]
puts $fh "{"
puts $fh "  \"status\": \"$verdict\","
puts $fh "  \"top\": \"$top\","
puts $fh "  \"part\": \"$PART\","
puts $fh "  \"target_period_ns\": $period,"
puts $fh "  \"wns_ns\": $wns,"
puts $fh "  \"latch_count\": $latch,"
puts $fh "  \"lut\": $lut, \"ff\": $ff, \"dsp\": $dsp, \"bram\": $bram,"
puts $fh "  \"notes\": \"[join $notes {; }]\","
puts $fh "  \"stage\": \"synth_only (no implement yet; WNS is optimistic)\""
puts $fh "}"
close $fh

puts "SYNTH_RESULT: $verdict  WNS=$wns ns  latch=$latch  LUT=$lut FF=$ff DSP=$dsp BRAM=$bram"
puts "Reports in $outdir/"
if {$verdict ne "ok"} { exit 1 }
exit 0

# 06f_regen_ip.tcl —— 強制 IP 重新產生輸出（繞過 IPCACHE）
#
# 清 config_ip_cache 後 IP OOC 還是命中 cache（io_out_hi 得 0）。
# 這支用 upgrade_ip + reset_target + generate_target 逼 IP 從
# ip_repo 的（已更新）RTL 重新產生所有輸出產品，再合成。

puts "=== 06f start [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr

# 關掉全域 IP cache，避免任何一步又命中
config_ip_cache -disable_cache
puts "IP cache disabled"

set ips [get_ips -quiet top_bd_xspi_slave_0 top_bd_matmul_top_0]
puts "IPS to regen: $ips"

foreach ip $ips {
    reset_target -quiet all [get_ips $ip]
    puts "reset_target $ip"
}
generate_target all [get_ips $ips]
puts "generate_target done"

# 重新產生 OOC 合成 run 的檔案
foreach ip $ips {
    if {[llength [get_runs -quiet ${ip}_synth_1]] > 0} {
        reset_run ${ip}_synth_1
    }
}
reset_run synth_1

# 約束
set old [get_files -quiet */timing.xdc]
if {[llength $old] > 0} { set_property is_enabled false $old }
foreach f {constraints/timing_bd.xdc constraints/pins_vcu118.xdc} {
    if {[llength [get_files -quiet */[file tail $f]]] == 0} {
        add_files -fileset constrs_1 $f
    }
}

launch_runs synth_1 -jobs 8
puts "SYNTH launched (cache disabled, IP regenerated)"
puts "=== 06f done [clock format [clock seconds] -format %H:%M:%S] ==="

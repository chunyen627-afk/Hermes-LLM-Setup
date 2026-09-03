# _probe_vlnv.tcl — find exact VLNV + ddr4 clock param name (no guessing).
open_project vivado/sys_int/sys_int.xpr
set_property ip_repo_paths [file normalize ip_repo] [current_project]
update_ip_catalog -rebuild

puts "=== user IP ipdefs ==="
foreach id [get_ipdefs -quiet *xspi_slave*] { puts "  xspi NAME=[get_property NAME $id] VLNV=[get_property -quiet VLNV $id]" }
foreach id [get_ipdefs -quiet *matmul_top*] { puts "  mmul NAME=[get_property NAME $id] VLNV=[get_property -quiet VLNV $id]" }

puts "=== create_bd_cell candidate tests (xspi_slave) ==="
foreach cand {local:user:xspi_slave:1.0 xspi_slave} {
    if {[catch {create_bd_cell -type ip -vlnv $cand _probe_x} e]} { puts "  FAIL [$cand] :: [string range $e 0 90]" } else { puts "  OK   [$cand]"; delete_bd_obj [get_bd_cells _probe_x] }
}

puts "=== ddr4 cell CONFIG params matching clock/system ==="
set m [create_bd_cell -type ip -vlnv xilinx.com:ip:ddr4:2.2 _probe_mig]
foreach pn [get_properties $m -filter {NAME =~ CONFIG.*}] {
    if {[string match "*lock*" $pn] || [string match "*ystem*" $pn]} { puts "  PARAM $pn = [get_property $pn $m]" }
}
delete_bd_obj $m
puts "=== probe done ==="

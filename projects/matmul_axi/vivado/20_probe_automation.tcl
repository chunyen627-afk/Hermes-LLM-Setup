# probe only: what config does the ddr4 board automation accept? (rule 12: ask the tool)
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files */top_bd.bd]
puts "=== BOARD INTERFACES ==="
foreach i [get_board_part_interfaces] { puts "BIF $i" }
puts "=== RULES on mig_ddr4 ==="
if {[catch {foreach r [get_bd_automation_rules -of_objects [get_bd_cells mig_ddr4]] {
  puts "RULE $r"
  puts [report_property -return_string $r]
}} e]} { puts "NO_RULES_CMD $e" }
puts "=== intf pins on mig ==="
foreach p [get_bd_intf_pins mig_ddr4/*] { puts "MIGINTF $p vlnv=[get_property VLNV $p] mode=[get_property MODE $p]" }
foreach p [get_bd_pins mig_ddr4/*] { puts "MIGPIN $p dir=[get_property DIR $p] type=[get_property TYPE $p]" }
puts "=== HELP get_board_part_interfaces ==="
puts [help -args get_board_part_interfaces]
puts "=== HELP apply_bd_automation ==="
puts [help apply_bd_automation]
puts "=== HELP apply_board_connection ==="
puts [help apply_board_connection]
puts "=== MIG props of interest ==="
foreach p {CONFIG.C0_CLOCK_BOARD_INTERFACE CONFIG.C0_DDR4_BOARD_INTERFACE CONFIG.RESET_BOARD_INTERFACE CONFIG.C0.DDR4_TimePeriod CONFIG.C0.DDR4_InputClockPeriod CONFIG.C0.DDR4_AxiDataWidth CONFIG.C0.DDR4_AxiAddressWidth} {
  puts "MIGPROP $p = [get_property $p [get_bd_cells mig_ddr4]]"
}
puts "=== current external ports ==="
foreach p [get_bd_ports] { puts "PORT $p dir=[get_property DIR $p] type=[get_property TYPE $p]" }
foreach p [get_bd_intf_ports] { puts "IPORT $p vlnv=[get_property VLNV $p]" }
puts "=== old bd address segs ==="
foreach s [get_bd_addr_segs] { puts "SEG $s off=[get_property OFFSET $s] range=[get_property RANGE $s]" }
puts "PROBE_DONE"

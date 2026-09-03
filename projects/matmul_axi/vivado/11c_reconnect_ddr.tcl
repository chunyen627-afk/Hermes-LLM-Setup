puts "=== 11c reconnect ddr [clock format [clock seconds] -format %H:%M:%S] ==="
open_project vivado/sys_int/sys_int.xpr
open_bd_design [get_files -quiet */top_bd.bd]

# C0_DDR4 pin 目前沒有 external port（刪掉了）。用 make external 重建，
# 它會繼承 pin 的 board_interface 屬性（pin 本身來自 board preset 的 MIG）。
set pin [get_bd_intf_pins -quiet mig_ddr4/C0_DDR4]
puts "PIN_BOARDIF [get_property -quiet CONFIG.BOARD.ASSOCIATED_PARAM $pin][get_property -quiet CONFIG.BOARD_INTERFACE $pin]"
make_bd_intf_pins_external $pin
set newport [get_bd_intf_ports -quiet C0_DDR4_0]
puts "NEW_DDR4_PORT $newport board=[get_property -quiet CONFIG.BOARD_INTERFACE $newport]"

# 若還是沒 board 關聯，明確設（這次是在 make external 後、port 存在時設）
if {[get_property -quiet CONFIG.BOARD_INTERFACE $newport] eq ""} {
  set_property CONFIG.BOARD_INTERFACE ddr4_sdram_c1 $newport
  puts "forced board_if -> [get_property -quiet CONFIG.BOARD_INTERFACE $newport]"
}

if {[catch {validate_bd_design} e]} { puts "VALIDATE_FAIL [string range $e 0 200]" } else { puts "VALIDATE_OK" }
save_bd_design
puts "=== 11c done ==="

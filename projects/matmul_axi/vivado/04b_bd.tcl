# 04b_bd.tcl — Stage 5, round "rebuild block design with IP".
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 600 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/04b_bd.tcl
#
# WHY: the old bd was connected pin-by-pin (intf_nets=0) so Vivado could not trace a
# master->slave AXI path -> assign_bd_address failed -> validate failed. Plan C
# (CHANGELOG 08:20/10:35) packages xspi_slave + matmul_top as user IP with real AXI
# bus interfaces, so we rebuild the bd and connect them with connect_bd_intf_net.
#
# SCOPE this round (planner): rebuild top_bd using the two user IPs + the 5 Xilinx
# cells, wire clocks/reset (8 nets), connect the 4 AXI paths as INTERFACE nets,
# create the external ports (7) matching constraints/pins_vcu118.xdc, assign_bd_address.
# NO wrapper / set_property top (next round). NO rtl/*.v or ip_repo/ changes.
#
# ACCEPTANCE: cells=7 (no xlconstant), intf_nets>=4, ports=7, addressing=2,
# validate_bd_design zero ERROR.

puts "=== 04b_bd.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
puts "opened project part=[get_property PART [current_project]] board=[get_property BOARD_PART [current_project]]"

# --- Fresh block design ------------------------------------------------------
# Remove any registered .bd from a prior run so create is clean (same as 02_ip.tcl).
set stale_bds [get_files -quiet -filter {NAME =~ *.bd}]
if {[llength $stale_bds]} { remove_files $stale_bds; puts "removed registered bd: $stale_bds" }
# NOTE: create_bd_design takes NO -force (Common 17-170). remove_files above already
# unregisters the old top_bd so a plain create is clean. Guard against an in-memory
# design object that survived remove_files.
if {[llength [get_bd_designs -quiet top_bd]]} { delete_bd_obj [get_bd_designs top_bd] }
create_bd_design top_bd
current_bd_design top_bd
puts "created bd: [current_bd_design]"

# --- Step 1: make the project see the user IP repo --------------------------
set_property ip_repo_paths [file normalize ip_repo] [current_project]
update_ip_catalog -rebuild
# get_property NAME on an ipdef returns the bare component name, NOT the full VLNV.
# Probe confirmed (rule 12): use local:user:<name>:1.0 directly for create_bd_cell.
set xspi_vlnv "local:user:xspi_slave:1.0"
set mmul_vlnv "local:user:matmul_top:1.0"
puts "CATALOG_VLNVS xspi=$xspi_vlnv matmul=$mmul_vlnv (ipdefs found: [llength [get_ipdefs -quiet *xspi_slave*]] / [llength [get_ipdefs -quiet *matmul_top*]])"
if {[llength [get_ipdefs -quiet *xspi_slave*]] == 0 || [llength [get_ipdefs -quiet *matmul_top*]] == 0} { puts "FATAL: user IP not found in catalog"; exit 1 }

# --- Step 2: create all 7 cells ---------------------------------------------
# Xilinx cells (config from the validated 02_ip.tcl).
set c1 [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0]
set_property CONFIG.PRIM_IN_FREQ 300 $c1
set_property CONFIG.PRIM_SOURCE Differential_clock_capable_pin $c1
set_property CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100 $c1
set_property CONFIG.CLKOUT2_USED true $c1
set_property CONFIG.CLKOUT2_REQUESTED_OUT_FREQ 50 $c1

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_aclk
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_xspi

set s1 [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property CONFIG.NUM_SI 2 $s1
set_property CONFIG.NUM_MI 1 $s1

set m1 [create_bd_cell -type ip -vlnv xilinx.com:ip:ddr4:2.2 mig_ddr4]
# NOTE: dropped CONFIG.C0.System_Clock from the 02_ip.tcl dict — ddr4:2.2 has no such
# param (CRITICAL WARNING [BD 41-1276]). System clock type comes from the board preset.
set_property -dict [list \
    CONFIG.C0.DDR4_TimePeriod        {833} \
    CONFIG.C0.DDR4_MemoryPart        {MT40A256M16GE-083E} \
    CONFIG.C0.DDR4_InputClockPeriod  {4000} \
    CONFIG.C0.DDR4_AxiAddressWidth   {31} \
    CONFIG.C0.DDR4_DataWidth         {64} \
    CONFIG.C0.DDR4_AxiDataWidth      {512} \
    CONFIG.ADDN_UI_CLKOUT1_FREQ_HZ   {100} \
] $m1

# User IPs (the whole point of this round).
create_bd_cell -type ip -vlnv $xspi_vlnv xspi_slave
create_bd_cell -type ip -vlnv $mmul_vlnv matmul_top
puts "CELLS [llength [get_bd_cells]]"

# --- Step 3: clocks + reset (8 nets, logic from 03a_clocks.tcl) -------------
proc mkport {args} {
    set name [lindex $args end]
    if {[llength [get_bd_ports -quiet $name]] == 0} { create_bd_port {*}$args; puts "mkport created $name" } \
    else { puts "mkport reused $name" }
}

# Board sysclk (300 MHz differential) -> clk_wiz + MIG.
mkport -dir I -type clk -freq_hz 300000000 sysclk_p
mkport -dir I -type clk -freq_hz 300000000 sysclk_n
connect_bd_net [get_bd_ports sysclk_p] [get_bd_pins clk_wiz_0/clk_in1_p] [get_bd_pins mig_ddr4/c0_sys_clk_p]
connect_bd_net [get_bd_ports sysclk_n] [get_bd_pins clk_wiz_0/clk_in1_n] [get_bd_pins mig_ddr4/c0_sys_clk_n]

# Async system reset (active-low) -> clk_wiz async reset + both proc_sys_reset ext_reset_in.
mkport -dir I rst_n
connect_bd_net [get_bd_ports rst_n] \
    [get_bd_pins clk_wiz_0/reset] [get_bd_pins rst_aclk/ext_reset_in] [get_bd_pins rst_xspi/ext_reset_in]

# (1) aclk domain = MIG UI clock. The AXI fabric that reaches DDR (xspi_slave,
#     matmul_top, axi_smc) must share the MIG's C0_DDR4_S_AXI clock domain, i.e.
#     c0_ddr4_ui_clk. If the fabric ran on clk_wiz_0/clk_out1 (a separate 100 MHz
#     PLL output) while M00->MIG ran on c0_ddr4_ui_clk, validate would flag a
#     FREQ_HZ / CLK_DOMAIN mismatch at that boundary (BD 41-237). Driving the whole
#     fabric from the MIG UI clock is the standard MIG integration pattern and makes
#     every AXI hop in the design one coherent domain. clk_wiz_0/clk_out1 is now
#     unused by the fabric (clk_out2 already unused).
connect_bd_net [get_bd_pins mig_ddr4/c0_ddr4_ui_clk] \
    [get_bd_pins xspi_slave/aclk] [get_bd_pins matmul_top/aclk] \
    [get_bd_pins axi_smc/aclk] [get_bd_pins rst_aclk/slowest_sync_clk]

# (2) xspi_clk domain: the SCK arrives from the STM32 master on the xspi_clk pin, so
#     the whole xSPI-domain logic keys off that EXTERNAL clock (see 03d_ports.tcl).
mkport -dir I -type clk xspi_clk
connect_bd_net [get_bd_ports xspi_clk] \
    [get_bd_pins xspi_slave/xspi_clk] [get_bd_pins matmul_top/xspi_clk] [get_bd_pins rst_xspi/slowest_sync_clk]

# (3) Clocking-Wizard lock -> both reset instances' dcm_locked.
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins rst_aclk/dcm_locked] [get_bd_pins rst_xspi/dcm_locked]

# (4) Reset fan-out (active-low).
connect_bd_net [get_bd_pins rst_aclk/peripheral_aresetn] \
    [get_bd_pins xspi_slave/arst_n] [get_bd_pins matmul_top/aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins rst_xspi/peripheral_aresetn] [get_bd_pins matmul_top/xspi_rst_n]
puts "clocks+reset wired (clk_wiz_0/clk_out2 now unused; xspi_clk is external)"

# --- Step 4: AXI as INTERFACE nets (no pin-by-pin, no xlconstant) -----------
proc conn_intf {a b tag} {
    if {[catch {connect_bd_intf_net [get_bd_intf_pins $a] [get_bd_intf_pins $b]} e]} { puts "INTF_ERR $tag: $e" } \
    else { puts "intf ok $tag ($a -> $b)" }
}
conn_intf xspi_slave/m_ddr   axi_smc/S00_AXI            "m_ddr->S00"
conn_intf matmul_top/m_axi   axi_smc/S01_AXI            "m_axi->S01"
conn_intf axi_smc/M00_AXI    mig_ddr4/C0_DDR4_S_AXI     "M00->MIG"
conn_intf xspi_slave/m_reg   matmul_top/s_axi           "m_reg->s_axi"
puts "INTF_NETS [llength [get_bd_intf_nets -quiet]]"

# --- Step 5: address assignment ---------------------------------------------
# Bare assign first so Vivado auto-resolves the DDR segments (it lands them at
# 0x8000_0000, which is fine). Then FORCE the m_reg segment to the SPEC value:
# bare assign parks matmul_top/s_axi/reg0 at offset 0x0 with a 4G range, but the
# SPEC (SPEC_xspi_bridge.md) requires the register map at 0x9001_0000 / 64K.
# Without this force, AXI register transactions would go to the wrong address and
# xsim would fail. (Planner round: m_reg offset must be 0x90010000.)
if {[catch {assign_bd_address} e]} { puts "ADDR_ERR(bare): $e" } else { puts "assign_bd_address ok (bare)" }

# Force the three master address segments to their SPEC values.
set seg_reg  [get_bd_addr_segs -quiet matmul_top/s_axi/reg0]
set seg_ddr1 [get_bd_addr_segs -quiet mig_ddr4/C0_DDR4_MEMORY_MAP/C0_DDR4_ADDRESS_BLOCK]
if {[catch {assign_bd_address -offset 0x90010000 -range 64K -target_address_space /xspi_slave/m_reg $seg_reg -force} e]} { puts "ADDR_ERR(m_reg): $e" } else { puts "m_reg forced -> 0x90010000/64K" }
if {[catch {assign_bd_address -offset 0x80000000 -range 2G  -target_address_space /xspi_slave/m_ddr $seg_ddr1 -force} e]} { puts "ADDR_ERR(m_ddr): $e" } else { puts "m_ddr forced -> 0x80000000/2G" }
if {[catch {assign_bd_address -offset 0x80000000 -range 2G  -target_address_space /matmul_top/m_axi $seg_ddr1 -force} e]} { puts "ADDR_ERR(m_axi): $e" } else { puts "m_axi forced -> 0x80000000/2G" }

set segs [get_bd_addr_segs -quiet]
puts "SEG_COUNT [llength $segs]"
foreach s $segs { puts "  SEG $s off=[get_property -quiet OFFSET $s] range=[get_property -quiet RANGE $s]" }

# --- Step 6: external xSPI ports (names match constraints/pins_vcu118.xdc) --
mkport -dir I xspi_cs_n
mkport -dir I xspi_dqs
mkport -dir IO -from 7 -to 0 xspi_io
proc conn {port pin tag} {
    if {[catch {connect_bd_net [get_bd_ports $port] [get_bd_pins $pin]} e]} { puts "CONN_ERR $tag: $e" } \
    else { puts "conn ok $tag ($port -> $pin)" }
}
conn xspi_cs_n xspi_slave/xspi_cs_n "cs_n"
conn xspi_dqs  xspi_slave/xspi_dqs  "dqs"
conn xspi_io   xspi_slave/xspi_io   "xspi_io(inout bus)"

# --- Step 6b: externalize the MIG physical DDR4 interface -------------------
# C0_DDR4 is the physical DRAM pin group (ck_t/ck_c/dq/dqs/adr/ba/cs_n/...): it must
# be an EXTERNAL interface port so the board preset can apply its PACKAGE_PIN
# constraints to the real VCU118 DDR4 ball-out. Do NOT hand-fill PACKAGE_PIN — the
# board preset owns those. The system clock (c0_sys_clk_p/n) is already routed as
# scalar pins to the sysclk ports, so C0_SYS_CLK needs no externalizing here.
set c0ddr4 [get_bd_intf_pins -quiet mig_ddr4/C0_DDR4]
if {[llength $c0ddr4] > 0} {
    if {[catch {make_bd_intf_pins_external $c0ddr4} e]} { puts "EXT_ERR(C0_DDR4): $e" } \
    else { puts "externalized C0_DDR4 -> intf_port [get_property -quiet NAME [get_bd_intf_ports -quiet C0_DDR4]]" }
} else { puts "WARN: mig_ddr4/C0_DDR4 not found, nothing to externalize" }

# sys_rst is MIG's synchronous system-reset input (active-low). Tie it to the async
# system reset so it is driven; otherwise it dangles and could later flag in validate.
conn rst_n mig_ddr4/sys_rst "sys_rst<-rst_n"

save_bd_design
puts "saved bd"

# --- ACCEPTANCE --------------------------------------------------------------
puts "=== FINAL COUNTS ==="
puts "CELLS [llength [get_bd_cells]]"
foreach c [get_bd_cells] { puts "  CELL [get_property NAME $c] type=[get_property TYPE $c]" }
puts "NETS [llength [get_bd_nets -quiet]]"
puts "INTF_NETS [llength [get_bd_intf_nets -quiet]]"
puts "PORTS [llength [get_bd_ports -quiet]]"
foreach p [get_bd_ports -quiet] { puts "  PORT $p dir=[get_property -quiet DIR $p]" }
puts "INTF_PORTS [llength [get_bd_intf_ports -quiet]]"
foreach ip [get_bd_intf_ports -quiet] { puts "  INTFPORT $ip dir=[get_property -quiet DIRECTION $ip]" }
puts "ADDR_SEGS [llength [get_bd_addr_segs -quiet]]"

# validate: capture full report text so the log shows every message verbatim.
set vres [catch {validate_bd_design} verr]
puts "VALIDATE_RC $vres"
if {$vres != 0} { puts "VALIDATE_ERR: $verr" } else { puts "VALIDATE_CLEAN (rc=0)" }
puts "=== 04b_bd.tcl done ==="

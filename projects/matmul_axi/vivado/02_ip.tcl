# 02_ip.tcl — Stage 5, segment 2: add 4 Xilinx IP cells to top_bd.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/02_ip.tcl
#
# Scope of THIS segment (SPEC section 4, item 2): add IP ONE AT A TIME, run
# validate_bd_design after each so a failure points at the exact IP.
# NO connections yet (that is segment 3) -> unconnected-clock ERRORs in validate
# are EXPECTED here and are not treated as failures; parameter CRITICAL WARNINGs
# would be. NO wrapper / set_property top yet (segment 3).
#
# Board facts (from xhub vcu118/2.0 board.xml + preset.xml):
#   primary system clock = default_sysclk1_300, 300 MHz, DIFFERENTIAL
#   DDR4 board preset    = ddr4 IP, MT40A256M16GE-083E, DataWidth 64,
#                          AxiDataWidth 512, TimePeriod 833 ps

puts "=== 02_ip.tcl start ==="

# open_project (NOT create_project -force): a force-rebuild in the same batch
# session makes create_bd_design fail with [Common 17-217] Failed to load feature
# 'ipintegrator', whereas opening the existing project loads IP Integrator cleanly.
open_project vivado/sys_int/sys_int.xpr
puts "opened project: [current_project] part=[get_property PART [current_project]] board=[get_property BOARD_PART [current_project]]"

# Clear any stale block design(s) from a prior partial run so create is clean.
# `get_files -filter {NAME =~ *.bd}` returns only project-registered .bd source
# files (the IP-generated gen-dir sub-BDs are NOT project files, so they are not
# matched and no [filemgmt 20-1679] warning is emitted). remove_files unregisters
# them from the project DB — including a dangling reference to a file that was
# already deleted off-disk — so create_bd_design below succeeds on re-run.
set stale_bds [get_files -quiet -filter {NAME =~ *.bd}]
if {[llength $stale_bds]} {
    remove_files $stale_bds
    puts "removed registered bd: $stale_bds"
}

# Fresh block design for this segment.
create_bd_design "top_bd"
current_bd_design "top_bd"
puts "created bd: top_bd"

# Helper: validate and print a compact per-IP marker. The console log (grep
# ERROR / CRITICAL WARNING) is the authoritative record; this line marks progress
# and cell count. Pre-connection unconnected-clock errors are expected here.
proc ip_done {name} {
    puts "--- validating after $name ---"
    set ok [catch {validate_bd_design} vret]
    puts "VALIDATE_$name status=$ok cells=[llength [get_bd_cells]]"
}

# =====================================================================
# IP 1 — Clocking Wizard (clk_wiz:6.0)
#   in : board sysclk, 300 MHz differential
#   out: aclk = 100 MHz, xspi_clk = 50 MHz
#   NOTE: NUM_OUT_CLKS is a DERIVED parameter (computed from CLKOUTn_USED), so it
#         cannot be set directly. Enable the 2nd clock via CLKOUT2_USED=true, then
#         set its requested frequency.
# =====================================================================
puts "=== adding IP 1: clk_wiz ==="
set c1 [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0]
set_property CONFIG.PRIM_IN_FREQ 300 $c1
set_property CONFIG.PRIM_SOURCE Differential_clock_capable_pin $c1
set_property CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100 $c1
set_property CONFIG.CLKOUT2_USED true $c1
set_property CONFIG.CLKOUT2_REQUESTED_OUT_FREQ 50 $c1
# NOTE on output naming: an IP cell's port names (clk_out1/clk_out2) are managed by
# the IP and CANNOT be renamed with set_property NAME (Vivado reverts it). The top-
# level signals named aclk / xspi_clk come from create_bd_port + make_connections in
# segment 3 — so no rename is done here. The clock OUTPUTS are already 100/50 MHz via
# CLKOUT1/2_REQUESTED_OUT_FREQ above, which is what this segment needs.
puts "clk_wiz_0 NUM_OUT_CLKS=[get_property CONFIG.NUM_OUT_CLKS $c1] out1=[get_property CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $c1]MHz out2=[get_property CONFIG.CLKOUT2_REQUESTED_OUT_FREQ $c1]MHz"
ip_done clk_wiz

# =====================================================================
# IP 2 — Processor System Reset (proc_sys_reset:5.0)
#   one per clock domain -> two instances (aclk, xspi_clk).
#   ext_rst is left unconnected in this segment (wired in segment 3).
# =====================================================================
puts "=== adding IP 2: proc_sys_reset (x2) ==="
set r1 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_aclk]
puts "rst_aclk vlnv=[get_property CONFIG.VLNV $r1]"
set r2 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_xspi]
puts "rst_xspi vlnv=[get_property CONFIG.VLNV $r2]"
ip_done proc_sys_reset

# =====================================================================
# IP 3 — AXI SmartConnect (smartconnect:1.0)
#   2 slave interfaces, 1 master interface. Data/address widths are NOT config
#   params in this IP — they derive from the connected masters/slaves (segment 3),
#   so only NUM_SI / NUM_MI are set here. Width conversion is done by SC itself.
# =====================================================================
puts "=== adding IP 3: axi_smartconnect ==="
set s1 [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property CONFIG.NUM_SI 2 $s1
set_property CONFIG.NUM_MI 1 $s1
puts "axi_smc vlnv=[get_property CONFIG.VLNV $s1]"
ip_done smartconnect

# =====================================================================
# IP 4 — MIG / DDR4 SDRAM (ddr4:2.2)
#   Use VCU118 board preset values (do NOT hand-fill pins).
#   NOTE: apply ALL params in ONE set_property -dict call. Setting them one at a
#         time leaves intermediate invalid states (e.g. after MemoryPart but before
#         DataWidth=64 the memory map only allows 28-bit addressing, so
#         AxiAddressWidth=31 is rejected and MIG rolls back). A single dict does one
#         validation pass on the final config.
# =====================================================================
puts "=== adding IP 4: ddr4 (MIG) ==="
set m1 [create_bd_cell -type ip -vlnv xilinx.com:ip:ddr4:2.2 mig_ddr4]
set_property -dict [list \
    CONFIG.C0.DDR4_TimePeriod        {833} \
    CONFIG.C0.DDR4_MemoryPart        {MT40A256M16GE-083E} \
    CONFIG.C0.DDR4_InputClockPeriod  {4000} \
    CONFIG.C0.DDR4_AxiAddressWidth   {31} \
    CONFIG.System_Clock              {Differential} \
    CONFIG.C0.DDR4_DataWidth         {64} \
    CONFIG.C0.DDR4_AxiDataWidth      {512} \
    CONFIG.ADDN_UI_CLKOUT1_FREQ_HZ   {100} \
] $m1
puts "mig_ddr4 vlnv=[get_property CONFIG.VLNV $m1] mempart=[get_property CONFIG.C0.DDR4_MemoryPart $m1] axi_addrw=[get_property CONFIG.C0.DDR4_AxiAddressWidth $m1] axi_dataw=[get_property CONFIG.C0.DDR4_AxiDataWidth $m1]"
ip_done ddr4

# =====================================================================
# Final state: full cell list + VLNV (version is encoded in the VLNV).
# =====================================================================
puts "=== get_bd_cells (full) ==="
foreach c [get_bd_cells] {
    puts "CELL [get_property NAME $c] type=[get_property TYPE $c] vlnv=[get_property CONFIG.VLNV $c]"
}

# =====================================================================
# PERSIST. Critical: validate_bd_design writes only the *committed* (initial,
# empty) design state to top_bd.bd — it does NOT commit newly created cells.
# Without an explicit save_bd_design the .bd reverts to an empty design_tree on
# disk and segment 3 (open_project -> connect) finds nothing to work with.
# NOTE: there is no `save_project` command in Vivado (only save_project_as, which
# needs a name); save_bd_design alone is sufficient to persist the block design.
# =====================================================================
save_bd_design
puts "saved bd"
puts "=== 02_ip.tcl done ==="

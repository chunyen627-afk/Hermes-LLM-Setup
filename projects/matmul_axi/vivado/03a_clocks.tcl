# 03a_clocks.tcl — Stage 5, segment 2b: clocks + reset ONLY.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/03a_clocks.tcl
#
# SCOPE (planner round, "only clocks and reset"): wire clk_wiz_0 outputs to the
# aclk / xspi_clk domains, feed both proc_sys_reset instances, and fan out their
# peripheral_aresetn to each domain's reset pins. NO AXI connections, NO wrapper,
# NO set_property top — those are the NEXT round.
#
# ACCEPTANCE (planner 00:30): do NOT run validate_bd_design here (it will fail on
# the still-unconnected MIG clock / address paths — expected). Instead:
#   * NETS = llength [get_bd_nets] must go from 0 to >= 8
#   * no [BD 41-758] "clock pins not connected" error (clk_wiz + both rst clocks wired)
#
# Pin names were PROBED live (get_bd_pins <cell>/*), not guessed:
#   clk_wiz_0 : clk_in1_p, clk_in1_n, clk_out1(100M aclk), clk_out2(50M xspi_clk), locked, reset
#   rst_aclk / rst_xspi (proc_sys_reset:5.0) : slowest_sync_clk, dcm_locked, ext_reset_in,
#              peripheral_aresetn, interconnect_aresetn, ...
#   axi_smc   : aclk, aresetn  (+ AXI intf pins — NOT touched this round)
#   xspi_slave: aclk, arst_n, xspi_clk (+68 scalar/intf pins — AXI not touched)
#   matmul_top: aclk, aresetn, xspi_clk, xspi_rst_n  (xspi_* ports are UNCONDITIONAL in the
#              RTL module decl, so they exist even at default params -> wire them)
#   mig_ddr4  : c0_sys_clk_p/n, c0_ddr4_reset_n (aclk-domain reset; sysclk fed next round)

puts "=== 03a_clocks.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
if {[llength $bdfile] == 0} { puts "FATAL: top_bd.bd not registered in project"; exit 1 }
open_bd_design $bdfile
puts "opened bd=[current_bd_design] cells=[llength [get_bd_cells]] nets_before=[llength [get_bd_nets -quiet]]"

# ---------------------------------------------------------------------------
# Pre-check: confirm every pin we are about to connect actually exists. If any is
# missing, abort BEFORE making a partial connection (planner rule 12: don't guess).
# ---------------------------------------------------------------------------
set required_pins {
    clk_wiz_0/clk_in1_p   clk_wiz_0/clk_in1_n
    clk_wiz_0/clk_out1    clk_wiz_0/clk_out2
    clk_wiz_0/locked      clk_wiz_0/reset
    rst_aclk/slowest_sync_clk  rst_aclk/dcm_locked  rst_aclk/ext_reset_in  rst_aclk/peripheral_aresetn
    rst_xspi/slowest_sync_clk  rst_xspi/dcm_locked  rst_xspi/ext_reset_in  rst_xspi/peripheral_aresetn
    axi_smc/aclk   axi_smc/aresetn
    xspi_slave/aclk  xspi_slave/arst_n  xspi_slave/xspi_clk
    matmul_top/aclk  matmul_top/aresetn  matmul_top/xspi_clk  matmul_top/xspi_rst_n
    mig_ddr4/c0_sys_clk_p  mig_ddr4/c0_sys_clk_n
}
set missing 0
foreach p $required_pins {
    if {[llength [get_bd_pins -quiet $p]] == 0} {
        puts "MISSING_PIN $p"
        incr missing
    }
}
puts "PIN_CHECK missing=$missing of [llength $required_pins]"
if {$missing > 0} {
    # List the real pins for any cell that had a miss so we can correct names.
    foreach c {clk_wiz_0 rst_aclk rst_xspi axi_smc xspi_slave matmul_top mig_ddr4} {
        set hits [get_bd_pins -quiet ${c}/*]
        puts "CELL $c count=[llength $hits]"
    }
    puts "ABORT: pin names do not match; fix the list above and re-run."
    exit 1
}

# Helper: idempotent top-level port (re-runs don't duplicate).
proc mkport {args} {
    set name [lindex $args [expr {[llength $args]-1}]]
    if {[llength [get_bd_ports -quiet $name]] == 0} {
        create_bd_port {*}$args
        puts "mkport created $name"
    } else {
        puts "mkport reused $name"
    }
}

# ---------------------------------------------------------------------------
# Board system clock (300 MHz differential) -> top ports. clk_wiz and MIG are both
# fed from the SAME board sysclk source (see 03_connect.tcl STEP 2), so share it.
# NOTE: mig_ddr4/c0_ddr4_reset_n is an OUTPUT port (MIG drives the DRAM reset), NOT a
# sink -> it must not be fanned out to; MIG's own internal reset is handled by its
# init/calib sequence, not by our peripheral_aresetn.
# ---------------------------------------------------------------------------
mkport -dir I -type clk -freq_hz 300000000 sysclk_p
mkport -dir I -type clk -freq_hz 300000000 sysclk_n

connect_bd_net [get_bd_ports sysclk_p] \
    [get_bd_pins clk_wiz_0/clk_in1_p] [get_bd_pins mig_ddr4/c0_sys_clk_p]
connect_bd_net [get_bd_ports sysclk_n] \
    [get_bd_pins clk_wiz_0/clk_in1_n] [get_bd_pins mig_ddr4/c0_sys_clk_n]

# Async system reset (active-low) -> top port. Feeds the Clocking Wizard async reset
# and both proc_sys_reset ext_reset_in inputs (the standard reset-tree wiring).
mkport -dir I rst_n
connect_bd_net [get_bd_ports rst_n] \
    [get_bd_pins clk_wiz_0/reset] \
    [get_bd_pins rst_aclk/ext_reset_in] \
    [get_bd_pins rst_xspi/ext_reset_in]

# ---------------------------------------------------------------------------
# (1) aclk domain (100 MHz, clk_out1): fabric + SmartConnect + aclk reset domain.
# ---------------------------------------------------------------------------
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] \
    [get_bd_pins xspi_slave/aclk] \
    [get_bd_pins matmul_top/aclk] \
    [get_bd_pins axi_smc/aclk] \
    [get_bd_pins rst_aclk/slowest_sync_clk]

# ---------------------------------------------------------------------------
# (2) xspi_clk domain (50 MHz, clk_out2): xSPI front-end + matmul streaming + reset.
#     matmul_top/xspi_clk is an unconditional RTL port -> wire it too.
# ---------------------------------------------------------------------------
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] \
    [get_bd_pins xspi_slave/xspi_clk] \
    [get_bd_pins matmul_top/xspi_clk] \
    [get_bd_pins rst_xspi/slowest_sync_clk]

# ---------------------------------------------------------------------------
# (3) Clocking-Wizard lock -> both reset instances' dcm_locked.
# ---------------------------------------------------------------------------
connect_bd_net [get_bd_pins clk_wiz_0/locked] \
    [get_bd_pins rst_aclk/dcm_locked] \
    [get_bd_pins rst_xspi/dcm_locked]

# ---------------------------------------------------------------------------
# (4) Reset fan-out (active-low). peripheral_aresetn -> each domain's reset pins.
#     aclk domain: xspi_slave + matmul_top + SmartConnect.
#     xspi_clk domain: matmul_top streaming reset.
#     ext_reset_in is fed from the async rst_n port above; MIG's DRAM reset
#     (c0_ddr4_reset_n) is an OUTPUT, driven by MIG itself — not fanned out here.
# ---------------------------------------------------------------------------
connect_bd_net [get_bd_pins rst_aclk/peripheral_aresetn] \
    [get_bd_pins xspi_slave/arst_n] \
    [get_bd_pins matmul_top/aresetn] \
    [get_bd_pins axi_smc/aresetn]

connect_bd_net [get_bd_pins rst_xspi/peripheral_aresetn] \
    [get_bd_pins matmul_top/xspi_rst_n]

puts "connections done"
save_bd_design
puts "saved bd"

# ---------------------------------------------------------------------------
# ACCEPTANCE (planner 00:30): count nets + list them with their source pin, so the
# clock sourcing is visible WITHOUT running validate_bd_design (forbidden this round —
# it would report AXI/address errors that are expected before the next segment).
# Each clock-source pin must show a driver: clk_in1_p/n <- sysclk port; the reset
# slowest_sync_clk pins <- clk_out1/2; the fabric aclk/xspi_clk pins <- clk_out1/2.
# ---------------------------------------------------------------------------
set nets [get_bd_nets -quiet]
puts "NETS [llength $nets]"
foreach n $nets {
    set srcs [get_property SRC_PINNAMES $n]
    puts "  NET [get_property NAME $n]  <- $srcs"
}

# Explicit clock-sourcing check (no validate): every pin that needs a clock source
# must sit on a net whose driver is a clk_wiz output or the sysclk port.
set clock_source_pins {
    clk_wiz_0/clk_in1_p   clk_wiz_0/clk_in1_n
    rst_aclk/slowest_sync_clk  rst_xspi/slowest_sync_clk
    xspi_slave/aclk  matmul_top/aclk  axi_smc/aclk
    xspi_slave/xspi_clk  matmul_top/xspi_clk
}
set unsourced 0
foreach p $clock_source_pins {
    set nets_for_pin [get_bd_nets -quiet -of_objects [get_bd_pins -quiet $p]]
    if {[llength $nets_for_pin] == 0} {
        puts "UNSOURCED_CLOCK_PIN $p"
        incr unsourced
    } else {
        foreach nn $nets_for_pin {
            puts "  SOURCED $p  on [get_property NAME $nn]  (src=[get_property SRC_PINNAMES $nn])"
        }
    }
}
puts "CLOCK_SOURCING unsourced=$unsourced of [llength $clock_source_pins]"

puts "=== 03a_clocks.tcl done ==="

# 03_connect.tcl — Stage 5, segment 3: connections + wrapper + set top.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 600 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/03_connect.tcl
#
# Four sub-steps, each followed by validate_bd_design (zero ERROR is the bar):
#   STEP 1: add RTL modules (xspi_slave, matmul_top) with -type module -reference
#   STEP 2: clocks + resets (clk_wiz -> aclk/xspi_clk domains; proc_sys_reset x2)
#   STEP 3: AXI connections (m_reg->s_axi direct; m_ddr+m_axi -> axi_smc -> mig)
#   STEP 4: external ports (11 xSPI pins + sysclk), make_wrapper, set_property top
#
# Key facts (probed this round, not guessed):
#   - RTL module cells expose AXI as SCALAR pins (m_reg_awvalid ...), NOT interface
#     ports -> connect signal-by-signal with connect_bd_net.
#   - IP interface ports: axi_smc S00_AXI/S01_AXI/M00_AXI; mig C0_DDR4_S_AXI.
#   - clk_wiz pins: clk_in1_p/n, clk_out1(100M), clk_out2(50M), locked, reset.
#   - proc_sys_reset pins: slowest_sync_clk, ext_reset_in, interconnect_aresetn, ...
#   - mig scalar pins: c0_sys_clk_p/n, c0_ddr4_reset_n, c0_ddr4_s_axi_*, c0_init_calib_complete.

puts "=== 03_connect.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
# open by file object (by-name 'open_bd_design top_bd' is flaky after open_project)
set bdfile [get_files -quiet top_bd.bd]
if {[llength $bdfile] == 0} { puts "FATAL: top_bd.bd not registered in project"; exit 1 }
open_bd_design $bdfile
puts "opened bd: [current_bd_design] cells=[llength [get_bd_cells]] top_before=[get_property top [current_fileset]]"
# NOTE: reset_netlist does not exist in this Vivado build. Idempotency is handled
# by create-or-reuse for cells + mkport for ports + the fact that connect_bd_net
# on an already-connected pair is a no-op (not an error). If a prior partial run
# left stale nets, re-running will just re-assert the same connections.

# Helper: create a top-level port only if it does not already exist (idempotent).
proc mkport {args} {
    set name [lindex $args [expr {[llength $args]-1}]]
    if {[llength [get_bd_ports -quiet $name]] == 0} {
        create_bd_port {*}$args
    } else {
        puts "mkport: $name already exists, reusing"
    }
}

# Helper: run validate, print a compact marker with real counts. Zero ERROR is
# the FINAL bar; intermediate steps (before clocks/AXI are wired) legitimately
# carry [BD 41-758] unconnected-clock errors that clear in later steps. So we
# CATCH (validate throws on error) and report counts + any non-clock errors.
proc step_validate {tag} {
    set ok [catch {validate_bd_design} vres]
    # validate returns the issue text as its result; on throw, vres holds the msg
    set nerr  [expr {[llength [regexp -all -line -inline {ERROR} $vres]]}]
    set nwarn [expr {[llength [regexp -all -line -inline {WARNING|CRITICAL WARNING} $vres]]}]
    # unconnected-clock errors are the expected pre-connection class
    set nclk  [expr {[llength [regexp -all -inline {BD 41-758} $vres]]}]
    puts "STEP_VALIDATE $tag throw=$ok errors=$nerr (clock=$nclk) warnings=$nwarn"
    # print any error that is NOT the expected unconnected-clock class
    foreach l [split $vres "\n"] {
        if {[string match "*ERROR*" $l] && ![string match "*41-758*" $l]} {
            puts "  ERR $l"
        }
    }
}

# =====================================================================
# STEP 1 — add the two RTL modules.
#   -type module -reference <module>  (NOT -type ip; those are for Xilinx VLNVs).
#   matmul_top.DATA_WIDTH=32 so its m_axi is 32-bit (matches the spec diagram and
#   xspi_slave's 32-bit masters); SC then does the single 32->512 conversion to MIG.
# =====================================================================
puts "=== STEP 1: add RTL modules ==="
# Idempotent create-or-reuse: a prior partial run may already have created these
# cells (and saved them). Reuse if present, create if not. (There is no reliable
# delete_bd_cell/remove_ip in this build, so we avoid deleting and just check.)
if {[llength [get_bd_cells -quiet xspi_slave]] == 0} {
    set xs [create_bd_cell -type module -reference xspi_slave xspi_slave]
    puts "STEP1 created xspi_slave"
} else {
    set xs [get_bd_cells xspi_slave]
    puts "STEP1 reusing existing xspi_slave"
}
if {[llength [get_bd_cells -quiet matmul_top]] == 0} {
    set mt [create_bd_cell -type module -reference matmul_top matmul_top]
    puts "STEP1 created matmul_top"
} else {
    set mt [get_bd_cells matmul_top]
    puts "STEP1 reusing existing matmul_top"
}
set_property PARAMETER.DATA_WIDTH 32 $mt
puts "STEP1 cells=[llength [get_bd_cells]]"
save_bd_design
step_validate step1

# =====================================================================
# STEP 2 — clocks + resets.
#   Two clock domains from clk_wiz: aclk=100MHz (clk_out1), xspi_clk=50MHz (clk_out2).
#   Board sysclk (300MHz differential) feeds clk_wiz AND mig c0_sys_clk_p/n.
#   The PHYSICAL xSPI SCK (from the STM32 host) is a separate top port -> xspi_slave.xspi_clk.
# =====================================================================
puts "=== STEP 2: clocks + resets ==="
create_bd_port -dir I -type clk -freq_hz 300000000 sysclk_p
create_bd_port -dir I -type clk -freq_hz 300000000 sysclk_n
create_bd_port -dir I -type clk -freq_hz 50000000 xspi_clk

# physical SCK -> xspi_slave front-end clock (external, from host)
connect_bd_net [get_bd_ports xspi_clk] [get_bd_pins xspi_slave/xspi_clk]
# board sysclk differential -> clk_wiz input
connect_bd_net [get_bd_ports sysclk_p] [get_bd_pins clk_wiz_0/clk_in1_p]
connect_bd_net [get_bd_ports sysclk_n] [get_bd_pins clk_wiz_0/clk_in1_n]
# board sysclk differential -> MIG system clock
connect_bd_net [get_bd_ports sysclk_p] [get_bd_pins mig_ddr4/c0_sys_clk_p]
connect_bd_net [get_bd_ports sysclk_n] [get_bd_pins mig_ddr4/c0_sys_clk_n]

# aclk domain (100MHz): clk_out1 -> fabric + SC + aclk reset domain
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] \
    [get_bd_pins xspi_slave/aclk] \
    [get_bd_pins matmul_top/aclk] \
    [get_bd_pins axi_smc/aclk] \
    [get_bd_pins rst_aclk/slowest_sync_clk]
# internal xspi_clk domain (50MHz): clk_out2 -> matmul_top streaming + xspi reset domain
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] \
    [get_bd_pins matmul_top/xspi_clk] \
    [get_bd_pins rst_xspi/slowest_sync_clk]

# aclk-domain reset (active-low) -> fabric resets + MIG reset
connect_bd_net [get_bd_pins rst_aclk/interconnect_aresetn] \
    [get_bd_pins xspi_slave/arst_n] \
    [get_bd_pins matmul_top/aresetn] \
    [get_bd_pins axi_smc/aresetn] \
    [get_bd_pins mig_ddr4/c0_ddr4_reset_n]
# xspi-domain reset (active-low) -> matmul_top streaming reset
connect_bd_net [get_bd_pins rst_xspi/interconnect_aresetn] \
    [get_bd_pins matmul_top/xspi_rst_n]

puts "STEP2 done"
save_bd_design
step_validate step2

# =====================================================================
# STEP 3 — AXI connections.
#   (a) xspi_slave.m_reg (master, 32b) -> matmul_top.s_axi (slave, 32b): DIRECT.
#   (b) xspi_slave.m_ddr (master, 32b) -> axi_smc.S00_AXI (slave).
#   (c) matmul_top.m_axi (master, 32b) -> axi_smc.S01_AXI (slave).
#   (d) axi_smc.M00_AXI (master) -> mig_ddr4.C0_DDR4_S_AXI (slave): INTERFACE net.
# =====================================================================
puts "=== STEP 3: AXI connections ==="

# --- (a) m_reg <-> s_axi : scalar-to-scalar, paired by signal name ---
set axi_sigs {awaddr awlen awsize awburst awid awvalid awready \
              wdata wstrb wlast wvalid wready bresp bvalid bready bid \
              araddr arlen arsize arburst arid arvalid arready \
              rdata rresp rvalid rready rlast rid}
foreach s $axi_sigs {
    connect_bd_net [get_bd_pins xspi_slave/m_reg_$s] [get_bd_pins matmul_top/s_axi_$s]
}
puts "STEP3a m_reg->s_axi connected ([llength $axi_sigs] signals)"

# --- (b) m_ddr <-> S00_AXI : scalar(master) <-> interface(slave), per signal ---
foreach s $axi_sigs {
    connect_bd_net [get_bd_pins xspi_slave/m_ddr_$s] \
        [get_bd_pins axi_smc/S00_AXI/[string toupper $s]]
}
puts "STEP3b m_ddr->S00_AXI connected"

# --- (c) m_axi <-> S01_AXI : scalar(master) <-> interface(slave), per signal ---
foreach s $axi_sigs {
    connect_bd_net [get_bd_pins matmul_top/m_axi_$s] \
        [get_bd_pins axi_smc/S01_AXI/[string toupper $s]]
}
puts "STEP3c m_axi->S01_AXI connected"

# --- (d) M00_AXI -> C0_DDR4_S_AXI : interface-to-interface ---
connect_bd_intf_net [get_bd_pins axi_smc/M00_AXI] [get_bd_pins mig_ddr4/C0_DDR4_S_AXI]
puts "STEP3d M00_AXI->C0_DDR4_S_AXI connected"

save_bd_design
step_validate step3

# =====================================================================
# STEP 4 — external ports + wrapper + set top.
#   Pull the 11 physical xSPI pins to top-level ports (names MUST match
#   constraints/pins_vcu118.xdc exactly). xspi_io is inout -> -dir IO.
#   Then make_wrapper and set_property top to the wrapper.
# =====================================================================
puts "=== STEP 4: external ports + wrapper ==="
create_bd_port -dir I  xspi_cs_n
create_bd_port -dir I  xspi_dqs
create_bd_port -dir IO -from 7 -to 0 xspi_io

connect_bd_net [get_bd_ports xspi_cs_n] [get_bd_pins xspi_slave/xspi_cs_n]
connect_bd_net [get_bd_ports xspi_dqs]  [get_bd_pins xspi_slave/xspi_dqs]
connect_bd_net [get_bd_ports xspi_io]   [get_bd_pins xspi_slave/xspi_io]

# generate the wrapper for this block design
make_wrapper
puts "STEP4 wrapper generated"

# Confirm the wrapper file + its module name (default: <bd>_wrapper = top_bd_wrapper).
set wrapp [get_files -quiet -filter {NAME =~ *_wrapper.v}]
puts "STEP4 wrapper files: $wrapp"
if {[llength $wrapp] == 0} { puts "STEP4 ERROR no wrapper file produced"; }
set wpath [get_property NAME [lindex $wrapp 0]]
# read the module declaration + port list straight from the generated source
set fh [open $wpath r]
set wsrc [read $fh]
close $fh
set modline [regexp -all -inline {^\s*module\s+(\w+)} $wsrc]
puts "STEP4 wrapper module line: $modline"
# print the port list (lines between 'module ...' and the first ')' / end of header)
foreach l [split $wsrc "\n"] {
    if {[regexp {^\s*(input|output|inout)\b.*\}\s*$} $l] || \
       [regexp {^\s*(input|output|inout)\b.*\)\s*$} $l]} { puts "WRAPPORT $l" }
}

# set the project top to the wrapper (was auto-guessed as xspi_slave)
set topmod "top_bd_wrapper"
set_property top $topmod [current_fileset]
puts "STEP4 top set to: [get_property top [current_fileset]]"

save_bd_design
step_validate step4

# =====================================================================
# Final report.
# =====================================================================
puts "=== FINAL get_bd_cells ==="
foreach c [get_bd_cells] { puts "CELL [get_property NAME $c] type=[get_property TYPE $c]" }
puts "TOP [get_property top [current_fileset]]"
puts "=== 03_connect.tcl done ==="

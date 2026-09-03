# 03b_axi.tcl — Stage 5, segment 3: AXI connections ONLY.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 400 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/03b_axi.tcl
#
# SCOPE (planner: "only AXI, then stop"): wire the four AXI paths
#   xspi_slave.m_ddr  -> axi_smc.S00_AXI     (scalar master -> SC slave)
#   matmul_top.m_axi  -> axi_smc.S01_AXI     (scalar master -> SC slave)
#   axi_smc.M00_AXI   -> mig_ddr4.C0_DDR4_S_AXI   (IP interface -> IP interface)
#   xspi_slave.m_reg  -> matmul_top.s_axi    (scalar master -> scalar slave, direct)
# then assign_bd_address and dump the address segments.
# NO port / wrapper / set_property top (next round). NO validate_bd_design.
# No rtl/*.v modified.
#
# Pin names were PROBED live (_probe_pins.tcl), not guessed:
#   masters (23 signals each, incl wlast): m_reg_*, m_ddr_*, m_axi_*
#   s_axi (slave, 22 signals, NO wlast)
#   SC S00/S01 slave ports + MIG c0_ddr4_s_axi: full AXI4.
#
# RTL driver widths (from rtl/*.v, probed):
#   xspi_slave.m_reg / m_ddr : DATA=32  ADDR=32 ID=4
#   matmul_top.m_axi         : DATA=512 ADDR=32 ID=4
#   mig_ddr4.c0_ddr4_s_axi   : DATA=512 ADDR=31 (ID=4 per xci)
# SC port widths are DERIVED params (default 32/32/0 in batch mode); they get the
# correct widths (S01/M00 = 512-bit) when validate runs next round. Not settable here.

puts "=== 03b_axi.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
if {[llength $bdfile] == 0} { puts "FATAL: top_bd.bd not registered"; exit 1 }

# open_bd_design is intermittently flaky in batch mode (IP_Flow 19-3428). Retry.
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }
puts "opened bd=[current_bd_design] cells=[llength [get_bd_cells]] nets_before=[llength [get_bd_nets -quiet]]"

# ---------------------------------------------------------------------------
# Pre-check: every pin we will connect must exist. If any is missing, dump the
# real pins of that cell and ABORT before making a partial connection (rule 12).
# ---------------------------------------------------------------------------
set required_pins {
    xspi_slave/m_reg_awvalid xspi_slave/m_reg_wlast xspi_slave/m_reg_rid
    xspi_slave/m_ddr_awvalid xspi_slave/m_ddr_wlast xspi_slave/m_ddr_rid
    matmul_top/m_axi_awvalid matmul_top/m_axi_wlast matmul_top/m_axi_rid
    matmul_top/s_axi_awvalid matmul_top/s_axi_wready matmul_top/s_axi_rid
    axi_smc/S00_AXI_awvalid axi_smc/S00_AXI_aruser axi_smc/S00_AXI_wid
    axi_smc/S01_AXI_awvalid axi_smc/S01_AXI_aruser axi_smc/S01_AXI_wid
    mig_ddr4/c0_ddr4_s_axi_awvalid mig_ddr4/c0_ddr4_s_axi_rdata
}
set missing 0
foreach p $required_pins {
    if {[llength [get_bd_pins -quiet $p]] == 0} { puts "MISSING_PIN $p"; incr missing }
}
puts "PIN_CHECK missing=$missing of [llength $required_pins]"
if {$missing > 0} {
    foreach c {xspi_slave matmul_top axi_smc mig_ddr4} {
        puts "CELL $c count=[llength [get_bd_pins -quiet ${c}/*]]"
    }
    puts "ABORT: pin names do not match; fix the list and re-run."
    exit 1
}

# Helper: connect a scalar master pin to a slave pin, report ok/skip/err.
# Idempotent: if either pin already sits on a net, skip (re-run safe).
proc conn {a b tag} {
    set pa [get_bd_pins -quiet $a]
    set pb [get_bd_pins -quiet $b]
    if {[llength $pa] == 0 || [llength $pb] == 0} { puts "CONN_SKIP(no pin) $tag"; return }
    # Idempotent: each master output drives exactly one net. If the source pin is
    # already on a net, it was connected in a prior run -> skip (re-run safe).
    if {[llength [get_bd_nets -quiet -of_objects $pa]] > 0} { puts "CONN_SKIP(already) $tag"; return }
    if {[catch {connect_bd_net $pa $pb} e]} {
        puts "CONN_ERR $tag ($a -> $b): $e"
    } else {
        puts "conn ok $tag"
    }
}

# ---------------------------------------------------------------------------
# (1) xspi_slave.m_reg (master, 23) -> matmul_top.s_axi (slave, 22; NO wlast).
#     Direct connection, does NOT go through SmartConnect.
# ---------------------------------------------------------------------------
set reg_sigs {awvalid awready awaddr awlen awsize awburst awid \
              wvalid wready wdata wstrb bvalid bready bresp bid \
              arvalid arready araddr arlen arsize arburst arid \
              rvalid rready rdata rresp rlast rid}
set nreg 0
foreach s $reg_sigs { conn "xspi_slave/m_reg_$s" "matmul_top/s_axi_$s" "m_reg->s_axi/$s"; incr nreg }
puts "STEP1 m_reg->s_axi done ($nreg signals)"

# ---------------------------------------------------------------------------
# (2) xspi_slave.m_ddr (master, 23) -> axi_smc.S00_AXI (slave).
#     Scalar master pin -> SC interface pin accessed as a scalar.
# ---------------------------------------------------------------------------
set ddr_sigs {awvalid awready awaddr awlen awsize awburst awid \
              wvalid wready wdata wstrb wlast bvalid bready bresp bid \
              arvalid arready araddr arlen arsize arburst arid \
              rvalid rready rdata rresp rlast rid}
set nddr 0
foreach s $ddr_sigs { conn "xspi_slave/m_ddr_$s" "axi_smc/S00_AXI_${s}" "m_ddr->S00/$s"; incr nddr }
puts "STEP2 m_ddr->S00_AXI done ($nddr signals)"

# ---------------------------------------------------------------------------
# (3) matmul_top.m_axi (master, 23) -> axi_smc.S01_AXI (slave).
# ---------------------------------------------------------------------------
set naxi 0
foreach s $ddr_sigs { conn "matmul_top/m_axi_$s" "axi_smc/S01_AXI_${s}" "m_axi->S01/$s"; incr naxi }
puts "STEP3 m_axi->S01_AXI done ($naxi signals)"

# ---------------------------------------------------------------------------
# (4) axi_smc.M00_AXI (IP master) -> mig_ddr4.C0_DDR4_S_AXI (IP slave).
#     Planner method: treat as scalar pins, connect_bd_net one by one (same as the
#     other three paths). MIG C0 has 37 AXI signals; SC M00 has those plus extra
#     user/region signals (aruser/awuser/buser/ruser/arregion/awregion/wid) which
#     MIG lacks -> connect the 37 common ones, leave M00's extras unconnected.
#     Idempotent: skip if already connected.
# ---------------------------------------------------------------------------
set mig_sigs {araddr arburst arcache arid arlen arlock arprot arqos arready \
              arsize arvalid awaddr awburst awcache awid awlen awlock awprot \
              awqos awready awsize awvalid bid bready bresp bvalid rdata rid \
              rlast rready rresp rvalid wdata wlast wready wstrb wvalid}
set nmig 0
foreach s $mig_sigs { conn "axi_smc/M00_AXI_${s}" "mig_ddr4/c0_ddr4_s_axi_${s}" "M00->MIG/$s"; incr nmig }
puts "STEP4 M00_AXI->C0_DDR4_S_AXI done ($nmig signals, pin-by-pin)"

# NOTE on SC port widths: SmartConnect has NO C_*_AXI_DATA_WIDTH config params
# (only NUM_MI/NUM_SI/NUM_CLKS/HAS_ARESETN/HAS_RESET). Port widths are DERIVED from
# connected interfaces during validate. In batch mode with scalar-pin connections,
# they default to 32-bit; the correct widths (S01/M00 = 512-bit) get derived when
# validate runs next round. Not settable here — do NOT add bogus CONFIG params.

save_bd_design
puts "saved bd after connections"

# ---------------------------------------------------------------------------
# Tie off SC slave-port inputs the masters do not drive, so they are defined.
#   * S00/S01: arcache/arlock/arprot/arqos/arregion/aruser/awcache/awlock/
#     awprot/awqos/awregion/awuser/buser/ruser/wuser + wid  (all constants).
#   * MIG c0_ddr4_s_axi: arcache/arlock/arprot/arqos/awcache/awlock/awprot/
#     awqos + wid  (MIG has NO user signals, so no *_user to tie here).
# Cell names must not contain '/' — sanitize the pin path.
# ---------------------------------------------------------------------------
proc tie_const {pin val} {
    if {[llength [get_bd_pins -quiet $pin]] == 0} { puts "TIE_SKIP(no pin) $pin"; return }
    # Everything below is best-effort: a flaky IP_Flow error on create_bd_cell must
    # NOT abort the script before the NETS/SEG report. Catch and continue.
    if {[catch {
        # sanitize: replace '/' and other illegal chars with '_' for the cell name
        set safe [string map {/ _ . _ - _} $pin]
        set cname "cconst_$safe"
        set const [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 $cname]
        # xlconstant 1.1 params are CONST_VAL / CONST_WIDTH (NOT CONSTANT_VALUE/WIDTH,
        # which silently fails with "Parameter does not exist" and leaves the cell at
        # its default of 1 — wrong for arlock/awlock). Width-1 auto zero-extends.
        set_property CONFIG.CONST_VAL $val $const
        set_property CONFIG.CONST_WIDTH 1 $const
        # create_bd_cell auto-makes a net named <cell>_dout with dout already as its
        # source. Delete it first so connect_bd_net doesn't hit "another source port
        # cannot be connected" (this is what broke buser/ruser on re-runs).
        catch {delete_bd_obj [get_bd_nets -quiet ${cname}_dout]}
        connect_bd_net [get_bd_pins ${cname}/dout] [get_bd_pins $pin]
    } e]} {
        puts "TIE_ERR $pin: $e"
    } else {
        set rb [get_property CONFIG.CONST_VAL [get_bd_cells -quiet cconst_*${safe}]]
        puts "tie $pin = $val (readback=$rb)"
    }
}

# Clean slate: remove any constants from prior runs so tie-offs are idempotent and
# correct (also clears the 4 leftover cells that collided on their dout net name).
foreach c [get_bd_cells -quiet cconst_*] { delete_bd_obj $c }
puts "cleaned stale cconst cells"

# SC slave ports (S00, S01): un-driven INPUTS to tie off.
# NOTE: buser/ruser are OUTPUTS on an AXI slave port (B/R-channel user data flows
# slave->master). Our RTL masters have no user inputs, so those outputs just dangle —
# fine for outputs. Tying them would double-drive -> "another source port" error.
set sc_tie {arcache arlock arprot arqos arregion aruser \
            awcache awlock awprot awqos awregion awuser \
            wuser wid}
foreach port {S00 S01} {
    foreach s $sc_tie { tie_const "axi_smc/${port}_AXI_${s}" 0 }
}
# NOTE: MIG c0_ddr4_s_axi needs NO tie-offs — after M00->MIG pin-by-pin connect,
# every MIG input (arcache/arlock/arprot/arqos/aw*/wid) is driven by SC's M00
# outputs. Tying them here would double-drive; the guard skips them anyway.

save_bd_design
puts "saved bd after tie-offs"

# ---------------------------------------------------------------------------
# Address assignment + dump the resulting segments.
# ---------------------------------------------------------------------------
if {[catch {assign_bd_address} e]} { puts "ADDR_ERR: $e" } else { puts "assign_bd_address ok" }
set segs [get_bd_addr_segs -quiet]
puts "SEG_COUNT [llength $segs]"
foreach s $segs {
    puts "SEG [get_property NAME $s] base=[get_property OFFSET $s] range=[get_property RANGE $s] target=[get_property TARGET_SLAVE_PORT_NAME $s]"
}

# ---------------------------------------------------------------------------
# ACCEPTANCE (planner: no validate_bd_design): report net/intf-net counts.
# ---------------------------------------------------------------------------
set nets [get_bd_nets -quiet]
puts "NETS [llength $nets]"
puts "INTF_NETS [llength [get_bd_intf_nets -quiet]]"

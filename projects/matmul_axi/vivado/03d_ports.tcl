# 03d_ports.tcl — Stage 5, FINAL step: external ports + wrapper + set top.
# Run from the matmul_axi repo root:
#   cd /c/Users/pjunm/matmul_axi
#   timeout 400 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/03d_ports.tcl
#
# SCOPE (planner: "last small step — external port + wrapper + set top"):
#   1. Pull out the xSPI physical interface as top-level ports whose names match
#      constraints/pins_vcu118.xdc EXACTLY:
#          xspi_clk   (input,  type clk)
#          xspi_cs_n  (input)
#          xspi_dqs   (input)
#          xspi_io[7:0] (inout bus, -dir IO)
#      and connect each to the matching xspi_slave pin.
#   2. make_wrapper -top -import, then set_property top <wrapper>.
#   3. Run validate_bd_design ONCE (step 4 of planner's acceptance table).
#
# CLOCK DECISION (best judgement — user did not respond to the clarify):
#   xspi_clk was previously driven by clk_wiz_0/clk_out2 (a 50 MHz generated
#   clock) on a net shared with matmul_top/xspi_clk and rst_xspi/slowest_sync_clk.
#   Physically the SCK arrives from the STM32 master on the xspi_clk pin, so all
#   xSPI-domain logic must key off that EXTERNAL clock. We therefore:
#     - create xspi_clk as an input port (type clk),
#     - connect it to xspi_slave/xspi_clk + matmul_top/xspi_clk + rst_xspi/slowest_sync_clk,
#       which pulls those three sinks OFF the clk_wiz_0/clk_out2 net.
#   clk_wiz_0/clk_out2 becomes an unused output (pruned at synthesis; a WARNING, not ERROR).
#   This is the physically-correct choice and matches the pin file (xspi_clk is a board input).
#
# DO NOT: touch tie-off/xlconstant, address assignment, or AXI connections (all passed in 03b/03c).
# No rtl/*.v modified. Idempotent enough to re-run (ports created with -quiet guards).

puts "=== 03d_ports.tcl start ==="
open_project vivado/sys_int/sys_int.xpr
set bdfile [get_files -quiet top_bd.bd]
if {[llength $bdfile] == 0} { puts "FATAL: top_bd.bd not registered"; exit 1 }

# open_bd_design is intermittently flaky in batch mode (IP_Flow 19-3428). Retry.
set opened 0
for {set i 1} {$i <= 5} {incr i} {
    if {[catch {open_bd_design $bdfile} e]} { puts "OPEN_RETRY $i"; exec sleep 2 } else { set opened 1; break }
}
if {!$opened} { puts "FATAL could not open bd"; exit 1 }
puts "opened bd=[current_bd_design] cells=[llength [get_bd_cells]]"

# ---------------------------------------------------------------------------
# (1) Create the xSPI top-level ports (names must match pins_vcu118.xdc).
#     Guard with -quiet so a re-run does not fatal on an existing port.
# ---------------------------------------------------------------------------
proc mkport {args} {
    # create_bd_port unless it already exists
    set name [lindex $args end]
    if {[llength [get_bd_ports -quiet $name]] == 0} {
        if {[catch {create_bd_port {*}$args} e]} { puts "PORT_ERR $name: $e" } \
          else { puts "PORT_OK $name" }
    } else {
        puts "PORT_EXISTS $name"
    }
}

# xspi_clk: input clock (SCK from master). type clk so proc_sys_reset + validate accept it.
mkport -dir I -type clk xspi_clk
mkport -dir I xspi_cs_n
mkport -dir I xspi_dqs
mkport -dir IO -from 7 -to 0 xspi_io

# ---------------------------------------------------------------------------
# (2) Connect ports to xspi_slave pins.
#     For xspi_clk, one connect_bd_net call puts the port + all three xSPI-domain
#     sinks on a single net named after the port, pulling them off clk_wiz_0/clk_out2.
# ---------------------------------------------------------------------------
proc conn {port pin tag} {
    if {[catch {connect_bd_net [get_bd_ports $port] [get_bd_pins $pin]} e]} {
        puts "CONN_ERR $tag: $e"
    } else {
        puts "CONN_OK $tag port=$port pin=$pin"
    }
}

# xspi_clk net: port + 3 sinks (xspi_slave, matmul_top, rst_xspi) in one call.
if {[catch {connect_bd_net [get_bd_ports xspi_clk] \
        [get_bd_pins xspi_slave/xspi_clk] \
        [get_bd_pins matmul_top/xspi_clk] \
        [get_bd_pins rst_xspi/slowest_sync_clk]} e]} {
    puts "CONN_ERR xspi_clk-net: $e"
} else {
    puts "CONN_OK xspi_clk-net -> {xspi_slave/xspi_clk matmul_top/xspi_clk rst_xspi/slowest_sync_clk}"
}

conn xspi_cs_n xspi_slave/xspi_cs_n "cs_n"
conn xspi_dqs  xspi_slave/xspi_dqs  "dqs"
# xspi_io is an inout bus: connect the whole bus port to the whole bus pin.
if {[catch {connect_bd_net [get_bd_ports xspi_io] [get_bd_pins xspi_slave/xspi_io]} e]} {
    puts "CONN_ERR xspi_io: $e"
} else { puts "CONN_OK xspi_io -> xspi_slave/xspi_io" }

save_bd_design
puts "saved bd after ports"

# ---------------------------------------------------------------------------
# (3) Generate the wrapper and set it as top.
# ---------------------------------------------------------------------------
if {[catch {make_wrapper -files [get_files */top_bd.bd] -top -import} e]} {
    puts "WRAPPER_ERR: $e"
} else {
    puts "WRAPPER_OK"
}

# Discover the generated wrapper name (design_name + _wrapper) rather than hardcoding.
set design_name [current_bd_design]              ;# top_bd
set wrapper_name "${design_name}_wrapper"        ;# top_bd_wrapper
if {[catch {set_property top $wrapper_name [current_fileset]} e]} {
    puts "SETTOP_ERR: $e"
} else {
    update_compile_order -fileset sources_1
    puts "SETTOP_OK $wrapper_name"
}

puts "TOP [get_property top [current_fileset]]"

# ---------------------------------------------------------------------------
# (4) ACCEPTANCE — run validate_bd_design ONCE now that ports+wrapper+top are set.
#     validate_bd_design returns non-zero if it finds ERRORs; we capture the
#     full report text so the log shows every message verbatim. Post-run, the
#     operator greps this log for "ERROR" to confirm zero errors.
# ---------------------------------------------------------------------------
set vres [catch {validate_bd_design} verr]
puts "VALIDATE_RC $vres"
if {$vres != 0} { puts "VALIDATE_ERR: $verr" } else { puts "VALIDATE_CLEAN (rc=0)" }

# List every top-level port with its direction so the operator can diff against
# constraints/pins_vcu118.xdc (the wrapper-rename check).
puts "=== BD PORTS ==="
foreach p [get_bd_ports -quiet] {
    puts "PORT $p dir=[get_property -quiet DIR $p]"
}

# Report where the generated wrapper landed so the operator can diff its ports.
puts "=== WRAPPER FILE ==="
set wfiles [get_files -quiet *${wrapper_name}.v]
foreach f $wfiles { puts "WRAPPER_FILE [get_property NAME $f] path=[get_property FILEPATH $f]" }

puts "=== 03d_ports.tcl done ==="

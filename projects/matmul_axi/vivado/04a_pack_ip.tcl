# 04a_pack_ip.tcl — Stage 5, round "pack IP" (方案 C): package RTL as user IP.
#
# Goal: turn xspi_slave and matmul_top into proper Vivado IP cores so their AXI
# ports become real bus interfaces (not scattered scalar pins). This lets the
# next round connect them in a block design with connect_bd_intf_net and let
# assign_bd_address trace a complete master->slave path.
#
# WHY ip_repo/<name>/ subdirs:
#   ipx::infer_core <dir> scans ONE directory, picks the single top-level module
#   (the one not instantiated by any other file in that dir), and writes
#   component.xml + xgui/ INTO that same directory. The generated component.xml
#   references its source .v files by BARE filename relative to the IP dir, so
#   each IP must be self-contained: all of its submodule closure must sit next
#   to its component.xml. We therefore copy each module's closure into its own
#   ip_repo/<name>/ folder and infer there. rtl/ is never pointed at, so it stays
#   clean (9 .v, no component.xml / xgui). The copies under ip_repo/ are build
#   artifacts (a user-IP repo), not the source of truth — rtl/ remains canonical.
#
# Closures (verified by grepping instantiation sites):
#   xspi_slave : xspi_slave.v  async_fifo.v  axi4_master.v
#   matmul_top : matmul_top.v  axi4s_reg.v  matmul_core.v  async_fifo.v
#                axi4_master.v  f32_mul.v  f32_add.v
#
# Run from the repo root (so relative paths resolve):
#   cd /c/Users/pjunm/matmul_axi
#   timeout 600 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog \
#       -source vivado/04a_pack_ip.tcl

puts "=== 04a_pack_ip.tcl start ==="

set root [file normalize .]
set rtl_dir  [file join $root rtl]
set ip_repo  [file join $root ip_repo]

# Idempotent: wipe any prior generated IP dirs so re-runs are clean.
if {[file exists $ip_repo]} { file delete -force $ip_repo }
file mkdir $ip_repo
puts "IP_REPO $ip_repo"

# module name -> list of source .v files that make up its closure (in rtl/).
set modules [list \
    [list xspi_slave  xspi_slave.v async_fifo.v axi4_master.v] \
    [list matmul_top  matmul_top.v axi4s_reg.v matmul_core.v async_fifo.v \
                     axi4_master.v f32_mul.v f32_add.v] \
]

# pack_one <name> <file ...>: copy closure into ip_repo/<name>/, infer the core,
# report the inferred bus interfaces. Returns 0 on success, 1 on failure.
proc pack_one { name args } {
    global root rtl_dir ip_repo
    set ipdir [file join $ip_repo $name]
    file mkdir $ipdir

    # Copy the closure .v files next to where component.xml will be written.
    foreach f $args {
        if {![file exists [file join $rtl_dir $f]]} {
            puts "PACK_FAIL $name missing source: $f"
            return 1
        }
        file copy -force [file join $rtl_dir $f] [file join $ipdir $f]
    }

    # Infer the core. This writes component.xml + xgui/ into $ipdir.
    set rc [catch {set core [ipx::infer_core -vendor local -library user \
                -taxonomy /UserIP $ipdir]} ierr]
    if {$rc != 0} {
        puts "PACK_FAIL $name infer_core error: $ierr"
        return 1
    }

    set xml [file join $ipdir component.xml]
    if {![file exists $xml]} {
        puts "PACK_FAIL $name no component.xml produced at $xml"
        return 1
    }

    # Report the inferred bus interfaces (the acceptance evidence).
    set ifaces [ipx::get_bus_interfaces -of_objects $core]
    set names [list]
    foreach i $ifaces { lappend names [get_property NAME $i] }
    puts "IFACES $name => [join $names , ]"

    # Which .v does component.xml reference (must cover the full closure).
    set fh [open $xml r]; set xc [read $fh]; close $fh
    set vrefs [list]
    foreach m [regexp -all -inline {[a-z_0-9]+\.v} $xc] {
        if {[lsearch -exact $vrefs $m] < 0} { lappend vrefs $m }
    }
    puts "XML_VREFS  $name: $vrefs"

    return 0
}

set failures 0
foreach m $modules {
    set name  [lindex $m 0]
    set files [lrange $m 1 end]
    if {[pack_one $name {*}$files] != 0} { incr failures }
}

puts "PACK_FAILURES $failures"

# Acceptance guard: rtl/ must still be exactly 9 .v with no generated IP junk.
set rtl_v [glob -nocomplain [file join $rtl_dir *.v]]
set rtl_xml [glob -nocomplain [file join $rtl_dir component.xml]]
set rtl_xgui [glob -nocomplain -directory $rtl_dir xgui]
puts "RTL_V_COUNT [llength $rtl_v]"
puts "RTL_COMPONENT_XML_PRESENT [expr {[llength $rtl_xml] > 0}] (want 0)"
puts "RTL_XGUI_PRESENT [expr {[file exists $rtl_xgui]}] (want 0)"

# List the produced component.xml files.
foreach m $modules {
    set name [lindex $m 0]
    set xml [file join $ip_repo $name component.xml]
    puts "XML $name exists=[file exists $xml]"
}

if {$failures == 0 && [llength $rtl_v] == 9 && ![file exists $rtl_xgui]} {
    puts "PACK_IP_OK"
} else {
    puts "PACK_IP_FAIL"
}
puts "=== 04a_pack_ip.tcl done ==="

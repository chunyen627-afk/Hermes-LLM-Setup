// tb_system : xsim bring-up + xSPI end-to-end read/write of the top_bd_wrapper.
//
// Round 1 (done): proved the block design elaborates and runs to $finish with a
//   short idle window ("SYSCHECK boot ok").
// Round 2 (this file): extend that bring-up into an end-to-end data-flow test,
//   per SPEC_system_integration.md section 5 item 4:
//     1. wait for MIG DDR4 calibration to complete (c0_init_calib_complete)
//     2. from the xSPI interface, WRITE 8 halfwords into a DDR4 address
//        (top of the 2GB / 31-bit DDR4 space = "the 0x8000_0000 range")
//     3. READ back the same address from the xSPI interface
//     4. compare and print:   SYSCHECK data_flow <checked> <bad>
//
// The xSPI wire protocol is driven EXACTLY like tb/tb_xspi_slave.v's drive_frame
// task (instruction SDR + address DDR(2cyc) + dummy + data DDR(16b/cyc), MODE 0).
// We do NOT touch rtl/*.v, ip_repo/, the block design, or constraints.

`timescale 1ns/1ps

module tb_system;

    // ---- Clocks / reset ----------------------------------------------------
    localparam real SYSCLK_PERIOD = 3.333;   // ~300 MHz board sysclk
    localparam integer XSPI_HALF = 10;       // 20 ns period = 50 MHz SCK

    reg         sysclk_p;
    reg         sysclk_n;
    reg         rst_n;

    // ---- xSPI side (now actively driven) -----------------------------------
    reg         xspi_clk;      // SCK from the "host" (this TB); BD input port
    reg         xspi_cs_n;     // chip select, active low
    wire        xspi_dqs;      // output from DUT (write data strobe) - unused by slave
    wire [7:0]  xspi_io;       // bidirectional
    reg  [7:0]  xspi_io_master;// master-model driver value

    // ---- DDR4 physical interface (MIG drives these in sim) -----------------
    wire        C0_DDR4_0_act_n;
    wire [16:0] C0_DDR4_0_adr;
    wire [1:0]  C0_DDR4_0_ba;
    wire        C0_DDR4_0_bg;
    wire        C0_DDR4_0_ck_c;
    wire        C0_DDR4_0_ck_t;
    wire        C0_DDR4_0_cke;
    wire        C0_DDR4_0_cs_n;
    wire [7:0]  C0_DDR4_0_dm_n;
    wire [63:0] C0_DDR4_0_dq;
    wire [7:0]  C0_DDR4_0_dqs_c;
    wire [7:0]  C0_DDR4_0_dqs_t;
    wire        C0_DDR4_0_odt;
    wire        C0_DDR4_0_reset_n;

    // ---- Address map (must match xspi_slave.v defaults) --------------------
    localparam [31:0] DDR_BASE     = 32'h9001_0000;  // host frame addr -> m_ddr
    localparam [31:0] DDR4_OFFSET  = 32'h7FFF_F000;  // top of the 2GB (31-bit) space
    localparam [31:0] FRAME_ADDR   = DDR_BASE + DDR4_OFFSET;  // 0x10FFF_F000

    // Bounded calib wait: max sim-time to wait for c0_init_calib_complete before
    // proceeding anyway (prints a warning). 5 ms of sim time is well beyond the
    // expected MIG behavioral calibration duration.
    localparam integer CALIB_CAP_US = 5_000;   // 5 ms of sim time

    // ---- Waveform dump (rule 6) --------------------------------------------
    // Dump ONLY the signals we need to debug the xSPI data path. A full
    // $dumpvars(0, tb_system) captures the entire MIG DDR4 PHY hierarchy
    // (thousands of bits at ps precision) and makes sim take hours + GBs of VCD.
    initial begin
        $dumpfile("tb_system.vcd");
        $dumpvars(1, tb_system);                       // TB-level regs/wires
        $dumpvars(0, dut.top_bd_i.xspi_slave.inst);     // xspi_slave internals
        $dumpvars(0, dut.top_bd_i.mig_ddr4.c0_init_calib_complete);
    end

    // ---- sysclk: differential pair, p leads by half a period ----------------
    always #(SYSCLK_PERIOD/2.0) sysclk_p = ~sysclk_p;
    always #(SYSCLK_PERIOD/2.0) #1.6665 sysclk_n = ~sysclk_n;

    // ---- xspi_clk: real 50 MHz SCK from the "host" -------------------------
    initial begin
        xspi_clk = 1'b0;
        forever #XSPI_HALF xspi_clk = ~xspi_clk;
    end

    // Multi-driver IO bus: DUT drives during read-data (io_oe=1), master model
    // otherwise. Mutually exclusive so the net never sees a conflict.
    genvar gi;
    generate
        for (gi = 0; gi < 8; gi = gi + 1) begin : io_master_gen
            // io_oe lives one level down: the BD instantiates the IP wrapper
            // top_bd_xspi_slave_0 (cell name "xspi_slave"), which instantiates
            // the raw xspi_slave module as ".inst".
            assign xspi_io[gi] = dut.top_bd_i.xspi_slave.inst.io_oe ? 1'bz : xspi_io_master[gi];
        end
    endgenerate

    // ---- DUT ----------------------------------------------------------------
    top_bd_wrapper dut (
        .C0_DDR4_0_act_n   (C0_DDR4_0_act_n),
        .C0_DDR4_0_adr     (C0_DDR4_0_adr),
        .C0_DDR4_0_ba      (C0_DDR4_0_ba),
        .C0_DDR4_0_bg      (C0_DDR4_0_bg),
        .C0_DDR4_0_ck_c    (C0_DDR4_0_ck_c),
        .C0_DDR4_0_ck_t    (C0_DDR4_0_ck_t),
        .C0_DDR4_0_cke     (C0_DDR4_0_cke),
        .C0_DDR4_0_cs_n    (C0_DDR4_0_cs_n),
        .C0_DDR4_0_dm_n    (C0_DDR4_0_dm_n),
        .C0_DDR4_0_dq      (C0_DDR4_0_dq),
        .C0_DDR4_0_dqs_c   (C0_DDR4_0_dqs_c),
        .C0_DDR4_0_dqs_t   (C0_DDR4_0_dqs_t),
        .C0_DDR4_0_odt     (C0_DDR4_0_odt),
        .C0_DDR4_0_reset_n (C0_DDR4_0_reset_n),
        .rst_n             (rst_n),
        .sysclk_p          (sysclk_p),
        .sysclk_n          (sysclk_n),
        .xspi_clk          (xspi_clk),
        .xspi_cs_n         (xspi_cs_n),
        .xspi_dqs          (xspi_dqs),
        .xspi_io           (xspi_io)
    );

    // ---- Shared data buffer + check counters --------------------------------
    reg [15:0] data [0:31];      // halfwords: write=0..7, read-back=8..15
    integer chk_checked, chk_bad;
    integer i;

    // ================= master-model tasks (mirror tb_xspi_slave.v) ==========
    // Drive a byte on the IO bus at the next falling edge (master shift-out).
    task drive_fall(input [7:0] val);
        begin @(negedge xspi_clk); #1; xspi_io_master = val; end
    endtask

    // Drive N dummy cycles (IO held at 0).
    task drive_dummy(input integer n);
        integer k;
        begin
            for (k = 0; k < n; k = k + 1) begin
                @(negedge xspi_clk); #1; xspi_io_master = 8'h00;
            end
        end
    endtask

    // Drive a complete frame: instruction + address(DDR) + dummy + data(DDR).
    // Data is packed halfwords (16 bits each), one per DDR cycle, in `data`
    // starting at halfword index `base`. For writes the master drives
    // data[base+i]; for reads the slave->master bytes are captured there.
    task drive_frame(
        input  [7:0]  instr,
        input  [31:0] addr,
        input  integer n_dummy,
        input  integer base,
        input  integer n_hw,
        input         is_read
    );
        integer k;
        reg [15:0] hw;
        begin
            xspi_cs_n = 1'b1;
            @(negedge xspi_clk); #1; xspi_cs_n = 1'b0;

            // Instruction (SDR, 1 cycle).
            @(negedge xspi_clk); #1; xspi_io_master = instr;

            // Address (DDR, 2 cycles, 32 bits MSB first).
            @(negedge xspi_clk); #1; xspi_io_master = addr[31:24];   // byte3 (rising)
            @(negedge xspi_clk); #1; xspi_io_master = addr[23:16];   // byte2 (falling)
            @(negedge xspi_clk); #1; xspi_io_master = addr[15:8];    // byte1 (rising)
            @(negedge xspi_clk); #1; xspi_io_master = addr[7:0];     // byte0 (falling)

            drive_dummy(n_dummy);

            // Data phase (DDR: one halfword per SCK cycle, 16 bits/cycle).
            for (k = 0; k < n_hw; k = k + 1) begin
                if (is_read) begin
                    @(posedge xspi_clk); #1; hw[15:8] = xspi_io;   // upper (rising)
                    @(negedge xspi_clk); #1; hw[7:0]  = xspi_io;   // lower (falling)
                    data[base+k] = hw;
                end else begin
                    hw = data[base+k];
                    @(negedge xspi_clk); #1; xspi_io_master = hw[15:8];   // upper
                    @(posedge xspi_clk); #1; xspi_io_master = hw[7:0];    // lower
                end
            end

            @(negedge xspi_clk); #1; xspi_cs_n = 1'b1;   // deassert CS
        end
    endtask

    // Wait until both AXI masters inside xspi_slave are idle (bounded, time-based
    // so it always terminates even if the DUT clocks were somehow stuck).
    task wait_axi_idle;
        integer t;
        begin
            t = 0;
            // The IP wrapper cell is "xspi_slave"; the raw module (with the two
            // axi4_master instances) is ".inst". rd_busy/wr_busy are axi4_master ports.
            while ((dut.top_bd_i.xspi_slave.inst.u_reg_master.rd_busy ||
                    dut.top_bd_i.xspi_slave.inst.u_reg_master.wr_busy ||
                    dut.top_bd_i.xspi_slave.inst.u_ddr_master.rd_busy ||
                    dut.top_bd_i.xspi_slave.inst.u_ddr_master.wr_busy) && t < 40000) begin
                #1000; t = t + 1;   // 1 us poll, 40 ms cap
            end
            if (t >= 40000) $display("WARN wait_axi_idle timeout");
        end
    endtask

    // ---- Diagnostic heartbeat (rule 6/8: print what you can't see) --------
    // Every 100 us of sim time, report whether MIG calibration is progressing or
    // stuck. Prints the calib signal's raw value plus a UI-clock toggle count so
    // we can tell "calibrating slowly" from "UI clock never running / calib x".
    reg [31:0] ui_clk_toggles;
    initial begin
        ui_clk_toggles = 0;
        forever begin
            #100_000;   // 100 us of sim time (fits in a 32-bit delay)
            $display("SYSCHECK diag t=%0t calib=%b ui_clk_toggles_since_last=%0d",
                     $time, dut.top_bd_i.mig_ddr4.c0_init_calib_complete, ui_clk_toggles);
            ui_clk_toggles = 0;
        end
    end
    initial begin
        forever begin
            @(posedge dut.top_bd_i.mig_ddr4.c0_ddr4_ui_clk);
            ui_clk_toggles = ui_clk_toggles + 1;
        end
    end

    // ---- Reset + test sequence ---------------------------------------------
    // NOTE on delays: xsim clamps very large delay literals (>= ~4e9) to ~0, so a
    // single "#20_000_000_000" watchdog fires at t=0. All bounded waits below use
    // small per-step literals in a counter loop instead -- always safe.
    integer calib_waited_us;

    initial begin
        rst_n          = 1'b0;
        xspi_cs_n      = 1'b1;   // idle, not selected
        sysclk_p       = 1'b0;
        sysclk_n       = 1'b0;
        xspi_io_master = 8'h00;

        #1000;              // hold reset for ~1us
        rst_n = 1'b1;       // deassert

        // (1) Wait for MIG DDR4 calibration to complete, bounded. Poll every 1 us
        //     up to CALIB_CAP_US (20 ms of sim time). If it asserts, proceed; if
        //     the cap is hit first, print a warning and proceed anyway so the test
        //     still completes and prints numbers (per spec: "先求跑得完").
        calib_waited_us = 0;
        $display("SYSCHECK waiting for MIG calib (t=%0t)...", $time);
        while ((dut.top_bd_i.mig_ddr4.c0_init_calib_complete !== 1'b1) &&
               calib_waited_us < CALIB_CAP_US) begin
            #1000;                  // 1 us step (small literal, xsim-safe)
            calib_waited_us = calib_waited_us + 1;
        end
        if (dut.top_bd_i.mig_ddr4.c0_init_calib_complete === 1'b1)
            $display("SYSCHECK calib complete t=%0t", $time);
        else
            $display("SYSCHECK WARN: calib not complete after %0d us -- proceeding anyway",
                     calib_waited_us);

        // (2) Write 8 known halfwords to the DDR4 address via xSPI.
        for (i = 0; i < 8; i = i + 1) data[i] = {i[7:0], i[7:0] ^ 8'h5a};
        drive_frame(8'ha0, FRAME_ADDR, 4, 0, 8, 1'b0);   // linear-burst write
        wait_axi_idle;

        // (3) Read back the same address via xSPI.
        drive_frame(8'h20, FRAME_ADDR, 4, 8, 8, 1'b1);   // linear-burst read
        wait_axi_idle;

        // (4) Compare and report (same format as module-level CHECK).
        for (i = 0; i < 8; i = i + 1) begin
            chk_checked = chk_checked + 1;
            if (data[8+i] !== data[i]) begin
                chk_bad = chk_bad + 1;
                $display("SYSCHECK MISMATCH hw %0d: got %h expected %h", i, data[8+i], data[i]);
            end
        end
        $display("SYSCHECK data_flow %0d %0d", chk_checked, chk_bad);

        #1000;
        $finish;
    end

endmodule

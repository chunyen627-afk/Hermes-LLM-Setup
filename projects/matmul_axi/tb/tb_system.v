// tb_system : minimal xsim bring-up of the top_bd_wrapper (Stage 5, round 1).
//
// Goal this round ONLY: prove the full block design elaborates and runs in xsim
// without hanging or throwing an elaboration error. We do NOT drive the xSPI
// bus with a real transaction and we do NOT wait for MIG DDR4 calibration to
// complete (that is the next round). We just:
//   - instantiate top_bd_wrapper
//   - feed the board's 300 MHz differential sysclk + deassert rst_n
//   - leave xSPI in an idle state (cs high, io released)
//   - run a short window and print "SYSCHECK boot ok" to prove it reached $finish.
//
// The DDR4 C0_DDR4_0_* pins are the MIG's physical interface. In simulation the
// MIG behavioral model drives them; with no real SDRAM attached they simply float
// on the inout nets, which is fine for a bring-up that does not read/write DDR4.

`timescale 1ns/1ps

module tb_system;

    // ---- Clocks / reset ----------------------------------------------------
    localparam SYSCLK_PERIOD = 3.333;   // ~300 MHz (board sysclk)

    reg         sysclk_p;
    reg         sysclk_n;
    reg         rst_n;

    // ---- xSPI side (idle this round) ---------------------------------------
    reg         xspi_clk;      // driven by the DUT's clk_wiz, but also a port; tie low
    reg         xspi_cs_n;     // idle high = chip not selected
    wire        xspi_dqs;      // output from DUT (write data strobe)
    wire [7:0]  xspi_io;       // bidirectional

    // ---- DDR4 physical interface (MIG drives these in sim) -----------------
    wire        C0_DDR4_0_act_n;
    wire [16:0] C0_DDR4_0_adr;
    wire [1:0]  C0_DDR4_0_ba;
    wire        C0_DDR4_0_bg;
    wire        C0_DDR4_0_ck_c;
    wire        C0_DDR4_0_ck_t;
    wire        C0_DDR4_0_cke;
    wire        C0_DDR4_0_cs_n;
    wire [7:0]  C0_DDR4_0_dm_n;   // inout in wrapper -> plain wire here (MIG drives)
    wire [63:0] C0_DDR4_0_dq;     // inout in wrapper -> plain wire here
    wire [7:0]  C0_DDR4_0_dqs_c;  // inout in wrapper -> plain wire here
    wire [7:0]  C0_DDR4_0_dqs_t;  // inout in wrapper -> plain wire here
    wire        C0_DDR4_0_odt;
    wire        C0_DDR4_0_reset_n;

    // ---- Waveform dump (rule 6) --------------------------------------------
    initial begin
        $dumpfile("tb_system.vcd");
        $dumpvars(0, tb_system);
    end

    // ---- sysclk: differential pair, p leads by half a period ----------------
    always #(SYSCLK_PERIOD/2.0) sysclk_p = ~sysclk_p;
    always #(SYSCLK_PERIOD/2.0) #1.6665 sysclk_n = ~sysclk_n;

    // ---- xspi_clk: DUT generates its own 50 MHz internally from sysclk, so
    //      the external xspi_clk port is not a clock source for the DUT logic in
    //      this BD (clk_wiz derives it). Tie low to avoid an undriven input.
    initial xspi_clk = 1'b0;

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

    // ---- Reset + run window -------------------------------------------------
    initial begin
        rst_n     = 1'b0;
        xspi_cs_n = 1'b1;   // idle, not selected
        sysclk_p  = 1'b0;
        sysclk_n  = 1'b0;

        #1000;              // hold reset for ~1us
        rst_n = 1'b1;       // deassert

        // Run a short bring-up window. MIG calibration is NOT awaited this round.
        #20000;             // ~20 us total from t=0

        $display("SYSCHECK boot ok");
        $finish;
    end

endmodule

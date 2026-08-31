// tb_axi4_slave_reg : self-checking AXI4 master for axi4_slave_reg.
//
// Exercises: single-beat word writes/reads, WSTRB byte-select, RO-register
// write-ignore, and multi-beat INCR bursts. Parameterized on AXI_DATA_WIDTH so
// the same TB checks 32/64/128/256-bit buses.
`timescale 1ns/1ps
module tb_axi4_slave_reg #(
    parameter AXI_DATA_WIDTH = 256,
    parameter AXI_ADDR_WIDTH = 32,
    parameter C_AXI_ID_WIDTH = 4
);

    localparam W = AXI_DATA_WIDTH;
    localparam SB= W/8;                 // strobe width
    localparam IDW = C_AXI_ID_WIDTH;

    reg         clk = 0;
    reg         rst_n = 0;
    always #5 clk = ~clk;               // 100 MHz

    // ---- AXI signals ----
    reg  [IDW-1:0] awid;  reg [AXI_ADDR_WIDTH-1:0] awaddr;
    reg  [7:0] awlen;     reg [2:0] awsize; reg [1:0] awburst;
    reg  awvalid;
    wire awready;

    reg  [W-1:0] wdata; reg [SB-1:0] wstrb; reg wlast; reg wvalid;
    wire wready;

    wire [IDW-1:0] bid; wire [1:0] bresp; wire bvalid; reg bready;

    reg  [IDW-1:0] arid;  reg [AXI_ADDR_WIDTH-1:0] araddr;
    reg  [7:0] arlen;     reg [2:0] arsize; reg [1:0] arburst;
    reg  arvalid;
    wire arready;

    wire [IDW-1:0] rid; wire [W-1:0] rdata; wire [1:0] rresp;
    wire rlast; wire rvalid; reg rready;

    // ---- register-file side (IP-driven RO values) ----
    wire [31:0] ctrl, w_base, x_base, out_base;
    reg  [31:0] status = 32'h0000_0005;   // busy|done|error set, for RO check
    reg  [31:0] m_dim  = 32'd288;
    reg  [31:0] n_dim  = 32'd288;
    reg  [31:0] count  = 32'd42;
    reg  [31:0] burst_rd [0:63];          // scratch for read-burst results

    axi4_slave_reg #(
        .AXI_DATA_WIDTH(W), .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .C_AXI_ID_WIDTH(IDW)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .awid(awid), .awaddr(awaddr), .awlen(awlen), .awsize(awsize),
        .awburst(awburst), .awvalid(awvalid), .awready(awready),
        .wdata(wdata), .wstrb(wstrb), .wlast(wlast), .wvalid(wvalid),
        .wready(wready),
        .bid(bid), .bresp(bresp), .bvalid(bvalid), .bready(bready),
        .arid(arid), .araddr(araddr), .arlen(arlen), .arsize(arsize),
        .arburst(arburst), .arvalid(arvalid), .arready(arready),
        .rid(rid), .rdata(rdata), .rresp(rresp), .rlast(rlast),
        .rvalid(rvalid), .rready(rready),
        .ctrl(ctrl), .status(status), .m_dim(m_dim), .n_dim(n_dim),
        .w_base(w_base), .x_base(x_base), .out_base(out_base), .count(count)
    );

    // ------------------------------------------------------------------
    // AXI master tasks (word-granular, awsize/arsize = 2 => 4-byte beats)
    // ------------------------------------------------------------------
    integer i;
    reg [31:0] rd_word;

    task axi_write_word(input [AXI_ADDR_WIDTH-1:0] addr, input [31:0] data);
        begin
            @(posedge clk);
            // drive AW + W together (independent channels)
            awid <= 4'd1; awaddr <= addr; awlen <= 8'd0;
            awsize <= 3'd2; awburst <= 2'b01; awvalid <= 1'b1;
            wdata  <= data;             // zero-extends to W bits
            wstrb  <= 4'b1111;          // low word only, zero-extends to SB
            wlast  <= 1'b1; wvalid <= 1'b1; bready <= 1'b1;
            // wait for the write to complete (B response); valids stay high
            while (!bvalid) @(posedge clk);
            bready <= 1'b0; awvalid <= 1'b0; wvalid <= 1'b0;
        end
    endtask

    task axi_read_word(input [AXI_ADDR_WIDTH-1:0] addr, output [31:0] data);
        begin
            @(posedge clk);
            arid <= 4'd2; araddr <= addr; arlen <= 8'd0;
            arsize <= 3'd2; arburst <= 2'b01; arvalid <= 1'b1; rready <= 1'b1;
            // wait for R data (DUT latches AR, then asserts rvalid)
            while (!rvalid) @(posedge clk);
            data   = rdata[31:0];   // sample in active region (before NBA)
            arvalid <= 1'b0; rready <= 1'b0;
        end
    endtask

    // Multi-beat write burst of `n` consecutive 32-bit words starting at addr.
    task axi_write_burst(input [AXI_ADDR_WIDTH-1:0] addr,
                         input integer n, input [31:0] base_val);
        integer nlen;
        begin
            nlen = n - 1;
            @(posedge clk);
            awid <= 4'd1; awaddr <= addr; awlen <= nlen[7:0];
            awsize <= 3'd2; awburst <= 2'b01; awvalid <= 1'b1;
            bready <= 1'b1;
            // wait for AW to be latched (wready goes high) before streaming W
            while (!wready) @(posedge clk);
            // stream n beats, one per cycle (wready stays high through burst)
            for (i = 0; i < n; i = i + 1) begin
                wdata  <= base_val + i[31:0];
                wstrb  <= 4'b1111;
                wlast  <= (i == n-1);
                wvalid <= 1'b1;
                @(posedge clk);                 // beat consumed at this edge
            end
            wvalid <= 1'b0;
            while (!bvalid) @(posedge clk);
            bready <= 1'b0; awvalid <= 1'b0;
        end
    endtask

    // Multi-beat read burst of `n` consecutive 32-bit words (into global arr).
    task axi_read_burst(input [AXI_ADDR_WIDTH-1:0] addr, input integer n);
        integer nlen;
        begin
            nlen = n - 1;
            @(posedge clk);
            arid <= 4'd2; araddr <= addr; arlen <= nlen[7:0];
            arsize <= 3'd2; arburst <= 2'b01; arvalid <= 1'b1; rready <= 1'b1;
            // wait for first rvalid, then sample one beat per posedge
            while (!rvalid) @(posedge clk);
            for (i = 0; i < n; i = i + 1) begin
                burst_rd[i] = rdata[31:0];   // active region: current beat
                if (i < n-1) @(posedge clk);
            end
            arvalid <= 1'b0; rready <= 1'b0;
        end
    endtask

    // ------------------------------------------------------------------
    // Test driver
    // ------------------------------------------------------------------
    integer errors = 0;
    integer checks_total = 0;
    reg [31:0] rd;

    task check(input [255:0] name, input [31:0] got, input [31:0] exp);
        begin
            checks_total = checks_total + 1;
            if (got !== exp) begin
                $display("FAIL %0s: got=%h exp=%h", name, got, exp);
                errors = errors + 1;
            end else begin
                $display("ok   %0s = %h", name, got);
            end
        end
    endtask

    initial begin
        awid=0;awaddr=0;awlen=0;awsize=0;awburst=0;awvalid=0;
        wdata=0;wstrb=0;wlast=0;wvalid=0;bready=0;
        arid=0;araddr=0;arlen=0;arsize=0;arburst=0;arvalid=0;rready=0;

        // reset
        rst_n = 0;
        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        $display("=== AXI data width = %0d ===", W);

        // --- Test 1: write + read back RW registers (single-beat) ---
        axi_write_word(32'h00, 32'h0000_0007);   // CTRL
        axi_write_word(32'h10, 32'h0001_0000);   // W_BASE
        axi_write_word(32'h14, 32'h0002_0000);   // X_BASE
        axi_write_word(32'h18, 32'h0003_0000);   // OUT_BASE

        axi_read_word(32'h00, rd); check("CTRL",    rd, 32'h0000_0007);
        axi_read_word(32'h10, rd); check("W_BASE",  rd, 32'h0001_0000);
        axi_read_word(32'h14, rd); check("X_BASE",  rd, 32'h0002_0000);
        axi_read_word(32'h18, rd); check("OUT_BASE",rd, 32'h0003_0000);

        // --- Test 2: RO registers read back the IP-driven value ---
        axi_read_word(32'h04, rd); check("STATUS(RO)", rd, status);
        axi_read_word(32'h08, rd); check("M_DIM(RO)",  rd, m_dim);
        axi_read_word(32'h0C, rd); check("N_DIM(RO)",  rd, n_dim);
        axi_read_word(32'h1C, rd); check("COUNT(RO)",  rd, count);

        // --- Test 3: writing a RO register is ignored ---
        axi_write_word(32'h04, 32'hDEAD_BEEF);   // try to clobber STATUS
        axi_read_word(32'h04, rd); check("STATUS after write (RO)", rd, status);

        // --- Test 4: multi-beat write burst of 4 words at 0x10..0x1C ---
        axi_write_burst(32'h10, 4, 32'hA000_0000);   // W_BASE,X_BASE,OUT_BASE,COUNT
        axi_read_word(32'h10, rd); check("W_BASE(burst)",  rd, 32'hA000_0000);
        axi_read_word(32'h14, rd); check("X_BASE(burst)",  rd, 32'hA000_0001);
        axi_read_word(32'h18, rd); check("OUT_BASE(burst)",rd, 32'hA000_0002);
        // COUNT is RO -> should still be the IP value, not A000_0003
        axi_read_word(32'h1C, rd); check("COUNT after burst (RO)", rd, count);

        // --- Test 5: multi-beat read burst of 4 words at 0x00..0x0C ---
        axi_write_word(32'h00, 32'h1111_1111);
        axi_read_burst(32'h00, 4);
        check("burst[0] CTRL",   burst_rd[0], 32'h1111_1111);
        check("burst[1] STATUS", burst_rd[1], status);
        check("burst[2] M_DIM",  burst_rd[2], m_dim);
        check("burst[3] N_DIM",  burst_rd[3], n_dim);

        // --- Test 6: WSTRB byte-select (write only low byte of CTRL) ---
        @(posedge clk);
        awid<=4'd1;awaddr<=32'h00;awlen<=8'd0;awsize<=3'd2;awburst<=2'b01;awvalid<=1'b1;
        wdata<=32'h0000_00FF; wstrb<=4'b0001; // low byte only, zero-extends to SB
        wlast<=1'b1;wvalid<=1'b1;bready<=1'b1;
        while (!bvalid) @(posedge clk); bready<=1'b0; awvalid<=1'b0; wvalid<=1'b0;
        axi_read_word(32'h00, rd);
        // CTRL was 0x1111_1111, low byte now 0xFF -> 0x1111_11FF
        check("CTRL after byte-write", rd, 32'h1111_11FF);

        if (errors == 0) $display("ALL_PASS (width=%0d)", W);
        else             $display("HAS_FAILURES: %0d errors (width=%0d)", errors, W);
        // ---- simcheck gate markers ----
        $display("CHECK axi4_slave_reg %0d %0d", checks_total, errors);
        $display("SIMEND %s", (errors == 0) ? "ok" : "fail");
        $finish;
    end

    // watchdog
    initial begin
        #1_000_000;
        $display("TIMEOUT");
        $finish;
    end

endmodule

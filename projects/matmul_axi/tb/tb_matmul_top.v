// tb_matmul_top : drive matmul_top over its AXI4 slave port, verify the
// register map and a full matmul run end-to-end.
//
// Flow:
//   1. reset
//   2. read M_DIM / N_DIM -> check they equal D / N
//   3. write W_BASE / X_BASE / OUT_BASE (stored, echoed back on read)
//   4. write CTRL[0]=1 (start)
//   5. poll STATUS until done
//   6. compare xout_vec against expected.hex (C-oracle result)
//
// The matmul_core loads w.hex / x.hex via $readmemh (vvp cwd). expected.hex is
// the FP32 bit patterns from the C oracle, one per row.

`timescale 1ns/1ps

module tb_matmul_top #(
    parameter D = 288,
    parameter N = 768
);
    localparam ADDR_WIDTH = 32;
    localparam DATA_WIDTH = 32;
    localparam ID_WIDTH   = 4;

    reg clk = 0;
    reg aresetn = 0;
    always #5 clk = ~clk;

    // ---- AXI write-address ----
    reg        awvalid = 0;
    wire       awready;
    reg [31:0] awaddr  = 0;
    reg [7:0]  awlen   = 0;
    reg [2:0]  awsize  = 2;     // 4 bytes
    reg [1:0]  awburst = 2'b01; // INCR
    reg [3:0]  awid    = 0;

    // ---- AXI write-data ----
    reg        wvalid = 0;
    wire       wready;
    reg [31:0] wdata  = 0;
    reg [3:0]  wstrb  = 4'hF;

    // ---- AXI write-response ----
    wire       bvalid;
    reg        bready = 1;
    wire [1:0] bresp;
    wire [3:0] bid;

    // ---- AXI read-address ----
    reg        arvalid = 0;
    wire       arready;
    reg [31:0] araddr  = 0;
    reg [7:0]  arlen   = 0;
    reg [2:0]  arsize  = 2;
    reg [1:0]  arburst = 2'b01;
    reg [3:0]  arid    = 0;

    // ---- AXI read-data ----
    wire       rvalid;
    reg        rready = 1;
    wire [31:0] rdata;
    wire [1:0] rresp;
    wire       rlast;
    wire [3:0] rid;

    wire [D*32-1:0] xout_vec;

    matmul_top #(
        .D(D), .N(N),
        .ADDR_WIDTH(ADDR_WIDTH), .DATA_WIDTH(DATA_WIDTH), .ID_WIDTH(ID_WIDTH)
    ) dut (
        .aclk(clk), .aresetn(aresetn),
        .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_awaddr(awaddr),   .s_axi_awlen(awlen),
        .s_axi_awsize(awsize),   .s_axi_awburst(awburst), .s_axi_awid(awid),
        .s_axi_wvalid(wvalid),   .s_axi_wready(wready),
        .s_axi_wdata(wdata),     .s_axi_wstrb(wstrb),
        .s_axi_bvalid(bvalid),   .s_axi_bready(bready),
        .s_axi_bresp(bresp),     .s_axi_bid(bid),
        .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_araddr(araddr),   .s_axi_arlen(arlen),
        .s_axi_arsize(arsize),   .s_axi_arburst(arburst), .s_axi_arid(arid),
        .s_axi_rvalid(rvalid),   .s_axi_rready(rready),
        .s_axi_rdata(rdata),     .s_axi_rresp(rresp),
        .s_axi_rlast(rlast),     .s_axi_rid(rid),
        .xout_vec(xout_vec)
    );

    integer errors = 0;

    // register byte addresses (word index * 4)
    localparam [31:0] ADDR_CTRL     = 32'h00;
    localparam [31:0] ADDR_STATUS   = 32'h04;
    localparam [31:0] ADDR_M_DIM    = 32'h08;
    localparam [31:0] ADDR_N_DIM    = 32'h0C;
    localparam [31:0] ADDR_W_BASE   = 32'h10;
    localparam [31:0] ADDR_X_BASE   = 32'h14;
    localparam [31:0] ADDR_OUT_BASE = 32'h18;
    localparam [31:0] ADDR_COUNT    = 32'h1C;

    // ---- AXI write task: single-beat, blocking. Drive on negedge, sample the
    // handshake in the active region (before NBA updates) so awready/wready are
    // seen at their pre-edge values. ----
    reg hs;
    task axi_write(input [31:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            awaddr = addr; awlen = 0; awsize = 2; awburst = 2'b01; awid = 0;
            awvalid = 1;
            // wait for a posedge where AW is accepted (awready high pre-edge)
            hs = 0;
            while (!hs) begin
                @(posedge clk);
                hs = awvalid && awready;
            end
            @(negedge clk);
            awvalid = 0;
            // drive W data; wready = aw_pending (now high)
            wdata = data; wstrb = 4'hF; wvalid = 1;
            hs = 0;
            while (!hs) begin
                @(posedge clk);
                hs = wvalid && wready;
            end
            @(negedge clk);
            wvalid = 0;
            // wait for B response (bvalid = w_done, bready held high)
            hs = 0;
            while (!hs) begin
                @(posedge clk);
                hs = bvalid && bready;
            end
            if (bresp !== 2'b00) begin
                $display("ERROR: write to %h got bresp=%b", addr, bresp);
                errors = errors + 1;
            end
        end
    endtask

    // ---- AXI read task: single-beat, blocking. Same negedge-drive pattern. ----
    task axi_read(input [31:0] addr, output [31:0] data);
        begin
            @(negedge clk);
            araddr = addr; arlen = 0; arsize = 2; arburst = 2'b01; arid = 0;
            arvalid = 1;
            // wait for a posedge where AR is accepted
            hs = 0;
            while (!hs) begin
                @(posedge clk);
                hs = arvalid && arready;
            end
            @(negedge clk);
            arvalid = 0;
            // wait for R data (rvalid = ar_pending, now high; rready held high)
            hs = 0;
            while (!hs) begin
                @(posedge clk);
                hs = rvalid && rready;
            end
            data = rdata;
            if (rresp !== 2'b00) begin
                $display("ERROR: read from %h got rresp=%b", addr, rresp);
                errors = errors + 1;
            end
        end
    endtask

    reg [31:0] rd;
    integer i;
    reg [31:0] expected [0:D-1];

    initial begin
        $readmemh("expected.hex", expected);

        // reset
        aresetn = 0;
        repeat (4) @(posedge clk);
        aresetn = 1;
        repeat (2) @(posedge clk);

        // ---- check M_DIM / N_DIM ----
        axi_read(ADDR_M_DIM, rd);
        if (rd !== D) begin $display("ERROR: M_DIM=%0d expected %0d", rd, D); errors=errors+1; end
        else $display("OK: M_DIM=%0d", rd);

        axi_read(ADDR_N_DIM, rd);
        if (rd !== N) begin $display("ERROR: N_DIM=%0d expected %0d", rd, N); errors=errors+1; end
        else $display("OK: N_DIM=%0d", rd);

        // ---- write base addresses, read back ----
        axi_write(ADDR_W_BASE,   32'h0000_1000);
        axi_write(ADDR_X_BASE,   32'h0000_2000);
        axi_write(ADDR_OUT_BASE, 32'h0000_3000);
        axi_read(ADDR_W_BASE, rd);
        if (rd !== 32'h0000_1000) begin $display("ERROR: W_BASE readback=%h", rd); errors=errors+1; end
        else $display("OK: W_BASE readback");
        axi_read(ADDR_X_BASE, rd);
        if (rd !== 32'h0000_2000) begin $display("ERROR: X_BASE readback=%h", rd); errors=errors+1; end
        else $display("OK: X_BASE readback");
        axi_read(ADDR_OUT_BASE, rd);
        if (rd !== 32'h0000_3000) begin $display("ERROR: OUT_BASE readback=%h", rd); errors=errors+1; end
        else $display("OK: OUT_BASE readback");

        // ---- start the matmul ----
        axi_write(ADDR_CTRL, 32'h1);   // bit0 = start

        // ---- poll STATUS until done (bit1) ----
        rd = 0;
        i = 0;
        while (!rd[1] && i < 200000) begin
            axi_read(ADDR_STATUS, rd);
            i = i + 1;
            if (i % 50000 == 0) $display("polling... iter=%0d status=%h", i, rd);
        end
        if (!rd[1]) begin $display("ERROR: matmul did not finish"); errors=errors+1; $finish; end
        $display("OK: matmul done (status=%h)", rd);

        // ---- check COUNT ----
        axi_read(ADDR_COUNT, rd);
        if (rd !== D) begin $display("ERROR: COUNT=%0d expected %0d", rd, D); errors=errors+1; end
        else $display("OK: COUNT=%0d", rd);

        // ---- compare xout_vec against expected ----
        for (i = 0; i < D; i = i + 1) begin
            if (xout_vec[i*32 +: 32] !== expected[i]) begin
                $display("MISMATCH row %0d: got=%h exp=%h", i, xout_vec[i*32 +: 32], expected[i]);
                errors = errors + 1;
            end
        end

        if (errors == 0) $display("ALL_PASS (%0d rows verified)", D);
        else             $display("HAS_FAILURES (%0d errors)", errors);
        $finish;
    end

endmodule

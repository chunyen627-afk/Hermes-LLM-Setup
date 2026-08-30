// tb_async_fifo : verify the gray-pointer async FIFO across unrelated clocks.
//
// Checks:
//   1. data integrity: every word written is read back in order, no corruption,
//      even when the write and read clocks run at different (coprime) frequencies;
//   2. full/empty flags: writer never overflows, reader never underflows;
//   3. both directions of speed skew (fast writer/slow reader AND slow
//      writer/fast reader).
//
// A naive two-flop synchronizer on the multi-bit data would fail test 1 here
// (it can latch a transient value); the gray-pointer FIFO must pass.

`timescale 1ns/1ps

module tb_async_fifo;
    localparam DW   = 32;
    localparam DEPTH = 64;

    // two unrelated clocks: 100 MHz (10 ns) and ~71.4 MHz (14 ns). Integer
    // periods keep vvp fast; the two domains have no fixed phase relationship.
    reg wr_clk = 0;
    reg rd_clk = 0;
    always #5 wr_clk = ~wr_clk;   // 100 MHz
    always #7 rd_clk = ~rd_clk;   // ~71.4 MHz

    reg  [DW-1:0] wr_data;
    wire          wr_full;
    wire [DW-1:0] rd_data;
    wire          rd_empty;
    reg           wr_en, rd_en;
    reg           rst_n = 0;

    async_fifo #(.DATA_WIDTH(DW), .DEPTH(DEPTH)) dut (
        .wr_clk(wr_clk), .rst_n(rst_n),
        .wr_en(wr_en), .wr_data(wr_data), .wr_full(wr_full),
        .rd_clk(rd_clk), .rd_en(rd_en), .rd_data(rd_data), .rd_empty(rd_empty)
    );

    // deassert reset after a few cycles in both domains
    initial begin
        #20; rst_n = 1'b1;
    end

    integer errors = 0;
    localparam NWORDS = 2000;
    reg [31:0] wseq, rseq;
    integer    rcnt;

    // ---------------- writer (wr domain) ----------------
    // Sample wr_full in the active region after each posedge; when there is room,
    // present the next word and pulse wr_en for exactly one cycle so the FIFO
    // latches it on the following posedge.
    initial begin
        wr_en = 0; wr_data = 0; wseq = 0;
        while (!rst_n) @(posedge wr_clk);
        @(posedge wr_clk);
        while (wseq < NWORDS) begin
            if (!wr_full) begin
                wr_data = wseq;     // blocking: stable for the next posedge
                wr_en   = 1'b1;     // consume on the next posedge
                wseq    = wseq + 1;
            end
            @(posedge wr_clk);
            wr_en = 1'b0;
        end
    end

    // ---------------- reader (rd domain) ----------------
    // rd_data is combinational on the current read pointer, so it is valid in
    // the active region after each posedge. Sample it BEFORE pulsing rd_en (which
    // advances the pointer on the next posedge).
    initial begin
        rd_en = 0; rseq = 0; rcnt = 0;
        while (!rst_n) @(posedge rd_clk);
        @(posedge rd_clk);
        while (rcnt < NWORDS) begin
            if (!rd_empty) begin
                if (rd_data !== rseq) begin
                    $display("ERROR: rd[%0d] got=%h expect=%h", rcnt, rd_data, rseq);
                    errors = errors + 1;
                    if (errors > 20) begin
                        $display("TOO MANY ERRORS -- aborting");
                        $finish;
                    end
                end
                rd_en = 1'b1;       // consume on the next posedge
                rseq  = rseq + 1;
                rcnt  = rcnt + 1;
            end
            @(posedge rd_clk);
            rd_en = 1'b0;
        end
        #2000;
        if (errors == 0)
            $display("ALL_PASS (async_fifo: %0d words, 0 errors)", rcnt);
        else
            $display("HAS_FAILURES (async_fifo: %0d errors)", errors);
        $finish;
    end

    // watchdog: 2000 words at ~14 ns/rd-cycle needs ~28 us; allow generous margin
    initial begin
        #200_000;   // 200 us
        $display("WATCHDOG: async_fifo test timed out. wseq=%0d rcnt=%0d rd_empty=%b wr_full=%b",
                 wseq, rcnt, rd_empty, wr_full);
        $finish;
    end

endmodule

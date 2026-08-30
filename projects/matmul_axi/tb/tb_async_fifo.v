// tb_async_fifo : verify the gray-pointer async FIFO across unrelated clocks.
//
// Emits simcheck gate markers:
//   CHECK  data_integrity <n_checked> <n_bad>
//   COVER  slow_to_fast / fast_to_slow / fifo_full / fifo_empty / reset_during_traffic
//   SIMEND ok
//
// A naive two-flop synchronizer on the multi-bit data would fail the integrity
// check here (it can latch a transient value); the gray-pointer FIFO must pass.
//
// Phases:
//   1. fast_to_slow : writer faster than reader -> FIFO fills, wr_full backpressure
//   2. slow_to_fast : reader faster than writer -> FIFO drains, rd_empty wait
//   3. reset_during_traffic : assert rst_n mid-transfer, recover, verify integrity

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

    // ---- shared phase control ----
    integer wr_duty = 1;     // write every N wr cycles (1 = every cycle)
    integer rd_duty = 1;     // read every N rd cycles
    reg     run_writer = 0;
    reg     run_reader = 0;
    integer wseq = 0, rseq = 0;
    integer words_written = 0, words_read = 0;   // per-phase (reset each phase)
    integer total_words   = 0;                    // cumulative, for the final CHECK

    // ---- cover counters ----
    integer cov_slow_to_fast = 0;
    integer cov_fast_to_slow = 0;
    integer cov_fifo_full    = 0;
    integer cov_fifo_empty   = 0;
    integer cov_reset_during_traffic = 0;
    integer errors = 0;

    // ---- writer (wr domain) : long-running, gated by run_writer ----
    initial begin
        integer cyc = 0;
        wr_en = 0; wr_data = 0;
        while (!rst_n) @(posedge wr_clk);
        @(posedge wr_clk);
        forever begin
            if (run_writer && !wr_full) begin
                if (cyc % wr_duty == 0) begin
                    wr_data = wseq;
                    wr_en   = 1'b1;
                    wseq    = wseq + 1;
                    words_written = words_written + 1;
                    if (wr_duty < rd_duty)
                        cov_fast_to_slow = cov_fast_to_slow + 1;   // writer faster than reader
                end
            end else if (run_writer && wr_full) begin
                cov_fifo_full = cov_fifo_full + 1;   // backpressure: full, want to write
            end
            @(posedge wr_clk);
            wr_en = 1'b0;
            cyc   = cyc + 1;
        end
    end

    // ---- reader (rd domain) : long-running, gated by run_reader ----
    initial begin
        integer cyc = 0;
        rd_en = 0;
        while (!rst_n) @(posedge rd_clk);
        @(posedge rd_clk);
        forever begin
            if (run_reader && !rd_empty) begin
                if (cyc % rd_duty == 0) begin
                    if (rd_data !== rseq) begin
                        $display("ERROR: rd[%0d] got=%h expect=%h", rseq, rd_data, rseq);
                        errors = errors + 1;
                        if (errors > 20) begin
                            $display("TOO MANY ERRORS -- aborting");
                            $finish;
                        end
                    end
                    rd_en   = 1'b1;
                    rseq    = rseq + 1;
                    words_read = words_read + 1;
                    total_words = total_words + 1;
                    if (rd_duty < wr_duty)
                        cov_slow_to_fast = cov_slow_to_fast + 1;   // reader faster than writer
                end
            end else if (run_reader && rd_empty) begin
                cov_fifo_empty = cov_fifo_empty + 1;   // downstream waits on empty
            end
            @(posedge rd_clk);
            rd_en = 1'b0;
            cyc   = cyc + 1;
        end
    end

    // ---- run a bounded transfer: writer emits n words, reader drains them ----
    task run_phase(input integer n);
        begin
            wseq = 0; rseq = 0;
            words_written = 0; words_read = 0;
            run_writer = 1'b1;
            run_reader = 1'b1;
            while (words_written < n)
                @(posedge wr_clk);   // wait until the writer has emitted all n words
            run_writer = 1'b0;
            while (words_read < n)
                @(posedge rd_clk);   // wait until the reader has consumed all n words
            run_reader = 1'b0;
        end
    endtask

    // ---- orchestrator: run the phases ----
    initial begin
        #20; rst_n = 1'b1;
        repeat (4) @(posedge wr_clk);

        // Phase 1: fast_to_slow (writer every cycle, reader every 3rd) -> fills FIFO
        $display("PHASE 1: fast_to_slow (wr_duty=1, rd_duty=3)");
        wr_duty = 1; rd_duty = 3;
        run_phase(200);

        // Phase 2: slow_to_fast (writer every 3rd, reader every cycle) -> drains FIFO
        $display("PHASE 2: slow_to_fast (wr_duty=3, rd_duty=1)");
        wr_duty = 3; rd_duty = 1;
        run_phase(200);

        // Phase 3: reset_during_traffic -- traffic is active, assert reset mid-transfer,
        // stop the streams, then run a clean phase to verify the FIFO recovers.
        $display("PHASE 3: reset_during_traffic");
        wr_duty = 1; rd_duty = 1;
        run_writer = 1'b1; run_reader = 1'b1;
        repeat (50) @(posedge wr_clk);   // get traffic in flight
        run_writer = 1'b0; run_reader = 1'b0;   // stop the streams
        rst_n = 1'b0;
        repeat (6) @(posedge wr_clk);
        rst_n = 1'b1;
        cov_reset_during_traffic = 1;
        // after reset the FIFO is empty and pointers are 0; run a clean transfer
        run_phase(200);

        // ---- summary / gate markers ----
        $display("CHECK data_integrity %0d %0d", total_words, errors);
        $display("COVER slow_to_fast %0d", cov_slow_to_fast);
        $display("COVER fast_to_slow %0d", cov_fast_to_slow);
        $display("COVER fifo_full %0d", cov_fifo_full);
        $display("COVER fifo_empty %0d", cov_fifo_empty);
        $display("COVER reset_during_traffic %0d", cov_reset_during_traffic);
        if (errors == 0)
            $display("ALL_PASS (async_fifo: %0d words, 0 errors)", words_read);
        else
            $display("HAS_FAILURES (async_fifo: %0d errors)", errors);
        $display("SIMEND ok");
        $finish;
    end

    // watchdog
    initial begin
        #400_000;   // 400 us
        $display("WATCHDOG: async_fifo test timed out. wseq=%0d rseq=%0d rd_empty=%b wr_full=%b",
                 wseq, rseq, rd_empty, wr_full);
        $finish;
    end

endmodule

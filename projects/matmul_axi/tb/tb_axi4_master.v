// tb_axi4_master : verify axi4_master against a simple AXI slave memory model.
//
// Tests:
//   1. Write a known pattern, read it back, verify data integrity.
//   2. 4KB boundary crossing: start a read/write near a 4KB boundary and verify
//      the master splits into legal bursts (assertions catch violations).
//   3. Multiple outstanding reads: issue several reads and verify in-order return.
//   4. Large transfer spanning multiple 4KB pages.
//
// The memory model adds configurable latency to exercise the master's ability
// to handle non-zero response times.

`timescale 1ns/1ps

module tb_axi4_master #(
    parameter DATA_WIDTH = 256,
    parameter ADDR_WIDTH = 32,
    parameter ID_WIDTH   = 4,
    parameter MAX_RD_BURSTS = 16
);
    localparam BEAT_BYTES = DATA_WIDTH/8;   // 32 bytes per beat
    localparam RD_LEN_W   = 20;

    reg clk = 0;
    reg aresetn = 0;
    always #5 clk = ~clk;   // 100 MHz

    integer errors = 0;

    // ---- master command signals ----
    reg        rd_start = 0;
    reg [31:0] rd_addr  = 0;
    reg [RD_LEN_W-1:0] rd_len_bytes = 0;
    wire       rd_busy;
    wire       rd_data_valid;
    wire [DATA_WIDTH-1:0] rd_data;
    wire       rd_last;

    reg        wr_start = 0;
    reg [31:0] wr_addr  = 0;
    reg [RD_LEN_W-1:0] wr_len_bytes = 0;
    wire       wr_busy;
    reg        wr_data_in_valid = 0;
    reg [DATA_WIDTH-1:0] wr_data_in = 0;
    wire       wr_data_in_ready;
    wire       wr_done;

    // ---- AXI interconnect (master -> memory model) ----
    wire        m_awvalid, m_awready;
    wire [31:0] m_awaddr;
    wire [7:0]  m_awlen;
    wire [2:0]  m_awsize;
    wire [1:0]  m_awburst;
    wire [ID_WIDTH-1:0] m_awid;

    wire        m_wvalid, m_wready;
    wire [DATA_WIDTH-1:0] m_wdata;
    wire [BEAT_BYTES-1:0] m_wstrb;
    wire        m_wlast;

    wire        m_bvalid;
    wire        m_bready;
    wire [1:0]  m_bresp;
    wire [ID_WIDTH-1:0] m_bid;

    wire        m_arvalid, m_arready;
    wire [31:0] m_araddr;
    wire [7:0]  m_arlen;
    wire [2:0]  m_arsize;
    wire [1:0]  m_arburst;
    wire [ID_WIDTH-1:0] m_arid;

    wire        m_rvalid;
    wire        m_rready;
    wire [DATA_WIDTH-1:0] m_rdata;
    wire [1:0]  m_rresp;
    wire        m_rlast;
    wire [ID_WIDTH-1:0] m_rid;

    // ---- DUT ----
    axi4_master #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .ID_WIDTH(ID_WIDTH),
        .MAX_RD_BURSTS(MAX_RD_BURSTS),
        .RD_LEN_W(RD_LEN_W)
    ) dut (
        .aclk(clk), .aresetn(aresetn),
        .rd_start(rd_start), .rd_addr(rd_addr), .rd_len_bytes(rd_len_bytes),
        .rd_busy(rd_busy),
        .rd_data_valid(rd_data_valid), .rd_data(rd_data), .rd_last(rd_last),
        .rd_data_ready(1'b1),
        .wr_start(wr_start), .wr_addr(wr_addr), .wr_len_bytes(wr_len_bytes),
        .wr_busy(wr_busy),
        .wr_data_in_valid(wr_data_in_valid), .wr_data_in(wr_data_in),
        .wr_data_in_ready(wr_data_in_ready), .wr_done(wr_done),
        .m_axi_awvalid(m_awvalid), .m_axi_awready(m_awready),
        .m_axi_awaddr(m_awaddr), .m_axi_awlen(m_awlen),
        .m_axi_awsize(m_awsize), .m_axi_awburst(m_awburst), .m_axi_awid(m_awid),
        .m_axi_wvalid(m_wvalid), .m_axi_wready(m_wready),
        .m_axi_wdata(m_wdata), .m_axi_wstrb(m_wstrb), .m_axi_wlast(m_wlast),
        .m_axi_bvalid(m_bvalid), .m_axi_bready(m_bready),
        .m_axi_bresp(m_bresp), .m_axi_bid(m_bid),
        .m_axi_arvalid(m_arvalid), .m_axi_arready(m_arready),
        .m_axi_araddr(m_araddr), .m_axi_arlen(m_arlen),
        .m_axi_arsize(m_arsize), .m_axi_arburst(m_arburst), .m_axi_arid(m_arid),
        .m_axi_rvalid(m_rvalid), .m_axi_rready(m_rready),
        .m_axi_rdata(m_rdata), .m_axi_rresp(m_rresp),
        .m_axi_rlast(m_rlast), .m_axi_rid(m_rid)
    );

    // ---- AXI4 slave memory model (with latency, multi-outstanding reads) ----
    // A correct-enough AXI4 slave:
    //   * read: a FIFO of outstanding bursts. arready deasserts when full. R data
    //     is returned strictly in AR-accept order (single id => in-order), which
    //     matches what the master relies on. Each burst has an initial latency.
    //   * write: one burst at a time (the master only ever has one AW in flight).
    localparam MEM_LATENCY = 5;   // cycles from a burst becoming head to first R beat
    localparam MEM_SIZE_WORDS = 1 << 16;   // 64K beats = 2 MB
    localparam RDQ_DEPTH = MAX_RD_BURSTS + 2;   // room for all outstanding + slack
    localparam RDQ_AW    = $clog2(RDQ_DEPTH);

    reg [DATA_WIDTH-1:0] mem [0:MEM_SIZE_WORDS-1];
    integer mi;
    initial begin
        for (mi = 0; mi < MEM_SIZE_WORDS; mi = mi + 1)
            mem[mi] = {DATA_WIDTH{1'b0}};
    end

    // ================= write path (single outstanding burst) =================
    reg        s_aw_pending;
    reg [31:0] s_aw_addr;
    reg [7:0]  s_aw_len;
    reg [7:0]  s_w_beat;
    reg        s_w_done;

    assign m_awready = ~s_aw_pending;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            s_aw_pending <= 0;
            s_aw_addr    <= 0;
            s_aw_len     <= 0;
            s_w_beat     <= 0;
            s_w_done     <= 0;
        end else if (m_awvalid && m_awready) begin
            s_aw_pending <= 1;
            s_aw_addr    <= m_awaddr;
            s_aw_len     <= m_awlen;
            s_w_beat     <= 0;
            s_w_done     <= 0;
        end else if (s_w_done && m_bvalid && m_bready) begin
            s_aw_pending <= 0;
        end
    end

    assign m_wready = s_aw_pending;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            s_w_done <= 0;
        else begin
            if (m_wvalid && m_wready) begin
                mem[(s_aw_addr + s_w_beat * BEAT_BYTES) / BEAT_BYTES] <= m_wdata;
                if (s_w_beat == s_aw_len)
                    s_w_done <= 1;
                else
                    s_w_beat <= s_w_beat + 1;
            end
            if (s_w_done && m_bvalid && m_bready) begin
                s_w_done <= 0;
                s_w_beat <= 0;
            end
        end
    end
    assign m_bvalid = s_w_done;
    assign m_bresp  = 2'b00;
    assign m_bid    = m_awid;

`ifdef MEM_DBG
    integer dbg_wcnt = 0, dbg_awcnt = 0, dbg_arcnt = 0;
    always @(posedge clk) if (m_awvalid && m_awready) begin dbg_awcnt=dbg_awcnt+1; $display("t=%0t [MEM] AW#%0d addr=%h len=%0d", $time, dbg_awcnt, m_awaddr, m_awlen); end
    always @(posedge clk) if (m_arvalid && m_arready) begin dbg_arcnt=dbg_arcnt+1; $display("t=%0t [MEM] AR#%0d addr=%h len=%0d", $time, dbg_arcnt, m_araddr, m_arlen); end
    always @(posedge clk) if (m_wvalid && m_wready) begin
        dbg_wcnt=dbg_wcnt+1;
        if (dbg_wcnt<=2 || dbg_wcnt>=47)
            $display("t=%0t [MEM] W#%0d s_w_beat=%0d wlast=%b idx[63:32]=%h addr[31:0]=%h", $time, dbg_wcnt, s_w_beat, m_wlast, m_wdata[63:32], m_wdata[31:0]);
    end
    always @(posedge clk) if (m_bvalid && m_bready)
        $display("t=%0t [MEM] B accepted (s_w_done was 1)", $time);
    // periodic read-state dump to catch stalls
    integer dbg_rcnt = 0;
    always @(posedge clk) begin
        dbg_rcnt = dbg_rcnt + 1;
        if (dbg_rcnt % 200 == 0 && (m_arvalid || m_rvalid || rq_count != 0))
            $display("t=%0t [RD] arv=%b arr=%b rptr=%0d wptr=%0d cnt=%0d lat=%0d srb=%0d rv=%b rr=%b",
                     $time, m_arvalid, m_arready, rq_rptr, rq_wptr, rq_count,
                     rq_lat[rq_rptr], s_r_beat, m_rvalid, m_rready);
    end
`endif

    // ================= read path (FIFO of outstanding bursts) =================
    reg [ADDR_WIDTH-1:0] rq_addr [0:RDQ_DEPTH-1];
    reg [7:0]            rq_len  [0:RDQ_DEPTH-1];
    reg [MEM_LATENCY-1:0] rq_lat [0:RDQ_DEPTH-1];
    reg [RDQ_AW-1:0]     rq_wptr, rq_rptr;
    reg [RDQ_AW-1:0]     rq_count;
    reg [7:0]            s_r_beat;

    wire rq_full = (rq_count == RDQ_DEPTH[RDQ_AW-1:0]);
    assign m_arready = ~rq_full;

    // push accepted ARs into the queue
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            rq_wptr  <= 0;
            rq_rptr  <= 0;
            rq_count <= 0;
        end else begin
            if (m_arvalid && m_arready) begin
                rq_addr[rq_wptr] <= m_araddr;
                rq_len [rq_wptr] <= m_arlen;
                rq_lat [rq_wptr] <= MEM_LATENCY[MEM_LATENCY-1:0];
                rq_wptr  <= (rq_wptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count + 1;
            end
            // pop the head when its last beat is consumed
            if (m_rvalid && m_rready && m_rlast) begin
                rq_rptr  <= (rq_rptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count - 1;
            end
        end
    end

    // per-head latency countdown (introduces a gap before each burst's first beat)
    wire head_valid = (rq_count != 0);
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            ;
        else if (head_valid && rq_lat[rq_rptr] != 0)
            rq_lat[rq_rptr] <= rq_lat[rq_rptr] - 1;
    end

    wire head_ready = head_valid && (rq_lat[rq_rptr] == 0);
    // Widen s_r_beat before multiplying: in an 8-bit context, beat*32 overflows
    // for beat>=8 (e.g. 48*32=1536 -> 0), collapsing the read index to beat 0.
    wire [31:0] r_byte_off = {24'b0, s_r_beat} * BEAT_BYTES[31:0];
`ifdef RDBG
    integer rdbg = 0;
    always @(posedge clk) if (rdbg < 30 && m_rvalid) begin
        rdbg = rdbg + 1;
        $display("t=%0t [RDBG] rv=%b rr=%b s_r_beat=%0d rq_len=%0d idx=%0d data_idx=%h",
                 $time, m_rvalid, m_rready, s_r_beat, rq_len[rq_rptr],
                 (rq_addr[rq_rptr] + r_byte_off)/BEAT_BYTES, m_rdata[63:32]);
    end
`endif
    assign m_rvalid = head_ready;
    assign m_rdata  = mem[(rq_addr[rq_rptr] + r_byte_off) / BEAT_BYTES];
    assign m_rresp  = 2'b00;
    assign m_rlast  = head_ready && (s_r_beat == rq_len[rq_rptr]);
    assign m_rid    = m_arid;

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            s_r_beat <= 0;
        else if (m_rvalid && m_rready) begin
            if (s_r_beat == rq_len[rq_rptr])
                s_r_beat <= 0;
            else
                s_r_beat <= s_r_beat + 1;
        end
    end

    // ==================================================================
    // test tasks
    // ==================================================================
    reg [31:0] rd_beat_data;
    integer i, beat_cnt;

    // write `nbeats` beats of pattern starting at `addr`
    task do_write(input [31:0] addr, input integer nbeats);
        begin
            @(negedge clk);
            wr_addr = addr;
            wr_len_bytes = nbeats * BEAT_BYTES;
            wr_start = 1;
            @(negedge clk);
            wr_start = 0;
            // feed data
            for (i = 0; i < nbeats; i = i + 1) begin
                while (!wr_data_in_ready) @(negedge clk);
                wr_data_in_valid = 1;
                wr_data_in = {8'hA5, 8'h5A, 32'(i), 32'(addr)}; // pattern
                @(negedge clk);
                wr_data_in_valid = 0;
            end
            // wait for done
            while (!wr_done) @(posedge clk);
            @(negedge clk);
        end
    endtask

    // read `nbeats` beats starting at `addr`, verify against expected pattern.
    // rd_data_valid/rd_data are registered (update on posedge), so we sample at
    // the negedge where they're stable and aligned. The memory model inserts a
    // latency gap between bursts, so valid drops between them -- waiting for a
    // negedge with valid high handles both back-to-back beats and gaps.
    task do_read(input [31:0] addr, input integer nbeats, input integer expect_base);
        begin
            beat_cnt = 0;
            @(negedge clk);
            rd_addr = addr;
            rd_len_bytes = nbeats * BEAT_BYTES;
            rd_start = 1;
            @(negedge clk);
            rd_start = 0;
            for (beat_cnt = 0; beat_cnt < nbeats; beat_cnt = beat_cnt + 1) begin
                do @(negedge clk); while (!rd_data_valid);   // wait for this beat
                if (rd_data !== {8'hA5, 8'h5A, 32'(expect_base + beat_cnt), 32'(addr)}) begin
                    $display("ERROR: read beat %0d at addr=%h: got=%h exp={A5 5A %08x %08x}",
                             beat_cnt, addr, rd_data, expect_base + beat_cnt, addr);
                    errors = errors + 1;
                end
                if (rd_last && beat_cnt != nbeats - 1) begin
                    $display("ERROR: rd_last too early at beat %0d of %0d", beat_cnt, nbeats);
                    errors = errors + 1;
                end
            end
        end
    endtask

    // ==================================================================
    initial begin
        aresetn = 0;
        repeat (5) @(posedge clk);
        aresetn = 1;
        repeat (2) @(posedge clk);

        // watchdog: if the sim stalls, dump state and finish
        fork
            begin : wd
                integer wc;
                for (wc = 0; wc < 20000; wc = wc + 1) @(posedge clk);
                $display("WATCHDOG: stalled. wr_act=%b wr_st=%d rd_act=%b wv=%b rv=%b rqcnt=%0d s_awp=%b wr_wleft=%0d rd_issue=%0d",
                         dut.wr_active, dut.wr_state, dut.rd_active, m_wvalid, m_rvalid,
                         rq_count, s_aw_pending, dut.wr_w_left, dut.rd_issue_left);
                $finish;
            end
            begin : tests

        // ---- Test 1: basic write + read back ----
        $display("TEST 1: basic write+read (64 beats at 0x1000)");
        do_write(32'h0000_1000, 64);
        do_read(32'h0000_1000, 64, 0);
        $display("TEST 1 done (errors=%0d)", errors);

        // ---- Test 2: 4KB boundary crossing ----
        // Start at 0x0FC0 (beat-aligned, 64 bytes before the 4KB boundary at
        // 0x1000). 8 beats * 32 B = 256 B, ending at 0x10C0 -> crosses the page.
        // The master must split this into a 2-beat burst (0x0FC0..0x0FF0) plus a
        // 6-beat burst (0x1000..0x10C0), neither of which crosses 4KB.
        $display("TEST 2: 4KB boundary crossing (start=0xFC0, 8 beats)");
        do_write(32'h0000_0FC0, 8);
        do_read(32'h0000_0FC0, 8, 0);
        $display("TEST 2 done (errors=%0d)", errors);

        // ---- Test 3: large transfer spanning multiple pages ----
        // 256 beats * 32 bytes = 8192 bytes = 2 pages, starting at 0x2000
        $display("TEST 3: multi-page transfer (256 beats at 0x2000)");
        do_write(32'h0000_2000, 256);
        do_read(32'h0000_2000, 256, 0);
        $display("TEST 3 done (errors=%0d)", errors);

        // ---- Test 4: single beat read/write ----
        $display("TEST 4: single beat (1 beat at 0x5000)");
        do_write(32'h0000_5000, 1);
        do_read(32'h0000_5000, 1, 0);
        $display("TEST 4 done (errors=%0d)", errors);

        // ---- Test 5: write at a 4KB-aligned address with max burst ----
        // 256 beats = 8192 bytes starting at 0x3000 (page-aligned)
        $display("TEST 5: page-aligned max burst (256 beats at 0x3000)");
        do_write(32'h0000_3000, 256);
        do_read(32'h0000_3000, 256, 0);
        $display("TEST 5 done (errors=%0d)", errors);

        // ---- summary ----
        if (errors == 0)
            $display("ALL_PASS (axi4_master: %0d tests, 0 errors)", 5);
        else
            $display("HAS_FAILURES (%0d errors)", errors);
        $finish;
        end : tests
        join
    end

endmodule

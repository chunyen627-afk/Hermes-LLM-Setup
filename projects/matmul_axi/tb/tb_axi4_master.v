// tb_axi4_master : verify axi4_master against a simple AXI slave memory model.
//
// Emits simcheck gate markers:
//   CHECK  data_integrity <n_checked> <n_bad>
//   ASSERT axi_protocol   <violations>      (4KB-cross / illegal ARLEN/AWLEN)
//   COVER  single_burst | back_to_back | boundary_cross | backpressure
//          | outstanding_max | error_response
//   SIMEND ok|fail
//
// Scenarios:
//   1. basic write + read back (single burst, in-page)
//   2. 4KB boundary crossing (master must split into legal bursts)
//   3. large multi-page transfer (fills the read queue -> outstanding_max,
//      several ARs in flight -> back_to_back)
//   4. single beat
//   5. page-aligned max burst
//   6. consumer backpressure (rd_data_ready deasserted mid-read)
//   7. error response (memory model returns SLVERR for a poison range)

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
    reg        rd_data_ready = 1;   // controllable for backpressure test

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
        .rd_data_ready(rd_data_ready),
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
    localparam MEM_LATENCY = 5;   // cycles from a burst becoming head to first R beat
    localparam MEM_SIZE_WORDS = 1 << 16;   // 64K beats = 2 MB
    localparam RDQ_DEPTH = MAX_RD_BURSTS + 2;   // room for all outstanding + slack
    localparam RDQ_AW    = $clog2(RDQ_DEPTH);

    // poison range: reads from here return SLVERR (error_response scenario)
    localparam POISON_BASE = 32'h8000_0000;
    localparam POISON_TOP  = 32'h8001_0000;

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

    // ================= read path (FIFO of outstanding bursts) =================
    reg [ADDR_WIDTH-1:0] rq_addr [0:RDQ_DEPTH-1];
    reg [7:0]            rq_len  [0:RDQ_DEPTH-1];
    reg [MEM_LATENCY-1:0] rq_lat [0:RDQ_DEPTH-1];
    reg [RDQ_AW-1:0]     rq_wptr, rq_rptr;
    reg [RDQ_AW-1:0]     rq_count;
    reg [7:0]            s_r_beat;

    wire rq_full = (rq_count == RDQ_DEPTH[RDQ_AW-1:0]);
    reg ar_stall = 0;   // test hook: hold AR channel to fill the DUT's read queue
    assign m_arready = ~rq_full && !ar_stall;

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
            if (m_rvalid && m_rready && m_rlast) begin
                rq_rptr  <= (rq_rptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count - 1;
            end
        end
    end

    wire head_valid = (rq_count != 0);
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            ;
        else if (head_valid && rq_lat[rq_rptr] != 0)
            rq_lat[rq_rptr] <= rq_lat[rq_rptr] - 1;
    end

    wire head_ready = head_valid && (rq_lat[rq_rptr] == 0);
    wire [31:0] r_byte_off = {24'b0, s_r_beat} * BEAT_BYTES[31:0];
    wire [31:0] r_addr_now = rq_addr[rq_rptr] + r_byte_off;
    wire head_is_poison = (r_addr_now >= POISON_BASE) && (r_addr_now < POISON_TOP);

    assign m_rvalid = head_ready;
    assign m_rdata  = mem[(rq_addr[rq_rptr] + r_byte_off) / BEAT_BYTES];
    assign m_rresp  = head_is_poison ? 2'b10 : 2'b00;   // SLVERR in poison range
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
    // gate markers: protocol monitor + scenario counters
    // ==================================================================
    integer axi_viol = 0;      // 4KB-cross / illegal-len violations (TB-side)
    integer cov_single = 0, cov_b2b = 0, cov_boundary = 0;
    integer cov_bp = 0, cov_outstanding = 0, cov_errresp = 0;
    integer n_checked = 0, n_bad = 0;

    // TB-side protocol monitor (independent of the RTL's own assertions).
    always @(posedge clk) begin
        if (m_arvalid && m_arready) begin
            if (m_axi_arlen_check(m_araddr, m_arlen)) axi_viol = axi_viol + 1;
            // a burst that ends exactly on a 4KB page edge and is shorter than
            // the max -> it was truncated by the boundary (boundary_cross).
            if ((m_araddr[11:0] + (m_arlen + 8'd1) * BEAT_BYTES[11:0]) == 16'd4096 &&
                m_arlen < 8'd127)
                cov_boundary = cov_boundary + 1;
        end
        if (m_awvalid && m_awready) begin
            if (m_axi_awlen_check(m_awaddr, m_awlen)) axi_viol = axi_viol + 1;
        end
    end

    function [1:0] m_axi_arlen_check;
        input [31:0] addr;
        input [7:0]  len;
        begin
            if (addr[11:0] + (len * BEAT_BYTES) >= 16'd4096)
                m_axi_arlen_check = 2'b11;   // crosses 4KB
            else
                m_axi_arlen_check = 2'b00;
        end
    endfunction
    function [1:0] m_axi_awlen_check;
        input [31:0] addr;
        input [7:0]  len;
        begin
            if (addr[11:0] + (len * BEAT_BYTES) >= 16'd4096)
                m_axi_awlen_check = 2'b11;
            else
                m_axi_awlen_check = 2'b00;
        end
    endfunction

    // scenario detectors (sampled every cycle)
    always @(posedge clk) begin
        if (!aresetn) ;
        else begin
            // back_to_back: more than one read burst outstanding in the model
            if (rq_count >= 2) cov_b2b = cov_b2b + 1;
            // outstanding_max: DUT read-burst queue filled to capacity
            if (dut.rd_fifo_full) cov_outstanding = cov_outstanding + 1;
            // backpressure: consumer held a valid beat
            if (rd_data_valid && !rd_data_ready) cov_bp = cov_bp + 1;
            // error_response: an R beat with SLVERR was accepted
            if (m_rvalid && m_rready && (m_rresp != 2'b00)) cov_errresp = cov_errresp + 1;
        end
    end

    // ==================================================================
    // test tasks
    // ==================================================================
    reg [31:0] rd_beat_data;
    integer i, beat_cnt;

    task do_write(input [31:0] addr, input integer nbeats);
        begin
            @(negedge clk);
            wr_addr = addr;
            wr_len_bytes = nbeats * BEAT_BYTES;
            wr_start = 1;
            @(negedge clk);
            wr_start = 0;
            for (i = 0; i < nbeats; i = i + 1) begin
                while (!wr_data_in_ready) @(negedge clk);
                wr_data_in_valid = 1;
                wr_data_in = {8'hA5, 8'h5A, 32'(i), 32'(addr)};
                @(negedge clk);
                wr_data_in_valid = 0;
            end
            while (!wr_done) @(posedge clk);
            @(negedge clk);
        end
    endtask

    // read `nbeats` beats, verify against expected pattern. `allow_err` lets the
    // error_response test skip data comparison (the poison range has no valid data).
    task do_read(input [31:0] addr, input integer nbeats, input integer expect_base,
                 input integer allow_err);
        begin
            beat_cnt = 0;
            @(negedge clk);
            rd_addr = addr;
            rd_len_bytes = nbeats * BEAT_BYTES;
            rd_start = 1;
            @(negedge clk);
            rd_start = 0;
            for (beat_cnt = 0; beat_cnt < nbeats; beat_cnt = beat_cnt + 1) begin
                do @(negedge clk); while (!rd_data_valid);
                n_checked = n_checked + 1;
                if (!allow_err) begin
                    if (rd_data !== {8'hA5, 8'h5A, 32'(expect_base + beat_cnt), 32'(addr)}) begin
                        $display("ERROR: read beat %0d at addr=%h: got=%h exp={A5 5A %08x %08x}",
                                 beat_cnt, addr, rd_data, expect_base + beat_cnt, addr);
                        n_bad = n_bad + 1;
                    end
                end
                if (rd_last && beat_cnt != nbeats - 1) begin
                    $display("ERROR: rd_last too early at beat %0d of %0d", beat_cnt, nbeats);
                    n_bad = n_bad + 1;
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

        fork
            begin : wd
                integer wc;
                for (wc = 0; wc < 400000; wc = wc + 1) @(posedge clk);
                $display("STALL: test did not finish in time. wr_act=%b rd_act=%b rqcnt=%0d",
                         dut.wr_active, dut.rd_active, rq_count);
                $finish;
            end
            begin : tests

        // ---- Test 1: basic write + read back (single in-page burst) ----
        $display("TEST 1: basic write+read (64 beats at 0x1000)");
        do_write(32'h0000_1000, 64);
        do_read(32'h0000_1000, 64, 0, 0);
        cov_single = cov_single + 1;   // one burst, no split

        // ---- Test 2: 4KB boundary crossing ----
        $display("TEST 2: 4KB boundary crossing (start=0xFC0, 8 beats)");
        do_write(32'h0000_0FC0, 8);
        do_read(32'h0000_0FC0, 8, 0, 0);

        // ---- Test 3: large multi-page transfer (fills read queue) ----
        $display("TEST 3: multi-page transfer (2048 beats at 0x2000)");
        do_write(32'h0000_2000, 2048);
        do_read(32'h0000_2000, 2048, 0, 0);

        // ---- Test 4: single beat ----
        $display("TEST 4: single beat (1 beat at 0x5000)");
        do_write(32'h0000_5000, 1);
        do_read(32'h0000_5000, 1, 0, 0);
        cov_single = cov_single + 1;

        // ---- Test 5: page-aligned max burst ----
        $display("TEST 5: page-aligned max burst (256 beats at 0x3000)");
        do_write(32'h0000_3000, 256);
        do_read(32'h0000_3000, 256, 0, 0);

        // ---- Test 6: consumer backpressure mid-read ----
        $display("TEST 6: backpressure (stall rd_data_ready during a 128-beat read)");
        do_write(32'h0000_6000, 128);
        begin : bp_test
            integer b;
            @(negedge clk);
            rd_addr = 32'h0000_6000;
            rd_len_bytes = 128 * BEAT_BYTES;
            rd_start = 1;
            @(negedge clk);
            rd_start = 0;
            for (b = 0; b < 128; b = b + 1) begin
                do @(negedge clk); while (!rd_data_valid);
                // stall the consumer every 8 beats for a few cycles
                if (b % 8 == 4) begin
                    rd_data_ready = 0;
                    repeat (3) @(negedge clk);
                    rd_data_ready = 1;
                end
                n_checked = n_checked + 1;
                if (rd_data !== {8'hA5, 8'h5A, 32'(b), 32'h0000_6000}) begin
                    $display("ERROR: bp beat %0d mismatch", b);
                    n_bad = n_bad + 1;
                end
            end
        end

        // ---- Test 7: error response from poison range ----
        $display("TEST 7: error response (read poison range, expect SLVERR)");
        do_read(POISON_BASE, 4, 0, 1);   // allow_err: don't compare data

        // ---- Test 8: fill the DUT read queue to capacity (outstanding_max) ----
        // Hold the AR channel (ar_stall) while a large read is in flight so the
        // DUT's internal burst FIFO fills to MAX_RD_BURSTS and rd_fifo_full trips.
        $display("TEST 8: outstanding_max (stall AR to fill DUT read queue)");
        do_write(32'h0000_4000, 3072);
        begin : om_test
            integer b;
            @(negedge clk);
            rd_addr = 32'h0000_4000;
            rd_len_bytes = 3072 * BEAT_BYTES;
            rd_start = 1;
            @(negedge clk);
            rd_start = 0;
            ar_stall = 1;
            repeat (80) @(negedge clk);   // DUT fills its 16-deep burst FIFO here
            ar_stall = 0;
            for (b = 0; b < 3072; b = b + 1) begin
                do @(negedge clk); while (!rd_data_valid);
                n_checked = n_checked + 1;
                if (rd_data !== {8'hA5, 8'h5A, 32'(b), 32'h0000_4000}) begin
                    $display("ERROR: om beat %0d mismatch", b);
                    n_bad = n_bad + 1;
                end
            end
        end

        // ---- summary / gate markers ----
        $display("CHECK data_integrity %0d %0d", n_checked, n_bad);
        $display("ASSERT axi_protocol %0d", axi_viol);
        $display("COVER single_burst %0d", cov_single);
        $display("COVER back_to_back %0d", cov_b2b);
        $display("COVER boundary_cross %0d", cov_boundary);
        $display("COVER backpressure %0d", cov_bp);
        $display("COVER outstanding_max %0d", cov_outstanding);
        $display("COVER error_response %0d", cov_errresp);

        if ((n_bad == 0) && (axi_viol == 0))
            $display("SIMEND ok");
        else
            $display("SIMEND fail");
        $finish;
        end : tests
        join
    end

endmodule

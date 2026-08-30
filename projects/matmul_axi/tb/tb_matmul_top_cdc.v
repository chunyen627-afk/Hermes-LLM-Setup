// tb_matmul_top_cdc : end-to-end test of matmul_top with EXTERNAL_DATA=1 and
// X_FROM_XSPI=1. The activation vector x arrives on a SEPARATE clock domain
// (xspi_clk, ~71 MHz) and crosses into the aclk domain (~100 MHz) through the
// async FIFO. W is still read from DDR4 via the AXI master. The output is
// compared against the C-oracle expected.hex at D=288, N=288.
//
// This exercises the multi-bit CDC path: a two-flop synchronizer would be wrong
// here (the 16-bit element changes every xspi cycle); the async FIFO guarantees
// each element is written before it is read and never overwritten.

`timescale 1ns/1ps

module tb_matmul_top_cdc #(
    parameter D          = 288,
    parameter N          = 288,
    parameter DATA_WIDTH = 512,
    parameter SEED       = 42
);
    localparam BEAT_BYTES   = DATA_WIDTH/8;        // 64
    localparam BF16_PER_BEAT = DATA_WIDTH/16;      // 32
    localparam F32_PER_BEAT  = DATA_WIDTH/32;      // 16

    localparam W_BASE   = 32'h0001_0000;
    localparam X_BASE   = 32'h0010_0000;
    localparam OUT_BASE = 32'h0020_0000;

    localparam W_BEATS = (D*N*2) / BEAT_BYTES;     // 2592
    localparam X_BEATS = (N*2)   / BEAT_BYTES;     // 9
    localparam OUT_BEATS = (D*4) / BEAT_BYTES;     // 18

    localparam W_START = W_BASE/BEAT_BYTES;
    localparam X_START = X_BASE/BEAT_BYTES;
    localparam OUT_START = OUT_BASE/BEAT_BYTES;

    // two unrelated clocks: aclk ~100 MHz, xspi_clk ~71.4 MHz (no common period)
    reg clk  = 0;
    reg xclk = 0;
    reg aresetn = 0;
    reg xrst_n  = 0;
    always #5 clk = ~clk;   // 100 MHz
    always #7 xclk = ~xclk; // ~71.4 MHz

    integer errors = 0;

    // ---- gate cover counters ----
    integer cov_x_from_xspi        = 0;   // x elements loaded from the xSPI async FIFO
    integer cov_weights_from_ddr   = 0;   // W elements loaded from DDR4 via AXI master
    integer cov_result_written_back = 0;  // output words verified in DDR4 after store
    integer cov_status_polls       = 0;   // STATUS polls while waiting for done

    // ================= register-slave (s_axi) signals =================
    reg        s_awvalid = 0;
    wire       s_awready;
    reg [31:0] s_awaddr = 0;
    reg [7:0]  s_awlen = 0;
    reg [2:0]  s_awsize = 3'b010;
    reg [1:0]  s_awburst = 2'b01;
    reg [3:0]  s_awid = 0;

    reg        s_wvalid = 0;
    wire       s_wready;
    reg [31:0] s_wdata = 0;
    reg [3:0]  s_wstrb = 4'hF;

    wire       s_bvalid;
    reg        s_bready = 1;
    wire [1:0] s_bresp;
    wire [3:0] s_bid;

    reg        s_arvalid = 0;
    wire       s_arready;
    reg [31:0] s_araddr = 0;
    reg [7:0]  s_arlen = 0;
    reg [2:0]  s_arsize = 3'b010;
    reg [1:0]  s_arburst = 2'b01;
    reg [3:0]  s_arid = 0;

    wire       s_rvalid;
    reg        s_rready = 1;
    wire [31:0] s_rdata;
    wire [1:0] s_rresp;
    wire       s_rlast;
    wire [3:0] s_rid;

    // ================= DDR4 master (m_axi) signals =================
    wire       m_awvalid;
    wire       m_awready;
    wire [31:0] m_awaddr;
    wire [7:0]  m_awlen;
    wire [2:0]  m_awsize;
    wire [1:0]  m_awburst;
    wire [3:0]  m_awid;

    wire       m_wvalid;
    wire       m_wready;
    wire [DATA_WIDTH-1:0] m_wdata;
    wire [BEAT_BYTES-1:0] m_wstrb;
    wire       m_wlast;

    wire       m_bvalid;
    wire       m_bready;
    wire [1:0] m_bresp;
    wire [3:0] m_bid;

    wire       m_arvalid;
    wire       m_arready;
    wire [31:0] m_araddr;
    wire [7:0]  m_arlen;
    wire [2:0]  m_arsize;
    wire [1:0]  m_arburst;
    wire [3:0]  m_arid;

    wire       m_rvalid;
    wire       m_rready;
    wire [DATA_WIDTH-1:0] m_rdata;
    wire [1:0] m_rresp;
    wire       m_rlast;
    wire [3:0] m_rid;

    // ================= xSPI-side activation stream (separate clock domain) ====
    reg        xspi_x_valid = 0;
    reg [15:0] xspi_x_data  = 0;
    wire       xspi_x_full;

    wire [D*32-1:0] xout_vec;

    // ================= DUT =================
    matmul_top #(
        .D(D), .N(N), .DATA_WIDTH(DATA_WIDTH),
        .EXTERNAL_DATA(1), .X_FROM_XSPI(1), .XFIFO_DEPTH(512)
    ) dut (
        .aclk(clk), .aresetn(aresetn),
        .xspi_clk(xclk), .xspi_rst_n(xrst_n),
        .xspi_x_valid(xspi_x_valid), .xspi_x_data(xspi_x_data),
        .xspi_x_full(xspi_x_full),
        .s_axi_awvalid(s_awvalid), .s_axi_awready(s_awready),
        .s_axi_awaddr(s_awaddr), .s_axi_awlen(s_awlen),
        .s_axi_awsize(s_awsize), .s_axi_awburst(s_awburst), .s_axi_awid(s_awid),
        .s_axi_wvalid(s_wvalid), .s_axi_wready(s_wready),
        .s_axi_wdata(s_wdata), .s_axi_wstrb(s_wstrb),
        .s_axi_bvalid(s_bvalid), .s_axi_bready(s_bready),
        .s_axi_bresp(s_bresp), .s_axi_bid(s_bid),
        .s_axi_arvalid(s_arvalid), .s_axi_arready(s_arready),
        .s_axi_araddr(s_araddr), .s_axi_arlen(s_arlen),
        .s_axi_arsize(s_arsize), .s_axi_arburst(s_arburst), .s_axi_arid(s_arid),
        .s_axi_rvalid(s_rvalid), .s_axi_rready(s_rready),
        .s_axi_rdata(s_rdata), .s_axi_rresp(s_rresp),
        .s_axi_rlast(s_rlast), .s_axi_rid(s_rid),
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
        .m_axi_rlast(m_rlast), .m_axi_rid(m_rid),
        .xout_vec(xout_vec)
    );

    // ---- cover counters: x loaded from xSPI FIFO, W loaded from DDR4 ----
    always @(posedge clk) begin
        if (dut.x_load_valid)
            cov_x_from_xspi = cov_x_from_xspi + 1;
        if (dut.w_load_valid)
            cov_weights_from_ddr = cov_weights_from_ddr + 1;
    end

    // ================= DDR4 memory model (AXI4 slave, non-ideal) =================
    // Adds latency jitter + periodic AR/AW backpressure so the mem_latency_jitter /
    // mem_backpressure covers are genuinely exercised (real DDR4 row-hit vs row-miss
    // differ ~3x; MIG command queue full deasserts ready). Memory is a beat array.
    localparam MEM_LAT_BASE = 5;
    localparam MEM_SIZE_WORDS = 1 << 16;   // 64K beats = 4 MB
    localparam MAX_RD_BURSTS = 16;
    localparam RDQ_DEPTH = MAX_RD_BURSTS + 2;
    localparam RDQ_AW    = $clog2(RDQ_DEPTH);

    reg [DATA_WIDTH-1:0] mem [0:MEM_SIZE_WORDS-1];
    integer mi;
    initial for (mi = 0; mi < MEM_SIZE_WORDS; mi = mi + 1) mem[mi] = {DATA_WIDTH{1'b0}};

    // ---- backpressure generator: periodically deassert arready/awready ----
    reg [15:0] bp_cnt;
    reg        bp_ar, bp_aw;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            bp_cnt <= 0; bp_ar <= 0; bp_aw <= 0;
        end else begin
            bp_cnt <= bp_cnt + 1;
            if (bp_cnt == 16'd256) begin      // every 256 cycles, stall ~4 cycles
                bp_ar <= 1; bp_aw <= 1;
            end else if (bp_cnt == 16'd260) begin
                bp_ar <= 0; bp_aw <= 0;
            end
        end
    end

    // ---- write path (single outstanding burst) ----
    reg        s_aw_pending;
    reg [31:0] s_aw_addr;
    reg [7:0]  s_aw_len;
    reg [7:0]  s_w_beat;
    reg        s_w_done;

    assign m_awready = ~s_aw_pending && !bp_aw;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            s_aw_pending <= 0; s_aw_addr <= 0; s_aw_len <= 0; s_w_beat <= 0; s_w_done <= 0;
        end else if (m_awvalid && m_awready) begin
            s_aw_pending <= 1; s_aw_addr <= m_awaddr; s_aw_len <= m_awlen; s_w_beat <= 0; s_w_done <= 0;
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
                if (s_w_beat == s_aw_len) s_w_done <= 1;
                else s_w_beat <= s_w_beat + 1;
            end
            if (s_w_done && m_bvalid && m_bready) begin
                s_w_done <= 0; s_w_beat <= 0;
            end
        end
    end
    assign m_bvalid = s_w_done;
    assign m_bresp  = 2'b00;
    assign m_bid    = m_awid;

    // ---- read path (FIFO of outstanding bursts, with latency jitter) ----
    reg [31:0] rq_addr [0:RDQ_DEPTH-1];
    reg [7:0]  rq_len  [0:RDQ_DEPTH-1];
    reg [4:0]  rq_lat  [0:RDQ_DEPTH-1];   // widened for jitter (base..base+7)
    reg [RDQ_AW-1:0] rq_wptr, rq_rptr, rq_count;
    reg [7:0]  s_r_beat;

    wire rq_full = (rq_count == RDQ_DEPTH[RDQ_AW-1:0]);
    assign m_arready = ~rq_full && !bp_ar;

    // cover counters for the non-ideal memory behaviour
    integer cov_mem_jitter = 0;   // read bursts whose latency differs from the first
    integer cov_mem_bp     = 0;   // cycles with a pending request but ready low
    reg [15:0] ar_seq = 0;        // per-burst counter -> guaranteed latency variation
    reg        first_lat_set = 0;
    reg [4:0]  first_lat;

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            rq_wptr <= 0; rq_rptr <= 0; rq_count <= 0; ar_seq <= 0;
        end else begin
            if (m_arvalid && m_arready) begin
                // latency jitter: base + per-burst variation (5..12 cycles).
                // Real DDR4 row-hit (~15ns) vs row-miss (~45ns) differ ~3x.
                rq_addr[rq_wptr] <= m_araddr;
                rq_len [rq_wptr] <= m_arlen;
                rq_lat [rq_wptr] <= MEM_LAT_BASE + (ar_seq[2:0]);
                if (!first_lat_set) begin
                    first_lat     <= MEM_LAT_BASE + (ar_seq[2:0]);
                    first_lat_set <= 1;
                end else if ((MEM_LAT_BASE + (ar_seq[2:0])) != first_lat) begin
                    cov_mem_jitter = cov_mem_jitter + 1;
                end
                ar_seq   <= ar_seq + 1;
                rq_wptr  <= (rq_wptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count + 1;
            end
            if (m_rvalid && m_rready && m_rlast) begin
                rq_rptr  <= (rq_rptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count - 1;
            end
        end
    end

    // count backpressure: a request is pending but the channel is stalled
    always @(posedge clk) begin
        if ((m_arvalid && !m_arready) || (m_awvalid && !m_awready))
            cov_mem_bp = cov_mem_bp + 1;
    end

    wire head_valid = (rq_count != 0);
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) ;
        else if (head_valid && rq_lat[rq_rptr] != 0)
            rq_lat[rq_rptr] <= rq_lat[rq_rptr] - 1;
    end

    wire head_ready = head_valid && (rq_lat[rq_rptr] == 0);
    wire [31:0] r_byte_off = {24'b0, s_r_beat} * BEAT_BYTES[31:0];
    assign m_rvalid = head_ready;
    assign m_rdata  = mem[(rq_addr[rq_rptr] + r_byte_off) / BEAT_BYTES];
    assign m_rresp  = 2'b00;
    assign m_rlast  = head_ready && (s_r_beat == rq_len[rq_rptr]);
    assign m_rid    = m_arid;

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            s_r_beat <= 0;
        else if (m_rvalid && m_rready) begin
            if (s_r_beat == rq_len[rq_rptr]) s_r_beat <= 0;
            else s_r_beat <= s_r_beat + 1;
        end
    end

    // ================= source data =================
    reg [15:0] w_src [0:D*N-1];
    reg [15:0] x_src [0:N-1];
    integer wi, xi;
    initial begin
        $readmemh("w.hex", w_src);
        $readmemh("x.hex", x_src);
        // pack W into DDR4 (x is NOT in DDR4 this time; it comes via xSPI)
        for (wi = 0; wi < W_BEATS; wi = wi + 1) begin
            mem[W_START + wi] = {DATA_WIDTH{1'b0}};
            for (xi = 0; xi < BF16_PER_BEAT; xi = xi + 1)
                mem[W_START + wi][xi*16 +: 16] = w_src[wi*BF16_PER_BEAT + xi];
        end
    end

    // ================= register write/read helpers =================
    task reg_write(input [31:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            s_awvalid = 1; s_awaddr = addr; s_awlen = 0; s_awsize = 3'b010; s_awburst = 2'b01;
            @(negedge clk);
            s_wvalid = 1; s_wdata = data; s_wstrb = 4'hF;
            do @(posedge clk); while (!(s_bvalid && s_bready));
            @(negedge clk);
            s_wvalid = 0; s_awvalid = 0;
        end
    endtask

    task reg_read(input [31:0] addr, output [31:0] data);
        begin
            @(negedge clk);
            s_arvalid = 1; s_araddr = addr; s_arlen = 0; s_arsize = 3'b010; s_arburst = 2'b01;
            do @(posedge clk); while (!(s_rvalid && s_rready));
            data = s_rdata;
            @(negedge clk);
            s_arvalid = 0;
        end
    endtask

    // ================= expected output =================
    reg [31:0] expected [0:D-1];
    integer ei;
    initial $readmemh("expected.hex", expected);

    // ==================================================================
    // xSPI streamer: push all N elements of x onto the xspi clock domain.
    // Runs on xclk, gated by ~xspi_x_full so no element is ever dropped.
    // The async FIFO (depth 512 > 288) holds them until L_LOAD_X consumes
    // them in the aclk domain.
    integer xs;
    initial begin
        xspi_x_valid = 0; xspi_x_data = 0; xs = 0;
        while (!xrst_n) @(posedge xclk);
        repeat (2) @(posedge xclk);
        while (xs < N) begin
            if (!xspi_x_full) begin
                xspi_x_valid = 1;
                xspi_x_data  = x_src[xs];
                xs = xs + 1;
            end
            @(posedge xclk);
        end
        // deassert after a couple of cycles so the last write settles
        repeat (3) @(posedge xclk);
        xspi_x_valid = 0;
    end

    // ==================================================================
    initial begin
        aresetn = 0; xrst_n = 0;
        repeat (5) @(posedge clk);
        aresetn = 1;
        xrst_n  = 1;
        repeat (2) @(posedge clk);

        // watchdog
        fork
            begin : wd
                integer wc;
                for (wc = 0; wc < 400000; wc = wc + 1) @(posedge clk);
                $display("WATCHDOG: stalled. lstate=%d rd_busy=%b wr_busy=%b core_done=%b xfull=%b",
                         dut.lstate, dut.rd_busy, dut.wr_busy, dut.core_done, xspi_x_full);
                $finish;
            end
            begin : tests
                // configure the register map
                reg_write(32'h00, 32'd0);          // CTRL = 0
                reg_write(32'h08, W_BASE);         // W_BASE
                reg_write(32'h0C, X_BASE);         // X_BASE (unused in X_FROM_XSPI mode)
                reg_write(32'h10, OUT_BASE);       // OUT_BASE
                reg_write(32'h14, D);              // D
                reg_write(32'h18, N);              // N

                // start the job (x is already in the async FIFO)
                reg_write(32'h00, 32'd1);          // CTRL.start = 1

                // wait for done (STATUS bit0)
                begin : waitdone
                    reg [31:0] st;
                    integer timeout;
                    timeout = 0;
                    do begin
                        reg_read(32'h04, st);
                        cov_status_polls = cov_status_polls + 1;
                        if (st[0]) disable waitdone;
                        timeout = timeout + 1;
                        if (timeout > 60000) begin $display("ERROR: did not finish in time (lstate=%d)", dut.lstate); errors=errors+1; disable waitdone; end
                    end while (1);
                end

                // sanity: verify a few x elements landed in the core correctly
                begin : loadchk
                    $display("DBG x_mem[0]=%h (src %h)  x_mem[%0d]=%h (src %h)",
                             dut.u_core.x_mem[0], x_src[0], N-1, dut.u_core.x_mem[N-1], x_src[N-1]);
                end

                // read back the output from DDR4 and compare to expected
                begin : checkout
                    integer bi, ei2;
                    reg [31:0] word;
                    for (bi = 0; bi < OUT_BEATS; bi = bi + 1) begin
                        for (ei2 = 0; ei2 < F32_PER_BEAT; ei2 = ei2 + 1) begin
                            word = mem[OUT_START + bi][ei2*32 +: 32];
                            if (word !== expected[bi*F32_PER_BEAT + ei2]) begin
                                if (errors < 10)
                                    $display("ERROR: xout[%0d] = %h, expected %h",
                                             bi*F32_PER_BEAT + ei2, word, expected[bi*F32_PER_BEAT + ei2]);
                                errors = errors + 1;
                            end else begin
                                cov_result_written_back = cov_result_written_back + 1;
                            end
                        end
                    end
                end

                // ---- gate markers ----
                $display("CHECK end_to_end_match %0d %0d", D, errors);
                $display("COVER cdc_crossing %0d", cov_x_from_xspi);
                $display("COVER mem_latency_jitter %0d", cov_mem_jitter);
                $display("COVER mem_backpressure %0d", cov_mem_bp);

                if (errors == 0)
                    $display("ALL_PASS (CDC e2e D=%0d N=%0d: %0d outputs match C oracle, x via async FIFO)", D, N, D);
                else
                    $display("HAS_FAILURES (%0d errors)", errors);
                $display("SIMEND ok");
                $finish;
            end : tests
        join
    end

endmodule

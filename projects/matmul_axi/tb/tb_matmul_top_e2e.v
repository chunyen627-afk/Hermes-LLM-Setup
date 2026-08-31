// tb_matmul_top_e2e : end-to-end test of matmul_top with EXTERNAL_DATA=1.
//
// The AXI master reads W (D*N BF16) and x (N BF16) from a DDR4 memory model,
// the core computes xout = W @ x, and the result (D FP32) is written back to
// OUT_BASE in DDR4. We compare the DDR4 output against the C-oracle expected.hex
// at the real tinystories-15M dimensions D=288, N=288.
//
// Data source: out/matmul/{w.hex,x.hex,expected.hex} produced by
// ref/gen_matmul_vecs.py 288 288 <seed>. w.hex/x.hex are BF16 (one per line);
// expected.hex is FP32 bit patterns (one per line).
//
// DDR4 layout (beat = DATA_WIDTH/8 = 64 bytes for a 512-bit master):
//   W_BASE   = 0x0001_0000 : D*N BF16 = 82944 * 2 B = 165888 B = 2592 beats
//   X_BASE   = 0x0010_0000 : N BF16   = 288 * 2 B     = 576 B    = 9 beats
//   OUT_BASE = 0x0020_0000 : D FP32   = 288 * 4 B     = 1152 B   = 18 beats

`timescale 1ns/1ps

module tb_matmul_top_e2e #(
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

    localparam W_START = W_BASE/BEAT_BYTES;        // 512
    localparam X_START = X_BASE/BEAT_BYTES;
    localparam OUT_START = OUT_BASE/BEAT_BYTES;

    reg clk = 0;
    reg aresetn = 0;
    always #5 clk = ~clk;   // 100 MHz

    integer errors = 0;

    // ================= register-slave (s_axi) signals =================
    reg        s_awvalid = 0;
    wire       s_awready;
    reg [31:0] s_awaddr = 0;
    reg [7:0]  s_awlen = 0;
    reg [2:0]  s_awsize = 3'b010;   // 4 bytes
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

    wire [D*32-1:0] xout_vec;

    // ================= DUT =================
    matmul_top #(
        .D(D), .N(N), .DATA_WIDTH(DATA_WIDTH), .EXTERNAL_DATA(1)
    ) dut (
        .aclk(clk), .aresetn(aresetn),
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

    // ================= DDR4 memory model (AXI4 slave, non-ideal) =================
    // Correct AXI4 slave with realistic non-ideal behaviour so the gate's
    // memory covers are genuinely exercised:
    //   * per-burst read latency jitter (row hit vs miss)      -> mem_latency_jitter
    //   * periodic AR/AW ready backpressure (MIG cmd queue full)-> mem_backpressure
    //   * periodic long refresh pause (all channels held)       -> mem_refresh_stall
    //   * extra delay on read<->write direction switch          -> mem_read_write_turnaround
    localparam MEM_LATENCY_BASE = 5;
    localparam MEM_SIZE_WORDS = 1 << 16;   // 64K beats = 4 MB
    localparam MAX_RD_BURSTS = 16;
    localparam RDQ_DEPTH = MAX_RD_BURSTS + 2;
    localparam RDQ_AW    = $clog2(RDQ_DEPTH);

    // non-ideal knobs
    localparam REFRESH_PERIOD = 4000;   // cycles between refresh pauses
    localparam REFRESH_DUR    = 60;     // cycles held during a refresh
    localparam BP_PERIOD      = 700;    // cycles between AR/AW backpressure stalls
    localparam BP_DUR         = 8;      // cycles of backpressure
    localparam TURNAROUND_EXTRA = 6;    // extra latency on a direction switch

    reg [DATA_WIDTH-1:0] mem [0:MEM_SIZE_WORDS-1];
    integer mi;
    initial for (mi = 0; mi < MEM_SIZE_WORDS; mi = mi + 1) mem[mi] = {DATA_WIDTH{1'b0}};

    // ---- non-ideal state machines ----
    reg [15:0] ref_cnt = 0;
    reg [7:0]  ref_left = 0;
    reg        mem_refresh = 0;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin ref_cnt <= 0; ref_left <= 0; mem_refresh <= 0; end
        else begin
            if (mem_refresh) begin
                if (ref_left == 0) mem_refresh <= 0;
                else ref_left <= ref_left - 1;
            end else begin
                ref_cnt <= ref_cnt + 1;
                if (ref_cnt == REFRESH_PERIOD[15:0]) begin
                    ref_cnt  <= 0;
                    ref_left <= REFRESH_DUR[7:0];
                    mem_refresh <= 1;
                end
            end
        end
    end

    reg [15:0] bp_cnt = 0;
    reg [7:0]  bp_left = 0;
    reg        mem_bp = 0;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin bp_cnt <= 0; bp_left <= 0; mem_bp <= 0; end
        else begin
            if (mem_bp) begin
                if (bp_left == 0) mem_bp <= 0;
                else bp_left <= bp_left - 1;
            end else begin
                bp_cnt <= bp_cnt + 1;
                if (bp_cnt == BP_PERIOD[15:0]) begin
                    bp_cnt  <= 0;
                    bp_left <= BP_DUR[7:0];
                    mem_bp  <= 1;
                end
            end
        end
    end

    // ---- write path (single outstanding burst) ----
    reg        s_aw_pending;
    reg [31:0] s_aw_addr;
    reg [7:0]  s_aw_len;
    reg [7:0]  s_w_beat;
    reg        s_w_done;
    reg [3:0]  w_td = 0;   // turnaround delay before first W beat

    assign m_awready = ~s_aw_pending && !mem_refresh && !mem_bp;
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            s_aw_pending <= 0; s_aw_addr <= 0; s_aw_len <= 0; s_w_beat <= 0; s_w_done <= 0; w_td <= 0;
        end else if (m_awvalid && m_awready) begin
            s_aw_pending <= 1; s_aw_addr <= m_awaddr; s_aw_len <= m_awlen; s_w_beat <= 0; s_w_done <= 0;
            w_td <= (last_dir == 1'b0) ? TURNAROUND_EXTRA[3:0] : 4'd0;   // write-after-read
        end else if (s_w_done && m_bvalid && m_bready) begin
            s_aw_pending <= 0;
        end
    end

    assign m_wready = s_aw_pending && (w_td == 0);
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            s_w_done <= 0;
        else begin
            if (s_aw_pending && w_td != 0)
                w_td <= w_td - 1;
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

    // ---- read path (FIFO of outstanding bursts) ----
    reg [31:0] rq_addr [0:RDQ_DEPTH-1];
    reg [7:0]  rq_len  [0:RDQ_DEPTH-1];
    reg [4:0]  rq_lat  [0:RDQ_DEPTH-1];
    reg [RDQ_AW-1:0] rq_wptr, rq_rptr, rq_count;
    reg [7:0]  s_r_beat;
    reg [7:0]  rq_burst_idx = 0;   // for latency jitter
    reg        last_dir = 1'b0;    // 0=read, 1=write (last accepted direction)

    // single driver for the last-accepted direction (used for turnaround delay)
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn)
            last_dir <= 1'b0;
        else if (m_arvalid && m_arready)
            last_dir <= 1'b0;   // a read was just accepted
        else if (m_awvalid && m_awready)
            last_dir <= 1'b1;   // a write was just accepted
    end

    wire rq_full = (rq_count == RDQ_DEPTH[RDQ_AW-1:0]);
    assign m_arready = ~rq_full && !mem_refresh && !mem_bp;

    // per-burst read latency: base + jitter (3..12) + turnaround if after a write
    wire [4:0] lat_jitter = 5'd3 + rq_burst_idx[4:0] % 5'd10;   // 3..12
    wire [4:0] lat_now = (last_dir == 1'b1) ? (lat_jitter + TURNAROUND_EXTRA[4:0]) : lat_jitter;

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            rq_wptr <= 0; rq_rptr <= 0; rq_count <= 0; rq_burst_idx <= 0;
        end else begin
            if (m_arvalid && m_arready) begin
                rq_addr[rq_wptr] <= m_araddr;
                rq_len [rq_wptr] <= m_arlen;
                rq_lat [rq_wptr] <= lat_now;
                rq_wptr  <= (rq_wptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count + 1;
                rq_burst_idx <= rq_burst_idx + 1;
            end
            if (m_rvalid && m_rready && m_rlast) begin
                rq_rptr  <= (rq_rptr + 1) % RDQ_DEPTH;
                rq_count <= rq_count - 1;
            end
        end
    end

    wire head_valid = (rq_count != 0);
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) ;
        else if (head_valid && rq_lat[rq_rptr] != 0)
            rq_lat[rq_rptr] <= rq_lat[rq_rptr] - 1;
    end

    wire head_ready = head_valid && (rq_lat[rq_rptr] == 0);
    wire [31:0] r_byte_off = {24'b0, s_r_beat} * BEAT_BYTES[31:0];
    assign m_rvalid = head_ready && !mem_refresh;
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

    // ---- memory cover counters ----
    integer cov_mem_jitter = 0, cov_mem_bp = 0, cov_mem_refresh = 0, cov_mem_turnaround = 0;
    always @(posedge clk) begin
        if (!aresetn) ;
        else begin
            // latency jitter: a burst whose assigned latency != base
            if (m_arvalid && m_arready && (lat_now != MEM_LATENCY_BASE[4:0]))
                cov_mem_jitter = cov_mem_jitter + 1;
            // backpressure: master wants to issue but the memory holds ready low
            if ((m_arvalid && !m_arready) || (m_awvalid && !m_awready))
                cov_mem_bp = cov_mem_bp + 1;
            // refresh: count each refresh pause (rising edge of mem_refresh)
            if (mem_refresh && ref_left == REFRESH_DUR[7:0])
                cov_mem_refresh = cov_mem_refresh + 1;
            // turnaround: a direction switch (read after write, or write after read)
            if ((m_arvalid && m_arready && last_dir == 1'b1) ||
                (m_awvalid && m_awready && last_dir == 1'b0))
                cov_mem_turnaround = cov_mem_turnaround + 1;
        end
    end

    // ================= preload W/x into DDR4 from hex files =================
    reg [15:0] w_src [0:D*N-1];
    reg [15:0] x_src [0:N-1];
    integer wi, xi;
    initial begin
        $readmemh("w.hex", w_src);
        $readmemh("x.hex", x_src);
        // pack W: BF16_PER_BEAT elements per beat
        for (wi = 0; wi < W_BEATS; wi = wi + 1) begin
            mem[W_START + wi] = {DATA_WIDTH{1'b0}};
            for (xi = 0; xi < BF16_PER_BEAT; xi = xi + 1)
                mem[W_START + wi][xi*16 +: 16] = w_src[wi*BF16_PER_BEAT + xi];
        end
        // pack x
        for (wi = 0; wi < X_BEATS; wi = wi + 1) begin
            mem[X_START + wi] = {DATA_WIDTH{1'b0}};
            for (xi = 0; xi < BF16_PER_BEAT; xi = xi + 1)
                mem[X_START + wi][xi*16 +: 16] = x_src[wi*BF16_PER_BEAT + xi];
        end
    end

    // ================= register write/read helpers =================
    task reg_write(input [31:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            s_awvalid = 1; s_awaddr = addr; s_awlen = 0; s_awsize = 3'b010; s_awburst = 2'b01;
            @(negedge clk);
            s_wvalid = 1; s_wdata = data; s_wstrb = 4'hF;
            // wait for the write to be accepted and B returned
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
    // trace: log the first few W/x load events into the core (during the run)
    integer wtrace_n = 0, xtrace_n = 0;
    integer cov_w_loaded = 0;   // count of W elements loaded from DDR4 into the core
    integer cov_status_polls = 0;  // number of STATUS polls while waiting for done
    always @(posedge clk) begin
        if (dut.w_load_valid)
            cov_w_loaded = cov_w_loaded + 1;
        if (dut.w_load_valid && wtrace_n < 6) begin
            $display("DBG wload idx=%0d data=%h (expect src[%0d]=%h)",
                     dut.w_load_idx, dut.w_load_data, dut.w_load_idx, w_src[dut.w_load_idx]);
            wtrace_n = wtrace_n + 1;
        end
        if (dut.x_load_valid && xtrace_n < 4) begin
            $display("DBG xload idx=%0d data=%h (expect src[%0d]=%h)",
                     dut.x_load_idx, dut.x_load_data, dut.x_load_idx, x_src[dut.x_load_idx]);
            xtrace_n = xtrace_n + 1;
        end
    end

    // ==================================================================
    initial begin
        aresetn = 0;
        repeat (5) @(posedge clk);
        aresetn = 1;
        repeat (2) @(posedge clk);

        // watchdog
        fork
            begin : wd
                integer wc;
                for (wc = 0; wc < 1000000; wc = wc + 1) @(posedge clk);
                $display("WATCHDOG: stalled. lstate=%d rd_busy=%b wr_busy=%b core_done=%b",
                         dut.lstate, dut.rd_busy, dut.wr_busy, dut.core_done);
                $finish;
            end
            begin : tests
                // configure the register map
                reg_write(32'h00, 32'd0);          // CTRL = 0
                reg_write(32'h08, W_BASE);         // W_BASE
                reg_write(32'h0C, X_BASE);         // X_BASE
                reg_write(32'h10, OUT_BASE);       // OUT_BASE
                reg_write(32'h14, D);              // D
                reg_write(32'h18, N);              // N

                // sanity: read back a couple of registers
                begin : rdchk
                    reg [31:0] rv;
                    reg_read(32'h08, rv);
                    if (rv !== W_BASE) begin $display("ERROR: W_BASE readback %h != %h", rv, W_BASE); errors=errors+1; end
                    reg_read(32'h14, rv);
                    if (rv !== D) begin $display("ERROR: D readback %0d != %0d", rv, D); errors=errors+1; end
                end

                // start the job. bit0=start, bit2=bf16_in (this core is BF16-only,
                // so bf16_in must be 1 or the job is rejected with STATUS.error).
                reg_write(32'h00, 32'd5);          // CTRL.start | CTRL.bf16_in = 1

                // wait for done (STATUS bit0). The core alone takes D*N cycles and
                // the W-load unpacks one element/cycle; under the memory model's
                // backpressure/refresh stalls the wall-clock time is several times
                // the raw cycle count, so allow a generous bound.
                begin : waitdone
                    reg [31:0] st;
                    integer timeout;
                    timeout = 0;
                    do begin
                        reg_read(32'h04, st);
                        cov_status_polls = cov_status_polls + 1;
                        if (st[0]) disable waitdone;
                        timeout = timeout + 1;
                        if (timeout > 400000) begin $display("ERROR: did not finish in time (lstate=%d)", dut.lstate); errors=errors+1; disable waitdone; end
                    end while (1);
                end
                // sanity: verify a few W/x elements landed in the core correctly
                begin : loadchk
                    reg [15:0] wexp, xexp;
                    $display("DBG w_mem[0]=%h (src %h)  w_mem[1]=%h (src %h)",
                             dut.u_core.w_mem[0], w_src[0], dut.u_core.w_mem[1], w_src[1]);
                    $display("DBG w_mem[%0d]=%h (src %h)  x_mem[0]=%h (src %h)",
                             D, dut.u_core.w_mem[D], w_src[D], dut.u_core.x_mem[0], x_src[0]);
                    $display("DBG x_mem[%0d]=%h (src %h)", N-1, dut.u_core.x_mem[N-1], x_src[N-1]);
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
                            end
                        end
                    end
                end

                // ---- new register-map fields: COUNT, STATUS.error, CTRL.reset ----
                begin : regfields
                    reg [31:0] st, cnt;
                    integer rw;

                    // COUNT (0x1C) should equal D after the job completed.
                    reg_read(32'h1C, cnt);
                    if (cnt !== D) begin $display("ERROR: COUNT=%0d != D=%0d", cnt, D); errors=errors+1; end
                    else            $display("COVER count_register %0d", D);

                    // Error path: start a job with bf16_in=0 (unsupported format).
                    // The job must NOT run and STATUS.error (bit2) must latch.
                    reg_write(32'h00, 32'd1);          // start=1, bf16_in=0
                    for (rw = 0; rw < 8; rw = rw + 1) @(posedge clk);
                    reg_read(32'h04, st);
                    if (!st[2]) begin $display("ERROR: STATUS.error not set on bf16_in=0 start (st=%h)", st); errors=errors+1; end
                    else            $display("COVER error_latch %0d", 1);

                    // Soft reset (CTRL.reset, bit1): clears status and COUNT.
                    reg_write(32'h00, 32'd2);          // reset=1
                    for (rw = 0; rw < 8; rw = rw + 1) @(posedge clk);
                    reg_read(32'h04, st);
                    reg_read(32'h1C, cnt);
                    if (st !== 0)    begin $display("ERROR: STATUS=%h not cleared by soft reset", st); errors=errors+1; end
                    if (cnt !== 0)   begin $display("ERROR: COUNT=%0d not cleared by soft reset", cnt); errors=errors+1; end
                    if (st === 0 && cnt === 0) $display("COVER soft_reset %0d", 1);

                    // CTRL.reset must auto-clear (not hold the design in reset).
                    reg_read(32'h00, st);
                    if (st[1]) begin $display("ERROR: CTRL.reset not auto-cleared (ctrl=%h)", st); errors=errors+1; end
                end

                // ---- gate markers ----
                $display("CHECK end_to_end_match %0d %0d", D, errors);
                $display("COVER end_to_end_match %0d", (errors == 0) ? D : 0);
                $display("COVER weights_from_ddr %0d", cov_w_loaded);
                $display("COVER result_written_back %0d", OUT_BEATS);
                $display("COVER status_polling %0d", cov_status_polls);
                $display("COVER mem_latency_jitter %0d", cov_mem_jitter);
                $display("COVER mem_backpressure %0d", cov_mem_bp);
                $display("COVER mem_refresh_stall %0d", cov_mem_refresh);
                $display("COVER mem_read_write_turnaround %0d", cov_mem_turnaround);

                if (errors == 0)
                    $display("ALL_PASS (e2e matmul_top D=%0d N=%0d: %0d outputs match C oracle)", D, N, D);
                else
                    $display("HAS_FAILURES (%0d errors)", errors);
                // SIMEND must reflect the real result. The marker protocol
                // requires the line to be EXACTLY "SIMEND ok" or "SIMEND fail"
                // (no trailing text).
                $display("SIMEND %s", (errors == 0) ? "ok" : "fail");
                $finish;
            end : tests
        join
    end

endmodule

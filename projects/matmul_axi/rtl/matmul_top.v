// matmul_top : register-mapped AXI4 slave front-end for the BF16 matmul core.
//
// Register map (32-bit, word-addressed):
//   0x00 CTRL      bit0 start, bit1 reset (soft), bit2 bf16_in (input format)
//   0x04 STATUS    bit0 done, bit1 busy, bit2 error (sticky)
//   0x08 W_BASE    DDR4 base address of the weight buffer (D x N BF16)
//   0x0C X_BASE    DDR4 base address of the activation vector (N BF16)
//   0x10 OUT_BASE  DDR4 base address for the FP32 output vector (D floats)
//   0x14 D         number of rows / outputs
//   0x18 N         reduction length
//   0x1C COUNT     number of outputs produced by the last completed job
//
// CTRL semantics:
//   start  : pulse a job (auto-cleared when the job finishes).
//   reset  : soft reset -- clears the load FSM, status and counters for one
//            cycle, then auto-clears. Lets the driver recover from a stuck or
//            errored job without a full aresetn.
//   bf16_in: input format select. The datapath is BF16-only, so 1 (BF16) is the
//            supported value; requesting 0 (FP32) raises STATUS.error.
//
// Two data paths, selected by EXTERNAL_DATA:
//   EXTERNAL_DATA=0 (default): W/x are loaded into the core via $readmemh and
//     xout is read back through the xout_vec port. The AXI master is present but
//     idle. This is what tb_matmul_top.v exercises.
//   EXTERNAL_DATA=1: a load/store FSM streams W and x from DDR4 (via the AXI
//     master) into the core's element-load ports, runs the core, then writes the
//     FP32 xout vector back to OUT_BASE in DDR4. This is the real accelerator
//     path exercised by tb_matmul_top_e2e.v.
//
// The AXI master (m_axi_*) is the DDR4-facing interface; the register slave
// (s_axi_*) is the xSPI/processor-facing control interface. In the full system
// these sit in different clock domains and are bridged by CDC logic (Block 3).

module matmul_top #(
    parameter D           = 288,
    parameter N           = 288,
    parameter DATA_WIDTH  = 512,      // AXI master data width (bits) -> 32 BF16/beat @512
    parameter EXTERNAL_DATA = 0,      // 1: stream W/x/xout through the AXI master
    parameter X_FROM_XSPI   = 0,      // 1: x arrives on the xSPI clock domain (CDC)
    parameter XFIFO_DEPTH   = 512     // async FIFO depth for the x stream (power of two)
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    // ---- xSPI-side activation stream (different clock domain, CDC via async FIFO) ----
    // Present N BF16 elements one per cycle on xspi_clk. Only used when
    // X_FROM_XSPI=1; the async_fifo bridges them into the aclk domain.
    input  wire                    xspi_clk,
    input  wire                    xspi_rst_n,
    input  wire                    xspi_x_valid,   // element present this cycle
    input  wire [15:0]             xspi_x_data,    // BF16 element
    output wire                    xspi_x_full,    // backpressure to the xSPI side

    // ---- AXI4 slave (register) interface ----
    input  wire                    s_axi_awvalid,
    output wire                    s_axi_awready,
    input  wire [31:0]             s_axi_awaddr,
    input  wire [7:0]              s_axi_awlen,
    input  wire [2:0]              s_axi_awsize,
    input  wire [1:0]              s_axi_awburst,
    input  wire [3:0]              s_axi_awid,

    input  wire                    s_axi_wvalid,
    output wire                    s_axi_wready,
    input  wire [31:0]             s_axi_wdata,
    input  wire [3:0]              s_axi_wstrb,

    output wire                    s_axi_bvalid,
    input  wire                    s_axi_bready,
    output wire [1:0]              s_axi_bresp,
    output wire [3:0]              s_axi_bid,

    input  wire                    s_axi_arvalid,
    output wire                    s_axi_arready,
    input  wire [31:0]             s_axi_araddr,
    input  wire [7:0]              s_axi_arlen,
    input  wire [2:0]              s_axi_arsize,
    input  wire [1:0]              s_axi_arburst,
    input  wire [3:0]              s_axi_arid,

    output wire                    s_axi_rvalid,
    input  wire                    s_axi_rready,
    output wire [31:0]             s_axi_rdata,
    output wire [1:0]              s_axi_rresp,
    output wire                    s_axi_rlast,
    output wire [3:0]              s_axi_rid,

    // ---- AXI4 master (DDR4) interface ----
    output wire                    m_axi_awvalid,
    input  wire                    m_axi_awready,
    output wire [31:0]             m_axi_awaddr,
    output wire [7:0]              m_axi_awlen,
    output wire [2:0]              m_axi_awsize,
    output wire [1:0]              m_axi_awburst,
    output wire [3:0]              m_axi_awid,

    output wire                    m_axi_wvalid,
    input  wire                    m_axi_wready,
    output wire [DATA_WIDTH-1:0]   m_axi_wdata,
    output wire [DATA_WIDTH/8-1:0] m_axi_wstrb,
    output wire                    m_axi_wlast,

    input  wire                    m_axi_bvalid,
    output wire                    m_axi_bready,
    input  wire [1:0]              m_axi_bresp,
    input  wire [3:0]              m_axi_bid,

    output wire                    m_axi_arvalid,
    input  wire                    m_axi_arready,
    output wire [31:0]             m_axi_araddr,
    output wire [7:0]              m_axi_arlen,
    output wire [2:0]              m_axi_arsize,
    output wire [1:0]              m_axi_arburst,
    output wire [3:0]              m_axi_arid,

    input  wire                    m_axi_rvalid,
    output wire                    m_axi_rready,
    input  wire [DATA_WIDTH-1:0]   m_axi_rdata,
    input  wire [1:0]              m_axi_rresp,
    input  wire                    m_axi_rlast,
    input  wire [3:0]              m_axi_rid,

    // ---- core result (used by the EXTERNAL_DATA=0 path / standalone TB) ----
    output wire [D*32-1:0]         xout_vec
);

    localparam NUM_REGS = 8;
    localparam AW       = $clog2(NUM_REGS);
    localparam WORDS_PER_BEAT = DATA_WIDTH/32;   // 16 for a 512-bit beat

    // ================= register file (mirrored from the AXI slave) =================
    reg [31:0] ctrl_reg, status_reg;
    reg [31:0] w_base_reg, x_base_reg, out_base_reg;
    reg [31:0] d_reg, n_reg;
    reg [31:0] count_reg;      // 0x1C COUNT -- outputs produced by the last job

    wire start   = ctrl_reg[0];
    wire reset_b = ctrl_reg[1];   // soft reset (auto-clears after one cycle)
    wire bf16_in = ctrl_reg[2];   // input format select: 1=BF16 (supported), 0=FP32

    // register read mux (combinational) keyed on the slave's current read index
    wire [AW-1:0] ridx;
    reg  [31:0]   reg_rdata_mux;
    always @(*) begin
        case (ridx)
            0:   reg_rdata_mux = ctrl_reg;
            1:   reg_rdata_mux = status_reg;
            2:   reg_rdata_mux = w_base_reg;
            3:   reg_rdata_mux = x_base_reg;
            4:   reg_rdata_mux = out_base_reg;
            5:   reg_rdata_mux = d_reg;
            6:   reg_rdata_mux = n_reg;
            7:   reg_rdata_mux = count_reg;
            default: reg_rdata_mux = 32'd0;
        endcase
    end

    // register write mirror (driven by the slave's reg_we/reg_waddr/reg_wdata).
    // The FSM can also auto-clear CTRL.start when a job finishes so a driver that
    // leaves start high does not immediately re-trigger.
    wire        reg_we;
    wire [AW-1:0] reg_waddr;
    wire [31:0]   reg_wdata;
    wire          fsm_clr_start;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            ctrl_reg     <= 32'd0;
            w_base_reg   <= 32'd0;
            x_base_reg   <= 32'd0;
            out_base_reg <= 32'd0;
            d_reg        <= 32'd0;
            n_reg        <= 32'd0;
        end else if (reg_we) begin
            case (reg_waddr)
                0:   ctrl_reg     <= reg_wdata;
                2:   w_base_reg   <= reg_wdata;
                3:   x_base_reg   <= reg_wdata;
                4:   out_base_reg <= reg_wdata;
                5:   d_reg        <= reg_wdata;
                6:   n_reg        <= reg_wdata;
                default: ;
            endcase
        end else if (fsm_clr_start || soft_reset_done) begin
            if (fsm_clr_start)   ctrl_reg[0] <= 1'b0;   // job finished, clear start
            if (soft_reset_done) ctrl_reg[1] <= 1'b0;   // reset consumed, clear reset
        end
    end

    // Soft reset (CTRL.reset): one-cycle pulse that clears the load FSM, status
    // and counters. Auto-clears so a driver that leaves the bit high does not
    // hold the design in reset.
    reg soft_reset_r;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)      soft_reset_r <= 1'b0;
        else               soft_reset_r <= reset_b;
    end
    // one-cycle pulse the cycle after soft_reset_r, used to auto-clear
    // CTRL.reset so a driver that leaves the bit high does not hold the design
    // in reset.
    reg soft_reset_done;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)      soft_reset_done <= 1'b0;
        else               soft_reset_done <= soft_reset_r;
    end

    // STATUS.error (bit2): sticky. Set when an unsupported input format is
    // requested (bf16_in=0); cleared by a soft reset or aresetn. Gated so it
    // does not re-fire during the one-cycle soft-reset window.
    wire err_set = EXTERNAL_DATA && start && !bf16_in && !soft_reset_r;

    // ================= AXI4 slave (register) =================
    axi4s_reg #(
        .ADDR_WIDTH(32), .DATA_WIDTH(32), .ID_WIDTH(4), .NUM_REGS(NUM_REGS)
    ) u_slave (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awlen(s_axi_awlen),
        .s_axi_awsize(s_axi_awsize), .s_axi_awburst(s_axi_awburst),
        .s_axi_awid(s_axi_awid),
        .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb),
        .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bid(s_axi_bid),
        .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arlen(s_axi_arlen),
        .s_axi_arsize(s_axi_arsize), .s_axi_arburst(s_axi_arburst),
        .s_axi_arid(s_axi_arid),
        .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready),
        .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp),
        .s_axi_rlast(s_axi_rlast), .s_axi_rid(s_axi_rid),
        .reg_we(reg_we), .reg_waddr(reg_waddr), .reg_wdata(reg_wdata),
        .reg_raddr(ridx), .reg_rdata_mux(reg_rdata_mux)
    );

    // ================= matmul core =================
    wire [D*32-1:0] xout_vec_i;
    wire core_done;

    // element-load ports (only meaningful when EXTERNAL_DATA=1)
    wire        w_load_valid;
    wire [$clog2(D*N)-1:0] w_load_idx;
    wire [15:0] w_load_data;
    wire        x_load_valid;
    wire [$clog2(N)-1:0]  x_load_idx;
    wire [15:0] x_load_data;

    // core start: in EXTERNAL_DATA mode the FSM pulses it once after loading;
    // otherwise it tracks the CTRL.start register bit directly.
    wire fsm_core_start;
    wire core_start = EXTERNAL_DATA ? fsm_core_start : start;

    matmul_core #(
        .D(D), .N(N), .EXTERNAL_LOAD(EXTERNAL_DATA)
    ) u_core (
        .clk(aclk), .rst_n(aresetn),
        .start(core_start),
        .done(core_done),
        .xout_vec(xout_vec_i),
        .w_load_valid(w_load_valid),
        .w_load_idx(w_load_idx),
        .w_load_data(w_load_data),
        .x_load_valid(x_load_valid),
        .x_load_idx(x_load_idx),
        .x_load_data(x_load_data)
    );

    assign xout_vec = xout_vec_i;

    // ================= xSPI -> aclk CDC (activation vector x) =================
    // When X_FROM_XSPI=1 the activation vector arrives on the xspi_clk domain and
    // is bridged into aclk with an async FIFO (gray pointers). This is the
    // multi-bit data path: a two-flop synchronizer would be wrong here because the
    // 16-bit element changes every xspi cycle, so sampling it directly could latch
    // a transient value that never existed in the source domain. The FIFO instead
    // guarantees each element is written before it is read and never overwritten.
    wire [15:0] xfifo_rd_data;
    wire        xfifo_rd_empty;
    wire        xfifo_wr_full;
    assign xspi_x_full = xfifo_wr_full;

    async_fifo #(.DATA_WIDTH(16), .DEPTH(XFIFO_DEPTH)) u_xfifo (
        .wr_clk(xspi_clk), .rst_n(xspi_rst_n),
        .wr_en(xspi_x_valid), .wr_data(xspi_x_data), .wr_full(xfifo_wr_full),
        .rd_clk(aclk), .rd_en(xfifo_rd_en), .rd_data(xfifo_rd_data),
        .rd_empty(xfifo_rd_empty)
    );

    // consume one x element per aclk cycle while loading from the FIFO
    wire xfifo_rd_en = X_FROM_XSPI && (lstate == L_LOAD_X) && !xfifo_rd_empty;

    // ---- core x-load ports: mux between AXI (X_FROM_XSPI=0) and FIFO (=1) ----
    assign x_load_valid = X_FROM_XSPI ?
        ((lstate == L_LOAD_X) && !xfifo_rd_empty) :
        ((lstate == L_LOAD_X) && rd_data_valid);
    assign x_load_idx   = X_FROM_XSPI ? x_elem : (x_elem + x_beat_el);
    assign x_load_data  = X_FROM_XSPI ? xfifo_rd_data : rd_data[x_beat_el*16 +: 16];

    // ================= AXI master (DDR4) =================
    wire        rd_start, wr_start;
    wire [31:0] rd_addr, wr_addr;
    wire [19:0] rd_len_bytes, wr_len_bytes;   // sized to master RD_LEN_W/WR_LEN_W
    wire        wr_data_in_valid;
    wire [DATA_WIDTH-1:0] wr_data_in;
    wire        wr_data_in_ready;
    wire        wr_done;
    wire        rd_data_valid;
    wire [DATA_WIDTH-1:0] rd_data;
    wire        rd_last;
    wire        rd_data_ready;
    wire        rd_busy, wr_busy;

    axi4_master #(
        .ADDR_WIDTH(32), .DATA_WIDTH(DATA_WIDTH)
    ) u_master (
        .aclk(aclk), .aresetn(aresetn),
        // read command
        .rd_start(rd_start), .rd_addr(rd_addr), .rd_len_bytes(rd_len_bytes),
        .rd_busy(rd_busy),
        .rd_data_valid(rd_data_valid), .rd_data(rd_data), .rd_last(rd_last),
        .rd_data_ready(rd_data_ready),
        // write command
        .wr_start(wr_start), .wr_addr(wr_addr), .wr_len_bytes(wr_len_bytes),
        .wr_busy(wr_busy),
        .wr_data_in_valid(wr_data_in_valid), .wr_data_in(wr_data_in),
        .wr_data_in_ready(wr_data_in_ready), .wr_done(wr_done),
        // AXI master ports
        .m_axi_awvalid(m_axi_awvalid), .m_axi_awready(m_axi_awready),
        .m_axi_awaddr(m_axi_awaddr), .m_axi_awlen(m_axi_awlen),
        .m_axi_awsize(m_axi_awsize), .m_axi_awburst(m_axi_awburst),
        .m_axi_awid(m_axi_awid),
        .m_axi_wvalid(m_axi_wvalid), .m_axi_wready(m_axi_wready),
        .m_axi_wdata(m_axi_wdata), .m_axi_wstrb(m_axi_wstrb),
        .m_axi_wlast(m_axi_wlast),
        .m_axi_bvalid(m_axi_bvalid), .m_axi_bready(m_axi_bready),
        .m_axi_bresp(m_axi_bresp), .m_axi_bid(m_axi_bid),
        .m_axi_arvalid(m_axi_arvalid), .m_axi_arready(m_axi_arready),
        .m_axi_araddr(m_axi_araddr), .m_axi_arlen(m_axi_arlen),
        .m_axi_arsize(m_axi_arsize), .m_axi_arburst(m_axi_arburst),
        .m_axi_arid(m_axi_arid),
        .m_axi_rvalid(m_axi_rvalid), .m_axi_rready(m_axi_rready),
        .m_axi_rdata(m_axi_rdata), .m_axi_rresp(m_axi_rresp),
        .m_axi_rlast(m_axi_rlast), .m_axi_rid(m_axi_rid)
    );

    // ================= load/store FSM (EXTERNAL_DATA=1) =================
    localparam L_IDLE      = 3'd0;
    localparam L_LOAD_W    = 3'd1;
    localparam L_LOAD_X    = 3'd2;
    localparam L_RUN       = 3'd3;
    localparam L_STORE_OUT = 3'd4;

    // number of BF16 elements per AXI beat (DATA_WIDTH/16) and its counter width
    localparam BF16_PER_BEAT = DATA_WIDTH/16;
    localparam BEAT_EL_W     = $clog2(BF16_PER_BEAT);

    reg [2:0]  lstate;
    reg [$clog2(D*N)-1:0] w_elem;     // W elements loaded so far
    reg [BEAT_EL_W-1:0]   w_beat_el;  // element within current beat (0..BF16_PER_BEAT-1)
    reg [$clog2(N)-1:0]   x_elem;     // x elements loaded so far
    reg [BEAT_EL_W-1:0]   x_beat_el;
    reg [$clog2(D):0]     out_word;   // FP32 words presented for store (0..D)

    // one-shot command pulses
    reg rd_start_r, wr_start_r;
    assign rd_start = rd_start_r;
    assign wr_start = wr_start_r;

    // ---- master read-data ready: accept a beat only after unpacking all elements ----
    assign rd_data_ready =
        (lstate == L_LOAD_W && rd_data_valid && w_beat_el == BF16_PER_BEAT-1) ||
        (lstate == L_LOAD_X && rd_data_valid && x_beat_el == BF16_PER_BEAT-1);

    // ---- core element-load ports (W: one element per cycle while a beat is held) ----
    assign w_load_valid = (lstate == L_LOAD_W) && rd_data_valid;
    assign w_load_idx   = w_elem + w_beat_el;
    assign w_load_data  = rd_data[w_beat_el*16 +: 16];

    // ---- write-data for storing xout (pack WORDS_PER_BEAT FP32 words/beat) ----
    assign wr_data_in_valid = (lstate == L_STORE_OUT) && (out_word < D);
    genvar gw;
    generate
        for (gw = 0; gw < WORDS_PER_BEAT; gw = gw + 1) begin : pack_out
            assign wr_data_in[gw*32 +: 32] = xout_vec_i[(out_word + gw)*32 +: 32];
        end
    endgenerate

    // ---- command addresses / lengths ----
    // Lengths are sized to the master's RD_LEN_W/WR_LEN_W (20 bits, max 1 MiB).
    // D*N*2 and D*4 are far below that for any supported D/N, so no truncation.
    wire [19:0] w_len_bytes = D*N*2;   // D*N BF16 * 2 bytes
    wire [19:0] x_len_bytes = N*2;     // N BF16 * 2 bytes

    assign rd_addr      = (lstate == L_LOAD_W) ? w_base_reg : x_base_reg;
    assign rd_len_bytes = (lstate == L_LOAD_W) ? w_len_bytes : x_len_bytes;
    assign wr_addr      = out_base_reg;
    assign wr_len_bytes = D*4;         // D FP32 words * 4 bytes

    // ---- FSM ----
    // status_reg is driven ONLY here (the register-file block no longer touches
    // it) so there is a single driver. Bit layout: bit0 done, bit1 busy,
    // bit2 error (sticky).
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            lstate     <= L_IDLE;
            w_elem     <= 0;
            w_beat_el  <= 0;
            x_elem     <= 0;
            x_beat_el  <= 0;
            out_word   <= 0;
            rd_start_r <= 1'b0;
            wr_start_r <= 1'b0;
            status_reg <= 32'd0;
            count_reg  <= 32'd0;
        end else if (soft_reset_r) begin
            // soft reset (CTRL.reset): clear FSM, status and counters.
            lstate     <= L_IDLE;
            w_elem     <= 0;
            w_beat_el  <= 0;
            x_elem     <= 0;
            x_beat_el  <= 0;
            out_word   <= 0;
            rd_start_r <= 1'b0;
            wr_start_r <= 1'b0;
            status_reg <= 32'd0;
            count_reg  <= 32'd0;
        end else begin
            rd_start_r <= 1'b0;
            wr_start_r <= 1'b0;

            // sticky error: set when an unsupported input format is requested,
            // held until a soft reset or aresetn.
            if (err_set)
                status_reg[2] <= 1'b1;

            case (lstate)
                L_IDLE: begin
                    status_reg[1] <= 1'b0;   // busy clear
                    // Gate on !clr_start_pulse: on the cycle the previous job
                    // finishes (L_STORE_OUT->L_IDLE), clr_start_pulse is high but
                    // ctrl_reg[0] (start) has not been cleared yet (registered,
                    // takes effect next cycle). Without this guard the FSM would
                    // see start still high and immediately re-trigger a second
                    // job that reads an empty FIFO.
                    if (EXTERNAL_DATA && start && bf16_in && !clr_start_pulse) begin
                        lstate     <= L_LOAD_W;
                        w_elem     <= 0;
                        w_beat_el  <= 0;
                        x_elem     <= 0;
                        x_beat_el  <= 0;
                        out_word   <= 0;
                        rd_start_r <= 1'b1;   // start reading W
                        status_reg[0] <= 1'b0; // done clear (new job)
                        status_reg[1] <= 1'b1; // busy
                    end
                end

                L_LOAD_W: begin
                    if (rd_data_valid) begin
                        if (w_beat_el == BF16_PER_BEAT-1) begin
                            w_beat_el <= 0;
                            w_elem    <= w_elem + BF16_PER_BEAT;
                            if (w_elem + BF16_PER_BEAT >= D*N) begin
                                lstate     <= L_LOAD_X;
                                // x comes from DDR4 only in AXI mode; in X_FROM_XSPI
                                // mode it arrives on the async FIFO, so no read here.
                                if (!X_FROM_XSPI)
                                    rd_start_r <= 1'b1;   // start reading x
                            end
                        end else begin
                            w_beat_el <= w_beat_el + 1;
                        end
                    end
                end

                L_LOAD_X: begin
                    if (X_FROM_XSPI) begin
                        // one element per aclk cycle from the async FIFO
                        if (!xfifo_rd_empty) begin
                            x_elem <= x_elem + 1'b1;
                            if (x_elem + 1'b1 >= N)
                                lstate <= L_RUN;
                        end
                    end else begin
                        if (rd_data_valid) begin
                            if (x_beat_el == BF16_PER_BEAT-1) begin
                                x_beat_el <= 0;
                                x_elem    <= x_elem + BF16_PER_BEAT;
                                if (x_elem + BF16_PER_BEAT >= N)
                                    lstate <= L_RUN;
                            end else begin
                                x_beat_el <= x_beat_el + 1;
                            end
                        end
                    end
                end

                L_RUN: begin
                    if (core_done) begin
                        lstate     <= L_STORE_OUT;
                        out_word   <= 0;
                        wr_start_r <= 1'b1;   // start writing xout
                    end
                end

                L_STORE_OUT: begin
                    // advance the output word pointer as beats are accepted.
                    if (wr_data_in_valid && wr_data_in_ready)
                        out_word <= out_word + WORDS_PER_BEAT;
                    // finish when all D words have been presented and the master
                    // has completed the write (wr_done pulse).
                    if (out_word >= D && wr_done) begin
                        lstate     <= L_IDLE;
                        status_reg[0] <= 1'b1;   // done
                        status_reg[1] <= 1'b0;   // busy clear
                        count_reg    <= D;        // outputs produced this job
                    end
                end

                default: lstate <= L_IDLE;
            endcase
        end
    end

    // one-cycle core-start pulse, asserted on the cycle after loading completes
    reg core_start_pulse;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)
            core_start_pulse <= 1'b0;
        else
            core_start_pulse <= (lstate == L_RUN) && !core_done;
    end
    assign fsm_core_start = core_start_pulse;

    // one-cycle pulse to auto-clear CTRL.start when a job finishes
    reg clr_start_pulse;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)
            clr_start_pulse <= 1'b0;
        else
            clr_start_pulse <= (lstate == L_STORE_OUT) && (out_word >= D) && wr_done;
    end
    assign fsm_clr_start = clr_start_pulse;

endmodule

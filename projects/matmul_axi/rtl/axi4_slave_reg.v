// axi4_slave_reg : AXI4 full slave exposing a memory-mapped register file.
//
// The IP's AXI4 slave port (control/status block, ARCHITECTURE.md §3.5).
// Full 5-channel AXI4 protocol with INCR burst support; width-generic
// (AXI_DATA_WIDTH = 32/64/128/256).
//
// Address space: one register block at REG_BASE, 32-bit registers at byte
// offsets. A beat's transfer size is set by awsize/arsize (bytes per beat =
// 2^size); on a wide bus a narrow transfer (e.g. awsize=2, 4 bytes) touches
// only the low word-lanes of the data bus and advances the address by 2^size.
// WSTRB selects bytes within the active lanes.
//
// Registers (byte offsets, all 32-bit):
//   0x00 CTRL    RW  bit0=start, bit1=reset, bit2=bf16_in
//   0x04 STATUS  RO  bit0=busy, bit1=done, bit2=error
//   0x08 M_DIM   RO  rows of W (d)
//   0x0C N_DIM   RO  reduction length (n)
//   0x10 W_BASE  RW  DDR4 base addr of weight matrix
//   0x14 X_BASE  RW  DDR4 base addr of activation vector
//   0x18 OUT_BASE RW  DDR4 base addr of output vector
//   0x1C COUNT   RO  number of outputs produced
// Reads of unmapped offsets return 0; writes to RO bits are ignored.
module axi4_slave_reg #(
    parameter AXI_DATA_WIDTH = 256,
    parameter AXI_ADDR_WIDTH = 32,
    parameter C_AXI_ID_WIDTH = 4,
    parameter REG_BASE       = 32'd0
)(
    input  wire        clk,
    input  wire        rst_n,

    // ---- write address channel (AW) ----
    input  wire [C_AXI_ID_WIDTH-1:0] awid,
    input  wire [AXI_ADDR_WIDTH-1:0] awaddr,
    input  wire [7:0]                awlen,
    input  wire [2:0]                awsize,
    input  wire [1:0]                awburst,
    input  wire                      awvalid,
    output wire                      awready,

    // ---- write data channel (W) ----
    input  wire [AXI_DATA_WIDTH-1:0] wdata,
    input  wire [AXI_DATA_WIDTH/8-1:0] wstrb,
    input  wire                      wlast,
    input  wire                      wvalid,
    output wire                      wready,

    // ---- write response channel (B) ----
    output reg  [C_AXI_ID_WIDTH-1:0] bid,
    output wire [1:0]                bresp,
    output reg                       bvalid,
    input  wire                      bready,

    // ---- read address channel (AR) ----
    input  wire [C_AXI_ID_WIDTH-1:0] arid,
    input  wire [AXI_ADDR_WIDTH-1:0] araddr,
    input  wire [7:0]                arlen,
    input  wire [2:0]                arsize,
    input  wire [1:0]                arburst,
    input  wire                      arvalid,
    output wire                      arready,

    // ---- read data channel (R) ----
    output reg  [C_AXI_ID_WIDTH-1:0] rid,
    output reg  [AXI_DATA_WIDTH-1:0] rdata,
    output wire [1:0]                rresp,
    output reg                       rlast,
    output reg                       rvalid,
    input  wire                      rready,

    // ---- register-file side (driven by the IP logic) ----
    output wire [31:0] ctrl,        // CTRL register
    input  wire [31:0] status,      // STATUS (RO)
    input  wire [31:0] m_dim,       // M_DIM (RO)
    input  wire [31:0] n_dim,       // N_DIM (RO)
    output wire [31:0] w_base,      // W_BASE
    output wire [31:0] x_base,      // X_BASE
    output wire [31:0] out_base,    // OUT_BASE
    input  wire [31:0] count        // COUNT (RO)
);

    localparam BYTE_PER_BEAT = AXI_DATA_WIDTH/8;
    localparam NUM_REGS      = 8;                 // 0x00..0x1C
    localparam WORDS_PER_BEAT= AXI_DATA_WIDTH/32;

    // ------------------------------------------------------------------
    // Register file
    // ------------------------------------------------------------------
    reg [31:0] regs [0:NUM_REGS-1];
    localparam R_CTRL=0, R_STATUS=1, R_MDIM=2, R_NDIM=3,
               R_WBASE=4, R_XBASE=5, R_OUTBASE=6, R_COUNT=7;

    assign ctrl     = regs[R_CTRL];
    assign w_base   = regs[R_WBASE];
    assign x_base   = regs[R_XBASE];
    assign out_base = regs[R_OUTBASE];

    // Read a register by index (RO regs come from the IP, RW from the file).
    function [31:0] read_reg;
        input [31:0] idx;
        begin
            case (idx)
                R_STATUS : read_reg = status;
                R_MDIM   : read_reg = m_dim;
                R_NDIM   : read_reg = n_dim;
                R_COUNT  : read_reg = count;
                default  : read_reg = regs[idx];
            endcase
        end
    endfunction

    // Is register index a read-only one (not writable from the bus)?
    function is_ro;
        input [31:0] idx;
        begin
            is_ro = (idx == R_STATUS) || (idx == R_MDIM) ||
                    (idx == R_NDIM)   || (idx == R_COUNT);
        end
    endfunction

    // ------------------------------------------------------------------
    // Write path state
    // ------------------------------------------------------------------
    reg [C_AXI_ID_WIDTH-1:0] w_id;
    reg [AXI_ADDR_WIDTH-1:0] w_base_addr;
    reg [7:0]                w_len;        // total beats in burst
    reg [2:0]                w_size;
    reg [7:0]                w_beat;       // current beat index (0..w_len-1)

    // ------------------------------------------------------------------
    // Read path state (3-state FSM: idle -> first -> data)
    // RST_FIRST delays rvalid by one cycle so it aligns with the registered
    // rdata (avoids NBA race where rvalid goes high before rdata updates).
    // ------------------------------------------------------------------
    localparam [1:0] RST_IDLE  = 2'd0;
    localparam [1:0] RST_FIRST = 2'd1;
    localparam [1:0] RST_DATA  = 2'd2;
    reg [1:0]        r_state;
    reg [C_AXI_ID_WIDTH-1:0] r_id;
    reg [AXI_ADDR_WIDTH-1:0] r_base_addr;
    reg [7:0]                r_len;
    reg [2:0]                r_size;
    reg [7:0]                r_beat;

    // ------------------------------------------------------------------
    // AW acceptance (latch burst info; one outstanding write burst at a time)
    // awready is combinational: high when no burst is in flight.
    // ------------------------------------------------------------------
    reg                      aw_latched;
    assign awready = ~aw_latched;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_latched <= 1'b0;
        end else begin
            if (awvalid && awready) begin
                aw_latched  <= 1'b1;
                w_id        <= awid;
                w_base_addr <= awaddr;
                w_len       <= awlen + 8'd1;
                w_size      <= awsize;
                w_beat      <= 8'd0;
            end else if (bvalid && bready) begin
                aw_latched <= 1'b0;   // burst complete, free for the next
            end
        end
    end

    // ------------------------------------------------------------------
    // W acceptance + register writes
    // wready is high once we know where to write (address latched).
    // ------------------------------------------------------------------
    assign wready = aw_latched;

    wire w_hs = wvalid && wready;

    // Number of active word-lanes in the current beat = 2^(w_size-2).
    integer wl;
    integer wb;
    reg [AXI_ADDR_WIDTH+13:0] waddr;
    reg [31:0]                widx;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            // nothing to reset in regs
        end else if (w_hs) begin
            for (wl = 0; wl < WORDS_PER_BEAT; wl = wl + 1) begin
                if (wl < (1 << (w_size - 2'd2))) begin
                    waddr = beat_addr(w_base_addr, w_beat, w_size) + (wl << 2);
                    widx  = reg_index(waddr);
                    if (widx < NUM_REGS && !is_ro(widx)) begin
                        for (wb = 0; wb < 4; wb = wb + 1) begin
                            if (wstrb[wl*4 + wb])
                                regs[widx][wb*8 +: 8] <= wdata[wl*32 + wb*8 +: 8];
                        end
                    end
                end
            end
            if (!wlast)
                w_beat   <= w_beat + 8'd1;
        end
    end

    // ------------------------------------------------------------------
    // Write response (B)
    // ------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bvalid <= 1'b0;
            bid    <= {C_AXI_ID_WIDTH{1'b0}};
        end else begin
            if (bvalid && bready)
                bvalid <= 1'b0;
            else if (w_hs && wlast) begin
                bvalid <= 1'b1;
                bid    <= w_id;
            end
        end
    end
    assign bresp = 2'b00;   // OKAY

    // ------------------------------------------------------------------
    // Read path — single unified FSM (AR accept + R data).
    // arready is combinational: high when idle.
    // rdata is registered so it holds stable during the cycle rvalid is high.
    // ------------------------------------------------------------------
    assign arready = (r_state == RST_IDLE);

    function [AXI_DATA_WIDTH-1:0] beat_data;
        input [AXI_ADDR_WIDTH-1:0] base;
        input [7:0]  idx;
        input [2:0]  size;
        integer bl;
        reg [AXI_ADDR_WIDTH+13:0] baddr;
        reg [31:0] bidx;
        begin
            beat_data = {AXI_DATA_WIDTH{1'b0}};
            for (bl = 0; bl < WORDS_PER_BEAT; bl = bl + 1) begin
                if (bl < (1 << (size - 2'd2))) begin
                    baddr = base + (idx << size) + (bl << 2);
                    bidx  = reg_index(baddr);
                    if (bidx < NUM_REGS)
                        beat_data[bl*32 +: 32] = read_reg(bidx);
                end
            end
        end
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r_state <= RST_IDLE;
            rvalid  <= 1'b0;
            rlast   <= 1'b0;
            rid     <= {C_AXI_ID_WIDTH{1'b0}};
            rdata   <= {AXI_DATA_WIDTH{1'b0}};
            r_id    <= {C_AXI_ID_WIDTH{1'b0}};
            r_base_addr <= {AXI_ADDR_WIDTH{1'b0}};
            r_len   <= 8'd0;
            r_size  <= 3'd0;
            r_beat  <= 8'd0;
        end else begin
            case (r_state)
            RST_IDLE: begin
                rvalid <= 1'b0;
                if (arvalid) begin
                    // latch AR; first beat data computed next cycle in RST_FIRST
                    r_id        <= arid;
                    r_base_addr <= araddr;
                    r_len       <= arlen + 8'd1;
                    r_size      <= arsize;
                    r_beat      <= 8'd0;
                    r_state     <= RST_FIRST;
                end
            end
            RST_FIRST: begin
                // compute beat-0 data now; rvalid asserts next cycle (RST_DATA)
                rdata   <= beat_data(r_base_addr, 8'd0, r_size);
                r_state <= RST_DATA;
            end
            default: begin  // RST_DATA
                rvalid <= 1'b1;
                rid    <= r_id;
                rlast  <= (r_beat == r_len - 8'd1);
                if (rvalid && rready) begin
                    if (r_beat == r_len - 8'd1) begin
                        r_state <= RST_IDLE;
                        rvalid  <= 1'b0;
                    end else begin
                        r_beat <= r_beat + 8'd1;
                        rdata  <= beat_data(r_base_addr, r_beat + 8'd1, r_size);
                    end
                end
            end
            endcase
        end
    end
    assign rresp = 2'b00;   // OKAY

    // ------------------------------------------------------------------
    // Address helpers
    // ------------------------------------------------------------------
    function [AXI_ADDR_WIDTH+13:0] beat_addr;
        input [AXI_ADDR_WIDTH-1:0] base;
        input [7:0]  idx;        // beat index within the burst
        input [2:0]  size;       // bytes per transfer = 2^size
        begin
            beat_addr = base + (idx << size);
        end
    endfunction

    function [31:0] reg_index;
        input [AXI_ADDR_WIDTH+13:0] addr;
        begin
            reg_index = (addr - REG_BASE) >> 2;   // /4
        end
    endfunction

endmodule

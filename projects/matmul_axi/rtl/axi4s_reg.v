// axi4s_reg : minimal AXI4 slave with an internal word-addressable register file.
//
// Implements the five AXI4 channels (AW, W, B, AR, R) with a simple, correct
// handshake: at most one outstanding write and one outstanding read (the slave
// deasserts AWREADY/ARREADY while a transaction is in flight). Supports
// single-beat transfers fully; for bursts it increments the address per beat
// (INCR), which is what the register map and a future data region need.
//
// The register file lives inside this block. The caller supplies:
//   - reg_rdata_mux : combinational read data for the current read address
//                     (used to build s_axi_rdata, registered one cycle).
//   - reg_we / reg_waddr / reg_wdata : write strobe + index + data, so the
//                     caller can mirror RW registers and react to writes.
//
// Response codes: OKAY (2'b00) for in-range accesses, SLVERR (2'b10) otherwise.

module axi4s_reg #(
    parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32,
    parameter ID_WIDTH   = 4,
    parameter NUM_REGS   = 8          // number of 32-bit registers
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    // ---- AXI4 write-address channel ----
    input  wire                    s_axi_awvalid,
    output wire                    s_axi_awready,
    input  wire [ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  wire [7:0]              s_axi_awlen,
    input  wire [2:0]              s_axi_awsize,
    input  wire [1:0]              s_axi_awburst,
    input  wire [ID_WIDTH-1:0]     s_axi_awid,

    // ---- AXI4 write-data channel ----
    input  wire                    s_axi_wvalid,
    output wire                    s_axi_wready,
    input  wire [DATA_WIDTH-1:0]   s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0] s_axi_wstrb,

    // ---- AXI4 write-response channel ----
    output wire                    s_axi_bvalid,
    input  wire                    s_axi_bready,
    output wire [1:0]              s_axi_bresp,
    output wire [ID_WIDTH-1:0]     s_axi_bid,

    // ---- AXI4 read-address channel ----
    input  wire                    s_axi_arvalid,
    output wire                    s_axi_arready,
    input  wire [ADDR_WIDTH-1:0]   s_axi_araddr,
    input  wire [7:0]              s_axi_arlen,
    input  wire [2:0]              s_axi_arsize,
    input  wire [1:0]              s_axi_arburst,
    input  wire [ID_WIDTH-1:0]     s_axi_arid,

    // ---- AXI4 read-data channel ----
    output wire                    s_axi_rvalid,
    input  wire                    s_axi_rready,
    output wire [DATA_WIDTH-1:0]   s_axi_rdata,
    output wire [1:0]              s_axi_rresp,
    output wire                    s_axi_rlast,
    output wire [ID_WIDTH-1:0]     s_axi_rid,

    // ---- register-file interface ----
    output reg                     reg_we,        // write-enable (one cycle)
    output reg  [$clog2(NUM_REGS)-1:0] reg_waddr, // register index
    output wire [DATA_WIDTH-1:0]   reg_wdata,     // data written (registered)
    output wire [$clog2(NUM_REGS)-1:0] reg_raddr, // register index being read
    input  wire [DATA_WIDTH-1:0]   reg_rdata_mux  // read data for current raddr
);

    localparam AW = $clog2(NUM_REGS);

    // ---- write path state ----
    reg         aw_pending;     // AW seen, waiting for W (or same-cycle)
    reg [ADDR_WIDTH-1:0] aw_addr;
    reg [7:0]  aw_len;
    reg [2:0]  aw_size;
    reg [ID_WIDTH-1:0] aw_id;
    reg [3:0]  w_beat;          // current beat index within the burst
    reg        w_done;          // all beats of this write received

    // ---- read path state ----
    reg         ar_pending;
    reg [ADDR_WIDTH-1:0] ar_addr;
    reg [7:0]  ar_len;
    reg [2:0]  ar_size;
    reg [ID_WIDTH-1:0] ar_id;
    reg [3:0]  r_beat;

    // address of the current beat (INCR burst)
    function [ADDR_WIDTH-1:0] beat_addr;
        input [ADDR_WIDTH-1:0] base;
        input [2:0] size;
        input [3:0] beat;
        begin
            beat_addr = base + (beat << size);
        end
    endfunction

    // register index for a byte address (word-aligned)
    function [AW-1:0] reg_index;
        input [ADDR_WIDTH-1:0] addr;
        begin
            reg_index = addr[AW+1:2];
        end
    endfunction

    wire in_range_w = (reg_index(aw_addr) < NUM_REGS);
    wire in_range_r = (reg_index(ar_addr) < NUM_REGS);

    // ---- AW channel ----
    assign s_axi_awready = ~aw_pending;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            aw_pending <= 1'b0;
            aw_addr    <= {ADDR_WIDTH{1'b0}};
            aw_len     <= 8'd0;
            aw_size    <= 3'd0;
            aw_id      <= {ID_WIDTH{1'b0}};
        end else if (s_axi_awvalid && s_axi_awready) begin
            aw_pending <= 1'b1;
            aw_addr    <= s_axi_awaddr;
            aw_len     <= s_axi_awlen;
            aw_size    <= s_axi_awsize;
            aw_id      <= s_axi_awid;
        end else if (w_done && s_axi_bvalid && s_axi_bready) begin
            aw_pending <= 1'b0;   // write fully complete
        end
    end

    // ---- W channel ----
    assign s_axi_wready = aw_pending;
    reg [DATA_WIDTH-1:0] wdata_reg;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            w_beat <= 4'd0;
            w_done <= 1'b0;
            reg_we   <= 1'b0;
            reg_waddr<= {AW{1'b0}};
            wdata_reg<= {DATA_WIDTH{1'b0}};
        end else begin
            if (s_axi_wvalid && s_axi_wready) begin
                // perform the register write for this beat
                reg_we    <= 1'b1;
                reg_waddr <= reg_index(beat_addr(aw_addr, aw_size, w_beat));
                wdata_reg <= s_axi_wdata;
                if (w_beat == aw_len[3:0]) begin
                    w_done <= 1'b1;
                end else begin
                    w_beat <= w_beat + 4'd1;
                end
            end else begin
                reg_we <= 1'b0;
            end
            if (w_done && s_axi_bvalid && s_axi_bready) begin
                w_done <= 1'b0;
                w_beat <= 4'd0;
            end
        end
    end
    assign reg_wdata = wdata_reg;

    // ---- B channel ----
    assign s_axi_bvalid = w_done;
    assign s_axi_bid    = aw_id;
    assign s_axi_bresp  = in_range_w ? 2'b00 : 2'b10;   // OKAY / SLVERR

    // ---- AR channel ----
    assign s_axi_arready = ~ar_pending;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            ar_pending <= 1'b0;
            ar_addr    <= {ADDR_WIDTH{1'b0}};
            ar_len     <= 8'd0;
            ar_size    <= 3'd0;
            ar_id      <= {ID_WIDTH{1'b0}};
        end else if (s_axi_arvalid && s_axi_arready) begin
            ar_pending <= 1'b1;
            ar_addr    <= s_axi_araddr;
            ar_len     <= s_axi_arlen;
            ar_size    <= s_axi_arsize;
            ar_id      <= s_axi_arid;
        end else if (s_axi_rvalid && s_axi_rready && s_axi_rlast) begin
            ar_pending <= 1'b0;   // read burst fully complete
        end
    end

    // ---- R channel ----
    // Read data is combinational from reg_rdata_mux (a register file has no read
    // latency). reg_raddr is the index of the beat currently being driven.
    assign s_axi_rvalid = ar_pending;
    assign s_axi_rdata  = reg_rdata_mux;
    assign s_axi_rresp  = in_range_r ? 2'b00 : 2'b10;   // OKAY / SLVERR
    assign s_axi_rlast  = (r_beat == ar_len[3:0]);
    assign s_axi_rid    = ar_id;

    assign reg_raddr = reg_index(beat_addr(ar_addr, ar_size, r_beat));

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            r_beat <= 4'd0;
        end else begin
            // advance the read beat while the burst is active and being accepted
            if (ar_pending && s_axi_rready) begin
                if (r_beat == ar_len[3:0]) begin
                    r_beat <= 4'd0;
                end else begin
                    r_beat <= r_beat + 4'd1;
                end
            end
        end
    end

endmodule

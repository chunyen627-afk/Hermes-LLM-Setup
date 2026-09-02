// xspi_slave : xSPI -> AXI BRIDGE (NOT a memory).
`timescale 1ns/1ps
//
// This is the block that lets the STM32H7S78-DK use its EXISTING OCTOSPI
// memory-mapped config to reach the FPGA with zero firmware changes. The
// STM32 side "looks like" the board's APS256XX PSRAM (same wire contract,
// same boot sequence), but the bridge itself STORES NOTHING: every read/write
// the host issues is turned into an AXI4 transaction and forwarded to either
// matmul_top's register file or the MIG DDR4. Data lands in DDR4 / the
// registers -- never in this module. (See SPEC_xspi_bridge.md; the old
// PSRAM-model version that had its own mem[] array is in .attic/.)
//
// Wire contract (unchanged from v1, derived from the real board BSP -- see
// ARCHITECTURE.md section 9):
//   frame = [ instruction : 8 bits, SDR ]
//           [ address     : 32 bits, DDR x8 -> 2 SCK cycles (16 bits/cycle) ]
//           [ dummy       : N cycles (LatencyCode - 1; reg-write = 0) ]
//           [ data        : DDR x8, 16 bits per SCK cycle, addr auto-increments ]
//   opcodes: 0x00/0x20 read, 0x80/0xA0 write, 0xFF reset,
//            0x40 reg-read, 0xC0 reg-write.
//   MODE 0 (CPOL0/CPHA0): SDR sampled on rising edge; DDR also samples falling.
//
// Address decode (SPEC section 5): the host sees one continuous memory-mapped
// window from 0x9000_0000. The bridge decodes the high address bits:
//   0x9000_0000 .. 0x9000_0FFF  -> reg region (matmul_top.s_axi_*), 4 KB
//   0x9001_0000 and above      -> DDR4 region (MIG)
// The 4 KB reg window is chosen so the two regions sit on AXI 4 KB boundaries.
//
// Two length models (SPEC section 6): xSPI does NOT pre-declare a length (CS
// deassert ends it); AXI declares awlen/arlen up front. The bridge resolves
// this by buffering: writes are staged in an async FIFO and flushed as one
// AXI burst when CS deasserts; reads prefetch a fixed window and the host
// consumes only what it asks for (unused beats are dropped). Register accesses
// are single 4-byte words, so they never need a long burst.
//
// Clock domains: xspi_clk (host SCK) and aclk (FPGA fabric) are unrelated.
// All data crosses through async_fifo (gray pointers). The aclk side is an
// axi4_master that drives the two AXI slave regions.

module xspi_slave #(
    parameter REG_BASE   = 32'h9000_0000,   // reg region base (host view)
    parameter DDR_BASE   = 32'h9001_0000,   // DDR4 region base (host view)
    parameter AXI_DATA_WIDTH = 32,          // beat width on the AXI side
    parameter AXI_ADDR_WIDTH = 32,
    parameter AXI_ID_WIDTH   = 4,
    parameter RD_PREFETCH_BEATS = 8,        // read prefetch window (beats)
    parameter WR_FIFO_DEPTH   = 1024,       // write-data FIFO (xspi -> aclk)
    parameter RD_FIFO_DEPTH   = 512         // read-data FIFO (aclk -> xspi)
)(
    // ---- xSPI physical interface (xspi_clk domain) ----
    input  wire        xspi_clk,       // SCK from the master
    input  wire        xspi_cs_n,      // chip select, active low
    inout  wire [7:0]  xspi_io,        // 8 bidirectional data lines (octal)
    input  wire        xspi_dqs,       // DQS (present but unused; we key off SCK)

    // ---- FPGA fabric clock domain ----
    input  wire        aclk,
    input  wire        arst_n,

    // ---- AXI4 master: reg region (matmul_top.s_axi_*) ----
    output wire                    m_reg_awvalid,
    input  wire                    m_reg_awready,
    output wire [AXI_ADDR_WIDTH-1:0] m_reg_awaddr,
    output wire [7:0]              m_reg_awlen,
    output wire [2:0]              m_reg_awsize,
    output wire [1:0]              m_reg_awburst,
    output wire [AXI_ID_WIDTH-1:0] m_reg_awid,
    output wire                    m_reg_wvalid,
    input  wire                    m_reg_wready,
    output wire [AXI_DATA_WIDTH-1:0] m_reg_wdata,
    output wire [AXI_DATA_WIDTH/8-1:0] m_reg_wstrb,
    output wire                    m_reg_wlast,
    input  wire                    m_reg_bvalid,
    output wire                    m_reg_bready,
    input  wire [1:0]              m_reg_bresp,
    input  wire [AXI_ID_WIDTH-1:0] m_reg_bid,
    output wire                    m_reg_arvalid,
    input  wire                    m_reg_arready,
    output wire [AXI_ADDR_WIDTH-1:0] m_reg_araddr,
    output wire [7:0]              m_reg_arlen,
    output wire [2:0]              m_reg_arsize,
    output wire [1:0]              m_reg_arburst,
    output wire [AXI_ID_WIDTH-1:0] m_reg_arid,
    input  wire                    m_reg_rvalid,
    output wire                    m_reg_rready,
    input  wire [AXI_DATA_WIDTH-1:0] m_reg_rdata,
    input  wire [1:0]              m_reg_rresp,
    input  wire                    m_reg_rlast,
    input  wire [AXI_ID_WIDTH-1:0] m_reg_rid,

    // ---- AXI4 master: DDR4 region (MIG) ----
    output wire                    m_ddr_awvalid,
    input  wire                    m_ddr_awready,
    output wire [AXI_ADDR_WIDTH-1:0] m_ddr_awaddr,
    output wire [7:0]              m_ddr_awlen,
    output wire [2:0]              m_ddr_awsize,
    output wire [1:0]              m_ddr_awburst,
    output wire [AXI_ID_WIDTH-1:0] m_ddr_awid,
    output wire                    m_ddr_wvalid,
    input  wire                    m_ddr_wready,
    output wire [AXI_DATA_WIDTH-1:0] m_ddr_wdata,
    output wire [AXI_DATA_WIDTH/8-1:0] m_ddr_wstrb,
    output wire                    m_ddr_wlast,
    input  wire                    m_ddr_bvalid,
    output wire                    m_ddr_bready,
    input  wire [1:2]              dummy_unused,   // (placeholder, see note)
    input  wire [1:0]              m_ddr_bresp,
    input  wire [AXI_ID_WIDTH-1:0] m_ddr_bid,
    output wire                    m_ddr_arvalid,
    input  wire                    m_ddr_arready,
    output wire [AXI_ADDR_WIDTH-1:0] m_ddr_araddr,
    output wire [7:0]              m_ddr_arlen,
    output wire [2:0]              m_ddr_arsize,
    output wire [1:0]              m_ddr_arburst,
    output wire [AXI_ID_WIDTH-1:0] m_ddr_arid,
    input  wire                    m_ddr_rvalid,
    output wire                    m_ddr_rready,
    input  wire [AXI_DATA_WIDTH-1:0] m_ddr_rdata,
    input  wire [1:0]              m_ddr_rresp,
    input  wire                    m_ddr_rlast,
    input  wire [AXI_ID_WIDTH-1:0] m_ddr_rid,

    // ---- status / debug (xspi_clk domain) ----
    output wire        xspi_busy,      // a frame is in progress
    output wire [31:0] xspi_last_addr, // last parsed address (debug)
    output wire [7:0]  xspi_last_cmd   // last parsed instruction (debug)
);

    localparam BEAT_BYTES = AXI_DATA_WIDTH / 8;

    // =====================================================================
    // Controller signal declarations.
    // These MUST appear before their first use (the FIFO/master instantiations
    // below and the front-end parser both reference them). Declaring them here
    // avoids Verilog implicit-wire creation from use-before-declaration.
    // =====================================================================
    localparam WR_FIFO_W = 32 + 16;   // {byte_addr[31:0], halfword[15:0]}
    localparam RD_FIFO_W = 16;        // one halfword per entry
    localparam CTL_W     = 1 + 1 + 16 + 32;

    localparam RD_IDLE = 1'd0, RD_ACTIVE = 1'd1;
    localparam WR_IDLE = 2'd0, WR_DRAIN = 2'd1, WR_WAIT = 2'd2;
    localparam HW_PER_BEAT = BEAT_BYTES / 2;

    // write FIFO read side (aclk)
    wire [WR_FIFO_W-1:0] w_rd_data;
    wire                 w_rd_empty;

    // read FIFO (aclk -> xspi)
    wire [RD_FIFO_W-1:0] rd_shift_out;   // next halfword to present on IO
    wire                 rd_rd_empty;    // read-FIFO empty (xspi side)
    wire [RD_FIFO_W-1:0] rd_wr_data;     // halfword pushed into the read FIFO
    wire                 rd_wr_en;
    wire                 rd_rd_en;       // pop the read FIFO (xspi side)

    // control FIFO
    reg                  ctl_push;
    wire [CTL_W-1:0]     ctl_rd_data;
    wire                 ctl_rd_empty;
    wire                 ctl_rd_en;      // pop the control FIFO (aclk side)

    // master command/data wires (driven by the engines below)
    reg                     reg_rd_start, ddr_rd_start;
    reg [AXI_ADDR_WIDTH-1:0] reg_rd_addr, ddr_rd_addr;
    reg                     reg_wr_start, ddr_wr_start;
    reg [AXI_ADDR_WIDTH-1:0] reg_wr_addr, ddr_wr_addr;
    wire                    reg_rd_busy, ddr_rd_busy;
    wire                    reg_rd_valid, ddr_rd_valid;
    wire [AXI_DATA_WIDTH-1:0] reg_rd_data, ddr_rd_data;
    wire                    reg_rd_last, ddr_rd_last;
    wire                    reg_rd_ready, ddr_rd_ready;   // read-channel ready (read engine)
    wire                    reg_wr_busy, ddr_wr_busy;
    wire                    reg_wr_done, ddr_wr_done;
    wire                    reg_wr_dr, ddr_wr_dr;         // wr_data_in_ready (from master)
    wire [AXI_DATA_WIDTH-1:0] reg_wr_data, ddr_wr_data;   // wr_data_in (to master)
    wire                    reg_wr_dv, ddr_wr_dv;         // wr_data_in_valid (to master)

    // write engine state
    reg [1:0]  wr_state;

    // ================= opcode constants =================
    localparam CMD_READ        = 8'h00;
    localparam CMD_READ_LB     = 8'h20;   // linear-burst read (memory-mapped)
    localparam CMD_WRITE       = 8'h80;
    localparam CMD_WRITE_LB    = 8'ha0;   // linear-burst write (memory-mapped)
    localparam CMD_RESET       = 8'hff;
    localparam CMD_REG_READ    = 8'h40;
    localparam CMD_REG_WRITE   = 8'hc0;

    // ================= xspi_clk domain: front-end parser =================
    reg        cs_n_q;
    wire       cs_fall = cs_n_q & ~xspi_cs_n;   // CS just went low (frame start)
    wire       cs_rise = ~cs_n_q & xspi_cs_n;   // CS just went high (frame end)

    always @(posedge xspi_clk or negedge arst_n) begin
        if (!arst_n)      cs_n_q <= 1'b1;
        else              cs_n_q <= xspi_cs_n;
    end

    localparam P_IDLE   = 3'd0;
    localparam P_CMD    = 3'd1;   // capture instruction on next rising edge
    localparam P_ADDR   = 3'd2;   // 4 cycles, DDR -> 32 bits (one byte per rising edge)
    localparam P_ADDR_D = 3'd5;   // assemble the full address word (all bytes settled)
    localparam P_DUMMY  = 3'd3;   // count dummy cycles
    localparam P_DATA   = 3'd4;   // read/write data, DDR

    reg [2:0]  phase;
    reg [7:0]  cmd_reg;
    reg [31:0] addr_reg;
    reg [1:0]  addr_cnt;          // 0..3 address bytes (one per rising edge)
    reg [7:0]  dummy_cnt;
    reg [7:0]  dummy_n;           // dummy count for the current frame
    reg        is_read;           // current frame is a read (slave drives IO)
    reg        is_reg;            // current frame targets the mode-register file
    reg [7:0]  io_out;            // value to drive on IO when reading
    reg        io_oe;             // output enable (drive IO high-Z otherwise)

    // DDR address bytes, MSB first. Captured across 2 cycles x 2 edges.
    reg [7:0] addr_b3, addr_b2, addr_b1, addr_b0;

    // write-data capture pipeline: upper byte on rising edge, lower byte on
    // falling edge. The completed halfword is committed one cycle later so the
    // falling-edge byte has time to settle.
    reg [7:0] w_hi;   // upper byte (rising edge)
    reg [7:0] w_lo;   // lower byte (falling edge)
    reg       w_valid; // a halfword is pending commit

    // DDR read: capture the current halfword's bytes at the rising edge so the
    // falling edge can present the lower byte of the SAME halfword.
    reg [7:0] rd_lo_q;

    // Halfwords seen in the current frame's data phase (both read and write).
    // Counted on the rising edge of each data cycle; this is the frame length
    // the aclk side needs for writes (xSPI does not pre-declare it -- CS
    // deassert ends it). Reset at the start of every frame.
    reg [15:0] hw_cnt;

    // Read-frame-active flag (xspi domain): high from the start of a read
    // frame's data phase until CS deasserts. Crossed into aclk so the read
    // engine knows when to stop prefetching (xSPI reads have no pre-declared
    // length -- the host ends them by raising CS).
    reg        rd_frame_active;

    // ---- front-end FSM (rising edge) ----
    always @(posedge xspi_clk or negedge arst_n) begin
        if (!arst_n) begin
            phase     <= P_IDLE;
            cmd_reg   <= 8'h00;
            addr_reg  <= 32'h0;
            addr_b3   <= 8'h00;
            addr_b2   <= 8'h00;
            addr_b1   <= 8'h00;
            addr_b0   <= 8'h00;
            addr_cnt  <= 2'd0;
            dummy_cnt <= 8'd0;
            dummy_n   <= 8'd0;
            is_read   <= 1'b0;
            is_reg    <= 1'b0;
            io_out    <= 8'h00;
            io_oe     <= 1'b0;
            w_valid   <= 1'b0;
            rd_lo_q   <= 8'h00;
            hw_cnt    <= 16'd0;
            rd_frame_active <= 1'b0;
        end else begin
            case (phase)
                P_IDLE: begin
                    io_oe <= 1'b0;
                    if (cs_fall) begin
                        phase  <= P_CMD;
                        hw_cnt <= 16'd0;   // fresh frame: reset the halfword count
                    end
                end

                P_CMD: begin
                    // A 1-byte frame (e.g. Reset 0xFF) ends here: CS deasserts
                    // before any address/data, so abort without pushing.
                    if (cs_rise) begin
                        phase <= P_IDLE;
                    end else begin
                        cmd_reg   <= xspi_io[7:0];
                        addr_cnt  <= 2'd0;
                        is_read   <= (xspi_io[7:0] == CMD_READ) ||
                                     (xspi_io[7:0] == CMD_READ_LB) ||
                                     (xspi_io[7:0] == CMD_REG_READ);
                        is_reg    <= (xspi_io[7:0] == CMD_REG_READ) ||
                                     (xspi_io[7:0] == CMD_REG_WRITE);
                        // dummy cycles per command (LatencyCode-1; reg-write=0):
                        //   read / write / reg-read -> 4 ; reg-write -> 0
                        dummy_n   <= (xspi_io[7:0] == CMD_REG_WRITE) ? 8'd0 : 8'd4;
                        phase     <= P_ADDR;
                    end
                end

                P_ADDR: begin
                    // Frame ended before the address completed -> abort.
                    if (cs_rise) begin
                        phase <= P_IDLE;
                    end else begin
                        // Capture all four DDR address bytes on rising edges,
                        // MSB first (the master drives each byte one falling
                        // edge before its sampling rising edge).
                        case (addr_cnt)
                            2'd0: addr_b3 <= xspi_io[7:0];
                            2'd1: addr_b2 <= xspi_io[7:0];
                            2'd2: addr_b1 <= xspi_io[7:0];
                            default: addr_b0 <= xspi_io[7:0];   // 2'd3
                        endcase
                        if (addr_cnt == 2'd3) begin
                            addr_reg <= {addr_b3, addr_b2, addr_b1, xspi_io[7:0]};
                            phase <= P_ADDR_D;   // all bytes latched; assemble next cycle
                        end else begin
                            addr_cnt <= addr_cnt + 2'd1;
                        end
                    end
                end

                P_ADDR_D: begin
                    // All four address bytes are now settled; assemble the word
                    // and move on. (Frame cannot end here -- the host holds CS
                    // low through the whole frame.)
                    addr_reg  <= {addr_b3, addr_b2, addr_b1, addr_b0};
                    // The master drives each byte one falling edge before its
                    // sampling rising edge, so by the time we reach P_DUMMY the
                    // DUT has already "used up" two posedges of the address/dummy
                    // boundary (byte0 sample + this assemble cycle). To line up
                    // P_DATA with the first data byte on the wire, count down from
                    // dummy_n-2 (transition after 3 posedges) instead of dummy_n.
                    // Calibrated against tb drive_frame: upper_0 must be sampled
                    // at the FIRST P_DATA posedge.
                    dummy_cnt <= (dummy_n >= 8'd2) ? (dummy_n - 8'd2) : 8'd0;
                    // Use dummy_n (the just-decoded value) for the phase decision,
                    // NOT dummy_cnt (which is still the old value due to non-blocking).
                    phase     <= (dummy_n != 8'd0) ? P_DUMMY : P_DATA;
                end

                P_DUMMY: begin
                    // Frame ended during the dummy cycles -> abort.
                    if (cs_rise) begin
                        phase <= P_IDLE;
                    end else if (is_read && dummy_cnt == 8'd1) begin
                        phase <= P_DATA;
                    end else if (dummy_cnt == 8'd0) begin
                        phase <= P_DATA;
                    end else begin
                        dummy_cnt <= dummy_cnt - 8'd1;
                    end
                end

                P_DATA: begin
                    if (is_read) begin
                        rd_frame_active <= 1'b1;   // host is reading: keep prefetching
                        // DDR read: drive the upper byte of the current halfword
                        // on this rising edge; capture the lower byte so the
                        // falling edge can present it.
                        if (is_reg) begin
                            io_out  <= mr_read(addr_reg[7:0]);
                            rd_lo_q <= 8'h00;
                        end else begin
                            io_out  <= rd_shift_out[15:8];   // upper byte (rising)
                            rd_lo_q <= rd_shift_out[7:0];    // lower byte (for fall)
                        end
                        io_oe <= 1'b1;
                        $display("RCOMMIT t=%0d ph=4 oe=%b shift=%h rempty=%b rden=%b iout=%h",
                                 $time, io_oe, rd_shift_out, rd_rd_empty, rd_rd_en, io_out);
                    end else begin
                        // write: capture this cycle's upper byte.
                        io_oe <= 1'b0;
                        w_hi    <= xspi_io[7:0];   // upper byte of this cycle
                        w_valid <= 1'b1;
                    end
                    if (cs_rise) begin
                        phase <= P_IDLE;
                        io_oe <= 1'b0;
                        w_valid <= 1'b0;
                        rd_frame_active <= 1'b0;   // frame over: stop prefetching
                    end
                end

                default: phase <= P_IDLE;
            endcase
        end
    end

    // ---- falling-edge handling (DDR data capture) ----
    always @(negedge xspi_clk or negedge arst_n) begin
        if (!arst_n) begin
            w_lo    <= 8'h00;
        end else begin
            case (phase)
                P_DATA: begin
                    if (!is_read)
                        w_lo <= xspi_io[7:0];      // lower byte of write data
                    else
                        io_out <= rd_lo_q;         // lower byte (falling)
                end
                default: ;
            endcase
        end
    end

    // ================= mode register file (MR0..MR8) =================
    // The host boots by reading/writing these (SPEC R4). Defaults match the
    // APS256XX power-on values so the ID read-back is correct.
    reg [7:0] mr0, mr1, mr2, mr3, mr4, mr6, mr8;

    function [7:0] mr_read;
        input [7:0] idx;
        begin
            case (idx)
                8'd0: mr_read = mr0;
                8'd1: mr_read = mr1;
                8'd2: mr_read = mr2;
                8'd3: mr_read = mr3;
                8'd4: mr_read = mr4;
                8'd6: mr_read = mr6;
                8'd8: mr_read = mr8;
                default: mr_read = 8'h00;
            endcase
        end
    endfunction

    // reg-write: store a value into the addressed mode register.
    task mr_write;
        input [7:0] idx;
        input [15:0] val;
        begin
            case (idx)
                8'd0: mr0 <= val[7:0];
                8'd1: mr1 <= val[7:0];
                8'd2: mr2 <= val[7:0];
                8'd3: mr3 <= val[7:0];
                8'd4: mr4 <= val[7:0];
                8'd6: mr6 <= val[7:0];
                8'd8: mr8 <= val[7:0];
                default: ;
            endcase
        end
    endtask

    // Global reset (0xFF): mode registers back to defaults.
    always @(posedge xspi_clk or negedge arst_n) begin
        if (!arst_n) begin
            mr0 <= 8'h00; mr1 <= 8'hd; mr2 <= 8'h7; mr3 <= 8'h00;
            mr4 <= 8'h00; mr6 <= 8'h00; mr8 <= 8'h00;
        end else if ((phase == P_CMD) && (xspi_io[7:0] == CMD_RESET)) begin
            mr0 <= 8'h00; mr1 <= 8'hd; mr2 <= 8'h7; mr3 <= 8'h00;
            mr4 <= 8'h00; mr6 <= 8'h00; mr8 <= 8'h00;
        end
    end

    // ================= xspi -> aclk : write-data FIFO =================
    // Each committed write halfword (16 bits) is pushed with its running byte
    // address. The aclk side assembles beats and issues AXI writes.

    // Write-data pipeline (fixes the off-by-one in the naive "push every data
    // cycle" approach, which pushed a stale first halfword and dropped the last):
    //   rising edge of data cycle i : w_hi <= io (upper byte of hw_i)
    //   falling edge of data cycle i : hw_pipe <= {w_hi, io} = hw_i (complete)
    //   rising edge of cycle i+1     : push hw_i into the FIFO
    // The final halfword is pushed on the cycle after the last data cycle, which
    // is exactly when CS deasserts -- so it is captured before the frame ends.
    //
    // hw_push_en must be COMBINATIONAL (not registered): if it were registered,
    // it would lag `phase` by one cycle, pushing the first halfword late and the
    // last halfword after CS deasserts (dropping hw0 and shifting the rest).
    // With it combinational, the push at posedge N (the first P_DATA posedge)
    // emits hw_pipe which was loaded at negedge N-1 = hw0, and each subsequent
    // posedge emits the next halfword, with the last one landing just before CS
    // deasserts.
    // Write-data pipeline. The DUT samples the UPPER byte on the rising edge and
    // the LOWER byte on the falling edge of the SAME SCK cycle (TB drives upper at
    // negedge+1, lower at posedge+1). At each P_DATA NEGEDGE, both bytes of the
    // current halfword are known:
    //   upper_i = w_hi register (sampled on the posedge earlier in this cycle)
    //   lower_i = xspi_io read directly from the wire right now
    // hw_pipe captures {w_hi, xspi_io} at each P_DATA negedge. The FIFO write
    // happens on the following P_DATA posedge, so the FIRST P_DATA posedge emits
    // a stale pipe value (loaded before w_hi was valid). We suppress it with
    // wr_data_started: 0 on the first P_DATA posedge, 1 thereafter.
    wire        hw_push_en = (phase == P_DATA) && !is_read;
    reg [7:0]   hw_pipe_hi, hw_pipe_lo;
    reg         wr_data_started;
    always @(negedge xspi_clk or negedge arst_n) begin
        if (!arst_n) begin
            hw_pipe_hi <= 8'h00;
            hw_pipe_lo <= 8'h00;
        end else if ((phase == P_DATA) && !is_read) begin
            hw_pipe_hi <= w_hi;               // upper byte (stable since this cycle's posedge)
            hw_pipe_lo <= xspi_io[7:0];       // lower byte (present on the wire this negedge)
        end
    end
    // Set wr_data_started on the second P_DATA edge (first valid pipe value).
    always @(posedge xspi_clk or negedge arst_n) begin
        if (!arst_n)
            wr_data_started <= 1'b0;
        else if (cs_fall)
            wr_data_started <= 1'b0;          // reset on new frame
        else if ((phase == P_DATA) && !is_read)
            wr_data_started <= 1'b1;          // high after first P_DATA posedge
    end
    // hw_pipe is only valid once it has been loaded at a P_DATA negedge this
    // frame. The very first P_DATA posedge of a frame still holds the previous
    // frame's (or reset) pipe value, so we must not commit on that edge -- even
    // for a 1-halfword frame, where that edge is the ONLY data edge and hw_pipe
    // was loaded at the immediately-preceding negedge. Tracking "loaded this
    // frame" (set on the first P_DATA negedge) is exact: it is high by the first
    // posedge for any frame with >=1 data cycle, so a 1-hw frame commits its
    // single halfword and multi-hw frames are unchanged.
    reg pipe_loaded;
    always @(negedge xspi_clk or negedge arst_n) begin
        if (!arst_n)
            pipe_loaded <= 1'b0;
        else if (cs_fall)
            pipe_loaded <= 1'b0;
        else if ((phase == P_DATA) && !is_read)
            pipe_loaded <= 1'b1;
    end
    wire [WR_FIFO_W-1:0] w_wr_data = {addr_reg, {hw_pipe_hi, hw_pipe_lo}};
    // Per-posedge commit: suppress the first (stale) P_DATA posedge via
    // wr_data_started. For a multi-halfword frame this commits exactly the N
    // valid halfwords at posedges 2..N+1. A 1-halfword frame has only ONE
    // posedge (the stale one), so it commits nothing here -- its single valid
    // halfword is flushed at CS deassert instead (see w_flush below).
    wire                 w_commit  = hw_push_en && !w_wr_full && wr_data_started;
    wire                 w_wr_full;

    // TEMP DEBUG: log every committed write halfword (front-end -> FIFO).
    always @(posedge xspi_clk) begin
        if (w_commit)
            $display("WCOMMIT t=%0t addr=%h hw=%h", $time, addr_reg, {hw_pipe_hi, hw_pipe_lo});
    end

    // TEMP DEBUG: full per-edge trace of the write pipeline (P_DATA only).
    always @(posedge xspi_clk) begin
        if ((phase == P_DATA) && !is_read)
            $display("WPIPE t=%0t POS io=%h w_hi=%h w_lo=%h pipe={%h,%h} push=%b",
                $time, xspi_io, w_hi, w_lo, hw_pipe_hi, hw_pipe_lo, hw_push_en);
    end
    // TEMP DEBUG: phase + raw wire on every edge (dummy/data entry alignment).
    always @(posedge xspi_clk) begin
        if (!is_read && (phase == P_DUMMY || phase == P_DATA))
            $display("PH t=%0t POS ph=%0d io=%h", $time, phase, xspi_io);
    end
    always @(negedge xspi_clk) begin
        if (!is_read && (phase == P_DUMMY || phase == P_DATA))
            $display("PH t=%0t NEG ph=%0d io=%h", $time, phase, xspi_io);
    end
    always @(negedge xspi_clk) begin
        if ((phase == P_DATA) && !is_read)
            $display("WPIPE t=%0t NEG io=%h w_hi=%h w_lo=%h pipe={%h,%h} push=%b",
                $time, xspi_io, w_hi, w_lo, hw_pipe_hi, hw_pipe_lo, hw_push_en);
    end

    // Number of halfwords actually committed to the write FIFO this frame.
    // Counting w_commit (not posedges) is exact: each committed halfword is one
    // FIFO entry, so the count equals the frame's data length with no off-by-one
    // from the P_DATA entry/exit edges. Reset at frame start.
    reg [15:0] wr_hw_cnt;
    always @(posedge xspi_clk or negedge arst_n) begin
        if (!arst_n)      wr_hw_cnt <= 16'd0;
        else if (cs_fall) wr_hw_cnt <= 16'd0;   // fresh frame
        else if (w_commit) wr_hw_cnt <= wr_hw_cnt + 16'd1;
    end

    async_fifo #(
        .DATA_WIDTH(WR_FIFO_W),
        .DEPTH(WR_FIFO_DEPTH)
    ) u_wfifo (
        .wr_clk(xspi_clk), .rst_n(arst_n),
        .wr_en(w_commit), .wr_data(w_wr_data), .wr_full(w_wr_full),
        .rd_clk(aclk),     .rd_en(wr_state == WR_DRAIN && !w_rd_empty),
                            .rd_data(w_rd_data), .rd_empty(w_rd_empty)
    );

    // ================= aclk -> xspi : read-data FIFO =================
    wire                 rd_wr_full;
    async_fifo #(
        .DATA_WIDTH(RD_FIFO_W),
        .DEPTH(RD_FIFO_DEPTH)
    ) u_rdfifo (
        .wr_clk(aclk),     .rst_n(arst_n),
        .wr_en(rd_wr_en),  .wr_data(rd_wr_data), .wr_full(rd_wr_full),
        .rd_clk(xspi_clk), .rd_en(rd_rd_en),     .rd_data(rd_shift_out), .rd_empty(rd_rd_empty)
    );

    // ================= aclk domain: AXI bridge controller =================
    // Decodes the host address into reg vs DDR4, and drives two axi4_master
    // instances. Writes are staged in the write FIFO and flushed as one or more
    // AXI bursts when the frame completes (CS deassert). Reads start as soon as
    // the address is known (end of P_ADDR) so data is prefetched into the read
    // FIFO before the host reaches the data phase.
    //
    // Control word: {is_read, is_reg, len[15:0], addr[31:0]} = 50 bits.
    //   - reads : pushed at end of P_ADDR (addr valid) so the fetch starts early.
    //             The length is not yet known (data phase hasn't happened), so a
    //             fixed prefetch size is used (SPEC §6: prefetch, discard unused).
    //   - writes: pushed at CS deassert (frame complete) carrying the exact
    //             committed halfword count (wr_hw_cnt).
    localparam [15:0] RD_PREFETCH_HW = 16'd16;   // 16 halfwords = 32 bytes = 8 beats
    wire [15:0] ctl_len = is_read ? RD_PREFETCH_HW
                                  : (wr_hw_cnt + {15'd0, w_commit});
    wire [CTL_W-1:0] ctl_wr_data = {is_read, is_reg, ctl_len, addr_reg};
    wire             ctl_wr_full;
    async_fifo #(
        .DATA_WIDTH(CTL_W),
        .DEPTH(8)
    ) u_ctlfifo (
        .wr_clk(xspi_clk), .rst_n(arst_n),
        .wr_en(ctl_push),  .wr_data(ctl_wr_data), .wr_full(ctl_wr_full),
        .rd_clk(aclk),     .rd_en(ctl_rd_en),     .rd_data(ctl_rd_data), .rd_empty(ctl_rd_empty)
    );

    wire head_is_read = ctl_rd_data[CTL_W-1];
    wire head_is_reg  = ctl_rd_data[CTL_W-2];
    wire [15:0] head_len_hw = ctl_rd_data[CTL_W-3 : CTL_W-18];
    wire [31:0] head_addr   = ctl_rd_data[31:0];
    wire head_is_reg_region = (head_addr < DDR_BASE);
    wire [31:0] head_target_addr = head_is_reg_region ? (head_addr - REG_BASE) : (head_addr - DDR_BASE);

    // Pop the control FIFO on the cycle an engine latches it.
    assign ctl_rd_en = (rd_state == RD_IDLE && !ctl_rd_empty && head_is_read) ||
                       (wr_state == WR_IDLE && f_valid && !f_is_read);

    // Push timing: reads at end of P_ADDR (so the fetch starts during the dummy
    // cycles); writes at CS deassert (frame complete, length now known).
    // Reg reads are NOT pushed: they serve data from mr_read() directly in the
    // xspi_clk domain and don't use the read FIFO. Pushing them would pollute
    // the FIFO with stale/xxxx entries that shift subsequent DDR reads.
    always @(posedge xspi_clk or negedge arst_n) begin
        if (!arst_n)      ctl_push <= 1'b0;
        else              ctl_push <= (is_read && !is_reg && (phase == P_ADDR && addr_cnt == 2'd3)) ||
                                      (cs_rise && (phase == P_DATA) && !is_read);
    end

    // ---- aclk-side frame state (decoded from the control word) ----
    reg [31:0] f_addr;
    reg        f_is_read;
    reg        f_is_reg;
    reg [15:0] f_len_hw;                 // halfwords in this frame
    reg        f_valid;                  // a latched control word is pending for the engines
    wire       f_is_reg_region = (f_addr < DDR_BASE);   // reg region is below DDR_BASE
    // SPEC §5: the host sees one contiguous space from 0x9000_0000. The bridge
    // decodes which slave and presents the OFFSET within that slave's own space:
    //   reg region (0x9000_0000..): offset = f_addr - REG_BASE  -> matmul_top.s_axi_*
    //   DDR region (0x9001_0000..): offset = f_addr - DDR_BASE  -> MIG DDR4
    // Both slaves are addressed from their own base (0), so the offset is what the
    // AXI master drives. (Passing the full host address through would index far
    // outside each slave's memory.)
    wire [31:0] f_target_addr  = f_is_reg_region ? (f_addr - REG_BASE) : (f_addr - DDR_BASE);

    // Latch the control word into frame state when the FIFO has data. The latched
    // f_* values only become valid one cycle after the latch, so f_valid is set on
    // the FOLLOWING edge (and held while the FIFO still holds the word) and cleared
    // once an engine consumes the frame (ctl_rd_en pops the FIFO -> empty).
    always @(posedge aclk or negedge arst_n) begin
        if (!arst_n) begin
            f_addr    <= 32'd0;
            f_is_read <= 1'b0;
            f_is_reg  <= 1'b0;
            f_len_hw  <= 16'd0;
            f_valid   <= 1'b0;
        end else begin
            if (!ctl_rd_empty) begin
                // (re)latch the head control word every cycle it is present.
                f_addr    <= ctl_rd_data[31:0];
                f_is_read <= ctl_rd_data[CTL_W-1];
                f_is_reg  <= ctl_rd_data[CTL_W-2];
                f_len_hw  <= ctl_rd_data[CTL_W-3 : CTL_W-18];   // hw_cnt[15:0] = bits [47:32]
            end
            // f_valid is high exactly while the latched f_* are valid and not yet
            // consumed: one cycle after a word first appears, until it is popped.
            if (!ctl_rd_empty && !f_valid)
                f_valid <= 1'b1;
            else if (ctl_rd_en)
                f_valid <= 1'b0;
        end
    end

    // ---- axi4_master instances (reg region + DDR4 region) ----
    localparam RD_LEN_W = 20;            // max transfer 2^20 bytes (1 MB) per frame
    wire [RD_LEN_W-1:0] reg_rd_len, ddr_rd_len;
    wire [RD_LEN_W-1:0] reg_wr_len, ddr_wr_len;

    axi4_master #(
        .ADDR_WIDTH(AXI_ADDR_WIDTH), .DATA_WIDTH(AXI_DATA_WIDTH),
        .ID_WIDTH(AXI_ID_WIDTH), .MAX_RD_BURSTS(4), .RD_LEN_W(RD_LEN_W)
    ) u_reg_master (
        .aclk(aclk), .aresetn(arst_n),
        .rd_start(reg_rd_start), .rd_addr(reg_rd_addr), .rd_len_bytes(reg_rd_len),
        .rd_busy(reg_rd_busy),
        .rd_data_valid(reg_rd_valid), .rd_data(reg_rd_data), .rd_last(reg_rd_last),
        .rd_data_ready(reg_rd_ready),
        .wr_start(reg_wr_start), .wr_addr(reg_wr_addr), .wr_len_bytes(reg_wr_len),
        .wr_busy(reg_wr_busy),
        .wr_data_in_valid(reg_wr_dv), .wr_data_in(reg_wr_data), .wr_data_in_ready(reg_wr_dr),
        .wr_done(reg_wr_done),
        .m_axi_awvalid(m_reg_awvalid), .m_axi_awready(m_reg_awready),
        .m_axi_awaddr(m_reg_awaddr), .m_axi_awlen(m_reg_awlen), .m_axi_awsize(m_reg_awsize),
        .m_axi_awburst(m_reg_awburst), .m_axi_awid(m_reg_awid),
        .m_axi_wvalid(m_reg_wvalid), .m_axi_wready(m_reg_wready),
        .m_axi_wdata(m_reg_wdata), .m_axi_wstrb(m_reg_wstrb), .m_axi_wlast(m_reg_wlast),
        .m_axi_bvalid(m_reg_bvalid), .m_axi_bready(m_reg_bready),
        .m_axi_bresp(m_reg_bresp), .m_axi_bid(m_reg_bid),
        .m_axi_arvalid(m_reg_arvalid), .m_axi_arready(m_reg_arready),
        .m_axi_araddr(m_reg_araddr), .m_axi_arlen(m_reg_arlen), .m_axi_arsize(m_reg_arsize),
        .m_axi_arburst(m_reg_arburst), .m_axi_arid(m_reg_arid),
        .m_axi_rvalid(m_reg_rvalid), .m_axi_rready(m_reg_rready),
        .m_axi_rdata(m_reg_rdata), .m_axi_rresp(m_reg_rresp),
        .m_axi_rlast(m_reg_rlast), .m_axi_rid(m_reg_rid)
    );

    axi4_master #(
        .ADDR_WIDTH(AXI_ADDR_WIDTH), .DATA_WIDTH(AXI_DATA_WIDTH),
        .ID_WIDTH(AXI_ID_WIDTH), .MAX_RD_BURSTS(4), .RD_LEN_W(RD_LEN_W)
    ) u_ddr_master (
        .aclk(aclk), .aresetn(arst_n),
        .rd_start(ddr_rd_start), .rd_addr(ddr_rd_addr), .rd_len_bytes(ddr_rd_len),
        .rd_busy(ddr_rd_busy),
        .rd_data_valid(ddr_rd_valid), .rd_data(ddr_rd_data), .rd_last(ddr_rd_last),
        .rd_data_ready(ddr_rd_ready),
        .wr_start(ddr_wr_start), .wr_addr(ddr_wr_addr), .wr_len_bytes(ddr_wr_len),
        .wr_busy(ddr_wr_busy),
        .wr_data_in_valid(ddr_wr_dv), .wr_data_in(ddr_wr_data), .wr_data_in_ready(ddr_wr_dr),
        .wr_done(ddr_wr_done),
        .m_axi_awvalid(m_ddr_awvalid), .m_axi_awready(m_ddr_awready),
        .m_axi_awaddr(m_ddr_awaddr), .m_axi_awlen(m_ddr_awlen), .m_axi_awsize(m_ddr_awsize),
        .m_axi_awburst(m_ddr_awburst), .m_axi_awid(m_ddr_awid),
        .m_axi_wvalid(m_ddr_wvalid), .m_axi_wready(m_ddr_wready),
        .m_axi_wdata(m_ddr_wdata), .m_axi_wstrb(m_ddr_wstrb), .m_axi_wlast(m_ddr_wlast),
        .m_axi_bvalid(m_ddr_bvalid), .m_axi_bready(m_ddr_bready),
        .m_axi_bresp(m_ddr_bresp), .m_axi_bid(m_ddr_bid),
        .m_axi_arvalid(m_ddr_arvalid), .m_axi_arready(m_ddr_arready),
        .m_axi_araddr(m_ddr_araddr), .m_axi_arlen(m_ddr_arlen), .m_axi_arsize(m_ddr_arsize),
        .m_axi_arburst(m_ddr_arburst), .m_axi_arid(m_ddr_arid),
        .m_axi_rvalid(m_ddr_rvalid), .m_axi_rready(m_ddr_rready),
        .m_axi_rdata(m_ddr_rdata), .m_axi_rresp(m_ddr_rresp),
        .m_axi_rlast(m_ddr_rlast), .m_axi_rid(m_ddr_rid)
    );

    // ================= READ engine (aclk) =================
    // On a read control word: issue rd_start on the right master with
    // len = ceil(f_len_hw*2 / beat) beats. Consume returned beats and push
    // their halfwords into the read FIFO in xSPI DDR order: for each 32-bit
    // beat [A+3 A+2 A+1 A+0] push {A+1,A+0} then {A+3,A+2}.
    reg        rd_state;
    reg        rd_target_reg;            // this read targets the reg master
    reg [15:0] rd_hw_left;               // halfwords still to push into rd FIFO
    reg [15:0] rd_beat_cnt;              // beats consumed so far this frame
    reg [15:0] rd_total_beats;           // total beats for this frame

    // Round the frame's byte count up to a whole number of beats (>= 1 beat).
    wire [15:0] cur_rd_hw = (rd_state == RD_IDLE && !ctl_rd_empty && head_is_read) ? head_len_hw : f_len_hw;
    wire [15:0] rd_frame_bytes = {4'd0, cur_rd_hw, 1'b0};   // hw_cnt * 2 bytes
    wire [15:0] rd_beats_raw   = (rd_frame_bytes + BEAT_BYTES - 16'd1) / BEAT_BYTES;
    wire [15:0] rd_total_beats_comb = (rd_beats_raw == 16'd0) ? 16'd1 : rd_beats_raw;

    // Total bytes to read = whole beats * beat size (a multiple of the beat).
    wire [15:0] rd_len_bytes = rd_total_beats_comb * BEAT_BYTES;

    // rd_len_bytes is driven combinationally for the cycle rd_start pulses.
    wire cur_rd_is_reg = (rd_state == RD_IDLE) ? head_is_reg_region : rd_target_reg;
    assign reg_rd_len = cur_rd_is_reg  ? {4'd0, rd_len_bytes} : {RD_LEN_W{1'b0}};
    assign ddr_rd_len = !cur_rd_is_reg ? {4'd0, rd_len_bytes} : {RD_LEN_W{1'b0}};

    always @(posedge aclk or negedge arst_n) begin
        if (!arst_n) begin
            rd_state       <= RD_IDLE;
            rd_target_reg  <= 1'b0;
            rd_hw_left     <= 16'd0;
            rd_beat_cnt    <= 16'd0;
            rd_total_beats <= 16'd0;
            reg_rd_start   <= 1'b0;
            ddr_rd_start   <= 1'b0;
            reg_rd_addr    <= {AXI_ADDR_WIDTH{1'b0}};
            ddr_rd_addr    <= {AXI_ADDR_WIDTH{1'b0}};
        end else begin
            reg_rd_start <= 1'b0;
            ddr_rd_start <= 1'b0;

            if (rd_state == RD_IDLE) begin
                if (!ctl_rd_empty && head_is_read) begin
                    // latch the frame and issue the read on the correct master immediately
                    rd_state       <= RD_ACTIVE;
                    rd_target_reg  <= head_is_reg_region;
                    rd_hw_left     <= head_len_hw;
                    rd_beat_cnt    <= 16'd0;
                    rd_total_beats <= rd_total_beats_comb;
                    if (head_is_reg_region) begin
                        reg_rd_start <= 1'b1;
                        reg_rd_addr  <= head_target_addr;
                    end else begin
                        ddr_rd_start <= 1'b1;
                        ddr_rd_addr  <= head_target_addr;
                    end
                end else if (f_valid && f_is_read) begin
                    rd_state       <= RD_ACTIVE;
                    rd_target_reg  <= f_is_reg_region;
                    rd_hw_left     <= f_len_hw;
                    rd_beat_cnt    <= 16'd0;
                    rd_total_beats <= rd_total_beats_comb;
                    if (f_is_reg_region) begin
                        reg_rd_start <= 1'b1;
                        reg_rd_addr  <= f_target_addr;
                    end else begin
                        ddr_rd_start <= 1'b1;
                        ddr_rd_addr  <= f_target_addr;
                    end
                end
            end else begin
                // consume returned beats (from whichever master is active)
                if (rd_target_reg && reg_rd_valid && reg_rd_ready) begin
                    rd_beat_cnt <= rd_beat_cnt + 16'd1;
                    // stay ACTIVE until the push phase drains (one extra cycle
                    // to push the high halfword of the last beat)
                    if ((rd_beat_cnt == rd_total_beats - 16'd1) && !rd_push_phase)
                        rd_state <= RD_IDLE;
                end else if (!rd_target_reg && ddr_rd_valid && ddr_rd_ready) begin
                    rd_beat_cnt <= rd_beat_cnt + 16'd1;
                    if ((rd_beat_cnt == rd_total_beats - 16'd1) && !rd_push_phase)
                        rd_state <= RD_IDLE;
                end
            end
        end
    end

    // Consume read beats into the read FIFO as two halfwords per beat
    // (upper byte first to match xspi DDR order).
    assign reg_rd_ready  = (rd_state == RD_ACTIVE) && rd_target_reg  && !rd_wr_full;
    assign ddr_rd_ready  = (rd_state == RD_ACTIVE) && !rd_target_reg && !rd_wr_full;

    // Push two halfwords per AXI beat into the read FIFO, in address order:
    //   low  halfword = beat[15:0]  (bytes A+1,A+0)  -> first SCK cycle
    //   high halfword = beat[31:16] (bytes A+3,A+2)  -> second SCK cycle
    // The consumed beat is latched so the high halfword can be pushed on the
    // following cycle even after the master advances to the next beat.
    wire [AXI_DATA_WIDTH-1:0] rd_mux_data = rd_target_reg ? reg_rd_data : ddr_rd_data;
    wire rd_beat_consumed = (rd_state == RD_ACTIVE) && !rd_wr_full &&
        ((rd_target_reg && reg_rd_valid && reg_rd_ready) ||
         (!rd_target_reg && ddr_rd_valid && ddr_rd_ready));

    reg        rd_push_phase;            // 1 = next cycle push the high halfword
    reg [AXI_DATA_WIDTH-1:0] rd_beat_hold;
    always @(posedge aclk or negedge arst_n) begin
        if (!arst_n) begin
            rd_push_phase <= 1'b0;
            rd_beat_hold  <= {AXI_DATA_WIDTH{1'b0}};
        end else begin
            if (rd_beat_consumed) begin
                rd_beat_hold  <= rd_mux_data;   // latch the consumed beat
                rd_push_phase <= 1'b1;          // next cycle: push high halfword
            end else if (rd_push_phase == 1'b1) begin
                rd_push_phase <= 1'b0;          // done pushing this beat's pair
            end
        end
    end

    assign rd_wr_en   = (rd_state == RD_ACTIVE) && !rd_wr_full &&
                        (rd_beat_consumed || rd_push_phase);
    // On the consume cycle push the live low halfword; on the following cycle
    // push the latched high halfword.
    assign rd_wr_data = rd_push_phase ? rd_beat_hold[31:16] : rd_mux_data[15:0];

    always @(posedge aclk) begin
        if (rd_wr_en)
            $display("RDWR  t=%0d wrdata=%h pushphase=%b consumed=%b state=%b",
                     $time, rd_wr_data, rd_push_phase, rd_beat_consumed, rd_state);
    end

    // ---- debug trace: read-FIFO pointer sync (aclk side) ----
    always @(posedge aclk) begin
        if (rd_wr_en || rd_rd_en)
            $display("RDFIFO t=%0d wren=%b rden=%b wrbin=%0d rdbin=%0d empty=%b full=%b",
                     $time, rd_wr_en, rd_rd_en,
                     xspi_slave.u_rdfifo.wr_bin[8:0], xspi_slave.u_rdfifo.rd_bin[8:0],
                     rd_rd_empty, rd_wr_full);
    end

    // ---- debug trace: start pulses + engine FSM states ----
    always @(posedge aclk) begin
        if (reg_rd_start || ddr_rd_start)
            $display("RDSTART t=%0d reg=%b ddr=%b isread=%b faddr=%h target=%h len=%0d",
                     $time, reg_rd_start, ddr_rd_start, f_is_read, f_addr, f_target_addr, rd_len_bytes);
        if (reg_wr_start || ddr_wr_start)
            $display("WRSTART t=%0d reg=%b ddr=%b isread=%b faddr=%h target=%h len=%0d",
                     $time, reg_wr_start, ddr_wr_start, f_is_read, f_addr, f_target_addr, wr_len_bytes);
        if (ctl_push)
            $display("CTLPUSH t=%0d isread=%b isreg=%b len=%0d addr=%h phase=%d",
                     $time, is_read, is_reg, ctl_len, addr_reg, phase);
    end

    always @(posedge xspi_clk) begin
        if (phase == P_DATA && is_read)
            $display("FIFOPTR t=%0d rd_bin=%0d wr_bin=%0d rd_gray=%b wr_gray_sync2=%b rempty=%b rden=%b shift=%h",
                     $time, u_rdfifo.rd_bin, u_rdfifo.wr_bin,
                     u_rdfifo.rd_gray, u_rdfifo.wr_gray_sync2,
                     rd_rd_empty, rd_rd_en, rd_shift_out);
    end

    // ---- debug trace: engine FSM state transitions + consumption ----
    reg rd_state_p;
    reg [1:0] wr_state_p;
    always @(posedge aclk) begin
        if (rd_state !== rd_state_p)
            $display("RDSTATE t=%0d %b->%b targetreg=%b beatcnt=%0d total=%0d pushph=%b fvalid=%b isread=%b",
                     $time, rd_state_p, rd_state, rd_target_reg, rd_beat_cnt, rd_total_beats, rd_push_phase, f_valid, f_is_read);
        if (wr_state !== wr_state_p)
            $display("WRSTATE t=%0d %b->%b targetreg=%b hwleft=%0d hwpb=%0d beatvalid=%b fvalid=%b isread=%b",
                     $time, wr_state_p, wr_state, wr_target_reg, wr_hw_left, wr_hwpb, wr_beat_valid, f_valid, f_is_read);
        rd_state_p <= rd_state;
        wr_state_p <= wr_state;
        if (rd_beat_consumed)
            $display("RDCONS t=%0d targetreg=%b beatcnt=%0d total=%0d muxdata=%h",
                     $time, rd_target_reg, rd_beat_cnt, rd_total_beats, rd_mux_data);
    end

    // ================= WRITE engine (aclk) =================
    // On a write control word: drain f_len_hw halfwords from the write FIFO,
    // pack them into beats (low-address halfword first), and stream them to the
    // correct master. wr_start is issued once at the start of draining with the
    // full byte length; each assembled beat is presented to the master's
    // write-data input and held until accepted (wr_data_in_ready).
    reg        wr_target_reg;
    reg [15:0] wr_hw_left;               // halfwords still to drain from w FIFO
    reg [3:0]  wr_hwpb;                  // halfwords packed into the current beat
    reg [AXI_DATA_WIDTH-1:0] wr_beat;    // the beat currently assembled/presented
    reg        wr_beat_valid;            // wr_beat is a complete beat to present

    // FIFO entry layout (packed at the xspi side, line ~486):
    //   { addr_reg[31:0], halfword[15:0] }  =>  [WR_FIFO_W-1:16] = addr, [15:0] = hw.
    // Unpack MUST match that layout exactly -- an off-by-one part-select here
    // silently feeds the address's low bits into the write data (26/26 mismatch).
    wire [31:0] w_fifo_addr = w_rd_data[WR_FIFO_W-1:16];
    wire [15:0] w_fifo_hw   = w_rd_data[15:0];

    // total bytes for the frame, rounded UP to a whole number of beats (>= 1).
    // The axi4_master requires wr_len_bytes to be a multiple of the beat size.
    wire [15:0] wr_frame_bytes = {4'd0, f_len_hw, 1'b0};   // hw_cnt * 2 bytes
    wire [15:0] wr_beats_raw   = (wr_frame_bytes + BEAT_BYTES - 16'd1) / BEAT_BYTES;
    wire [15:0] wr_total_beats = (wr_beats_raw == 16'd0) ? 16'd1 : wr_beats_raw;
    wire [15:0] wr_len_bytes   = wr_total_beats * BEAT_BYTES;

    // write length in bytes, driven combinationally for the wr_start cycle.
    assign reg_wr_len  = wr_target_reg  ? {4'd0, wr_len_bytes} : {RD_LEN_W{1'b0}};
    assign ddr_wr_len  = !wr_target_reg ? {4'd0, wr_len_bytes} : {RD_LEN_W{1'b0}};

    // the master accepts the presented beat this cycle
    wire wr_accept = wr_beat_valid &&
                     ((wr_target_reg && reg_wr_dr) || (!wr_target_reg && ddr_wr_dr));

    always @(posedge aclk or negedge arst_n) begin
        if (!arst_n) begin
            wr_state      <= WR_IDLE;
            wr_target_reg <= 1'b0;
            wr_hw_left    <= 16'd0;
            wr_hwpb       <= 4'd0;
            reg_wr_start  <= 1'b0;
            ddr_wr_start  <= 1'b0;
            reg_wr_addr   <= {AXI_ADDR_WIDTH{1'b0}};
            ddr_wr_addr   <= {AXI_ADDR_WIDTH{1'b0}};
            wr_beat       <= {AXI_DATA_WIDTH{1'b0}};
            wr_beat_valid <= 1'b0;
        end else begin
            reg_wr_start <= 1'b0;
            ddr_wr_start <= 1'b0;

            case (wr_state)
            WR_IDLE: begin
                // Skip zero-length writes: nothing to drain, and issuing wr_start
                // would tell the master "1 beat coming" that never arrives -> it
                // waits in WR_WAIT forever (no W beats -> no wr_done), wedging the
                // shared engine and blocking every later frame. A len=0 word has
                // no data; just let ctl_rd_en pop it and stay in WR_IDLE.
                if (f_valid && !f_is_read && f_len_hw != 16'd0) begin
                    // latch the frame, issue wr_start once, begin draining
                    wr_state      <= WR_DRAIN;
                    wr_target_reg <= f_is_reg_region;
                    wr_hw_left    <= f_len_hw;
                    wr_hwpb       <= 4'd0;
                    wr_beat       <= {AXI_DATA_WIDTH{1'b0}};
                    wr_beat_valid <= 1'b0;
                    if (f_is_reg_region) begin
                        reg_wr_start <= 1'b1;
                        reg_wr_addr  <= f_target_addr;
                    end else begin
                        ddr_wr_start <= 1'b1;
                        ddr_wr_addr  <= f_target_addr;
                    end
                end
            end

            WR_DRAIN: begin
                if (wr_accept) begin
                    // the presented beat was accepted; start packing the next
                    wr_beat_valid <= 1'b0;
                    if (!w_rd_empty) begin
                        wr_beat    <= w_fifo_hw;          // first hw of new beat
                        wr_hwpb    <= 4'd1;
                        wr_hw_left <= wr_hw_left - 16'd1;
                    end else begin
                        wr_hwpb    <= 4'd0;               // nothing left to pack
                    end
                end else if (!wr_beat_valid && !w_rd_empty) begin
                    // pack the next halfword into its slot (variable shift-OR,
                    // legal in Verilog-2001 and width-generic)
                    wr_beat    <= wr_beat | (w_fifo_hw << (wr_hwpb * 16));
                    wr_hwpb    <= wr_hwpb + 4'd1;
                    wr_hw_left <= wr_hw_left - 16'd1;
                    if (wr_hwpb == HW_PER_BEAT[3:0] - 4'd1)
                        wr_beat_valid <= 1'b1;   // beat now full
                end
                // flush a final partial beat (only possible for unaligned sizes)
                if ((wr_hw_left == 16'd0) && w_rd_empty && !wr_beat_valid &&
                    wr_hwpb != 4'd0) begin
                    wr_beat_valid <= 1'b1;
                    wr_hwpb       <= 4'd0;   // consumed; don't re-flush
                end
                // everything drained and no beat pending -> wait for completion
                if ((wr_hw_left == 16'd0) && w_rd_empty && !wr_beat_valid &&
                    wr_hwpb == 4'd0)
                    wr_state <= WR_WAIT;
            end

            WR_WAIT: begin
                if ((wr_target_reg && reg_wr_done) || (!wr_target_reg && ddr_wr_done))
                    wr_state <= WR_IDLE;
            end

            default: wr_state <= WR_IDLE;
            endcase
        end
    end

    // Drive the master write-data inputs from the assembled beat.
    assign reg_wr_dv   = (wr_state == WR_DRAIN) && wr_target_reg  && wr_beat_valid;
    assign ddr_wr_dv   = (wr_state == WR_DRAIN) && !wr_target_reg && wr_beat_valid;
    assign reg_wr_data = wr_beat;
    assign ddr_wr_data = wr_beat;

    // ================= read-FIFO read side (xspi_clk) =================
    // The front-end consumes one halfword per data cycle during a read.
    assign rd_rd_en = (phase == P_DATA) && is_read && !rd_rd_empty;


    assign xspi_busy      = (phase != P_IDLE);
    assign xspi_last_addr = addr_reg;
    assign xspi_last_cmd  = cmd_reg;

    // IO is driven only during a read data phase; otherwise high-Z.
    genvar gi;
    generate
        for (gi = 0; gi < 8; gi = gi + 1) begin : io_gen
            assign xspi_io[gi] = io_oe ? io_out[gi] : 1'bz;
        end
    endgenerate

endmodule

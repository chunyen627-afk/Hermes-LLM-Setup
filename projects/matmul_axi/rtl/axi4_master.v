// axi4_master : AXI4 master burst engine (INCR only).
//
// Presents a simple byte-count command API to the consumer:
//   - read  : rd_start + rd_addr + rd_len_bytes  -> beats stream out on rd_data_*
//   - write : wr_start + wr_addr + wr_len_bytes  -> consumer feeds wr_data_in_*
// The master splits each transfer into legal AXI4 INCR bursts that never cross a
// 4 KB boundary, so the consumer never has to reason about boundaries itself.
// Each burst's length is min(page-room, remaining-beats, 256).
//
// Outstanding reads: all read bursts use ONE id (0). AXI4 guarantees in-order R
// data return for a single id, so several ARs may be in flight at once and the R
// beats still arrive in request order -- no reorder buffer is needed. The read
// burst queue depth (MAX_RD_BURSTS) bounds how many bursts can be outstanding.
//
// Consumer contract:
//   - rd_len_bytes / wr_len_bytes must be a multiple of the beat size (>= 1 beat).
//   - For a write, present exactly (wr_len_bytes/beat) beats on wr_data_in_* while
//     wr_data_in_ready is high.
//
// Protocol assertions (`ifdef AXI_MASTER_ASSERT`): every INCR burst stays within
// one 4 KB page and ARLEN/AWLEN are legal (beats-1, <= 255).

module axi4_master #(
    parameter ADDR_WIDTH    = 32,
    parameter DATA_WIDTH    = 256,
    parameter ID_WIDTH      = 4,
    parameter MAX_RD_BURSTS = 16,          // read-burst queue depth (max outstanding)
    parameter RD_LEN_W      = 20           // max transfer = 2^RD_LEN_W bytes
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    // ================= read command (from consumer) =================
    input  wire                    rd_start,        // pulse: begin a read
    input  wire [ADDR_WIDTH-1:0]   rd_addr,         // first byte address
    input  wire [RD_LEN_W-1:0]     rd_len_bytes,    // total bytes to read (>= beat)
    output wire                    rd_busy,         // high while a read is in flight

    // ================= read data out (to consumer) =================
    output reg                     rd_data_valid,
    output reg  [DATA_WIDTH-1:0]   rd_data,
    output reg                     rd_last,         // last beat of the whole transfer
    input  wire                    rd_data_ready,   // consumer accepts the current beat

    // ================= write command (from consumer) =================
    input  wire                    wr_start,        // pulse: begin a write
    input  wire [ADDR_WIDTH-1:0]   wr_addr,         // first byte address
    input  wire [RD_LEN_W-1:0]     wr_len_bytes,    // total bytes to write (>= beat)
    output wire                    wr_busy,         // high while a write is in flight

    // ================= write data in (from consumer) =================
    input  wire                    wr_data_in_valid,
    input  wire [DATA_WIDTH-1:0]   wr_data_in,
    output wire                    wr_data_in_ready,
    output wire                    wr_done,         // pulse: write fully accepted

    // ================= AXI4 write-address channel =================
    output wire                    m_axi_awvalid,
    input  wire                    m_axi_awready,
    output wire [ADDR_WIDTH-1:0]   m_axi_awaddr,
    output wire [7:0]              m_axi_awlen,
    output wire [2:0]              m_axi_awsize,
    output wire [1:0]              m_axi_awburst,
    output wire [ID_WIDTH-1:0]     m_axi_awid,

    // ================= AXI4 write-data channel =================
    output wire                    m_axi_wvalid,
    input  wire                    m_axi_wready,
    output wire [DATA_WIDTH-1:0]   m_axi_wdata,
    output wire [DATA_WIDTH/8-1:0] m_axi_wstrb,
    output wire                    m_axi_wlast,

    // ================= AXI4 write-response channel =================
    input  wire                    m_axi_bvalid,
    output wire                    m_axi_bready,
    input  wire [1:0]              m_axi_bresp,
    input  wire [ID_WIDTH-1:0]     m_axi_bid,

    // ================= AXI4 read-address channel =================
    output wire                    m_axi_arvalid,
    input  wire                    m_axi_arready,
    output wire [ADDR_WIDTH-1:0]   m_axi_araddr,
    output wire [7:0]              m_axi_arlen,
    output wire [2:0]              m_axi_arsize,
    output wire [1:0]              m_axi_arburst,
    output wire [ID_WIDTH-1:0]     m_axi_arid,

    // ================= AXI4 read-data channel =================
    input  wire                    m_axi_rvalid,
    output wire                    m_axi_rready,
    input  wire [DATA_WIDTH-1:0]   m_axi_rdata,
    input  wire [1:0]              m_axi_rresp,
    input  wire                    m_axi_rlast,
    input  wire [ID_WIDTH-1:0]     m_axi_rid
);

    localparam BEAT_BYTES = DATA_WIDTH/8;
    localparam BB_SHIFT   = $clog2(BEAT_BYTES);          // log2(bytes per beat)
    localparam RD_FIFO_AW = (MAX_RD_BURSTS > 1) ? $clog2(MAX_RD_BURSTS) : 1;

    // ------------------------------------------------------------------
    // helpers
    // ------------------------------------------------------------------
    function [RD_LEN_W-1:0] bytes_to_beats;
        input [RD_LEN_W-1:0] bytes;
        begin
            bytes_to_beats = bytes >> BB_SHIFT;
        end
    endfunction

    // max beats in one INCR burst starting at `addr` without crossing 4 KB,
    // capped at MAX_BURST_BEATS (128 = one 4 KB page at 32 B/beat). Returns a
    // 9-bit count so it can represent up to 256 if the cap is raised.
    localparam MAX_BURST_BEATS = 128;
    function [8:0] page_room_beats;
        input [ADDR_WIDTH-1:0] addr;
        begin
            begin : bb
                integer room;   // bytes until the next 4 KB boundary (1..4096)
                integer cap;
                room = 4096 - (addr[11:0]);
                cap  = room / BEAT_BYTES;
                if (cap > MAX_BURST_BEATS) cap = MAX_BURST_BEATS;
                page_room_beats = cap[8:0];
            end
        end
    endfunction

    // burst length = min(page room, remaining beats in the transfer), as 9 bits.
    // The comparison must be done in FULL width (RD_LEN_W), not on a 9-bit slice:
    // a large transfer (e.g. 2560 beats) has remaining[8:0] wrap to 0, which would
    // make burst_len return 0 -> arlen = -1 = 255 (a bogus 256-beat burst). Since
    // page_room is always <= MAX_BURST_BEATS (<= 128 < 512), the result fits in 9
    // bits; we only need the full-width value to pick the correct minimum.
    function [8:0] burst_len;
        input [ADDR_WIDTH-1:0] addr;
        input [RD_LEN_W-1:0]   remaining;
        begin
            if (remaining == {RD_LEN_W{1'b0}})
                burst_len = 9'd0;
            else if ((remaining[RD_LEN_W-1:9] != 0) ||
                     (page_room_beats(addr) <= remaining[8:0]))
                burst_len = page_room_beats(addr);
            else
                burst_len = remaining[8:0];
        end
    endfunction

    // ==================================================================
    // READ engine
    // ==================================================================
    // Two counters:
    //   rd_issue_left : beats not yet issued as ARs (drives the burst queue).
    //   rd_total_beats: beats left in the transfer (drives rd_last on R consume).
    // Bursts are pushed into a FIFO as fast as it allows (one/cycle), so up to
    // MAX_RD_BURSTS ARs can be outstanding. The head is issued as an AR each cycle.

    reg [RD_FIFO_AW-1:0] rd_wptr, rd_rptr;
    reg [ADDR_WIDTH-1:0] rd_addr_q  [0:MAX_RD_BURSTS-1];
    reg [7:0]            rd_beats_q [0:MAX_RD_BURSTS-1];

    wire rd_fifo_empty = (rd_wptr == rd_rptr);
    wire rd_fifo_full  = (rd_wptr[RD_FIFO_AW-1] != rd_rptr[RD_FIFO_AW-1]) &&
                         (rd_wptr[RD_FIFO_AW-2:0] == rd_rptr[RD_FIFO_AW-2:0]);

    reg        rd_active;
    reg [ADDR_WIDTH-1:0] rd_next_addr;     // address of the next burst to push
    reg [RD_LEN_W-1:0]   rd_issued;        // beats already pushed into the FIFO
    reg [RD_LEN_W-1:0]   rd_total_beats;   // beats left in the transfer (for rd_last)

    wire rd_can_start = rd_start && !rd_active;

    // remaining beats not yet issued, derived from a single counter so it always
    // matches rd_next_addr (both advance by the same amount on each push). Using
    // one source of truth avoids the off-by-one that arises when two registers are
    // updated from a value computed in the same cycle.
    wire [RD_LEN_W-1:0] rd_remaining = bytes_to_beats(rd_len_bytes) - rd_issued;

    // push one burst per cycle while there is room and beats remain to issue
    wire rd_push = rd_active && (rd_remaining > 0) && !rd_fifo_full;
    wire [7:0] rd_push_beats = burst_len(rd_next_addr, rd_remaining);

    // R-channel beat consumed this cycle (used for rd_last and to clear rd_active).
    wire r_beat_consumed = m_axi_rvalid && m_axi_rready;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            rd_wptr       <= {RD_FIFO_AW{1'b0}};
            rd_rptr       <= {RD_FIFO_AW{1'b0}};
            rd_active     <= 1'b0;
            rd_next_addr  <= {ADDR_WIDTH{1'b0}};
            rd_issued     <= {RD_LEN_W{1'b0}};
            rd_total_beats<= {RD_LEN_W{1'b0}};
        end else begin
            if (rd_can_start) begin
                rd_active     <= 1'b1;
                rd_next_addr  <= rd_addr;
                rd_issued     <= {RD_LEN_W{1'b0}};
                rd_total_beats<= bytes_to_beats(rd_len_bytes);
            end
            if (rd_push) begin
                rd_addr_q[rd_wptr[RD_FIFO_AW-2:0]]  <= rd_next_addr;
                rd_beats_q[rd_wptr[RD_FIFO_AW-2:0]] <= rd_push_beats;
                rd_wptr       <= rd_wptr + 1'b1;
                // advance the running address and issued count together so they
                // stay in lockstep (rd_remaining is recomputed from rd_issued).
                rd_next_addr  <= rd_next_addr + (rd_push_beats << BB_SHIFT);
                rd_issued     <= rd_issued + rd_push_beats;
            end
            if (m_axi_arvalid && m_axi_arready)
                rd_rptr <= rd_rptr + 1'b1;
            // decrement the transfer beat count as R beats are consumed, and clear
            // rd_active when the final beat of the transfer is consumed so the next
            // read can start. (rd_total_beats is owned ONLY by this block.)
            if (r_beat_consumed) begin
                rd_total_beats <= rd_total_beats - 1'b1;
                if (rd_active && (rd_total_beats == 20'd1))
                    rd_active <= 1'b0;
            end
        end
    end

    // ---- issue ARs from the fifo head (combinational) ----
    // arvalid is high exactly while the burst FIFO has an entry. The head's
    // addr/len are stable until the AR is accepted (rd_rptr advances on the
    // handshake), so driving them combinationally keeps a 1:1 mapping between
    // FIFO entries and issued ARs -- no re-issue, no x-addresses.
    wire [ADDR_WIDTH-1:0] rd_head_addr  = rd_addr_q[rd_rptr[RD_FIFO_AW-2:0]];
    wire [7:0]            rd_head_beats = rd_beats_q[rd_rptr[RD_FIFO_AW-2:0]];
    assign m_axi_arvalid = !rd_fifo_empty;
    assign m_axi_araddr  = rd_head_addr;
    assign m_axi_arlen   = rd_head_beats - 8'd1;   // beats-1
    assign m_axi_arsize  = BB_SHIFT[2:0];
    assign m_axi_arburst = 2'b01;       // INCR
    assign m_axi_arid    = {ID_WIDTH{1'b0}};   // single read id -> in-order R

    // ---- R data forwarding + rd_last (with consumer backpressure) ----
    // A beat is latched from the AXI R channel into rd_data/rd_data_valid and
    // held until the consumer accepts it (rd_data_ready). m_axi_rready is
    // deasserted while a beat is pending, so the master never overwrites an
    // unaccepted beat -- this lets the consumer unpack one beat (16 elements)
    // over several cycles before releasing it.
    assign m_axi_rready = !rd_data_valid;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            rd_data_valid <= 1'b0;
            rd_data       <= {DATA_WIDTH{1'b0}};
            rd_last       <= 1'b0;
        end else begin
            if (m_axi_rvalid && m_axi_rready) begin
                // accept a new beat from AXI into the output buffer.
                // rd_total_beats is decremented in the read-engine block (single
                // owner); here we only capture the last-beat flag for this beat.
                rd_data        <= m_axi_rdata;
                rd_last        <= (rd_total_beats == 20'd1);
                rd_data_valid  <= 1'b1;
            end else if (rd_data_valid && rd_data_ready) begin
                // consumer accepted the buffered beat
                rd_data_valid <= 1'b0;
            end
        end
    end
    assign rd_busy = rd_active;

    // ==================================================================
    // WRITE engine (explicit state machine)
    // ==================================================================
    // IDLE -> AW -> W -> B -> (next burst | IDLE). The master waits for the B
    // response before issuing the next burst's AW, so it never pipelines two
    // write bursts. This is a deliberate design choice: a matmul accelerator
    // streams one buffer at a time and gains nothing from cross-burst write
    // pipelining, while it keeps the slave side simple (one outstanding write).
    localparam WR_IDLE = 2'd0, WR_AW = 2'd1, WR_W = 2'd2, WR_B = 2'd3;
    reg [1:0] wr_state;
    reg       wr_active;
    reg [ADDR_WIDTH-1:0] wr_cur_addr;     // start address of the current burst
    reg [RD_LEN_W-1:0]   wr_total_beats;  // beats left in the transfer (incl. cur burst)
    reg [8:0]            wr_burst_len;    // total beats in the current burst
    reg [8:0]            wr_w_left;       // beats left to send in the current burst

    wire wr_can_start = wr_start && !wr_active;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            wr_state       <= WR_IDLE;
            wr_active      <= 1'b0;
            wr_cur_addr    <= {ADDR_WIDTH{1'b0}};
            wr_total_beats <= {RD_LEN_W{1'b0}};
            wr_burst_len   <= 9'd0;
            wr_w_left      <= 9'd0;
        end else begin
            case (wr_state)
            WR_IDLE: begin
                if (wr_can_start) begin
                    wr_active      <= 1'b1;
                    wr_cur_addr    <= wr_addr;
                    wr_total_beats <= bytes_to_beats(wr_len_bytes);
                    wr_burst_len   <= burst_len(wr_addr, bytes_to_beats(wr_len_bytes));
                    wr_w_left      <= burst_len(wr_addr, bytes_to_beats(wr_len_bytes));
                    wr_state       <= WR_AW;
                end
            end
            WR_AW: begin
                // AW handshake: awvalid is combinational (== wr_state==WR_AW), so
                // when the slave accepts it we move to W. Only this FSM writes
                // wr_state -- the AXI signals are driven combinationally below.
                if (m_axi_awvalid && m_axi_awready)
                    wr_state <= WR_W;
            end
            WR_W: begin
                // W beats streamed by the W channel. When the last beat of this
                // burst is accepted, move to B.
                //
                // The wr_w_left decrement lives here, not in its own always
                // block: a second block assigning the same reg elaborates and
                // simulates fine but synthesises to two physical registers
                // driving one net -- implement's DRC MDRV-1 rejects it
                // (37 errors, one per bit). 2026-09-03.
                if (m_axi_wvalid && m_axi_wready) begin
                    wr_w_left <= wr_w_left - 9'd1;
                    if (wr_w_left == 9'd1)
                        wr_state <= WR_B;
                end
            end
            WR_B: begin
                if (m_axi_bvalid && m_axi_bready) begin
                    if (wr_total_beats <= wr_burst_len) begin
                        // final burst done
                        wr_active <= 1'b0;
                        wr_state  <= WR_IDLE;
                    end else begin
                        wr_cur_addr    <= wr_cur_addr + (wr_burst_len << BB_SHIFT);
                        wr_total_beats <= wr_total_beats - wr_burst_len;
                        wr_burst_len   <= burst_len(wr_cur_addr + (wr_burst_len << BB_SHIFT),
                                                   wr_total_beats - wr_burst_len);
                        wr_w_left      <= burst_len(wr_cur_addr + (wr_burst_len << BB_SHIFT),
                                                   wr_total_beats - wr_burst_len);
                        wr_state       <= WR_AW;
                    end
                end
            end
            default: wr_state <= WR_IDLE;
            endcase
        end
    end
    assign wr_busy = wr_active;

    // ---- issue AW for the current burst (combinational, in WR_AW state) ----
    // awvalid is high exactly while wr_state==WR_AW; the FSM advances to WR_W on
    // the handshake. addr/len are stable during WR_AW because wr_cur_addr and
    // wr_burst_len only change on burst boundaries (in WR_B).
    assign m_axi_awvalid = (wr_state == WR_AW);
    assign m_axi_awaddr  = wr_cur_addr;
    assign m_axi_awlen   = wr_burst_len - 9'd1;
    assign m_axi_awsize  = BB_SHIFT[2:0];
    assign m_axi_awburst = 2'b01;       // INCR
    assign m_axi_awid    = {{(ID_WIDTH-1){1'b0}}, 1'b1}; // write id = 1

    // ---- write-data staging fifo (consumer runs slightly ahead of AXI) ----
    localparam WR_FIFO_AW = 3;             // 8-deep
    reg [WR_FIFO_AW-1:0] wdata_wptr, wdata_rptr;
    reg [DATA_WIDTH-1:0] wdata_fifo [0:7];
    wire wdata_full  = (wdata_wptr[WR_FIFO_AW-1] != wdata_rptr[WR_FIFO_AW-1]) &&
                       (wdata_wptr[WR_FIFO_AW-2:0] == wdata_rptr[WR_FIFO_AW-2:0]);
    wire wdata_empty = (wdata_wptr == wdata_rptr);

    assign wr_data_in_ready = wr_active && !wdata_full;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            wdata_wptr <= {WR_FIFO_AW{1'b0}};
            wdata_rptr <= {WR_FIFO_AW{1'b0}};
        end else begin
            if (wr_data_in_valid && wr_data_in_ready) begin
                wdata_fifo[wdata_wptr[WR_FIFO_AW-2:0]] <= wr_data_in;
                wdata_wptr <= wdata_wptr + 1'b1;
            end
            if (m_axi_wvalid && m_axi_wready)
                wdata_rptr <= wdata_rptr + 1'b1;
        end
    end

    // ---- W channel (combinational from the staging-FIFO head) ----
    // wvalid/wdata/wlast are driven combinationally from the FIFO so that the data
    // on the bus always matches the current wdata_rptr. If they were registered,
    // the data would lag rptr by a cycle (rptr advances on accept), and every beat
    // would store the previous beat's data. wlast is derived from wr_w_left (the
    // write-engine beat count) so it marks the true final beat of the burst.
    assign m_axi_wvalid = (wr_state == WR_W) && !wdata_empty;
    assign m_axi_wdata  = wdata_fifo[wdata_rptr[WR_FIFO_AW-2:0]];
    assign m_axi_wstrb  = {DATA_WIDTH/8{1'b1}};
    assign m_axi_wlast  = (wr_state == WR_W) && !wdata_empty && (wr_w_left == 9'd1);

    // ---- B response / write done ----
    assign m_axi_bready = 1'b1;
    // wr_done pulses on the cycle the final burst's B response is accepted.
    assign wr_done = (wr_state == WR_B) && m_axi_bvalid && m_axi_bready &&
                     (wr_total_beats <= wr_burst_len);

    // ==================================================================
    // protocol assertions
    // ==================================================================
`ifdef AXI_MASTER_ASSERT
    always @(posedge aclk) begin
        if (m_axi_arvalid && m_axi_arready) begin
            if (m_axi_arlen > 8'd255)
                $display("AXI_MASTER_ASSERT: ARLEN too large (%0d)", m_axi_arlen);
            if (m_axi_araddr[11:0] + (m_axi_arlen * BB_SHIFT) >= 16'd4096)
                $display("AXI_MASTER_ASSERT: read burst crosses 4KB (addr=%h len=%0d)",
                         m_axi_araddr, m_axi_arlen);
        end
        if (m_axi_awvalid && m_axi_awready) begin
            if (m_axi_awlen > 8'd255)
                $display("AXI_MASTER_ASSERT: AWLEN too large (%0d)", m_axi_awlen);
            if (m_axi_awaddr[11:0] + (m_axi_awlen * BB_SHIFT) >= 16'd4096)
                $display("AXI_MASTER_ASSERT: write burst crosses 4KB (addr=%h len=%0d)",
                         m_axi_awaddr, m_axi_awlen);
        end
    end
`endif

endmodule

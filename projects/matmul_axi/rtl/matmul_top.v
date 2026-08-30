// matmul_top : AXI4 slave wrapper around matmul_core.
//
// Exposes the control/status register map from ARCHITECTURE.md §3.5 on an AXI4
// slave port, and instantiates the verified matmul_core datapath.
//
// Register map (word index = addr[5:2]):
//   idx 0  0x00  CTRL     RW  bit0=start, bit1=reset, bit2=bf16_in
//   idx 1  0x04  STATUS   RO  bit0=busy, bit1=done, bit2=error
//   idx 2  0x08  M_DIM    RO  rows of W (d)
//   idx 3  0x0C  N_DIM    RO  reduction length (n)
//   idx 4  0x10  W_BASE   RW  DDR4 base addr of weight matrix
//   idx 5  0x14  X_BASE   RW  DDR4 base addr of activation vector
//   idx 6  0x18  OUT_BASE RW  DDR4 base addr of output vector
//   idx 7  0x1C  COUNT    RO  number of outputs produced
//
// Data movement (reading W/X from DDR4, writing OUT) is intentionally NOT done
// here yet — per ARCHITECTURE.md §6 open item 4 the IP is a slave for
// control+activation with weights pre-staged; the matmul_core still loads its
// operands via $readmemh for verification. The base-address registers are stored
// and exposed so a later AXI-master data path can use them.

module matmul_top #(
    parameter D          = 288,
    parameter N          = 768,
    parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32,
    parameter ID_WIDTH   = 4
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    // ---- AXI4 slave: write-address ----
    input  wire                    s_axi_awvalid,
    output wire                    s_axi_awready,
    input  wire [ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  wire [7:0]              s_axi_awlen,
    input  wire [2:0]              s_axi_awsize,
    input  wire [1:0]              s_axi_awburst,
    input  wire [ID_WIDTH-1:0]     s_axi_awid,

    // ---- AXI4 slave: write-data ----
    input  wire                    s_axi_wvalid,
    output wire                    s_axi_wready,
    input  wire [DATA_WIDTH-1:0]   s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0] s_axi_wstrb,

    // ---- AXI4 slave: write-response ----
    output wire                    s_axi_bvalid,
    input  wire                    s_axi_bready,
    output wire [1:0]              s_axi_bresp,
    output wire [ID_WIDTH-1:0]     s_axi_bid,

    // ---- AXI4 slave: read-address ----
    input  wire                    s_axi_arvalid,
    output wire                    s_axi_arready,
    input  wire [ADDR_WIDTH-1:0]   s_axi_araddr,
    input  wire [7:0]              s_axi_arlen,
    input  wire [2:0]              s_axi_arsize,
    input  wire [1:0]              s_axi_arburst,
    input  wire [ID_WIDTH-1:0]     s_axi_arid,

    // ---- AXI4 slave: read-data ----
    output wire                    s_axi_rvalid,
    input  wire                    s_axi_rready,
    output wire [DATA_WIDTH-1:0]   s_axi_rdata,
    output wire [1:0]              s_axi_rresp,
    output wire                    s_axi_rlast,
    output wire [ID_WIDTH-1:0]     s_axi_rid,

    // ---- debug/observation of the datapath result (verification) ----
    output wire [D*32-1:0]         xout_vec
);

    localparam NUM_REGS = 8;
    localparam AW = $clog2(NUM_REGS);

    // ---- register file (RW registers) ----
    reg [31:0] ctrl_reg;     // idx 0
    reg [31:0] w_base_reg;   // idx 4
    reg [31:0] x_base_reg;   // idx 5
    reg [31:0] out_base_reg; // idx 6

    // ---- register-file interface to axi4s_reg ----
    wire        reg_we;
    wire [AW-1:0] reg_waddr;
    wire [31:0]   reg_wdata;
    wire [AW-1:0] reg_raddr;
    reg  [31:0]   reg_rdata_mux;

    // ---- datapath control signals ----
    wire core_start;
    wire core_reset;
    wire core_done_pulse;   // 1-cycle pulse from the core
    reg  core_done;         // sticky: set on completion, cleared by reset/start
    wire core_busy;
    wire core_error;

    // start pulse: write-1 to CTRL[0]
    assign core_start = reg_we && (reg_waddr == 3'd0) && reg_wdata[0];
    // reset pulse: write-1 to CTRL[1]
    assign core_reset = reg_we && (reg_waddr == 3'd0) && reg_wdata[1];

    // ---- sticky done latch (software polls STATUS; a 1-cycle pulse would be missed) ----
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)      core_done <= 1'b0;
        else if (core_reset) core_done <= 1'b0;
        else if (core_start) core_done <= 1'b0;   // clear when a new run begins
        else if (core_done_pulse) core_done <= 1'b1;
    end

    // ---- register writes ----
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            ctrl_reg     <= 32'd0;
            w_base_reg   <= 32'd0;
            x_base_reg   <= 32'd0;
            out_base_reg <= 32'd0;
        end else if (reg_we) begin
            case (reg_waddr)
                3'd0: ctrl_reg     <= reg_wdata;
                3'd4: w_base_reg   <= reg_wdata;
                3'd5: x_base_reg   <= reg_wdata;
                3'd6: out_base_reg <= reg_wdata;
                default: ;
            endcase
        end
    end

    // ---- register reads (RO values computed, RW values from the file) ----
    wire [31:0] status_val = {29'd0, core_error, core_done, core_busy};
    wire [31:0] m_dim_val  = D;
    wire [31:0] n_dim_val  = N;
    wire [31:0] count_val  = core_done ? D : 32'd0;

    always @(*) begin
        case (reg_raddr)
            3'd0: reg_rdata_mux = ctrl_reg;
            3'd1: reg_rdata_mux = status_val;
            3'd2: reg_rdata_mux = m_dim_val;
            3'd3: reg_rdata_mux = n_dim_val;
            3'd4: reg_rdata_mux = w_base_reg;
            3'd5: reg_rdata_mux = x_base_reg;
            3'd6: reg_rdata_mux = out_base_reg;
            3'd7: reg_rdata_mux = count_val;
            default: reg_rdata_mux = 32'd0;
        endcase
    end

    // ---- AXI4 slave register block ----
    axi4s_reg #(
        .ADDR_WIDTH (ADDR_WIDTH),
        .DATA_WIDTH (DATA_WIDTH),
        .ID_WIDTH   (ID_WIDTH),
        .NUM_REGS   (NUM_REGS)
    ) u_axi (
        .aclk      (aclk),
        .aresetn   (aresetn),

        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awlen   (s_axi_awlen),
        .s_axi_awsize  (s_axi_awsize),
        .s_axi_awburst (s_axi_awburst),
        .s_axi_awid    (s_axi_awid),

        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),

        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bid     (s_axi_bid),

        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arlen   (s_axi_arlen),
        .s_axi_arsize  (s_axi_arsize),
        .s_axi_arburst (s_axi_arburst),
        .s_axi_arid    (s_axi_arid),

        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rlast   (s_axi_rlast),
        .s_axi_rid     (s_axi_rid),

        .reg_we      (reg_we),
        .reg_waddr   (reg_waddr),
        .reg_wdata   (reg_wdata),
        .reg_raddr   (reg_raddr),
        .reg_rdata_mux (reg_rdata_mux)
    );

    // ---- matmul core ----
    // The core's `start` is a level in the current design; we pulse it. The
    // core's `rst_n` is active-low; we map core_reset to a deassert of rst_n.
    reg core_rst_n;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)      core_rst_n <= 1'b0;
        else if (core_reset) core_rst_n <= 1'b0;
        else                core_rst_n <= 1'b1;
    end

    matmul_core #(.D(D), .N(N)) u_core (
        .clk      (aclk),
        .rst_n    (core_rst_n),
        .start    (core_start),
        .done     (core_done_pulse),
        .xout_vec (xout_vec)
    );

    // busy = core is running (not done and not idle). The core has no explicit
    // busy output, so derive it: busy while a run is in progress. We track a
    // simple "running" flag set by start, cleared by the done pulse.
    reg running;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn)      running <= 1'b0;
        else if (core_start) running <= 1'b1;
        else if (core_done_pulse)  running <= 1'b0;
    end
    assign core_busy  = running;
    assign core_error = 1'b0;   // no error sources yet

endmodule

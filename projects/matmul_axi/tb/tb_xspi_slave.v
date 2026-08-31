// tb_xspi_slave : Testbench for the xSPI -> AXI bridge (xspi_slave).
//
// Drives a realistic OCTOSPI master model that generates the exact wire
// framing from the APS256XX board BSP:
//   [instruction 8b SDR][address 32b DDR=2cyc][dummy N cyc][data DDR 16b/cyc]
//
// SPI MODE 0 (CPOL0/CPHA0): slave samples on rising edge, master shifts out on
// falling edge. For DDR phases the slave also samples the falling edge and the
// master shifts a second byte there. The TB drives IO at falling edges (after
// the slave has sampled) and reads IO at rising/falling edges.
//
// Two AXI4 slave models (reg region + DDR4 region) back the two axi4_master
// instances so data integrity can be checked end to end. Fires all 12
// require_cover scenarios and checks data integrity.

`timescale 1ns/1ps

module tb_xspi_slave;

    // ================= parameters =================
    localparam AXI_ADDR_WIDTH = 32;
    localparam AXI_DATA_WIDTH = 32;
    localparam AXI_ID_WIDTH   = 4;
    localparam MEM_BEATS      = 65536;   // 256 KB per region (beat-indexed)

    // ================= xSPI signals =================
    reg         xspi_clk;
    reg         xspi_cs_n;
    wire [7:0]  xspi_io;        // multi-driver: DUT (read-data) + master model
    reg  [7:0]  xspi_io_master; // master-model driver value
    reg         xspi_dqs;
    // ================= aclk signals =================
    reg         aclk;
    reg         arst_n;

    // ================= AXI slave-model buses (reg region) =================
    wire        m_reg_awvalid;  reg  m_reg_awready;
    wire [31:0] m_reg_awaddr;   wire [7:0] m_reg_awlen;
    wire [2:0]  m_reg_awsize;   wire [1:0] m_reg_awburst;  wire [3:0] m_reg_awid;
    wire        m_reg_wvalid;   reg  m_reg_wready;
    wire [31:0] m_reg_wdata;    wire [3:0] m_reg_wstrb;    wire m_reg_wlast;
    reg         m_reg_bvalid;   wire m_reg_bready;
    reg  [1:0]  m_reg_bresp;    reg  [3:0] m_reg_bid;
    wire        m_reg_arvalid;  reg  m_reg_arready;
    wire [31:0] m_reg_araddr;   wire [7:0] m_reg_arlen;
    wire [2:0]  m_reg_arsize;   wire [1:0] m_reg_arburst;  wire [3:0] m_reg_arid;
    reg         m_reg_rvalid;   wire m_reg_rready;
    reg  [31:0] m_reg_rdata;    reg  [1:0] m_reg_rresp;    reg m_reg_rlast;
    reg  [3:0]  m_reg_rid;

    // ================= AXI slave-model buses (DDR4 region) =================
    wire        m_ddr_awvalid;  reg  m_ddr_awready;
    wire [31:0] m_ddr_awaddr;   wire [7:0] m_ddr_awlen;
    wire [2:0]  m_ddr_awsize;   wire [1:0] m_ddr_awburst;  wire [3:0] m_ddr_awid;
    wire        m_ddr_wvalid;   reg  m_ddr_wready;
    wire [31:0] m_ddr_wdata;    wire [3:0] m_ddr_wstrb;    wire m_ddr_wlast;
    reg         m_ddr_bvalid;   wire m_ddr_bready;
    reg  [1:0]  m_ddr_bresp;    reg  [3:0] m_ddr_bid;
    wire        m_ddr_arvalid;  reg  m_ddr_arready;
    wire [31:0] m_ddr_araddr;   wire [7:0] m_ddr_arlen;
    wire [2:0]  m_ddr_arsize;   wire [1:0] m_ddr_arburst;  wire [3:0] m_ddr_arid;
    reg         m_ddr_rvalid;   wire m_ddr_rready;
    reg  [31:0] m_ddr_rdata;    reg  [1:0] m_ddr_rresp;    reg m_ddr_rlast;
    reg  [3:0]  m_ddr_rid;

    // ================= DUT =================
    xspi_slave #(.AXI_ADDR_WIDTH(AXI_ADDR_WIDTH), .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
                 .AXI_ID_WIDTH(AXI_ID_WIDTH)) dut (
        .xspi_clk(xspi_clk), .xspi_cs_n(xspi_cs_n), .xspi_io(xspi_io), .xspi_dqs(xspi_dqs),
        .aclk(aclk), .arst_n(arst_n),
        .m_reg_awvalid(m_reg_awvalid), .m_reg_awready(m_reg_awready),
        .m_reg_awaddr(m_reg_awaddr), .m_reg_awlen(m_reg_awlen),
        .m_reg_awsize(m_reg_awsize), .m_reg_awburst(m_reg_awburst), .m_reg_awid(m_reg_awid),
        .m_reg_wvalid(m_reg_wvalid), .m_reg_wready(m_reg_wready),
        .m_reg_wdata(m_reg_wdata), .m_reg_wstrb(m_reg_wstrb), .m_reg_wlast(m_reg_wlast),
        .m_reg_bvalid(m_reg_bvalid), .m_reg_bready(m_reg_bready),
        .m_reg_bresp(m_reg_bresp), .m_reg_bid(m_reg_bid),
        .m_reg_arvalid(m_reg_arvalid), .m_reg_arready(m_reg_arready),
        .m_reg_araddr(m_reg_araddr), .m_reg_arlen(m_reg_arlen),
        .m_reg_arsize(m_reg_arsize), .m_reg_arburst(m_reg_arburst), .m_reg_arid(m_reg_arid),
        .m_reg_rvalid(m_reg_rvalid), .m_reg_rready(m_reg_rready),
        .m_reg_rdata(m_reg_rdata), .m_reg_rresp(m_reg_rresp),
        .m_reg_rlast(m_reg_rlast), .m_reg_rid(m_reg_rid),
        .m_ddr_awvalid(m_ddr_awvalid), .m_ddr_awready(m_ddr_awready),
        .m_ddr_awaddr(m_ddr_awaddr), .m_ddr_awlen(m_ddr_awlen),
        .m_ddr_awsize(m_ddr_awsize), .m_ddr_awburst(m_ddr_awburst), .m_ddr_awid(m_ddr_awid),
        .m_ddr_wvalid(m_ddr_wvalid), .m_ddr_wready(m_ddr_wready),
        .m_ddr_wdata(m_ddr_wdata), .m_ddr_wstrb(m_ddr_wstrb), .m_ddr_wlast(m_ddr_wlast),
        .m_ddr_bvalid(m_ddr_bvalid), .m_ddr_bready(m_ddr_bready),
        .m_ddr_bresp(m_ddr_bresp), .m_ddr_bid(m_ddr_bid),
        .m_ddr_arvalid(m_ddr_arvalid), .m_ddr_arready(m_ddr_arready),
        .m_ddr_araddr(m_ddr_araddr), .m_ddr_arlen(m_ddr_arlen),
        .m_ddr_arsize(m_ddr_arsize), .m_ddr_arburst(m_ddr_arburst), .m_ddr_arid(m_ddr_arid),
        .m_ddr_rvalid(m_ddr_rvalid), .m_ddr_rready(m_ddr_rready),
        .m_ddr_rdata(m_ddr_rdata), .m_ddr_rresp(m_ddr_rresp),
        .m_ddr_rlast(m_ddr_rlast), .m_ddr_rid(m_ddr_rid)
    );

    // ================= AXI slave models =================
    axi4_slave_model #(.ADDR_WIDTH(AXI_ADDR_WIDTH), .DATA_WIDTH(AXI_DATA_WIDTH),
                       .ID_WIDTH(AXI_ID_WIDTH), .MEM_BEATS(MEM_BEATS)) u_reg_slave (
        .aclk(aclk), .aresetn(arst_n),
        .awvalid(m_reg_awvalid), .awready(m_reg_awready), .awaddr(m_reg_awaddr), .awlen(m_reg_awlen),
        .wvalid(m_reg_wvalid), .wready(m_reg_wready), .wdata(m_reg_wdata), .wlast(m_reg_wlast),
        .bvalid(m_reg_bvalid), .bready(m_reg_bready), .bresp(m_reg_bresp), .bid(m_reg_bid),
        .arvalid(m_reg_arvalid), .arready(m_reg_arready), .araddr(m_reg_araddr), .arlen(m_reg_arlen),
        .rvalid(m_reg_rvalid), .rready(m_reg_rready), .rdata(m_reg_rdata),
        .rresp(m_reg_rresp), .rlast(m_reg_rlast), .rid(m_reg_rid)
    );
    axi4_slave_model #(.ADDR_WIDTH(AXI_ADDR_WIDTH), .DATA_WIDTH(AXI_DATA_WIDTH),
                       .ID_WIDTH(AXI_ID_WIDTH), .MEM_BEATS(MEM_BEATS)) u_ddr_slave (
        .aclk(aclk), .aresetn(arst_n),
        .awvalid(m_ddr_awvalid), .awready(m_ddr_awready), .awaddr(m_ddr_awaddr), .awlen(m_ddr_awlen),
        .wvalid(m_ddr_wvalid), .wready(m_ddr_wready), .wdata(m_ddr_wdata), .wlast(m_ddr_wlast),
        .bvalid(m_ddr_bvalid), .bready(m_ddr_bready), .bresp(m_ddr_bresp), .bid(m_ddr_bid),
        .arvalid(m_ddr_arvalid), .arready(m_ddr_arready), .araddr(m_ddr_araddr), .arlen(m_ddr_arlen),
        .rvalid(m_ddr_rvalid), .rready(m_ddr_rready), .rdata(m_ddr_rdata),
        .rresp(m_ddr_rresp), .rlast(m_ddr_rlast), .rid(m_ddr_rid)
    );

    // ================= clocks =================
    reg [31:0] xspi_half;  // half-period of xspi_clk in ns
    reg [31:0] aclk_half;  // half-period of aclk in ns
    initial begin
        xspi_half = 10;  // 20ns period (50 MHz)
        aclk_half = 2;   // 4ns period (250 MHz)
    end
    always begin #xspi_half xspi_clk = ~xspi_clk; end
    always begin #aclk_half  aclk  = ~aclk;  end

    // Multi-driver IO bus: the DUT drives during read-data (io_oe=1), the
    // master model drives otherwise. The two drivers are mutually exclusive so
    // the net never sees a conflict.
    genvar gi;
    generate
        for (gi = 0; gi < 8; gi = gi + 1) begin : io_master_gen
            assign xspi_io[gi] = dut.io_oe ? 1'bz : xspi_io_master[gi];
        end
    endgenerate

    // ================= cover / check counters =================
    integer cov_host_init, cov_mm_write, cov_mm_read, cov_burst_addr;
    integer cov_cs_mid, cov_dummy, cov_clk_ratio, cov_irregular;
    integer cov_access_reg, cov_access_ddr, cov_addr_decode, cov_interleaved;
    integer chk_checked, chk_bad;
    integer dbg_cnt;

    // Shared data buffer for drive_frame (128 halfwords).
    reg [15:0] data [0:127];

    // ================= master-model tasks =================
    // Drive a byte on the IO bus at the next falling edge (master shift-out).
    task drive_fall(input [7:0] val);
        begin @(negedge xspi_clk); #1; xspi_io_master = val; end
    endtask

    // Drive N dummy cycles (IO held at 0).
    task drive_dummy(input integer n);
        integer i;
        begin
            for (i = 0; i < n; i = i + 1) begin
                @(negedge xspi_clk); #1; xspi_io_master = 8'h00;
            end
        end
    endtask

    // Drive a complete frame: instruction + address + dummy + data.
    // Data is packed halfwords (16 bits each), one per DDR cycle, stored in the
    // shared buffer `data` starting at halfword index `base`. For writes the
    // master drives data[base+i]; for reads the slave->master bytes are captured
    // into data[base+i].
    task drive_frame(
        input  [7:0]  instr,
        input  [31:0] addr,
        input  integer n_dummy,
        input  integer base,     // halfword index in `data` where the frame starts
        input  integer n_hw,     // number of halfwords in the data phase
        input         is_read
    );
        integer i;
        reg [15:0] hw;
        begin
            xspi_cs_n = 1'b1;
            @(negedge xspi_clk); #1; xspi_cs_n = 1'b0;

            // Instruction (SDR, 1 cycle).
            @(negedge xspi_clk); #1; xspi_io_master = instr;

            // Address (DDR, 2 cycles, 32 bits MSB first).
            @(negedge xspi_clk); #1; xspi_io_master = addr[31:24];   // byte3 (rising)
            @(negedge xspi_clk); #1; xspi_io_master = addr[23:16];   // byte2 (falling)
            @(negedge xspi_clk); #1; xspi_io_master = addr[15:8];    // byte1 (rising)
            @(negedge xspi_clk); #1; xspi_io_master = addr[7:0];     // byte0 (falling)

            drive_dummy(n_dummy);

            // Data phase (DDR: one halfword per SCK cycle, 16 bits/cycle).
            // The DUT samples the upper byte on the rising edge and the lower
            // byte on the falling edge of the SAME SCK cycle. So per halfword:
            //   negedge: drive upper (stable through the next rising edge)
            //   posedge: drive lower  (DUT just sampled upper; lower now stable
            //             through the next falling edge, where it is sampled)
            for (i = 0; i < n_hw; i = i + 1) begin
                if (is_read) begin
                    @(posedge xspi_clk); #1; hw[15:8] = xspi_io;   // upper (rising)
                    @(negedge xspi_clk); #1; hw[7:0]  = xspi_io;   // lower (falling)
                    data[base+i] = hw;
                end else begin
                    hw = data[base+i];
                    @(negedge xspi_clk); #1; xspi_io_master = hw[15:8];   // upper
                    @(posedge xspi_clk); #1; xspi_io_master = hw[7:0];    // lower
                end
            end

            @(negedge xspi_clk); #1; xspi_cs_n = 1'b1;   // deassert CS
        end
    endtask

    // Wait for both AXI masters to be idle (no outstanding read/write).
    task wait_axi_idle;
        integer t;
        begin
            t = 0;
            while ((dut.u_reg_master.rd_active || dut.u_reg_master.wr_active ||
                    dut.u_ddr_master.rd_active || dut.u_ddr_master.wr_active) && t < 20000) begin
                @(posedge aclk); t = t + 1;
            end
            if (t >= 20000) $display("WARN wait_axi_idle timeout");
        end
    endtask

    // ================= test scenarios =================
    // ⚠ 使用者的提示（22:10）：「你要不要跑波形檢查，你有眼睛」
    //
    // 你現在追的是 timing-alignment 問題（addr_b1 在 rising edge 取樣時
    // 匯流排還是 X）—— 那正是波形最擅長的：一眼看出訊號什麼時候變、
    // 時脈邊緣落在哪裡，$display 抓不到邊緣前後的差異。
    //
    // 但這個 tb 目前沒有 $dumpfile/$dumpvars，所以產不出 VCD，
    // 你就算想看也沒東西可看。先加上去：
    //     initial begin
    //         $dumpfile("xspi_tb.vcd");
    //         $dumpvars(0, tb_xspi_slave);
    //     end
    // 然後照 skill `embedded/rtl-sim-verification` 的
    // references/vcd-parsing.md 把 VCD 畫成波形圖，
    // 用 vision_analyze 問**具體**問題（例如「addr_b1 在第幾個 SCK
    // 上升緣被取樣？那個時間點 xspi_io 是什麼值？」），
    // 不要問「這張圖有什麼問題」。
    //
    // 你有視覺能力（mmproj 已掛），這是它該派上用場的地方。
    // 加完波形、問題解決之後，刪掉這段註解。
    initial begin
        xspi_clk = 1'b0; aclk = 1'b0; xspi_cs_n = 1'b1; xspi_dqs = 1'b0; arst_n = 1'b0;
        cov_host_init=0; cov_mm_write=0; cov_mm_read=0; cov_burst_addr=0;
        cov_cs_mid=0; cov_dummy=0; cov_clk_ratio=0; cov_irregular=0;
        cov_access_reg=0; cov_access_ddr=0; cov_addr_decode=0; cov_interleaved=0;
        chk_checked=0; chk_bad=0;

        #100; arst_n = 1'b1; #100;

        // TEMP DEBUG: per-xspi-edge trace of the front-end FSM (first ~400 edges).
        fork
            begin : xtrace
                integer xn;
                reg [31:0] win_lo, win_hi;
                win_lo = 32'd2_200_000; win_hi = 32'd2_400_000;   // mm_write frame window
                xn = 0;
                while (xn < 200000) begin
                    @(posedge xspi_clk); #1;
                    if ($time >= win_lo && $time <= win_hi)
                        $display("XTR t=%0t POS phase=%d w_hi=%h w_lo=%h pipe=%h push_en=%b commit=%b cs_rise=%b io=%h",
                            $time, dut.phase, dut.w_hi, dut.w_lo, {dut.hw_pipe_hi,dut.hw_pipe_lo}, dut.hw_push_en, dut.w_commit, dut.cs_rise, xspi_io);
                    @(negedge xspi_clk); #1;
                    if ($time >= win_lo && $time <= win_hi)
                        $display("XTR t=%0t NEG phase=%d w_hi=%h w_lo=%h pipe=%h push_en=%b commit=%b cs_fall=%b io=%h",
                            $time, dut.phase, dut.w_hi, dut.w_lo, {dut.hw_pipe_hi,dut.hw_pipe_lo}, dut.hw_push_en, dut.w_commit, dut.cs_fall, xspi_io);
                    xn = xn + 1;
                end
            end
        join_none

        // TEMP DEBUG: monitor key DUT internals.
        dbg_cnt = 0;
        fork
            begin : dbg_loop
                while (dbg_cnt < 4000) begin
                    @(posedge aclk);
                    if (dut.f_valid || dut.ctl_rd_en || dut.wr_state != 2'd0 ||
                        dut.rd_state != 1'd0 ||
                        dut.u_ddr_master.m_axi_awvalid || dut.u_ddr_master.m_axi_wvalid ||
                        dut.u_ddr_master.m_axi_bvalid ||
                        dut.u_reg_master.m_axi_arvalid || dut.u_reg_master.m_axi_rvalid) begin
                        $display("DBG t=%0t f_valid=%b f_is_read=%b f_is_reg=%b f_len_hw=%0d f_addr=%h | rd_state=%b rd_tgt_reg=%b rd_beat_cnt=%0d rd_total=%0d | ctl_rd_empty=%b ctl_rd_en=%b wr_state=%d w_rd_empty=%b wr_hw_left=%0d wr_hwpb=%0d wr_bv=%b wr_tgt_reg=%b | reg_arv=%b reg_rv=%b reg_rrdy=%b ddr_awv=%b ddr_wv=%b ddr_bv=%b ddr_arv=%b ddr_rv=%b",
                            $time, dut.f_valid, dut.f_is_read, dut.f_is_reg, dut.f_len_hw, dut.f_addr,
                            dut.rd_state, dut.rd_target_reg, dut.rd_beat_cnt, dut.rd_total_beats,
                            dut.ctl_rd_empty, dut.ctl_rd_en, dut.wr_state, dut.w_rd_empty,
                            dut.wr_hw_left, dut.wr_hwpb, dut.wr_beat_valid, dut.wr_target_reg,
                            dut.u_reg_master.m_axi_arvalid,
                            dut.u_reg_master.m_axi_rvalid, m_reg_rready,
                            dut.u_ddr_master.m_axi_awvalid, dut.u_ddr_master.m_axi_wvalid,
                            dut.u_ddr_master.m_axi_bvalid,
                            dut.u_ddr_master.m_axi_arvalid, dut.u_ddr_master.m_axi_rvalid);
                        dbg_cnt = dbg_cnt + 1;
                    end
                end
            end
        join_none

        // ---- Test 1: host_init_sequence ----
        // Boot sequence: Reset(0xFF) -> ReadReg MR0 -> WriteReg MR4 -> ReadReg MR8.
        begin : test_init
            integer i;
            // Reset command (0xFF, single byte, no address/data).
            xspi_cs_n = 1'b1;
            @(negedge xspi_clk); #1; xspi_cs_n = 1'b0;
            @(negedge xspi_clk); #1; xspi_io_master = 8'hff;
            @(negedge xspi_clk); #1; xspi_cs_n = 1'b1;
            wait_axi_idle;
            // ReadReg MR0 (0x40, addr=0, 2 bytes = 1 halfword, 4 dummy).
            drive_frame(8'h40, 32'd0, 4, 0, 1, 1'b1);
            cov_host_init = cov_host_init + 1;
            // WriteReg MR4 (0xC0, addr=4, 2 bytes = 1 halfword, 0 dummy).
            data[0] = 16'h0005;
            drive_frame(8'hc0, 32'd4, 0, 1, 1, 1'b0);
            cov_host_init = cov_host_init + 1;
            // ReadReg MR8 (0x40, addr=8, 2 bytes = 1 halfword, 4 dummy).
            drive_frame(8'h40, 32'd8, 4, 2, 1, 1'b1);
            cov_host_init = cov_host_init + 1;
            wait_axi_idle;
        end

        // ---- Test 2: memory_mapped_write (DDR region) ----
        begin : test_mm_write
            integer i;
            for (i = 0; i < 4; i = i + 1) data[i] = {i[7:0], i[7:0] ^ 8'hff};
            drive_frame(8'ha0, 32'h9001_0000, 4, 0, 4, 1'b0);
            cov_mm_write = cov_mm_write + 1;
            wait_axi_idle;
            // Verify by reading back (2 halfwords per read frame).
            begin : verify_write
                integer i;
                drive_frame(8'h20, 32'h9001_0000, 4, 8, 2, 1'b1);
                for (i = 0; i < 2; i = i + 1) begin
                    chk_checked = chk_checked + 1;
                    if (data[8+i] !== {i[7:0], i[7:0] ^ 8'hff}) begin
                        chk_bad = chk_bad + 1;
                        $display("WRITE_VERIFY MISMATCH hw %0d: got %h expected %h", i, data[8+i], {i[7:0], i[7:0]^8'hff});
                    end
                end
                drive_frame(8'h20, 32'h9001_0004, 4, 10, 2, 1'b1);
                for (i = 0; i < 2; i = i + 1) begin
                    chk_checked = chk_checked + 1;
                    if (data[10+i] !== {i[7:0], i[7:0] ^ 8'hff}) begin
                        chk_bad = chk_bad + 1;
                        $display("WRITE_VERIFY MISMATCH hw %0d: got %h expected %h", i+2, data[10+i], {i[7:0], i[7:0]^8'hff});
                    end
                end
            end
            wait_axi_idle;
        end

        // ---- Test 3: memory_mapped_read (DDR region) ----
        begin : test_mm_read
            integer i;
            drive_frame(8'h20, 32'h9001_0000, 4, 12, 2, 1'b1);
            cov_mm_read = cov_mm_read + 1;
            begin : verify_read
                integer i;
                for (i = 0; i < 2; i = i + 1) begin
                    chk_checked = chk_checked + 1;
                    if (data[12+i] !== {i[7:0], i[7:0] ^ 8'hff}) begin
                        chk_bad = chk_bad + 1;
                        $display("READ_VERIFY MISMATCH hw %0d: got %h expected %h", i, data[12+i], {i[7:0], i[7:0]^8'hff});
                    end
                end
            end
            wait_axi_idle;
        end

        // ---- Test 4: burst_address_increment (DDR region) ----
        begin : test_burst
            integer i;
            reg [7:0] lo;
            for (i = 0; i < 8; i = i + 1) data[i] = {i[7:0], i[7:0] ^ 8'h5a};
            drive_frame(8'ha0, 32'h9001_0100, 4, 0, 8, 1'b0);
            cov_burst_addr = cov_burst_addr + 1;
            wait_axi_idle;
            // Read back in 2-halfword chunks and verify auto-increment.
            for (i = 0; i < 4; i = i + 1) begin
                drive_frame(8'h20, 32'h9001_0100 + i*4, 4, 16+i*2, 2, 1'b1);
                lo = i*2;
                chk_checked = chk_checked + 1;
                if (data[16+i*2] !== {lo, lo ^ 8'h5a}) begin
                    chk_bad = chk_bad + 1;
                    $display("BURST MISMATCH hw %0d: got %h expected %h", i*2, data[16+i*2], {lo, lo^8'h5a});
                end
                lo = i*2+1;
                chk_checked = chk_checked + 1;
                if (data[16+i*2+1] !== {lo, lo ^ 8'h5a}) begin
                    chk_bad = chk_bad + 1;
                    $display("BURST MISMATCH hw %0d: got %h expected %h", i*2+1, data[16+i*2+1], {lo, lo^8'h5a});
                end
            end
            wait_axi_idle;
        end

        // ---- Test 5: cs_deassert_mid_transfer ----
        begin : test_cs_mid
            xspi_cs_n = 1'b1;
            @(negedge xspi_clk); #1; xspi_cs_n = 1'b0;
            @(negedge xspi_clk); #1; xspi_io_master = 8'ha0;
            @(negedge xspi_clk); #1; xspi_io_master = 8'h90;   // addr[31:24]
            @(negedge xspi_clk); #1; xspi_io_master = 8'h01;   // addr[23:16]
            @(negedge xspi_clk); #1; xspi_io_master = 8'h02;   // addr[15:8]
            @(negedge xspi_clk); #1; xspi_io_master = 8'h00;   // addr[7:0]
            drive_dummy(4);
            // Start data: drive 1 halfword then deassert CS mid-transfer.
            @(negedge xspi_clk); #1; xspi_io_master = 8'hde;
            @(negedge xspi_clk); #1; xspi_cs_n = 1'b1;
            cov_cs_mid = cov_cs_mid + 1;
            wait_axi_idle;
        end

        // ---- Test 6: dummy_cycle_timing ----
        begin : test_dummy
            drive_frame(8'h20, 32'h9001_0000, 4, 24, 2, 1'b1);
            cov_dummy = cov_dummy + 1;
            wait_axi_idle;
            drive_frame(8'h20, 32'h9001_0000, 0, 26, 2, 1'b1);
            cov_dummy = cov_dummy + 1;
            wait_axi_idle;
        end

        // ---- Test 7: clock_ratio_extremes ----
        begin : test_clk_ratio
            integer i;
            for (i = 0; i < 2; i = i + 1) data[28+i] = {i[7:0], 8'h33};
            // Fast SCK: xspi period = 4ns, aclk period = 20ns.
            xspi_half = 2; aclk_half = 10; #100;
            drive_frame(8'ha0, 32'h9001_0500, 4, 28, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'h20, 32'h9001_0500, 4, 30, 2, 1'b1);
            cov_clk_ratio = cov_clk_ratio + 1;
            wait_axi_idle;
            // Slow SCK: xspi period = 40ns, aclk period = 4ns.
            xspi_half = 20; aclk_half = 2; #100;
            drive_frame(8'ha0, 32'h9001_0500, 4, 32, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'h20, 32'h9001_0500, 4, 34, 2, 1'b1);
            cov_clk_ratio = cov_clk_ratio + 1;
            wait_axi_idle;
            xspi_half = 10; aclk_half = 2;
        end

        // ---- Test 8: irregular_host_timing ----
        begin : test_irregular
            integer i;
            for (i = 0; i < 5; i = i + 1) begin
                data[36+i*2] = {i[7:0], 8'h11};
                data[36+i*2+1] = {i[7:0], 8'h22};
                drive_frame(8'ha0, 32'h9001_0600 + i*4, 4, 36+i*2, 2, 1'b0);
                case (i)
                    0: #0;
                    1: #50;
                    2: #200;
                    3: #500;
                    default: #1000;
                endcase
            end
            cov_irregular = cov_irregular + 1;
            wait_axi_idle;
        end

        // ---- Test 9: access_slave_reg (reg region, addr < DDR_BASE) ----
        begin : test_access_reg
            integer i;
            for (i = 0; i < 2; i = i + 1) data[40+i] = {i[7:0], 8'h77};
            drive_frame(8'ha0, 32'h9000_0000, 4, 40, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'h20, 32'h9000_0000, 4, 42, 2, 1'b1);
            cov_access_reg = cov_access_reg + 1;
            for (i = 0; i < 2; i = i + 1) begin
                chk_checked = chk_checked + 1;
                if (data[42+i] !== {i[7:0], 8'h77}) begin
                    chk_bad = chk_bad + 1;
                    $display("REG_ACCESS MISMATCH hw %0d: got %h expected %h", i, data[42+i], {i[7:0], 8'h77});
                end
            end
            wait_axi_idle;
        end

        // ---- Test 10: access_ddr4 (DDR region) ----
        begin : test_access_ddr
            integer i;
            for (i = 0; i < 2; i = i + 1) data[44+i] = {i[7:0], 8'h88};
            drive_frame(8'ha0, 32'h9001_1000, 4, 44, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'h20, 32'h9001_1000, 4, 46, 2, 1'b1);
            cov_access_ddr = cov_access_ddr + 1;
            for (i = 0; i < 2; i = i + 1) begin
                chk_checked = chk_checked + 1;
                if (data[46+i] !== {i[7:0], 8'h88}) begin
                    chk_bad = chk_bad + 1;
                    $display("DDR_ACCESS MISMATCH hw %0d: got %h expected %h", i, data[46+i], {i[7:0], 8'h88});
                end
            end
            wait_axi_idle;
        end

        // ---- Test 11: address_decode (both regions decode to correct slave) ----
        begin : test_addr_decode
            integer i;
            for (i = 0; i < 2; i = i + 1) begin
                data[48+i] = {i[7:0], 8'hAA};   // wr_reg
                data[50+i] = {i[7:0], 8'hBB};   // wr_ddr
            end
            // Write distinct patterns to reg and DDR regions.
            drive_frame(8'ha0, 32'h9000_0100, 4, 48, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'ha0, 32'h9001_2000, 4, 50, 2, 1'b0);
            wait_axi_idle;
            // Read back both and confirm each region holds its own pattern.
            drive_frame(8'h20, 32'h9000_0100, 4, 52, 2, 1'b1);
            for (i = 0; i < 2; i = i + 1) begin
                chk_checked = chk_checked + 1;
                if (data[52+i] !== {i[7:0], 8'hAA}) begin
                    chk_bad = chk_bad + 1;
                    $display("ADDR_DECODE reg MISMATCH hw %0d: got %h expected %h", i, data[52+i], {i[7:0], 8'hAA});
                end
            end
            drive_frame(8'h20, 32'h9001_2000, 4, 54, 2, 1'b1);
            for (i = 0; i < 2; i = i + 1) begin
                chk_checked = chk_checked + 1;
                if (data[54+i] !== {i[7:0], 8'hBB}) begin
                    chk_bad = chk_bad + 1;
                    $display("ADDR_DECODE ddr MISMATCH hw %0d: got %h expected %h", i, data[54+i], {i[7:0], 8'hBB});
                end
            end
            cov_addr_decode = cov_addr_decode + 1;
            wait_axi_idle;
        end

        // ---- Test 12: interleaved_reg_and_ddr ----
        begin : test_interleaved
            integer i;
            for (i = 0; i < 2; i = i + 1) begin
                data[56+i] = {i[7:0], 8'hCC};   // wr_reg
                data[58+i] = {i[7:0], 8'hDD};   // wr_ddr
            end
            // Alternate reg and DDR accesses.
            drive_frame(8'ha0, 32'h9000_0200, 4, 56, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'ha0, 32'h9001_3000, 4, 58, 2, 1'b0);
            wait_axi_idle;
            drive_frame(8'h20, 32'h9000_0200, 4, 60, 2, 1'b1);
            for (i = 0; i < 2; i = i + 1) begin
                chk_checked = chk_checked + 1;
                if (data[60+i] !== {i[7:0], 8'hCC}) begin
                    chk_bad = chk_bad + 1;
                    $display("INTERLEAVE reg MISMATCH hw %0d: got %h expected %h", i, data[60+i], {i[7:0], 8'hCC});
                end
            end
            drive_frame(8'h20, 32'h9001_3000, 4, 62, 2, 1'b1);
            for (i = 0; i < 2; i = i + 1) begin
                chk_checked = chk_checked + 1;
                if (data[62+i] !== {i[7:0], 8'hDD}) begin
                    chk_bad = chk_bad + 1;
                    $display("INTERLEAVE ddr MISMATCH hw %0d: got %h expected %h", i, data[62+i], {i[7:0], 8'hDD});
                end
            end
            cov_interleaved = cov_interleaved + 1;
            wait_axi_idle;
        end

        #500;
        $display("CHECK data_integrity %0d %0d", chk_checked, chk_bad);
        $display("COVER host_init_sequence %0d", cov_host_init);
        $display("COVER memory_mapped_write %0d", cov_mm_write);
        $display("COVER memory_mapped_read %0d", cov_mm_read);
        $display("COVER burst_address_increment %0d", cov_burst_addr);
        $display("COVER cs_deassert_mid_transfer %0d", cov_cs_mid);
        $display("COVER dummy_cycle_timing %0d", cov_dummy);
        $display("COVER clock_ratio_extremes %0d", cov_clk_ratio);
        $display("COVER irregular_host_timing %0d", cov_irregular);
        $display("COVER access_slave_reg %0d", cov_access_reg);
        $display("COVER access_ddr4 %0d", cov_access_ddr);
        $display("COVER address_decode %0d", cov_addr_decode);
        $display("COVER interleaved_reg_and_ddr %0d", cov_interleaved);
        $display("SIMEND %s", (chk_bad == 0) ? "ok" : "fail");
        $finish;
    end

    // Watchdog.
    initial begin
        #5_000_000;
        $display("WATCHDOG timeout");
        $finish;
    end

endmodule

// ============================================================================
// axi4_slave_model : minimal AXI4 slave with a beat-indexed memory.
// Accepts AW/AR immediately (awready/arready = 1), accepts W beats immediately
// (wready = 1), responds B one cycle after the final W beat, and streams R
// beats one per cycle. Never backpressures -- sufficient for this testbench
// where each transfer is small and frames are serialized with wait_axi_idle.
// ============================================================================
module axi4_slave_model #(
    parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32,
    parameter ID_WIDTH   = 4,
    parameter MEM_BEATS  = 65536
)(
    input  wire                    aclk,
    input  wire                    aresetn,
    // write-address
    input  wire                    awvalid,
    output reg                     awready,
    input  wire [ADDR_WIDTH-1:0]   awaddr,
    input  wire [7:0]              awlen,
    // write-data
    input  wire                    wvalid,
    output reg                     wready,
    input  wire [DATA_WIDTH-1:0]   wdata,
    input  wire                    wlast,
    // write-response
    output reg                     bvalid,
    input  wire                    bready,
    output reg  [1:0]              bresp,
    output reg  [ID_WIDTH-1:0]     bid,
    // read-address
    input  wire                    arvalid,
    output reg                     arready,
    input  wire [ADDR_WIDTH-1:0]   araddr,
    input  wire [7:0]              arlen,
    // read-data
    output reg                     rvalid,
    input  wire                    rready,
    output reg  [DATA_WIDTH-1:0]   rdata,
    output reg  [1:0]              rresp,
    output reg                     rlast,
    output reg  [ID_WIDTH-1:0]     rid
);
    localparam BB_SHIFT = $clog2(DATA_WIDTH/8);

    reg [DATA_WIDTH-1:0] mem [0:MEM_BEATS-1];

    // ---- write path ----
    reg        wr_active;
    reg [31:0] wr_base_beat;
    reg [7:0]  wr_len;
    reg [7:0]  wr_wcount;

    // ---- read path (single outstanding, sufficient for serialized frames) ----
    reg        rd_active;
    reg [31:0] rd_base_beat;
    reg [7:0]  rd_len;
    reg [7:0]  rd_rcount;

    assign awready = 1'b1;
    assign arready = 1'b1;
    assign wready  = 1'b1;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            wr_active    <= 1'b0;
            wr_base_beat <= 32'd0;
            wr_len       <= 8'd0;
            wr_wcount    <= 8'd0;
            bvalid       <= 1'b0;
            bresp        <= 2'b00;
            bid          <= {ID_WIDTH{1'b0}};
            rd_active    <= 1'b0;
            rd_base_beat <= 32'd0;
            rd_len       <= 8'd0;
            rd_rcount    <= 8'd0;
            rvalid       <= 1'b0;
            rdata        <= {DATA_WIDTH{1'b0}};
            rresp        <= 2'b00;
            rlast        <= 1'b0;
            rid          <= {ID_WIDTH{1'b0}};
        end else begin
            // ---- write: latch AW, count W beats, respond B ----
            if (awvalid && awready) begin
                wr_active    <= 1'b1;
                wr_base_beat <= awaddr >> BB_SHIFT;
                wr_len       <= awlen + 8'd1;
                wr_wcount    <= 8'd0;
            end
            if (wr_active && wvalid && wready) begin
                mem[wr_base_beat + wr_wcount] <= wdata;
                wr_wcount <= wr_wcount + 8'd1;
            end
            // respond B on the cycle after the final W beat is accepted
            bvalid <= (wr_active && wvalid && wready) &&
                      ((wr_wcount + 8'd1) == wr_len);
            if (bvalid && bready) begin
                wr_active <= 1'b0;
                bvalid    <= 1'b0;
            end

            // ---- read: latch AR, stream R beats ----
            if (arvalid && arready) begin
                rd_active    <= 1'b1;
                rd_base_beat <= araddr >> BB_SHIFT;
                rd_len       <= arlen + 8'd1;
                rd_rcount    <= 8'd0;
            end
            rvalid <= rd_active;
            if (rd_active) begin
                rdata <= mem[rd_base_beat + rd_rcount];
                rlast <= (rd_rcount == rd_len - 8'd1);
            end
            if (rvalid && rready) begin
                if (rlast) rd_active <= 1'b0;
                else       rd_rcount <= rd_rcount + 8'd1;
            end
        end
    end

endmodule


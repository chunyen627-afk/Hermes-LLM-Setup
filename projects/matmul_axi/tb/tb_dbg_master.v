`timescale 1ns/1ps
module tb_dbg;
  reg clk=0, aresetn=0; always #5 clk=~clk;
  wire m_wvalid, m_awvalid, m_bvalid;
  wire [31:0] m_awaddr; wire [7:0] m_awlen; wire m_wlast;
  wire wr_done, wr_data_in_ready, rd_busy, wr_busy;
  reg rd_start=0, wr_start=0; reg [31:0] rd_addr=0, wr_addr=0; reg [19:0] rd_len=0, wr_len=0;
  reg wr_data_in_valid=0; reg [255:0] wr_data_in=0;
  axi4_master #(.DATA_WIDTH(256)) dut (
    .aclk(clk),.aresetn(aresetn),
    .rd_start(rd_start),.rd_addr(rd_addr),.rd_len_bytes(rd_len),.rd_busy(rd_busy),
    .rd_data_valid(),.rd_data(),.rd_last(),
    .wr_start(wr_start),.wr_addr(wr_addr),.wr_len_bytes(wr_len),.wr_busy(wr_busy),
    .wr_data_in_valid(wr_data_in_valid),.wr_data_in(wr_data_in),.wr_data_in_ready(wr_data_in_ready),.wr_done(wr_done),
    .m_axi_awvalid(m_awvalid),.m_axi_awready(1'b1),.m_axi_awaddr(m_awaddr),.m_axi_awlen(m_awlen),
    .m_axi_awsize(),.m_axi_awburst(),.m_axi_awid(),
    .m_axi_wvalid(m_wvalid),.m_axi_wready(1'b1),.m_axi_wdata(),.m_axi_wstrb(),.m_axi_wlast(m_wlast),
    .m_axi_bvalid(m_bvalid),.m_axi_bready(),.m_axi_bresp(2'b00),.m_axi_bid(),
    .m_axi_arvalid(),.m_axi_arready(1'b1),.m_axi_araddr(),.m_axi_arlen(),.m_axi_arsize(),.m_axi_arburst(),.m_axi_arid(),
    .m_axi_rvalid(1'b0),.m_axi_rready(),.m_axi_rdata(256'h0),.m_axi_rresp(2'b00),.m_axi_rlast(1'b0),.m_axi_rid()
  );
  integer wbeats=0;
  always @(posedge clk) if (m_wvalid && m_wready) begin wbeats=wbeats+1; $display("t=%0t W beat %0d wlast=%b", $time, wbeats, m_wlast); end
  always @(posedge clk) if (m_awvalid && m_awready) $display("t=%0t AW addr=%h len=%0d", $time, m_awaddr, m_awlen);
  initial begin
    aresetn=0; repeat(5)@(posedge clk); aresetn=1; repeat(2)@(posedge clk);
    wr_addr=32'h1000; wr_len=4*32; wr_start=1; @(negedge clk); wr_start=0;
    for (integer i=0;i<4;i=i+1) begin
      while(!wr_data_in_ready) @(negedge clk);
      wr_data_in_valid=1; wr_data_in={i,256'h0}; @(negedge clk); wr_data_in_valid=0;
    end
    $display("waiting for wr_done...");
    repeat(300)@(posedge clk);
    $display("total W beats sent=%0d", wbeats);
    $finish;
  end
endmodule

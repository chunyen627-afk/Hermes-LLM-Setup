// async_fifo : asynchronous FIFO for crossing clock domains (CDC).
//
// This is the correct way to move MULTI-BIT data between two unrelated clocks.
// A naive two-flop synchronizer only works for a single bit that is stable for
// >= 2 destination cycles; applied to multi-bit data it can latch a transient
// combination that never existed in the source domain (e.g. reading a counter
// mid-increment). The fix used here:
//   - the DATA path is a plain dual-port RAM (no synchronization needed -- the
//     read/write pointers guarantee we only read a location after it has been
//     written and before it is overwritten);
//   - the POINTERS are gray-coded so each transition changes exactly ONE bit,
//     making each pointer behave like a single-bit signal that a two-flop
//     synchronizer can safely sample;
//   - full/empty are generated in their own domain from the synchronized
//     pointers.
//
// DEPTH must be a power of two. The FIFO is safe for any ratio of wr_clk to
// rd_clk (including one being much faster than the other).

module async_fifo #(
    parameter DATA_WIDTH = 16,
    parameter DEPTH      = 256,          // power of two
    parameter AW         = $clog2(DEPTH) // log2(DEPTH)
)(
    // ---- write (source) domain ----
    input  wire                  wr_clk,
    input  wire                  rst_n,        // async reset: clears both pointers
    input  wire                  wr_en,
    input  wire [DATA_WIDTH-1:0] wr_data,
    output wire                  wr_full,

    // ---- read (destination) domain ----
    input  wire                  rd_clk,
    input  wire                  rd_en,
    output wire [DATA_WIDTH-1:0] rd_data,
    output wire                  rd_empty
);
    localparam PW = AW + 1;   // pointer width: one extra bit for wrap-around

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    // gray = (bin >> 1) ^ bin
    function [PW-1:0] bin2gray;
        input [PW-1:0] b;
        begin : bg
            bin2gray = (b >> 1) ^ b;
        end
    endfunction

    // ================= write domain =================
    reg [PW-1:0] wr_bin;
    reg [PW-1:0] wr_gray;
    wire [AW-1:0] wr_addr = wr_bin[AW-1:0];

    always @(posedge wr_clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_bin  <= {PW{1'b0}};
            wr_gray <= {PW{1'b0}};
        end else if (wr_en && !wr_full) begin
            mem[wr_addr] <= wr_data;
            wr_bin  <= wr_bin + 1'b1;
            wr_gray <= bin2gray(wr_bin + 1'b1);
        end
    end

    // read pointer synchronized into the write domain (2-flop chain). Safe
    // because rd_gray is gray-coded: only one bit changes per transition.
    reg [PW-1:0] rd_gray_sync1, rd_gray_sync2;
    always @(posedge wr_clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_gray_sync1 <= {PW{1'b0}};
            rd_gray_sync2 <= {PW{1'b0}};
        end else begin
            rd_gray_sync1 <= rd_gray;
            rd_gray_sync2 <= rd_gray_sync1;
        end
    end

    // full: gray write pointer has lapped the gray read pointer. In gray code
    // this means the two MSBs differ and all lower bits are equal.
    assign wr_full = (wr_gray[PW-1]   != rd_gray_sync2[PW-1]) &&
                     (wr_gray[PW-2]   != rd_gray_sync2[PW-2]) &&
                     (wr_gray[PW-3:0] == rd_gray_sync2[PW-3:0]);

    // ================= read domain =================
    reg [PW-1:0] rd_bin;
    reg [PW-1:0] rd_gray;
    wire [AW-1:0] rd_addr = rd_bin[AW-1:0];

    always @(posedge rd_clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_bin  <= {PW{1'b0}};
            rd_gray <= {PW{1'b0}};
        end else if (rd_en && !rd_empty) begin
            rd_bin  <= rd_bin + 1'b1;
            rd_gray <= bin2gray(rd_bin + 1'b1);
        end
    end

    assign rd_data = mem[rd_addr];

    // write pointer synchronized into the read domain (2-flop chain).
    reg [PW-1:0] wr_gray_sync1, wr_gray_sync2;
    always @(posedge rd_clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_gray_sync1 <= {PW{1'b0}};
            wr_gray_sync2 <= {PW{1'b0}};
        end else begin
            wr_gray_sync1 <= wr_gray;
            wr_gray_sync2 <= wr_gray_sync1;
        end
    end

    // empty: gray pointers equal.
    assign rd_empty = (rd_gray == wr_gray_sync2);

endmodule

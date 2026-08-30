// tb_matmul_core : verify matmul_core against the C-oracle expected output.
`timescale 1ns/1ps
module tb_matmul_core #(
    parameter D = 8,
    parameter N = 16
);

    reg         clk = 0;
    reg         rst_n = 0;
    reg         start = 0;
    wire        done;
    wire [D*32-1:0] xout_vec;

    matmul_core #(.D(D), .N(N)) dut (
        .clk(clk), .rst_n(rst_n), .start(start),
        .done(done), .xout_vec(xout_vec)
    );

    reg [31:0] expected [0:D-1];
    initial $readmemh("expected.hex", expected);

    always #5 clk = ~clk;   // 100 MHz

    integer i;
    integer mismatches;
    integer tick;
    reg [31:0] got, exp;

    initial begin
        mismatches = 0;
        rst_n = 0; start = 0;
        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        start = 1;
        @(posedge clk);
        start = 0;

        tick = 0;
        while (!done && tick < 5_000_000) begin
            @(posedge clk);
            tick = tick + 1;
        end
        if (!done) begin
            $display("TIMEOUT: done never asserted");
            $finish;
        end

        repeat (2) @(posedge clk);
        for (i = 0; i < D; i = i + 1) begin
            got = xout_vec[i*32 +: 32];
            exp = expected[i];
            if (got !== exp) begin
                mismatches = mismatches + 1;
                if (mismatches <= 10)
                    $display("MISMATCH i=%0d got=%08x exp=%08x", i, got, exp);
            end
        end
        $display("==== MATMUL SUMMARY ====");
        $display("D=%0d N=%0d : %0d / %0d bit-exact (%0d mismatches)",
                 D, N, D - mismatches, D, mismatches);
        if (mismatches == 0) $display("ALL_PASS");
        else                 $display("HAS_FAILURES");
        $finish;
    end

endmodule

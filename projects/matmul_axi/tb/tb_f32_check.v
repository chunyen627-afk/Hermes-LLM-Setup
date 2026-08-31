// Self-checking testbench for f32_mul and f32_add.
// Loads vectors + expected results (from ref/f32.py golden model) via $readmemh,
// drives each vector through the DUTs, and counts bit-exact mismatches.
`timescale 1ns/1ps

module tb_f32_check;

    parameter integer N = 50000;

    reg  [31:0] a, b;
    reg         sub;
    wire [31:0] mul_y, add_y;

    f32_mul dut_mul (.a(a), .b(b), .y(mul_y));
    f32_add dut_add (.a(a), .b(b), .sub(sub), .y(add_y));

    reg [31:0] mul_a[N-1:0], mul_b[N-1:0], mul_exp[N-1:0];
    reg [31:0] add_a[N-1:0], add_b[N-1:0], add_exp[N-1:0];
    reg        add_sub[N-1:0];

    integer i;
    integer mul_fail = 0, add_fail = 0;
    integer shown = 0;

    initial begin
        $readmemh("out/mul_a.hex",   mul_a);
        $readmemh("out/mul_b.hex",   mul_b);
        $readmemh("out/mul_exp.hex", mul_exp);
        $readmemh("out/add_a.hex",   add_a);
        $readmemh("out/add_b.hex",   add_b);
        $readmemh("out/add_sub.hex", add_sub);
        $readmemh("out/add_exp.hex", add_exp);

        for (i = 0; i < N; i = i + 1) begin
            a = mul_a[i]; b = mul_b[i]; sub = 1'b0;
            #1;
            if (mul_y !== mul_exp[i]) begin
                mul_fail = mul_fail + 1;
                if (shown < 25) begin
                    $display("MUL MISMATCH i=%0d a=%h b=%h got=%h exp=%h", i, a, b, mul_y, mul_exp[i]);
                    shown = shown + 1;
                end
            end
        end

        for (i = 0; i < N; i = i + 1) begin
            a = add_a[i]; b = add_b[i]; sub = add_sub[i];
            #1;
            if (add_y !== add_exp[i]) begin
                add_fail = add_fail + 1;
                if (shown < 50) begin
                    $display("ADD MISMATCH i=%0d a=%h b=%h sub=%b got=%h exp=%h", i, a, b, sub, add_y, add_exp[i]);
                    shown = shown + 1;
                end
            end
        end

        $display("==== SUMMARY ====");
        $display("MUL: %0d / %0d passed (%0d failures)", N - mul_fail, N, mul_fail);
        $display("ADD: %0d / %0d passed (%0d failures)", N - add_fail, N, add_fail);
        if (mul_fail == 0 && add_fail == 0)
            $display("ALL_PASS");
        else
            $display("HAS_FAILURES");
        #10;
        $finish;
    end

endmodule

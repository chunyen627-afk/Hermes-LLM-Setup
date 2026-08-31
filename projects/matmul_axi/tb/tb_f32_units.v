// Testbench for f32_mul and f32_add.
// Generates random + edge-case inputs, logs all results for Python comparison.
`timescale 1ns/1ps

module tb_f32_units;

    reg  [31:0] a, b;
    reg         sub;
    wire [31:0] mul_y, add_y;

    f32_mul  dut_mul (.a(a), .b(b), .y(mul_y));
    f32_add  dut_add (.a(a), .b(b), .sub(sub), .y(add_y));

    integer i;
    integer errors = 0;

    // Simple counter-based stimulus (deterministic, no latch issues)
    reg [31:0] seed;

    initial begin
        $dumpfile("f32_units.vcd");
        $dumpvars(0, tb_f32_units);
        
        seed = 32'hDEADBEEF;
        sub = 1'b0;   // edge cases below are all additions
        
        // ---- Edge cases for multiply ----
        // 0 * 0
        a = 32'h00000000; b = 32'h00000000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // 1.0 * 1.0
        a = 32'h3F800000; b = 32'h3F800000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // -1.0 * 1.0
        a = 32'hBF800000; b = 32'h3F800000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // Inf * 0
        a = 32'h7F800000; b = 32'h00000000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // Inf * finite
        a = 32'h7F800000; b = 32'h40000000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // NaN * anything
        a = 32'h7FC00000; b = 32'h3F800000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // Max normal * max normal (overflow)
        a = 32'h7F7FFFFF; b = 32'h7F7FFFFF;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // Min normal * min normal (underflow to subnormal)
        a = 32'h00800000; b = 32'h00800000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // Subnormal * normal
        a = 32'h00000001; b = 32'h3F800000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // Subnormal * subnormal
        a = 32'h00000001; b = 32'h00000001;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // -0 * +0
        a = 32'h80000000; b = 32'h00000000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // -0 * -1.0
        a = 32'h80000000; b = 32'hBF800000;
        #10 $display("MUL %h %h => %h", a, b, mul_y);
        
        // ---- Edge cases for add ----
        // 0 + 0
        a = 32'h00000000; b = 32'h00000000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // +0 + -0
        a = 32'h00000000; b = 32'h80000000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // 1.0 + 1.0
        a = 32'h3F800000; b = 32'h3F800000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // 1.0 + (-1.0) = 0
        a = 32'h3F800000; b = 32'hBF800000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // Inf + (-Inf) = NaN
        a = 32'h7F800000; b = 32'hFF800000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // Inf + finite
        a = 32'h7F800000; b = 32'h40000000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // NaN + anything
        a = 32'h7FC00000; b = 32'h3F800000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // Large + small (cancellation)
        a = 32'h4B000000; b = 32'hCB000001; // 1e6 + (-1e6 - tiny)
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // Max + max (overflow)
        a = 32'h7F7FFFFF; b = 32'h7F7FFFFF;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // Min subnormal + min subnormal
        a = 32'h00000001; b = 32'h00000001;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // Subnormal + normal (very different exponents)
        a = 32'h00000001; b = 32'h4B000000;
        #10 $display("ADD %h %h => %h", a, b, add_y);
        
        // ---- Random tests: 5000 multiply + 5000 add ----
        // NOTE: assign inputs, then #1 to let the combinational DUT settle
        // BEFORE reading the output. Reading in the same delta returns the
        // PREVIOUS stimulus's result (off-by-one). This was a real bug that
        // made ~99% of results look wrong when the RTL was actually fine.
        for (i = 0; i < 5000; i = i + 1) begin
            seed = seed + 32'h12345678; // advance LFSR
            a = seed;
            b = {seed[15:0], seed[31:16]}; // different arrangement
            #1 $display("MUL %h %h => %h", a, b, mul_y);
        end

        for (i = 0; i < 5000; i = i + 1) begin
            seed = seed + 32'h87654321;
            a = seed;
            b = {seed[15:0], seed[31:16]};
            sub = seed[31];   // exercise both add and subtract
            #1 $display("ADD %h %h %b => %h", a, b, sub, add_y);
        end

        // ---- Targeted random: bf16-range values (what matmul actually uses) ----
        // bf16 has 8 exponent bits + 7 mantissa bits. When zero-extended to f32,
        // the lower 16 mantissa bits are always 0.
        for (i = 0; i < 5000; i = i + 1) begin
            seed = seed + 32'hCAFEF00D;
            // Create bf16-like f32: top 16 bits random, bottom 16 zero
            a = {seed[15:0], 16'd0};
            b = {seed[31:16], 16'd0};
            #1 $display("MUL %h %h => %h", a, b, mul_y);
        end

        for (i = 0; i < 5000; i = i + 1) begin
            seed = seed + 32'hF00DCAFE;
            a = {seed[15:0], 16'd0};
            b = {seed[31:16], 16'd0};
            sub = seed[31];   // exercise both add and subtract
            #1 $display("ADD %h %h %b => %h", a, b, sub, add_y);
        end
        
        $display("TESTBENCH_DONE");
        #100 $finish;
    end

endmodule

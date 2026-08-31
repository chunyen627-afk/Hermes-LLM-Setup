// Minimal debug: drive a few hardcoded vectors, print results.
`timescale 1ns/1ps
module tb_dbg;
    reg [31:0] a, b; reg sub;
    wire [31:0] my, ay;
    f32_mul m(.a(a),.b(b),.y(my));
    f32_add ad(.a(a),.b(b),.sub(sub),.y(ay));
    initial begin
        sub=0;
        // 1.0 * 1.0 = 1.0 (3F800000)
        a=32'h3F800000; b=32'h3F800000; #1 $display("MUL 1*1 => %h (exp 3f800000)", my);
        // 2.0 * 3.0 = 6.0 (40C00000)
        a=32'h40000000; b=32'h40400000; #1 $display("MUL 2*3 => %h (exp 40c00000)", my);
        // 0x46685257 * 0x1a3d1fa7
        a=32'h46685257; b=32'h1A3D1FA7; #1 $display("MUL case0 => %h (exp 212ba184)", my);
        // min normal * min normal -> subnormal
        a=32'h00800000; b=32'h00800000; #1 $display("MUL minnorm*minnorm => %h", my);
        // 1.0 + 1.0 = 2.0 (40000000)
        a=32'h3F800000; b=32'h3F800000; #1 $display("ADD 1+1 => %h (exp 40000000)", ay);
        // 2.0 + 3.0 = 5.0 (40A00000)
        a=32'h40000000; b=32'h40800000; #1 $display("ADD 2+3 => %h (exp 40a00000)", ay);
        // 0x46685257 + 0x1a3d1fa7
        a=32'h46685257; b=32'h1A3D1FA7; #1 $display("ADD case0 => %h (exp 46685257)", ay);
        #10 $finish;
    end
endmodule

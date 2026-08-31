`timescale 1ns/1ps
module tb_dbg_add;
    reg [31:0] a, b;
    wire y;
    // instantiate the real module
    wire [31:0] add_y;
    f32_add dut (.a(a), .b(b), .sub(1'b0), .y(add_y));

    initial begin
        // case 1: smallest subnormal + itself = 2^-148 = 0x00000002
        a = 32'h00000001; b = 32'h00000001;
        #5 $display("case1 a=00000001 b=00000001 => %h (exp 00000002)", add_y);

        // case 2: 1.0 + 1.0 = 2.0
        a = 32'h3F800000; b = 32'h3F800000;
        #5 $display("case2 1+1 => %h (exp 40000000)", add_y);

        // case 3: two equal normals, a=a=0xdeadb321 (positive)
        a = 32'hdeadb321; b = 32'hdeadb321;
        #5 $display("case3 a+a => %h", add_y);

        // case 4: 0x4B000000 + 0xCB000001 (from tb edge case)
        a = 32'h4B000000; b = 32'hCB000001;
        #5 $display("case4 => %h", add_y);

        // case 5: subnormal + normal very different exp
        a = 32'h00000001; b = 32'h4B000000;
        #5 $display("case5 => %h (exp 4b000000)", add_y);

        #10 $finish;
    end
endmodule

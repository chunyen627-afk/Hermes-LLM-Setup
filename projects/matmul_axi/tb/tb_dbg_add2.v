`timescale 1ns/1ps
module tb_dbg_add2;
    reg [31:0] a, b;
    wire [31:0] add_y;
    f32_add dut (.a(a), .b(b), .sub(1'b0), .y(add_y));

    // tap the internal wires of the DUT via hierarchical refs
    initial begin
        a = 32'hdeadb321; b = 32'hb321dead;
        #5
        $display("a=%h b=%h", a, b);
        $display("  a_s=%b a_e=%0d a_f=%h  b_s=%b b_e=%0d b_f=%h",
                 dut.a_s, dut.a_e, dut.a_f, dut.b_s, dut.b_e, dut.b_f);
        $display("  Sa=%h Sb=%h", dut.Sa, dut.Sb);
        $display("  Ea=%0d Eb=%0d", dut.Ea, dut.Eb);
        $display("  beff_s=%b same_sign=%b a_ge=%b", dut.beff_s, dut.same_sign, dut.a_ge);
        $display("  Sbig=%h Ssmall=%h Ebase=%0d", dut.Sbig, dut.Ssmall, dut.Ebase);
        $display("  d=%0d shs=%0d sh=%0d", dut.d, dut.shs, dut.sh);
        $display("  big_aligned=%h", dut.big_aligned);
        $display("  small_aligned=%h", dut.small_aligned);
        $display("  SUM=%h", dut.SUM);
        $display("  res_sign=%b", dut.res_sign);
        $display("  y=%h", add_y);
        #10 $finish;
    end
endmodule

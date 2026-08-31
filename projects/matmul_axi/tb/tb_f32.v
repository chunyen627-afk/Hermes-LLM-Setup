// Unit testbench for f32_mul and f32_add.
// Reads vectors from out/mul_vectors.txt and out/add_vectors.txt, checks each.
// mul line: <a> <b> <exp>
// add line: <a> <b> <sub> <exp>
`timescale 1ns/1ps
module tb_f32;
    reg  [31:0] a, b;
    reg         sub;
    wire [31:0] y_mul, y_add;

    f32_mul  dut_mul (.a(a), .b(b), .y(y_mul));
    f32_add  dut_add (.a(a), .b(b), .sub(sub), .y(y_add));

    integer i, nmul, nadd, badmul, badadd;
    reg [31:0] va, vb, vexp;
    reg        vsub;
    integer fd_mul, fd_add;

    initial begin
        a = 0; b = 0; sub = 0;
        nmul = 0; nadd = 0; badmul = 0; badadd = 0;

        // ---- multiply vectors ----
        fd_mul = $fopen("out/mul_vectors.txt", "r");
        if (fd_mul == 0) begin
            $display("FATAL: cannot open out/mul_vectors.txt");
            $finish;
        end
        while (!$feof(fd_mul)) begin
            if ($fscanf(fd_mul, "%h %h %h", va, vb, vexp) == 3) begin
                a = va; b = vb; sub = 0;
                #0;
                nmul = nmul + 1;
                if (y_mul !== vexp) begin
                    badmul = badmul + 1;
                    if (badmul <= 20)
                        $display("MUL MISMATCH #%0d a=%h b=%h got=%h exp=%h", nmul, va, vb, y_mul, vexp);
                end
            end
        end
        $fclose(fd_mul);

        // ---- add vectors ----
        fd_add = $fopen("out/add_vectors.txt", "r");
        if (fd_add == 0) begin
            $display("FATAL: cannot open out/add_vectors.txt");
            $finish;
        end
        while (!$feof(fd_add)) begin
            if ($fscanf(fd_add, "%h %h %b %h", va, vb, vsub, vexp) == 4) begin
                a = va; b = vb; sub = vsub;
                #0;
                nadd = nadd + 1;
                if (y_add !== vexp) begin
                    badadd = badadd + 1;
                    if (badadd <= 20)
                        $display("ADD MISMATCH #%0d a=%h b=%h sub=%b got=%h exp=%h", nadd, va, vb, vsub, y_add, vexp);
                end
            end
        end
        $fclose(fd_add);

        $display("==================================================");
        $display("f32_mul : %0d checked, %0d mismatches", nmul, badmul);
        $display("f32_add : %0d checked, %0d mismatches", nadd, badadd);
        if (badmul == 0 && badadd == 0)
            $display("RESULT: PASS");
        else
            $display("RESULT: FAIL");
        $finish;
    end
endmodule

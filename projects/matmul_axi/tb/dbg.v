module dbg;
initial begin
    integer fd, r; reg [31:0] a,b,c;
    fd = $fopen("out/mul_vectors.txt", "r");
    $display("fd=%0d", fd);
    r = $fscanf(fd, "%h %h %h", a, b, c);
    $display("r=%0d a=%h b=%h c=%h", r, a, b, c);
    $finish;
end
endmodule

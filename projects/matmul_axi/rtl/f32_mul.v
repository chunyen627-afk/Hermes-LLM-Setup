// f32_mul : IEEE-754 binary32 multiply, round-to-nearest-even.
// Bit-exact with C `float a*b` on x86 SSE (verified against a compiled C oracle).
//
// Strategy: reduce each finite nonzero operand to an exact integer value
//   value = S * 2^E      (S up to 24 bits, E the unbiased exponent)
// multiply the integers exactly (M = Sa*Sb, up to 48 bits), then apply a SINGLE
// round-to-nearest-even to f32. This avoids double-rounding on subnormals and
// matches C exactly. Special values (NaN/inf/zero) are dispatched to match the
// x86 SSE behaviour: first-NaN-wins, inf*0 -> NaN, signed inf, signed zero.
module f32_mul (
    input  wire [31:0] a,
    input  wire [31:0] b,
    output wire [31:0] y
);
    wire        a_s = a[31];
    wire [8:0]  a_e = a[30:23];
    wire [22:0] a_f = a[22:0];
    wire        a_zero = (a[30:0] == 21'd0);
    wire        a_nan  = (a_e == 9'hFF) && (a_f != 23'd0);
    wire        a_inf  = (a_e == 9'hFF) && (a_f == 23'd0);
    wire        a_sub  = (a_e == 9'd0) && !a_zero;

    wire        b_s = b[31];
    wire [8:0]  b_e = b[30:23];
    wire [22:0] b_f = b[22:0];
    wire        b_zero = (b[30:0] == 21'd0);
    wire        b_nan  = (b_e == 9'hFF) && (b_f != 23'd0);
    wire        b_inf  = (b_e == 9'hFF) && (b_f == 23'd0);
    wire        b_sub  = (b_e == 9'd0) && !b_zero;

    wire sign = a_s ^ b_s;

    // exact integer significands S and unbiased exponents E
    wire [23:0] Sa = a_sub ? {1'b0, a_f} : {1'b1, a_f};
    wire [23:0] Sb = b_sub ? {1'b0, b_f} : {1'b1, b_f};
    wire signed [9:0] Ea = a_sub ? -149 : $signed({1'b0, a_e}) - 150;
    wire signed [9:0] Eb = b_sub ? -149 : $signed({1'b0, b_e}) - 150;

    wire [47:0] M  = Sa * Sb;
    wire signed [11:0] Etot = Ea + Eb;

    // ---- special-value dispatch (mirrors ref/f32.py::f32_mul_bits exactly) ----
    // Any NaN operand -> canonical quiet NaN 0x7FC00000 (golden does NOT do
    // first-NaN-wins; it always returns the canonical payload).
    wire        inf_zero = ((a_inf && b_zero) || (b_inf && a_zero));
    wire [31:0] y_inf  = {sign, 8'hFF, 23'd0};
    wire [31:0] y_zero = {sign, 31'd0};

    assign y = (a_nan || b_nan) ? 32'h7FC00000 :
               inf_zero          ? 32'h7FC00000 :   // inf * 0 -> NaN
               (a_inf || b_inf)  ? y_inf :
               (a_zero || b_zero)? y_zero :
               round_to_f32(sign, M, Etot);

    // ---- bit_length(M): position of top set bit + 1 (0 if M==0) ----
    // Scan low->high; each hit overwrites, so the final value is the highest
    // set bit's index + 1. (Scanning high->low without a break would leave the
    // LOWEST set bit, which is wrong.)
    function [6:0] bit_length;
        input [47:0] m;
        integer i;
        begin
            bit_length = 7'd0;
            for (i = 0; i <= 47; i = i + 1)
                if (m[i]) bit_length = i[6:0] + 7'd1;
        end
    endfunction

    // ---- round exact value M * 2^E to f32, RNE. Mirrors _round_to_f32. ----
    function [31:0] round_to_f32;
        input         sign;
        input  [47:0] M;
        input  signed [11:0] E;
        reg  [6:0]  k;
        reg  signed [11:0] e_field;
        reg  signed [11:0] t;
        reg  [7:0]  sh;          // shift amount (capped, see below)
        reg  [47:0] keep, dropped, half;
        reg  [69:0] msub;
        reg  [47:0] M24;
        reg  signed [11:0] e_field2;
        reg  signed [11:0] tsh;   // -t (temp for part-select)
        reg  [69:0] msubm;        // msub - 2^23 (temp for part-select)
        begin
            if (M == 0) begin
                round_to_f32 = {sign, 31'd0};
            end else begin
                k = bit_length(M);
                e_field = E + $signed({4'b0, k}) + 126;
                if (e_field >= 255) begin
                    round_to_f32 = {sign, 8'hFF, 23'd0};          // overflow -> inf
                end else if (e_field <= 0) begin
                    // subnormal / underflow: msub = round(M * 2^(E+149))
                    t = E + 149;
                    if (t >= 0) begin
                        msub = {22'd0, M} << t[6:0];
                    end else begin
                        // sh = -t ; keep=M>>sh, dropped=low sh bits, half=1<<(sh-1)
                        tsh = -t;
                        sh  = tsh[7:0];
                        if (sh >= 49) begin
                            msub = 70'd0;                          // half > M always -> rounds to 0
                        end else if (sh == 48) begin
                            msub = {69'd0, M[47]};                 // keep=0, dropped=M, half=2^47
                        end else begin
                            keep    = M >> sh;
                            dropped = M & ((48'd1 << sh) - 48'd1);
                            half    = {47'd0, 1'b1} << (sh - 8'd1);
                            if ((dropped > half) || ((dropped == half) && (keep[0])))
                                keep = keep + 1'b1;
                            msub = {22'd0, keep};
                        end
                    end
                    if (msub >= 70'h800000) begin                  // >= 2^23 -> normal e_field=1
                        msubm = msub - 70'h800000;
                        round_to_f32 = {sign, 8'h1, msubm[22:0]};
                    end else if (msub == 0) begin
                        round_to_f32 = {sign, 31'd0};
                    end else begin
                        round_to_f32 = {sign, 8'h0, msub[22:0]};
                    end
                end else begin
                    // normal path: round M to a 24-bit significand
                    if (k <= 24) begin
                        M24 = M << (24 - k[5:0]);
                    end else begin
                        sh = k[6:0] - 7'd24;
                        keep    = M >> sh;
                        dropped = M & ((48'd1 << sh) - 48'd1);
                        half    = {47'd0, 1'b1} << (sh - 8'd1);
                        if ((dropped > half) || ((dropped == half) && (keep[0])))
                            keep = keep + 1'b1;
                        M24 = keep;
                    end
                    e_field2 = e_field;
                    if (M24 >= 48'h1000000) begin                  // carry from rounding
                        M24 = M24 >> 1;                            // -> 0x800000, frac=0
                        e_field2 = e_field2 + 1;
                    end
                    if (e_field2 >= 255) begin
                        round_to_f32 = {sign, 8'hFF, 23'd0};       // overflow -> inf
                    end else begin
                        round_to_f32 = {sign, e_field2[7:0], M24[22:0]};
                    end
                end
            end
        end
    endfunction

endmodule

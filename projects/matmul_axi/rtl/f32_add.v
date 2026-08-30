// f32_add : IEEE-754 binary32 add/subtract, round-to-nearest-even.
// Bit-exact with ref/f32.py::f32_add_bits (the verified golden model).
//
// Strategy (mirrors the golden exactly): reduce each finite nonzero operand to
// an exact integer value  value = S * 2^E  (S up to 24 bits, E unbiased), align
// both to the SMALLER exponent by shifting the larger LEFT (exact, no bits lost),
// add/subtract the magnitudes to get an EXACT integer SUM, then apply a SINGLE
// round-to-nearest-even to f32. This avoids double-rounding on subnormals.
//
// Width note: sh = |ea-eb| can be as large as 127-(-149) = 276, so the aligned
// significand S_big << sh needs up to 24+276 = 300 bits. We carry the full exact
// sum in a 320-bit vector so rounding is applied to the true value (no sticky
// loss), exactly like the golden's arbitrary-precision integers.
//
// Special values mirror the golden: any NaN -> canonical 0x7FC00000, inf-inf ->
// NaN, lone inf keeps its sign, +/-0 handling per the golden, exact zero -> +0.
module f32_add (
    input  wire [31:0] a,
    input  wire [31:0] b,
    input  wire        sub,   // 1 => a - b, 0 => a + b
    output wire [31:0] y
);
    // Exact-integer widths (fixed constants so Icarus can size everything).
    //   MW  = 320 : width of the exact sum M (S_big<<sh | S_small), max ~300 bits
    //   MWS = 344 : width of msub = M shifted left up to 24 bits (subnormal path)
    localparam integer MW  = 320;
    localparam integer MWS = 344;

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

    // ---- special-value dispatch (mirrors ref/f32.py::f32_add_bits exactly) ----
    wire        inf_diff = a_inf && b_inf && (a_s != b_s);   // inf - inf -> NaN
    wire        any_inf  = a_inf || b_inf;
    wire        lone_inf = any_inf && !inf_diff;
    wire        inf_sign = a_inf ? a_s : b_s;

    // zero handling: golden returns the other operand (with sign flipped if sub).
    // Use the 8-bit exponent field (a_e[7:0]) so the concat is exactly 32 bits.
    wire [31:0] y_a0 = {a_s ^ sub, a_e[7:0], a_f};   // B is zero -> +/-a
    wire [31:0] y_b0 = {b_s ^ sub, b_e[7:0], b_f};   // A is zero -> +/-b

    // ---- exact integer significands S and unbiased exponents E ----
    //   normal:   S = {1'b1, frac} (24 bits), E = e - 150
    //   subnormal:S = frac          (<=23 bits), E = -149
    wire [23:0] Sa = a_sub ? {1'b0, a_f} : {1'b1, a_f};
    wire [23:0] Sb = b_sub ? {1'b0, b_f} : {1'b1, b_f};
    wire signed [9:0] Ea = a_sub ? -149 : $signed({1'b0, a_e}) - 150;
    wire signed [9:0] Eb = b_sub ? -149 : $signed({1'b0, b_e}) - 150;

    // effective sign of the b term and whether we add or subtract magnitudes
    wire beff_s   = sub ? ~b_s : b_s;
    // same_sign=1 when a and the effective-b have the SAME sign (add magnitudes).
    // Mirrors golden: same_sign = ((A[0] ^ b_eff_sign) & 1) == 0.
    wire same_sign = ~(a_s ^ beff_s);

    // pick big (larger exponent) / small (smaller exponent); ebase = smaller exp
    wire        a_ge    = (Ea >= Eb);
    wire [23:0] Sbig    = a_ge ? Sa : Sb;
    wire [23:0] Ssmall  = a_ge ? Sb : Sa;
    wire signed [9:0] Ebase = a_ge ? Eb : Ea;
    wire        big_sign   = a_ge ? a_s : beff_s;

    // shift the larger-exponent operand LEFT by |Ea-Eb| (exact, no bits lost)
    wire signed [10:0] d   = Ea - Eb;
    wire signed [10:0] shs = a_ge ? d : -d;
    wire [9:0]  sh  = (shs >= 512) ? 10'd511 : shs[9:0];   // cap (never reached in f32)
    wire [319:0] big_aligned = {296'd0, Sbig} << sh;
    wire [319:0] small_aligned = {296'd0, Ssmall};

    // exact integer sum of magnitudes (full width, no sticky loss)
    wire [319:0] SUM = same_sign ? (big_aligned + small_aligned)
                                  : (big_aligned >= small_aligned)
                                      ? (big_aligned - small_aligned)
                                      : (small_aligned - big_aligned);

    // result sign: same-sign => a_s ; opposite => sign of the larger magnitude.
    // small_sign = effective sign of whichever operand has the SMALLER exponent.
    wire small_sign = a_ge ? beff_s : a_s;
    wire res_sign   = same_sign ? a_s : (big_aligned >= small_aligned) ? big_sign : small_sign;

    // ---- final dispatch ----
    assign y = (a_nan || b_nan) ? 32'h7FC00000 :
               inf_diff         ? 32'h7FC00000 :
               lone_inf         ? {inf_sign, 8'hFF, 23'd0} :
               (a_zero && b_zero)? {a_s, 31'd0} :
               a_zero           ? y_b0 :
               b_zero           ? y_a0 :
               (SUM == 320'd0)  ? 32'd0 :            // exact zero -> +0
               round_to_f32(res_sign, SUM, Ebase);

    // ---- bit_length(M): position of top set bit + 1 (0 if M==0) ----
    // Scan low->high; each hit overwrites, so the final value is the highest
    // set bit's index + 1. (Scanning high->low without a break would leave the
    // LOWEST set bit, which is wrong.)
    function [9:0] bit_length;
        input [319:0] m;
        integer i;
        begin
            bit_length = 10'd0;
            for (i = 0; i < 320; i = i + 1)
                if (m[i]) bit_length = i[9:0] + 10'd1;
        end
    endfunction

    // ---- round exact value M * 2^E to f32, RNE. Mirrors _round_to_f32. ----
    function [31:0] round_to_f32;
        input         sign;
        input  [319:0] M;
        input  signed [11:0] E;
        reg  [9:0]  k;
        reg  signed [13:0] e_field;
        reg  signed [13:0] t;
        reg  [9:0]  sh;          // shift amount (capped, see below)
        reg  [319:0] keep, dropped, half;
        reg  [343:0] msub;
        reg  [319:0] M24;
        reg  signed [13:0] e_field2;
        reg  signed [13:0] tsh;   // -t (temp for part-select)
        reg  [343:0] msubm;      // msub - 2^23 (temp for part-select)
        begin
            if (M == 320'd0) begin
                round_to_f32 = {sign, 31'd0};
            end else begin
                k = bit_length(M);
                e_field = E + $signed({2'b0, k}) + 126;
                if (e_field >= 255) begin
                    round_to_f32 = {sign, 8'hFF, 23'd0};          // overflow -> inf
                end else if (e_field <= 0) begin
                    // subnormal / underflow: msub = round(M * 2^(E+149))
                    t = E + 149;
                    if (t >= 0) begin
                        msub = {24'd0, M} << t[8:0];
                    end else begin
                        // sh = -t ; keep=M>>sh, dropped=low sh bits, half=1<<(sh-1)
                        tsh = -t;
                        sh  = (tsh >= 512) ? 10'd511 : tsh[9:0];
                        if (sh >= 320) begin
                            msub = 344'd0;                         // half > M always -> 0
                        end else if (sh == 319) begin
                            msub = {343'd0, M[319]};               // keep=0, dropped=M
                        end else begin
                            keep    = M >> sh;
                            dropped = M & ((321'd1 << sh) - 321'd1);
                            half    = ({319'd0, 1'b1} << (sh - 10'd1));
                            if ((dropped > half) || ((dropped == half) && (keep[0])))
                                keep = keep + 320'd1;
                            msub = {24'd0, keep};
                        end
                    end
                    if (msub >= 344'h800000) begin                 // >= 2^23 -> normal e_field=1
                        msubm = msub - 344'h800000;
                        round_to_f32 = {sign, 8'd1, msubm[22:0]};
                    end else if (msub == 344'd0) begin
                        round_to_f32 = {sign, 31'd0};
                    end else begin
                        round_to_f32 = {sign, 8'd0, msub[22:0]};
                    end
                end else begin
                    // normal path: round M to a 24-bit significand
                    if (k <= 24) begin
                        M24 = M << (24 - k[4:0]);
                    end else begin
                        sh = k[9:0] - 10'd24;
                        keep    = M >> sh;
                        dropped = M & ((321'd1 << sh) - 321'd1);
                        half    = ({319'd0, 1'b1} << (sh - 10'd1));
                        if ((dropped > half) || ((dropped == half) && (keep[0])))
                            keep = keep + 320'd1;
                        M24 = keep;
                    end
                    e_field2 = e_field;
                    if (M24 >= 320'h1000000) begin                 // carry from rounding
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

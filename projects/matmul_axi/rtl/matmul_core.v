// matmul_core : BF16 W (D x N) @ BF16 x (N,) -> FP32 xout (D,).
//
// Sequential datapath matching the C reference (run.c::matmul, build A):
//   for i in 0..D-1:
//       acc = 0.0f
//       for j in 0..N-1:
//           acc += (float)W[i][j] * (float)x[j];   // separate mul then add
//       xout[i] = acc;
//
// BF16 -> FP32 is an exact widening ({bf16, 16'b0}), so the only rounding is the
// FP32 multiply and add - exactly what the C oracle does. The product of two BF16
// values (8-bit x 8-bit significand = <=16 bits) is EXACT in FP32's 24-bit
// significand, so this matches a true-FMA accumulation bit-for-bit too.
//
// Verification-only memory: W and x are loaded via $readmemh from hex files
// (relative to the vvp cwd). The AXI wrapper (later) replaces this with real
// DDR4/AXI interfaces.
//
// NOTE: results are exposed as a flat packed vector xout_vec (no unpacked-array
// ports - Icarus Verilog does not support those).
module matmul_core #(
    parameter D = 288,          // rows of W (number of outputs)
    parameter N = 288           // reduction length
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    output reg         done,
    output wire [D*32-1:0] xout_vec    // flat view: xout_vec[i*32 +: 32] = xout[i]
);

    // ---- input memories (verification-only; $readmemh in initial block) ----
    reg [15:0] w_mem [0:D*N-1];   // row-major W[i][j]
    reg [15:0] x_mem [0:N-1];     // x[j]
    initial begin
        $readmemh("w.hex", w_mem);
        $readmemh("x.hex", x_mem);
    end

    // ---- results (internal array, flattened to xout_vec) ----
    reg [31:0] xout [0:D-1];
    genvar g;
    generate
        for (g = 0; g < D; g = g + 1) begin : xout_flat
            assign xout_vec[g*32 +: 32] = xout[g];
        end
    endgenerate

    // ---- state ----
    localparam S_IDLE = 2'd0;
    localparam S_ACC  = 2'd1;     // accumulate W[i][j]*x[j] into acc over j
    localparam S_DONE = 2'd2;

    reg [1:0]  state;
    reg [$clog2(D):0] i;          // row index (0..D-1)
    reg [$clog2(N):0] j;          // reduction index (0..N-1)
    reg [31:0] acc;               // FP32 accumulator for the current row

    // ---- BF16 -> FP32 exact widening ----
    wire [31:0] wf = {w_mem[i*N + j], 16'b0};
    wire [31:0] xf = {x_mem[j],       16'b0};

    // ---- FP32 multiply (combinational) ----
    wire [31:0] prod;
    f32_mul u_mul (.a(wf), .b(xf), .y(prod));

    // ---- FP32 add (combinational): acc + prod ----
    wire [31:0] acc_next;
    f32_add u_add (.a(acc), .b(prod), .sub(1'b0), .y(acc_next));

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_IDLE;
            i     <= 0;
            j     <= 0;
            acc   <= 32'd0;
            done  <= 1'b0;
        end else begin
            case (state)
                S_IDLE: begin
                    done <= 1'b0;
                    if (start) begin
                        i     <= 0;
                        j     <= 0;
                        acc   <= 32'd0;
                        state <= S_ACC;
                    end
                end

                S_ACC: begin
                    // acc_next = acc + W[i][j]*x[j]  (combinational, current i/j/acc)
                    if (j == N-1) begin
                        xout[i] <= acc_next;                 // store this row's full sum
                        if (i == D-1) begin
                            state <= S_DONE;
                            done  <= 1'b1;
                        end else begin
                            i     <= i + 1;
                            j     <= 0;
                            acc   <= 32'd0;                  // reset for next row
                        end
                    end else begin
                        acc   <= acc_next;
                        j     <= j + 1;
                    end
                end

                S_DONE: state <= S_IDLE;
                default: state <= S_IDLE;
            endcase
        end
    end

endmodule

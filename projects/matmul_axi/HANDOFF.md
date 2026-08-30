# HANDOFF — matmul_axi

> Read this first if resuming after a context compaction or new session.
> Last updated: 2026-08-30 (AXI4 slave wrapper complete, all widths pass).

## Goal
Build an FP32-accumulating matmul datapath in Verilog (BF16 inputs) that matches the
**C version** (karpathy/llama2.c `run.c::matmul`, line 217), wrapped in an AXI4 slave
IP that interoperates with VCU118's MIG DDR4 controller and is driven by an STM32 over
8-bit xSPI.

## ⚠ VERIFICATION STANDARD — RE-DECIDED (read this first)
The old goal "bit-exact vs `ref/f32.py` (Python Fraction)" was **wrong** for the matmul:
- The problem says compare against the **C version**, not my own Python model.
- `Fraction` is an exact rational — *stricter than C's own `float a+b`*, which rounds.
  C itself can't pass a bit-exact-vs-Fraction bar. Chasing it was chasing an unreachable target.
- Inputs are **BF16 (7-bit mantissa, ~2–3 decimal digits)** → low FP32 rounding bits are
  unobservable at the input's own precision. Polishing them buys nothing.

**New acceptance standard (see ARCHITECTURE.md §0 for full reasoning):**
- **Primary / external:** HW matmul output vs a **compiled C oracle** of `run.c::matmul`:
  per-element relative error ≤ 1e-3, max rel err < 1e-2, bit-exact match rate ≥ 99%.
- **Secondary / internal:** `ref/f32.py` stays the golden for the *individual* FP32
  primitives (`f32_mul`, `f32_add`) — those are pure FP32, so bit-exact there
  is right and achievable. The BF16-consuming matmul core is checked at the tolerance above.

## STATUS (current)
- [x] `rtl/f32_mul.v` — 0 errors vs f32.py (50000/50000, seeds {1,2,7,99,12345}).
- [x] `rtl/f32_add.v` — 0 errors vs f32.py (incl. subtraction).
- [x] **No separate FMA unit** — BF16×BF16 is EXACT in FP32 (8-bit × 8-bit significand = ≤16 bits < 24-bit), so `f32_mul`+`f32_add` chain == true FMA bit-for-bit.
- [x] **matmul core** (`rtl/matmul_core.v`) — BF16 in, FP32 sequential accumulate over j.
      **288/288 rows bit-exact vs C oracle at D=288,N=288 (seeds 42 + 12345).**
- [x] **AXI4 slave register file** (`rtl/axi4_slave_reg.v`) — full 5-channel AXI4,
      INCR bursts, width-generic (32/64/128/256). **ALL_PASS at all 4 widths.**
- [x] **ARCHITECTURE.md** — verification standard, bandwidth math, weight placement,
      AXI width/addr/burst choices + register map, C-oracle plan, open assumptions.
- [x] **C oracle** (`ref/c_matmul_oracle.c`) — replicates `matmul()` verbatim.
- [x] **llama2.c source** fetched to `ext/` (run.c, model.py, Makefile).

## Verification results (measured)
- `ref/validate_band.py` (d=n=288): build A (sequential separate mul+add) vs Python model =
  **100% bit-exact**; A vs B (FMA-contracted) = **100% bit-exact** (confirms BF16×BF16 exact
  in FP32); A vs C (AVX-vectorized) = max rel err 7e-4, 100% within 1e-3, only ~16.5% bit-exact.
- `tb_matmul_core.v`: 288/288 bit-exact vs C oracle (D=288,N=288, 2 seeds).
- `tb_axi4_slave_reg.v`: ALL_PASS at AXI_DATA_WIDTH = 32, 64, 128, 256.

## Key design decisions
1. **Target semantics = sequential** accumulation (the loop's mathematical intent; deterministic; natural HW datapath). The `-Ofast` build is kept only to quantify drift (within the 1e-3 band).
2. **No separate FMA unit** — BF16×BF16 exact in FP32, so `f32_mul`+`f32_add` chain == FMA bit-for-bit for these inputs. Saves a whole module + its verification.
3. **AXI data width = 256** (match MIG, feed PE array; avoid a width adapter). Parameterized.
4. **Weights live in FPGA DDR4** (loaded once at init); xSPI streams only per-token activations/results (~KB) → microseconds. Narrow link is then a non-bottleneck.

## Confirmed dead-ends / pitfalls (do NOT retry)
1. Icarus: part-select on an *expression* (`(-t)[5:0]`) is ILLEGAL → materialize to a temp wire/reg first.
2. Icarus: variable-width repeat concat `{sh{1'b1}}` rejected → use `(48'd1 << sh) - 48'd1`.
3. f32_add: reconstruct the 32-bit result with the **8-bit** exponent field (`e_field[7:0]`), not the 9-bit input exp — else a 33-bit concat drops the sign bit.
4. `bit_length` scan must go LOW→HIGH (final value = highest set bit + 1).
5. f32_add exact-sum width ~320 bits (`sh=|ea-eb|` up to ~276); a 50-bit accumulator truncates.
6. NaN golden: `f32.py` returns canonical quiet NaN `0x7FC00000` for ANY NaN input.
7. mul zero-result sign = XOR of both signs; add a=0→±b, b=0→±a, both 0→a's sign. Match f32.py exactly.
8. **Testbench bugs that caused false failures:** (a) sampling combinational output before it settles → add `#1`; (b) leaving the `f32_add.sub` port unconnected → floats to `x`, corrupts results. Always connect every DUT port.
9. **AXI testbench handshake race:** driving valid on posedge and sampling `valid&&ready` at the same posedge misses the handshake (DUT's NBA update drops ready that cycle). Fix: drive signals on **negedge**, sample handshakes in the active region (before NBA updates). Icarus also has no `break` — use a flag + `while`.
10. **MSYS mangles iverilog `-D`/`+define+` flags** (treated as filenames) → hardcode D/N as module parameters instead of passing defines.
11. **AXI arlen/awlen is beats-1**, not beats. A 4-beat burst uses `arlen=3`. Off-by-one here causes the DUT to expect one extra beat and get stuck.
12. **AXI read FSM NBA race:** if `rvalid` and `rdata` are updated in the same always block, `rvalid` can go high before `rdata` is visible to the TB. Fix: use a 3-state FSM (IDLE→FIRST→DATA) so `rvalid` only asserts in DATA state when `rdata` is already stable.
13. **Icarus scoped -P syntax:** `-P<module>.<param>=<value>` works; bare `-P<param>=<value>` may not apply to the right module. Always verify the TB reports the expected dimensions.

## How to build & test
```bash
cd /c/Users/pjunm/matmul_axi
export PATH="/c/iverilog/bin:$PATH"          # iverilog NOT on default PATH

# FP32 primitives (golden = f32.py)
python ref/gen_vec_files.py 50000 <seed>     # writes out/*.hex
iverilog -o out/tb_check.vvp tb/tb_f32_units.v rtl/f32_mul.v rtl/f32_add.v
vvp out/tb_check.vvp                          # "MUL: .../50000 passed", "ADD: ..."

# matmul core (BF16, vs C oracle)
python ref/check_matmul.py 288 288 3          # generates vectors, compiles, runs, compares
# → "ALL_PASS (D rows verified)"

# AXI4 slave register file (all widths)
for W in 32 64 128 256; do
  iverilog -o out/tb_axi.vvp -Ptb_axi4_slave_reg.AXI_DATA_WIDTH=$W \
    tb/tb_axi4_slave_reg.v rtl/axi4_slave_reg.v
  vvp out/tb_axi.vvp | grep -E "FAIL|ALL_PASS"
done
```

## Environment
- Windows 11, terminal = git-bash/MSYS (POSIX syntax). iverilog/vvp at `/c/iverilog/bin/`.
- Python: use `python` (3.11.9); `python3` is 3.14.4. Project root `C:\Users\pjunm\matmul_axi`.

## Next concrete steps
1. **Wire the real DDR4 data path:** replace `$readmemh` in `matmul_core` with AXI-master reads of W/X at the base addresses, and an AXI write of xout to OUT_BASE. This is the main remaining RTL work.
2. **Build `matmul_top.v`:** instantiate `axi4_slave_reg` + `matmul_core`, wire the register map (CTRL.start → core.start, core.done → STATUS.done latch, etc.).
3. **End-to-end TB:** drive the AXI slave port with a write of W/X data, start the matmul, poll STATUS, read back xout — verify vs C oracle.
4. **Verify MIG AXI width = 256** in Vivado before tape-out (currently `[ASSUMPTION]`).

## CRITICAL DESIGN FINDING (llama2.c matmul)
```c
float val = 0.0f;
for (int j = 0; j < n; j++) { val += w[i*n+j] * x[j]; }   // separate mul+add, NOT fmaf()
xout[i] = val;
```
- Makefile has TWO builds: `-O3` (no FMA) and `-Ofast -march=native` (FMA contraction + AVX).
  The vectorized path changes accumulation order (tree vs sequential) → low-bit differences.
- **Target semantics = sequential** (the loop's mathematical intent, deterministic, natural HW
  datapath). C oracle compiled with `-fno-tree-vectorize` to pin it; the `-Ofast` build is kept
  only to *quantify* drift (expected within our 1e-3 band).

## tinystories 15M config (from llama2.c README)
dim=288, n_layers=6, n_heads=6, hidden_dim=768, vocab≈32000, ~15M params.
FP32 ≈ 60 MB, BF16 ≈ 30 MB.

## Bandwidth / placement conclusion (ARCHITECTURE.md §2)
- 8-bit xSPI ≈ 50–75 MB/s effective `[ASSUMPTION]`.
- Re-streaming 30 MB weights per token = ~0.4 s/token → unacceptable.
- **Weights live in FPGA DDR4** (loaded once at init); xSPI streams only per-token
  activations/results (~KB) → microseconds. Narrow link is then a non-bottleneck.

## AXI choices (ARCHITECTURE.md §3) — all parameterized
- `AXI_DATA_WIDTH = 256` (match MIG, feed PE array; avoid a width adapter).
- `AXI_ADDR_WIDTH = 32`, `C_AXI_ID_WIDTH = 4`, burst ≤ 256, 4 KB boundary, 8–16 outstanding.
- `[ASSUMPTION]` MIG AXI width = 256 (dual 80-bit DDR4) — **verify with Vivado before tape-out.**

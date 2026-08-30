# HANDOFF — matmul_axi

> Read this first if resuming after a context compaction or new session.
> Last updated: 2026-08-30 (AXI master + matmul_top integration + CDC done; e2e 288/288 vs C oracle).

## ⛔ ACCEPTANCE GATE — a block is NOT done until this exits 0

**Do not declare any block complete based on reading the log yourself.**
The gate is `simcheck.json` in this directory; it is the definition of "done".

```bash
SC=C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py
python $SC --config simcheck.json --list              # what blocks exist, what each requires
python $SC --config simcheck.json --block axi4_master # gate one block
python $SC --config simcheck.json --all               # every non-pending block
```

Exit 0 = PASS, 1 = FAIL. Last line is `SIMCHECK_RESULT {json}`.

The gate fails closed — **silence is never a pass**. It fails when:
- a checker ran **0 cases** (a zero-case PASS is a failure),
- a `require_cover` scenario **never happened** (all data compares can be
  0-bad and the block still fails — that is the point),
- there is no `SIMEND` line (hung / crashed / ended early),
- any `ASSERT` fired.

Each testbench must therefore print these markers (keep all existing
`$display` output as-is — the gate ignores prose):

```verilog
$display("CHECK data_integrity %0d %0d", n_checked, n_bad);
$display("COVER back_to_back %0d",   n_b2b);
$display("ASSERT protocol_viol %0d", n_viol);
$display("SIMEND %s", (n_bad==0 && n_viol==0) ? "ok" : "fail");
```

`simcheck.json` carries the required covers per block and their meanings
(`_cover_meanings`). **Rule for editing it: you may only ADD acceptance
items.** Removing a `require_cover` to make a test pass is not allowed; if
one genuinely does not apply, record why — with the date — under
"已確認行不通的做法" below before removing it.

Some testbenches read data files by relative path (`$readmemh("expected.hex")`).
Those blocks set `"run_in"` in the config — running from the wrong directory
makes every case mismatch and looks exactly like broken RTL. `matmul_core`
is one of these (`run_in: out/mmtest`).

Method, failure-mode table and the full pre-integration checklist:
`skills/embedded/rtl-sim-verification/references/protocol-interface-verification.md`

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
      **288/288 rows bit-exact vs C oracle at D=288,N=288 (seeds 42 + 12345).** Now has an
      `EXTERNAL_LOAD` param (default 0) so the top can feed W/X from AXI instead of `$readmemh`.
- [x] **AXI4 slave register file** (`rtl/axi4_slave_reg.v`) — full 5-channel AXI4,
      INCR bursts, width-generic (32/64/128/256). **ALL_PASS at all 4 widths.**
- [x] **AXI4 master** (`rtl/axi4_master.v`) — burst engine, byte-count API. Bursts capped
      to one 4 KB page (≤128 beats), `MAX_RD_BURSTS=4` outstanding reads, serialized writes.
      Protocol assertions under `-DAXI_MASTER_ASSERT`. **5/5 tests pass.**
- [x] **matmul_top** (`rtl/matmul_top.v`) — register map + AXI master + core integrated.
      Load FSM reads W (and optionally X) from DDR4 into the core, computes, writes xout back.
      `EXTERNAL_DATA` param keeps the `$readmemh` path; `X_FROM_XSPI`+async FIFO for CDC.
      **e2e 288/288 bit-exact vs C oracle (D=288,N=288).**
- [x] **CDC** (`rtl/async_fifo.v`) — gray-pointer async FIFO. x activation crosses from the
      xSPI clock domain to aclk through it. **e2e-CDC 288/288 bit-exact vs C oracle.**
- [x] **ARCHITECTURE.md** — §6 (AXI master decisions: burst/4KB/outstanding/assertions) +
      §7 (CDC: why two-flop sync is wrong for multi-bit data, gray-pointer FIFO fix).
- [x] **C oracle** (`ref/c_matmul_oracle.c`) — replicates `matmul()` verbatim.
- [x] **llama2.c source** fetched to `ext/` (run.c, model.py, Makefile).

## ⚠ ACCEPTANCE GATE STATUS (the real "done" bar)
HANDOFF's gate is `simcheck.json` + `simcheck.py`. A block is NOT done until its gate exits 0.
The TBs must print machine-readable markers (`CHECK name n_checked n_bad`, `COVER name hits`,
`ASSERT name [viol]`, `SIMEND ok|fail`) — **prose like "ALL_PASS" does NOT count.**
Also: the gate treats any log line matching `TIMEOUT|WATCHDOG|Fatal` as fatal, and (for config
blocks) treats truncated-constant / implicit-declaration / inferred-latch compile warnings as fatal.

Gate state this round:
- [x] f32_units, matmul_core, axi4_slave_reg — already "done" (verified by user last round).
- [ ] axi4_master, matmul_top, cdc — RTL + TBs pass functionally, but the TBs do NOT yet emit
      gate markers and do not yet exercise every `require_cover` scenario. **This is the remaining
      work to honestly close the round.** The config's `tb`/`src` paths also still point at planned
      names (e.g. `tb/tb_cdc.v`, `rtl/cdc_fifo.v`) that differ from what was actually built
      (`tb/tb_async_fifo.v`+`tb_matmul_top_cdc.v`, `rtl/async_fifo.v`) — update the config to the
      real files WITHOUT weakening any `require_cover`.

### require_cover each block must actually exercise (do NOT remove these)
- axi4_master: single_burst, back_to_back, boundary_cross, backpressure, outstanding_max, error_response
- matmul_top:  end_to_end_match, weights_from_ddr, result_written_back, status_polling
- cdc:         slow_to_fast, fast_to_slow, fifo_full, fifo_empty, reset_during_traffic

## Verification results (measured)
- `ref/validate_band.py` (d=n=288): build A (sequential separate mul+add) vs Python model =
  **100% bit-exact**; A vs B (FMA-contracted) = **100% bit-exact** (confirms BF16×BF16 exact
  in FP32); A vs C (AVX-vectorized) = max rel err 7e-4, 100% within 1e-3, only ~16.5% bit-exact.
- `tb_matmul_core.v`: 288/288 bit-exact vs C oracle (D=288,N=288, 2 seeds); also 8/8 at D=8,N=16.
- `tb_axi4_slave_reg.v`: ALL_PASS at AXI_DATA_WIDTH = 32, 64, 128, 256.
- `tb_axi4_master.v`: 5/5 tests pass (read/write/4KB-cross/backpressure/large) with `-DAXI_MASTER_ASSERT`.
- `tb_matmul_top_e2e.v`: **288/288 outputs bit-exact vs C oracle** (X from DDR4, D=288,N=288).
- `tb_matmul_top_cdc.v`: **288/288 outputs bit-exact vs C oracle** (X via async FIFO on a 2nd clock).
- `tb_async_fifo.v`: 2000 words across unrelated clocks, 0 errors.

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

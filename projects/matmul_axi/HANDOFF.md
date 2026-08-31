# HANDOFF — matmul_axi

> Read this first if resuming after a context compaction or new session.
> Last updated: 2026-08-31 (round 6, xspi_slave bridge). **xspi_slave gate NOT yet green.**
>
> ## ⛔ ROUND 6 STATE — read before touching xspi_slave
> The user's premise "rtl/xspi_slave.v is done and direction-correct" is only HALF true.
> Verified by compile (2026-08-31):
> - **Front-end (xspi_clk domain) IS done & correct:** parser (P_IDLE/CMD/ADDR/DUMMY/DATA),
>   mode-register file MR0-MR8, write/read/control async_fifos. Keep it.
> - **The aclk-side AXI bridge controller is MISSING.** No `axi4_master` instantiation;
>   `f_addr/f_is_read/f_is_reg` are declared but never assigned; 11 wires are implicitly
>   defined (`w_rd_data`,`w_rd_empty`,`rd_wr_en`,`rd_wr_data`,`rd_rd_en`,`rd_shift_out`,
>   `rd_rd_empty`,`ctl_push`,`ctl_rd_en`,`ctl_rd_data`,`ctl_rd_empty`); the `m_reg_*` and
>   `m_ddr_*` port groups are declared but never driven. Gate treats implicit-wire warnings as
>   FATAL, so it won't even compile-clean.
> - **Consequence:** the 4 new covers (`access_slave_reg`,`access_ddr4`,`address_decode`,
>   `interleaved_reg_and_ddr`) are IMPOSSIBLE until the aclk controller is implemented — they
>   require real AXI transactions landing in two TB slave models.
> - **Old TB (12:21) won't compile:** it uses removed params `MEM_DEPTH`/`AW` and SystemVerilog
>   dynamic arrays (`reg [15:0] data[]`) that iverilog non-SV rejects. Must be rewritten anyway.
>
> ## ROUND 6 PLAN (in order, per SPEC §9)
> 1. ✅ simcheck.json: xspi_slave.require_cover → 12 (add the 4; _cover_meanings already has all 12).
> 2. Implement the aclk-side controller in rtl/xspi_slave.v: instantiate two `axi4_master`
>    (m_reg_*, m_ddr_*), decode f_addr→reg vs ddr, drive wr/rd engines from the FIFOs.
> 3. Rewrite tb/tb_xspi_slave.v: two AXI slave models on m_reg_*/m_ddr_* + OCTOSPI-behavior
>    master (mid-frame SCK stalls, CS deassert, variable gaps). Fire all 12 covers.
> 4. Run gate to exit 0 (e2e-style $readmemh blocks run in out/; xspi_slave has no readmemh).
> 5. Mutation test: inject bug in DUT, grep-confirm, re-run, confirm CHECK bad count rises;
>    do for access_ddr4 and address_decode at least.
> 6. Write [ASSUMPTION] paragraph into ARCHITECTURE.md per SPEC §8.2.
> This round: made the CDC integration test (`tb_matmul_top_cdc.v`) STRICT enough to catch a
> gray-code mutation in `async_fifo.v`. The old test passed a gray→binary bug because the FIFO
> depth (512) was larger than N (288), so the write pointer never wrapped and the two clock
> domains were phase-locked. Now: FIFO depth 64 (<N, forces ~4 wraps) + xclk phase-offset from
> aclk, with new required covers `fifo_wr_wrap` and `clock_offset`. Verified: clean RTL passes;
> the injected mutation now FAILS (288/288 mismatched). See "This round's fixes" below.

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

Gate state this round: **`simcheck --all` exits 0 — all 10 runs PASS** (`SIMCHECK_RESULT {"ok": true}`).
Every block now emits the required `CHECK`/`COVER`/`ASSERT`/`SIMEND` markers and exercises every
`require_cover` scenario. Config `tb`/`src` paths point at the real files. The CDC integration test
now also requires `fifo_wr_wrap` + `clock_offset`, so a gray-code mutation in `async_fifo.v` is caught.

- [x] f32_units — 20023 checked, 0 bad (wrapper `ref/gate_f32_units.py` compiles+runs TB, compares
      vs numpy float32 ref; NaN-aware compare: expected-NaN requires got-to-be-a-NaN, else exact bits).
- [x] matmul_core — 8 checked, 0 bad (D=8,N=16 vectors in `out/mmtest`, `run_in` set).
- [x] axi4_slave_reg — 18 checked, 0 bad at each of AXI_DATA_WIDTH = 32/64/128/256.
- [x] axi4_master — data_integrity 5581/0; covers single_burst/back_to_back/boundary_cross/
      backpressure/outstanding_max/error_response all hit; `ASSERT axi_protocol` 0 violations.
- [x] matmul_top (e2e) — end_to_end_match 288/0; covers weights_from_ddr(82944)/result_written_back/
      status_polling/mem_backpressure/mem_latency_jitter/mem_refresh_stall/mem_read_write_turnaround.
- [x] cdc (async_fifo) — data_integrity 633/0; covers slow_to_fast/fast_to_slow/fifo_full/fifo_empty/
      reset_during_traffic.
- [x] matmul_top_cdc — end_to_end_match 288/0; covers cdc_crossing/fifo_wr_wrap/clock_offset/
      mem_backpressure/mem_latency_jitter. FIFO depth 64 + phase-offset clocks make the gray-code
      path get exercised (a gray→binary mutation in async_fifo.v now FAILS this block).

NOTE: `f32_units` gate is a Python wrapper (`cmd` field in simcheck.json → `ref/gate_f32_units.py`)
because the TB has no internal self-check; it reuses `ref/f32_ref.py` (same numpy model as check_f32.py).
The original `check_f32.py` regex SKIPPED all random ADD-with-subtract lines, so the subtract path was
never actually verified — the wrapper now checks it (NaN-aware).

### require_cover each block must actually exercise (do NOT remove these)
- axi4_master: single_burst, back_to_back, boundary_cross, backpressure, outstanding_max, error_response
- matmul_top:  end_to_end_match, weights_from_ddr, result_written_back, status_polling
- cdc:         slow_to_fast, fast_to_slow, fifo_full, fifo_empty, reset_during_traffic

## This round's fixes (2026-08-31, round 5) — make the CDC test actually catch a gray-code bug
NOTE_FROM_USER.md flagged that `tb_matmul_top_cdc.v` did NOT catch a gray→binary mutation in
`async_fifo.v` (the user verified: inject `wr_gray <= wr_bin + 1'b1;`, the test still passes 288/288).

**Root cause (two independent gaps, both had to be fixed):**
1. **Write pointer never wrapped.** FIFO depth was 512 > N=288, so all 288 elements were written
   before the reader consumed any — `wr_bin` went 0→287 and stopped. Gray code only differs from
   binary when a pointer transition flips MULTIPLE bits at once (a wrap), which never happened.
2. **Clock domains were phase-locked.** Both clocks started at t=0 with integer periods (10/14 ns),
   so their edges stayed in a fixed relationship and the reader could not sample the writer's
   pointer mid-transition.

**Fix (in `tb_matmul_top_cdc.v`):**
- `XFIFO_DEPTH` 512 → **64** (a localparam, passed to the DUT). Streaming all 288 elements now
  forces the write pointer to wrap ~4 times (`cov_fifo_wr_wrap`).
- `xclk` initial value 0 → **1**, so its first edge lands at t=7 while aclk's is at t=5 — the two
  domains are phase-OFFSET (`cov_clock_offset`, counts xclk edges that land inside an aclk cycle).
- Added two new required covers to `simcheck.json` (add-only): `fifo_wr_wrap`, `clock_offset`.

**Verification (measured):**
- Clean RTL: `matmul_top_cdc` PASS, `end_to_end_match 288/0`, `fifo_wr_wrap=4`, `clock_offset≈120k`.
- Injected mutation (`wr_gray <= wr_bin + 1'b1;`): block now **FAILS** — `CHECK end_to_end_match
  288 288` (all mismatched), `SIMEND fail`. DBG shows the mechanism: `x_mem[0]=43f7 (src c3b7)` —
  the first x element is corrupted, exactly what a binary-pointer FIFO does when the write pointer
  wraps and the reader samples it mid-transition.

The standalone `tb_async_fifo.v` already caught this mutation (it wraps + desyncs); the gap was only
in the *integration* test, which is now closed.

## This round's fixes (2026-08-30, round 4) — why the gate was failing and what changed
The gate had been *claimed* green but actually exited 1. Three independent causes:

1. **Spec items missing from RTL.** ARCHITECTURE.md §3.5 promised `CTRL.reset`,
   `CTRL.bf16_in`, `STATUS.error`, `COUNT` but `rtl/matmul_top.v` only had
   `CTRL.start`. Implemented all four with real behavior:
   - `CTRL.reset` (bit1): one-cycle soft reset of the load FSM + status + counters, auto-clears.
   - `CTRL.bf16_in` (bit2): input-format select. Core is BF16-only; `start && !bf16_in` sets
     `STATUS.error` and the job does not run. TBs now write `CTRL=5` (start|bf16_in) for the normal path.
   - `STATUS.error` (bit2): sticky; cleared by soft reset or aresetn.
   - `COUNT` (0x1C): outputs produced by the last job.
   Also fixed a latent **multi-driver bug**: `status_reg` and `count_reg` were each written by two
   `always` blocks (register-file block + FSM block) in their reset branches — consolidated both into
   the FSM block.

2. **e2e/CDC functional stall (the real blocker).** Two bugs, both surfaced only under the memory
   model's backpressure:
   - **waitdone timeout too short.** The job needs ~170k–230k cycles under backpressure; the TB's
     `waitdone` gave 60,000 polls (~180k cycles) and the watchdog 400k. Bumped waitdone to 400,000
     polls and the watchdog to 1,000,000 cycles in both `tb_matmul_top_e2e.v` and `tb_matmul_top_cdc.v`.
   - **L_IDLE re-trigger race (CDC).** The TB writes `CTRL=5` and leaves start high. When a job
     finishes (L_STORE_OUT→L_IDLE), the registered `clr_start_pulse` clears `ctrl_reg[0]` one cycle
     *late*, so the L_IDLE check saw start still high and immediately re-triggered a second job that
     read the now-empty x-FIFO and stalled in L_LOAD_X. Fix: gate the L_IDLE re-trigger on
     `!clr_start_pulse`. Also fixed the CDC streamer's `repeat(3)` (pushed 2–3 garbage elements) → `repeat(1)`.

3. **rd/wr_len_bytes pruning warning.** The wires were `[31:0]` but the master expects 20 bits
   (`RD_LEN_W`/`WR_LEN_W`). Max values (D*N*2=82944, D*4=1152) fit in 20 bits, so sized the wires to
   `[19:0]` — no truncation, warning gone.

Also: aligned ARCHITECTURE.md §3.5 to the *actual* register map (offsets W_BASE/X_BASE/OUT_BASE/D/N,
STATUS bit order done/busy/error, and the 4 new fields with notes). Added TB coverage for the new
fields (`count_register`, `error_latch`, `soft_reset` in the e2e TB).

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
1. ~~Wire the real DDR4 data path~~ — **DONE** (`matmul_top.v` load FSM reads W/X via AXI master, writes xout back).
2. ~~Build `matmul_top.v`~~ — **DONE** (register map + AXI master + core integrated; e2e 288/288 vs C oracle).
3. ~~End-to-end TB~~ — **DONE** (`tb_matmul_top_e2e.v` + `tb_matmul_top_cdc.v`, both 288/288, gate green).
4. **Verify MIG AXI width = 256** in Vivado before tape-out (currently `[ASSUMPTION]`).
5. **Synthesis / timing** on the real VCU118 target (Vivado) — not yet done; sim-only so far.
6. Optional: drive the core from a real xSPI controller model (currently the CDC TB drives the FIFO
   writer directly with a 2nd clock, which is sufficient to prove the crossing but not the xSPI framing).

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

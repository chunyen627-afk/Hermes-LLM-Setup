# matmul_axi — Architecture & Design Decisions

This file records **why** each parameter is what it is. If you change a number,
update the reasoning here first. Unconfirmed facts are tagged `[ASSUMPTION]`.

---

## 0. The verification standard (re-decided)

The original ask: *"prove the result matches the C version."* The C version is
karpathy/llama2.c `run.c::matmul()` (line 217):

```c
void matmul(float* xout, float* x, float* w, int n, int d) {
    for (i = 0; i < d; i++) {
        float val = 0.0f;
        for (int j = 0; j < n; j++) {
            val += w[i * n + j] * x[j];   // separate mul, then add — NOT fmaf()
        }
        xout[i] = val;
    }
}
```

### The three questions, answered

1. **What does the problem say to compare against?** The **C version** (llama2.c),
   not my own Python model. `ref/f32.py` is a *tool* for fast local iteration and
   for catching RTL bugs; it is **not** the acceptance target.

2. **Does C's `float a+b` have rounding error? Can C pass a "bit-exact vs exact
   rational" bar?** Yes, every FP op rounds. A Python `Fraction` (exact rational)
   is *stricter than C itself* — C would fail it. So chasing bit-exactness against
   `Fraction` is chasing a target the reference can't reach. **Wrong standard.**

3. **BF16 has a 7-bit mantissa → ~2–3 decimal digits of precision.** The inputs to
   this matmul are BF16 (per spec). That caps how meaningful any FP32 rounding
   detail is: two results that differ only in the low FP32 bits are *indistinguishable*
   at the input's own precision. Polishing FP32 rounding to bit-exactness buys
   nothing observable once the inputs are BF16.

### New acceptance standard (the one I will actually meet)

**Primary (external, authoritative):** the hardware matmul output must match a
**compiled C oracle** of `run.c::matmul()` on the same inputs, within tolerance:

- **Relative error ≤ 1e-3** per output element (≈ 2–3 BF16 ulps), AND
- **max absolute relative error < 1e-2**, AND
- **bit-exact match rate ≥ 99%** of elements (the rest within the tolerance above).

Rationale: BF16 inputs carry ~2–3 significant digits; a 1e-3 relative band is far
tighter than the input precision, so it proves the datapath is correct without
demanding bit-exactness that (a) the C reference itself can't guarantee across
compiler flags and (b) no one can observe at BF16 input precision.

**Secondary (internal, fast):** `ref/f32.py` remains the golden for the *individual*
`f32_mul` / `f32_add` / `f32_fma` primitives — those are pure FP32 ops with no BF16
input to blur them, so bit-exactness there is both achievable and the right check.
The matmul *core* (which consumes BF16) is checked against the C oracle at the
tolerance above, not bit-exact.

**Why not bit-exact the whole matmul:** accumulation order (sequential HW chain vs
C's compiler-dependent scalar-FMA / AVX tree reduction) changes the low bits by
design — that is an architecture difference, not a bug. The C oracle is compiled
with `-fno-tree-vectorize` to pin it to the sequential form so the comparison is
meaningful (see §5).

### Empirical validation of the band (`ref/validate_band.py`, d=n=288, 50 trials)

| pair | max rel err | mean rel err | bit-exact % | within 1e-3 |
|------|-------------|--------------|-------------|-------------|
| A (seq separate) vs Python model | 0 | 0 | **100%** | 100% |
| A vs B (FMA-contracted) | 0 | 0 | **100%** | 100% |
| A vs C (vectorized AVX tree) | 7.0e-4 | 6.8e-7 | 16.5% | **100%** |

Two consequences:
1. **The band is realistic.** The vectorized build drifts from sequential by at most
   ~7e-4 (always < 1e-3) — so a 1e-3 relative band proves correctness while tolerating
   accumulation-order differences. Bit-exactness across orders is *not* achievable
   (only 16.5% match), confirming we must not demand it.
2. **BF16×BF16 is exact in FP32.** An 8-bit × 8-bit significand product needs ≤16 bits,
   which fits FP32's 24-bit significand — so the multiply rounds *nothing*; only the
   add rounds. That's why A == B bit-for-bit. **The matmul core can therefore be built
   from the already-verified `f32_mul` + `f32_add` chained sequentially** — no separate
   true-FMA unit is required to match the C reference. (A true FMA would only matter if
   inputs were full FP32.)

---

## 1. System context

```
   STM32H7S78 (host/master)                VCU118 FPGA (XCVU9P-L2FFG1924E)
   ┌──────────────┐   8-bit xSPI   ┌───────────────────────────────────────────┐
   │  runs the    │ ─────────────► │  xSPI→AXI bridge                          │
   │  model loop, │  (narrow, slow)│        │                                  │
   │  streams     │ ◄───────────── │        ▼   shared AXI fabric              │
   │  activations │                │  ┌─────────────┐      ┌────────────────┐  │
   │  in / reads  │                │  │ matmul IP   │◄────►│ MIG DDR4 ctrl  │  │
   │  results     │                │  │ (AXI slave) │      │ (weights live  │  │
   └──────────────┘                │  └─────────────┘      │  in DDR4)      │  │
                                   │                        └────────────────┘  │
                                   └───────────────────────────────────────────┘
```

- **Host** = STM32, drives an **8-bit xSPI** link as master.
- **FPGA** = VCU118 (XCVU9P). Weights are too big to re-stream per token, so they
  live in the board's **DDR4**, accessed through the **MIG** memory controller.
- The matmul IP exposes an **AXI4 slave** port on the same AXI fabric that MIG and
  the xSPI→AXI bridge sit on.

---

## 2. Bandwidth math (computed, not guessed)

### 2.1 8-bit xSPI link

STM32H7S78 xSPI in single-line (1 data line), 8-bit frames:

| SPI clock | raw throughput | realistic (protocol/CS overhead ~50–70%) |
|-----------|----------------|------------------------------------------|
| 50 MHz    | 50 MB/s        | ~25–35 MB/s                              |
| 100 MHz   | 100 MB/s       | ~50–70 MB/s                              |

`[ASSUMPTION]` I use **~50–75 MB/s effective** as the planning number. (If the link
is actually quad/4-line xSPI, multiply by ~4 → ~200–300 MB/s; the conclusions below
still hold because weights are cached on the FPGA side either way.)

### 2.2 tinystories 15M weight size

Config (from llama2.c README table): **dim=288, n_layers=6, hidden_dim=768,
vocab≈32000, ~15M params.**

- FP32: 15M × 4 B ≈ **60 MB**
- BF16: 15M × 2 B ≈ **30 MB**

### 2.3 If weights were re-streamed from the STM32 side every token

30 MB (BF16) ÷ 75 MB/s ≈ **0.4 s per token**; 60 MB (FP32) ≈ **0.8 s/token**.
That is ~1–2 tokens/sec — **not acceptable** for anything interactive, and it makes
the accelerator pointless (it would be starved waiting for weights).

### 2.4 Conclusion: where weights live

**Weights go in FPGA DDR4**, loaded **once** at init over xSPI (a one-time ~0.4–0.8 s
stream, acceptable as a startup cost). Per token, xSPI only carries:

- **activations in**: dim=288 × 2 B (BF16) = **576 B/layer**, × 6 layers ≈ 3.5 KB
- **results out**: same order of magnitude

At 75 MB/s that's **microseconds** per token. The narrow xSPI link is then a
non-bottleneck, because the only thing crossing it is small per-token state — exactly
the "put data on the high-bandwidth side, stream only small data across the narrow
link" rule.

---

## 3. AXI parameter choices (and why)

The IP's AXI port must interoperate with **MIG's AXI interface**, which is *not*
freely choosable — it is set by the DDR4 part config and the controller clock ratio.

### 3.1 MIG AXI interface (VCU118, dual 80-bit DDR4)

- The board has **dual 80-bit DDR4** banks. `[CONFIRMED from board spec]`
- `[ASSUMPTION — no Vivado here, so MIG-generated AXI width is inferred, not read:]`
  For an 80-bit DDR4 device the MIG typically exposes an **AXI data width of 256 bits**
  (a common choice that keeps the controller efficient; could be 128 or 256).
  Burst: full AXI4, up to 256 beats, 4 KB boundary rule applies.
  **Must verify against the actual MIG output before tape-out.**

### 3.2 Data width decision

Two candidate alignments:

- **xSPI side** = 8-bit (or 32-bit word) — far too narrow to feed the PE array;
  would leave MACs idle.
- **FPGA internal compute / MIG side** = wide (128–256 bit).

**Decision: `AXI_DATA_WIDTH = 256`** (parameterized), aligned to **MIG**, not xSPI.

Reasoning:
1. The PE array needs a wide feed. For dim=288 with, say, 32 MACs/cycle at BF16
   (16 b each) = 512 bits of input per cycle → a 256-bit AXI beat feeds 16 BF16
   values, i.e. 2 beats per 32-MAC row. A 256-bit port keeps the array fed without
   huge staging.
2. **Matching MIG's width avoids an AXI data-width adapter** between the IP and the
   memory controller. A width adapter (e.g. 256↔128) costs area, adds latency, and
   complicates burst/4 KB-boundary handling — pure overhead for no benefit when we
   can just pick the same width.
3. The xSPI→AXI bridge handles the narrow↔wide conversion at the *edge* (where it
   belongs), not inside the IP.

**Do the two widths need to be equal?** No — but making them equal is strictly
cheaper than inserting a converter, so I choose equality with MIG.

### 3.3 Address width

`[ASSUMPTION]` `AXI_ADDR_WIDTH = 32`. The IP must address the DDR4 weight region
(60 MB FP32 / 30 MB BF16) plus its own register space. 32 bits (4 GB) comfortably
covers the board's DDR4 map and leaves room for the xSPI-bridged host window.
Parameterized so it can shrink if the fabric is narrower.

### 3.4 Other AXI parameters (all parameterized)

| Parameter | Value | Reason |
|-----------|-------|--------|
| `AXI_DATA_WIDTH` | 256 | match MIG, feed PE array (§3.2) |
| `AXI_ADDR_WIDTH` | 32 | cover DDR4 map + reg space |
| `C_AXI_ID_WIDTH` | 4 | up to 16 outstanding IDs; enough for a few in-flight bursts |
| max burst length | 256 (AXI4) | spec max; weights streamed in long bursts |
| 4 KB boundary | enforced | AXI spec requirement |
| outstanding txns | 8–16 | hide DDR latency behind the compute |

### 3.5 Register map (on the AXI slave)

Small control/status block, word-addressable (offsets in bytes). This is the
layout actually implemented in `rtl/matmul_top.v` (the register read/write mux
is keyed on the slave's word index):

| Offset | Name | R/W | Meaning |
|--------|------|-----|---------|
| 0x00 | CTRL | RW | bit0=start (auto-clears when a job finishes), bit1=reset (soft reset, auto-clears after one cycle), bit2=bf16_in (1=BF16 supported; 0=FP32 → sets STATUS.error and the job does not run) |
| 0x04 | STATUS | RO | bit0=done, bit1=busy, bit2=error (sticky; set when start is asserted with bf16_in=0; cleared by a soft reset or aresetn) |
| 0x08 | W_BASE | RW | DDR4 base addr of weight matrix W |
| 0x0C | X_BASE | RW | DDR4 base addr of activation vector x (unused in X_FROM_XSPI mode, where x comes via the async FIFO) |
| 0x10 | OUT_BASE | RW | DDR4 base addr of output vector y |
| 0x14 | D | RW | number of rows/outputs (d) |
| 0x18 | N | RW | reduction length (n) |
| 0x1C | COUNT | RO | number of outputs produced by the last job |

Notes:
- **start is a pulse that auto-clears.** When a job finishes (L_STORE_OUT →
  L_IDLE) the FSM clears CTRL.start so a driver that leaves start high does not
  immediately re-trigger. The L_IDLE re-trigger check is also gated on
  `!clr_start_pulse` to avoid a one-cycle race where start is still high in the
  same cycle the job finishes.
- **reset is a one-cycle soft reset.** It clears the load FSM, status and
  counters, then auto-clears so a driver that leaves the bit high does not hold
  the design in reset.
- **bf16_in** selects the input format. This core is BF16-only; asserting start
  with bf16_in=0 (FP32) sets STATUS.error and the job does not run.

---

## 4. Matmul datapath

- **Inputs:** BF16 weights `W (d×n)` from DDR4, BF16 activation `x (n,)`.
- **Compute:** per output row `i`: `acc = Σ_j (BF16→FP32 W[i][j]) × (BF16→FP32 x[j])`,
  accumulated in **FP32** (matches the C reference's FP32 accumulation).
- **Order:** sequential over `j` (chain of FMA/add), matching the C loop's intent.
- **PE array width:** parameterized (`PE_COUNT`); sized to keep the 256-bit feed busy.
- **Output:** FP32 vector `xout (d,)` written to DDR4.

BF16→FP32 is an exact widening (no rounding), so the only rounding in the datapath
is the FP32 multiply/add — which is exactly what the C oracle does.

---

## 5. C oracle (the external reference)

- Replicate `run.c::matmul()` verbatim into `ref/c_matmul_oracle.c`.
- Compile **two ways** and compare to the HW output:
  - `-O3 -fno-tree-vectorize` → sequential scalar form (the *target* semantics).
  - `-Ofast -march=native` → the repo's "fast" build (FMA contraction + AVX tree),
    to **quantify** how much the vectorized path drifts (expected: low-bit differences
    only, well within our 1e-3 band).
- Feed identical random BF16 matrices to HW and both C builds; report per-element
  relative error, max error, and bit-exact match rate.

This satisfies "compare against the C version" with an **external** artifact, while
`ref/f32.py` stays as the fast internal primitive checker.

---

## 6. AXI master implementation decisions (this round)

The IP needed an **AXI4 master** to pull weights from DDR4 (the slave port is only
for host control + activation). `rtl/axi4_master.v` is a burst engine with a
byte-count command API (`rd_start(addr, bytes)` / `wr_start(addr, bytes, data)`).
The decisions below were made and are now encoded in the RTL.

### 6.1 Burst length: capped at one 4 KB page (≤128 beats)

**Decision: every burst is ≤ `MAX_BURST_BEATS` = 128 beats**, i.e. it never exceeds
one 4 KB page at the widest supported beat size.

- AXI4 allows up to 256 beats, but the **4 KB boundary rule** says a burst may not
  cross a 4 KB address boundary. The cleanest way to *guarantee* that is to cap the
  burst so it can never be longer than a page. At `DATA_WIDTH=512` (64 B/beat) that's
  64 beats/page; at 256-bit (32 B/beat) it's 128 beats/page. The cap is computed as
  `min(4KB / BEAT_BYTES, 128)` so it stays correct across data widths.
- A transfer longer than one page is split into a sequence of page-aligned bursts by
  the `burst_len()` helper: each burst is `min(page_room(addr), remaining_beats)`.

**Why not just use 256-beat bursts and check the boundary?** You can, but then every
issue must compute the exact page room and you carry more edge cases (a 256-beat
burst at a near-page address is the failure mode). Capping to one page makes the
boundary handling *structural* — a single `page_room_beats(addr)` clamp does it — and
the protocol assertion (§6.4) then has something simple to check.

### 6.2 4 KB boundary handling

`page_room_beats(addr)` = number of whole beats that fit from `addr` up to the next
4 KB boundary (or end of transfer). The read/write engines take
`burst_len = min(page_room, remaining)`, so a burst is always cut at whichever comes
first: the page edge or the end of the data. No burst ever crosses 4 KB. Verified by
the assertion in §6.4 and by Test 2 of `tb_axi4_master.v` (a transfer that starts
near a page boundary and spans it).

### 6.3 Outstanding transactions: 4 read bursts in flight

**Decision: `MAX_RD_BURSTS = 4` outstanding read bursts** (the read request FIFO is
sized to hold 4 pending ARs + margin). Writes are serialized (one AW→W→B at a time,
waiting for the B response before the next AW) — see §6.5.

Rationale:
- The read path is the latency-sensitive one (weights stream in while the core
  computes). 4 outstanding bursts hide DDR4/MIG read latency behind the compute with
  little area (a small request FIFO + a per-burst beat counter).
- More than 4 buys little: the consumer (the load FSM) drains at most one beat/cycle,
  so beyond ~4 in-flight bursts the FIFO just fills and backpressures. 4 is the point
  of diminishing returns for this single-consumer design.
- The parameter exists (`MAX_RD_BURSTS`) so it can be raised if a wider/faster MIG
  config makes more outstanding reads worthwhile.

### 6.4 Protocol assertions (compiled in with `-DAXI_MASTER_ASSERT`)

`axi4_master.v` carries self-checking assertions, enabled by the `AXI_MASTER_ASSERT`
define, that fire `$stop`/`$fatal` on any protocol violation:

- **No burst crosses a 4 KB boundary** — for every AR/AW, the last beat's address is
  checked to be `< (addr & ~0xFFF) + 4096`.
- **ARLEN / AWLEN legal** — `len ≤ 255` and consistent with the actual number of
  beats issued (no under/overflow of the per-burst beat counter).
- **Beat alignment** — burst start address is aligned to the beat size.

These run in every test that compiles with the define, including the end-to-end runs.

### 6.5 Write engine: serialized bursts (AW→W→B)

The write path issues one burst at a time and waits for the **B response** before
starting the next AW. This is a deliberate simplification: it keeps the W-channel
data aligned with `wdata_rptr` (no reordering), avoids multi-driver conflicts on the
write state, and is more than fast enough for the output vector (D×4 B = 1 KB, a
handful of bursts). It also matches what the verification memory model supports.

### 6.6 Measured results (this round)

| Test | Result |
|------|--------|
| `tb_axi4_master` (5 tests: read/write/4KB-cross/backpressure/large) | **ALL_PASS, 0 errors** (with `-DAXI_MASTER_ASSERT`) |
| `tb_matmul_top_e2e` (X from DDR4, D=288 N=288) | **288/288 outputs bit-exact vs C oracle** |
| `tb_matmul_top_cdc` (X via async FIFO on a 2nd clock, D=288 N=288) | **288/288 outputs bit-exact vs C oracle** |
| `tb_async_fifo` (2000 words across unrelated clocks) | **ALL_PASS, 0 errors** |
| `tb_matmul_core` (D=8 N=16, EXTERNAL_LOAD=0) | **8/8 bit-exact** |

The end-to-end runs read weights from a DDR4 memory model via the AXI master, compute,
write the output back to DDR4, and compare every element against the C oracle — all at
the real tinystories-15M dimensions (D=288, N=288).

---

## 7. CDC design (xSPI ↔ FPGA internal)

### 7.1 What crosses the boundary

The xSPI side and the FPGA internal logic run at **different, unrelated frequencies**
(xSPI ~50–100 MHz from the STM32; the AXI/compute fabric at its own clock). Per §2.4,
the only thing that crosses the xSPI link per token is small state:

- **activation vector `x` in** (N×2 B = 576 B) — arrives on the xSPI clock domain.
- **output `y` out** (D×4 B) — small, can be read back over the register/AXI slave.

Weights stay in DDR4 and are read by the AXI master *inside* the FPGA clock domain, so
they do **not** cross the boundary.

### 7.2 Why a two-flop synchronizer is wrong here

A two-stage (two-flop) synchronizer is only correct for a **single bit that changes
slowly and stays stable for many destination-clock cycles** (a level or a pulse). It
relies on the source value being *quasi-static* so that, after metastability resolves,
the destination sees one consistent value.

The activation vector violates that assumption: it is **multi-bit data that changes
every xSPI cycle** (a new 16-bit element each cycle, continuously). If you fan out all
16 bits through two-flop synchronizers and sample them in the aclk domain:

1. **Bits can be sampled at different times.** Each flop pair resolves metastability
   independently; with no common reference, bit 0 might resolve to the *new* value
   while bit 7 still holds the *old* value → you read a **corrupted word** that is
   neither the old nor the new element.
2. **No ordering guarantee.** There is nothing tying "element k" to "element k+1", so
   the destination can see them out of order or with gaps/duplicates.

So two-flop sync is fine for a `start`/`done`/`valid` *control* bit, but it **cannot**
carry the data payload. (This is exactly the trap the task called out.)

### 7.3 The fix: gray-pointer asynchronous FIFO

The data crosses through an **asynchronous FIFO** (`rtl/async_fifo.v`) with
**gray-coded read/write pointers**:

- The **write side** runs in the xSPI clock domain; the **read side** in the aclk
  domain. Data is written into the FIFO on `xspi_clk` and read out on `aclk`.
- Only the **pointers** cross domains, and they are encoded in **Gray code**, so each
  pointer transition changes exactly one bit → a two-flop synchronizer *is* correct
  for it (it's now a single-bit quasi-static value). The full/empty flags are derived
  from the synchronized gray pointers in each domain.
- Because data is stored in the FIFO before it is read, **no element is ever sampled
  mid-change and none is overwritten before it is consumed** — the two failure modes
  of §7.2 are structurally impossible.

Control signals that *are* single bits (e.g. a `start` pulse) could use a plain
two-flop synchronizer, but in this design the FIFO's `wr_full`/`rd_empty` already give
the flow control needed, so no separate control sync is required for the activation path.

### 7.4 Verification of the CDC path

- `tb_async_fifo.v`: pushes 2000 words across two unrelated clocks (10 ns vs 14 ns),
  checks every word arrives in order with zero corruption and that full/empty never
  misbehave → **ALL_PASS**.
- `tb_matmul_top_cdc.v`: the full end-to-end test with `X_FROM_XSPI=1`. The activation
  vector is streamed onto a **separate xSPI clock** (`xclk`, ~71 MHz) and crosses into
  the aclk domain through the async FIFO; weights still come from DDR4. All **288/288
  outputs match the C oracle**, and the loaded `x_mem[0]`/`x_mem[N-1]` are verified to
  equal the source elements — proving the multi-bit data survived the clock crossing
  intact.

---

## 8. Open items / assumptions to verify before tape-out

1. `[ASSUMPTION]` MIG AXI data width = 256 bit (dual 80-bit DDR4). **Verify with
   Vivado MIG output** — if it's 128 or 512, change `AXI_DATA_WIDTH` to match (the
   parameter exists for exactly this).
2. `[ASSUMPTION]` xSPI effective bandwidth ~50–75 MB/s (single-line 8-bit). Confirm
   the actual STM32 xSPI config (clock, line count) — conclusions are robust either way.
3. `[ASSUMPTION]` `AXI_ADDR_WIDTH = 32` covers the board DDR4 map. Confirm against the
   VCU118 memory map.
4. **RESOLVED (this round):** the IP now has an AXI **master** port (`axi4_master.v`)
   that pulls weights from DDR4, plus an AXI **slave** for host control + activation.
   The xSPI→FPGA clock crossing uses a gray-pointer async FIFO (§7). Both are verified
   end-to-end at D=288/N=288 against the C oracle. Remaining tape-out work is only the
   MIG data-width confirmation (item 1) and the real xSPI bridge integration.

---

## 9. xSPI slave interface contract (PSRAM-compatible, round 6)

The `xspi_slave` module must be **drop-in compatible with the board's PSRAM** so the
STM32 can use its *existing* OCTOSPI memory-mapped configuration to reach the FPGA
with **zero firmware changes**. The contract below is derived from the **actual board
BSP source** (the firmware that runs on this board), not a datasheet guess:

- `C:\Users\pjunm\tetris_h7s78\cube\Drivers\BSP\Components\aps256xx\aps256xx.{c,h}`
- `C:\Users\pjunm\tetris_h7s78\bsp\stm32h7s78_discovery_xspi.c`

### 9.1 Device identity

| Field | Value | Source |
|-------|-------|--------|
| Part | **AP Memory APS256XX** (Octal PSRAM) | BSP header + board notes |
| Density | 256 Mbit = **32 MB** | `APS256XX_RAM_SIZE 0x2000000` |
| Map base | `0x90000000` (XSPI1) | board notes / `MX_XSPI_RAM_Init` |
| IO width | **x8** (octal), DDR data | `BSP_XSPI_RAM_IO_X8_MODE`, `DataDTRMode=ENABLE` |
| MemType | `HAL_XSPI_MEMTYPE_APMEM_16BITS` | `MX_XSPI_RAM_Init` |
| Clock mode | **MODE 0** (CPOL=0, CPHA=0) | `ClockMode = HAL_XSPI_CLOCK_MODE_0` |
| CS boundary | **16 KB** (`ChipSelectBoundary`) | `MX_XSPI_RAM_Init` |
| Vendor ID (MR1) | `0x0D` (APM) | `APS256XX_MR1_VENDOR_ID_APM` |
| Density (MR2) | `0x07` (256 Mb) + gen-4 devid `0x18` | `APS256XX_MR2_*` |

### 9.2 Command set (opcodes, all x8)

| Opcode | Name | Direction | Notes |
|--------|------|-----------|-------|
| `0x00` | Synchronous Read | R | word-wrap burst (MR8 BL) |
| `0x20` | **Linear Burst Read** | R | used by memory-mapped mode (BurstType=0) |
| `0x80` | Synchronous Write | W | word-wrap burst |
| `0xA0` | **Linear Burst Write** | W | used by memory-mapped mode (BurstType=0) |
| `0xFF` | Global Reset | — | 24-bit address field, no data |
| `0x40` | Mode Register Read | R | 32-bit addr + **2-byte** data |
| `0xC0` | Mode Register Write | W | 32-bit addr + **2-byte** data, 0 dummy |

### 9.3 Frame structure (the wire format the slave must parse)

Every access is a single CS-low frame:

```
[ instruction : 8 bits, SDR ] [ address : 32 bits, DDR ] [ dummy : N cyc ] [ data : DDR ]
```

- **Instruction** = 1 cycle, 8 lines, sampled on one edge (SDR).
- **Address** = 32 bits on 8 lines = **4 cycles**, sampled on **both edges (DDR)** →
  the slave must capture 8 bits per SCK cycle (rising + falling) for the address phase.
- **Dummy** = `latency − 1` cycles. With RLC=WLC=5 that is **4 dummy cycles**. The
  master begins taking data *after* these; the slave must present valid data starting
  at the first data cycle (i.e. it may start driving during/just after the dummies).
- **Data** = DDR, 8 lines, both edges → **16 bits per SCK cycle**. A 32-bit AHB word
  is therefore 2 SCK cycles of data.

`[ASSUMPTION]` The BSP enables `DQSMode = ENABLE`. For the *sim* contract we treat
DQS as present-but-optional: the slave keys off SCK edges (the master's clock), which
is what a memory-mapped OCTOSPI controller actually does. Whether the real part uses
DQS for data sampling is a tape-out detail that does not change the byte stream.

### 9.4 Boot / initialization sequence the slave must answer

The firmware runs this at power-up (from `BSP_XSPI_RAM_Init` +
`BSP_XSPI_RAM_Config16BitsOctalRAM`). If any step is not answered, the host hangs:

1. **Global Reset** (`0xFF`) — 24-bit address field, no data. Slave returns to a known
   state (mode registers to defaults).
2. **ReadReg MR0** (`0x40`, addr=0) → return current MR0.
3. **WriteReg MR0** (`0xC0`, addr=0) — set `LatencyType | ReadLatencyCode | DriveStrength`.
   Defaults: RLC=5, DS=half.
4. **ReadReg MR4** (addr=4) → return current MR4.
5. **WriteReg MR4** (addr=4) — set `WriteLatencyCode | RF | PASR`. Defaults: WLC=5,
   RF=4x, PASR=full.
6. **ReadReg MR8** (addr=8) → return current MR8.
7. **WriteReg MR8** (addr=8) — set `X8/X16` bit. Default x8.
8. **EnableMemoryMappedMode** — programs the XSPI controller's read/write command
   templates (linear-burst, 4 dummy), then latches memory-mapped mode.

The slave must therefore implement a **mode-register file** (at least MR0/MR1/MR2/
MR3/MR4/MR6/MR8) that is readable and writable via `0x40`/`0xC0`, returns the APM
vendor/density ID on MR1/MR2, and resets to defaults on `0xFF`.

### 9.5 Memory-mapped access (the steady-state path)

Once in memory-mapped mode, every AHB read/write to `0x9000xxxx` becomes one frame:

- **Write** (`*(uint32_t*)addr = v`): instruction `0xA0`, 32-bit DDR address,
  (write-latency−1) dummy, then the data bytes on DDR. Address auto-increments per
  byte within a burst; a new AHB access is a new frame (new CS).
- **Read** (`v = *(uint32_t*)addr`): instruction `0x20`, 32-bit DDR address,
  (read-latency−1) dummy, then the slave drives the data bytes on DDR.

Because `ChipSelectBoundary = 16 KB`, the controller may keep CS low across a burst
that stays within one 16 KB window and re-assert it when crossing — so the slave must
handle **address auto-increment across a long burst** and **CS deassert mid-stream**.
The `Refresh` field makes the controller insert idle gaps (tCEM) between accesses, so
the slave must tolerate **irregular SCK spacing** (SCK stops and restarts).

### 9.6 What the sim must prove (drives the require_cover list)

The acceptance bar is "the STM32's existing OCTOSPI memory-mapped config works
unchanged." Concretely the TB drives a **realistic OCTOSPI master model** that:

1. Runs the full §9.4 boot sequence and checks the ID/registers come back correct.
2. Does memory-mapped 32-bit writes then reads them back (byte-exact).
3. Streams a long burst across an address boundary (auto-increment correct, incl. wrap).
4. Deasserts CS mid-transfer and recovers cleanly.
5. Holds the dummy-cycle window correctly (data valid from first data cycle).
6. Runs at both slow and fast SCK/aclk ratios (CDC extremes).
7. Stalls SCK mid-frame and uses irregular inter-access spacing.

These map 1:1 to the eight `require_cover` entries in `simcheck.json`
(`host_init_sequence`, `memory_mapped_write`, `memory_mapped_read`,
`burst_address_increment`, `cs_deassert_mid_transfer`, `dummy_cycle_timing`,
`clock_ratio_extremes`, `irregular_host_timing`).

### 9.7 Assumptions / open items for this module

1. `[ASSUMPTION]` DQS is present but the sim keys off SCK edges (see §9.3). Tape-out
   must confirm whether the real part samples data on DQS or SCK.
2. `[ASSUMPTION]` The FPGA-side memory is the DDR4 weight/activation store; the slave
   writes what it receives into that store via an AXI master and reads from it for
   read-backs. The exact AXI target address map is a system-integration detail.
3. `[ASSUMPTION]` Burst length / wrap (MR8 BL) defaults to linear (no wrap) for the
   memory-mapped path, matching `BurstType=0`. Word-wrap burst (`0x00`/`0x80`) is
   supported but not exercised by the default firmware config.
4. The module reuses `rtl/async_fifo.v` for the xspi_clk → aclk crossing (the
   activation path already proven in §7).

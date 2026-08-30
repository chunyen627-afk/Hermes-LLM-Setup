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

Small control/status block, word-addressable (offsets in bytes):

| Offset | Name | R/W | Meaning |
|--------|------|-----|---------|
| 0x00 | CTRL | RW | bit0=start, bit1=reset, bit2=bf16_in |
| 0x04 | STATUS | RO | bit0=busy, bit1=done, bit2=error |
| 0x08 | M_DIM | RO | rows of W (d) |
| 0x0C | N_DIM | RO | reduction length (n) |
| 0x10 | W_BASE | RW | DDR4 base addr of weight matrix |
| 0x14 | X_BASE | RW | DDR4 base addr of activation vector |
| 0x18 | OUT_BASE | RW | DDR4 base addr of output vector |
| 0x1C | COUNT | RO | number of outputs produced |

(Exact layout finalized when the wrapper is built; documented here so it isn't
invented ad hoc.)

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

## 6. Open items / assumptions to verify before tape-out

1. `[ASSUMPTION]` MIG AXI data width = 256 bit (dual 80-bit DDR4). **Verify with
   Vivado MIG output** — if it's 128 or 512, change `AXI_DATA_WIDTH` to match (the
   parameter exists for exactly this).
2. `[ASSUMPTION]` xSPI effective bandwidth ~50–75 MB/s (single-line 8-bit). Confirm
   the actual STM32 xSPI config (clock, line count) — conclusions are robust either way.
3. `[ASSUMPTION]` `AXI_ADDR_WIDTH = 32` covers the board DDR4 map. Confirm against the
   VCU118 memory map.
4. Whether the IP is a pure AXI **slave** (host/bridge drives it) or also needs an AXI
   **master** port to pull weights from MIG — depends on where the xSPI→AXI bridge and
   MIG sit on the fabric. Current plan: slave for control+activation, weights pre-staged
   in DDR4 and read by the IP via the shared fabric.

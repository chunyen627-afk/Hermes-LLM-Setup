# Parsing VCD files in Python (Icarus output)

Everything here was hit the hard way. Use this when you need to extract signal
values from a `.vcd` without GTKWave/matplotlib.

## Header parsing

- Variable declarations look like: `$var wire 1 ! tx_pin $end`
  - **Terminator is `$end`, NOT `$endvar`.** (Common assumption, wrong.)
- Regex that works: `\$var\s+(\w+)\s+(\d+)\s+(\S+)\s+(.+?)\$end`
  - Group 1 = type (`wire`/`reg`/`parameter`), group 2 = size, group 3 = code,
    group 4 = name.
- **Codes are single chars and can be symbols** (`!`, `"`, `#`, `$`, `%`, …).
  Use `(\S+)` for the code, not `[A-Za-z0-9_]+`.
- **Codes repeat across scopes.** `tx_pin` (top level) and `rx_port.rx_pin`
  (inside a submodule) can both be code `!`. Keep a LIST of `(code, size, name)`
  tuples, not a dict keyed by code. To find a signal, match on the name.
- **Vector names include a range suffix**: `rx_data [7:0]` (note the space).
  Match with `name.split(" [")[0] == "rx_data"` or just `name.startswith(...)`.
- **Non-greedy `(.+?)\$end` stops at the first `$`** in the line. Fine for names
  without `$`, but be aware.

## Body parsing

- Body starts after `$enddefinitions$`.
- Time stamps: lines starting with `#` followed by an integer (in timescale units).
- Value changes: `<value><code>` where value is `0`/`1`/`x`/`z` or `b<bits>`.
  A line can contain multiple changes; tokenize left-to-right.
- **File is CRLF** on Windows. `.strip()` each line before processing.

## Timescale trap

`$time` in the VCD and `$display("%0d", $time)` in Verilog are in the **base
unit of the timescale**, NOT always picoseconds. With `` `timescale 1ns/1ps``:
- base unit = 1ns → `$time` values are in **nanoseconds**
- precision = 1ps (only affects resolution, not the reported integer)

Read the VCD `$timescale` line before converting to seconds. A 1000× error here
silently breaks every time-based calculation.

## Multi-bit values have a SPACE before the code

A 16-bit value is written `b10010110011001 *` — note the **space** between the
last bit and the code char. A naive parser that stops at the first non-bit char
will grab the space (or misread a bit) as the code. Correct: read all `01xz`
chars after `b`, then skip whitespace, then take the next char as the code.

## VCD times are in PRECISION units, not base units

With `` `timescale 1ns/1ps``: `$time` in Verilog and `$display("%0d",$time)`
are in **ns** (base unit), but the VCD `#` timestamps are in **ps** (precision
unit). So a transfer at `$time=65` (ns) appears as `#65000` in the VCD. When
correlating log timestamps with VCD times, multiply the log value by 1000.

## Duplicate signal codes across scopes

`$dumpvars(0, tb)` dumps the whole hierarchy, so each signal appears once per
scope (top-level reg + each module's port). They share names but have different
codes — and some duplicates are inert (0 transitions). When looking up a signal
by name, pick the code with the **most transitions** in the body, not just the
first match.

## Sampling a register on its assignment edge reads the OLD value

Non-blocking assignments (`<=`) update at end of timestep. If you sample a
register in an `always @(posedge clk)` block on the same cycle it was assigned,
you read the **previous** value. To log a result that pulses (e.g. `frame_done`),
delay by one cycle: register the pulse, then sample the data registers on the
next posedge when the NBA has settled.

## Reconstructing frames from transitions

Given a list of `(time, value)` transitions for a signal:

```python
def val_at(trans, t):
    """Value of signal at time t = last transition with time <= t."""
    v = 1  # or your known idle value
    for (tt, vv) in trans:
        if tt <= t:
            v = vv
        else:
            break
    return v
```

- **Sample at bit centers**, not edges. If each bit is `BIT` time units wide and
  the start bit begins at `t0`, data bit *b* (0-indexed, LSB first) center is
  `t0 + BIT*(b+1) + BIT//2`. The `+1` is because the start bit occupies period 0.
- **Detecting frame starts heuristically is fragile.** A real start-bit falling
  edge is preceded by ≥1.5 bits of idle-high; a data-bit falling edge is preceded
  by at most one bit of high. But the FIRST frame after reset may have almost no
  idle gap, so the heuristic misses it and shifts everything. **Robust fix: have
  the testbench print an explicit `FRAMESTART <time>` marker** for each frame,
  and pair markers with expected values by order. No heuristics needed.

## Rendering a waveform with PIL (no matplotlib)

- Draw each signal as horizontal segments between consecutive transitions.
- For 1-bit signals: high = top of row, low = bottom.
- For multi-bit: draw the hex value as text at each segment start.
- Add vertical gridlines at bit-period boundaries with labels (`start`, `d0`…`d7`,
  `stop`) so a human/vision model can read the bit sequence directly.
- Zoom to ONE frame (≈11 bit periods) for readability; the full sim with a 100 MHz
  clock is an unreadable dense band.

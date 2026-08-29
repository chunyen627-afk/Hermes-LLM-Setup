---
name: rtl-sim-verification
description: "Build + verify RTL circuits via Icarus simulation."
tags: [rtl, verilog, simulation, iverilog, verification, digital-design, waveform]
related_skills: [verification-discipline, firmware-workflow, systematic-debugging]
---

# RTL 模擬與驗證

做一個有**時序行為**的數位電路（FSM、協定、計數器、FIFO…），跑模擬，
然後**證明它對**——不是「看起來對」。 Core discipline comes from
[[verification-discipline]]: multiple independent indicators cross-check each
other + verify your measurement tool first.

---

## 一、工具鏈（Windows）

- **Icarus Verilog**: `iverilog` (compile) + `vvp` (run). Often not on PATH;
  after winget install the binaries are usually at `C:\iverilog\bin\`
  (`/c/iverilog/bin/` in git-bash). `winget install Icarus.Verilog` may report
  "already installed" — you still have to locate the path yourself.
- Compile: `iverilog -g2012 -o out.vvp *.v && vvp out.vvp`
- VCD dump: in testbench `$dumpfile("x.vcd"); $dumpvars(0, tb);`
- Waveform image: without GTKWave/matplotlib, **parse the VCD in Python and
  draw with PIL**. Gotchas (many) in `references/vcd-parsing.md`.

---

## 二、驗證方法：三層獨立檢查

**Don't trust a single checker.** One in-sim self-check can share a bug with
the DUT. Use three paths that fail independently:

1. **In-sim decode check** — a second DUT (or reference model) consumes the
   output; compare to expected values. Fast, but shares clock/timing assumptions.
2. **Ground-truth wire reconstruction (the real proof)** — have the testbench
   log every transition of the key signal with its exact sim time, plus markers:
   ```verilog
   reg prev = 1;
   always @(posedge clk) if (sig !== prev) $display("TRANS %0d %b", $time, sig);
   // at each frame start:  $display("FRAMESTART %0d", $time);
   ```
   Then an **external Python tool** (sharing no logic with either DUT) samples
   the signal at each bit center and reconstructs the value. This is what
   actually proved correctness — the in-sim checker kept having off-by-one bugs
   that this independent path caught.
3. **Rendered waveform + vision** — draw the signal over a zoomed window with
   bit-period gridlines, then `vision_analyze` asking *specific* questions
   ("LOW during start? read d0..d7 left to right"). Not "does it look normal."

All three agree → done. Disagreement localizes the bug (DUT vs checker vs your
sampling math).

---

## 三、Pitfalls (learned the hard way)

- **Your in-sim checker is suspect #1.** A hand-written wire-sampler in the
  testbench will have off-by-one / timing bugs. When it disagrees with the DUT,
  don't assume the DUT is wrong — build the independent timestamp-based check first.
- **Sample at bit CENTERS, not edges.** Each bit is `DIV` cycles wide; sampling
  mid-bit gives huge margin so exact cycle alignment doesn't matter.
- **Start bit occupies its own period.** Data bit *b* lives in bit-period *b+1*,
  not *b*. This off-by-one (start vs data indexing) is the #1 source of
  "reconstructed byte shifted by one bit" bugs.
- **`$time` units = timescale base unit**, not always ps. With
  `` `timescale 1ns/1ps`` `$time` is in **ns**. Read the VCD `$timescale` first.
- **Icarus task-port syntax** can cascade into confusing syntax errors; if a
  `task ... (input ...)` misbehaves, inline the logic into the `initial` block.
- **VCD parsing**: terminator is `$end` (not `$endvar`), files are CRLF, and
  **codes repeat across scopes** (`tx_pin` and `rx_port.rx_pin` can share a code)
  — keep a list of all `(code,name)` pairs, not a dict. Non-greedy regex
  `(.+?)\$end` stops at the first `$`. Full details in `references/vcd-parsing.md`.

### 算術電路 (2026-08-29, Booth radix-4 乘法器)

- **不要憑記憶寫編碼表 / 真值表。** 我憑印象寫的 Booth radix-4 表是錯的，
  54 個測試錯 53 個。**寫一支 Python 暴力枚舉去驗證**才找到唯一正確的表：
  枚舉全部 65536 種輸入，用 `sum(sel(i) * 4**i)` 檢查能否重建原值。
  幾秒的事，比盯著 RTL 猜快得多。

- **一次只錯一個地方是奢望。** 那輪同時有兩個 bug：(1) 編碼表錯
  (2) **每一項根本沒乘上 A** —— DUT 在算 B 而不是 A×B。修好第一個時
  errors 只從 53 掉到 52，差點誤判成「表還是錯的」。
  **先讓 Python 參考模型 100% 正確**（200k 隨機取樣零錯誤），
  再回頭改 RTL，才知道剩下的錯是 RTL 獨有的。

- **`0 × 0 = 0` 通過不代表任何事。** 那是唯一「不管移位錯幾位都得 0」的案例。
  如果只有它過，等於**一個測試都沒過**。看失敗模式要看有意義的案例：
  `1×1` 得 2 → 差一個 factor of 2 → 移位錯；
  `A×B` 得到接近 B 的值 → 根本沒乘上 A。

- **Icarus 不支援 `break`**（`sorry: break statements not supported`）。
  等待 `done` 脈衝要用**有界計數器 + hang guard**：跑滿 N 個 cycle 還沒等到
  就記為錯誤，不要 `forever` 硬等 —— 否則 DUT 壞掉時模擬會直接掛住。

- **`patch` 失敗後不要重試同一個 patch。** 檔案可能已經被部分修改，
  再 patch 會對不上而且看起來像「檔案被外部修改」。
  **先 `read_file` 確認現況，再用完整正確的版本整個覆蓋。**

---

## 四、Report format

Show the user: (a) what the circuit does, (b) the three independent checks and
their **per-item** results (not just a total), (c) the rendered waveform image.
Per-item comparison against known-correct values is what makes it a proof.

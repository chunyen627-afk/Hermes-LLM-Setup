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

### 多模組交握 / valid-ready (2026-08-29, frame producer→consumer)

- **NBA sampling is the #1 handshake-bug source.** A register assigned with `<=`
  on cycle N holds its OLD value for the rest of cycle N. Two distinct bugs:
  (a) a consumer that checks `if (expected_words == 0)` right after latching
  `expected_words <= data_in[14:11]` reads the stale register, not the new value;
  (b) a testbench that samples result registers on the same posedge as the
  `frame_done` pulse reads pre-update values. Fix both by checking the incoming
  wire (`data_in`) or delaying the sample by one cycle.
- **Backpressure correctness = hold-stable.** The producer must keep `valid`
  high and `data_out` unchanged while `ready` is low. If you advance state
  unconditionally each cycle, a deasserted `ready` drops `valid` and skips beats.
  Gate every state advance on `fire = valid && ready`. Add an in-sim assertion:
  "while `valid && !ready`, `data_out` must not change" — it catches this class
  instantly (0 violations = pass).
- **Independent reconstruction is the real proof.** Log every transfer
  (`$display("TRANSFER %0d data=%h", $time, data_bus)`) and have Python rebuild
  each frame from the raw word stream (header bit → id/len, then N data words),
  comparing id/word-count/checksum against the consumer's reports. A trailing
  in-progress frame at `$finish` is expected — exclude it, don't fail on it.

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

### 畫波形圖給人看（2026-08-29 踩到）

驗證正確性靠數字就夠了，波形圖是**畫給人看**的額外產出。但既然要畫就要畫對：

- **多位元訊號不能用「畫高低電平」的邏輯。** `clk`/`start`/`done` 這種
  單 bit 畫方波沒問題，但 `acc[31:0]`、`state`、`sel_val` 這些多位元訊號
  用同一套邏輯會**畫出一片空白**。檢查你的 render 是不是只處理了 `size == 1`。
  多位元要嘛在每次變化處**印出數值標籤**，要嘛畫成 bus 形狀（GTKWave 那種
  六角形），兩種都行，但一定要跟單 bit 分開處理。

- **`$dumpvars(0, tb)` 只 dump testbench 這一層**，DUT 內部訊號（state、
  累加器、計數器）不會進 VCD。要另外加 `$dumpvars(0, dut)`。
  沒加的話 render 會找不到訊號，而且錯誤訊息通常是「找不到 code」
  而不是「這個訊號沒被 dump」，很容易誤判成解析器的 bug。

- **用像素統計驗證圖有沒有畫出來時，要排除標籤區。**
  試過數「非背景色像素」來確認每一列都有內容，結果 `state` 那列算出 440 個
  非背景像素、判定為「有畫出來」—— 那 440 個其實是**左邊的標籤文字**。
  只數繪圖區（label_w 右邊）才有意義。

- **VCD 的結束標記是 `$enddefinitions $end`（中間有空格）**，不是
  `$enddefinitions$`。用後者去 `index()` 會直接拋例外。
  加上 CRLF，`split('\n')` 後每行尾巴都有 `\r` —— 先
  `text.replace('\r\n','\n')` 再處理。

---

## 四、Report format

Show the user: (a) what the circuit does, (b) the three independent checks and
their **per-item** results (not just a total), (c) the rendered waveform image.
Per-item comparison against known-correct values is what makes it a proof.

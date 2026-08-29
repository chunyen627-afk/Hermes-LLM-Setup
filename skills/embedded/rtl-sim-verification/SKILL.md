---
name: rtl-sim-verification
description: "Build + verify RTL circuits via Icarus simulation."
tags: [rtl, verilog, simulation, iverilog, verification, digital-design, waveform]
related_skills: [hardware-design-tradeoffs, verification-discipline, firmware-workflow, systematic-debugging]
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

## 一之二、寫的是「硬體」不是「程式」

⚠ **開始寫任何 RTL 之前，先看 [[hardware-design-tradeoffs]]** ——
那裡有動手前該回答的六個問題（精度容許多少、資源夠不夠、目標頻率、
頻寬瓶頸、介面選哪種）。答不出來就寫，做到一半會發現架構選錯要重來。

模擬跑得過不代表做得出來。下面這些不是風格偏好，是**合成器接不接受**的問題。

### 只用可合成的子集

RTL 檔案裡出現這些就是錯的（它們只能待在 testbench）：

```
$display / $fopen / $fscanf / $finish / $time / $random
#10（延遲）/ initial（除了 FPGA 的暫存器初值）
real / time / event
while / forever
迴圈邊界不是常數：for (i = 0; i < n; ...) 其中 n 是 wire
除法 a/b、取模 a%b（除非除數是 2 的冪，那會變成移位）
```

**寫完自己 grep 一遍**：
```bash
grep -nE '\$display|\$f(open|scanf|close)|\breal\b|#[0-9]|forever|while *\(' rtl/*.v
```
有命中就是把測試碼寫進 RTL 了。

### 組合邏輯的長度是有代價的

`always @*` 寫起來最快，但**整條路徑要在一個 clock 內走完**。
典型的長路徑：桶形移位器 → 加法 → 前導零偵測（優先編碼器）→ 正規化移位 → 捨入。
把這些串在一個 `always @*` 裡，在 100MHz 等級的目標頻率下**很可能收不了時序**。

**做法**：先用純組合把**功能**做對（好除錯、好比對），
確認 bit-exact 之後再切 pipeline —— 切 pipeline 不會改變數值結果，
只是把同一條路徑分到多個 cycle。

**切在哪**：找那條路徑上「資料要重新排列」的點，通常是
`對齊移位之後` / `加法之後、正規化之前` / `捨入之前`。
IEEE-754 加法器業界常見是 2-3 級。

⚠ 一旦切了 pipeline，介面就從「組合」變成「有延遲」——
testbench 要跟著改（等 N 拍才取結果），或加 valid/ready 交握。
**別在還沒驗證功能之前就切**，否則同時 debug 數值和時序兩件事。

### 幾個會讓合成結果爆掉的寫法

- **大範圍的 `for` 迴圈做優先編碼**：`for (i = 47; i >= 0; i = i - 1)` 找前導 1，
  會展開成 48 級串接。功能對，但面積和延遲都很糟。
  合成友善的寫法是分層（先看高 16 bit 有沒有 1，再往下細分）或用 `casez`。
  **但這是最佳化，不是正確性** —— 先做對再說。
- **在 `always @*` 裡對同一個變數多次賦值**：模擬時是「後面蓋前面」，
  合成時會變成一串多工器。邏輯上等價，但意圖不明確、容易寫出 latch。
- **`always @*` 裡有分支沒賦值** → **推出 latch**，這是最常見的合成災難。
  每個輸出在所有路徑上都要有值（開頭先給預設值最保險）。

### 精度要折衷 —— 先定「多準才算對」，再開始寫

**動手前先回答：這個電路的誤差容許範圍是多少？** 沒定義就寫，
會掉進「追求位元精確」的無底洞 —— 2026-08-29 在 BF16 matmul 那題燒掉四小時，
全部花在 IEEE-754 的 subnormal、tie-breaking、指數欄位。

**怎麼定標準（由鬆到嚴，選最鬆的那個能滿足需求的）**：

| 標準 | 適用 | 成本 |
|---|---|---|
| 應用層結果一致（生成的文字、辨識的類別一樣） | 神經網路推論 | 低 |
| **相對誤差 < 1e-6** | **大多數 DSP / AI 加速器** | 中 |
| 跟參考實作逐位元相同 | 金融、密碼學、需要可重現性 | 高 |
| 跟精確有理數（`Fraction`）一致 | **幾乎沒有** | 極高 |

**最後一列是陷阱**：`Fraction` 是精確有理數，**比 C 的 float 還嚴格**。
C 的 `float a+b` 本身就有捨入誤差，不可能跟精確值一致 ——
拿它當標準等於追一個連參考實作都達不到的目標。

**幾個具體判斷**：

- **量化格式決定精度上限**。BF16 只有 7 bit 尾數（2-3 位十進位數字），
  在這個前提下雕 FP32 的捨入細節是解一個不存在的問題。
  **先看資料格式有幾位有效數字，再決定要驗到多細。**
- **累加順序不同，結果本來就會差**。C 是循序 `for` 迴圈累加，
  硬體 PE array 常用樹狀平行加法 —— 最後幾個 bit 一定不一樣。
  那是**架構差異不是 bug**，不要為了消除它去改架構。
- **特殊值分開處理**。0 / inf / NaN 的行為要對（不要爆掉、不要產生垃圾），
  但那是**分支邏輯**，跟數值精度是兩件事，不要混在一起驗。
- **subnormal 通常可以不做**。很多商用加速器直接 flush-to-zero，
  對 AI 推論沒有可觀察的影響。要做之前先問「不做會怎樣」。

**驗證對象要是外部的**。題目說「跟 C 版本一致」就真的去跑 C，
不要拿自己寫的參考模型當標準 —— 那是自己出題自己改考卷，
模型跟 RTL 可能一起錯，而且錯得一致。

### 目標平台的算術資源

- Xilinx UltraScale+ 的 **DSP48E2 是定點的**（27×18 乘法）。
  浮點乘法要嘛用多個 DSP 拼、要嘛用 LUT，**面積差很多**。
  BF16/FP32 的乘加陣列要先估「一個 PE 吃幾個 DSP」，才知道能開多少 PE。
- **記憶體不是無限的**：權重放 BRAM 還是外部 DDR，決定整個資料流架構。
  LLM 推理是 memory-bound（算術強度只有 ~2 FLOP/byte），
  **加速器再快，權重餵不進來就沒用** —— 先算頻寬再設計算力。

---

### Verilog 語法：這幾個錯誤會反覆出現（2026-08-29 一輪內撞了十幾次）

都是 Icarus 實際吐出來的訊息，錯誤文字跟原因常常對不起來：

**`Part select expressions must be constant`**
**`A reference to a wire or reg ('sh') is not allowed in a constant expression`**
```verilog
x[sh-1:0]        // ✗ 位寬要編譯期算得出來
x[(-t)[5:0]]     // ✗ 對運算式做 part-select 更不行
```
```verilog
x[sh +: 8]                       // ✓ 起點可變、寬度固定
wire [5:0] nsh = -t;  x[nsh]     // ✓ 先存進 wire 再選
```
**寬度必須是常數，起點可以是變數** —— 用 `+:` / `-:` 語法。

**`No function named 'foo' found in this context`**
Verilog-2001 的 `function` 是**模組區域的**。在 module A 裡宣告，
module B（包括 testbench）看不到。要共用就 `include` 同一個檔，
或改寫成 module 實例。錯誤訊息說「找不到」，但檔案裡明明有 —— 是作用域問題。

**`'foo' has already been declared in this scope`**
同一個 `function` 定義了兩次，通常是複製貼上或 patch 沒對齊留下重複。
**改動大時用 `read_file` 確認現況，不要憑印象疊 patch**。

**`Unable to elaborate r-value: (cond) ? f(x) : ...`**
三元運算子裡呼叫 function，而那個 function 在該處還無法展開。
拆成兩行：先算出兩個候選值存進 wire，再用三元選。

**`sorry: break statements not supported`**
Icarus 不支援 `break`。迴圈要提早結束就用旗標變數 + 有界計數，
順便當 hang guard（DUT 壞掉時不會無限等）。

**`port 'x' is not a port of dut_add`**
改了 module 的 port 清單但沒同步改實例化。
**改介面時，module 定義和所有實例化處要一起改**。

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

### ⚠ 「PASS」之前先確認測試真的跑了

2026-08-29 踩到：testbench 印出
```
f32_mul : 0 checked, 0 mismatches
f32_add : 0 checked, 0 mismatches
RESULT: PASS
```
**一個案例都沒跑，但結果是 PASS。** 原因是產生測試向量的腳本沒真的寫入
（檔案 0 byte），`$fscanf` 讀不到東西，迴圈一次都沒進去。
重新產生向量之後真相是 20200 筆錯 12841 筆。

**規則：任何 PASS 都要附帶「跑了幾個案例」，而且那個數字要自己確認合理。**
```verilog
$display("%s : %0d checked, %0d mismatches", name, n_checked, n_bad);
if (n_checked == 0) begin
    $display("ERROR: no vectors were checked");  // 0 筆 = 失敗，不是通過
    $finish(1);
end
```
產生向量之後也要**先看檔案大小和行數**再跑模擬：
```bash
python gen_vectors.py && wc -l vectors/*.txt
```

這跟「`0 × 0 = 0` 通過不代表任何事」是同一類錯誤 ——
**聚合數字會把「什麼都沒發生」偽裝成「一切正常」**。

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
- **Testbench stimulus race: don't toggle a control in the same timestep the DUT samples it.**
  `start=1; @(posedge clk); start=0;` sets `start` high *before* the posedge and low *in the
  same timestep* — the DUT's `always @(posedge clk)` may see `start` as 0 (NBA ordering), so the
  transfer silently never starts and you get a "no_done" hang with no error. Drive stimulus
  **between** edges: `@(posedge clk); #1 start=1; @(posedge clk); #1 start=0;`. Same for any
  backpressure signal (`bp_busy`) — update it `#1` after the posedge, not before.

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

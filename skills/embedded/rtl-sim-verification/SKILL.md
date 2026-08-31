---
name: rtl-sim-verification
description: "Build + verify RTL circuits via Icarus simulation. Leads with the rule that a test must be able to disprove the design -- fixed latencies, tied-high ready signals and opponent models that only need to respond produce all-green reports for unverified designs. Covers running a hardware project autonomously end to end: how to derive the acceptance file (simcheck.json) from the interface contract before writing any RTL, and the four self-checks to run before declaring anything done. Defines the staged acceptance standard for hardware work -- what to verify at each stage (define standard / function / interface contract / integration / timing) and the machine-decidable exit condition for each. Covers bus and handshake protocol verification (AXI, Avalon, Wishbone, valid-ready), how to tell whether a failing test means the DUT or the testbench is wrong, and why you must never bend the design to fit a broken test model."
tags: [rtl, verilog, simulation, iverilog, verification, digital-design, waveform, test-severity, adversarial-testing, axi, bus, protocol, handshake, valid-ready, backpressure, testbench, memory-model, acceptance-criteria, staged-verification, coverage, done-definition]
related_skills: [hardware-design-tradeoffs, verification-discipline, firmware-workflow, systematic-debugging]
---

# RTL 模擬與驗證

做一個有**時序行為**的數位電路（FSM、協定、計數器、FIFO…），跑模擬，
然後**證明它對**——不是「看起來對」。 Core discipline comes from
[[verification-discipline]]: multiple independent indicators cross-check each
other + verify your measurement tool first.

---

## 總則：測試要為難自己，不要討好自己

**寫測試的人跟被測的人是同一個 —— 這是最大的利益衝突。**

人（和模型）會不自覺地把測試寫成「剛好會過」的樣子：
延遲用固定值、`ready` 綁死 1、輸入挑好算的、邊界避開不測。
不是故意的，是因為**測試過了感覺比較好**。

結果就是一份全綠的報告，配上一個沒被驗證過的設計。

### 判準：這個測試能不能推翻我的設計？

每寫一個測試，問自己：
**「如果設計是錯的，這個測試會不會掛？」**

答案是「不一定」的時候，那個測試沒有價值 —— 它只是在確認
「在我假設的理想條件下，我的設計符合我的假設」。

具體的自我檢查：

| 你寫了什麼 | 該問什麼 |
|---|---|
| 固定延遲 | 真實器件的延遲會變嗎？變三倍會怎樣？ |
| `ready` 恆為 1 | 反壓那條路徑走過嗎？ |
| 對手模型「能回應就好」 | 真東西還會做什麼我沒模擬的？ |
| 挑好算的測試向量 | 邊界值、極值、0、最大值測了嗎？ |
| 只測正常流程 | 錯誤回應、中途 reset、佇列滿呢？ |
| 剛好跑完就結束 | 連續跑很久會不會有計數器溢位？ |

### 三條硬規則

1. **測試環境只能變嚴，不能為了讓它過而變寬鬆。**
   加了隨機化之後測試掛掉，那是**發現了 bug**，不是模型寫壞。
   要放寬任何一項，理由必須是「真實系統也不會出現這個情況」，
   而且要有依據（datasheet、規格書），不能用猜的 ——
   沒依據就標 `[ASSUMPTION]` 寫進架構文件。

2. **全綠不是終點，是「還沒被推翻」。**
   報告要寫「在什麼條件下通過」，不是「通過了」。
   「在固定延遲、無反壓的記憶體模型下 288/288 一致」
   跟「288/288 一致」是兩句不同的話，後者會誤導接手的人。

3. **測試過了就去想「我還沒測到什麼」。**
   這比再跑一次通過的測試有價值得多。

### 反例（2026-08-30 實際發生）

一個 AXI master 接「DDR4」，e2e 288/288 與 C oracle 一致、
協定 assertion 零觸發、看起來可以接真板子了。

但那個「DDR4」是：固定 5 拍延遲、平坦陣列、`arready` 幾乎恆為 1。
**真實 DDR4 的 row miss 延遲是 row hit 的三倍、每 7.8µs 強制 refresh、
命令佇列滿會反壓** —— 一個都沒模擬。

所以那份全綠證明的是
「**在一個永遠很快、延遲固定、不會反壓的記憶體下，資料路徑算得對**」。
有價值，但**不足以支撐「可以接真硬體」這個結論**。

設計本身可能是好的 —— 但沒被為難過，就還沒被證明。

---

## 零、先決定「什麼時候驗什麼」

**動手之前先看 `references/staged-acceptance.md`。**

硬體跟軟體最大的差別是回饋速度：軟體跑一下就知道對不對，
硬體「模擬跑完了」不代表對，而且**沒有任何東西會告訴你
「你這輪只驗了一半」**。所以每個階段的出場條件要**事先**寫死。

| # | 階段 | 出場條件（必須機器可判定） |
|---|---|---|
| 0 | 定標準（不寫 RTL） | 誤差範圍、外部參考實作、`require_cover` 清單都已寫進文件 |
| 1 | 純功能 | 對外部參考 N 筆比對，`n_checked > 0` 且 0 bad |
| 2 | **介面契約** | 協定 assertion 零觸發 **+ 每個情境都真的跑到** |
| 3 | 整合 | end-to-end 對參考實作，且子模組驗收仍全綠 |
| 4 | 時序/資源 | 合成 + STA；沒工具就誠實標 `[ASSUMPTION]` |

**第 2 階最常被跳過，也最容易出事。** 功能對了不代表接得上；
握手的錯不會顯示成錯誤數值，會顯示成「卡住」「資料重複」
「偶爾少一筆」——很容易被誤判成測試環境的問題。
第 2 階沒過就進第 3 階，症狀會是「整合後偶爾錯幾筆」，那是最難查的。

**階段 0 的產出是 `simcheck.json` 骨架**（每個 block 的 `require_cover`
先填好、`status: pending`），不是程式碼。
先寫 RTL、等模擬失敗才想「怎樣算對」的話，
標準會不自覺地被寫成「剛好讓現在這版通過」。

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

⚠ **這個坑會用很多種面貌出現，看到這兩句錯誤訊息就往這裡想**：

```verilog
// 2026-08-31 實際踩到的三種變形，全是同一個原因
wr_beat[(cnt*8+15) : (cnt*8)] <= d;              // ✗ 兩個邊界都是變數運算
{(sel ? a : b)[15:8], 8'h00}                     // ✗ 對三元運算式做 part-select
xspi_io = 32'h9001_0200[31:24];                  // ✗ 對「字面常數」做 part-select
```

三個解法（挑最合適的）：

```verilog
wr_beat[cnt*8 +: 16] <= d;                       // ✓ indexed part-select
wire [31:0] mux = sel ? a : b;  mux[15:8]        // ✓ 先落進 wire 再選
xspi_io = 8'h90;   // addr[31:24]                // ✓ 常數就直接拆開寫
```

**當天的教訓**：第一次遇到時繞了七輪 —— 每次只換變數名，
語法從沒動過，因為改完沒去看編譯器實際講什麼。
`must be constant` 就是在說「你放了變數」，訊息已經把原因講完了。
**改完一定要跑編譯器並讀輸出**，不要憑印象改。

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

### 用 `simcheck.py` 當驗收關卡（別自己判 PASS）

`references/scripts/simcheck.py` 是一支通用的驗收閘門：
**它不看 log 裡的人話，只認機器可判定的標記行**，
而且 **fail closed —— 沒有證據就是 FAIL**。

testbench 只要多印四種標記（其他 `$display` 照舊）：

```verilog
$display("CHECK data_integrity %0d %0d", n_checked, n_bad);  // 0 筆 = FAIL
$display("COVER back_to_back %0d", n_b2b);                   // 0 次 = FAIL
$display("ASSERT crosses_4kb %0d", n_viol);                  // 非 0 = FAIL
$display("SIMEND %s", (errors==0) ? "ok" : "fail");          // 沒印 = FAIL
```

```bash
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --tb tb/tb_foo.v --src rtl/foo.v --top tb_foo     --require-cover back_to_back,boundary_cross,backpressure
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --tb ... --top tb_foo --sweep DATA_WIDTH=32,64,128,256
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --cmd "pytest -q tests/" --require-cover slow_path
```

exit 0 = PASS，1 = FAIL，最後一行是 `SIMCHECK_RESULT {json}`。
不是 Verilog 也能用（`--cmd`），標記協定跟語言無關。

### 專案起手就建 `simcheck.json`

不要每次手打參數。專案根目錄放一份設定檔，**把每個 block 的驗收標準先定下來**，
「完成」的定義就從腦子裡搬到檔案裡：

```json
{
  "blocks": {
    "axi4_master": {
      "status": "in_progress",
      "tb": "tb/tb_axi4_master.v",
      "src": ["rtl/axi4_master.v"],
      "top": "tb_axi4_master",
      "require_cover": ["single_burst", "back_to_back", "boundary_cross",
                        "backpressure", "outstanding_max", "error_response"]
    },
    "matmul_core": {
      "status": "done",
      "tb": "tb/tb_matmul_core.v",
      "src": ["rtl/matmul_core.v"],
      "top": "tb_matmul_core",
      "run_in": "out/mmtest",
      "require_cover": []
    }
  },
  "_cover_meanings": { "back_to_back": "前一筆回應還沒回就發下一筆 ..." }
}
```

```bash
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --list             # 有哪些 block、各要什麼
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --block axi4_master
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --all              # 跑全部非 pending
```

- 路徑相對於設定檔所在目錄
- `run_in` 是**執行**目錄（跟編譯目錄分開）：
  testbench 用 `$readmemh("expected.hex")` 這種相對路徑時，
  在錯的目錄跑會**每一筆都 mismatch，看起來完全像 RTL 壞掉**。
  踩過一次就把它寫進設定檔，不要靠記憶。
- `status: pending` 的 block `--all` 會跳過
- **改設定檔的規則：只能「加」驗收項目。**
  為了讓測試過而拿掉 `require_cover` 是作弊；
  真的不適用就把理由和日期寫進 HANDOFF 的「已確認行不通的做法」再拿掉。

把這份設定檔的跑法寫進 HANDOFF.md 最上面，
下一輪（或接手的人）第一眼就知道什麼叫做完成。

**`--require-cover` 是這支腳本最有價值的地方**：
它抓的是「所有檢查都 0 bad、但某個情境根本沒跑到」——
傳統判準會說 PASS，實際上那塊完全沒驗過。
把整合前驗收清單的每一項都寫成一個 `COVER`，
就沒辦法在漏測的情況下宣告完成。

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

## 二之二、測試環境壞掉時，改哪一邊？

**這是最容易做錯的一個判斷，而且做錯的代價是永久的。**

測試失敗時，錯的可能是 DUT，也可能是 testbench / 參考模型 / 對手模型。
查出來是**測試環境**的問題之後，會出現兩條路：

1. 修測試環境，讓它能正確地測目前的設計
2. **改設計**，讓它繞開測試環境撐不住的那個情況

**第 2 條路要非常小心。** 它會把一個「為了讓測試過」的限制
永久燒進設計裡，而且事後沒人看得出那個限制的真正來源 ——
六個月後有人問「為什麼這裡不能管線化」，答案已經沒人記得了。

### 兩個判斷題

要改設計，理由必須**設計本身站得住腳**，不能是「這樣我的模型就不會壞」。

- **如果測試環境是完美的，我還會做這個改動嗎？**
  答案是「不會」→ 去修測試環境。
- **這個限制寫進規格書的話，我說得出理由嗎？**
  「本 IP 不支援 ○○，因為……」後面接不出東西
  → 那不是設計決定，是在遷就工具。

### 實際案例（2026-08-30, AXI4 master）

背靠背的寫入交易讓 memory model 算錯位址。
診斷正確：model 只追一筆交易，撐不住協定允許的管線化。

當時想的修法是**改設計** —— 讓 master 等回應才發下一筆，
理由是「這個加速器不需要跨交易的寫入管線化」。

聽起來合理，但套上面兩題：
- 測試環境完美的話會這樣改嗎？**不會。**
- 是規格決定嗎？不是 —— 而且它讓**寫入頻寬直接掉一整個往返延遲**，
  對 memory-bound 的加速器是實打實的損失。

正解是把 model 的寫入端也改成 queue（讀取端本來就是 queue，照抄即可）。

### 但也有真的該改設計的時候

不是所有「測試環境撐不住」都該修測試環境。
如果那個情況**在真實系統裡也不會發生**，簡化設計是對的。差別在理由：

- ✗「我的模型只追一筆交易」→ **工具限制**
- ✓「對接的那顆 IP 本來就只接受一筆 outstanding」→ **系統約束**

**後者要有依據**（datasheet、IP 手冊、規格書），不能用猜的。
沒依據就標成 `[ASSUMPTION]` 寫進架構文件，
別讓它靜靜躺在原始碼裡變成無人知曉的限制。

### 連帶原則：不要在一個大破口旁邊做局部推理

同一輪還踩到另一件事：只盯著寫入路徑某一筆的錯位做了很細的分析，
**完全沒注意到同一次模擬裡讀取路徑從第一筆就 63 筆全錯、
而且下一個 test 直接 watchdog 卡死。**

- 只讀 log 尾巴、或只 grep 自己關心的那個訊號，會錯過更大的問題
- **先數一遍：這次跑總共幾個 error？分佈在哪幾個 test？**
  再決定要先追哪一個
- 多個測試同時爛掉時，**最先壞的那個通常是根因**，
  後面的往往只是它的下游效應。
  從第一個 test 的第一筆錯開始查，不要從最後一筆

握手 / 匯流排介面的完整驗證方法（對手模型怎麼寫、
失敗模式對照表、assertion 範本、整合前驗收清單）在
`references/protocol-interface-verification.md` ——
AXI / Avalon / Wishbone / 自訂 valid-ready 都適用。

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

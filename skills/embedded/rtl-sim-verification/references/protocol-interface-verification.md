# 協定 / 介面驗證（通用）

適用於任何「兩個模組之間靠握手搬資料」的介面：
AXI4 / AXI-Lite / AXI-Stream、Avalon、Wishbone、APB、
自訂的 valid-ready、FIFO 介面、SPI/I2C/UART 的 controller、
甚至軟體端的 producer-consumer queue。

**核心差異**：算術電路的錯會顯示成錯誤數值；
**協定電路的錯會顯示成「卡住」「資料重複」「偶爾少一筆」** ——
這幾種症狀都很容易被誤判成測試環境的問題。

---

## 一、先驗你的測試對手，再驗 DUT

驗一個 master 要有 slave，驗一個 producer 要有 consumer。
**那個對手是你自己寫的、未驗證的程式碼。**
它跟 DUT 一樣會有 bug，而且它的 bug 會偽裝成 DUT 的 bug。

**動手驗 DUT 之前，先用「已知正確的刺激」打你的對手模型**：
在 testbench 裡手刻一組交握（不透過 DUT），送已知 pattern、讀回來比對。
對手自己先過了，才有資格當標準。

這是主 SKILL.md「先讓參考模型 100% 正確再回頭改 RTL」的同一條原則。

### ⛔ 對手模型必須像真實器件，不能只是「能回應」

**這是最容易整組漏掉的一件事。**

寫一個 slave memory model 來驗 AXI master，最自然的寫法是：
一個陣列 + 固定延遲 + `ready` 恆為 1。它能跑、資料也對，
於是測試全綠 —— 但那證明的是
**「在一個永遠很快、延遲固定、不會反壓的記憶體下，資料路徑算得對」**，
不是「接上真的記憶體會動」。

真實器件與理想模型的差距（以 DDR4 + MIG 為例）：

| 真實行為 | 理想模型 | 漏掉會怎樣 |
|---|---|---|
| 延遲會變：row hit ~15ns / row miss ~45ns（差三倍） | 固定 N 拍 | outstanding 管理、重排序假設沒被測 |
| 每 7.8µs 強制 refresh，期間不能存取 | 沒有 | 長停頓可能讓計數器溢位或 FSM 卡死 |
| bank/row 結構，跨 row 代價高 | 平坦陣列 | 位址樣式的效能差異看不出來 |
| 讀寫方向切換有 bus turnaround 損失 | 沒有 | 交錯讀寫的行為未驗證 |
| 命令佇列滿會拉低 `arready`/`awready` | 恆為 1 | **反壓路徑完全沒走過** |

**最後一項最致命**，因為 `ready` 綁死 1 的測試，等於那條路徑一次都沒走過。

⚠ **但改 `ready` 的時候，取樣邏輯要一起改** ——
2026-08-30 我自己踩過：把 `rd_data_ready` 從 `1'b1` 換成 70% 隨機接受，
卻沒動 testbench 的取樣迴圈，結果跑出 574 errors，
**差點據此判定 DUT 的反壓實作是壞的**。

實際上原本的迴圈假設 `ready` 恆 1、靠「每拍前進一次」對齊 beat；
`ready` 一旦會拉低，beat 就不再每拍前進，取樣自然錯位。

正確寫法是讓 testbench 當真正的消費者 ——
**只在 `valid && ready` 成立的那一拍取樣並前進**：

```verilog
// ✗ 只等 valid：ready 恆 1 時剛好能動，一旦會拉低就錯位
do @(negedge clk); while (!valid);

// ✓ 等交握成立
do @(negedge clk); while (!(valid && ready));
```

改對之後同一份 RTL 在 70% 隨機反壓下是 **5 個 test 全 0 errors**。

**教訓：加反壓時，「測試掛掉」的第一嫌疑人是取樣邏輯，不是 DUT。**
先確認 testbench 真的按交握語意消費，再去懷疑設計。

### 對手模型的「為難清單」

寫完能跑的版本之後，**再加這幾樣**，每一樣都要有對應的 COVER：

```verilog
// 1. 延遲抖動 —— 不要固定值
lat <= MIN_LAT + ($random % (MAX_LAT - MIN_LAT));

// 2. 反壓 —— ready 不可以恆 1
always @(posedge clk) ready <= ($random % 100) < 70;

// 3. 週期性長停頓（refresh / 匯流排讓出 / 其他 master 搶）
if (refresh_timer == 0) stall_cycles <= REFRESH_PENALTY;

// 4. 讀寫轉向損失
if (dir != last_dir) extra_delay <= TURNAROUND;

// 5. 亂序回應（若協定允許）
```

**規則：對手模型只能變得更難，不能為了讓測試過而變簡單。**
測試因此掛掉是**發現了 bug**，不是模型寫壞了。

⚠ 別把這件事推遲到「接上真硬體再說」。
真硬體上的症狀是「結果偶爾錯幾筆」，是最難查的一類；
而在模擬裡加隨機反壓只要兩行。

### 對手模型最常見的三個坑

**(1) 只能追一筆交易，背靠背就爛掉**

大多數協定**允許**發起端在前一筆的回應還沒回來之前就送出下一筆
（AXI 的 write address pipelining、SCSI/NVMe 的 queue depth、
HTTP 的 pipelining、任何 credit-based 的流控都算）。

如果你的模型只用一組 `cur_addr` / `cur_count` 暫存器追蹤，
兩筆交易重疊時就會用**舊交易的狀態**去處理新交易的資料。

> 症狀：第一筆完全正確，第二筆開始位址或內容偏掉。

**發起端這樣做是合法的，是你的模型撐不住。**
正解是讓模型也能排隊：把每筆交易推進 FIFO，
每個 entry 帶自己的 `addr` / `len` / `counter`，資料通道消費 FIFO 的頭。

> 如果模型的讀取端已經寫了 queue、寫入端還是單組暫存器，
> 那就是還沒寫完 —— **兩個方向要對稱**。

**(2) 深度沒守住，`ready` / `full` 亂給**

```
WATCHDOG: stalled. rqcnt=18       // 但上限宣告是 16
```
不是效能問題，是**協定違規**。兩邊都要查：
模型的 `ready = ~full` 條件對不對，以及 DUT 有沒有自己數 outstanding。

**任何「計數器超過宣告上限」的 dump 都是 bug，不是警告。**

**(3) 兩端的索引算式不一致**

寫入端和讀取端各自算一次 `index = (base + n*ELEM_BYTES) / ELEM_BYTES`，
只要有一端忘了乘或多除一次 —— 症狀是「讀回來的值全部一樣」
而不是「差一格」，很難聯想到位址計算。

**同一個算式出現兩次就是兩個出錯機會。**
抽成一支 function 給兩端共用。

---

## 二、失敗模式 → 病因對照表

協定電路的症狀辨識度很高，背起來能省幾小時：

| 症狀 | 幾乎一定是 |
|---|---|
| **整段資料讀回同一個值** | 消費端沒推進計數 / 沒握手就取樣 |
| 差一格（off-by-one） | 計數 vs 位址遞增差一拍 |
| 值**整體位元位移** | pattern 拼接的寬度推導不一致 |
| 前 N 筆對、第 N+1 筆起全錯 | queue / FIFO 繞回或深度不足 |
| 卡住：`valid` 高但 `ready` 永遠低 | 消費端死鎖，或 ready 的條件含到自己 |
| 卡住：兩邊都在等對方 | 組合迴路 `ready = f(valid)` 且 `valid = g(ready)` |
| 計數器超過宣告上限 | ready/full 反壓條件寫錯 |
| 只有第一筆對 | 對手模型只追一筆（見上面 (1)） |
| 偶爾少一筆、重跑又正常 | 無背壓介面掉資料，或 CDC 沒同步 |

**「整段讀回同一個值」特別重要** —— 它不是數值錯誤，
是**握手根本沒發生**。看到這個症狀不要查資料路徑，去查 valid/ready。

**「偶爾少一筆」是最難的一類**，往往要跑很多次才重現。
看到它先去查有沒有無背壓介面（下一節）。

---

## 三、無背壓介面是設計決定，不是實作細節

一個資料輸出介面如果只有 valid、沒有對應的 ready：

```verilog
output reg        data_valid,     // 只有 valid
output reg [W-1:0] data,          // 沒有 ready
...
assign upstream_ready = 1'b1;     // 對上游恆為 1，照單全收
```

這是 **streaming / 無背壓** 介面：
**下游必須每一拍都吃得下，否則資料靜默消失，不會有任何錯誤訊號。**

接下游之前先問：

- 下游能保證每拍消費嗎？有沒有可能因為 buffer 滿、或在忙別的事而停一拍？
- 不能保證的話，是**加 ready 反壓**，還是**中間插 FIFO**？
- 選 FIFO，深度要能吸收多少？（至少一筆完整交易，
  通常是 `outstanding × 單筆長度`）

**這個決定要在整合之前做完。**
整合之後才發現會掉資料，症狀是「結果偶爾錯幾筆」，那是最難查的一類 bug。

### 回應通道不能不看

同一類問題：**完成的判定看錯通道**。

```verilog
assign resp_ready = 1'b1;                        // 照單全收
assign done = (beats_left == 1) && data_fire;    // 資料送完就宣告完成
```

這代表**回應碼完全沒被檢查** —— 錯誤回應
（AXI 的 SLVERR/DECERR、任何協定的 NACK / error status）進來你不會知道。

**完成 = 對方確認收到且沒出錯**，不是「我送完了」。
凡是協定有回應通道，完成判定就該看那個通道。

---

## 四、testbench 自己的坑

**(1) 只等 valid、不驅動 ready，就直接取樣**

```verilog
while (!data_valid) @(posedge clk);   // ✗ 沒有消費動作
if (data !== expected) ...            // 取樣到的可能是同一拍重複 N 次
```

- 介面**有** ready → testbench 就要當那個消費者：
  拉高 ready、**只在 `valid && ready` 那拍取樣**、然後推進計數
- 介面**沒有** ready（無背壓）→ 必須**每一拍都檢查**，
  用 `while(!valid)` 輪詢一定會漏

**取樣點永遠是「交握成立」的那一拍**，不是「valid 高」的那一拍。

**(2) pattern 拼接的寬度推導**

```verilog
data_in = {8'hA5, 8'h5A, 32'(i), 32'(addr)};   // 只有 80 bits
```
`DATA_WIDTH=256` 時剩下 176 bits 是零填充，而且**右對齊**。
產生端和比對端寫法一致就沒事，但只要有一端改了欄位順序或寬度，
症狀是「值看起來對但位置跑掉」（`A5 5A` 出現在別的欄位裡）。

**明確寫滿整個寬度，並且抽成共用 function**：
```verilog
function [W-1:0] pattern(input [31:0] i, input [31:0] addr);
    pattern = {{(W-80){1'b0}}, 8'hA5, 8'h5A, i, addr};
endfunction
```
產生端和比對端各寫一次 = 兩個出錯機會。

**(3) 每個 test 之間要重置狀態**

test 1 留下的 beat 計數 / queue 指標沒清，test 2 從髒狀態開始 ——
錯誤出現在 test 2 但根因在 test 1。
每個 test 前加「等到所有通道都 idle」的檢查，或直接重置對手模型。

**(4) 刺激不要在 DUT 取樣的同一個 timestep 變動**

```verilog
start=1; @(posedge clk); start=0;   // ✗ NBA 排序下 DUT 可能看不到
@(posedge clk); #1 start=1; @(posedge clk); #1 start=0;   // ✓
```
（主 SKILL.md 的多模組交握那節有完整說明。）

---

## 五、協定 assertion 是最便宜的保險

在整合**之前**就要過，而且是持續掛著的，不是跑一次就算。
每個協定的規則不同，但這幾類到處都適用：

```verilog
// 1. 邊界 / 對齊規則（例：AXI4 的 burst 不得跨 4KB）
if (addr_fire)
    if (addr[11:0] + ((len+1) << SIZE_SHIFT) > 4096)
        $display("ASSERT t=%0t: burst crosses 4KB addr=%h len=%0d", $time, addr, len);

// 2. 欄位合法範圍
if (addr_fire && burst_type == INCR)
    if (len > 8'd255) $display("ASSERT t=%0t: len illegal %0d", $time, len);

// 3. outstanding 不超過宣告值
if (req_fire)  outstanding <= outstanding + 1;
if (resp_fire) outstanding <= outstanding - 1;
if (outstanding > MAX_OUTSTANDING)
    $display("ASSERT t=%0t: outstanding=%0d > %0d", $time, outstanding, MAX_OUTSTANDING);

// 4. hold-stable：valid 高而 ready 低時，payload 不准變
if (valid && !ready && $past(valid))
    if (data !== $past(data))
        $display("ASSERT t=%0t: payload changed under backpressure", $time);

// 5. valid 不得無故撤回（多數協定要求 valid 一旦拉高就撐到 ready）
if ($past(valid) && !$past(ready) && !valid)
    $display("ASSERT t=%0t: valid withdrawn before handshake", $time);
```

⚠ 注意 `(len+1)`：很多協定的長度欄位是 **beats-1**（AXI 就是）。
寫成 `len * bytes` 會少算一拍，剛好跨界的那種就漏掉。
**每個長度欄位都先確認是「幾筆」還是「幾筆減一」。**

**assertion 要印出足以定位的資訊**（時間、位址、長度、計數值）。
只印 "ASSERT FAILED" 等於沒印 —— 你會知道有錯，但不知道哪一筆。

---

## 六、整合前的驗收清單

DUT 單獨驗過、要接下游之前，這幾項全部要綠。
把「burst」換成你那個協定的交易單位即可：

- [ ] 對手模型自己先被已知正確的刺激驗過
- [ ] 單筆交易，資料 bit-exact
- [ ] **背靠背交易**（不等回應就送下一筆）
- [ ] 跨邊界 / 需要拆分的傳輸，被正確拆成多筆合法交易
- [ ] 多筆 outstanding，回傳順序與資料都對
- [ ] outstanding 壓到上限時，DUT 自己會停（不會超發）
- [ ] 對手**隨機插入 wait state**（`ready` 亂拉低）時資料不掉
- [ ] 錯誤回應碼（SLVERR / NACK / error status）有被往上報
- [ ] 所有 assertion 零觸發，且 **checked 筆數 > 0**

倒數第二項的隨機 wait state 是**最會抓到 bug 的一項**：
`ready` 恆 1 的測試等於沒測反壓，而反壓正是握手邏輯出錯最多的地方。

```verilog
// 最簡單的隨機反壓
always @(posedge clk) ready <= ($random % 100) < 70;   // 70% 機率接受
```

最後一項呼應主 SKILL.md 的「PASS 之前先確認測試真的跑了」：
協定測試特別容易出現「一筆交易都沒發，所以零錯誤」。

### 把清單變成可執行的關卡

清單用讀的會漏，用跑的不會。每一項在 testbench 裡數一個計數器、
印成 `COVER`，再交給 `scripts/simcheck.py` 判定：

```verilog
// 每個情境各數一個
if (aw_fire && !prev_resp_done) n_b2b        = n_b2b + 1;   // 背靠背
if (split_happened)             n_boundary   = n_boundary + 1;
if (valid && !ready)            n_stall      = n_stall + 1; // 反壓真的發生過
if (resp_code != 0)             n_errresp    = n_errresp + 1;
if (outstanding == MAX_OUTSTANDING) n_atmax  = n_atmax + 1;

// 收尾一次印完
$display("CHECK  data_integrity %0d %0d", n_checked, n_bad);
$display("COVER  back_to_back %0d",   n_b2b);
$display("COVER  boundary_cross %0d", n_boundary);
$display("COVER  backpressure %0d",   n_stall);
$display("COVER  error_response %0d", n_errresp);
$display("COVER  outstanding_max %0d", n_atmax);
$display("ASSERT protocol_viol %0d",  n_viol);
$display("SIMEND %s", (n_bad==0 && n_viol==0) ? "ok" : "fail");
```

```bash
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --tb tb/tb_master.v --src rtl/master.v --top tb_master     --require-cover back_to_back,boundary_cross,backpressure,error_response,outstanding_max
```

任何一個 `COVER` 是 0 就 FAIL，即使所有資料比對都零錯誤。
**這正是「測試全綠但某條路徑根本沒走過」的解藥。**

反壓那項要真的隨機拉低 `ready` 才數得到 —— `ready` 恆 1 的測試
會讓 `n_stall` 停在 0，關卡就會擋下來，這是刻意的。

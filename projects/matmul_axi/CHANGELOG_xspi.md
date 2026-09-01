# xspi_slave 改動記錄（防止改回去）

> 每次改動 RTL 或 tb 的**設計決定**（不是修語法錯）就在這裡加一行。
> 動手改之前先讀這份 —— 避免把自己剛推翻的做法又改回來。

| 時間 | 改了什麼 | 理由 | 結果 |
|---|---|---|---|
| 08-31 22:39 | `ctl_push` 改成讀取在 `P_ADDR_D` 推、寫入在 CS deassert 推 | 讀取要在資料階段前就開始 fetch | timeout 仍 24 |
| 09-01 00:13 | 全檔宣告重排（清 47 個重複） | 使用在前宣告在後會產生隱式 1-bit wire，`wr_state` 恆為 Z | `wr_state` Z→2 ✅ |
| 09-01 00:41 | （AXI 引擎相關修正） | — | **timeout 24→0** ✅ |
| 09-01 01:11 | `ctl_push` 改成 `cs_rise && phase==P_DATA` | 當時的想法：統一在 CS deassert 推 | 沒改善 |
| 09-01 01:26 | **改回 01:11 之前的版本** | ⚠ 整個檔案回到 00:55 的狀態，01:11 那次白做 | 沒改善 |

| 09-01 04:35 | （資料路徑相關修正） | — | **xxxx 12→0** ✅ 所有值都讀得回來了 |

## ⚠ 已經確認行不通的做法（不要再試）

- **`ctl_push` 統一在 CS deassert 推**（01:11 試過，01:26 自己改回去了）
  理由：讀取必須在資料階段**之前**就開始 fetch，等到 CS 拉高才通知
  aclk 側，讀 FIFO 來不及填。這是你自己 01:07 診斷出來的。

## 目前卡住的點（04:41 更新）

`data_integrity 26/26` —— **26 筆全部讀回 `0000`**（期望 `00ff`、`01fe` 等）。

**已經解決的**：編譯乾淨、12 個 cover 全中、AXI 不再逾時、
**沒有任何 `xxxx` 了**（資料通路建立起來了）。

**現在的問題性質變了**：不是「路徑不通」（那會是 x），
是「**寫進去的值沒有真的到達**」—— 讀回來是初始值 0。

可能的方向（還沒驗證，不要當結論）：
- AXI 寫入交易發生了但 `wdata` 內容不對
- 寫入位址和讀取位址對不上
- `wstrb` 沒設對，資料被遮掉

---

## 規劃者實測（2026-09-01 15:16）—— 根因是「寫入 halfword 相位錯位」

用 VCD 查出 AXI 寫入交易**有發生**（reg awvalid 4 次、ddr 12 次、wstrb=1111
沒被遮掉），所以 **問題不在 AXI 引擎、不在位址解碼、不在 wstrb**。
資料在進 FIFO 之前就已經錯了 —— 看 `WCOMMIT` 就知道。

### ✅ 已驗證有效（請保留）

- **`hw_pipe_lo <= xspi_io[7:0]`**（取代 `hw_pipe_lo <= w_lo`）
  `w_lo` 在同一個 negedge 用 NBA 更新，讀到的是上一拍的值。
  改完之後 **低 byte 全部對齊**：`075d 065c 055f 045e` 完全命中期望值。

### ⛔ 已確認行不通（不要再試）

- **把整個 push 延後一拍**（加 `hw_push_d` 暫存 `hw_push_en`）
  → 首筆消失、末筆重複，比原本更糟。
- **加 `hw_pipe_loaded` 閘門只擋首筆**
  → WCOMMIT 完全沒變。flag 在 negedge 才設起來，posedge 的首次 push 已經過了。
- **只修 `w_fifo_hw` / `w_fifo_addr` 的位元切片**
  → 切片確實是錯的（FIFO 48 位元 `{addr[47:16], hw[15:0]}`，
  現在寫成 `[47:32]` / `[31:16]` 兩個都錯），**但單獨修它 0 改善**，
  因為進 FIFO 的資料本身就錯位了。要修，但不是主因。

### 還沒解的

低 byte 已對齊，**高 byte 還晚一拍**（得到 `fc5b 0258 0359...`，
高 byte 是下一筆的）。FIFO 在 **posedge** 寫入（`async_fifo.v:55`），
資料在 **negedge** 組好 —— 這條鏈上有一級多／少半拍，要逐拍印出來對，不要猜。

---

## 16:37 —— 低 byte 修正已套用（27B 完成，規劃者已驗證）

`hw_pipe_lo <= xspi_io[7:0]`（取代 `hw_pipe_lo <= w_lo`）已進 RTL。
註解寫得很清楚：`w_lo` 在同一個 negedge block 用 NBA 賦值，
讀它會拿到 `lower_{i-1}`，所以要直接讀線上的值。

**規劃者實測確認**（不是採信報告）：

```
WCOMMIT ... hw=fc5b 0258 0359 045e 055f 065c 075d
期望       hw=005a 015b 0258 0359 045e 055f 065c 075d
```

- ✅ **低 byte 全對**（`5d 5c 5f 5e` 完全命中）
- ❌ **高 byte 還晚一拍**（每筆的高 byte 是下一筆的）
- 首筆 `fc5b` 高 byte 是垃圾、末筆 `075d` 掉了

`CHECK data_integrity` 仍是 26 26。

### 下一步只剩高 byte

低 byte 已證明「直接讀線上的值」這個方向是對的。
**高 byte 用同一套邏輯檢查一遍**：`w_hi` 在 posedge N 採樣，
到了 negedge N 讀它 —— 這中間 `w_hi` 有沒有被別的地方改？
`hw_push_en` 為真的第一個 posedge，`hw_pipe` 裡裝的是哪一拍的資料？

⚠ 照第 8 條規則：**印出來逐拍比對，不要猜**。
在 tb 送出高 byte 的地方和 RTL 採樣 `w_hi` 的地方各加一行 `$display`
印 `$time`，兩邊並排看差幾拍。已經有兩次猜測失敗的紀錄了（見上一節）。

---

## 17:05 —— 你加的 PH 追蹤直接把答案攤出來了（規劃者讀了輸出）

你加的 `PH` 逐拍輸出是對的做法。它印出來的東西比先前所有推測都有用：

```
PH t=2220000 NEG ph=3 io=5a    ← 5a 在線上了，但 phase 還是 3 (P_DUMMY)
PH t=2230000 POS ph=3 io=01    ← 01 是「下一筆」的高 byte，phase 仍是 3
PH t=2240000 NEG ph=4 io=5b    ← phase 這時才變成 4 (P_DATA)
```

BURST 期望的第一筆是 `005a`。它的兩個 byte 是：
- `00` 在 t=2210000 的 posedge
- `5a` 在 t=2220000 的 negedge

**兩個都發生在 `phase` 還是 `P_DUMMY` 的時候。**

### 所以根因是「相位切換晚了一拍」

`hw_pipe` 的載入條件是 `(phase == P_DATA) && !is_read`，
第一個 halfword 整筆被這個條件擋掉 → 丟失 → 之後每筆往後錯一格。

**這一個根因同時解釋三個症狀**（不是三個獨立問題）：
- 首筆 `fc5b` 高 byte 是垃圾 → 因為真正的首筆被丟了
- 每筆高 byte 看起來「晚一拍」→ 因為整串往後平移
- 末筆 `075d` 掉了 → 因為串尾多出來的那筆沒地方放

### 交給你判斷的部分

修 `P_DATA` 的進入時機。可能在 dummy cycle 的計數、也可能在
phase 狀態機的邊緣選擇。**先用 PH 輸出確認 `P_DATA` 應該在哪個 edge 就位**
（對照 tb 第 216-217 行的驅動順序），再決定改哪裡。

⚠ 改完的驗收看這個，不要只看 CHECK：
```bash
/c/iverilog/bin/vvp out/scratch/tb.out | grep WCOMMIT | sed -n '5,12p'
```
BURST 那八筆要變成 `005a 015b 0258 0359 045e 055f 065c 075d`。
八筆全中，`CHECK data_integrity` 才會跟著降。

---

## 17:35 —— `dummy_n - 2` 是對的，資料序列已完全正確（規劃者驗證）

你把 `dummy_cnt` 改成 `dummy_n - 2`，**這一步是對的，請保留**。

實測 WCOMMIT：

```
fc00              ← 多出來的前置垃圾（唯一剩下的問題）
005a 015b 0258 0359 045e 055f 065c 075d   ← 八筆全部命中期望值 ✅
```

對照修改前是 `fc5b 0258 0359...`（整串錯位）。**現在資料本身全對了。**
相位切換也對了：`PH t=2200000 NEG ph=4` —— 進 P_DATA 的時機正確。

### 我幫你排除掉的一條岔路

試過 `dummy_n - 1`（在臨時副本，沒動你的檔）：
首筆變成 `fc5a`，高 byte 是垃圾，比 `-2` 更糟。
⛔ **不要往 `-1` 調，也不要再動 dummy 計數了 —— `-2` 已經是對的。**

### 只剩一個問題：多推了一筆

`fc00` 出現在 t=2210000，排在真正的首筆 `005a`（t=2230000）之前。
它把整串往後推一格，所以 `CHECK` 還是 26 26。

`fc00` 的內容說明了它是什麼：`fc` 是前一相位殘留在 `hw_pipe_hi` 的值，
`00` 是剛進 P_DATA 時線上的值。**這是相位切換後的第一次 push，
那時 `hw_pipe` 還沒裝進任何一筆有效資料。**

### 交給你判斷

要擋掉這第一次 push。注意：先前 CHANGELOG 記過「加 `hw_pipe_loaded` 閘門
完全沒作用」—— 那是**在 dummy 還沒對齊的時候**試的，前提已經不同了，
現在相位對了，這個方向可能反而成立。自己判斷該擋在哪一級。

⚠ 驗收標準（照第 8 條，先看 WCOMMIT 再看 CHECK）：
```bash
/c/iverilog/bin/vvp out/scratch/tb.out | grep WCOMMIT | sed -n '4,13p'
```
**`fc00` 那筆要消失，`005a` 變成 BURST 的第一筆。**
那八筆乾淨了，`CHECK data_integrity` 就會降。

---

## 18:10 —— 寫入端完成 ✅ 問題已轉移到讀取端（規劃者驗證）

`wr_data_started` 閘門接上了（510-514 行都有驅動，不是只有宣告）。
**實測 WCOMMIT：`fc00` 消失，BURST 八筆完全乾淨**

```
005a 015b 0258 0359 045e 055f 065c 075d   ← 全中 ✅
```

**寫入路徑到此完成。** 從 08-31 20:37 卡到現在的「寫進去的值沒到達」，
到這裡真的解決了。三個修正合起來才成立，缺一不可：
1. `hw_pipe_lo <= xspi_io[7:0]`（低 byte 不能讀同邊 NBA 的 w_lo）
2. `dummy_cnt <= dummy_n - 2`（P_DATA 進入時機）
3. `wr_data_started` 閘門（丟掉相位切換後的第一次 stale push）

### 但 CHECK 還是 26 26 —— 因為問題轉移了

現在的 mismatch 長這樣：

```
WRITE_VERIFY hw 0: got 0000 expected 00ff
WRITE_VERIFY hw 1: got xxxx expected 01fe     ← 注意是 xxxx
BURST hw 0: got 0000 expected 005a
BURST hw 1: got xxxx expected 015b
BURST hw 2: got 0000 expected 0258
BURST hw 3: got xxxx expected 0359
```

**偶數 halfword 讀回 `0000`、奇數讀回 `xxxx`，規律地交替。**

寫進去的值已經確定是對的（WCOMMIT 證明了），所以這是**讀取路徑**的問題，
不要再回頭動寫入端 —— ⛔ 上面那三個修正都不要改掉。

`xxxx` 代表「該有值的時候沒有值」，`0000` 代表「讀到初始值」，
兩者規律交替 = 讀取通道的 halfword 組裝每兩筆錯一次。
**這跟寫入端當初的症狀是鏡像的**，成因很可能同一類：
posedge/negedge 取樣相位、或 `rd_shift_out` 高低 byte 的推送時機。

### 下一步

照第 8 條規則：在讀取路徑加 `RCOMMIT` 之類的逐拍 `$display`
（對應寫入端的 `WCOMMIT`），印出每個 halfword 從 AXI 讀回來、
進 rd FIFO、推上 xspi 線的每一級，看是哪一級開始交替出錯。

⚠ 先確認 `xxxx` 是在哪一級出現的 —— 那一級就是問題所在。

---

## ✅ 21:30 —— 寫入路徑完成（規劃者實測確認，兩組全對）

```
00ff 01fe 02fd 03fc                          ← WRITE_VERIFY 四筆，開頭無垃圾 ✅
005a 015b 0258 0359 045e 055f 065c 075d      ← BURST 八筆，開頭無垃圾 ✅
```

**你把兩種寫法成功合併了。** 從 08-31 20:37 卡到現在的寫入端問題到此結束。

⛔ **寫入端從現在起不要再動。** 四個修正缺一不可：
1. `hw_pipe_lo <= xspi_io[7:0]` —— 低 byte 不能讀同邊 NBA 的 `w_lo`
2. `dummy_cnt <= dummy_n - 2` —— P_DATA 進入時機
3. negedge 直接 commit `{w_hi, xspi_io}` —— 讓單筆 frame 也對
4. `wr_data_started` 閘門 —— 丟掉每個 frame 第一次的 stale commit

改動前先跑一次記下這兩組數字，改完比對。**任何一組退化就是改錯了。**

## 現在唯一的問題：讀取路徑

`CHECK` 還是 26 26，但 mismatch 的模式已經完全不同了：

```
hw 0: got 0000 expected 00ff     ← 偶數：讀回初始值
hw 1: got xxxx expected 01fe     ← 奇數：根本沒有值
hw 2: got 0000 expected 00ff
hw 3: got xxxx expected 01fe
```

**偶數 halfword 讀回 `0000`、奇數讀回 `xxxx`，規律交替。**
寫進去的值已經證明是對的（WCOMMIT 全中），所以問題純粹在讀出來這一段。

### 這個交替模式在告訴你什麼

- `xxxx` = 該有值的時候沒有值 → 那一級**根本沒被驅動**
- `0000` = 讀到初始值 → 那一級**有驅動但資料沒到**
- 兩者**每兩筆交替一次** → 一個 32-bit beat 裝兩個 halfword，
  **高半和低半的處理不對稱** —— 一半有接、一半沒接

**這跟寫入端當初的問題是鏡像的**：寫入是「兩個 byte 組成一個 halfword」，
讀取是「一個 beat 拆成兩個 halfword」。拆的時候高低半搞錯了。

### 下一步（照第 8 條規則）

在讀取路徑加 `RCOMMIT` 逐拍 `$display`（對應寫入端的 `WCOMMIT`），
把 halfword 從 AXI 讀回 → 進 rd FIFO → 推上 xspi 線的每一級都印出來。

**先確認 `xxxx` 是從哪一級開始出現的** —— 那一級就是問題所在。
不要從頭猜整條路徑，用印出來的值把範圍縮小，跟你解寫入端時一樣。

參考：`rd_shift_out`、`rd_lo_q`、讀取 FIFO 的推入邏輯（註解說 xSPI DDR
順序是「for each 32-bit beat [A+3 A+2 A+1 A+0] push {A+1,A+0} then {A+3,A+2}」）
—— 這個順序有沒有真的照做，值得先確認。

---

## 23:40 —— 兩條線索其實是同一件事（規劃者實測）

你這輪加的 `RCOMMIT` / `RDWR` 追蹤是對的做法。規劃者跑完之後，
發現你手上的兩條線索**不是兩個問題，是同一個根因的兩面**：

```
AXIWR 出現次數：0                              ← 寫入引擎從沒發過交易
RDWR t=450 wrdata=xxxx pushphase=0 ...        ← 讀出來全是 xxxx
RDWR t=454 wrdata=xxxx pushphase=1 ...
```

**因果關係**：`AXIWR` 從沒發生 → 資料從沒進 DDR 模型 →
讀回來當然是未初始化的 `xxxx` / `0000`。

`RDWR` 的 `pushphase` 在 0/1 交替，正好對上 mismatch 的
`0000` / `xxxx` 交替 —— 那個交替是「讀 FIFO 一次推高半一次推低半」的
正常行為，**不是 bug**。讀取端的邏輯可能本來就是對的。

### ⚠ 所以不要花時間修讀取端

先把 `AXIWR` 弄出來。寫入交易一旦真的發出去、資料進了 DDR 模型，
讀取端很可能自動就對了 —— 到時再看還有沒有問題。

**修讀取端之前，先確認寫入端真的有把資料送出去。**
否則你會在一個「輸入本來就是 x」的路徑上除錯，怎麼修都不會對。

### 這一輪只要回答一個問題

**`wr_state` 為什麼不進 WR_DRAIN？**

```bash
# 加這個，看狀態機卡在哪一態
# always @(posedge aclk) if (wr_state != 0)
#     $display("WRST t=%0t state=%0d hw_left=%0d empty=%b start=%b busy=%b",
#              $time, wr_state, wr_hw_left, w_rd_empty, ddr_wr_start, ddr_wr_busy);
```

要查的幾件事（照順序，一次一個）：
1. `wr_state` 有沒有離開過 IDLE？沒有的話是**觸發條件**沒成立
2. 觸發它的訊號是什麼（`ctl_push`？CS deassert？）—— 那個訊號有沒有來
3. `w_rd_empty` 是不是一直是 1 —— 那表示 FIFO 的**跨時脈域指標**沒同步過來
   （WCOMMIT 證明資料寫進去了，但 aclk 側可能看不到）

第 3 點特別值得先查：寫入端在 `xspi_clk`、讀出端在 `aclk`，
`async_fifo` 的指標要跨時脈域同步。**寫進去了不等於另一邊看得到。**

⛔ 寫入端那四個修正維持不動（WCOMMIT 23:36 實測仍全對，你沒動它，很好）。

---

## ⚠ 00:00 —— （這節的結論已被 00:26 推翻，只看證據不要看結論）

你加的 AXI 握手計數器是**決勝的一手**。數字直接指出根因：

```
AXI DDR aw=0 w=0 b=0 ar=0 r=0
AXI REG aw=1 w=0 b=0 ar=2 r=16
```

**`w=0` 在兩個通道都成立 —— 寫資料通道從來沒有握手過一次。**

規劃者再從 VCD 查：
```
ddr_wvalid 拉高次數：0        ← DUT 從沒送出寫資料
ddr_wready 拉高次數：2        ← DDR 模型準備好了，在等
```

不是 ready 沒來，是 **valid 從來沒拉起來**。

### 你自己的 DBG 輸出已經寫著答案

```
DBG t=426000 ... wr_state=0 w_rd_empty=1 wr_hw_left=0 ...
DBG t=430000 ... wr_state=0 w_rd_empty=1 wr_hw_left=0 ...
```

- `wr_state=0` —— 寫入狀態機**始終停在 IDLE**，從沒進 WR_DRAIN
- `w_rd_empty=1` —— **aclk 側看到寫入 FIFO 是空的**

**但 WCOMMIT 證明資料確實寫進去了**（39 筆，兩組全對）。

### 所以根因是：async_fifo 的跨時脈域指標沒同步過來

寫入端在 `xspi_clk`（50 MHz）、讀出端在 `aclk`（100 MHz）。
`w_commit` 把資料寫進 FIFO 了，但 **aclk 側的 `rd_empty` 永遠是 1**，
所以狀態機認為沒東西可送，永遠不啟動。

**寫進去了 ≠ 另一邊看得到。** 這是 CDC 的經典問題。

### 這一輪就查 `rtl/async_fifo.v`（117 行，很短）

一次查一件，照順序：

1. **寫指標有沒有同步到讀時脈域？** 找 `wr_ptr` 的兩級同步器
   （通常是 `wr_ptr_gray` → `sync1` → `sync2`）。有沒有真的接上，
   還是只有宣告沒有驅動？（⚠ 記得看 assign / <=，不要只看宣告）
2. **`rd_empty` 的比較式用的是同步過來的指標，還是原始的 `wr_ptr`？**
   用錯的話跨時脈域就會失效。
3. **格雷碼轉換有沒有做對？** 二進位指標直接跨時脈域會出錯。
4. **`rst_n` 有沒有把兩邊都重置乾淨？** async_fifo 只吃一個 `rst_n`，
   但有兩個時脈域。

⚠ 注意：**`async_fifo` 是七個已經 PASS 的 block 之一**（`tb_async_fifo` 過了）。
所以要嘛是那個 tb 沒測到跨時脈域的情況（很可能 —— 單元測試常用同一個時脈），
要嘛是 `xspi_slave` 這邊的接法有問題（例如 `rd_en` 的時機）。
**先跑一次 `tb_async_fifo` 看它測了什麼**，再決定改哪邊。

⛔ 寫入端那四個修正維持不動（WCOMMIT 23:57 實測仍全對）。

---

## 00:20 —— 範圍再縮小：控制字有進來，但 DDR 寫入引擎只啟動過一次

你加的 `CTLPUSH` / `WRSTART` 追蹤又往前推了一步。規劃者實測：

```
CTLPUSH t=654     isread=0 isreg=1 len=0 addr=00000004   ← REG
CTLPUSH t=81174   isread=0 isreg=0 len=4 addr=90010000   ← DDR
CTLPUSH t=322274  isread=0 isreg=0 len=8 addr=90010100   ← DDR
CTLPUSH t=483514  isread=0 isreg=0 len=1 addr=90010200   ← DDR
CTLPUSH t=1524694 isread=0 isreg=0 len=2 addr=90010500   ← DDR
（還有更多，位址和長度都正確）

WRSTART t=690 reg=1 ddr=0 isread=0 faddr=00000004 len=4  ← 只有這一次！
```

### 三個關鍵事實

1. **控制字全部有正確推進來** —— `len` 和 `addr` 都對，前端沒問題
2. **`WRSTART` 只發生過 1 次**，而且是 `reg=1`（REG 通道）
3. **所有 DDR 寫入（`isreg=0`）從沒觸發過 `wr_start`** —— 對應 `AXI DDR aw=0`

### 所以問題在「控制字進來之後、wr_start 發出之前」這一段

兩個可能，**先分辨是哪一個**（不要同時改）：

**(A) `w_rd_empty` 恆為 1（CDC 指標沒同步）** ← 我 00:00 那節的假設
狀態機看到 FIFO 是空的，即使控制字來了也不啟動。

**(B) 狀態機第一次之後沒回到 IDLE**
t=690 那次啟動後卡在某個狀態，之後的控制字全被忽略。

**怎麼分辨**（一行就夠）：
```verilog
always @(posedge aclk) if (ctl_rd_en || wr_state != 0)
    $display("WFSM t=%0d wr_state=%0d w_rd_empty=%b wr_hw_left=%0d ctl_len=%0d",
             $time, wr_state, w_rd_empty, wr_hw_left, ctl_len);
```
- 如果 `wr_state` 一直是 0 且 `w_rd_empty` 一直是 1 → **是 (A)**，去查 `async_fifo.v`
- 如果 `wr_state` 卡在非 0 的某個值 → **是 (B)**，去查狀態機的離開條件

### ⚠ 另外注意兩件事（先記下來，不要現在改）

1. **`ctl_push` 連發 4-5 個 aclk 週期**（t=394,398,402,406,410）
   它應該是單週期脈衝。可能造成控制 FIFO 塞進重複的項目。
2. **第一筆 REG 的 `len=0`**（t=654），但 WRSTART 顯示 `len=4`。
   長度換算某處不一致。

這兩個都是真問題，但**先解決「DDR 引擎完全不啟動」**——
那個解了，這兩個的影響才看得出來。⛔ 一次只解一個。

⛔ 寫入端那四個修正維持不動（WCOMMIT 00:17 實測仍全對）。

---

## ✅ 00:26 —— 規劃者更正：`async_fifo.v` 沒有問題，不要查它

我 00:00 那節的假設 (A)「CDC 指標沒同步」**是錯的**。
規劃者讀完 `rtl/async_fifo.v` 全部 117 行，確認它是**教科書級正確**的實作：

| 項目 | 狀態 |
|---|---|
| 格雷碼轉換 `bin2gray` | ✅ `(b >> 1) ^ b`，正確 |
| 寫指標同步到讀域 | ✅ `wr_gray_sync1/2` 雙級，有驅動 |
| 讀指標同步到寫域 | ✅ `rd_gray_sync1/2` 雙級，有驅動 |
| `rd_empty` 的比較 | ✅ `rd_gray == wr_gray_sync2` —— 用的是**同步後**的指標 |
| `wr_full` 的比較 | ✅ 同樣用同步後的指標 |
| 資料路徑 | ✅ 雙埠 RAM，不需同步（指標保證讀寫不撞） |

**⛔ 不要改 `async_fifo.v`，也不要為它寫新的 tb。** 這條路是死的，
我幫你走過了。

### 所以答案是 00:20 那節的 (B)：狀態機的問題

`wr_state` 沒有進 WR_DRAIN，不是因為看不到資料，而是**狀態機自己的
轉移條件不成立**，或第一次啟動後沒回到 IDLE。

證據回顧：`WRSTART` 只在 t=690 發生一次（REG 通道），
之後所有 DDR 寫入（t=81174、322274、483514…）控制字都正確進來了，
但引擎完全沒動。**第一次能動、之後都不動** —— 這個模式指向
「狀態機卡在某處沒回來」，而不是「從來沒被觸發」。

### 這一輪的順序（照這個做，不要跳）

1. **先把 `$past()` 拿掉**（4 處，見 00:25 那節的正確寫法），
   讓模擬跑得起來 —— 現在完全沒有輸出可看
2. 加 `WRSTATE` 追蹤印出 `wr_state` 的每次轉移
3. 看 t=690 那次啟動之後，`wr_state` **停在哪一個值**
4. 查那個狀態的離開條件為什麼不成立

⛔ 寫入端那四個修正維持不動。改完先確認 WCOMMIT 兩組沒退化。

---

## 🎯 00:28 —— 死結找到了：`WR_WAIT` 永遠等不到 `wr_done`

你修掉 `$past` 讓模擬恢復（很好，位元切片也順手改對了），
`WRSTATE` 追蹤直接把答案印出來：

```
WRSTATE t=2    xx->00              ← 重置
WRSTATE t=690  00->01  hwleft=0    ← 唯一一次啟動，進 WR_DRAIN
WRSTATE t=694  01->10  hwleft=0    ← 4ns 後就跳到 WR_WAIT
（之後再也沒有任何轉移 —— 永遠卡在狀態 2）
```

**`wr_state` 卡死在 `WR_WAIT`（=2），所以之後每一個控制字都被忽略。**
這就是為什麼 `WRSTART` 只有 1 次、`AXI DDR aw=0`。

### 死結的成因（三件事湊起來）

1. **進 `WR_DRAIN` 時 `wr_hw_left = 0`**（`f_len_hw` 是 0）
   → `WR_DRAIN` 沒送出任何一個 beat 就直接離開（t=690→694 只隔 4ns）

2. **但 `wr_start` 已經發出去了**，而且 `wr_len_bytes` 最小是一個完整 beat：
   ```verilog
   wire [15:0] wr_total_beats = (wr_beats_raw == 16'd0) ? 16'd1 : wr_beats_raw;
   wire [15:0] wr_len_bytes   = wr_total_beats * BEAT_BYTES;
   ```
   → **master 被告知「會有 1 個 beat」，但一個都沒收到**

3. **`WR_WAIT` 的唯一出路是 `wr_done`**：
   ```verilog
   WR_WAIT: begin
       if ((wr_target_reg && reg_wr_done) || (!wr_target_reg && ddr_wr_done))
           wr_state <= WR_IDLE;
   end
   ```
   → master 在等那個永遠不來的 beat，`wr_done` 永遠不發 → **死結**

### 00:20 那個「`len=0`」不是次要問題，它就是起因

我當時說「先不要碰」，那個判斷錯了 —— 抱歉。
`CTLPUSH t=654 isread=0 isreg=1 len=0` 那筆長度 0 的控制字，
就是把狀態機推進死結的那一下。

### 要解的是「長度 0 的寫入 frame 怎麼處理」

自己判斷哪個做法對，但至少要涵蓋這件事：
**`f_len_hw == 0` 的寫入 frame 不應該發 `wr_start`**（沒資料可寫），
應該直接跳過、留在 `WR_IDLE`。

⚠ 另外檢查一下 `WR_WAIT` 該不該有逃生門（timeout 或 abort）。
現在只要 master 因為任何原因不發 `wr_done`，整個引擎就永久停擺 ——
**一個 frame 出問題會害死後面所有 frame**，這在真實硬體上是嚴重缺陷，
不只是模擬問題。但**先解 len=0**，逃生門之後再說。

### 驗收

```bash
/c/iverilog/bin/vvp out/scratch/tb.out | grep -E "WRSTATE|^AXI " | head -20
```
- `WRSTATE` 要看到**多次** `00->01->10->00` 的完整循環（不是卡在 10）
- `AXI DDR aw=` 要 **> 0**（現在是 0）
- WCOMMIT 兩組維持不變

⛔ 寫入端那四個修正維持不動（WCOMMIT 剛實測仍全對）。
⛔ `async_fifo.v` 沒問題，不要查（見 00:30 那節）。

# 規劃者的診斷（2026-09-01 15:16）—— 讀完再動手

## 根因已經定位到「寫入資料相位錯位」，不是 AXI、不是位址解碼

用 VCD + WCOMMIT 實測，AXI 寫入交易**確實有發出**（reg 4 次 awvalid、
ddr 12 次），wstrb 全是 1111 沒被遮掉。所以問題**不在 AXI 引擎**。
真正的問題在 xspi 前端組 halfword 的時候。

## 實測證據（跑 `vvp` 後 grep WCOMMIT 就看得到）

BURST 那組期望 `005a 015b 0258 0359 045e 055f 065c 075d`（八筆），
目前 WCOMMIT 實際送進 FIFO 的是：

```
fcfc, 025b, 0358, 0459, 055e, 065f, 075c      ← 只有七筆
```

對照後的結論：**每一筆的高低 byte 各自來自不同的資料 cycle**，
而且整組往後偏移一格、末筆掉了。

## tb 的驅動相位（`tb/tb_xspi_slave.v:216-217`）—— 這是對齊的基準

```verilog
@(negedge xspi_clk); #1; xspi_io_master = hw[15:8];   // upper：negedge 之後放上 -> 要在「下一個 posedge」被採
@(posedge xspi_clk); #1; xspi_io_master = hw[7:0];    // lower：posedge 之後放上 -> 要在「下一個 negedge」被採
```

所以一個 halfword 橫跨：posedge N（高 byte）→ negedge N（低 byte）。

## 我驗證過的事情（省你時間，不要重試）

1. **`w_fifo_hw = w_rd_data[31:16]` 的位元切片是錯的** —— FIFO 是 48 位元
   `{addr[31:0], hw[15:0]}`，halfword 在 `[15:0]`、addr 在 `[47:16]`。
   現在 804/805 兩行寫成 `[47:32]` 和 `[31:16]`，兩個都錯。
   **但單獨修這個沒有改善**（因為進 FIFO 的資料本身就已經錯位了）。
   還是要修，只是它不是主因。

2. **`hw_pipe_lo <= w_lo` 是錯的** —— `w_lo` 也在同一個 negedge 用 NBA 更新，
   讀到的是**上一拍**的值。改成 `hw_pipe_lo <= xspi_io[7:0]`（當下直接採）
   之後，**低 byte 就全對了**：`075d 065c 055f 045e` 完全命中期望值。
   ✅ 這一步是真的有效，請保留。

3. 改完 (2) 之後**剩下高 byte 還晚一拍**：得到 `fc5b 0258 0359...`，
   高 byte 是下一筆的。

4. **把整個 push 延後一拍（`hw_push_d`）→ 反而更糟**：首筆消失、末筆重複。
   ❌ 不要走這條。

5. **加 `hw_pipe_loaded` 閘門只擋首筆 → 完全沒作用**（WCOMMIT 一模一樣），
   因為 flag 在 negedge 才設起來，posedge 的首次 push 已經過去了。
   ❌ 不要走這條。

## 所以還沒解的就剩一件事

低 byte 已經對齊、高 byte 晚一拍。

`w_hi` 在 posedge N 採樣 io（= cycle N 的高 byte，這是對的），
`hw_pipe` 在 negedge N 組裝（此時 `w_hi` 是 cycle N 的，也對），
但 **FIFO 在 posedge 寫入**（`u_wfifo` 的 `.wr_clk(xspi_clk)`，
`async_fifo.v:55` 是 `always @(posedge wr_clk)`）——
posedge N+1 寫進去的是 negedge N 組好的值。

**請你自己推一遍這條鏈上到底哪一級多／少了半拍**，把高 byte 也對齊。
建議做法：在 negedge 的 always block 裡加 `$display` 印出
`{w_hi, xspi_io[7:0]}` 跟當下的 `$time`，跟 tb 送出的順序逐拍對照，
不要用猜的。

## 驗收（自己跑，貼輸出）

```bash
cd /c/Users/pjunm/matmul_axi
/c/iverilog/bin/iverilog -o out/scratch/tb.out -g2012 -s tb_xspi_slave tb/tb_xspi_slave.v rtl/*.v
/c/iverilog/bin/vvp out/scratch/tb.out | grep WCOMMIT | head -12
/c/iverilog/bin/vvp out/scratch/tb.out | grep -E "CHECK|SIMEND"
```

**先看 WCOMMIT 那八筆有沒有變成 `005a 015b 0258 0359 045e 055f 065c 075d`。**
WCOMMIT 對了，`CHECK data_integrity` 才會跟著降。
每改一次就貼這兩段輸出 —— 不要連改三處再一起跑。

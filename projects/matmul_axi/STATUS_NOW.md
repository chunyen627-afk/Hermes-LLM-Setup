# 現況快照（規劃者維護，2026-09-01 15:07）

> 重開機或換 session 接續時**先讀這份**。

## 一句話

`xspi_slave` 編譯乾淨、12 個 cover 全中、AXI 不逾時、資料通路已打通，
但 **`data_integrity` 26/26 全錯** —— 全部讀回 `0000`（期望 `00ff`）。
其他七個 block 完整 gate 全 PASS，只剩這一個。

## ⚠ 09-01 下午：llama-server 鎖死，浪費約 1 小時

**`/health` 回 200 只要 1 毫秒，但 `/slots` 永久逾時** —— server 連續
跑了 72 小時後內部鎖死。這一個根因造成五種症狀，先前全被當成獨立問題：

| 症狀 | 誤判成 |
|---|---|
| 27B 連續兩次卡在 `Initializing agent...` | 初始化慢 |
| 看門狗一直印「llama-server 沒在跑」 | 探測誤判 |
| `1-START-GPU-Server.bat` 視窗一直跳 | 只是 `pause` 的問題 |
| 27B 對話莫名斷線 | server 重啟造成 |
| `_health.py` 速度顯示 `None` | 取樣問題 |

**判斷方法**（只看 health 會誤判成正常）：

```bash
curl -s --max-time 8 -o /dev/null -w "health %{http_code} (%{time_total}s)
" http://127.0.0.1:8001/health
curl -s --max-time 8 -o /dev/null -w "slots  %{http_code} (%{time_total}s)
" http://127.0.0.1:8001/slots
```

health 快、slots 逾時 = 鎖死，要重啟 server。
**監控已經會自動抓這個**（`_watch_restart.py` 的 `deadlocked`）。

## 這一夜的軌跡（08-31 20:37 → 09-01 15:00）

| 時間 | 突破 |
|---|---|
| 20:37 | 26 個 mismatch 全 `xxxx`、AXI timeout 24 次 |
| 00:14 | 隱式 wire 修好（`wr_state` Z→2）|
| 00:41 | **AXI timeout 24 → 0** |
| 04:35 | **`xxxx` 12 → 0**（資料通路打通）|
| 09:24 | 換手 —— 上一輪 16 小時沒用波形 |
| 09:40 | 新一輪加上 `$dumpvars`，VCD 產生 |
| 11:00 | 用了兩次 `vision_analyze` 看波形 |
| 14:32-15:00 | **server 鎖死**，收行程 + 重啟 + 重派 |

**主指標 `26/26` 從 08-31 20:37 起就沒動過。**

## 視覺的實測結論（重要）

它真的用了 `vision_analyze`（兩次，有畫波形圖），但**自己判斷不夠用**：

> The vision model is reading the waveform but the window is too wide to
> read exact byte values reliably — **it's guessing.**

> Vision is struggling to read exact hex off a dense waveform. Let me instead
> print the exact signal values at each SCK edge — **ground-truth without
> relying on vision.**

**這個判斷是對的**：視覺看得出訊號什麼時候翻、握手有沒有對上，
但**讀不準密集波形上的 hex 值**。

不過波形沒白開 —— 它現在**直接從 VCD 查任意時刻的訊號值**，
比昨晚「在 tb 加 `$display` 再重跑」快得多。

## ⚠ 規劃者的評估（12:53）

資料正確性 **16 小時沒動**。期間每個階段都真的解掉東西（不是空轉），
但最後這關卡很久。

判準對照：
- 同一問題超過三輪 → 早就觸發
- 修好又改回去 → 發生過一次（01:11→01:26，已建 CHANGELOG 防堵）
- 診斷能力 → **它的診斷都對，但解不掉**

**使用者 2026-09-01 12:53 決定：明天再說，今晚繼續讓它跑。**
如果明天還是 26/26，要重新評估（可能由規劃者下場，估約 60K tokens、兩三輪）。

## ⚠ 還沒做但該做的事

**tb 沒有 `$dumpfile` / `$dumpvars`，所以產不出 VCD。**
時序對齊問題正是波形最擅長的，但現在沒波形可看。
`tb/tb_xspi_slave.v` 的第一個 initial block 上方有詳細註解說明怎麼加。

27B 有視覺能力（mmproj 已掛），畫成波形圖用 `vision_analyze` 問具體問題
會比一輪一輪加 `$display` 快得多。

## 已經解掉的（不要重做）

- `ctl_rd_en` 沒有驅動 → 已補上 assign
- `data` 用 packed 大向量造成一堆 part-select 錯 → 改回 unpacked array
- `wr_beat[(cnt*8+15):(cnt*8)]` 不合法 → 改成 shift-OR
- `32'h9001_0200[31:24]` 對字面常數取 bit-select → 拆成四個 8-bit
- tb 的 `xspi_io` 不是 valid l-value → 加 `xspi_io_master` reg + assign

## 驗證指令

```bash
# 編譯 + 模擬
/c/iverilog/bin/iverilog -o /tmp/tb.out -g2012 -s tb_xspi_slave tb/tb_xspi_slave.v rtl/*.v
/c/iverilog/bin/vvp /tmp/tb.out | grep -E "CHECK|SIMEND"

# 單一 block 的 gate
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --block xspi_slave
```

## 系統整合的決定（2026-08-31）

- interconnect、width converter、MIG 全部用 **Xilinx 內建 IP**，不自己寫
- 那些是加密 SystemVerilog、只能用 xsim，**絕不進模組層的 iverilog gate**
- 時脈：`aclk` 100 MHz、`xspi_clk` 50 MHz，約束在 `constraints/timing.xdc`
- 架構圖：`docs/` 沒有，在 Hermes-LLM-Setup 倉庫的
  `projects/matmul_axi/docs/fpga-architecture.html`

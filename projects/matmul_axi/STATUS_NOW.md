# 現況快照（規劃者維護，2026-08-31 22:22）

> `HANDOFF.md` 停在 15:31，之後七小時的進展在這裡。
> 重開機或換 session 接續時**先讀這份**。

## 一句話

`xspi_slave` 的 RTL 和 tb 都編譯乾淨、12 個 cover 全中，
但**資料讀回來是 x（26/26 全錯）**，卡在 xSPI 前端的時序對齊。

## 完整 gate（22:00 跑過）

十一個 run 只有 `xspi_slave` FAIL，其餘全 PASS：

```
PASS f32_units / matmul_core / axi4_slave_reg(32,64,128,256)
PASS axi4_master / matmul_top / cdc / matmul_top_cdc
FAIL xspi_slave
  check 'data_integrity': 26 of 26 mismatched
  the test itself reported SIMEND fail
  fatal marker in log: 'timeout'
```

## 目前的診斷（27B 自己查到的，方向正確）

位址的 byte1 在取樣時匯流排還是 X：

```
f_addr=0040xx00   ← byte2=0x40 對，byte1 是 X
```

它的判斷：`addr_b1` 的 rising-edge capture 發生在匯流排還是 X 的時候，
是 **tb 驅動時機和 DUT 取樣時機對不上**（timing-alignment）。

相關的除錯輸出（它自己加的 DBG monitor）：

```
rd_state=1  rd_tgt_reg=x  rd_total=1
reg_arv=0  ddr_arv=0        ← 兩邊 arvalid 都沒拉起來
```

`rd_tgt_reg=x` 表示讀取引擎不知道目標是 reg 還是 ddr，所以兩邊都不發請求。

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

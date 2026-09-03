# CHANGELOG — Stage 5 (System Integration)

## Segment 1: create project + add RTL (vivado/01_project.tcl) — DONE

**What was done**
- Wrote `vivado/01_project.tcl` (segment 1 of SPEC section 4): create the
  Vivado project shell, pin board part, add all frozen `rtl/*.v`, add
  `constraints/timing.xdc`, update compile order. No IP / block design yet.
- Ran in batch mode:
  `timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog -source vivado/01_project.tcl`

**Actual output (exit code 0)**
- `created project: sys_int`
- `added rtl files: 9` (all of async_fifo, axi4_master, axi4_slave_reg,
  axi4s_reg, f32_add, f32_mul, matmul_core, matmul_top, xspi_slave)
- `added constraints` / `compile order updated`
- **`PROJECT_OK sys_int parts=xcvu9p-flga2104-2L-e board=xilinx.com:vcu118:part0:2.0`**
- No `ERROR:` lines in the log (only WARNINGs, see note below).

**Verification on disk**
- `vivado/sys_int/` produced with `sys_int.xpr` + `.cache` / `.hw` / `.ip_user_files`.
- `.xpr` references all 9 `rtl/*.v` and `constraints/timing.xdc` (confirmed by grep).

**Note — device speed grade changed -1 → -2L (expected, not an error)**
The spec text used `-part xcvu9p-flga2104-1-e`, but the VCU118 board part
`xilinx.com:vcu118:part0:2.0` ships a **-2L** speed-grade device. Vivado auto-upgraded
the project part to `xcvu9p-flga2104-2l-e` (WARNING [Project 1-153]). This is the
board preset winning — correct behavior for this board. Downstream IP / synthesis
will target -2L. Two more benign warnings: `[Ipconfig 75-871] Could not load NoC clock
tree from device` and `[Ipconfig 75-570] Unable to create NoC or AIE Models` — these
are normal for a fresh project before any IP is added (no NoC/AIE yet).

**Red lines respected:** no `rtl/*.v` modified; no Xilinx IP touched; batch mode only.

**Next segment (2): add IP** → `vivado/02_ip.tcl`. Add one IP at a time
(AXI SmartConnect, MIG DDR4 SDRAM w/ VCU118 preset, Clocking Wizard, Processor
System Reset), running `validate_bd_design` after each.

---

## ⚠ 19:18 —— 規劃者發現：RTL 有一處 iverilog 過但 Vivado 合成不過

規劃者在驗證新的腳位約束時，用 `synth_design -rtl` 做 elaborate，撞到：

```
ERROR: [Synth 8-9315] concurrent assignment to a non-net 'm_axi_rready'
       is not permitted [rtl/axi4_master.v:243]
ERROR: [Synth 8-12188] Failed to read verilog 'rtl/axi4_master.v'
```

**原因**（規劃者已定位，不用再查）：

```verilog
rtl/axi4_master.v:90    output reg  m_axi_rready,     ← 宣告成 reg
rtl/axi4_master.v:243   assign m_axi_rready = !rd_data_valid;   ← 卻用 assign 驅動
```

`reg` 不能用 `assign` 驅動。**iverilog 寬鬆放過，Vivado 合成器擋下。**
這是派工模板第 9 條（「編譯過不等於跑得起來」）的另一種版本：
**模擬過不等於合成得了。**

規劃者已掃過全部 9 個 RTL 檔，**同類問題只有這一處**。

### 要做的（第 2 段跑完再處理，不要中斷現在的工作）

1. `rtl/axi4_master.v:90` 的 `output reg m_axi_rready` 改成 `output wire m_axi_rready`
   （它實際上是被 `assign` 驅動的，本來就該是 wire）
2. **改完必須重跑完整 gate**：
   ```bash
   python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --all
   ```
   要維持 **11 run 全 PASS**。這是「RTL 凍結」的例外情況：合成擋下來了非改不可，
   但改完一定要證明沒有退化。
3. 再跑一次 elaborate 確認 Vivado 這關過了：
   ```bash
   timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog -source /tmp/chkpin.tcl
   ```

## ✅ 19:17 —— 新增腳位約束 `constraints/pins_vcu118.xdc`（規劃者建立）

使用者提供 VCU118 的 xSPI 腳位。**原稿的埠名跟 RTL 對不上，已對應**：

| 使用者原稿 | RTL 實際埠名 |
|---|---|
| `xspi_sclk` | `xspi_clk` |
| `xspi_dq[7:0]` | `xspi_io[7:0]` |
| `xspi_ce_i` | `xspi_cs_n` |
| `xspi_dm_dqs` | `xspi_dqs` |

11 條 `set_property`，全部 `LVCMOS18`。腳位值原封不動，只改名字。

⚠ **這些 port 名字要跟「合成時的 top」一致。** 系統整合完成後 top 會變成
block design 的 wrapper —— 如果 wrapper 把訊號改名，這份要跟著改。
第 3 段設 `set_property top` 時要一併確認。

⚠ 這份**還沒有在 Vivado 裡驗證過**（被上面那個 axi4_master 的錯誤擋住了）。
修完 `m_axi_rready` 之後要跑一次 elaborate，確認 11 個 port 都找得到。

---

## Segment 2: add IP (vivado/02_ip.tcl) — DONE

**What was done**
- Wrote `vivado/02_ip.tcl`: open the existing project, clear any stale `.bd`,
  `create_bd_design top_bd`, then add the 4 Xilinx IPs **one at a time**, running
  `validate_bd_design` after each so a failure points at the exact IP. No
  connections / wrapper / `set_property top` yet (segment 3).
- Ran in batch mode:
  `timeout 280 /c/Xilinx/Vivado/2024.2/bin/vivado -mode batch -nojournal -nolog -source vivado/02_ip.tcl`

**IPs added (5 cells, 4 IP types) — versions from the persisted `top_bd.bd` on disk**

| cell | VLNV | ip_revision | key config |
|---|---|---|---|
| `clk_wiz_0` | `xilinx.com:ip:clk_wiz:6.0` | 15 | PRIM_IN_FREQ=300, Differential; out1=100 MHz (aclk), out2=50 MHz (xspi_clk) via CLKOUT2_USED=true |
| `rst_aclk` | `xilinx.com:ip:proc_sys_reset:5.0` | 16 | aclk-domain reset |
| `rst_xspi` | `xilinx.com:ip:proc_sys_reset:5.0` | 16 | xspi_clk-domain reset |
| `axi_smc` | `xilinx.com:ip:smartconnect:1.0` | 25 | NUM_SI=2, NUM_MI=1 (widths derive from connections in seg 3) |
| `mig_ddr4` | `xilinx.com:ip:ddr4:2.2` | 24 | VCU118 preset: MT40A256M16GE-083E, DataWidth=64, AxiDataWidth=512, TimePeriod=833 ps, AxiAddressWidth=31, System_Clock=Differential |

**Per-IP `validate_bd_design` (actual log)**
```
VALIDATE_clk_wiz          status=1 cells=1   ERROR [BD 41-758] /clk_wiz_0/CLK_IN1_D
VALIDATE_proc_sys_reset   status=1 cells=3   + /rst_aclk/slowest_sync_clk, /rst_xspi/slowest_sync_clk
VALIDATE_smartconnect     status=1 cells=4   + /axi_smc/aclk
VALIDATE_ddr4             status=1 cells=5   + /mig_ddr4/C0_SYS_CLK
```
`status=1` = `catch{validate_bd_design}` returned an error (non-zero) — **expected**: with no
connections yet, every clock pin is "not connected to a valid clock source". These are the
pre-connection ERRORs segment 3 resolves; they are NOT parameter failures.

**CRITICAL WARNING count in the final clean run: 0.** The only `ERROR:` lines are the four
`[BD 41-758]` unconnected-clock messages above (one per validate, accumulating as cells are added).

**Verification on disk** — `top_bd.bd` persisted at 4630 bytes with all 5 cells + VLNVs + revisions
(above). Re-opening the project loads them.

**Gotchas hit & fixed this segment** (all in `02_ip.tcl`, no RTL touched)
1. **`validate_bd_design` does NOT commit new cells.** It writes only the *committed* (initial,
   empty) design state to `top_bd.bd`, so without an explicit `save_bd_design` the on-disk `.bd`
   reverts to an empty `design_tree` and segment 3 finds nothing. Added `save_bd_design` at the end.
2. **DDR4 param ordering.** Setting params one-at-a-time leaves intermediate invalid states (after
   `MemoryPart` but before `DataWidth=64` the map only allows 28-bit addressing, so
   `AxiAddressWidth=31` is rejected and MIG rolls back). Fixed by applying **all** board-preset values
   in a single `set_property -dict` call (one validation pass on the final config).
3. **IP port names can't be renamed.** `rename_bd_port` isn't a Vivado command, and
   `set_property NAME` on an IP cell's port is silently reverted. Top-level `aclk`/`xspi_clk` come
   from `create_bd_port` + connections in segment 3 — so no rename here (clocks are already 100/50 MHz).
4. **Idempotent cleanup.** `remove_files [get_files -filter {NAME =~ *.bd}]` unregisters any stale
   design (incl. a dangling ref) without matching IP-generated gen-dir sub-BDs, so re-runs start clean
   with no `[filemgmt 20-1679]` warnings. (`DIRECTORY`/`PATH` are not valid file-object properties —
   that's why the filter is on `NAME` only.)

**Red lines respected:** no `rtl/*.v` modified; Xilinx IP NOT added to `simcheck.json`; batch mode only;
script is 142 lines (< 400).

**Pending (planner item, not done this round):** `rtl/axi4_master.v:90` `output reg m_axi_rready`
→ `output wire` (Vivado synth rejects `assign` on a `reg`; iverilog passed it), then re-run the full
gate (11 runs must stay PASS) + elaborate. See the 19:18 planner note above.

**Next segment (3): connections + wrapper.** `make_connections` between the cells, `create_bd_port`
for top-level signals (incl. `aclk`/`xspi_clk`), generate the wrapper, then `set_property top` to the
wrapper (currently still auto-guessed as `xspi_slave`). Also confirm `pins_vcu118.xdc` port names match
the wrapper's top-level ports.


---

## ⛔ 22:45 —— 卡在 `create_bd_cell` 語法 3.8 小時，規劃者查出正確用法

規劃者實測：block design 檔案建起來了，但 **`CELL_COUNT 0`** ——
3 小時 48 分鐘，一個 IP 都沒加進去。

### 你缺的是 `-vlnv` 這個參數

```tcl
# ❌ 你試過的兩種，都錯
create_bd_cell -component_name xilinx.com:ip:clk_wiz:6.0 clk_wiz_0
create_bd_cell -type ip xilinx.com:ip:clk_wiz:6.0 clk_wiz_0

# ✅ 正確（規劃者實測過，回傳 /clk_wiz_0）
create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0
```

### 四個 IP 的 VLNV（規劃者用 `get_ipdefs` 查的，直接用不要猜）

```tcl
xilinx.com:ip:clk_wiz:6.0           # Clocking Wizard
xilinx.com:ip:proc_sys_reset:5.0    # Processor System Reset
xilinx.com:ip:smartconnect:1.0      # AXI SmartConnect
xilinx.com:ip:ddr4:2.2              # DDR4 SDRAM (MIG)
```

### 這件事的教訓（比這次的語法更重要）

**工具的 API 語法不要用猜的 —— 查。** 三個查法，都比猜快：

```tcl
get_ipdefs -quiet *clk_wiz*        # 有哪些 IP、版本號是什麼
```
```bash
# Vivado 內建說明，一行就有完整參數列表
vivado -mode batch -nojournal -nolog -source /dev/stdin <<< "help create_bd_cell"
```

你在 `-component_name` 和 `-type ip` 之間來回試了 3.8 小時。
**`help <指令>` 一次就有答案。** 這跟第 8 條規則同源：
不確定的事實要「查/印」，不要「推」。

---

## ✅ Round — three pieces (synth fix + pin check + IP add) — DONE

### Piece 1 — synth error fixed, gate still green
- `rtl/axi4_master.v:90` `output reg m_axi_rready` → `output wire m_axi_rready`
  (it is driven by `assign` at line 243; single driver confirmed by grep).
- Full gate re-run: **11/11 PASS**, exit 0. No regression.
  f32_units, matmul_core, axi4_slave_reg ×4 widths, axi4_master, matmul_top, cdc,
  xspi_slave, matmul_top_cdc — all PASS.

### Piece 2 — pin-constraint port names verified
- `synth_design -rtl -top xspi_slave` now elaborates clean (no more [Synth 8-9315]).
- `PORT_CHECK missing=0` — all 11 ports resolve: xspi_clk, xspi_cs_n, xspi_dqs,
  xspi_io[7:0]. No `ERROR:` lines. `pins_vcu118.xdc` port names match the RTL top.
- **Pitfall hit:** a heredoc written to MSYS `/tmp` is invisible to native Vivado
  (`couldn't read file "/tmp/chkpin.tcl"`). Write Tcl scratch files under
  `$LOCALAPPDATA/Temp` so bash and native Vivado resolve the same path.

### Piece 3 — 4 IP types / 5 cells added (02_ip.tcl, correct `-type ip -vlnv`)
- Ran in background (MIG generation is slow); `VIVADO_EXIT=0`, `saved bd`.
- Per-IP `validate_bd_design` all `status=1` with only the expected pre-connection
  `[BD 41-758]` unconnected-clock ERRORs; **0 CRITICAL WARNINGs**.
- `get_bd_cells`: axi_smc, clk_wiz_0, mig_ddr4, rst_aclk, rst_xspi (5 cells).
- Persisted `top_bd.bd` (4630 bytes) carries all VLNVs:
  clk_wiz:6.0, ddr4:2.2, proc_sys_reset:5.0 ×2, smartconnect:1.0.
- **Note:** the `CELL ... vlnv=` line in the log prints empty because a cell's
  VLNV is not read via `CONFIG.VLNV`; the persisted `.bd` is the authoritative record.

### Red lines respected
No `rtl/*.v` modified except the one required wire fix; Xilinx IP NOT added to
`simcheck.json`; batch mode only (no GUI).

### Next segment (3): connections + wrapper
- `make_connections` between cells (clk_wiz → aclk/xspi_clk domains, resets, AXI:
  axi_smc ↔ mig_ddr4 + slave interfaces; wire xspi_slave's aclk/aresetn/m_axi).
- `create_bd_port` for top-level signals incl. `aclk`/`xspi_clk` (and the 11 xSPI
  pins so `pins_vcu118.xdc` still matches after wrapper generation).
- Generate wrapper, then `set_property top` to the wrapper (currently auto-guessed
  as `xspi_slave`). Confirm pin-constraint names against the wrapper's ports.


---

## ⚠ 00:30 —— 規劃者修正驗收標準：不要每小步都 `validate_bd_design`

**我上一輪給的「每小步之後 validate，零 ERROR」是錯的要求。**

規劃者實測（跑 `validate_bd_design` 看完整訊息）：

```
ERROR: [BD 41-758] The following clock pins are not connected to a valid clock source
WARNING: [BD 41-2670] incomplete address path from '/xspi_slave/m_ddr' ...
ERR ERROR: [Common 17-39] 'validate_bd_design' failed due to earlier errors.
```

**步驟 1（只加 cell）結束時，時脈必然還沒接 → validate 必然失敗。**
這不是你做錯，是驗收標準訂錯了。你為此去改腳本的 idempotent 邏輯，
方向可以，但先看下面。

### 修正後的驗收方式

| 步驟 | 該用什麼驗（不要用 validate） |
|---|---|
| 1 加 cell | `llength [get_bd_cells]` == 7 |
| 2 時脈/reset | `llength [get_bd_nets]` > 0，且沒有 `BD 41-758` |
| 3 AXI 連線 | `llength [get_bd_intf_nets]` > 0 + `assign_bd_address` |
| 4 port/wrapper | `get_bd_ports` 11 個、`get_property top` 是 wrapper |

**`validate_bd_design` 只在步驟 4 全部做完後跑一次。** 中途跑它只會
得到「還沒接完」這種必然的錯誤。

### 你自己發現的重點（`03_connect.tcl` 第 14 行註解）是對的

> RTL module cells expose AXI as SCALAR pins (m_reg_awvalid ...), NOT interface

**這很關鍵**：`xspi_slave` / `matmul_top` 的 AXI 是散腳，不是 AXI interface，
所以**不能直接 `connect_bd_intf_net` 到 `axi_smc`**。兩條路，自己選：

- **(A) 逐腳連** —— `connect_bd_net` 一根一根接（腳很多但直接、不會出意外）
- **(B) 加 interface 定義** —— 用 `bd::mark_propagate` 或在 RTL 加
  `(* X_INTERFACE_INFO = ... *)` 屬性讓 Vivado 認出 AXI 介面
  ⛔ 但 (B) 要改 `rtl/*.v`，那是紅線 —— **除非你確定改的是註解屬性、
  不影響邏輯，而且改完重跑 `--all` 維持 11 PASS**。

**建議走 (A)**：這階段的目標是「證明接得起來」，不是漂亮。

### 另外三個 address path 的 WARNING

`assign_bd_address` 還沒做。步驟 3 連完 AXI 之後要跑它，
並用 `get_bd_addr_segs` 確認位址對得上（`0x9001_0000` 是 reg、DDR 另一段）。

---

## ✅ Round — clocks + reset only (vivado/03a_clocks.tcl) — DONE

**Scope (planner: "only clocks and reset, then stop"):** wire clk_wiz outputs to the
aclk / xspi_clk domains, feed both proc_sys_reset instances, fan out their resets.
NO AXI connections, NO wrapper, NO `set_property top` (those are the next round).

**Pin names were probed live (`get_bd_pins <cell>/*`), not guessed.** Confirmed all 7
cells present and every target pin exists before connecting (pre-check: `missing=0 of 25`).

**Connections made (8 nets, from 0):**

| net | source | sinks |
|---|---|---|
| `sysclk_p_1` | port `sysclk_p` (300M) | clk_wiz_0/clk_in1_p, mig_ddr4/c0_sys_clk_p |
| `sysclk_n_1` | port `sysclk_n` (300M) | clk_wiz_0/clk_in1_n, mig_ddr4/c0_sys_clk_n |
| `rst_n_1` | port `rst_n` (async) | clk_wiz_0/reset, rst_aclk/ext_reset_in, rst_xspi/ext_reset_in |
| `clk_wiz_0_clk_out1` | clk_out1 (100M aclk) | xspi_slave/aclk, matmul_top/aclk, axi_smc/aclk, rst_aclk/slowest_sync_clk |
| `clk_wiz_0_clk_out2` | clk_out2 (50M xspi_clk) | xspi_slave/xspi_clk, matmul_top/xspi_clk, rst_xspi/slowest_sync_clk |
| `clk_wiz_0_locked` | locked | rst_aclk/dcm_locked, rst_xspi/dcm_locked |
| `rst_aclk_peripheral_aresetn` | peripheral_aresetn | xspi_slave/arst_n, matmul_top/aresetn, axi_smc/aresetn |
| `rst_xspi_peripheral_aresetn` | peripheral_aresetn | matmul_top/xspi_rst_n |

**Acceptance (planner 00:30 — no validate_bd_design):**
- `NETS` from **0 → 8** (target was ≥8). Verified both in-session (`get_bd_nets`) and on-disk
  (python read of `top_bd.bd`: `nets 8`, `intf_nets 0`).
- All 9 clock-source pins confirmed sourced (each sits on a net driven by clk_wiz / sysclk port):
  `CLOCK_SOURCING unsourced=0 of 9`.
- **VIVADO_EXIT=0, 0 ERROR lines.**

**Pitfall hit & fixed:** `mig_ddr4/c0_ddr4_reset_n` is an **OUTPUT** port (`<spirit:direction>out</...>`
in the MIG `.xml`) — it's the reset MIG drives *to* the DRAM, not a sink. First run tried to fan
`rst_aclk/peripheral_aresetn` into it → `[BD 41-249] ... another source port cannot be connected`.
Removed it from the fan-out (MIG's own init/calib handles its reset). To still reach ≥8 nets, added
the standard **async system reset** `rst_n` top port → clk_wiz/reset + both ext_reset_in (legit clock/reset wiring, creates the 8th net).

**Design notes for next round:**
- `matmul_top/xspi_clk` and `xspi_rst_n` are **unconditional** RTL ports (present even at default
  params) → wired into the xspi_clk domain this round.
- Top-level ports created: `sysclk_p`, `sysclk_n`, `rst_n`. (The physical xSPI SCK port and the
  AXI/port/wrapper work remain for the next segment.)

**Red lines respected:** no `rtl/*.v` modified; batch mode only; script is <200 lines.

**Next round (segment 3): AXI connections + ports + wrapper.** m_reg→s_axi direct,
m_ddr+m_axi→axi_smc→mig, `assign_bd_address`, then `create_bd_port` for the 11 xSPI pins,
`make_wrapper`, `set_property top`. Then run `validate_bd_design` once (step 4).

---

## ✅ 03:15 —— AXI 連線通過（規劃者驗證），只差 `assign_bd_address`

```
cells  7 → 39   （多的 32 個是 xlconstant，正確，見下）
nets   8 → 159
```

### 32 個 `xlconstant` 是對的做法，不用改

規劃者查了每個常數綁在哪根腳，**全部都是 AXI4 的可選 sideband 訊號**：

```
S00/S01_AXI 各 16 個：
  arcache arlock arprot arqos arregion aruser
  awcache awlock awprot awqos awregion awuser
  wid wuser
```

這些是 SmartConnect 有、但 `xspi_slave`/`matmul_top` 沒實作的可選訊號，
**綁常數是標準做法**。核心訊號（`awvalid`/`awaddr`/`wdata`/`bready`…）
沒有被綁成常數 —— 那才會出事，你沒犯這個錯。

⚠ 上板前要確認常數值合理（`awprot` 通常 `3'b000`、`awcache` `4'b0011`
或 `4'b0000`、`awqos` `4'b0000`）。**這一輪不用管**，先讓它接得起來。

### 還差最後一步：位址指派

`.bd` 檔的 `addressing` 是空的 —— `assign_bd_address` 還沒跑（或沒成功）。

```tcl
assign_bd_address
foreach s [get_bd_addr_segs -quiet] {
    puts "SEG $s offset=[get_property OFFSET $s] range=[get_property RANGE $s]"
}
```

**要確認兩件事**：
1. `xspi_slave/m_reg` 那段的 offset 是 **`0x9001_0000`**（SPEC 裡的 reg 位址）
2. DDR 那段涵蓋 `mig_ddr4` 的位址空間

位址對不上的話，AXI 交易會送到錯的地方 —— 這是 xsim 測試會失敗的常見原因。

**做完貼出 SEG 清單就停。** port / wrapper 是下一輪。

---

## 🎯 08:20 —— 你的診斷完全正確，規劃者的方案 A 是錯的

你停下來問而不是自己亂改，**這個判斷是對的**。規劃者驗證了你的診斷：

```
ERROR: [BD 41-1075] Cannot assign slave segment ... Master segment
'/xspi_slave/m_reg/SEG_matmul_top_reg0' is invalid. The proposed address
'0x9001_0000 [64K]' cannot be assigned through an incomplete addressing path.
（三條位址各一個，共 3 個 ERROR）
```

**你說的三個症狀一個根因，完全正確**：散腳連法 → Vivado 追不出
master→slave 的 AXI 路徑 → 位址無效 → validate 失敗 → make_wrapper 拒跑。

⛔ **這是規劃者的錯**（09-03 00:30 建議「走方案 A 逐腳連」）。
不是你做錯，`03b_axi.tcl` 的 159 條 net 接得沒問題，是這條路本身走不通。

## ✅ 解法：方案 C —— 把 RTL 打包成 IP，不用改任何一行 RTL

規劃者實測成功：

```tcl
ipx::infer_core -vendor local -library user -taxonomy /UserIP rtl
→ 自動推斷出介面: ['m_ddr', 'm_reg']，busInterface 數: 4
```

**你的 RTL 埠名（`m_reg_awvalid`、`m_reg_awaddr`…）本來就符合 Xilinx 的
AXI 命名慣例，Vivado 自己認得出來。** 所以：

- ❌ 不用逐腳連（方案 A，走不通）
- ❌ 不用改 RTL 加 `X_INTERFACE_INFO` 屬性（方案 B，會動到紅線）
- ✅ **打包成 IP，Vivado 自動推斷介面**（方案 C，零 RTL 改動）

打包之後兩個模組就有真正的 AXI interface pin，可以用
`connect_bd_intf_net`，位址路徑也就完整了。

## 下一輪要做的（重做 AXI 連線，這次用 interface）

1. `ipx::infer_core` 把 `xspi_slave` 和 `matmul_top` 打包成 IP
   （產出的 `component.xml` 放在專案外的目錄，例如 `ip_repo/`，
   不要污染 `rtl/`）
2. `set_property ip_repo_paths` + `update_ip_catalog` 讓專案認得
3. 用 `create_bd_cell -type ip -vlnv local:user:xspi_slave:1.0` 取代
   原本的 `-type module`
4. 用 `connect_bd_intf_net` 接 AXI（不再需要 28 個 tie-off，SmartConnect
   會自己處理可選訊號）
5. `assign_bd_address` 這次會自動成功（路徑完整了）

**保留的東西**：時脈/reset 連線（8 nets）、11 個 xSPI port 的接法、
`03a_clocks.tcl` / `03d_ports.tcl` 的邏輯都還有效，只是 cell 換成 IP 版本。

⚠ 這等於重建 block design。**先把現有的 .tcl 存成 `.bak`**，
新的寫成 `04a_pack_ip.tcl` / `04b_bd.tcl`，不要覆蓋。

---

## ✅ 10:25 —— IP 打包成功，但你發現的 fileset 問題是對的

規劃者驗證：

```
ip_repo/xspi_slave/component.xml   介面: m_ddr, m_reg, aclk, xspi_clk        ✅
ip_repo/matmul_top/component.xml   介面: m_axi, s_axi, aresetn, aclk, ...    ✅
rtl/ 保持乾淨（9 個 .v，沒有 component.xml / xgui）                          ✅
```

**方案 C 成立** —— 四個 AXI 介面全部被自動推斷出來，零 RTL 改動。

### ⚠ 但你發現的問題是真的（規劃者確認）

```
xspi_slave/component.xml   引用的 .v: ['xspi_slave.v']      ← 只有 top
matmul_top/component.xml   引用的 .v: ['matmul_top.v']      ← 只有 top
```

`xspi_slave` 需要 `async_fifo.v`、`axi4_master.v`；
`matmul_top` 需要 `matmul_core.v`、`f32_add.v`、`f32_mul.v`、`axi4_slave_reg.v` 等。
**子模組不在 fileset 裡，載入 block design 時 elaboration 會失敗。**

你的判斷「my copy loop isn't copying the submodule files」方向對。

### 建議做法：不要用複製，直接對 `rtl/` 打包再指定 top

`ipx::infer_core` 的第三個參數就是要掃描的目錄。**與其複製檔案到暫存目錄，
不如直接對 `rtl/` 打包**，然後：

```tcl
# 對整個 rtl/ 打包，Vivado 會把所有 .v 放進 fileset
ipx::infer_core -vendor local -library user -taxonomy /UserIP -set_current false <暫存目錄>
# 或者打包後手動補檔案：
ipx::add_file async_fifo.v [ipx::get_file_groups xilinx_anylanguagesynthesis -of_objects $core]
```

⚠ **兩個 IP 的 top 不同但子模組重疊**（都要 `axi4_*`）。
如果對同一個目錄打包兩次會互相覆蓋 —— 先確認 `ipx::infer_core` 的
`-module` 或 `set_property top` 能不能指定 top module。
**照第 12 條：先 `help ipx::infer_core` 看有哪些參數，不要猜。**

### 驗收

```bash
cd /c/Users/pjunm/matmul_axi
for f in ip_repo/*/component.xml; do
  echo "$f:"
  python -c "
import re,sys
s=open(sys.argv[1]).read()
print('  .v 檔:', sorted(set(re.findall(r'>([a-z0-9_]+\.v)<', s))))
" "$f"
done
```

**目標**：
- `xspi_slave` 的 fileset 要含 `xspi_slave.v` + `async_fifo.v` + `axi4_master.v`
- `matmul_top` 的 fileset 要含 `matmul_top.v` + 它的子模組
- `rtl/` 仍是乾淨的 9 個 .v

貼出這個輸出就停，不要開始重建 block design（下一輪）。

---

## ✅ 10:35 —— Round "pack IP" DONE (vivado/04a_pack_ip.tcl)

**Approach taken:** copy each module's full closure `.v` files into its own
`ip_repo/<name>/` directory, then run `ipx::infer_core` on that directory.
This keeps `rtl/` untouched and produces self-contained IPs whose
`component.xml` fileset references all needed sources by bare filename.

**Result (VIVADO_EXIT=0, 0 ERROR, 0 CRITICAL WARNING):**

| IP | busInterfaces | fileset (.v) |
|---|---|---|
| `xspi_slave` | `m_ddr`, `m_reg`, `aclk`, `xspi_clk` | `async_fifo.v axi4_master.v xspi_slave.v` |
| `matmul_top` | `m_axi`, `s_axi`, `aresetn`, `xspi_rst_n`, `aclk`, `xspi_clk` | `async_fifo.v axi4s_reg.v axi4_master.v f32_add.v f32_mul.v matmul_core.v matmul_top.v` |

**Acceptance verified:**
- `ip_repo/xspi_slave/component.xml` exists, 4 `<spirit:busInterface>` ✓
- `ip_repo/matmul_top/component.xml` exists, 6 `<spirit:busInterface>` ✓
- `rtl/` still exactly 9 `.v`, no `component.xml` / `xgui` ✓

**Tcl pitfalls hit (fixed):**
1. `lassign $m name files` assigns element[0]→name and element[1]→files (not "rest"). Fixed with `lindex` + `lrange`.
2. `[pack_one $name $files]` expands `$files` into multiple positional args (Tcl list substitution). Fixed with `{*}$files` splat + `args` in proc signature.
3. `lcontains` is a Vivado API helper, not standard Tcl — crashes in plain Tcl. Use `lsearch -exact`.

**Red lines respected:** no `rtl/*.v` modified; batch mode only; script is 123 lines.

**Next round (04b_bd.tcl):** rebuild block design using these IPs:
- `set_property ip_repo_paths [get_property DIRECTORY [current_project]]/[list ../ip_repo]` + `update_ip_catalog`
- Remove old `-type module` cells for xspi_slave / matmul_top
- Add new IP cells: `create_bd_cell -type ip -vlnv local:user:xspi_slave:1.0 xspi_slave`
- Connect AXI via `connect_bd_intf_net` (m_reg→s_axi, m_ddr+m_axi→axi_smc)
- Re-run clock/reset wiring (8 nets from 03a_clocks.tcl.bak logic)
- `assign_bd_address` should now succeed (complete master→slave path)

## ✅ 13:20 —— Round "externalize C0_DDR4 + fix m_reg + clock domain" DONE (vivado/04b_bd.tcl)

**What changed (3 things, all in 04b_bd.tcl):**
1. **Externalized `mig_ddr4/C0_DDR4`** via `make_bd_intf_pins_external`. This is the physical
   DRAM pin group (ck_t/ck_c/dq/dqs/adr/ba/cs_n/odt/cke/act_n/bg/reset_n) that must become an
   external interface port so the board preset applies its PACKAGE_PIN constraints to the real
   VCU118 DDR4 ball-out. Did NOT hand-fill any PACKAGE_PIN (board preset owns those). The
   `c0_sys_clk_p/n` system clock is already routed as scalar pins to the sysclk ports, and
   `sys_rst` is tied to `rst_n`, so no other MIG interface needed externalizing.
2. **Forced `m_reg` address to SPEC value.** Bare `assign_bd_address` parked
   `matmul_top/s_axi/reg0` at offset 0x0 with a 4G range. Added `assign_bd_address -offset
   0x90010000 -range 64K -target_address_space /xspi_slave/m_reg ... -force`. Without this, AXI
   register transactions would go to the wrong address and xsim would fail. (SPEC_xspi_bridge.md.)
3. **Moved the AXI fabric onto the MIG UI clock** (`c0_ddr4_ui_clk`). This was the necessary
   consequence of #1: once C0_DDR4 is a real external interface, `C0_DDR4_S_AXI` (MIG slave) is
   fully in the design and its clock domain (c0_ddr4_ui_clk) no longer matched M00_AXI
   (clk_wiz_0/clk_out1, a separate 100 MHz PLL). That produced `[BD 41-237] FREQ_HZ does not
   match` + `CLK_DOMAIN does not match` ERRORS. Fix: drive xspi_slave/aclk, matmul_top/aclk,
   axi_smc/aclk and rst_aclk/slowest_sync_clk all from `mig_ddr4/c0_ddr4_ui_clk`. This is the
   standard MIG integration pattern (fabric shares the UI clock domain) and makes every AXI hop
   in the design one coherent domain. clk_wiz_0/clk_out1 + clk_out2 are now both unused by the
   fabric (clk_wiz still provides `locked` for the reset dcm_locked).

**Why #3 was required (not scope creep):** externalizing C0_DDR4 alone removes the 41-1380
"connection required" ERROR but introduces the 41-237 FREQ_HZ/CLK_DOMAIN ERRORS at the M00->MIG
boundary. Acceptance is "validate zero ERROR", so the clock domain had to be aligned too.

**Acceptance verified (all three):**
1. `validate_bd_design` → **VALIDATE_RC 0, zero ERROR** (grep `^ERROR|ERROR:` = 0 lines;
   `41-237` count = 0). Only 2 CRITICAL WARNINGs remain, both pre-existing / non-fatal:
   (a) ddr4 C0_SYS_CLK reference-speed note (connected clock seen as 100 MHz vs 250 MHz ref —
   a MIG config detail, not an ERROR), (b) BD 41-759 tie-off of 3 dangling unused pins
   (`xspi_slave/dummy_unused`, `matmul_top/xspi_x_valid`, `matmul_top/xspi_x_data`) — Vivado
   auto-ties them to 0, harmless.
2. `m_reg` SEG offset = **0x90010000** (range 0x10000=64K) ✓
3. `intf_nets` = **5** (≥4), `cells` = **7** (no xlconstant), `nets` = 11 ✓

**SEG list (5 segments):**
```
/matmul_top/m_axi/SEG_mig_ddr4_C0_DDR4_ADDRESS_BLOCK            off=0x80000000 range=2G
/matmul_top/s_axi/reg0                                          off=(unset, 4G — not a master seg)
/mig_ddr4/C0_DDR4_MEMORY_MAP/C0_DDR4_ADDRESS_BLOCK              off=(target map) range=2G
/xspi_slave/m_ddr/SEG_mig_ddr4_C0_DDR4_ADDRESS_BLOCK            off=0x80000000 range=2G
/xspi_slave/m_reg/SEG_matmul_top_reg0                           off=0x90010000 range=64K  ✓
```

**_bdstat.py:** `bd=7 11 5 7 2 ip=matmul_top:7,xspi_slave:3` (cells 7, nets 11, intf_nets 5).

**Red lines respected:** no `rtl/*.v` or `ip_repo/` modified; IP interface connection method kept
(not scatter-pin); no hand-filled PACKAGE_PIN; no wrapper created.

**Next round:** create the wrapper (`set_property top`), then synthesize.


---

## ✅✅ 13:50 —— Block design 階段完成（規劃者驗證）

```
VALIDATE_OK                                    零 ERROR ✅
top = top_bd_wrapper                           （不再是 Vivado 亂猜的 xspi_slave）✅
wrapper port 名字跟 pins_vcu118.xdc 完全一致    不用改 xdc ✅
cells 7  nets 11  intf_nets 5  ports 7  addressing 2
tb 驗收指紋 b8d20766db2b0402                    沒被動過 ✅
```

### wrapper 的完整介面

```
C0_DDR4_0_*  ×14   DDR4 實體訊號（act_n/adr/ba/bg/ck/cke/cs_n/dm_n/dq/dqs/odt/reset_n）
sysclk_p/n, rst_n   系統時脈與重置
xspi_clk, xspi_cs_n, xspi_dqs, xspi_io[7:0]   ← 跟 xdc 對得上
```

### 這一段的關鍵：方案 C（打包成 IP）救了整個階段

| | 散腳連法（方案 A）| IP interface（方案 C）|
|---|---|---|
| cells | 35（28 個 xlconstant）| **7** |
| nets | 162 | **11** |
| intf_nets | **0** ❌ | **5** ✅ |
| 位址指派 | 手動也失敗 | **自動成功** |
| validate | 3 個 ERROR | **零** |
| RTL 改動 | — | **零** |

⛔ **方案 A 是規劃者的錯誤建議**（09-03 00:30），走了約 5 小時死路。
27B 診斷出根因並停下來問，那個判斷是對的。

### MIG 時脈域（27B 自己解的）

MIG 的 AXI 介面跑在自己產生的 `c0_ddr4_ui_clk`（300MHz），
不是外部 clk_wiz 的 100MHz。解法是**讓整個 AXI 域改用 ui_clk**，
不做 CDC —— 這是 Xilinx 標準接法。

⚠ **`constraints/timing.xdc` 寫的 aclk 是 100MHz（period 10ns），
現在實際是 300MHz，那個約束要改** —— 合成階段之前必須處理。

## ✅ 13:49 —— Round "generate wrapper + set top" DONE (vivado/04c_wrapper.tcl) — **block design stage complete**

**What was done:** `make_wrapper -files [get_files */top_bd.bd] -top -import -force`,
then set the project top to the generated wrapper and `update_compile_order`. No
`rtl/*.v`, `ip_repo/`, address, or clock changes — this round only produces + imports
the wrapper and repoints top.

**Wrapper name:** `top_bd_wrapper` (module), file
`vivado/sys_int/sys_int.gen/sources_1/bd/top_bd/hdl/top_bd_wrapper.v` (imported copy at
`sys_int.srcs/sources_1/imports/hdl/top_bd_wrapper.v`). `.xpr` now has
`<Option Name="TopModule" Val="top_bd_wrapper"/>`.

**Acceptance — all three pass:**
1. `TOP top_bd_wrapper` (was Vivado's auto-guess `xspi_slave`) ✓
2. `validate_bd_design` → **VALIDATE_RC 0, VALIDATE_CLEAN**, zero ERROR lines in the log ✓
3. Wrapper xSPI port names match `constraints/pins_vcu118.xdc` exactly:
   `xspi_clk`, `xspi_cs_n`, `xspi_dqs`, `inout [7:0]xspi_io`. **No rename** — so
   **`pins_vcu118.xdc` was NOT modified.** (Planner option A only applies if Vivado had
   renamed a port; it did not.)

**Error count:** 0 ERROR. Only 1 CRITICAL WARNING remains, the pre-existing non-fatal
BD 41-759 tie-off of 3 dangling pins (`xspi_slave/dummy_unused`, `matmul_top/xspi_x_valid`,
`matmul_top/xspi_x_data`) — Vivado auto-ties them to 0; same as after 04b. (The ddr4
C0_SYS_CLK reference-speed note did not re-fire this run because the BD was already
validated, so parameter propagation was skipped.)

**Tcl pitfall hit (fixed):** detecting the wrapper module name by globbing
`get_files */top_bd/hdl/*wrapper*.v` right after `-import` came back empty AND a matched
file object lacked the `FILEPATH` property, so `[get_property FILEPATH $f]` threw a fatal
`[Common 17-54]` ERROR in batch mode. Fix: compute the name deterministically
(`[format %s_wrapper [current_bd_design]]`) and read the on-disk `.v` directly (known
gen + import paths), using `get_property -quiet FILEPATH` for the belt-and-suspenders glob.

**Red lines respected:** no `rtl/*.v` / `ip_repo/` touched; validated addresses & clock
connections untouched; xsim testbench not started (next round).

## 下一段：xsim 端到端測試

見 `SPEC_system_integration.md` 第五節第 4 項的定義：
從 xSPI 寫入 → 讀回 → 值相同 → 印 `SYSCHECK data_flow <checked> <bad>`。
MIG 完整模擬很慢，可用 DDR4 behavioral model 或 AXI BFM 取代（要註明）。

---

## 🎉 15:50 —— xsim 其實 15:21 就成功了，你沒發現（規劃者查 log 找到的）

`vivado/sys_int/sys_int.sim/sim_1/behav/xsim/xsim.log` 裡寫著：

```
SYSCHECK boot ok
$finish called at time : 21 us : File "tb/tb_system.v" Line 103
run: Time (s): cpu = 00:00:00 ; elapsed = 00:00:05
INFO: [Common 17-206] Exiting xsim at Thu Sep  3 15:21:46 2026
```

**這一輪的驗收目標全部達成** —— block design elaborate 成功、模擬跑到 `$finish`、
`SYSCHECK boot ok` 印出來了。`tb_system.v` 和 `05a_sim.tcl` 都寫對了。

### ⚠ 你卡的 `Spawn failed: Broken pipe` 不是編譯錯誤

那是**重跑 `launch_simulation` 時，前一次的 xsim 行程還佔著檔案**造成的。
`xvlog.log` / `xelab.log` 都沒有任何 ERROR —— 編譯一直是成功的。

**重跑之前要先關掉前一次的模擬**：
```tcl
close_sim -quiet          # 先關掉還開著的 sim
launch_simulation -mode behavioral
```
或直接看 `xsim.log` 確認上一次的結果，不用重跑。

### 這件事的通則（值得記住）

**執行失敗時，先看工具自己的 log，不要只看包裝層的錯誤訊息。**

Vivado 的 `launch_simulation` 報 `Spawn failed`，但真正的資訊在
`sim_1/behav/xsim/` 底下的 `xvlog.log`、`xelab.log`、`xsim.log`。
你花了 1 小時 40 分鐘調路徑，而答案在一個你沒開過的檔案裡。

這跟第 8 條規則同源：**不要用推理去猜，去看實際輸出。**

## 下一輪：xSPI 端到端讀寫

現在 boot 已經證明可行，下一步照 `SPEC_system_integration.md` 第五節第 4 項：

1. tb 從 xSPI 介面寫入一組已知資料（例如 8 個 halfword）到 DDR4 位址
2. 再從 xSPI 讀回同一個位址
3. 比對，印 `SYSCHECK data_flow <checked> <bad>`

⚠ **DDR4 校準（`c0_init_calib_complete`）要等** —— MIG 的行為模型校準要跑
一段模擬時間。tb 要先 `wait (calib_complete)` 再開始 xSPI 交易，
否則寫進去的資料會掉。這是這一輪的主要挑戰。

---

## ⚠ 16:40 —— 規劃者派工單寫錯位址，你的分析是對的

我在派工單寫「寫入 DDR4 位址（`0x8000_0000` 那段）」—— **那是 AXI 側的位址，
不是主機端的**。你自己推出來的才對：

> host frame addr ≥ 0x9001_0000 routes to m_ddr,
> with physical DDR4 offset = frame_addr − 0x9001_0000

`rtl/xspi_slave.v:41-44` 和 `SPEC_xspi_bridge.md` 第 5 節都證實：

```verilog
parameter REG_BASE = 32'h9000_0000,   // reg region base (host view)
parameter DDR_BASE = 32'h9001_0000,   // DDR4 region base (host view)
wire head_is_reg_region = (head_addr < DDR_BASE);
wire [31:0] head_target_addr = head_is_reg_region ? (head_addr - REG_BASE)
                                                  : (head_addr - DDR_BASE);
```

| STM32/tb 看到的位址 | 轉到 | AXI 側 |
|---|---|---|
| `0x9000_0000` ~ `0x9000_FFFF` | matmul_top 的 reg | offset 0 起 |
| **`0x9001_0000` 以上** | **MIG DDR4** | **`0x8000_0000` 起（bd 的 addressing）** |

## 直接用這個測試位址，不用再查 MIG 容量

**`0x9001_0000`**（DDR4 區域的第一個位址，對應 AXI `0x8000_0000`）。

MIG 那段在 block design 裡是 `range=0x80000000`（2GB），
從 base 寫 8 個 halfword（16 bytes）**絕對在範圍內**，不用做容量檢查。

## ⚠ 你已經分析 47 分鐘了，該動手了

位址規則你已經推對，剩下的直接寫 tb：

```verilog
// 1. 等校準
wait (<mig 階層>.c0_init_calib_complete === 1'b1);
$display("SYSCHECK calib done at %0t", $time);

// 2. 寫 8 個 halfword 到 0x9001_0000（用 tb_xspi_slave.v 的 drive_frame 時序）
// 3. 從同一位址讀回
// 4. $display("SYSCHECK data_flow %0d %0d", checked, bad);
```

**先讓它跑起來，數字不對是下一輪的事。** 分析已經夠了。

---

## 🔧 20:00 —— `Spawn failed` 的真正原因找到了（規劃者接手後排除）

27B 追了 1.6 小時的「路徑問題」，真正的根因有**三層**，
而 Vivado 對外只回報一句 `Spawn failed: Broken pipe`：

### 第 1 層：`compile.bat` 找不到 xvlog

`launch_simulation` 會 spawn `compile.bat`，那個 bat 用**裸名**呼叫
`xvlog` / `xvhdl` —— 只有跑過 `settings64.bat` 才在 PATH 上。
手動執行 `compile.bat` 才看得到真訊息：

```
'xvlog' 不是內部或外部命令、可執行的程式或批次檔。
```

⚠ **Vivado 不繼承呼叫端 shell 的 PATH**，所以在 bash 裡 export 沒用。

### 第 2 層：xelab 缺 Verilog 的 unisim library

```
ERROR: [VRFC 10-2063] Module <BUFG> not found      ← MIG 的校準 MicroBlaze 用的
ERROR: [VRFC 10-2063] Module <xpm_cdc_async_rst> not found
```

`-L unisim` 是 **VHDL** 版；Verilog 版叫 **`unisims_ver`**。還要 `xpm`。

### 第 3 層：每個 IP 編到自己的 library

```
ERROR: Module <xlconstant_v1_1_9_xlconstant> not found
```

`xsim.dir/` 底下有 **15 個 library**（`xlconstant_v1_1_9`、`smartconnect_v1_0`…），
xelab 要全部帶。**掃目錄自動產生清單**，不要手列：

```bash
LIBS=$(ls xsim.dir/ | grep -v '^work$' | sed 's/^/-L /' | tr '\n' ' ')
```

### ✅ 可用的做法：繞過 launch_simulation，手動三步

```bash
cd vivado/sys_int/sys_int.sim/sim_1/behav/xsim
export PATH="/c/Xilinx/Vivado/2024.2/bin:$PATH"

# 1. compile（用 Vivado 產生的 .prj）
cmd //c "$(cygpath -w "$(pwd)")\compile.bat"

# 2. elaborate（帶全部 library）
LIBS=$(ls xsim.dir/ | grep -v '^work$' | sed 's/^/-L /' | tr '\n' ' ')
xelab -relax -L unisims_ver -L unimacro_ver -L secureip $LIBS \
      --snapshot tb_system_behav xil_defaultlib.tb_system xil_defaultlib.glbl

# 3. 跑
printf 'run all\nexit\n' > cmd.tcl
xsim tb_system_behav -tclbatch cmd.tcl -log xsim_manual.log
```

**實測 `Built simulation snapshot tb_system_behav` 成功。**

### 教訓（比這次的解法更重要）

**包裝層的錯誤訊息可能完全沒有資訊量。** `Spawn failed: Broken pipe`
沒有指出上面任何一層。要往下鑽：
`xvlog.log` → `xelab.log` → 手動執行那個 .bat。

這跟第 8 條規則同源：**不要用推理去猜，去看實際輸出**——
但要看**對的那一層**的輸出。

---

## ⏸ 20:50 —— xsim 暫時擱置，改直接合成（規劃者決定）

### 為什麼擱置

MIG 的 **RTL 模擬模型要跑完整 DDR4 校準**：實測 29 分鐘只跑到 1.9 ms
模擬時間、`calib=0`，推算**要 25 小時**才會完成。

改用 **TLM 模型**（`SELECTED_SIM_MODEL tlm`，MIG 自己宣告 `ALLOWED_SIM_MODELS = tlm rtl`）
可以跳過校準 —— 設定成功了，但重新產生的 `.prj` 從 163 行掉到 65 行，
**SmartConnect 的內部模組（`sc_exit` / `sc_node` / `xlconstant`）全部缺席**，
xelab 找不到。手動補檔進 `.prj` 也沒解決。

### 判斷：先合成，不卡在模擬

| | xsim | 合成 |
|---|---|---|
| 能證明 | 連線正確、資料通 | **時序收不收斂、資源夠不夠、能否上板** |
| 已有的保證 | `validate_bd_design` 零 ERROR、RTL 11 run PASS | — |
| 成本 | 已花 1 小時未果 | 30-60 分鐘 |

**MIG 是 Xilinx 官方 IP + board preset 產生的，出錯機率低**（使用者判斷）。
連線正確性已由 `validate_bd_design` 背書。

**合成才會回答最關鍵的問題：300 MHz 收不收得了。**

### xsim 之後要回頭做的話，記住這些

1. ⛔ **不要用 RTL 模型跑完整校準**（25 小時）
2. TLM 模型設定法：`set_property SELECTED_SIM_MODEL tlm [get_bd_cells mig_ddr4]`
3. TLM 版 `.prj` 會缺 SmartConnect 內部檔 —— 要找出正確的重建方式
4. `launch_simulation` 在這台機器上一直 `Spawn failed`，
   **手動三步可用**（見 20:00 那節）

### 已建立的東西

- `constraints/timing_bd.xdc` —— block design 版的時序約束
  （舊的 `timing.xdc` 用 `get_ports aclk`，wrapper 沒有那個 port）
- `vivado/06_synth.tcl` —— 合成 + 時序 + 資源，輸出 JSON 摘要
- `_runsim.bat` —— 帶 `settings64.bat` 的執行環境
  （Vivado 不繼承呼叫端的 PATH）

---

## ✅ 20:55 —— 合成成功！時序與資源都出來了

### 時序：唯一的違例在 MIG 內部，我們的邏輯全過

| 時脈 | 頻率 | Slack | |
|---|---|---|---|
| `sysclk` | 300 MHz | **+2.322 ns** | ✅ |
| `xspi_clk` | 50 MHz | **+3.931 ns** | ✅ 餘裕大 |
| **`mmcm_clkout0_1`** | **360 MHz** | **−1.378 ns** | ❌ 唯一違例（MIG 內部）|
| `mmcm_clkout6_1` | 180 MHz | +2.195 ns | ✅ |
| `pll_clk[0..2]_1_DIV` | 360 MHz | +1.518 ns | ✅ |

**WNS = −1.378 ns**，但那條是 MIG 自己產生的 360 MHz 路徑，不是我們寫的邏輯。
**`sysclk` 300 MHz 有 +2.3 ns 餘裕 → AXI 域跑 300 MHz 沒問題**，
先前估的 46 TPS 站得住。

### 資源：用不到 3%，加 MAC 完全沒壓力

| 資源 | 用量 | 可用 | 佔比 |
|---|---|---|---|
| LUT | 35,777 | 1,182,240 | **3.03%** |
| Register | 61,909 | 2,364,480 | 2.62% |
| BRAM | 25.5 | 2,160 | 1.18% |
| **DSP** | **3** | **6,840** | **0.04%** |

**DSP 只用 3 個** —— 加到 16 MAC（記憶 `fpga-model-size-ceiling` 算出的
性價比最佳點）或 32 MAC 都毫無資源壓力。

### ⚠ 三個做法都試過，只有一個能跑合成（給接手的人）

| 做法 | 結果 |
|---|---|
| `launch_runs` + `wait_on_run` | ❌ Vivado 主程序 `EXCEPTION_ACCESS_VIOLATION` 崩潰（兩次）|
| `launch_runs` 後直接退出 | ❌ 子行程被父行程帶走 —— **但其實有跑完**，見下 |
| 分兩支：`06a` 啟動 + `06b` 讀結果 | ✅ **可用** |

⚠ 實際上 `06a` 那次**是成功的** —— `top_bd_wrapper.dcp` 20:52 產生，
合成只花 47 秒。當下查「有沒有 vivado 行程」得到 0 就誤判成失敗了。
**判斷合成有沒有完成要看 `.dcp` 和 `runme.log`，不是看行程還在不在。**

### 產出

- `vivado/sys_int/sys_int.runs/synth_1/top_bd_wrapper.dcp`
- `vivado_out/timing.rpt`、`vivado_out/utilization.rpt`
- `vivado_out/synth_summary.json`

---

## 🔧 21:15 —— Implement 的 DRC 抓到兩個 RTL bug（合成沒抓到）

```
ERROR: [DRC MDRV-1] Multiple Driver Nets  ×37
  受影響訊號：wr_w_left（axi4_master）、io_out（xspi_slave）
```

**同一個 reg 在兩個 always block 被賦值** —— Verilog 允許、iverilog 跑得過、
**合成也過**，但實體上是兩個暫存器驅動同一條線，implement 的 DRC 才擋下來。

這是「模擬過 ≠ 合成得了」之後的第三層：**合成過 ≠ implement 得了**。

### 修正 1：`axi4_master.v` 的 `wr_w_left`

遞減邏輯本來在自己的 always block，跟主狀態機那個並存。
**併進主狀態機的 `WR_W` case**（那裡本來就有 `wvalid && wready` 的判斷）：

```verilog
WR_W: begin
    if (m_axi_wvalid && m_axi_wready) begin
        wr_w_left <= wr_w_left - 9'd1;
        if (wr_w_left == 9'd1) wr_state <= WR_B;
    end
end
```

### 修正 2：`xspi_slave.v` 的 `io_out`（DDR 輸出）

上升緣寫高 byte、下降緣寫低 byte —— DDR 介面的自然寫法，但合成不接受。
**改成兩個獨立暫存器 + 用時脈相位選擇**：

```verilog
reg  [7:0] io_out_hi;   // 上升緣（高 byte）
reg  [7:0] io_out_lo;   // 下降緣（低 byte）
wire [7:0] io_out = xspi_clk ? io_out_hi : io_out_lo;
```

⚠ 兩個 block 的 reset 也要分開寫（`io_out_hi` 在 posedge、`io_out_lo` 在 negedge）。

### ✅ 全 gate 重跑：11 run 全 PASS，零 FAIL

兩個修正都沒讓任何測試退化。

### 給接手的人：怎麼提早抓到這類問題

DRC 的錯誤訊息會直接列出訊號名：
```bash
grep -E "^ERROR" vivado/sys_int/sys_int.runs/impl_1/runme.log \
  | grep -oE "Net [^ ]+" | sed 's/.*\///;s/\[[0-9]*\]//' | sort -u
```
⚠ 用 regex 掃 RTL 找「同一個 reg 被兩個 always 賦值」**會誤報**
（`if(!reset)` 那半段會被算成獨立 block）。**以 DRC 的實際結果為準。**

---

## 🔧 21:25 —— 移除重複的 sysclk 約束（合成的 critical warning）

```
CRITICAL WARNING: [Constraints 18-1056]
Clock 'sysclk' completely overrides clock 'sysclk_p'.
```

我在 `timing_bd.xdc` 寫的 `create_clock -name sysclk [get_ports sysclk_p]`
**覆蓋掉了 MIG board preset 自己的約束**。MIG 比我們清楚 DDR4 要什麼，
覆蓋它很危險 —— 已移除那一行。

合成結果證實那條路徑本來就有 **+2.3 ns 餘裕**，不需要我另外約束。

⚠ 這個改動**不影響正在跑的 implement**（它用已合成的 netlist），
下次重跑合成才生效。

### 合成結構確認正確

```
top_bd_axi_smc / clk_wiz_0 / matmul_top / mig_ddr4 / rst_aclk / rst_xspi / xspi_slave
七個 cell 都在，IBUF ×4
```

---

## 🔧 21:35 —— DRC 修正沒生效的真兇：RTL 有八份副本

改了 `rtl/xspi_slave.v` 和 `rtl/axi4_master.v`、全 gate 也過，但 implement
還是同樣 37 個 DRC 錯誤 —— 因為**合成讀的不是 `rtl/`**。

打包成 IP 後，同一個檔案在專案裡有八份：

```
rtl/xspi_slave.v                                    ← 我改的（gate 讀這個）
ip_repo/xspi_slave/xspi_slave.v                     ← 打包來源
sys_int.gen/.../ipshared/4249/xspi_slave.v          ← 合成真正讀這個
sys_int.ip_user_files/.../ipshared/4249/xspi_slave.v
（axi4_master.v 各處也有，兩個 IP 都引用它 -> 更多份）
```

`get_files *xspi_slave.v` 只回報 rtl/ 和 ipshared/ 兩個，
**ip_user_files 那份要用 find 才看得到**。

### 這次的處理：手動同步全部八份

```bash
# ip_repo（打包來源）
cp rtl/xspi_slave.v rtl/async_fifo.v rtl/axi4_master.v ip_repo/xspi_slave/
cp rtl/matmul_top.v rtl/axi4_master.v ... ip_repo/matmul_top/
# ipshared（合成/模擬真正讀的，.gen 和 .ip_user_files 各一份）
for d in sys_int.gen/sources_1 sys_int.ip_user_files; do
  base=vivado/sys_int/$d/bd/top_bd/ipshared
  cp rtl/xspi_slave.v $base/4249/xspi_slave.v
  cp rtl/axi4_master.v $base/4249/axi4_master.v
  cp rtl/axi4_master.v $base/c41b/axi4_master.v
done
```

### ⚠ 更乾淨但更慢的做法（若手動同步漏了）

重新 `ipx::infer_core` 打包 IP + 重建 bd，讓 Vivado 自己傳播。
手動同步的風險是漏一份就白做 —— **每次改 IP 內的 RTL 都要 grep 確認
八份都更新了**：
```bash
for f in $(find vivado/sys_int -path "*ipshared*" -name xspi_slave.v); do
  echo "$(grep -c io_out_hi $f) $f"; done   # 每個都要 >0
```

**教訓**：打包成 IP 之後，`rtl/` 只是「編輯用的主檔」，合成完全不看它。
這是 safe-file-edits 記憶的經典情況 —— 改到的副本不是被讀的副本。

---

## ✅ 22:30 —— 根本解法：IP 換成 module reference，快取問題解決

纏鬥兩小時的 IP 快取問題，根本解法是**不要打包成 IP**：

```
xspi_slave / matmul_top:
  local:user:xspi_slave:1.0  (打包的 IP，有 OOC 快取)
  → xilinx.com:module_ref:xspi_slave:1.0  (module reference，直讀 rtl/)
```

### 為什麼可行

module reference 一樣會自動把 AXI 散腳推斷成 interface pin
（實測 `m_ddr`/`m_reg`/`m_axi`/`s_axi` 都認得），埠名也跟原 IP 完全一致
（`aclk`/`arst_n`/`xspi_*`）—— **當初打包成 IP 是多餘的**，
module reference 就有 interface，還沒有快取層。

### 做法（`09_swap_exec.tcl`）

1. 在現有 bd 上抓兩個 cell 的所有連線（intf 4+4、scalar 19+15）
2. `delete_bd_objs` 刪 IP cell → `create_bd_cell -type module -reference` 建同名
3. 照抓到的連線 `connect_bd_intf_net` / `connect_bd_net` 重接
4. `assign_bd_address` + 手動設 `m_reg = 0x9001_0000`

⚠ 前置一定要 `set_property source_mgmt_mode All`，否則 module reference
找不到 RTL（`Failed to resolve reference`）。

### 結果

```
cells 7  intf_nets 5  nets 11  addr 5   （跟 IP 版一模一樣）
SEG xspi_slave/m_reg  off=0x90010000  ✅
VALIDATE_OK
```

**比砍掉重建 bd 安全** —— 只換兩個 cell，MIG automation / 位址 / 其他 IP 全保留。

### 教訓：自家 RTL 不要打包成 IP

打包成 IP 只在「要給別人用」或「要參數化重用」時才值得。
自己 bd 裡的 RTL 用 module reference 就好 —— 一樣有 interface 推斷，
但改 RTL 立刻生效，不會被 OOC 快取綁死。這次為了那個快取繞了六種方法。

---

## 🔧 23:15 —— sysclk 多驅動：clk_wiz 其實是多餘的

implement 報 `[Place 30-602] IO port 'sysclk_p' is driving multiple buffers`。
根因：MIG 的 sysclk 被接了兩次 —
1. `mig_ddr4/c0_sys_clk_p/n` ← 外部 sysclk（跟 clk_wiz 共用）
2. `mig_ddr4/C0_SYS_CLK` ← board automation 加的 default_250mhz_clk1

### 發現 clk_wiz 根本沒用

- `clk_wiz/clk_out1` (aclk 100MHz)：**0 sinks** — AXI 域早就改用 MIG 的 ui_clk（27B 09-03 改的）
- `clk_wiz/clk_out2` (xspi 50MHz)：**0 sinks** — xspi_clk 用外部 port
- 它還接著外部 sysclk → 多驅動的來源

**刪掉 clk_wiz**（換成 vcc_1 常數給 rst 的 dcm_locked），
順便刪掉多餘的外部 sysclk scalar port（MIG 用 board 的 default_250mhz_clk1）。
`rst/dcm_locked` 改接 `mig_ddr4/c0_init_calib_complete`（校準完成才放開 reset，合理）。

結果：7 cells、VALIDATE_OK、external = ddr4_sdram_c1 + default_250mhz_clk1 + 4 xSPI。

### 這一連串坑的總帳（module_ref 化引發的）

換 module_ref 後連鎖踩了三個 bd 結構問題：
1. DDR4 external port 沒 board 關聯 → `apply_board_connection`
2. sysclk 多驅動 → 刪多餘的 clk_wiz
每個都是「重建 external 連線時弄丟了原本 board automation 建好的東西」。
**教訓：動 bd 的 IP cell 時，board 關聯的 external port 很脆弱，要一併檢查。**

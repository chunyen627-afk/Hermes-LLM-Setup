# 系統整合規格（階段 5）

> 規劃者 2026-09-02 18:50 訂。**派工前先讀完這份，不要邊做邊猜。**
> 上一階段（RTL 驗證）已完成：11 個 run 全 PASS。

## 一、目標與非目標

**目標**：把已驗證的 RTL 接上 Xilinx IP，在 **xsim** 跑通一次端到端資料流，
證明「這套接得起來」。

**非目標**（做了會拖慢「能上板跑」）：
- ❌ 效能優化、平行化、換 42M 模型
- ❌ 完整的 MIG 訓練/校準模擬（太慢，留到上板）
- ❌ 漂亮的 block design GUI 佈局

判斷任何提議：**這讓「能上板跑」更快出現，還是更慢？**

## 二、目前的模組介面（實測，不是猜的）

| 模組 | AXI 角色 | 寬度 | 說明 |
|---|---|---|---|
| `xspi_slave` | **兩個 master**：`m_reg_*`、`m_ddr_*` | 32-bit | 對外是 xSPI 從端，對內是 AXI 主控 |
| `matmul_top` | **slave** `s_axi_*` + master（29 個 `m_axi`/`m_ddr` 埠） | 32-bit | 計算核心 |

⚠ **`ARCHITECTURE.md` 第 3.2 節寫「`AXI_DATA_WIDTH = 256` 對齊 MIG」，
但實際 RTL 是 32-bit。** 這是規格與實作的落差。

**這一階段的決定：維持 32-bit，不要改 RTL。**
理由：RTL 已凍結且全 PASS，改寬度等於重做一次驗證。
寬度轉換交給 **Xilinx SmartConnect 自動處理**（它本來就會插 width converter）。
效能不是這階段的目標。

## 三、要接的 Xilinx IP（全部用內建，不自己寫）

```
xspi_slave (m_ddr, 32b) ─┐
                          ├─► AXI SmartConnect ─► MIG DDR4 (256b)
matmul_top (m_axi,  32b) ─┘

xspi_slave (m_reg, 32b) ────► matmul_top (s_axi, 32b)   ← 直連，不用 IP
```

| IP | 用途 | 關鍵設定 |
|---|---|---|
| **AXI SmartConnect** | 2 master → 1 slave，順便做 32→256 寬度轉換 | 2 個 SI、1 個 MI |
| **MIG / DDR4 SDRAM** | 板上 DDR4 控制器 | 用 VCU118 board preset，不要手填腳位 |
| **Clocking Wizard** | 產生 `aclk` 100 MHz、`xspi_clk` 50 MHz | 輸入用板子的 sysclk |
| **Processor System Reset** | 各時脈域的 reset 同步 | 每個時脈域一個 |

⛔ 這些是加密 SystemVerilog，**只能用 xsim，絕對不要進 iverilog 的 gate**。

## 四、怎麼做（Tcl 批次，不開 GUI）

```tcl
create_project -force sys_int ./vivado/sys_int -part xcvu9p-flga2104-1-e
set_property board_part xilinx.com:vcu118:part0:2.0 [current_project]
add_files [glob rtl/*.v]
add_files -fileset constrs_1 constraints/timing.xdc
create_bd_design "top_bd"
# ... create_bd_cell / connect_bd_intf_net ...
validate_bd_design
make_wrapper / generate_target
```

**分段做，每段存檔**（第 4 條規則：不要一次寫 400 行）：
1. 建專案 + 加 RTL → 存 `vivado/01_project.tcl`，跑一次確認過
2. 加 IP（一次加一個，加完 `validate_bd_design`）→ `vivado/02_ip.tcl`
3. 連線 → `vivado/03_connect.tcl`
4. xsim testbench → `tb/tb_system.v` + `vivado/04_sim.tcl`

## 五、什麼算過關（驗收標準）

**必須全部成立**，任何一項不過就不算完成：

| # | 條件 | 怎麼驗 |
|---|---|---|
| 1 | `validate_bd_design` 零 ERROR | Tcl 回傳值 + log 沒有 `ERROR:` |
| 2 | `generate_target all` 成功 | 產出 wrapper 檔案存在 |
| 3 | xsim 跑完不 hang | 有 `$finish`，不是 timeout |
| 4 | **端到端資料流通** | 見下方定義 |
| 5 | **模組層 gate 沒退化** | `simcheck.py --all` 仍 11 run 全 PASS |

### 第 4 項「端到端」的明確定義

xsim 的 testbench 要做到：
1. 從 xSPI 介面寫入一組已知資料（例如 8 個 halfword）到 DDR4 位址
2. 再從 xSPI 介面讀回同一個位址
3. **讀回的值 == 寫進去的值**
4. 印出 `SYSCHECK data_flow <checked> <bad>`，格式跟模組層一致

⚠ MIG 完整模擬很慢。**可以先用 Xilinx 的 DDR4 behavioral model 或
簡化的 AXI slave BFM 取代 MIG** —— 目標是證明「連線正確」，
不是驗證 DDR4 時序。若用 BFM，在 CHANGELOG 註明。

## 六、時脈與約束

- `aclk` 100 MHz、`xspi_clk` 50 MHz（已在 `constraints/timing.xdc`）
- 兩個時脈域要各自約束，並宣告 `set_clock_groups -asynchronous`
- 細節見 skill `embedded/xilinx-vcu118` 第六節

## 七、環境（09-02 實測可用，不用再查）

```
Vivado    C:\Xilinx\Vivado\2024.2
XCVU9P    70 個變體（xcvu9p-flga2104-1-e）
board     xilinx.com:vcu118:part0:2.0
授權      不需要 .lic
```
首次啟動 Vivado 約 40-60 秒，**log 停住不代表死了**（skill 第七節）。

## 八、⛔ 紅線

1. **不要改 `rtl/*.v`** —— 已凍結、全 PASS。真的必須改就先跑 `--all` 記基準，
   改完比對，退化立刻回退，並在 CHANGELOG 說明。
2. **不要把 Xilinx IP 加進 `simcheck.json`** —— 那是 iverilog 的 gate。
3. **不要開 GUI**，全部 Tcl 批次。
4. **不要一次寫 400 行以上的 Tcl** —— 分段寫、每段跑過再往下。

## 九、完成之後

寫進 `CHANGELOG_sysint.md`（新檔）：做了什麼、用了哪些 IP 版本、
xsim 的實際輸出、還沒做的部分。然後進階段 6：合成 + 時序。

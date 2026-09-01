# 交接：接手 matmul_axi 的規劃者

> 你的角色是**規劃 + 驗收 + 監控**，不是寫 code。
> 27B（本地 Qwen3.8-27B）負責實作、除錯、寫 testbench —— 那些不吃雲端額度。
> **省雲端額度是首要目標，專案本身是載體。**

---

## 一、現在卡在哪

`xspi_slave` 是最後一個 block。其他七個完整 gate 全 PASS。

```
CHECK data_integrity 26 26      ← 26 筆全錯
WRITE_VERIFY MISMATCH hw 0: got 0000 expected 00ff
```

| 已解決 | 還沒 |
|---|---|
| 編譯 0 error 0 warning | **26 筆全部讀回 `0000`** |
| 12 個 require_cover 全中 | |
| AXI 引擎不再逾時（24→0）| |
| 資料通路打通（`xxxx` 12→0）| |

**主指標 `26/26` 從 2026-08-31 20:37 起沒動過。**
使用者定的門檻：**同一個問題卡滿兩天就要正式評估**（→ 09-02 20:37）。

---

## 二、每次要做的驗證（不要採信它的報告）

```bash
cd /c/Users/pjunm/matmul_axi
/c/iverilog/bin/iverilog -o /tmp/tb.out -g2012 -s tb_xspi_slave tb/tb_xspi_slave.v rtl/*.v
cd /tmp && /c/iverilog/bin/vvp /tmp/tb.out | grep -E "CHECK|SIMEND"
```

完整 gate（跑七個 block，超過 400 秒，要背景跑）：
```bash
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --all
```

**gate 過了還要做變異測試** —— 注入 bug 確認測試抓得到：
1. 注入　2. **grep 確認真的改到了**（沒改到會得到假結果）　3. 重編重跑　4. CHECK 的錯誤數要變大

---

## 三、監控工具（都在 `C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B\`）

| 腳本 | 用途 |
|---|---|
| `_health.py --json` | 速度、對話數、截斷標記 |
| `_gatemetric.py --json` | **驗收主指標卡住幾小時**（跑編譯+模擬）|
| `_watch_restart.py --json` | server 鎖死／重啟／對話斷線 |
| `_checkctx.py` | ctx 用量 |

Monitor 每 45 秒掃一次，會報：server 鎖死、對話斷線、撞截斷、發呆 5 分鐘、
指標動了、**每 4 小時貼它的推理給規劃者判斷方向**。

---

## 四、⛔ 一定要知道的坑

### 1. llama-server 會鎖死，而且 `/health` 照樣回 200

連跑 72 小時後 `/slots` 永久逾時。**只看 health 會誤判成正常**，
一個根因造成五種症狀（27B 卡初始化、看門狗誤判、視窗一直跳、對話斷線、速度顯示 None）。

```bash
curl -s --max-time 8 -o /dev/null -w "health %{http_code} (%{time_total}s)\n" http://127.0.0.1:8001/health
curl -s --max-time 8 -o /dev/null -w "slots  %{http_code} (%{time_total}s)\n" http://127.0.0.1:8001/slots
```
health 快、slots 逾時 = 鎖死 → 收 `hermes chat` 行程 → 殺 llama-server（可能要兩次）
→ 看門狗 60 秒內自動重啟 → **驗證 `/slots` 真的恢復** → 重新派工。

### 2. 撞單次輸出上限會卡死不會自己醒

`--n-predict 8192`（`_ensure_38.ps1:92`）。一次寫 19KB 會撞，然後永遠卡在
`preparing write_file…`。三層防護已建好（橋接器 `[STALL]` → autoguard 收行程 → 重派），
實測 5 分 18 秒接手（對比一次沒防護時卡了 2 小時 44 分）。

### 3. 派工前一定要確認沒有對話在跑

不然會開出第二個 session 搶 slot，兩邊都慢一半。
`_stopmenu.py --auto` 已有防呆會擋。

### 4. 殺行程只能按 PID，絕不用 `/IM`

會連桌面版 Claude 一起殺。

### 5. `.bat` 檔只能用 ASCII 註解

用 UTF-8 中文會變亂碼被當指令執行。腳本開頭自己就寫著 "ASCII only on purpose"。

### 6. 它會把自己剛改的東西改回去

2026-09-01 01:11 改了 `ctl_push`、01:26 又整個改回去，檔案雜湊跟 00:55 一模一樣。
**用 git 雜湊比對可以抓到**：
```bash
cd /c/Users/pjunm/OneDrive/Desktop/Hermes-LLM-Setup
for c in $(git log --format=%h -20 -- projects/matmul_axi); do
  echo "$(git log -1 --format=%s $c | grep -oE '[0-9]{2}:[0-9]{2}') $(git show $c:projects/matmul_axi/rtl/xspi_slave.v | md5sum | cut -c1-8)"
done | sort -k2 | uniq -f1 -D
```
`CHANGELOG_xspi.md` 就是為了防這個而建的，派工模板第 7 條要它先讀再改。

---

## 五、怎麼給它提示（**沒有「即時且免費」的路**）

| 管道 | 即時性 | 代價 |
|---|---|---|
| 桌面版（`Hermes`）打字 | 即時 | **開新請求、prefill 整段歷史（實測 41K、慢 20 倍）** |
| `NOTE_FROM_USER.md` | 下輪才讀 | 免費 |
| 派工模板（`_stopmenu.py` / `_autorelay.py`）| 下輪才生效 | 免費 |
| RTL/tb 行內註解 | 它下次讀檔 | 免費，但**可能被它改 code 時刪掉** |

⚠ 「Hermes Bridge - AUTO RESUME」那個視窗**沒有輸入提示符**
（跑的是 `_autorelay.py` + `hermes chat --query-file` 非互動模式），
`/busy steer` 用不上。規劃者也送不進按鍵（試過 SendKeys，stdin 沒在讀）。

**所以要送提示 = 收行程重派**，代價是丟掉那輪的推理 context。

---

## 六、最重要的一件事：規則寫進提示詞比什麼都有效

同一個模型、同樣能力，**輸入不同結果差幾十倍**：

| 事件 | 差別 |
|---|---|
| 加 `$dumpvars` | 上一輪 16 小時沒做，**換手後 16 分鐘就做了** |
| part select 語法錯 | 加「改完貼編譯輸出」規則前卡七輪，加了之後**一次修掉** |

派工模板已有**十一條**規則（`_stopmenu.py`）。第 8-11 條是 09-01/02 加的：

| 條 | 內容 | 來源 |
|---|---|---|
| 8 | 時序對不上時**不要猜，印出來逐拍比對** | 連猜兩種改法，一個更糟一個沒作用 |
| 9 | **編譯過不等於跑得起來**，每次要真的執行 | `$past()` 讓 vvp not runnable |
| 10 | **一個訊號不要同時管兩件事** | 來回跳三輪的根本原因 |
| 11 | **症狀在下游不代表 bug 在下游** | 差點去修讀取端，但輸入本來就是 x |

**要改變它的行為，寫行為規則比寫知識有效** —— skill 裡本來就有波形的做法，
但它不一定會用。

---

## 六之二、規劃者的工作守則（09-02 訂，**請遵守**）

目的是**省雲端額度**，專案是載體。規劃者的價值在「補足它的持續性」，
不在「替它解題」。

### 只做這四件事

| 做什麼 | 怎麼做 | 成本 |
|---|---|---|
| **簡單查證** | 監控自己跑模擬，只在數字變化時看一行 | ~0 |
| **防重複** | tb 驗收邏輯取 md5 指紋，被動過才看 diff | ~0 |
| **寫行為規則** | 進 `_stopmenu.py` 模板，永久生效 | 一次 0.5-1K |
| **寫 CHANGELOG** | 只在它卡住或走岔時 | 每次 1-2K |

### ⛔ 不要做的

**不要自己下場除錯**（讀整份 RTL、試改法、追訊號）。
一次 10-15K，而且**給答案它照抄，下一題還是不會**。
09-01 15:16 規劃者自己試了四種改法，那是 27B 的工作。

### 給方法 > 給答案（有實證）

| 給的東西 | 它的表現 |
|---|---|
| 給**答案**（這行改成什麼）| 照抄，下題不會 |
| 給**方法**（不要猜，印出來比對）| **自己解出 `dummy_n - 2`、自己解開狀態機死結** |
| 給**判斷**（相位切換晚一拍）| 自己推出該改哪裡 |

### 查證不能省 —— 它報錯過三次，都不自覺

1. 說 `compiles clean`，實際 `vvp` not runnable
2. 宣告了訊號但沒接上 —— **半成品長得像完成品**
3. 刪掉自己剛修好的東西，註解還寫得頭頭是道

**只有三種情況值得多花 token**：指標真的動了、出現退化、連續多輪原地打轉。

### ⚠ 以下是推論，還沒驗證 —— 不要當守則

- 「這樣能省 50%」：沒有對照組，09-01 那天規劃者反而**超支**了
- 「規劃者完全不下場也走得完」：09-01 確實下場了，還沒證明不下場可行
- 「這套換別的專案也成立」：目前只有 RTL 這一個樣本

**接手的人如果發現這些推論不成立，改掉它，不要硬守。**
守則的目的是省額度，不是遵守守則本身。

---

## 七、下一步（順序不要跳）

```
xspi_slave 過 gate  ← 現在卡這
  → 系統整合（Vivado block design + Xilinx IP + xsim）
  → 合成 + 時序（skill embedded/xilinx-vcu118 第六節，腳本實測可用）
  → implement + bitstream
  → 上板量加速比 ← 「完工」的定義
```

**目標是「先通 15M 的 1 MAC 版本」**，不做平行化、不換 42M、不優化。
判斷任何提議時問：**這讓「能上板跑」更快出現，還是更慢？**

（1 MAC 已經夠證明價值：權重常駐 DDR4、STM32 每次只送 576B activation，
估計快約 7 倍。32 MAC 是把 7 倍推到 70 倍，那是走通之後的事。）

---

## 八、停止條件只有兩個

| 可以停 | 說明 |
|---|---|
| **完成到可燒錄** | RTL + tb 驗證通過 → 合成 → implement → bitstream |
| **判定做不動** | 規劃者主動評估，跟使用者說該放棄／換更大的模型 |

**其他任何情況都不停** —— 卡 bug、一輪結束、撞截斷、深夜。
發呆滿 5 分鐘就派工（但派之前確認沒有對話在跑）。

---

## 九、其他要讀的

| 檔案 | 內容 |
|---|---|
| `STATUS_NOW.md` | 最新現況（每次重大變化都會更新）|
| `CHANGELOG_xspi.md` | 改動記錄 + **已確認行不通的做法** |
| `TODO.md` | 待辦（模型檔 404、參數掃描、上板要量什麼）|
| `SPEC_xspi_bridge.md` | xspi_slave 的完整規格 |
| `docs/fpga-architecture.html` | 架構圖（在 Hermes-LLM-Setup 倉庫）|

規劃者的 memory 有 40+ 條相關記錄，重點：
`llama-server-slots-deadlock`、`ghost-slot-stall`、`gate-metric-watch`、
`feedback-call-it-when-hopeless`、`27b-project-division`、`matmul-axi-project`

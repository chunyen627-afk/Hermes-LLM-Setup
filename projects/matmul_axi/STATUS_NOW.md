# 現況快照（規劃者維護，2026-09-02 10:38）

> 重開機或換 session 接續時**先讀這份**，再讀 `CHANGELOG_xspi.md` 最後三節。

## 一句話

**寫入路徑已完成並凍結，主指標 26 → 25（28 小時來第一次動）。**
現在卡在讀取路徑「晚一個週期進 `P_DATA`」，27B 已診斷出來，正在拆讀寫相位。

## 目前的數字（規劃者實測基準，改動前後都要比對）

```
CHECK data_integrity 26 25
AXI DDR aw=13 w=17 b=13 ar=24 r=113
WCOMMIT  00ff 01fe 02fd 03fc                       ← 開頭無垃圾
         005a 015b 0258 0359 045e 055f 065c 075d   ← 開頭無垃圾
tb 驗收邏輯指紋  b8d20766db2b0402f81bc1f97de69750
```

**任何一項退化就是改錯了，立刻回退。**

## ⛔ 寫入路徑已凍結 —— 四個修正互相依賴，不要碰

1. `hw_pipe_lo <= xspi_io[7:0]` —— 低 byte 不能讀同邊 NBA 的 `w_lo`
2. `dummy_cnt <= dummy_n - 2` —— P_DATA 進入時機
3. negedge 直接 commit `{w_hi, xspi_io}` —— 讓單筆 frame 也對
4. `wr_data_started` 閘門 —— 丟掉每 frame 第一次的 stale commit

證據：`AXIWR beat=0 data=01fe00ff`、`beat=64 data=015b005a` —— 資料
正確打包成 32-bit beat 並寫進 DDR 模型。**寫入端沒有問題。**

## 現在唯一的問題：讀取路徑晚一拍

```
WRITE_VERIFY hw 0: got 0000 expected 00ff
WRITE_VERIFY hw 1: got 00ff expected 01fe    ← got 的正是 hw 0 的期望值
```

⚠ `WRITE_VERIFY` 這個名字會誤導 —— 它的**驗證動作是讀回來比對**，
所以讀不對一樣 fail。不要因為它叫 write verify 就回去改寫入端。

27B 自己的診斷（10:29，正確）：
> the READ path ... entering P_DATA one cycle late
> decoupling the read and write phase timing — exactly "split the signal" (rule 10)

**⛔ 但不要用 `dummy_cnt = dummy_n - 3`** —— 那會讓寫入端整串偏移。
正解是拆開讀寫的相位時機（`is_read` 是現成的訊號）。

## 規劃者的工作守則（09-02 訂）

只做四件事，詳見 `HANDOFF_TO_NEXT.md` 第六之二節：
1. **簡單查證** —— 監控自己跑，只看一行數字
2. **防重複** —— tb 驗收邏輯 md5 指紋自動盯
3. **寫行為規則** —— 進 `_stopmenu.py` 模板（已有 11 條）
4. **寫 CHANGELOG** —— 只在它卡住或走岔時

⛔ **不自己下場除錯。** 給方法 > 給答案。

## 環境備註

- Claude Code CLI 已更新到 **2.1.258**（09-02 10:36）
- llama-server 正常（health/slots 都快）
- 27B 連續三輪撞截斷，原因都是**用長篇推理手推訊號值**燒光 ctx
  → 派工時要強調「印出來看，不要用想的」

## 監控（重開 session 後要重建）

兩個 Monitor：
1. **存檔 + 指標** —— 每 5 分鐘跑 `_autosync.sh`，只在數字變化或
   驗收指紋被動時通報
2. **行程健康** —— 45 秒一次，看卡死/截斷/發呆（idle 判定要
   交叉驗證 log 靜止，`_health.py` 的 agents 會報過期資訊）

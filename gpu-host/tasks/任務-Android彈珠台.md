# 任務：從零建 Android 開發環境，做一款彈珠台遊戲，在模擬器跑起來

這台機器**完全沒有 Android 開發環境** —— 沒有 JDK、沒有 SDK、沒有 Gradle、
沒有 Android Studio。你要自己把整套裝起來。

**驗收標準只有一個：模擬器裡真的在跑你做的 APK，而且我看得到截圖。**

---

## 為什麼是彈珠台

因為它的每一個部分都**可以被驗證**，不是靠感覺：

- 物理有數值可斷言（重力、反彈係數、能量守恆）
- 碰撞有邊界情況（角落、高速穿透、同時撞兩面牆）
- 畫面有截圖可看（球在不在場上、擋板有沒有動）
- 分數有邏輯可測（撞到什麼加多少分）

不要選「看起來很酷但驗不了」的東西。

---

## 環境（先做這個，做不完就沒有後面）

**要自己裝的**：JDK 17（Gradle 8 需要）、Android SDK command-line tools、
platform-tools、build-tools、platform、system-image、Gradle。

不要裝 Android Studio —— 那是 GUI，你用不到，而且好幾 GB。
用 `sdkmanager` 指令列裝就好。

**這台機器的現況**：
- Windows 11，terminal 走 git-bash（POSIX 語法）
- RAM 32GB 可用、磁碟 395GB —— 資源夠，不用省
- ⚠ **GPU 只剩 3.8GB VRAM**（本機 LLM 佔著）
  → 模擬器一定要用軟體渲染 `-gpu swiftshader_indirect`，
    用硬體加速會跟 LLM 搶 VRAM 然後兩邊都掛掉
- 已有 Node、Python 3.11、git、curl

**目標裝置**：紅米 Note 3（2015 年的機器）。
先查它實際跑的 Android 版本再決定 `minSdk` ——
不要憑印象假設，查到的資訊為準。模擬器也開同一個 API level。

**環境弄好後寫進 `docs/android-setup.md`**：每個工具裝在哪、
版本號、環境變數怎麼設、踩到什麼坑。下次不用重來。

---

## 遊戲需求

**必須有**：

- 球有重力、會反彈，撞牆和撞擋板的行為要對
- 底部兩片擋板（flipper），左右螢幕各控制一邊
- 場上至少三種得分物件（保險桿、標靶之類），分數不同
- 分數顯示、球數（3 顆）、Game Over
- 最高分存在本機，重開 App 還在

**加分**：

- 球速上限（防止穿透）
- 多球
- 音效
- 震動回饋

**不要做**：連線、帳號、廣告、內購。

---

## 這題真正在測什麼：能不能自己驗證

我不在旁邊，所以**你要自己證明它能跑**，不是說「應該可以」。

### 三層驗證，缺一不可

**1. 邏輯層（不用模擬器）**

物理和碰撞抽成純 Kotlin/Java 類別，用 JUnit 測：

```
./gradlew test
```

至少要涵蓋：球從高處落下的速度、撞牆後的角度、
高速球會不會穿過牆、同時撞兩面牆、擋板打到球的力道。

**這層跑得快，改一次跑一次。**

**2. 建置層**

```
./gradlew assembleDebug
```

要有 **零 warning**。有 warning 就修，不要留著。
產出的 APK 路徑寫進筆記。

**3. 模擬器層（決定性的一層）**

```bash
# 開模擬器（無視窗、軟體渲染）
emulator -avd <名稱> -no-window -gpu swiftshader_indirect -no-audio &

# 等它真的開機完成 —— 不要用 sleep 猜
adb wait-for-device
adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'

# 裝 APK 並啟動
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n <package>/<activity>

# 截圖
adb exec-out screencap -p > shots/01_start.png
```

**要截的畫面至少四張**：開始畫面、球在場上、得分後、Game Over。

**截完要自己看** —— 用 `vision_analyze` 檢查，不要只確認「檔案存在」。
問具體的問題：

```
這是 Android 彈珠台的截圖。請具體回答：
1. 畫面上有幾顆球？位置在哪（座標）？
2. 底部的擋板看得到嗎？兩片都在嗎？
3. 分數顯示在哪？數字是多少？
4. 有沒有東西超出畫面邊界或重疊？
```

**模擬按鍵測互動**：

```bash
adb shell input tap <x> <y>          # 點擊
adb shell input swipe x1 y1 x2 y2    # 滑動
```

點擊擋板區域 → 截圖 → 確認擋板真的動了。

---

## 卡住時怎麼辦

**環境安裝會卡**，這很正常。規則是：

- 同一個錯誤試 **3 次**沒過 → 換方法，並把試過什麼寫進
  `docs/android-setup.md` 的「踩過的坑」
- 找不到工具就查（`ddgs text -q "..."`），不要憑印象猜指令
- **不要無聲跳過** —— 如果某個步驟放棄了，明確寫出來

**絕對不要做的事**：
- ❌ 沒跑過就說「應該可以跑」
- ❌ 截圖存了但沒看內容
- ❌ 測試沒過就說完成
- ❌ 為了讓測試通過而改測試（要改的是程式碼）

---

## 交付

做完給我這些：

1. **`docs/android-setup.md`** —— 環境怎麼裝的完整記錄
2. **`ARCHITECTURE.md`** —— 模組分工、資料流、關鍵決定與為什麼
3. **`shots/`** —— 至少四張模擬器截圖，每張說明你從裡面看到什麼
4. **測試結果** —— `./gradlew test` 的通過數
5. **APK 路徑** —— 我要能直接傳到手機裝
6. **一句話說明**：這個 APK 在模擬器上實際跑起來了沒有

最後把這次學到的東西**存成 skill**（Android 環境建置、
adb 驗證手法），下次做 Android 專案不用重來。

---

## 一個提醒

這題的難點**不在遊戲邏輯**，在環境。
彈珠台的物理不難，難的是把 JDK、SDK、Gradle、模擬器全部弄對，
而且證明它真的在跑。

**先花時間把環境弄穩，再寫遊戲。** 環境不穩就寫程式，
最後會卡在「編譯不過但不知道為什麼」。

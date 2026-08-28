---
name: embedded-ui-verification
description: "Verify embedded UI yourself: screenshot, vision, tap simulation."
version: 1.0.0
author: pjunm
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [embedded, ui, verification, vision, framebuffer, stm32, testing]
    related_skills: [stm32h7s78-dk, systematic-debugging]
---

# 自己驗證嵌入式 UI，不要等人回報

**核心觀念：純數學測試看不出「畫面看起來對不對」。**

`test_gfx.c` 可以驗證「這個像素是不是 `0xFFFF`」，但驗證不了
「數字有沒有超出邊界」「字距是不是太寬」「版面有沒有上下顛倒」。
那些要**真的看一眼**。

沒有這套方法，UI 問題的迴圈是：

```
改程式 → 燒錄 → 等使用者回報 → 猜 → 再改
```

每一輪都卡在人身上。實測過一次「改了半小時燒錄一樣沒改好」——
不是能力問題，是**看不到自己畫的東西**。

有了這套方法：

```
改程式 → 自己跑模擬 → 自己看截圖 → 自己發現問題 → 自己修
```

---

## 一、三步驟：dump → PNG → 看

### 1. 產生 framebuffer dump

寫一支渲染測試，把畫面畫進 framebuffer 再存成 raw：

```c
static uint16_t fb[PHYS_W * PHYS_H];

static void dump(const char *path)
{
    FILE *f = fopen(path, "wb");
    fwrite(fb, 2, (size_t)PHYS_W * PHYS_H, f);
    fclose(f);
}

int main(void)
{
    gfx_set_framebuffer(fb);
    ui_draw_static();

    calc_init(&c);
    calc_digit(&c, '1');
    calc_digit(&c, '2');
    calc_op(&c, OP_ADD);
    ui_draw(&c, 0);
    dump("shots/shot_calc.raw");
}
```

**測邊界情況最有價值** —— 最長的字串、最大的數字、最多的元素。
例如 `99999999 * 99999999` 這種 16 位數結果。

### 2. 轉成 PNG

```python
from PIL import Image
PHYS_W, PHYS_H = 800, 480          # 實體橫向

def convert(path):
    data = open(path, "rb").read()
    img = Image.new("RGB", (PHYS_W, PHYS_H))
    px = img.load()
    for i in range(PHYS_W * PHYS_H):
        v = data[i*2] | (data[i*2+1] << 8)          # RGB565 little-endian
        r = ((v >> 11) & 0x1F) * 255 // 31
        g = ((v >>  5) & 0x3F) * 255 // 63
        b = ( v        & 0x1F) * 255 // 31
        px[i % PHYS_W, i // PHYS_W] = (r, g, b)
    # 直立面板要轉回使用者看到的方向
    img.rotate(-90, expand=True).save(path.replace(".raw", ".png"))
```

### 3. 用 vision_analyze 看

```
vision_analyze(image_path="shots/shot_result.png", prompt="<具體問題>")
```

---

## 二、問題要問得具體 —— 這是成敗關鍵

**沒用的問法**（會得到「這是一個計算機介面」這種廢話）：

```
這張圖看起來對嗎？
描述這個畫面
```

**有用的問法** —— 要求量座標、比較、找異常：

```
這是 480x800 直立螢幕的計算機截圖。請具體回答，用像素座標：
1. 顯示區的左右邊界 X 座標各是多少？
2. 主結果數字的最右邊像素在 X 多少？有沒有超出顯示區？
3. 數字和右下角標籤的水平間隔是幾像素？會不會重疊？
4. 每個數字字元的寬度、以及字元之間的間距各是多少像素？
5. 橘色按鈕的邊框畫法跟藍色按鈕有什麼不同？
```

**實戰效果**：這樣問出來「`1` 和 `6` 之間空了 20px，
幾乎等於一個字元本身的寬度」—— 直接定位到 `x_advance`
用了固定值（取最寬字元）而不是實際字形寬度。

那個 bug 表現成三個症狀（顯示區數字太大、位數多時跑版、
按鈕文字鬆散），**一次修好三個**。純測試永遠測不出來。

---

## 三、互動模擬：測「按下去會怎樣」

渲染測試直接呼叫 `calc_digit('1')`，測的是**邏輯層**。
但使用者回報的問題往往在**互動層**：按了某顆鍵之後畫面不對。

做法是算出按鈕的中心座標，餵給輸入處理函式，等於模擬真人按下去：

```c
static void tap(input_t *in, calc_t *c, btn_id_t btn, const char *label)
{
    btn_rect_t r = UI_BUTTONS[btn];
    int cx = r.x + r.w / 2;
    int cy = r.y + r.h / 2;

    btn_id_t held = input_update(in, c, true, cx, cy);   /* 按下 */
    ui_draw(c, held == BTN_COUNT ? 0u : (1u << held));
    dump_step(label);                                    /* 存這一幀 */

    input_update(in, c, false, cx, cy);                  /* 放開 */
    ui_draw(c, 0);
}

int main(void)
{
    /* 使用者回報的流程逐步重現 */
    tap(&in, &c, BTN_1, "press_1");
    tap(&in, &c, BTN_2, "press_2");
    tap(&in, &c, BTN_3, "press_3");
    dump_step("after_123");        /* 大白字 123、算式行空 */

    tap(&in, &c, BTN_ADD, "press_add");
    dump_step("after_plus");       /* 算式行 "123 +" */

    tap(&in, &c, BTN_4, "press_4");
    tap(&in, &c, BTN_5, "press_5");
    dump_step("after_45");         /* 算式行 "123 +"、大白字 45 */

    tap(&in, &c, BTN_EQ, "press_eq");
    dump_step("after_equals");     /* "123 + 45 ="、168 */

    /* 邊界：連按兩個運算符號，7 + * 8 = 應該是 56 不是 15 */
    /* 邊界：除以零，應該顯示錯誤而不是崩潰 */
}
```

**能抓到渲染測試抓不到的**：

| 問題 | 為什麼邏輯測試看不到 |
|---|---|
| 按鈕座標算錯（按 `7` 觸發 `8`）| 邏輯測試直接呼叫函式，跳過座標計算 |
| 按下的視覺回饋不對 | 沒有 pressed 狀態這一幀 |
| 連續操作的狀態殘留 | 每個測試都從乾淨狀態開始 |
| 按鍵邊緣的判定範圍 | 完全沒測到 |

---

## 四、什麼時候該主動看

**不要等使用者回報。** 這些時機自己看一眼：

| 時機 | 要問什麼 |
|---|---|
| 改了任何繪圖程式碼 | 版面有沒有跑掉、有沒有東西被切到 |
| 改了字型或排版 | 字距、對齊、有沒有超出容器 |
| 新增 UI 元件 | 有沒有跟既有元件重疊 |
| **要交付前** | **每個主要畫面都看一次** |

---

## 五、靜態截圖的極限

**看得出來的**：版面、對齊、字距、顏色、重疊、裁切、上下顛倒

**看不出來的**：閃爍、撕裂、FPS、按下瞬間的殘影

動態問題要用別的手法：

- **抓閃爍**：把「清除背景」的顏色暫時改成鮮紅色。
  如果閃的變成紅線，就確定是局部重繪的髒區塊算錯
  （清除的矩形比實際元件大 1-2 像素）
- **抓撕裂**：確認雙緩衝交換有對齊 VSync
- **量 FPS**：DWT cycle counter
- **DMA2D 與 CPU 搶 framebuffer**：檢查有沒有漏掉等待 DMA2D 結束

---

## 六、一個提醒

**視覺模型會自信地講錯。** 它量的座標是估計值，不是精確測量。

把回答當「線索」而不是「結論」—— 拿到「X≈320」之後，
去程式碼確認實際的繪製起點和寬度計算，兩邊對得起來才動手改。

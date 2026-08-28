/* 互動模擬：像真人一樣「按」按鈕，每按一下就 dump 一張畫面。
 *
 * 跟 render_shot.c 的差別：
 *   render_shot.c 直接呼叫 calc_digit() —— 測的是邏輯層
 *   這支走 input_update() 餵觸控座標 —— 測的是「使用者真的按下去會怎樣」
 *
 * 能抓到 render_shot.c 抓不到的問題：
 *   - 按鈕座標算錯（按 7 結果觸發 8）
 *   - 按下的視覺回饋不對
 *   - 按鍵邊緣的判定範圍
 *   - 連續操作時的狀態殘留
 *
 * 用法：
 *   gcc -I core test/sim_session.c core/*.c -o sim_session -lm
 *   ./sim_session
 *   python tools/raw2png.py shots/step_*.raw
 *   然後用 vision_analyze 逐張看
 */
#include <stdio.h>
#include <stdbool.h>
#include <string.h>

#include "calc.h"
#include "gfx.h"
#include "input.h"
#include "ui.h"

#define PHYS_W 800
#define PHYS_H 480

static uint16_t fb[PHYS_W * PHYS_H];
static int step_no = 0;

static void dump_step(const char *label)
{
    char path[128];
    snprintf(path, sizeof(path), "shots/step_%02d_%s.raw", step_no++, label);
    FILE *f = fopen(path, "wb");
    if (!f) {
        printf("cannot open %s\n", path);
        return;
    }
    fwrite(fb, 2, (size_t)PHYS_W * PHYS_H, f);
    fclose(f);
    printf("wrote %s\n", path);
}

/* 按一顆按鈕：算出它的中心座標，餵 down 再餵 up，中間各畫一幀。 */
static void tap(input_t *in, calc_t *c, btn_id_t btn, const char *label)
{
    btn_rect_t r = UI_BUTTONS[btn];
    int cx = r.x + r.w / 2;
    int cy = r.y + r.h / 2;

    /* 按下：畫出 pressed 狀態 */
    btn_id_t held = input_update(in, c, true, cx, cy);
    ui_draw(c, held == BTN_COUNT ? 0u : (1u << held));
    dump_step(label);

    /* 放開：畫出結果 */
    input_update(in, c, false, cx, cy);
    ui_draw(c, 0);
}

int main(void)
{
    calc_t c;
    input_t in;

    gfx_set_framebuffer(fb);
    ui_draw_static();
    calc_init(&c);
    input_init(&in);

    /* 起始畫面 */
    ui_draw(&c, 0);
    dump_step("start");

    /* 使用者回報的流程：按數字 -> 按運算符號 -> 看大白字有沒有不見 */
    tap(&in, &c, BTN_1, "press_1");
    tap(&in, &c, BTN_2, "press_2");
    tap(&in, &c, BTN_3, "press_3");
    ui_draw(&c, 0);
    dump_step("after_123");        /* 大白字應該是 123，算式行空 */

    tap(&in, &c, BTN_ADD, "press_add");
    ui_draw(&c, 0);
    dump_step("after_plus");       /* 算式行應該是 "123 +" */

    tap(&in, &c, BTN_4, "press_4");
    tap(&in, &c, BTN_5, "press_5");
    ui_draw(&c, 0);
    dump_step("after_45");         /* 算式行 "123 +"，大白字 45 */

    tap(&in, &c, BTN_EQ, "press_eq");
    ui_draw(&c, 0);
    dump_step("after_equals");     /* 算式行 "123 + 45 ="，大白字 168 */

    /* 邊界：連續按運算符號 */
    calc_init(&c);
    input_init(&in);
    tap(&in, &c, BTN_7, "b_7");
    tap(&in, &c, BTN_ADD, "b_add1");
    tap(&in, &c, BTN_MUL, "b_mul");    /* 改變主意換成乘 */
    tap(&in, &c, BTN_8, "b_8");
    tap(&in, &c, BTN_EQ, "b_eq");
    ui_draw(&c, 0);
    dump_step("after_op_switch");      /* 應該是 7 * 8 = 56，不是 7 + 8 */

    /* 邊界：除以零 */
    calc_init(&c);
    input_init(&in);
    tap(&in, &c, BTN_9, "d_9");
    tap(&in, &c, BTN_DIV, "d_div");
    tap(&in, &c, BTN_0, "d_0");
    tap(&in, &c, BTN_EQ, "d_eq");
    ui_draw(&c, 0);
    dump_step("after_div_zero");       /* 應該顯示錯誤，不是崩潰或亂碼 */

    printf("\n%d 張畫面已產生。接著跑：\n", step_no);
    printf("  python tools/raw2png.py shots/step_*.raw\n");
    printf("再用 vision_analyze 逐張確認版面。\n");
    return 0;
}

---
name: game-assets
description: 做遊戲時挑素材用（pygame 或網頁遊戲都適用）。提供本機 Kenney 素材庫的 sprite / 音效 / BGM 路徑對照表、載入範例、以及抓新 pack 的流程。當任務是做遊戲、需要音效、需要 sprite、或要用 shared_assets 時載入。
---

# 遊戲素材規則

**開工前先跑 `python shared_assets/find_asset.py <關鍵字>`** 拿真實路徑，不要憑空編。

素材庫：`C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets/`

## 圖
kenney_roguelike（RPG，16×16 spritesheet）/ kenney_platformer（Mario 風）/ kenney_racing /
kenney_space / kenney_top_down / kenney_tower_defense / kenney_ui

## 音效（8 pack、651 個 .ogg）
| 情境 | pack |
|---|---|
| 撞擊（戰鬥／子彈／碰撞／怪物死） | kenney_audio_impact |
| UI（按鈕／選單／確認） | kenney_audio_interface + kenney_audio_ui |
| 關鍵時刻短曲（升等／過關／Game Over） | kenney_audio_jingles（含 8-Bit NES） |
| 撿物／翻頁／開門 | kenney_audio_rpg |
| 科幻（雷射／引擎／警報） | kenney_audio_scifi |
| 抽象／電子提示音 | kenney_audio_digital |
| 角色喊話 | kenney_audio_voiceover |

## BGM（長段 loop）
`shared_assets/bgm/juhani_chiptunes/` → stage1 / stage2 / boss / menu.ogg
⚠ jingles 是 1-3 秒短曲，**不要拿來當 BGM**（會每秒重播很卡）。

## 絕對禁止
- ❌ Web Audio API `OscillatorNode` 生 sin 波嗶嗶聲（有 651 個真 .ogg）
- ❌ `math.sin` / numpy 算生成式音效
- ❌ 用 `pygame.draw` / `ctx.arc` 畫角色代替 sprite（抽象益智除外）
- ❌ 「網頁不適用素材規則」——**通用**，.png/.ogg 在 Canvas 跟 pygame 都能用
- ❌ 「之後再加 sprite」的藉口

**踩過的雷**：設了 SPRITESHEET_PATH 但整檔沒一個 `image.load`、全用 `draw.circle` → 畫面簡陋被打回。

## 抽象遊戲特例
俄羅斯方塊／貪食蛇／2048／反彈球：方塊本來就是色塊，**不需要 sprite**。
但**音效還是要用 .ogg**（落子→interface、消行→impact、Game Over→jingles、BGM→stage1）。

## 網頁怎麼用
`cp` 需要的檔到 cwd 的 `assets/`，用相對路徑（避開 file:// CORS）：
```bash
mkdir -p assets/audio
cp shared_assets/kenney_audio_impact/Audio/impactPlate_medium_000.ogg assets/audio/hit.ogg
```
```js
const sfx = { hit: new Audio('assets/audio/hit.ogg') };
sfx.hit.currentTime = 0; sfx.hit.play();
```
README 註明：`雙擊 index.html 或跑 python -m http.server 8000`。

## pygame 載入
```python
BASE = 'C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets'
pygame.mixer.pre_init(44100, -16, 2, 512)   # 必須在 init() 前
pygame.init(); pygame.mixer.init()
SFX = {'hit': pygame.mixer.Sound(f'{BASE}/kenney_audio_impact/Audio/impactPlate_medium_000.ogg')}
SFX['hit'].play()
```
spritesheet 切 tile（roguelike TILE=16, MARGIN=1，看 spritesheetInfo.txt）：
```python
sub = sheet.subsurface((col*(TILE+MARGIN), row*(TILE+MARGIN), TILE, TILE))
```
⚠ 載入順序：`init()` → `display.set_mode()` → `load()`，沒視窗 `.convert_alpha()` 會炸。
⚠ 載入前先 `ls` 確認檔名存在，不要猜路徑。
致謝：`Sprites & sounds by Kenney (kenney.nl) CC0`。

## 沒有對應素材時
照這流程抓 Kenney pack（不是跳過去畫色塊）：
```bash
ddgs text -q 'kenney space shooter pack' -m 3
curl -s 'https://kenney.nl/assets/xxx' > /tmp/p.html
grep -oE 'https://kenney.nl/media[^"]+\.zip' /tmp/p.html | head -1
curl -L -o /tmp/pack.zip '<URL>' ; unzip -q /tmp/pack.zip -d shared_assets/kenney_xxx
```

## 遊戲一定要有（不用問，自己加）
分數、Game Over 畫面、Restart、音效、難度遞增、暫停、最高分記錄。

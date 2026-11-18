"""Update the existing illustrated manual, preserving its pictures and formatting."""
import hashlib
import json
from pathlib import Path
import shutil
from docx import Document

ROOT = Path(__file__).parent
SOURCE = Path(r"C:\Users\pjunm\OneDrive\Desktop\H3 圖生影片完整操作手冊.docx")
BACKUP = Path(r"C:\Users\pjunm\.cache\hermes-strata-backups\H3-manual-before-strata.docx")


def text(paragraph, value):
    assert not paragraph._p.xpath('.//w:drawing'), 'Do not overwrite an image paragraph'
    if paragraph.runs:
        paragraph.runs[0].text = value
        for run in paragraph.runs[1:]:
            run.text = ''
    else:
        paragraph.add_run(value)


def main():
    video = json.loads((ROOT / 'hermes-video-verification.json').read_text(encoding='utf-8'))
    assert video['status'] == 'passed', 'Do not publish an unverified video profile'
    assert json.loads((ROOT / 'static-coexistence-verification.json').read_text(encoding='utf-8'))['status'] == 'passed'
    assert json.loads((ROOT / 'hermes-vision-verification.json').read_text(encoding='utf-8'))['status'] == 'passed'
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if not BACKUP.exists():
        shutil.copy2(SOURCE, BACKUP)
    before = {'bytes': SOURCE.stat().st_size, 'sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
    doc = Document(SOURCE)
    before.update(paragraphs=len(doc.paragraphs), tables=len(doc.tables), images=len(doc.inline_shapes))
    assert before['paragraphs'] == 180 and before['tables'] == 9 and before['images'] == 9
    replacements = {
        1: 'Hermes 原生視覺與本機 ComfyUI 圖文教學',
        2: '適用電腦 pjunm  |  更新日期 2026 年 10 月 7 日',
        13: '兩個入口共用同一聊天模型。切換會停止另一個閒置媒體服務並重新載入對應上下文；生成或聊天未完成時會拒絕切換，請等工作結束。',
        26: '日常聊天與傳圖使用 Hermes 桌面程式；5000 是靜態生圖網頁，8189 是 H3 影片網頁。兩套 ComfyUI 的安裝資料夾不同，入口會切換媒體服務。',
        52: '可用中文描述想要的動作，請 Hermes 整理成英文 H3 提示詞。直接在 H3 貼中文也可嘗試；英文場景與動作描述較便於控制，需要出現的文字或對白可保留中文。',
        88: 'Strata 聊天模型可與 H3 共存。聊天使用 GPU 0＋2，H3 使用 GPU 1，並分攤 CPU 與 RAM；生成期間避免再開其他大型 GPU 程式。',
        112: '讓 Hermes 自動把圖片轉成影片',
        113: '先開「模型＋生影片」，再到 Hermes 附加圖片或貼完整本機路徑。例如：「請把 C:/Users/pjunm/ComfyUI-H3/input/h3_duck_input.png 做成 3 秒影片，讓它輕輕左右搖動，保持原貌。」不用提供完整英文提示詞。',
        114: 'Hermes 會讀取 h3-local-video 技能、整理英文動作、提交 H3 並等待實際完成，最後給出 MP4 路徑。新模型具備原生視覺；專用編碼器在 CPU 執行。完整自動操作說明另見桌面「Hermes-H3 自動生影片操作.md」。',
        126: '等 H3 與聊天工作結束，再開「模型＋靜態生圖」。入口停止閒置 H3，使用 3 GPU、262,144 tokens 上下文啟動 Strata，並在 GPU 1 的預留空間執行靜態生圖。',
        128: '先存好圖片，等生圖與聊天結束，再開「模型＋生影片」。入口停止靜態生圖，改用 GPU 0＋2、32,768 tokens 上下文啟動 Strata，GPU 1 專供 H3。',
        131: 'H3 視窗標題為「H3 Video - GPU 1 - close to release memory」。模型視窗標示 STRATA 與目前模式；正常使用時保留這些視窗。',
        133: '只想停影片、繼續聊天，可只關 H3 視窗。全部停止時再關 Strata 模型視窗；橋接器視窗也可關閉。這些服務不會自動在背景重開。',
        135: '沒有背景自動重開或自動派工；27B-STATUS.txt 不會再自動產生。模式切換需要重新載入模型，請等就緒。Hermes 已開啟時，切換後開新對話；仍顯示舊設定再自行重開 Hermes。',
        155: '影片模式 H3 使用 RTX 3060 GPU 1，聊天模型使用 GPU 0＋2。H3 會使用 RAM 分攤；顯存隨設定變動。三張卡的顯存不會自動合成一張 32 GB 卡。',
        156: '靜態圖模式由 Strata 使用三張卡與 32 GiB 專家 RAM 快取，GPU 1 另留約 5 GiB 給生圖。影片模式使用兩張卡與 20 GiB RAM 快取。原生視覺使用自身約 0.91 GB 編碼器，在 CPU 執行；其他看圖模型檔保留。',
        159: '已測設定的完成結果與長期穩定性不同。256K 是模型原生上限與已載入容量，未測滿 256K 的長提示詞。較長影片、較大解析度與更多步數會增加耗時和記憶體，須逐項測試。',
        173: 'C:\\Users\\pjunm\\Strata',
        175: 'C:\\Users\\pjunm\\OneDrive\\Desktop\\Hermes-H3 自動生影片操作.md',
        179: 'H3 參數來源：comfy_extras\\nodes_minimax_h3.py。新模型實測：Strata 內的 static-coexistence-verification.json、hermes-video-verification.json、hermes-vision-verification.json；舊 27B 測試只作歷史比較。',
    }
    for i, paragraph in enumerate(doc.paragraphs):
        if i in replacements:
            text(paragraph, replacements[i])
        elif paragraph.text and '27B' in paragraph.text:
            value = paragraph.text.replace('27B＋靜態生圖', '模型＋靜態生圖').replace('27B＋影片', '模型＋生影片').replace('27B', 'Hermes')
            text(paragraph, value)
    cells = {
        (0,1,0): '模型＋靜態生圖', (0,1,1): '3 GPU；262,144 tokens；原生視覺；靜態圖共存。',
        (0,2,0): '模型＋生影片', (0,2,1): 'GPU 0＋2；32,768 tokens；GPU 1 執行 H3。',
        (5,2,0): 'Strata', (5,2,1): 'Hermes 桌面聊天與傳圖', (5,2,2): '關 Strata 視窗，停止聊天並釋放模型資源。',
        (6,2,1): 'RTX 3070 8 GB；兩種模式均供 Strata 使用。',
        (6,3,1): 'RTX 3060 12 GB；影片供 H3，靜態模式共享 Strata／生圖。',
        (6,4,1): 'RTX 3060 12 GB；兩種模式均供 Strata 使用。',
        (7,1,0): '先前 H3 單卡', (7,2,0): '先前 H3 與 27B',
        (7,3,0): '先前示範影片', (7,4,0): '先前三卡 H3 實驗',
        (7,5,0): '新模型與靜態圖', (7,5,1): '3 GPU／256K；1024×1024／10 步約 21 秒，生成期間聊天成功。',
    }
    # Include the new real video result without adding a new table or losing old evidence.
    jobs = video['verified_video_jobs']
    verification = jobs[-1]['verification']
    cells[(7,3,0)] = 'Hermes 新模型影片'
    cells[(7,3,1)] = '中文動作→工具派工→新 MP4；影片與音訊完整解碼通過。'
    cells[(3,1,2)] = '合法格點；Hermes 與新模型的 3 秒試片已完成。'
    for (ti,ri,ci), value in cells.items():
        cell = doc.tables[ti].rows[ri].cells[ci]
        text(cell.paragraphs[0], value)
        for paragraph in cell.paragraphs[1:]:
            text(paragraph, '')
    assert len(doc.inline_shapes) == 9
    doc.save(SOURCE)
    after = {'bytes': SOURCE.stat().st_size, 'sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
             'paragraphs': len(doc.paragraphs), 'tables': len(doc.tables), 'images': len(doc.inline_shapes)}
    evidence = {'path': str(SOURCE), 'backup': str(BACKUP), 'before': before, 'after': after,
                'video_verification': verification, 'render_review': 'pending'}
    (ROOT / 'manual-edit-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'edited': str(SOURCE), 'images_retained': 9}, ensure_ascii=False))


if __name__ == '__main__':
    main()

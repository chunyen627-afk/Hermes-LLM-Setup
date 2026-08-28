"""一次性：從 app.py 抽 WIN_RULES，產出 CLAUDE.md"""
import sys
sys.path.insert(0, r'C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui')

# 讀 app.py 找 WIN_RULES 字串內容（用 exec）
import re
src = open(r'C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui\app.py', encoding='utf-8').read()

# 抓 WIN_RULES = ( ... ) 那段
m = re.search(r'WIN_RULES = \(\n(.*?)\n    \)', src, re.DOTALL)
if not m:
    print("找不到")
    exit(1)

body = m.group(1)
# 每行是 "        \"...content...\\n\""  → 抽 content 還原
cwd_placeholder = "(BAT 啟動時的工作目錄)"

lines = []
for raw_line in body.split('\n'):
    # strip indent + 處理 f-string prefix
    s = raw_line.strip()
    if s.startswith('f"'):
        s = s[2:]
    elif s.startswith('"'):
        s = s[1:]
    # 結尾 "\n" 或 "
    if s.endswith('\\n"'):
        s = s[:-3]
    elif s.endswith('"'):
        s = s[:-1]
    # 反轉義
    s = s.replace('\\"', '"')
    # f-string {cwd} → placeholder
    s = s.replace('{cwd}', cwd_placeholder)
    lines.append(s)

md_body = '\n'.join(lines)

# 移除 Flask 專屬內容（圖片生成 markdown）
md_body = md_body.replace(
    "![image](/api/vision/generate?prompt=ENGLISH_PROMPT_URLENCODED)",
    "(電腦端沒 vision API)"
)

# 包成 CLAUDE.md
out = """# Claude Code Agent Rules (本地 LLM 用)

> 這份規則跟手機 Flask 後端的 WIN_RULES 同步。
> 內容核心一致，去掉了 Flask 專屬部分（vision API）。
> 適用對象：電腦端 BAT 啟動的 Claude Code + 本地 LLM (27B / 30B / 35B)。

""" + md_body

with open(r'C:\Users\pjunm\OneDrive\Desktop\hermes\CLAUDE.md', 'w', encoding='utf-8') as f:
    f.write(out)

import os
sz = os.path.getsize(r'C:\Users\pjunm\OneDrive\Desktop\hermes\CLAUDE.md')
print(f'OK wrote CLAUDE.md ({sz} bytes)')

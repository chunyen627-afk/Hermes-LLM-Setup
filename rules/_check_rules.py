"""確認每條規則都真的進了 system prompt。

三層檢查：
  1. WIN_RULES（app.py）有沒有這一節
  2. _deploy_rules.py 的 WANT 清單有沒有收錄
  3. SOUL.md 有沒有
  4. Hermes 實際使用的 system prompt 有沒有
只要有一層漏掉就會出現「規則寫了但沒生效」。
"""
import io, os, re, sqlite3, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

APP = os.path.join(os.environ['USERPROFILE'], 'OneDrive', 'Desktop', 'hermes',
                   'remote-station', 'gui', 'app.py')
DEPLOY = os.path.join(os.environ['USERPROFILE'], 'OneDrive', 'Desktop', 'hermes',
                      '_deploy_rules.py')
SOUL = os.path.join(os.environ['LOCALAPPDATA'], 'hermes', 'SOUL.md')
DB = os.path.join(os.environ['LOCALAPPDATA'], 'hermes', 'state.db')

app = io.open(APP, encoding='utf-8').read()
deploy = io.open(DEPLOY, encoding='utf-8').read()
soul = io.open(SOUL, encoding='utf-8').read()

try:
    c = sqlite3.connect(DB)
    row = c.execute('SELECT prompt FROM system_prompts ORDER BY rowid DESC LIMIT 1').fetchone()
    c.close()
    prompt = str(row[0]) if row else ''
except Exception:
    prompt = ''

# WIN_RULES 裡所有 "## " 開頭的章節標題
BS = chr(92)
titles = re.findall('"##' + BS + 's+([^"]+?)' + BS + BS + 'n"', app)
want = re.search(r'WANT\s*=\s*\[(.*?)\]', deploy, re.S)
want_items = re.findall(r"'([^']+)'", want.group(1)) if want else []

print(f'system prompt: {len(prompt):,} 字元\n')
print(f'{"章節":<34} {"WANT":<6} {"SOUL":<6} {"prompt"}')
print('-' * 60)

missing = []
for t in titles:
    key = re.sub(r'^[^\w一-鿿]+', '', t).strip()
    in_want = any(w in t for w in want_items)
    in_soul = key[:8] in soul
    in_prompt = key[:8] in prompt
    mark = lambda b: ' ✓   ' if b else ' ✗   '
    print(f'{t[:32]:<34}{mark(in_want)}{mark(in_soul)}{mark(in_prompt)}')
    if in_want and not in_prompt:
        missing.append(t)

print()
if missing:
    print('⚠ 收錄在 WANT 但沒進 prompt：')
    for m in missing:
        print(f'   {m}')
else:
    print('✓ WANT 收錄的章節都有進 prompt')

print()
print('WANT 清單:', want_items)

"""把 WIN_RULES 的重點寫進 Hermes 的全域 SOUL.md。

SOUL.md 在 HERMES_HOME，**不管在哪個專案都會載入**
（prompt_builder.py 的註解：「SOUL.md from HERMES_HOME is independent
and always included when present」）—— 所以規則放這裡一份就好，
不用每個專案放 .hermes.md 再各自維護。

專案級的 .hermes.md 只在「某個專案要覆寫通用規則」時才需要。

⚠ 不要碰 ~/.claude/CLAUDE.md —— 那是 Claude Code CLI 用的，用途不同。

流程：改 app.py 的 WIN_RULES -> _extract_rules.py -> 這支
"""
import io
import os
import re

SRC = os.path.join(os.environ['USERPROFILE'], 'OneDrive', 'Desktop',
                   'hermes', 'CLAUDE.md')
SOUL = os.path.join(os.environ['LOCALAPPDATA'], 'hermes', 'SOUL.md')

MARK = '<!-- WIN_RULES:start -->'
END = '<!-- WIN_RULES:end -->'

# 只挑真正影響行為的章節，不要整份 42K 塞進 system prompt
WANT = ['使用者中途說話', '先在快的地方驗證', '省 context']


def main():
    t = io.open(SRC, encoding='utf-8').read()
    parts = []
    for w in WANT:
        m = re.search(r'(## [^\n]*' + re.escape(w) + r'.*?)(?=\n## |\Z)', t, re.S)
        if m:
            parts.append(m.group(1).rstrip())
        else:
            print('  [!] 找不到章節：' + w)

    if not parts:
        print('  沒有內容可部署，中止')
        return

    orig = io.open(SOUL, encoding='utf-8').read()
    base = orig.split(MARK)[0].rstrip()      # 重跑時整段換掉，不累積

    block = (
        '\n\n' + MARK + '\n'
        '<!-- 自動產生：改規則請改 remote-station/gui/app.py 的 WIN_RULES，'
        '跑 _extract_rules.py 再跑這支 -->\n\n'
        '# 這台機器的工作規則\n\n'
        + '\n\n---\n\n'.join(parts)
        + '\n\n' + END + '\n'
    )

    io.open(SOUL, 'w', encoding='utf-8').write(base + block)
    print('  OK ' + SOUL)
    print('     {:,} -> {:,} 字元'.format(len(orig), len(base + block)))
    print('     (SOUL.md 是全域的，所有專案都會載入)')


if __name__ == '__main__':
    main()

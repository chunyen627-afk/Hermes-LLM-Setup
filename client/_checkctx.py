import json
import sys
import io
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

server = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
url = 'http://{}:8001/slots'.format(server)

try:
    with urllib.request.urlopen(url, timeout=8) as r:
        slots = json.loads(r.read().decode('utf-8'))
except Exception as e:
    print()
    print('  [ERROR] 連不到 {} — 伺服器啟動了嗎？'.format(url))
    print('  {}'.format(e))
    sys.exit(1)

s = slots[0]
n = s.get('n_ctx') or 0
used = s.get('n_prompt_tokens') or 0
cache = s.get('n_prompt_tokens_cache') or 0
proc = s.get('n_prompt_tokens_processed') or 0
busy = s.get('is_processing')

pct = used * 100 // max(n, 1)
bar = used * 40 // max(n, 1)

print()
print('  伺服器: {}:8001'.format(server))
print('  ' + '-' * 44)
print('  ctx 上限   : {:>9,}'.format(n))
print('  目前用掉   : {:>9,}   ({}%)'.format(used, pct))
print('  剩餘       : {:>9,}   ({}%)'.format(n - used, 100 - pct))
print()
print('  快取命中   : {:>9,}   (省下這麼多重算)'.format(cache))
print('  這輪重算   : {:>9,}'.format(proc))
print('  推理中     : {}'.format(busy))
print()
print('  [' + '#' * bar + '.' * (40 - bar) + '] {}%'.format(pct))
print()

if pct > 85:
    print('  ⚠ context 快滿了，Claude Code 會開始自動壓縮對話')
elif cache > 0 and used > 1000:
    print('  ✓ 快取生效中（命中率 {}%）'.format(cache * 100 // max(used, 1)))

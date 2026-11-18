"""Finite, read-only runtime observer; never sends a model prompt or changes services."""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
HOME = ROOT.parent
SESSION = '20261007_222252_29afa6'
parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=int, default=40)
parser.add_argument('--interval', type=int, default=10)
args = parser.parse_args()
assert 0 <= args.seconds <= 45 and 1 <= args.interval <= 15
output = ROOT / 'desktop-game-performance-20261007.jsonl'
deadline = time.monotonic() + args.seconds
while True:
    with urllib.request.urlopen('http://127.0.0.1:8001/metrics', timeout=5) as response:
        metrics = json.load(response)
    dbpath = HOME / 'AppData/Local/hermes/state.db'
    db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    rows = db.execute('SELECT id,role,timestamp,finish_reason,tool_name,tool_calls,length(content) AS content_chars,length(reasoning_content) AS reasoning_chars FROM messages WHERE session_id=? AND id>=? ORDER BY id DESC LIMIT 12', (SESSION, 33662)).fetchall()
    messages = []
    for row in rows:
        item = dict(row)
        calls = json.loads(item.pop('tool_calls') or '[]')
        item['tool_calls'] = [call.get('function', {}).get('name') for call in calls]
        messages.append(item)
    db.close()
    record = {'time': datetime.datetime.now().astimezone().isoformat(), 'session': SESSION,
              'live': metrics['live'], 'requests': metrics['requests'], 'messages': messages,
              'hardware': metrics.get('hardware', {}), 'totals': metrics.get('totals', {})}
    with output.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(json.dumps({'time': record['time'], 'live': record['live'], 'latest_messages': messages[:3]}, ensure_ascii=False), flush=True)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    time.sleep(min(args.interval, remaining))

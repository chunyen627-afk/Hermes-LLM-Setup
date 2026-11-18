"""Prove an independent chat response while an actual H3 job is running."""
import argparse
import json
from pathlib import Path
import time
import requests

parser = argparse.ArgumentParser()
parser.add_argument('--label', default='')
args = parser.parse_args()
assert all(c.isalnum() or c in '-_' for c in args.label)
suffix = ('-' + args.label) if args.label else ''
started = time.monotonic()
report = {'status': 'failed'}
try:
    queue = requests.get('http://127.0.0.1:8189/queue', timeout=10).json()
    assert queue['queue_running'], 'No H3 job running; do not claim overlap'
    state = requests.get('http://127.0.0.1:8001/v1/status', timeout=10).json()
    assert state['cache_max_tokens'] == 32768
    report['queue_at_start'] = queue
    report['runtime_at_start'] = state
    response = requests.post('http://127.0.0.1:1234/v1/chat/completions', json={
        'model': state['model'], 'messages': [{'role': 'user', 'content': '請只回覆 VIDEO_CHAT_OK。'}],
        'max_tokens': 128, 'reasoning_effort': 'none', 'temperature': 0, 'stream': False,
    }, timeout=180)
    response.raise_for_status()
    report['chat'] = response.json()
    assert 'VIDEO_CHAT_OK' in (report['chat']['choices'][0]['message'].get('content') or '')
    report['runtime_after'] = requests.get('http://127.0.0.1:8001/v1/status', timeout=10).json()
    report['status'] = 'passed'
finally:
    report['elapsed_s'] = round(time.monotonic() - started, 2)
    output = Path(__file__).with_name('video-overlap-verification' + suffix + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'elapsed_s': report['elapsed_s']}))

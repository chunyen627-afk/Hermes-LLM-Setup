"""Host-side checks; client-side ZeroTier routing must be checked on the other PC."""
import http.client
import json
from pathlib import Path
import time
import threading
import psutil
import requests
from zerotier_api import RestrictedBridge

ROOT = Path(__file__).resolve().parent
BASE = 'http://10.35.219.64:1235'
MODEL = 'orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs'
session = requests.Session()
session.trust_env = False
report = {'status': 'failed', 'scope': 'host via ZeroTier address; remote client not tested', 'checks': {}}
checks = report['checks']
started = time.monotonic()
try:
    models = session.get(BASE + '/v1/models', timeout=10)
    models.raise_for_status()
    ids = [m['id'] for m in models.json()['data']]
    assert MODEL in ids, ids
    checks['model_ids'] = ids
    # Administrative endpoints are never forwarded.
    assert session.post(BASE + '/v1/unload', json={}, timeout=5).status_code == 404
    checks['admin_path_rejected'] = True
    response = session.post(BASE + '/v1/chat/completions', data=b'x',
                            headers={'Transfer-Encoding': 'identity'}, timeout=5)
    assert response.status_code == 413
    checks['transfer_encoding_rejected'] = True
    # An isolated loopback server exercises the exact deployed request handler.
    # Windows does not route a Wi-Fi source IP to its own ZeroTier listener.
    from http.server import ThreadingHTTPServer
    probe = ThreadingHTTPServer(('127.0.0.1', 0), RestrictedBridge)
    probe.allowed_clients = {'10.35.219.158', '10.35.219.64'}
    worker = threading.Thread(target=probe.serve_forever, daemon=True)
    worker.start()
    try:
        blocked = session.get(f'http://127.0.0.1:{probe.server_port}/v1/models',
                              headers={'X-Forwarded-For': '10.35.219.158'}, timeout=5)
        assert blocked.status_code == 403
        checks['isolated_handler_rejects_other_peer_and_spoofed_header'] = True
    finally:
        probe.shutdown()
        probe.server_close()
        worker.join(timeout=3)
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Reply with exactly ZEROTIER_OK.'}],
            'max_tokens': 48, 'temperature': 0, 'reasoning_effort': 'none'}
    response = session.post(BASE + '/v1/chat/completions', json=body, timeout=180)
    response.raise_for_status()
    data = response.json()
    assert 'ZEROTIER_OK' in (data['choices'][0]['message'].get('content') or ''), data
    checks['chat'] = data
    body['stream'] = True
    with session.post(BASE + '/v1/chat/completions', json=body, stream=True, timeout=180) as response:
        response.raise_for_status()
        content, done, events = '', False, 0
        for line in response.iter_lines(chunk_size=1):
            if not line.startswith(b'data:'): continue
            event = line[5:].strip()
            if event == b'[DONE]': done = True; break
            item = json.loads(event)
            for choice in item.get('choices', []):
                content += choice.get('delta', {}).get('content') or ''
            events += 1
        assert done and 'ZEROTIER_OK' in content
    checks['stream'] = {'content': content, 'events': events, 'done': done}
    body.pop('stream')
    body['max_tokens'] = 128
    body['messages'] = [{'role': 'user', 'content': 'Call report_connection with value ZEROTIER_TOOL_OK. Do not answer in prose.'}]
    body['tools'] = [{'type': 'function', 'function': {'name': 'report_connection',
                       'description': 'Report the requested test string.', 'parameters': {
                           'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}}]
    response = session.post(BASE + '/v1/chat/completions', json=body, timeout=180)
    response.raise_for_status()
    message = response.json()['choices'][0]['message']
    call = message['tool_calls'][0]['function']
    assert call['name'] == 'report_connection'
    assert json.loads(call['arguments'])['value'] == 'ZEROTIER_TOOL_OK'
    checks['tool_call_structure'] = message
    state = json.loads((ROOT / 'media-control-state.json').read_text(encoding='utf-8'))
    listeners = [c for c in psutil.net_connections(kind='tcp') if c.status == 'LISTEN' and c.laddr.port == 1235]
    assert len(listeners) == 1 and listeners[0].laddr.ip == '10.35.219.64'
    assert listeners[0].pid in state['owned_pids']
    checks['listener'] = {'ip': listeners[0].laddr.ip, 'port': 1235, 'pid': listeners[0].pid, 'controller_owned': True}
    report['status'] = 'passed'
finally:
    report['elapsed_s'] = round(time.monotonic() - started, 2)
    (ROOT / 'zerotier-api-verification-20261010.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': list(checks), 'elapsed_s': report['elapsed_s']}))

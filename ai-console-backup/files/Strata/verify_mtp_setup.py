"""Read-only final configuration checks; saves evidence without running inference."""
import hashlib
import json
from pathlib import Path
import time
import psutil
import requests

ROOT = Path(__file__).resolve().parent
def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

state = read(ROOT / 'media-control-state.json')
assert state['mode'] == 'Image' and '已就緒' in state['status'], state['status']
assert not state['error'] and not state['closed']
status = requests.get('http://127.0.0.1:8001/v1/status', timeout=10).json()
assert status['loaded'] and status['cache_max_tokens'] == 262144
assert status['vision']['available'] and status['vision']['enabled']
assert status['activity']['in_flight'] == 0
engines = []
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    if (p.info['name'] or '').lower() == 'strata.exe':
        engines.append(p.info)
assert len(engines) == 1, engines
cmd = engines[0]['cmdline']
assert '--mtp' in cmd and cmd[cmd.index('--spec-min-p') + 1] == '0.7'
assert engines[0]['pid'] in state['owned_pids']
ports = {8001, 1234, 8188, 8003, 5000}
listeners = {c.laddr.port: c.pid for c in psutil.net_connections(kind='tcp')
             if c.status == psutil.CONN_LISTEN and c.laddr.port in ports}
assert set(listeners) == ports and set(listeners.values()) <= set(state['owned_pids'])
proof = {}
for name in ('native-vision', 'static-coexistence', 'hermes-tool', 'video-overlap'):
    filename = name + '-verification-mtp-final-20261008.json'
    value = read(ROOT / filename)
    assert value['status'] == 'passed', filename
    proof[name] = filename
job = read(ROOT.parent / 'ComfyUI-H3/user/default/h3_jobs/mtp-validation-4b3d2e12.json')
assert job['state'] == 'completed', job
medians = {}
for name in ('before-mtp', 'image-mtp-final', 'video-before-mtp', 'video-mtp-balanced'):
    value = read(ROOT / ('tps-' + name + '-20261008.json'))
    assert value['status'] == 'passed'
    medians[name] = value['median_tps']
configs = {}
for name in ('strata-orca-iq3_xxs-image.json', 'strata-orca-iq3_xxs.json'):
    value = read(ROOT / name)
    assert '--mtp' in value['args']
    assert value['args'][value['args'].index('--spec-min-p') + 1] == '0.7'
    configs[name] = {'sha256': hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), 'config': value}
log = (ROOT / 'strata-orca-iq3_xxs-image.log').read_text(encoding='utf-8', errors='replace')
draft_lines = [line for line in log.splitlines()
               if 'draft layer loaded' in line or 'draft head over' in line][-2:]
assert any('draft layer loaded' in line for line in draft_lines)
assert any('draft head over' in line for line in draft_lines)
report = {'status': 'passed', 'time': time.time(), 'mode': 'Image', 'context': 262144,
          'controller_pid': state['pid'], 'engine': engines[0], 'listeners': listeners,
          'configs': configs, 'median_tps': medians, 'functional_evidence': proof,
          'h3_job': job, 'draft_load_log': draft_lines,
          'verified_source_tensors': len(read(ROOT.parent / 'Strata-data/mtp/tensors/verified.json')),
          'limits': ['No native GUI click verification this turn', 'No full 256K fill test',
                     'Decode TPS is not end-to-end task speed', 'No long-term stability claim']}
output = ROOT / 'mtp-setup-verification-20261008.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': report['status'], 'mode': report['mode'], 'median_tps': medians,
                  'report': str(output)}, ensure_ascii=False))

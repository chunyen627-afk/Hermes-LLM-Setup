"""Compare retained model assets before and after exact old-chat cleanup."""
import argparse
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
HOME = ROOT.parent
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['before', 'after'])
args = parser.parse_args()
plan = json.loads((ROOT / 'model-cleanup-plan.json').read_text(encoding='utf-8-sig'))
paths = {Path(r['path']) for r in plan['preserved_gguf_files']}
for directory in (HOME / 'Strata-data/models/orca-iq3_xxs',
                  HOME / 'ComfyUI-H3/models',
                  HOME / 'OneDrive/Desktop/hermes/ComfyUI/models',
                  HOME / '.cache/huggingface/hub/models--unsloth--Qwen3-VL-8B-Instruct-unsloth-bnb-4bit'):
    assert directory.is_dir(), str(directory)
    paths.update(p for p in directory.rglob('*') if p.is_file() and p.suffix in ('.gguf', '.safetensors'))
assets = {}
for path in sorted(paths):
    stat = path.stat()
    assets[str(path)] = {'bytes': stat.st_size, 'physical_file_id': [stat.st_dev, stat.st_ino],
                         'modified_ns': stat.st_mtime_ns}
baseline = ROOT / 'cleanup-assets-before.json'
if args.stage == 'before':
    assert not plan['executed'], 'Cleanup is already complete'
    baseline.write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding='utf-8')
else:
    assert assets == json.loads(baseline.read_text(encoding='utf-8')), 'A retained asset changed'
    assert plan['executed'] and all(r.get('removed') for r in plan['proposed_files'])
    assert all(not Path(r['path']).exists() for r in plan['proposed_files'])
    with urllib.request.urlopen('http://127.0.0.1:8001/v1/status', timeout=15) as response:
        state = json.load(response)
    assert state['service'] == 'strata' and state['loaded'] and state['vision']['enabled']
    assert state['model'] == 'orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs'
    report = {'status': 'passed', 'retained_assets': len(assets), 'retained_bytes': sum(r['bytes'] for r in assets.values()),
              'all_nine_old_chat_files_absent': True, 'current_model_loaded': True, 'native_vision_enabled': True}
    (ROOT / 'cleanup-assets-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'stage': args.stage, 'retained_assets': len(assets), 'passed': True}))

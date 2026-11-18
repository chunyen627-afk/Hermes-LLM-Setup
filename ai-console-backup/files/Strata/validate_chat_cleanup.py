"""Validate the conditional, exact chat-only deletion proposal; do not delete."""
import argparse
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).parent
HOME = Path(r'C:\Users\pjunm')
ALLOWED = (HOME / '.cache' / 'huggingface' / 'hub', HOME / '.lmstudio' / 'models')
parser = argparse.ArgumentParser()
parser.add_argument('--scope', choices=['27B', 'All'], default='All')
parser.add_argument('--authorization-file', type=Path)
args = parser.parse_args()

for report_name in ('live-api-verification', 'hermes-chat-verification',
                    'hermes-vision-verification', 'static-coexistence-verification',
                    'hermes-video-verification', 'video-overlap-verification'):
    report = json.loads((ROOT / (report_name + '.json')).read_text(encoding='utf-8'))
    assert report['status'] == 'passed', f'Acceptance missing: {report_name}'
if args.scope == 'All' and args.authorization_file is None:
    report = json.loads((ROOT / 'desktop-ui-verification.json').read_text(encoding='utf-8'))
    assert report['status'] == 'passed', 'Desktop UI acceptance missing'
with urllib.request.urlopen('http://127.0.0.1:8001/v1/status', timeout=10) as response:
    state = json.load(response)
assert state['service'] == 'strata' and state['loaded'] and state['vision']['enabled']
assert state['model'] == 'orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs'
plan = json.loads((ROOT / 'model-cleanup-plan.json').read_text(encoding='utf-8-sig'))
assert not plan['executed'] and len(plan['proposed_files']) == 9
selected = [r for r in plan['proposed_files'] if not r.get('removed') and
            (args.scope == 'All' or Path(r['path']).name.startswith('Qwen3.8-27B-'))]
assert selected, 'Nothing selected for cleanup'
if args.authorization_file is not None:
    assert args.scope == 'All' and len(selected) == 5, 'Authorization covers exactly the remaining five files'
    authorization_path = args.authorization_file.resolve(strict=True)
    assert authorization_path == ROOT / 'cleanup-authorization-20261007.json'
    authorization = json.loads(authorization_path.read_text(encoding='utf-8'))
    assert authorization['authorized'] is True and authorization['desktop_ui_pending_accepted'] is True
    assert authorization['user_answer'] == '授權現在刪除這 5 個舊聊天模型'
    assert len(authorization['files']) == 5
    assert set(authorization['files']) == {r['path'] for r in selected}, 'Authorization does not match exact deletion targets'
if args.scope == '27B':
    assert len(selected) == 4, '27B cleanup must contain exactly its two weights and two drafts'
for record in selected:
    path = Path(record['path'])
    target = path.resolve(strict=True)
    assert any(target.is_relative_to(root.resolve()) for root in ALLOWED), str(target)
    assert target == Path(record['resolved_path']), str(target)
    assert path.is_file() and not path.is_symlink() and not record['symlink']
    assert 'mmproj' not in path.name.lower() and 'projector' not in path.name.lower()
    stat = path.stat()
    assert stat.st_size == record['bytes'], str(path)
    assert [stat.st_dev, stat.st_ino] == record['physical_file_id'], str(path)
for record in plan['preserved_gguf_files']:
    path = Path(record['path'])
    assert path.is_file() and path.stat().st_size == record['bytes'], str(path)
print(json.dumps({'validated': True, 'scope': args.scope, 'files': len(selected),
                  'explicit_authorization_file': str(args.authorization_file) if args.authorization_file else None,
                  'GiB': round(sum(r['bytes'] for r in selected) / 1024**3, 2)}))

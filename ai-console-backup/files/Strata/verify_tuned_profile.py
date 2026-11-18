"""Check deployed local profile selection through real Hermes imports."""
import copy
import json
import os
from pathlib import Path
import sys
import uuid

home = Path(r'C:\Users\pjunm\AppData\Local\hermes')
os.environ['HERMES_HOME'] = str(home)
sys.path.insert(0, str(home / 'hermes-agent'))
import yaml
from tui_gateway.local_video_profile import local_video_options
from tui_gateway.server import _make_agent

cfg = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8'))
model = 'orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs'
runtime = {'provider': 'lmstudio', 'base_url': 'http://127.0.0.1:1234/v1'}
cases = []
for context in (32768, 65536):
    for mode in ('Image', 'Video'):
        candidate = copy.deepcopy(cfg)
        candidate['model'].update(context_length=context, local_media_mode=mode)
        result = local_video_options(candidate, runtime, model, 'USER_PERSONALITY')
        prompt = result['ephemeral_system_prompt']
        assert ('8003/generate' in prompt) == (mode == 'Image')
        assert ('h3-local-video' in prompt) == (mode == 'Video')
        assert 'USER_PERSONALITY' in prompt and 'vision_analyze' in prompt
        assert result['max_tokens'] == 8192
        cases.append({'mode': mode, 'context': context, 'status': 'passed'})
identifier = 'tps-profile-' + uuid.uuid4().hex
agent = _make_agent(identifier, identifier, platform_override='desktop',
                    reasoning_config_override={'enabled': True, 'effort': 'low'})
try:
    assert agent.model == model and agent.max_tokens == 8192
    report = {'status': 'passed', 'cases': cases, 'model': agent.model,
              'max_tokens': agent.max_tokens, 'model_settings': {
                  k: cfg['model'].get(k) for k in ('context_length', 'local_media_mode')},
              'scope': 'Real desktop backend builder and profile imports; no desktop renderer automation.'}
finally:
    agent.close()
Path(__file__).with_name('tuned-profile-verification.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))

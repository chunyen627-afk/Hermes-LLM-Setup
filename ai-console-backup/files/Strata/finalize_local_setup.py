"""Read-only acceptance of the deployed controller/config; save a compact receipt."""
import hashlib
import json
from pathlib import Path
import time
import psutil
import requests

root=Path(__file__).resolve().parent
def read(name): return json.loads((root/name).read_text(encoding='utf-8-sig'))
cfg=read('strata-orca-iq3_xxs-image.json')
candidate=read('strata-tune-256k-stream.json')
assert cfg['args']==candidate['args'] and cfg['gpu']==candidate['gpu'] and cfg['layer_split']==candidate['layer_split']
state=read('media-control-state.json')
assert state['mode']=='Image' and state['status'].endswith('已就緒') and not state['closed']
status=requests.get('http://127.0.0.1:8001/v1/status',timeout=10).json()
metrics=requests.get('http://127.0.0.1:8001/metrics',timeout=10).json()
assert status['loaded'] and status['cache_max_tokens']==262144 and status['vision']['enabled']
assert metrics['engine']['kv_resident']==32768
engines=[p.info['pid'] for p in psutil.process_iter(['pid','name']) if p.info['name']=='strata.exe']
assert len(engines)==1 and engines[0] in state['owned_pids']
required={8001:'/v1/models',1234:'/v1/models',8188:'/queue',8003:'/status',5000:'/'}
ports={c.laddr.port:c.pid for c in psutil.net_connections(kind='tcp') if c.status=='LISTEN' and c.laddr.port in required}
assert set(ports)==set(required)
assert all(pid in state['owned_pids'] for pid in ports.values())
for port,path in required.items(): requests.get(f'http://127.0.0.1:{port}{path}',timeout=10).raise_for_status()
checks=['hermes-tool-verification-tps256.json','native-vision-verification-tps256.json',
        'static-coexistence-verification-tps256.json','tuned-profile-verification.json',
        'media-control-verification.json','control-lifecycle-verification.json']
for name in checks: assert read(name)['status']=='passed', name
final=read('tps-managed-final.json')
assert final['status']=='passed' and len(final['cases'])==3
assert final['weighted_tps']>=19.5, 'Final rate below about 20 TPS target'
context=read('context-tps-ctx256-stream.json')
report={'status':'passed','time':time.time(),'mode':'Image','context':262144,
        'controller_pid':state['pid'],'owned_service_listeners':ports,'single_engine_pid':engines[0],
        'engine':metrics['engine'],'static_config_sha256':hashlib.sha256((root/'strata-orca-iq3_xxs-image.json').read_bytes()).hexdigest(),
        'baseline_median_tps':read('tps-baseline256k.json')['median_tps'],
        'final_median_tps':final['median_tps'],'final_weighted_tps':final['weighted_tps'],
        'final_case_tps':[c['metrics']['decode_tok_s'] for c in final['cases']],
        'context_cases':[{'input_tokens':c['usage']['prompt_tokens'],'tps':c['metrics']['decode_tok_s'],
                          'cold_prompt_s':c['metrics']['prompt_ms']/1000} for c in context['cases']],
        'passed_checks':checks,'limitations':['Not a full 256K input test','Image generation can reduce simultaneous chat TPS',
                                            'Hermes desktop backend tested; its renderer not automated',
                                            'No actual Windows reboot performed']}
(root/'final-local-setup-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))

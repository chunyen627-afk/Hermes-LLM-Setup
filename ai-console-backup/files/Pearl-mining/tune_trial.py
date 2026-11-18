"""Bounded tuning trial; reuse job cleanup and thermal protection, always restore."""
import json, subprocess, time
import guarded_miner as g

BASE = [(100,900),(100,750),(100,900)]
STAGES = [
    (240, [(100,900),(100,600),(125,1500)]),
    (240, [(110,900),(100,600),(125,1500)]),
]
original_telemetry = g.telemetry
started = None
stage = -1
events = g.ROOT/'tune-events.jsonl'

def apply(settings):
    for i,(power,core) in enumerate(settings):
        subprocess.run(['nvidia-smi','-i',str(i),'-pl',str(power)],check=True,timeout=10)
        subprocess.run(['nvidia-smi','-i',str(i),'-lgc',f'{core},{core}'],check=True,timeout=10)
    time.sleep(3)

def check(rows):
    if any(r['temperature'] >= 78 for r in rows):
        raise RuntimeError('Trial early cutoff 78C; restore baseline')
    settings = STAGES[max(stage,0)][1]
    if any(r['limit'] > settings[i][0]+1 or r['core'] > settings[i][1]+30 for i,r in enumerate(rows)):
        raise RuntimeError('Trial settings mismatch: '+json.dumps(rows))

def telemetry():
    global started,stage
    if started is None: started=time.monotonic()
    elapsed=time.monotonic()-started
    boundary=0
    selected=None
    for i,(duration,settings) in enumerate(STAGES):
        boundary+=duration
        if elapsed < boundary: selected=i; break
    if selected is None: raise RuntimeError('Tuning trial completed')
    if selected != stage:
        # Check temperatures before raising limits.
        if any(r['temperature'] >= 78 for r in original_telemetry()):
            raise RuntimeError('Too hot to start next stage')
        apply(STAGES[selected][1])
        stage=selected
        with events.open('a',encoding='utf-8') as out:
            out.write(json.dumps({'time':time.strftime('%Y-%m-%d %H:%M:%S'),'stage':stage,'settings':STAGES[stage][1]})+'\n')
    return original_telemetry()

if __name__=='__main__':
    g.telemetry=telemetry
    g.check=check
    try: g.main()
    finally: apply(BASE)

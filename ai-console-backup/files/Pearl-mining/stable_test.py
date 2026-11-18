import csv, json, pathlib, subprocess, time

ROOT = pathlib.Path(__file__).resolve().parent
duration = 180
stamp = time.strftime('%Y%m%d-%H%M%S')
log = ROOT / f'stable-{stamp}.log'
samples = []
p = None
reason = 'duration complete'
try:
    with log.open('w', encoding='utf-8') as out:
        p = subprocess.Popen([str(ROOT/'krig-1.5.12'/'krig-miner.exe'), '--coin','pearl',
            '--url','stratum+ssl://prl.kryptex.network:8048','--user','<REDACTED>.llmrig',
            '--devices','0,1,2','--no-rocm','--no-intel','--gpu-no-reset-oc','--no-tui'],
            stdout=out, stderr=subprocess.STDOUT)
        start = time.monotonic()
        while time.monotonic()-start < duration:
            raw = subprocess.check_output(['nvidia-smi','--query-gpu=index,temperature.gpu,power.draw,power.limit,utilization.gpu,clocks.current.graphics,clocks.current.memory,fan.speed','--format=csv,noheader,nounits'],text=True,timeout=8)
            rows = [[float(v.strip()) for v in row] for row in csv.reader(raw.splitlines())]
            samples.append({'elapsed':round(time.monotonic()-start,1),'gpus':rows})
            if any(r[1]>=80 or r[3]>101 for r in rows):
                reason = 'thermal cutoff 80C or power limit changed'; break
            if p.poll() is not None:
                reason = f'miner exited {p.returncode}'; break
            time.sleep(3)
finally:
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=10)
        except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
    result={'reason':reason,'log':str(log),'samples':samples}
    (ROOT/f'stable-{stamp}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'reason':reason,'log':str(log),'last':samples[-1:]},indent=2))

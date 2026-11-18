"""Bounded mining session with fail-closed telemetry and Windows child cleanup."""
import csv, datetime, json, msvcrt, pathlib, re, socket, subprocess, time
import win32api, win32con, win32job

ROOT = pathlib.Path(__file__).resolve().parent
EXPECTED = ['GPU-b3a2e3f7-40f4-d0e8-7360-7a2422a20d42',
            'GPU-fecb8b54-6f1a-91ef-d2ee-d391b9007a54',
            'GPU-20db6978-0d13-b036-431c-d15f6956e1e1']

def telemetry():
    raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,temperature.gpu,power.draw,power.limit,clocks.current.graphics,fan.speed', '--format=csv,noheader,nounits'], text=True, timeout=8)
    rows = list(csv.reader(raw.splitlines()))
    if len(rows) != 3 or [r[1].strip() for r in rows] != EXPECTED:
        raise RuntimeError('GPU inventory changed; refusing to mine')
    return [dict(index=int(r[0]), temperature=float(r[2]), watts=float(r[3]), limit=float(r[4]), core=float(r[5]), fan=float(r[6])) for r in rows]

def check(rows):
    if any(r['temperature'] >= 80 for r in rows):
        raise RuntimeError('80C temperature cutoff')
    if any(r['limit'] > 101 or r['core'] > 930 for r in rows):
        raise RuntimeError('Conservative power/core settings changed; stopped')

def main():
    lock = (ROOT/'mining.lock').open('a+b')
    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    for port in (8001,8188,8189):
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(('127.0.0.1',port)) == 0:
                raise RuntimeError('Stop Local AI Console services before mining')
    check(telemetry())
    job = win32job.CreateJobObject(None, '')
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    log = ROOT/f'mining-{stamp}.log'
    stats = ROOT/f'gpu-{stamp}.jsonl'
    p = None
    start = time.monotonic()
    reason = '24 hour limit reached'
    try:
        p = subprocess.Popen([str(ROOT/'krig-1.5.12'/'krig-miner.exe'), '--coin','pearl','--url','stratum+ssl://prl.kryptex.network:8048','--user','<REDACTED>.llmrig','--devices','0,1,2','--no-rocm','--no-intel','--gpu-no-reset-oc','--no-tui','--log-file',str(log)])
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, p.pid)
        try: win32job.AssignProcessToJobObject(job, handle)
        finally: handle.Close()
        print('Guard active: 80C stop, 78C warning, 24h maximum. Ctrl+C or close window to stop.', flush=True)
        last_accepted, accepted = start, 0
        with stats.open('w',encoding='utf-8') as out:
            while time.monotonic()-start < 86400:
                rows=telemetry(); check(rows)
                out.write(json.dumps({'time':datetime.datetime.now().astimezone().isoformat(),'gpus':rows})+'\n'); out.flush()
                if any(r['temperature']>=78 for r in rows): print('WARNING: temperature >=78C',flush=True)
                if p.poll() is not None: raise RuntimeError(f'Miner exited {p.returncode}')
                if log.exists():
                    counts = re.findall(r'shares:\s*(\d+) accepted',log.read_text(encoding='utf-8',errors='replace')[-16000:])
                    count = max(map(int,counts),default=0)
                    if count>accepted: accepted=count; last_accepted=time.monotonic()
                if time.monotonic()-last_accepted > 900: raise RuntimeError('No accepted shares for 15 minutes')
                time.sleep(2)
    except KeyboardInterrupt: reason='User stopped'
    except Exception as exc: reason=str(exc)
    finally:
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
        job.Close()
        (ROOT/f'result-{stamp}.json').write_text(json.dumps({'reason':reason,'elapsed_seconds':time.monotonic()-start,'miner_log':str(log),'gpu_log':str(stats)},indent=2),encoding='utf-8')
        print('STOPPED: '+reason,flush=True)

if __name__ == '__main__':
    try: main()
    except Exception as exc: print('NOT STARTED: '+str(exc),flush=True); raise SystemExit(1)

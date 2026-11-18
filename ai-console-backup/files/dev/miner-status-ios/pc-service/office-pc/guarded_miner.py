"""Continuous mining with fail-closed telemetry and Windows child cleanup."""
import csv, datetime, json, msvcrt, pathlib, re, socket, subprocess, time, os, sys
from fan_control import Fans
from thermal_control import Thermal, BASE
import win32api, win32con, win32job

ROOT = pathlib.Path(__file__).resolve().parent

# Kryptex nodes for the same account (earnings still convert to BTC). From Taiwan on 2026-10-26:
# Hong Kong ~65 ms, Singapore ~95 ms, the global name (Germany) ~250 ms.
KRYPTEX_NODES = ('prl-hk.kryptex.network', 'prl-sg.kryptex.network', 'prl.kryptex.network')
SWITCH = ROOT/'SWITCH_NODES'  # node change request from the office node watcher

# Which miner runs: miner.json {"miner": "srbminer"} picks SRBMiner-Multi; anything else (or no
# file) keeps krig. On "office 2" (a 3070, 2026-10-29) SRBMiner 3.8.1 gave 69 TH/s at 121 W where
# krig gave 42.5 TH/s at 125 W, all shares accepted; it keeps a 2 % dev fee. It needs a Defender
# exclusion for this folder. Its numbers come from its local API, not from a log line.
# 3.8.6 since 2026-11-09 (pearlhash efficiency on NVIDIA 3000/4000/5000); 3.8.5 stays beside it to go back.
SRB_EXE = ROOT/'srbminer-3.8.6'/'SRBMiner-Multi-3-8-6'/'SRBMiner-MULTI.exe'
SRB_API = 'http://127.0.0.1:21550'

def miner_choice():
    try:
        chosen = json.loads((ROOT/'miner.json').read_text(encoding='utf-8')).get('miner')
    except (OSError, ValueError, AttributeError):
        chosen = None
    if chosen == 'srbminer' and not SRB_EXE.exists():
        print('miner.json asks for SRBMiner but it is missing (Defender?); using krig', flush=True)
        chosen = None
    return 'srbminer' if chosen == 'srbminer' else 'krig'

def srb_stats():
    """(TH/s, accepted, rejected) from SRBMiner's API, or None while it does not answer."""
    import urllib.request
    try:
        with urllib.request.urlopen(SRB_API, timeout=2) as response:
            algo = json.loads(response.read().decode('utf-8'))['algorithms'][0]
        return float(algo['hashrate']['gpu']['total']) / 1e12, int(algo['shares']['accepted']), int(algo['shares']['rejected'])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None

def kryptex_nodes(port=8048):
    """Kryptex nodes, fastest first by TCP connect time (3 tries; unreachable last). Both miners
    move down the list on their own when a node drops."""
    import socket as _socket, statistics as _statistics
    timed = []
    for host in KRYPTEX_NODES:
        samples = []
        for _ in range(3):
            began = time.perf_counter()
            try:
                with _socket.create_connection((host, port), timeout=3):
                    samples.append(time.perf_counter() - began)
            except OSError:
                pass
        timed.append((_statistics.median(samples) if samples else float('inf'), host))
    nodes = [host for _, host in sorted(timed)]
    print('Pool nodes, fastest first: ' + ', '.join(nodes), flush=True)
    return nodes

def pool_reachable(port=8048):
    """True when any Kryptex node takes a TCP connection."""
    for host in KRYPTEX_NODES:
        try:
            with socket.create_connection((host, port), timeout=3): return True
        except OSError: pass
    return False

class NetworkWait:
    """No shares while no pool node takes a connection means this PC's network is down, not the
    miner: wait for it instead of stopping (both miners reconnect on their own), and give the miner
    a fresh window once a node answers again. Each machine mines on its own line; an outage at one
    site must not leave the others, or this one, stopped. Checked at most once a minute."""
    def __init__(self, reachable=pool_reachable, clock=time.monotonic):
        self.reachable, self.clock = reachable, clock
        self.down, self.checked = False, None
    def check(self):
        """'wait' (network down), 'back' (it just returned: restart the timers) or 'fail' (the
        network is fine, so the miner is at fault)."""
        now = self.clock()
        if self.down and now-self.checked < 60: return 'wait'
        self.checked = now
        if not self.reachable():
            if not self.down: print('NETWORK DOWN: no pool node reachable; waiting instead of stopping', flush=True)
            self.down = True
            return 'wait'
        if self.down:
            self.down = False
            print('Network back; the miner gets a fresh window for shares', flush=True)
            return 'back'
        return 'fail'

def miner_command(kind, nodes, log_path):
    if kind == 'srbminer':
        pools = [f'{node}:8048' for node in nodes]
        return [str(SRB_EXE), '--algorithm', 'pearlhash', '--pool', ','.join(pools),
                '--wallet', ','.join(['<REDACTED>.llmrig'] * len(pools)), '--password', ','.join(['x'] * len(pools)),
                '--tls', ','.join(['true'] * len(pools)), '--disable-cpu', '--disable-gpu-amd', '--disable-gpu-intel',
                '--api-enable', '--api-port', '21550', '--log-file', str(log_path)]
    url_args = [arg for node in nodes for arg in ('--url', f'stratum+ssl://{node}:8048')]
    return [str(ROOT/'krig-1.5.12'/'krig-miner.exe'), '--coin','pearl',*url_args,'--user','<REDACTED>.llmrig','--devices','0,1,2','--no-rocm','--no-intel','--gpu-coffset','0','--gpu-moffset','0','--gpu-no-reset-oc','--no-tui','--log-file',str(log_path)]
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
    if any(r['temperature'] >= 82 for r in rows):
        raise RuntimeError('82C emergency cutoff')
    caps = BASE  # SET-LOW-CLOCKS.ps1 applies it; the weekday QUIET hours stay below it
    if any(r['limit'] > caps[i][0]+1 or r['core'] > caps[i][1]+30 for i,r in enumerate(rows)):
        raise RuntimeError('Balanced power/core settings changed; stopped')

def main():
    owner = None
    if os.environ.get('PEARL_PANEL_OWNER'):
        from panel_bridge import cancelled, save
        owner = win32api.OpenProcess(0x100000, False, int(os.environ['PEARL_PANEL_OWNER']))
        token = os.environ['PEARL_PANEL_TOKEN']
        if cancelled(owner, float(token)): return
    lock = (ROOT/'mining.lock').open('a+b')
    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    for port in (8001,8188,8189):
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(('127.0.0.1',port)) == 0:
                raise RuntimeError('Stop Local AI Console services before mining')
    initial=telemetry(); check(initial)
    if any(r['temperature']>=76 for r in initial):
        raise RuntimeError('Cool GPUs below 76C before starting a fresh mining session')
    job = win32job.CreateJobObject(None, '')
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    SWITCH.unlink(missing_ok=True)  # a request left from an earlier session is stale
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    log = ROOT/f'mining-{stamp}.log'
    stats = ROOT/f'gpu-{stamp}.jsonl'
    p = None
    fans = None
    watcher = None
    start = time.monotonic()
    reason = 'Session ended'
    try:
        watcher = subprocess.Popen([sys.executable,str(ROOT/'fan_restore_watch.py'),str(os.getpid())],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.PIPE,text=True)
        if watcher.stdout.readline().strip() != 'READY': raise RuntimeError('Fan cleanup watcher failed')
        fans = Fans()
        fans.update(telemetry())
        kind = miner_choice()
        print(f'Miner: {kind}', flush=True)
        def launch(nodes, log_path):
            proc = subprocess.Popen(miner_command(kind, nodes, log_path), cwd=str(SRB_EXE.parent if kind == 'srbminer' else ROOT))
            handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
            try: win32job.AssignProcessToJobObject(job, handle)
            finally: handle.Close()
            return proc
        p = launch(kryptex_nodes(), log)
        thermal=Thermal()
        print('Guard active: 76C derating, 80C minimum load, 82C/failed cooling stop. Ctrl+C or close window to stop.', flush=True)
        last_accepted, accepted = start, 0
        network = NetworkWait()
        rate = None
        rejected, data_at, accepted_at = None, None, None  # SRBMiner only: the panel state carries them
        smi_stall = None  # when nvidia-smi started timing out
        with stats.open('w',encoding='utf-8') as out:
            while True:
                if owner and cancelled(owner, float(token)):
                    reason = 'Stopped by AI Console'
                    break
                if SWITCH.exists():
                    # Written by the office node watcher: known nodes, best first; others follow.
                    wanted = [line.strip() for line in SWITCH.read_text(encoding='utf-8', errors='replace').splitlines()
                              if line.strip() in KRYPTEX_NODES]
                    SWITCH.unlink(missing_ok=True)
                    if wanted:
                        wanted += [node for node in KRYPTEX_NODES if node not in wanted]
                        print('Switching pool nodes on request: ' + ', '.join(wanted), flush=True)
                        p.terminate()
                        try: p.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            p.kill(); p.wait(timeout=5)
                        log = ROOT/f"mining-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
                        p = launch(wanted, log)
                        last_accepted, accepted = time.monotonic(), 0  # the new miner counts from zero
                # 11/08: nvidia-smi hung >8 s three times tonight while the driver logged nvlddmkm 153 on all
                # three GPUs at once (the miner kept hashing, HW:0); ride out up to 60 s before stopping.
                try:
                    rows=telemetry(); smi_stall=None
                except subprocess.TimeoutExpired:
                    smi_stall = smi_stall or time.monotonic()
                    if time.monotonic()-smi_stall > 60: raise RuntimeError('nvidia-smi timed out for 60 s')
                    print('WARNING: nvidia-smi timed out, retrying',flush=True); time.sleep(2); continue
                check(rows)
                if watcher.poll() is not None: raise RuntimeError('Fan cleanup watcher exited')
                fan_readings=fans.update(rows)
                derating=thermal.update(rows)
                if derating: print('THERMAL DERATING: '+json.dumps(derating),flush=True)
                out.write(json.dumps({'time':datetime.datetime.now().astimezone().isoformat(),'gpus':rows,'fans':fan_readings,'derating':derating})+'\n'); out.flush()
                if any(r['temperature']>=78 for r in rows): print('WARNING: temperature >=78C',flush=True)
                if p.poll() is not None: raise RuntimeError(f'Miner exited {p.returncode}')
                if kind == 'srbminer':
                    got = srb_stats()
                    if got:
                        rate, count, rejected = got
                        data_at = datetime.datetime.now().astimezone().isoformat()
                        if count > accepted: accepted, last_accepted, accepted_at = count, time.monotonic(), data_at
                elif log.exists():
                    with log.open('rb') as source:
                        source.seek(max(0, source.seek(0, 2)-16000))
                        tail = source.read().decode('utf-8',errors='replace')
                    counts = re.findall(r'shares:\s*(\d+) accepted',tail)
                    rates = re.findall(r'Total:\s*([\d.]+) TH/s',tail)
                    if rates: rate = float(rates[-1])
                    count = max(map(int,counts),default=0)
                    if count>accepted: accepted=count; last_accepted=time.monotonic()
                if network.down or time.monotonic()-last_accepted > 900:
                    verdict = network.check()
                    if verdict == 'fail': raise RuntimeError('No accepted shares for 15 minutes')
                    if verdict == 'back': last_accepted = time.monotonic()
                if owner:
                    save(dict(token=token,status='running',gpus=rows,accepted=accepted,th_s=rate,
                              log=str(log),pid=os.getpid(),miner=kind,rejected=rejected,data_at=data_at,
                              last_accepted_at=accepted_at), strict=False)
                time.sleep(2)
    except KeyboardInterrupt: reason='User stopped'
    except Exception as exc: reason=str(exc)
    finally:
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
        job.Close()
        if fans:
            try: fans.restore()
            except Exception as exc: print('Fan restore failed: '+str(exc),flush=True)
        (ROOT/f'result-{stamp}.json').write_text(json.dumps({'reason':reason,'elapsed_seconds':time.monotonic()-start,'miner_log':str(log),'gpu_log':str(stats)},indent=2),encoding='utf-8')
        print('STOPPED: '+reason,flush=True)

if __name__ == '__main__':
    try: main()
    except Exception as exc: print('NOT STARTED: '+str(exc),flush=True); raise SystemExit(1)

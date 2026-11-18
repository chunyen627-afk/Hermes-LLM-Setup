"""Portable guard; settings must be prepared locally, never auto-overclock a new PC."""
import csv
import datetime
import json
import msvcrt
from pathlib import Path
import re
import socket
import subprocess
import time
import math
import os
import threading
import win32api
import win32con
import win32job
import win32process
import win32event

ROOT = Path(__file__).resolve().parent

# Kryptex nodes for the same account (earnings still convert to BTC). From Taiwan on 2026-10-26:
# Hong Kong ~65 ms, Singapore ~95 ms, the global name (Germany) ~250 ms.
KRYPTEX_NODES = ('prl-hk.kryptex.network', 'prl-sg.kryptex.network', 'prl.kryptex.network')
SWITCH = ROOT/'SWITCH_NODES'  # node change request from the office node watcher
STOP_REQUEST = ROOT/'STOP_REQUEST'  # stop request from the phone, through the office status service

# Which miner runs: miner.json {"miner": "srbminer"} picks SRBMiner-Multi; anything else (or no
# file, or a missing exe) keeps krig. On the office PCs (2026-10-29) SRBMiner 3.8.1 gave far more
# per watt than krig, all shares accepted; it keeps a 2 % dev fee. It lives in its own folder with a
# Defender exclusion (this folder holds quarantined files and is not excluded).
# 3.8.6 since 2026-11-09 (pearlhash efficiency on NVIDIA 3000/4000/5000); 3.8.5 stays beside it to go back.
SRB_EXE = Path(r'C:\Users\user\srbminer\SRBMiner-Multi-3-8-6\SRBMiner-MULTI.exe')
SRB_API = 'http://127.0.0.1:21550'

def miner_kind():
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

def pool_down(tail):
    """True when the latest pool event in SRBMiner's log is a failure. On a lossy line a TCP connect
    can succeed while the miner still cannot get a pool session (10/31 12:20), so this counts too."""
    good = tail.rfind(b'Connected to ')
    bad = max(tail.rfind(b'not connected to a pool'), tail.rfind(b"Couldn't connect to pool"))
    return bad > good

class KrigStyleLog:
    """Writes SRBMiner's numbers into mining-<stamp>.log as krig-format lines, so the read-only
    monitor, this guard's progress checks and the office node watcher read it unchanged."""
    def __init__(self, path, srb_log):
        self.path, self.srb_log = path, srb_log
        self.accepted, self.node, self.last_total = 0, None, 0.0
        self.rate, self.yielding = None, False  # latest TH/s from the API; set by YieldWatch
        self.pool_down = False  # SRBMiner's own log: its latest pool event is a failed connection
    def write(self, text):
        clock = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with self.path.open('a', encoding='utf-8') as out:
            out.write(f'{clock} {text}\n')
    def update(self):
        try:
            with self.srb_log.open('rb') as source:
                source.seek(max(0, source.seek(0, 2)-16000))
                tail = source.read()
                self.pool_down = pool_down(tail)
                found = re.findall(rb'Connected to ([^:\s]+):\d+', tail)
            if found and found[-1].decode() != self.node:
                self.node = found[-1].decode()
                self.write(f'connected: stratum+ssl://{self.node}:8048')
        except OSError:
            pass
        got = srb_stats()
        self.rate = got[0] if got else None
        if got is None:
            return
        rate, accepted, rejected = got
        for _ in range(max(0, accepted-self.accepted)):
            self.write('share accepted: GPU0')
        self.accepted = max(self.accepted, accepted)
        # Written whenever the API answers, zero included: a pool reconnect on a flaky line (10/31: 2 min
        # without any node at 12:20) must not read as a stalled miner. A dead miner stops answering; a
        # live one that finds nothing runs into the share timeout and the network check.
        if time.monotonic()-self.last_total >= 30:
            self.last_total = time.monotonic()
            self.write(f'Total: {rate:.2f} TH/s shares: {accepted} accepted 0 stale {rejected} rejected')

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
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            pass
    return False

class NetworkWait:
    """No shares or reports while no pool node takes a connection means this PC's network is down,
    not the miner: wait for it instead of stopping (both miners reconnect on their own; SRBMiner
    reports 0 TH/s meanwhile, so the report check stalls too), and give the miner a fresh window
    once a node answers again. Each machine mines on its own line; an outage at one site must not
    leave the others, or this one, stopped. Checked at most once a minute."""
    def __init__(self, reachable=None, clock=time.monotonic):
        self.reachable, self.clock = reachable or pool_reachable, clock
        self.down, self.checked = False, None
    def check(self):
        """'wait' (network down), 'back' (it just returned: restart the timers) or 'fail' (the
        network is fine, so the miner is at fault)."""
        now = self.clock()
        if self.down and now-self.checked < 60:
            return 'wait'
        self.checked = now
        if not self.reachable():
            if not self.down:
                print('NETWORK DOWN: no pool node reachable; waiting instead of stopping', flush=True)
            self.down = True
            return 'wait'
        if self.down:
            self.down = False
            print('Network back; the miner gets a fresh window for reports and shares', flush=True)
            return 'back'
        return 'fail'

def yield_gpu(pid):
    """Puts the miner at Windows' IDLE GPU scheduling priority, so whatever else wants this card (a
    game, Moonlight streaming) gets it and the miner keeps the rest; it never stops. Measured on the
    5080 on 2026-10-31 with a Vulkan benchmark standing in for a game: at NORMAL (and BELOW_NORMAL,
    and any --gpu-intensity 8–24) the two split the card about evenly (benchmark 97 of 207 MH/s);
    at IDLE the benchmark got 223 MH/s and the miner fell to 6 TH/s, back to ~200 TH/s once alone.
    Set right after launch it did not stay (read back NORMAL once the miner had opened the card), so
    the guard checks it every 30 s. Returns 'kept' (already IDLE), 'set' or 'failed'."""
    import ctypes
    kernel32, gdi32 = ctypes.WinDLL('kernel32'), ctypes.WinDLL('gdi32')
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x0200 | 0x0400, False, int(pid))  # SET_ and QUERY_INFORMATION
    if not handle:
        return 'failed'
    try:
        current = ctypes.c_int(-1)
        if gdi32.D3DKMTGetProcessSchedulingPriorityClass(ctypes.c_void_p(handle), ctypes.byref(current)) == 0 and current.value == 0:
            return 'kept'
        return 'set' if gdi32.D3DKMTSetProcessSchedulingPriorityClass(ctypes.c_void_p(handle), 0) == 0 else 'failed'  # 0 = IDLE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))

def gpu_busy():
    """True when the card is mostly busy: with the miner slowed down, that means others are using it."""
    try:
        output = subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                                         text=True, timeout=8)
        return max(float(value) for value in output.split()) >= 50
    except (OSError, subprocess.SubprocessError, ValueError):
        return False

class YieldWatch:
    """While the miner gives way (its rate under half its best this run while the card stays busy),
    the time does not count toward the share and report timeouts: a long game must neither stop
    mining nor be mistaken for a broken miner. A slow miner on an idle card still counts."""
    def __init__(self, busy=None, clock=time.monotonic):
        self.busy, self.clock = busy or gpu_busy, clock
        self.best, self.yielding, self.last = 0.0, False, None
    def update(self, rate):
        """Seconds since the previous call spent yielding (0 when not)."""
        now = self.clock()
        elapsed = 0.0 if self.last is None else now-self.last
        self.last = now
        if rate is None:
            return 0.0
        self.best = max(self.best, rate)
        yielding = self.best > 0 and rate < self.best/2 and self.busy()
        if yielding != self.yielding:
            print(f'{rate:.1f} TH/s: ' + ('another program is using the GPU; mining gives way' if yielding else 'GPU free again; full speed'), flush=True)
        was, self.yielding = self.yielding, yielding
        return elapsed if was and yielding else 0.0

def guarded_rows(config, reapply=None, read=None, pause=2):
    """GPU readings that passed `check`. When only the limits differ (10/31 12:27: something, likely
    MSI Afterburner applying its profile after the session took over, set the power limit to 400 W)
    and `reapply` is given, the session's limits are put back once and checked again; only a change
    that does not go away stops mining. Temperature, identity and bad readings stop as before."""
    read = read or read_gpus
    rows = read()
    try:
        check(config, rows)
    except RuntimeError as error:
        if reapply is None or not str(error).startswith('GPU limits mismatch'):
            raise
        print(f'{error}; putting the session limits back', flush=True)
        reapply()
        time.sleep(pause)
        rows = read()
        check(config, rows)
    return rows

TICK = None
MINER_RESTARTS_PER_HOUR = 5  # a miner that exits more often than this is broken: stop and say so
REAPPLY = None  # set by session.py: re-applies its power limit and clock lock
TEST_SECONDS = 0

class JobProcess:
    """Assign suspended child before it can execute or escape window cleanup."""
    def __init__(self, args, job, cwd=ROOT):
        self.handle, thread, self.pid, _ = win32process.CreateProcess(
            None, subprocess.list2cmdline(args), None, None, False,
            win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
            None, str(cwd), win32process.STARTUPINFO())
        try:
            win32job.AssignProcessToJobObject(job, self.handle)
            win32process.ResumeThread(thread)
        except BaseException:
            win32api.TerminateProcess(self.handle, 1)
            self.handle.Close()
            raise
        finally:
            thread.Close()
    def poll(self):
        code = win32process.GetExitCodeProcess(self.handle)
        self.returncode = None if code == 259 else code
        return self.returncode
    def terminate(self):
        win32api.TerminateProcess(self.handle, 0)
    kill = terminate
    def wait(self, timeout):
        if win32event.WaitForSingleObject(self.handle, int(timeout*1000)) == win32event.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired('krig-miner', timeout)
        return self.poll()

def read_gpus(attempts=4, pause=5):
    """nvidia-smi can fail for a moment while Windows is short of memory or the display driver
    recovers (2026-10-31 it returned 255 twice and stopped mining); only a lasting failure stops."""
    for attempt in range(attempts):
        try:
            output = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,temperature.gpu,power.draw,power.limit,clocks.current.graphics,memory.used', '--format=csv,noheader,nounits'], text=True, timeout=8)
            break
        except (OSError, subprocess.SubprocessError):
            if attempt == attempts-1:
                raise
            print(f'nvidia-smi failed (try {attempt+1} of {attempts}); retrying', flush=True)
            time.sleep(pause)
    return {int(r[0]): dict(uuid=r[1].strip(), temperature=float(r[2]), watts=float(r[3]), power=float(r[4]), core=float(r[5]), memory=float(r[6])) for r in csv.reader(output.splitlines())}

def check(config, rows, idle=False):
    for gpu in config['gpus']:
        row = rows[gpu['index']]
        if not all(math.isfinite(row[k]) for k in ('temperature','power','core','memory')):
            raise RuntimeError('Invalid GPU telemetry')
        if row['uuid'] != gpu['uuid']:
            raise RuntimeError('GPU identity mismatch')
        if row['temperature'] >= config['stop_c']:
            raise RuntimeError('Temperature cutoff')
        if row['power'] > gpu['power_w']+1 or row['core'] > gpu['core_mhz']+30:
            raise RuntimeError(f"GPU limits mismatch: actual power={row['power']} core={row['core']}; expected <= {gpu['power_w']}W/{gpu['core_mhz']}MHz")
        if idle and row['memory'] > config['idle_memory_mib']:
            return False
    return True

def ports_closed(ports):
    for port in ports:
        with socket.socket() as sock:
            sock.settimeout(.3)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                return False
    return True


def log_progress(log):
    """Bounded reads. Incomplete lines are never treated as progress."""
    if not log.exists():
        return None, 0, None
    with log.open('rb') as source:
        size = source.seek(0, 2)
        source.seek(max(0, size-16000))
        raw = source.read().rpartition(b'\n')[0]
    totals = re.findall(rb'(?m)^([0-9:.]+) Total:\s*([0-9.]+\s+[KMGTPE]?H/s shares:\s*\d+ accepted\s+\d+ stale\s+\d+ rejected)\r?$', raw)
    counts = re.findall(rb'shares:\s*(\d+) accepted', raw)
    if not totals:
        return None, max(map(int, counts), default=0), None
    stamp, summary = totals[-1]
    return stamp, max(map(int, counts), default=0), b'Total: '+summary+b'\n'


def progress_check(signature, previous, last_progress, now, timeout=120):
    if signature is not None and signature != previous:
        return signature, now
    if now-last_progress > timeout:
        raise RuntimeError('Miner telemetry stalled')
    return previous, last_progress


def console_progress(log):
    """Only complete, whitelisted GPU data from the latest Total report frame."""
    if not log.exists():
        return None, None
    with log.open('rb') as source:
        size = source.seek(0, 2)
        source.seek(max(0, size-16000))
        raw = source.read().rpartition(b'\n')[0]
    totals = list(re.finditer(rb'(?m)^([0-9:.]+) Total:\s*([0-9.]+\s+[KMGTPE]?H/s shares:\s*\d+ accepted\s+\d+ stale\s+\d+ rejected)\r?$', raw))
    if not totals:
        return None, None
    latest = totals[-1]
    frame_start = totals[-2].end() if len(totals) > 1 else 0
    frame = raw[frame_start:latest.start()]
    gpu_pattern = re.compile(
        rb'(?m)^([0-9:.]+) GPU(\d+) [0-9A-Fa-f:.]+ (RTX 5080): '
        rb'([0-9.]+ [KMGTPE]?H/s) (\d+/\d+/\d+) ([0-9.]+ [KMGTPE]?H/W) '
        rb'([0-9.]+)W ([0-9.]+)C ([0-9.]+)C ([0-9.]+)% '
        rb'(\d+) \(([+-]?\d+)\)(?: (\d+) \(([+-]?\d+)\))?\r?$')
    lines = []
    for match in gpu_pattern.finditer(frame):
        (stamp, index, model, rate, shares, efficiency, watts, core_temp,
         memory_temp, fan, core_clock, core_offset, memory_clock, memory_offset) = match.groups()
        lines.append(stamp+b' GPU'+index+b' '+model+b': '+rate+
                     b' | Shares A/S/R: '+shares+b' | '+efficiency+b'\n')
        detail = (b'  GPU: '+core_temp+b' C | Memory: '+memory_temp+b' C | Fan: '+fan+
                  b'% | Power: '+watts+b' W | Core: '+core_clock+b' MHz ('+core_offset+b')')
        if memory_clock is not None:
            detail += b' | Memory clock: '+memory_clock+b' MHz ('+memory_offset+b')'
        lines.append(detail+b'\n')
    lines.append(latest.group(1)+b' Total: '+latest.group(2)+b'\n')
    return latest.group(1), b''.join(lines)


def relay_console(log, done):
    """Display in a separate thread; blocked console output cannot block cooling."""
    previous = None
    while not done.wait(2):
        try:
            signature, summary = console_progress(log)
            if signature is not None and signature != previous:
                if summary:
                    os.write(1, summary)
                previous = signature
        except (OSError, ValueError):
            continue

def main():
    global KRYPTEX_NODES
    config = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig'))
    if config.get('pool_nodes'):  # an outage test points the miner at a local relay
        KRYPTEX_NODES = tuple(config['pool_nodes'])
    if config.get('validated_for_this_machine') is not True:
        raise RuntimeError('New-machine validation required; read README first')
    if not re.fullmatch(r'krx[A-Za-z0-9]+', config['mining_user']) or not re.fullmatch(r'[A-Za-z0-9_-]+', config['worker']):
        raise RuntimeError('Invalid mining username or worker')
    if not config['gpus'] or len({g['index'] for g in config['gpus']}) != len(config['gpus']):
        raise RuntimeError('GPU list empty or duplicated')
    if not 0 < config['warn_c'] < config['stop_c'] <= 80 or not 0 < config['no_share_seconds'] <= 900:
        raise RuntimeError('Unsafe protection thresholds')
    lock = (ROOT/'mining.lock').open('a+b')
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    if config['ai_stop_command']:
        subprocess.run(config['ai_stop_command'], check=True, timeout=30)
    good = 0
    for _ in range(30):
        idle = check(config, read_gpus(), idle=True)
        good = good+1 if idle and ports_closed(config['ai_ports']) else 0
        if good >= 3:
            break
        time.sleep(2)
    else:
        raise RuntimeError('AI/GPU release timeout; mining not started')
    logs = ROOT/'logs'
    logs.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    log = logs/f'mining-{stamp}.log'
    SWITCH.unlink(missing_ok=True)  # a request left from an earlier session is stale
    STOP_REQUEST.unlink(missing_ok=True)
    job = win32job.CreateJobObject(None, '')
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    process = None
    started = time.monotonic()
    reason = 'Stopped'
    relay_done = threading.Event()
    try:
        print('Continuous mining; close window or Ctrl+C to stop. Thermal and no-share protection active. Miner runs without a console; summaries follow.', flush=True)
        kind = miner_kind()
        print(f'Miner: {kind}', flush=True)
        adapter = None
        def launch(nodes, log_path):
            nonlocal adapter
            user = config['mining_user']+'.'+config['worker']
            if kind == 'srbminer':
                srb_log = log_path.with_name(log_path.name.replace('mining-', 'srbminer-'))
                adapter = KrigStyleLog(log_path, srb_log)
                pools = [f'{node}:8048' for node in nodes]
                return JobProcess([str(SRB_EXE), '--algorithm', 'pearlhash', '--pool', ','.join(pools),
                                   '--wallet', ','.join([user]*len(pools)), '--password', ','.join(['x']*len(pools)),
                                   '--tls', ','.join(['true']*len(pools)), '--disable-cpu', '--disable-gpu-amd',
                                   '--disable-gpu-intel', '--api-enable', '--api-port', '21550', '--log-file', str(srb_log)],
                                  job, cwd=SRB_EXE.parent)
            url_args = [arg for node in nodes for arg in ('--url', f'stratum+ssl://{node}:8048')]
            return JobProcess([str(ROOT/'runtime'/'krig-miner.exe'), '--coin', 'pearl', *url_args, '--user', user, '--devices', ','.join(str(g['index']) for g in config['gpus']), '--no-rocm', '--no-intel', '--gpu-no-reset-oc', '--no-tui', '--log-file', str(log_path)], job)
        process = launch(kryptex_nodes(), log)
        last_share, accepted = time.monotonic(), 0
        signature, last_progress = None, time.monotonic()
        # Down means: no node takes a TCP connection, or SRBMiner itself says it cannot reach a pool.
        network = NetworkWait(lambda: pool_reachable() and not (adapter and adapter.pool_down))
        giving_way = YieldWatch()
        priority_checked, crashes = 0.0, []
        threading.Thread(target=relay_console, args=(log, relay_done), daemon=True).start()
        with (logs/f'gpu-{stamp}.jsonl').open('w', encoding='utf-8') as output:
            while True:
                rows = guarded_rows(config, REAPPLY)
                if TICK:
                    TICK(rows)
                if TEST_SECONDS and time.monotonic()-started >= TEST_SECONDS:
                    reason = 'Timed validation completed'
                    break
                output.write(json.dumps({'time': datetime.datetime.now().astimezone().isoformat(), 'gpus': rows})+'\n')
                output.flush()
                if STOP_REQUEST.exists():
                    STOP_REQUEST.unlink(missing_ok=True)
                    reason = 'Stopped from the phone'
                    break  # The finally below stops the miner; session.py then restores the GPU.
                wanted = None
                if process.poll() is not None:
                    # 11/01 04:16 SRBMiner crashed (0xC0000005) after 13 hours and this used to end mining
                    # until someone started it again: restart the miner instead, unless it keeps dying.
                    crashes = [t for t in crashes if time.monotonic()-t < 3600] + [time.monotonic()]
                    if len(crashes) > MINER_RESTARTS_PER_HOUR:
                        raise RuntimeError(f'Miner exited {process.returncode} ({len(crashes)} times within an hour)')
                    note = f'miner exited {process.returncode}; restarting it ({len(crashes)} of {MINER_RESTARTS_PER_HOUR} this hour)'
                    print(note, flush=True)
                    with log.open('a', encoding='utf-8') as out:
                        out.write(datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3] + ' ' + note + '\n')
                    time.sleep(10)
                    wanted = kryptex_nodes()
                elif SWITCH.exists():
                    # Written by the office node watcher: known nodes, best first; others follow.
                    wanted = [line.strip() for line in SWITCH.read_text(encoding='utf-8', errors='replace').splitlines()
                              if line.strip() in KRYPTEX_NODES]
                    SWITCH.unlink(missing_ok=True)
                    if wanted:
                        wanted += [node for node in KRYPTEX_NODES if node not in wanted]
                        print('Switching pool nodes on request: ' + ', '.join(wanted), flush=True)
                if wanted:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    process.handle.Close()
                    relay_done.set()
                    # The monitor pairs mining-<stamp>.log with gpu-<stamp>.jsonl (and result-<stamp>.json)
                    # to trust the log's dates, so all three move to the new stamp together.
                    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
                    log = logs/f'mining-{stamp}.log'
                    output.close()
                    output = (logs/f'gpu-{stamp}.jsonl').open('w', encoding='utf-8')
                    process = launch(wanted, log)
                    relay_done = threading.Event()
                    threading.Thread(target=relay_console, args=(log, relay_done), daemon=True).start()
                    # The new miner counts from zero and needs a moment before its first report.
                    last_share, accepted = time.monotonic(), 0
                    signature, last_progress = None, time.monotonic()
                    continue
                if adapter:
                    if config.get('yield_gpu', True) and time.monotonic()-priority_checked >= 30:
                        priority_checked = time.monotonic()
                        outcome = yield_gpu(process.pid)
                        if outcome != 'kept':
                            print(f'GPU priority IDLE (gives way to games and streaming): {outcome}', flush=True)
                    adapter.update()
                    paused = giving_way.update(adapter.rate)
                    adapter.yielding = giving_way.yielding
                    last_share += paused
                    last_progress += paused
                latest, count, _ = log_progress(log)
                if count > accepted:
                    accepted, last_share = count, time.monotonic()
                try:
                    signature, last_progress = progress_check(latest, signature, last_progress, time.monotonic())
                    if network.down or time.monotonic()-last_share > config['no_share_seconds']:
                        raise RuntimeError('No accepted shares within timeout')
                except RuntimeError as problem:
                    verdict = network.check()
                    if verdict == 'fail':
                        # 11/01 11:26: the line came back 2 s before the report timeout and this stopped
                        # mining for good. A miner that went quiet is restarted like one that crashed
                        # (the exit check above does it on the next pass, within the same hourly limit).
                        if len([t for t in crashes if time.monotonic()-t < 3600]) >= MINER_RESTARTS_PER_HOUR:
                            raise
                        print(f'{problem}; restarting the miner instead of stopping', flush=True)
                        process.terminate()
                        process.wait(timeout=10)
                        continue
                    if verdict == 'back':
                        last_progress = last_share = time.monotonic()
                time.sleep(2)
    except KeyboardInterrupt:
        reason = 'User stopped'
    except Exception as error:
        reason = str(error)
    finally:
        relay_done.set()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        job.Close()
        if process:
            process.handle.Close()
        (logs/f'result-{stamp}.json').write_text(json.dumps({'reason': reason, 'seconds': time.monotonic()-started}, indent=2), encoding='utf-8')
        # No synchronous console writes here: GPU recovery must never wait on selection.

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('NOT STARTED: '+str(error), flush=True)
        raise SystemExit(1)

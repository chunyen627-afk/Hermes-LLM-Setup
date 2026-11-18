"""Standalone read-only telemetry. Never imports or controls the mining guard."""
import csv
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import math
from pathlib import Path
import re
import socket
import threading
import time
import logging
import os
import subprocess
from logging.handlers import RotatingFileHandler

ROOT = Path(__file__).resolve().parent
MINER = ROOT.parent / 'Pearl-mining'
UTC = timezone.utc
TOTAL = re.compile(r'Total:\s*([\d.]+)\s*([KMGTPE]?H/s)\s+shares:\s*(\d+) accepted\s+(\d+) stale\s+(\d+) rejected')
CLOCK = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d+)')

def iso(value):
    return value.astimezone(UTC).isoformat() if value else None

def parse_log(path):
    """Bounded reverse scan; derive dates across midnight from file modification date."""
    result = {'hashrate': None, 'unit': None, 'data_at': None,
              'accepted': None, 'stale': None, 'rejected': None, 'last_accepted_at': None}
    stat = path.stat()
    with path.open('rb') as source:
        start = max(0, stat.st_size-1048576)
        source.seek(start)
        raw = source.read(1048576)
    if start:
        raw = raw.partition(b'\n')[2]
    if not raw.endswith(b'\n'):
        raw = raw.rpartition(b'\n')[0]
    anchor = datetime.fromtimestamp(stat.st_mtime).astimezone()
    previous = anchor
    for line in reversed(raw.decode('utf-8', errors='replace').splitlines()):
        clock = CLOCK.match(line)
        if not clock:
            continue
        moment = datetime.combine(previous.date(), datetime.strptime(clock[1], '%H:%M:%S.%f').time(), previous.tzinfo)
        if moment > previous + timedelta(seconds=2):
            moment -= timedelta(days=1)
        previous = moment
        total = TOTAL.search(line)
        if total and result['data_at'] is None:
            value = float(total[1])
            if math.isfinite(value):
                result.update(hashrate=value, unit=total[2], data_at=moment,
                              accepted=int(total[3]), stale=int(total[4]), rejected=int(total[5]))
        if 'share accepted:' in line and result['last_accepted_at'] is None:
            result['last_accepted_at'] = moment
        if result['data_at'] and result['last_accepted_at']:
            break
    return result

def miner_processes():
    import win32api, win32process
    query = ctypes.WinDLL('kernel32', use_last_error=True).QueryFullProcessImageNameW
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    # The guard runs either krig or SRBMiner (its own Defender-excluded folder, 2026-10-29).
    images = {'krig-miner.exe': MINER/'runtime'/'krig-miner.exe',
              'srbminer-multi.exe': Path(r'C:\Users\user\srbminer\SRBMiner-Multi-3-8-1\SRBMiner-MULTI.exe')}
    expected = {str(path.resolve()).casefold() for path in images.values()}
    # 3.8.3 since 2026-11-02; 3.8.1 stays recognised so going back needs only the guard's path.
    expected.add(str(Path(r'C:\Users\user\srbminer\SRBMiner-Multi-3-8-3\SRBMiner-MULTI.exe').resolve()).casefold())
    # 3.8.5 since 2026-11-08; 3.8.3 stays recognised for the same reason.
    expected.add(str(Path(r'C:\Users\user\srbminer\SRBMiner-Multi-3-8-5\SRBMiner-MULTI.exe').resolve()).casefold())
    # 3.8.6 since 2026-11-09; 3.8.5 stays recognised so going back does not look like a stranger.
    expected.add(str(Path(r'C:\Users\user\srbminer\SRBMiner-Multi-3-8-6\SRBMiner-MULTI.exe').resolve()).casefold())
    found = []
    # Read only process names/PIDs, never command lines. Probe failures must not
    # silently become "stopped". Restrict inspection to miner-name candidates.
    pids = []
    for image in images:
        listing = subprocess.check_output(
            [str(Path(os.environ['SystemRoot'])/'System32'/'tasklist.exe'),
             '/FI', f'IMAGENAME eq {image}', '/FO', 'CSV', '/NH'],
            text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        pids += [int(row[1]) for row in csv.reader(listing.splitlines())
                 if row and row[0].casefold() == image]
    for pid in pids:
        try:
            handle = win32api.OpenProcess(0x1000, False, pid)
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if not query(int(handle), 0, buf, ctypes.byref(size)):
                    raise OSError('Process identity unavailable')
                if buf.value.casefold() in expected:
                    started = win32process.GetProcessTimes(handle)['CreationTime'].timestamp()
                    found.append(started)
            finally:
                handle.Close()
        except Exception:
            raise RuntimeError('Process probe incomplete') from None
    return found

def gpu_values():
    import pynvml as nv
    nv.nvmlInit()
    def get(fn):
        try:
            value = fn()
            return value if not isinstance(value, float) or math.isfinite(value) else None
        except Exception:
            return None
    rows = []
    for index in range(nv.nvmlDeviceGetCount()):
        h = nv.nvmlDeviceGetHandleByIndex(index)
        fans = get(lambda: nv.nvmlDeviceGetNumFans(h))
        rows.append({'index':index, 'name':get(lambda:nv.nvmlDeviceGetName(h)),
                     'temperature_c':get(lambda:nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
                     'fan_percent':get(lambda:nv.nvmlDeviceGetFanSpeed(h)),
                     'fans_percent': [get(lambda f=f:nv.nvmlDeviceGetFanSpeed_v2(h,f)) for f in range(fans)] if fans is not None else None,
                     'power_w':get(lambda:nv.nvmlDeviceGetPowerUsage(h)/1000)})
    return rows

def classify(running, fresh, reason):
    if running is None:
        return 'unknown', 'unknown'
    if running:
        return ('running' if fresh else 'data_stale'), None
    if reason == 'User stopped':
        return 'user_stopped', 'user_requested'
    if reason == 'Timed validation completed':
        return 'planned_stop', 'validation_completed'
    reasons = {'Temperature cutoff':'temperature_cutoff',
               'No accepted shares within timeout':'no_accepted_shares',
               'GPU identity mismatch':'gpu_identity_mismatch',
               'Invalid GPU telemetry':'invalid_gpu_telemetry',
               'Fan control readback failed':'fan_control_failed'}
    if reason in reasons:
        return 'abnormal_exit', reasons[reason]
    if reason and reason.startswith('Miner exited '):
        return 'abnormal_exit', 'miner_exited'
    if reason and reason.startswith('GPU limits mismatch:'):
        return 'abnormal_exit', 'gpu_limits_mismatch'
    return 'unknown', 'unknown'

def empty_status(cfg=None):
    cfg = cfg or {}
    return {'computer_name':socket.gethostname(), 'worker':cfg.get('worker'),
            'expected_state':cfg.get('expected_state'), 'miner_running':None,
            'state':'unknown', 'data_status':'unknown',
            'hashrate':{'value':None,'unit':None,'data_at':None},
            'shares':{'accepted':None,'stale':None,'rejected':None},
            'shares_data_at':None, 'last_accepted_share_at':None,
            'gpus':None, 'gpu_data_at':None, 'data_at':None, 'stop_reason':'unknown'}


def whitelist(data):
    result = empty_status()
    for key in result:
        if key not in ('hashrate','shares','gpus'):
            result[key] = data.get(key, result[key])
    for key in ('hashrate','shares'):
        source = data.get(key) or {}
        result[key] = {field:source.get(field) for field in result[key]}
    if isinstance(data.get('gpus'), list):
        result['gpus'] = [{key:row.get(key) for key in
                          ('index','temperature_c','fan_percent','fans_percent','power_w')}
                         for row in data['gpus']]
    return result


def collect(cfg, observed=None):
    now = datetime.now(UTC)
    errors = []
    try:
        processes = miner_processes()
        running = bool(processes)
    except Exception:
        processes, running = [], None
        errors.append('process_probe_failed')
    files = sorted((MINER/'logs').glob('mining-????????-??????.log'))
    log = None
    correlated = False
    if running and len(processes) == 1:
        candidates = []
        for item in files:
            stamp = datetime.strptime(item.stem[7:], '%Y%m%d-%H%M%S').astimezone().timestamp()
            if -3 <= processes[0]-stamp <= 30:
                candidates.append(item)
        log = candidates[0] if len(candidates) == 1 else None
        correlated = len(candidates) == 1
        if correlated and observed is not None:
            observed['log'] = log
            observed['started'] = processes[0]
    elif running is False and observed and observed.get('log'):
        # Only attribute a stop to a session actually observed by this monitor.
        candidate = observed['log']
        if files and candidate == files[-1]:
            log, correlated = candidate, True
    if not correlated and observed is not None:
        observed.clear()
    metrics = {'hashrate':None,'unit':None,'data_at':None,'accepted':None,'stale':None,'rejected':None,'last_accepted_at':None}
    reason = None
    if log:
        try:
            metrics = parse_log(log)
            session_start = datetime.strptime(log.stem[7:], '%Y%m%d-%H%M%S').astimezone()
            # Time-only logs need corroboration from the guard's dated telemetry.
            gpu_log = log.with_name(log.name.replace('mining-','gpu-')).with_suffix('.jsonl')
            with gpu_log.open('rb') as source:
                source.seek(max(0, gpu_log.stat().st_size-65536))
                lines = source.read(65536).splitlines()
            # Re-read the clock after the reads: a line the miner or guard wrote since the start of
            # this sample is not from the future, and must not void the whole reading.
            now = datetime.now(UTC)
            dated = json.loads(lines[-1])
            guard_at = datetime.fromisoformat(dated['time'])
            if guard_at.tzinfo is None or not session_start <= guard_at <= now:
                raise ValueError('Untrusted date anchor')
            if abs(log.stat().st_mtime-guard_at.timestamp()) > cfg['stale_seconds']:
                raise ValueError('Inconsistent log dates')
            for key in ('data_at','last_accepted_at'):
                if metrics[key] and not session_start <= metrics[key] <= now:
                    raise ValueError('Timestamp outside session')
            end = log.with_name(log.name.replace('mining-','result-')).with_suffix('.json')
            if not running and end.exists():
                reason = json.loads(end.read_text(encoding='utf-8-sig')).get('reason')
        except Exception:
            metrics = dict.fromkeys(metrics)
            reason = None
            errors.append('miner_log_unavailable')
    age = (now-metrics['data_at']).total_seconds() if metrics['data_at'] else None
    fresh = correlated and age is not None and 0 <= age <= cfg['stale_seconds']
    state, stop_reason = classify(running, fresh, reason)
    expected = cfg['expected_state']
    try:
        gpus = gpu_values()
        gpu_at = iso(datetime.now(UTC))
    except Exception:
        gpus, gpu_at = None, None
        errors.append('gpu_probe_failed')
    return whitelist({'computer_name':socket.gethostname(), 'worker':cfg['worker'],
            'expected_state':expected,
            'miner_running':running, 'state':state,
            'data_status':'fresh' if fresh else ('stale' if metrics['data_at'] else 'unknown'),
            'hashrate':{'value':metrics['hashrate'] if running and fresh else None,
                        'unit':metrics['unit'] if running and fresh else None, 'data_at':iso(metrics['data_at'])},
            'shares':{k:metrics[k] if fresh else None for k in ('accepted','stale','rejected')},
            'shares_data_at':iso(metrics['data_at']),
            'last_accepted_share_at':iso(metrics['last_accepted_at']) if fresh else None,
            'gpus':gpus, 'gpu_data_at':gpu_at, 'data_at':iso(now),
            'stop_reason':stop_reason})

class Monitor:
    def __init__(self, cfg):
        self.cfg, self.lock, self.snapshot, self.sampled = cfg, threading.Lock(), None, 0
        self.observed = {}
    def sample(self):
        while True:
            try:
                result = collect(self.cfg, self.observed)
                with self.lock:
                    self.snapshot, self.sampled = result, time.monotonic()
            except Exception:
                logging.exception('Telemetry sampler failed')
            time.sleep(self.cfg['poll_seconds'])
    def response(self, path, peer):
        # Own ZT address permits same-machine diagnostics; remote clients are office only.
        if peer not in (self.cfg['office_ip'], self.cfg['bind_ip']):
            return 403, empty_status()
        if path not in ('/health','/status'):
            return 404, empty_status()
        with self.lock:
            data, age = self.snapshot, time.monotonic()-self.sampled
        alive = data is not None and age <= 20
        if not alive:
            expired = empty_status(self.cfg)
            expired.update(state='data_stale', data_status='stale',
                           data_at=data.get('data_at') if data else None)
            return 503, expired
        return 200, whitelist(data)

def handler_for(monitor):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'PearlReadOnlyMonitor'
        sys_version = ''
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def log_message(self, *args):
            pass
        def send_json(self, code, obj):
            body = json.dumps(whitelist(obj), ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.send_json(*monitor.response(self.path, self.client_address[0]))
        def denied_method(self):
            self.send_json(405, empty_status())
        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = denied_method
    return Handler

def main():
    cfg = json.loads((ROOT/'monitor.json').read_text(encoding='utf-8-sig'))
    if cfg['bind_ip'] != '10.35.219.158' or cfg['office_ip'] != '10.35.219.64' or cfg['port'] != 18785:
        raise RuntimeError('Review network/firewall binding before changing endpoint')
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(RotatingFileHandler(ROOT/'monitor.log', maxBytes=1048576, backupCount=3, encoding='utf-8'))
    monitor = Monitor(cfg)
    threading.Thread(target=monitor.sample, daemon=True).start()
    while True:
        try:
            server = ThreadingHTTPServer((cfg['bind_ip'],cfg['port']),handler_for(monitor))
            break
        except OSError as exc:
            if getattr(exc,'winerror',None) != 10049:
                raise
            time.sleep(10)  # Wait for ZeroTier; never fall back to another interface.
    logging.info('Read-only monitoring listening on configured ZeroTier endpoint')
    server.daemon_threads = True
    server.serve_forever(poll_interval=1)

if __name__ == '__main__':
    main()

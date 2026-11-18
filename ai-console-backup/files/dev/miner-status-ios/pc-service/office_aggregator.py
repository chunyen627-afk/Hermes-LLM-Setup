"""Read-only miner status aggregator for the iPhone app, run on the office PC.

Builds one feed in the app's JSON schema (schema/miner-status.schema.json) from:
- the office miner's state file and the same session's miner log (read only), and
- the RTX 5080's read-only monitor (GET /status), which only answers the office.

It never starts, stops or tunes mining, never runs commands, and only answers GET /status and
/health to clients inside the configured ZeroTier network. Tokens, PIDs, paths and command lines
from the sources are never copied into the output.
"""
import base64
import ctypes
import hmac
import ipaddress
import json
import logging
import math
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
TAIPEI = timezone(timedelta(hours=8))
TOTAL = re.compile(r'Total:\s*([\d.]+)\s*([KMGTPE]?H/s)\s+shares:\s*(\d+) accepted\s+(\d+) stale\s+(\d+) rejected')
CLOCK = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d+)')
SESSION_LOG = re.compile(r'mining-(\d{8}-\d{6})\.log')
UNITS = {'H/s': 'H/s', 'KH/s': 'kH/s', 'MH/s': 'MH/s', 'GH/s': 'GH/s', 'TH/s': 'TH/s'}
OFFICE_STATES = {'running': 'running', 'stopped': 'stopped', 'restored': 'stopped'}
RTX_STOP_REASONS = {
    'user_requested': 'userRequested', 'validation_completed': 'userRequested',
    'temperature_cutoff': 'protectionTriggered', 'no_accepted_shares': 'protectionTriggered',
    'gpu_identity_mismatch': 'protectionTriggered', 'invalid_gpu_telemetry': 'protectionTriggered',
    'fan_control_failed': 'protectionTriggered',
    'on_battery': 'protectionTriggered',
    'miner_exited': 'abnormalExit', 'gpu_limits_mismatch': 'abnormalExit',
    'unknown': 'unknown',
}


def iso(value):
    """Taiwan time with an explicit +08:00 offset: the app shows these strings as they are, and
    UTC 'Z' times read as eight hours behind."""
    if value is None:
        return None
    return value.astimezone(TAIPEI).isoformat(timespec='milliseconds')


def parse_time(text):
    """ISO 8601 with an offset, or None. Naive times are rejected rather than guessed."""
    if not isinstance(text, str):
        return None
    try:
        value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return value if value.tzinfo else None


def number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_log(path):
    """Last Total line and last accepted share of a miner log, scanning at most 1 MiB backwards.

    Lines carry only a time of day, so dates come from the file's modification time and step back
    across midnight. Same approach as the RTX monitor.
    """
    result = {'hashrate': None, 'unit': None, 'data_at': None,
              'accepted': None, 'stale': None, 'rejected': None, 'last_accepted_at': None}
    stat = path.stat()
    with path.open('rb') as source:
        start = max(0, stat.st_size - 1048576)
        source.seek(start)
        raw = source.read(1048576)
    if start:
        raw = raw.partition(b'\n')[2]
    if not raw.endswith(b'\n'):
        raw = raw.rpartition(b'\n')[0]
    previous = datetime.fromtimestamp(stat.st_mtime).astimezone()
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
            if math.isfinite(value) and total[2] in UNITS:
                result.update(hashrate=value, unit=UNITS[total[2]], data_at=moment,
                              accepted=int(total[3]), stale=int(total[4]), rejected=int(total[5]))
        if 'share accepted:' in line and result['last_accepted_at'] is None:
            result['last_accepted_at'] = moment
        if result['data_at'] and result['last_accepted_at']:
            break
    return result


class SessionTracker:
    """Session id and sample sequence the app's engine needs to compare two samples.

    With an explicit session (office log name) that id is used. Without one (RTX), a new session
    starts when the miner is seen running after not running, or when accepted shares go down.
    The sequence advances whenever the machine's observedAt changes, never on a re-read.
    """

    def __init__(self, prefix, instance=None):
        # instance (this process's start time) suffixes explicit ids, so a restart that resets the
        # sequence to 1 never reuses an id the app already holds a higher sequence for.
        self.prefix, self.instance, self.session, self.sequence = prefix, instance, None, 0
        self.last_key, self.last_accepted, self.ended = None, None, True

    def update(self, running, accepted, sample_key, now, explicit=None):
        if explicit is not None:
            if self.instance:
                explicit = f'{explicit}-{self.instance}'
            if explicit != self.session:
                self.session, self.sequence, self.last_key, self.last_accepted = explicit, 0, None, None
        elif running is True:
            dropped = accepted is not None and self.last_accepted is not None and accepted < self.last_accepted
            if self.session is None or self.ended or dropped:
                self.session = f"{self.prefix}-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"
                self.sequence, self.last_key, self.last_accepted = 0, None, None
            self.ended = False
        else:
            self.ended = True
        if self.session is None:
            return None, None
        if accepted is not None:
            self.last_accepted = accepted
        if sample_key is not None and sample_key != self.last_key:
            self.sequence += 1
            self.last_key = sample_key
        return self.session, self.sequence


def empty_machine(machine_id, name, worker, source_status, checked_at):
    return {
        'machineId': machine_id, 'displayName': name, 'worker': worker,
        'sourceStatus': source_status, 'sourceCheckedAt': iso(checked_at),
        'sessionId': None, 'sampleSequence': None, 'observedAt': None, 'expectedRunning': None,
        'minerRunning': {'state': None, 'observedAt': None},
        'hashrate': {'value': None, 'unit': None, 'observedAt': None},
        'gpus': None,
        'shares': {'accepted': None, 'stale': None, 'rejected': None, 'observedAt': None, 'lastAcceptedAt': None},
        'stop': {'reason': None, 'sessionId': None, 'observedAt': None},
    }


def read_state(path):
    """The guard replaces the file atomically about every 2 s; retry once on a partial read."""
    for attempt in range(2):
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            raise
        except (OSError, ValueError):
            if attempt:
                raise
            time.sleep(0.2)


def office_machine(cfg, tracker, now=None):
    now = now or datetime.now(UTC)
    office = cfg['office']
    machine = empty_machine(office['machine_id'], office['display_name'], office.get('worker'), 'ok', now)
    try:
        data = read_state(Path(office['state_file']))
        if not isinstance(data, dict):
            raise ValueError('state is not an object')
    except FileNotFoundError:
        machine['sourceStatus'] = 'noDataYet'
        return machine
    except (OSError, ValueError):
        machine['sourceStatus'] = 'error'
        return machine

    updated = number(data.get('updated'), 0)
    observed = datetime.fromtimestamp(updated, UTC) if updated is not None else None
    if observed and observed > now + timedelta(seconds=5):
        observed = None  # A state time ahead of this clock is not trusted.
    state = OFFICE_STATES.get(data.get('status'), 'unknown')

    session_stamp, metrics = None, None
    log_name = data.get('log')
    if isinstance(log_name, str):
        log_path = Path(log_name)
        match = SESSION_LOG.fullmatch(log_path.name)
        if match:
            session_stamp = match[1]
            try:
                started = datetime.strptime(session_stamp, '%Y%m%d-%H%M%S').astimezone()
                metrics = parse_log(log_path)
                for key in ('data_at', 'last_accepted_at'):
                    if metrics[key] and not started <= metrics[key] <= now + timedelta(seconds=5):
                        metrics[key] = None  # Outside this session: do not present as current.
                if metrics['data_at'] is None:
                    metrics.update(hashrate=None, unit=None, accepted=None, stale=None, rejected=None)
            except (OSError, ValueError):
                metrics = None

    if (metrics is None or metrics['data_at'] is None) and parse_time(data.get('data_at')):
        # SRBMiner writes no "Total:" line; the guard puts its API numbers in the panel state instead.
        data_at = parse_time(data['data_at'])
        if data_at <= now + timedelta(seconds=5):
            metrics = {'hashrate': number(data.get('th_s'), 0), 'unit': 'TH/s', 'data_at': data_at,
                       'accepted': count(data.get('accepted')), 'stale': 0, 'rejected': count(data.get('rejected')),
                       'last_accepted_at': parse_time(data.get('last_accepted_at'))}

    session_id = f"{office['machine_id']}-{session_stamp}" if session_stamp else None
    accepted = metrics['accepted'] if metrics else None
    # Keyed on the sample time the app compares: a newer observedAt must always carry a higher
    # sequence, or the app rejects the sample as a replay (it did when this followed the log line).
    session_id, sequence = tracker.update(state == 'running', accepted, observed, now, explicit=session_id)

    gpus = None
    if isinstance(data.get('gpus'), list):
        gpus = []
        for row in data['gpus']:
            if not isinstance(row, dict) or count(row.get('index')) is None:
                continue
            gpus.append({'index': row['index'], 'temperatureC': number(row.get('temperature'), -40, 150),
                         'fanPercent': number(row.get('fan'), 0, 100), 'powerW': number(row.get('watts'), 0),
                         'observedAt': iso(observed)})

    data_at = metrics['data_at'] if metrics else None
    machine.update({
        'sessionId': session_id, 'sampleSequence': sequence, 'observedAt': iso(observed),
        'minerRunning': {'state': state, 'observedAt': iso(observed)},
        'hashrate': {'value': metrics['hashrate'] if metrics else None, 'unit': metrics['unit'] if metrics else None,
                     'observedAt': iso(data_at)},
        'gpus': gpus,
        'shares': {'accepted': accepted, 'stale': metrics['stale'] if metrics else None,
                   'rejected': metrics['rejected'] if metrics else None, 'observedAt': iso(data_at),
                   'lastAcceptedAt': iso(metrics['last_accepted_at']) if metrics else None},
        'stop': {'reason': office_stop_reason(office, data) if state == 'stopped' else None,
                 'sessionId': session_id if state == 'stopped' else None,
                 'observedAt': iso(observed) if state == 'stopped' else None},
    })
    return machine


def office_stop_reason(office, data):
    """'userRequested' when the AI console was told to stop this session (its stop button, a mode
    switch, closing it, or the phone through it): it writes panel-stop.json at a time after the
    session's token. Otherwise 'unknown' (a crash, or a guard stop the state does not explain)."""
    try:
        asked = json.loads(Path(office['state_file']).with_name('panel-stop.json').read_text(encoding='utf-8'))['time']
        return 'userRequested' if float(asked) >= float(data.get('token')) else 'unknown'
    except (OSError, ValueError, KeyError, TypeError):
        return 'unknown'


class RtxPoller:
    """Polls the RTX monitor in the background; requests never wait on the network."""

    def __init__(self, cfg):
        self.cfg, self.lock = cfg['rtx'], threading.Lock()
        self.result = None  # (checked_at, source_status, payload or None)

    def poll_once(self):
        checked = datetime.now(UTC)
        try:
            request = urllib.request.Request(self.cfg['url'], headers={'Accept': 'application/json'})
            with urllib.request.urlopen(request, timeout=self.cfg['timeout_seconds']) as response:
                body = response.read(65536)
            status, payload = 'ok', json.loads(body.decode('utf-8'))
        except urllib.error.HTTPError as error:
            status, payload = ('noDataYet' if error.code == 503 else 'error'), None
        except (urllib.error.URLError, OSError, TimeoutError):
            status, payload = 'unreachable', None
        except ValueError:
            status, payload = 'error', None
        if status == 'ok' and not isinstance(payload, dict):
            status, payload = 'error', None
        with self.lock:
            self.result = (checked, status, payload)

    def run(self):
        while True:
            try:
                self.poll_once()
            except Exception:
                logging.exception('RTX poll failed')
            time.sleep(self.cfg['poll_seconds'])

    def latest(self):
        with self.lock:
            return self.result


class SshStatusPoller(RtxPoller):
    """Reads a monitor status file in the RTX /status shape over SSH (the laptop opens no port).

    Key-only SSH restricted to this PC on the far side; the file is read with `type`, nothing runs.
    """

    def __init__(self, cfg, section, notify=None):
        self.cfg, self.lock = cfg[section], threading.Lock()
        self.result = None
        self.notify = notify  # (title, message, priority, tags) -> None
        self.acted = set()  # emergency actions already taken for the current episode
        self.last_running = None  # miner_running from the last status read; None before the first

    def powered_off(self):
        """True when the machine cannot be reached and was not mining when last seen, so it is
        taken to be switched off rather than lost. A machine that drops off while mining is not."""
        with self.lock:
            result, last_running = self.result, self.last_running
        return result is not None and result[1] == 'unreachable' and last_running is not True

    def remote(self, script, timeout=None, run=subprocess.run):
        """Runs a PowerShell script on the machine; its last output line, or None when unreachable."""
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        try:
            done = run(self.command(f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"),
                       capture_output=True, timeout=timeout or self.cfg['timeout_seconds'] + 20,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            return None
        lines = done.stdout.decode('utf-8', 'replace').strip().splitlines() if done.returncode == 0 else []
        return lines[-1].strip() if lines else None

    def command(self, remote=None):
        ssh = self.cfg['ssh']
        return [ssh['client'], '-i', ssh['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', f"UserKnownHostsFile={ssh['known_hosts']}", '-o', f"ConnectTimeout={self.cfg['timeout_seconds']}",
                f"{ssh['user']}@{ssh['host']}", remote or f"type {self.cfg['status_path']}"]

    def guard(self, payload, now, run=subprocess.run):
        """Outside check on the machine's own guard (`guardian` config): kill the miner when it runs
        hot or its guard stopped updating, and shut the machine down when it gets dangerously hot."""
        rules = self.cfg.get('guardian')
        if not rules or not isinstance(payload, dict):
            return None
        gpus = payload.get('gpus') if isinstance(payload.get('gpus'), list) else []
        temps = [number(row.get('temperature_c'), -40, 150) for row in gpus if isinstance(row, dict)]
        temp = max([t for t in temps if t is not None], default=None)
        updated = parse_time(payload.get('data_at'))
        stale = updated is None or (now - updated).total_seconds() > rules['stale_seconds']
        running = payload.get('miner_running') is True
        action = None
        if temp is not None and temp >= rules['shutdown_at']:
            action = ('shutdown', f'GPU {temp:g}°C，已遠端關機',
                      'shutdown /s /t 30 /c "Office guardian: GPU too hot, shutting down."')
        elif running and temp is not None and temp >= rules['kill_at']:
            action = ('kill-hot', f'GPU {temp:g}°C 礦工還在跑，已遠端停止挖礦', None)
        elif running and stale:
            action = ('kill-stale', '保護程式沒有回應，已遠端停止挖礦', None)
        if action is None:
            self.acted.clear()
            return None
        key, message, remote = action
        if key in self.acted:
            return None
        self.acted.add(key)
        stop = f"type nul > {self.cfg['stop_path']} & taskkill /f /im krig-miner.exe /im SRBMiner-MULTI.exe"
        try:
            run(self.command(remote or stop), capture_output=True, timeout=self.cfg['timeout_seconds'] + 10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            logging.exception('guardian action %s failed', key)
        logging.warning('guardian %s on %s', key, self.cfg['machine_id'])
        if self.notify:
            try:
                self.notify(f"🚨 {self.cfg['display_name']} 緊急處理", message, 5, ['rotating_light'])
            except OSError:
                logging.exception('guardian notification failed')
        return key

    def poll_once(self, run=subprocess.run):
        checked = datetime.now(UTC)
        try:
            done = run(self.command(), capture_output=True, timeout=self.cfg['timeout_seconds'] + 5,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if done.returncode == 255:
                status, payload = 'unreachable', None  # ssh itself failed: no route, refused, auth
            elif done.returncode != 0:
                status, payload = 'noDataYet', None  # reached the machine, no status file yet
            else:
                status, payload = 'ok', json.loads(done.stdout.decode('utf-8-sig'))
        except (subprocess.TimeoutExpired, OSError):
            status, payload = 'unreachable', None
        except ValueError:
            status, payload = 'error', None
        if status == 'ok' and not isinstance(payload, dict):
            status, payload = 'error', None
        if status == 'ok' and payload.get('miner_running') is False:
            # The guard writes the file only while it runs, and the miner only runs under the guard.
            # A reachable machine whose last word was "stopped" is therefore stopped now, not lost:
            # after a wake-up the file is hours old and would otherwise read as 失聯.
            payload = dict(payload, data_at=checked.isoformat())
        with self.lock:
            self.result = (checked, status, payload)
            if status == 'ok':
                self.last_running = payload.get('miner_running') is True
        if status == 'ok':
            self.guard(payload, checked, run)


RELAY_STATUS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ relay INFO [\d.]+ MHz ([\d.]+) MH/s \| '
                          r'(-?[\d.]+) C [\d.]+ V \| cands \d+ \(hw err \d+\) shares \d+ acc (\d+) rej (\d+)')
RELAY_ACCEPTED = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ relay INFO share accepted')


def parse_relay_log(path, average_lines=12):
    """Last status line (written every 5 s), the hashrate averaged over the last `average_lines` of
    them, and the last accepted share of an FPGA relay log. One 5 s reading is coarse (120, 150 or
    180 MH/s around a true 150), so a minute's average is what gets shown."""
    stat = path.stat()
    with path.open('rb') as source:
        start = max(0, stat.st_size - 262144)
        source.seek(start)
        raw = source.read(262144)
    status, rates, accepted_at = None, [], None
    for line in reversed(raw.decode('utf-8', errors='replace').splitlines()):
        if len(rates) < average_lines and (found := RELAY_STATUS.match(line)):
            status = status or found
            rates.append(float(found[2]))
        if accepted_at is None and (found := RELAY_ACCEPTED.match(line)):
            accepted_at = datetime.strptime(found[1], '%Y-%m-%d %H:%M:%S').astimezone()
        if len(rates) >= average_lines and accepted_at:
            break
    return status, (sum(rates) / len(rates) if rates else None), accepted_at


def process_alive(pid):
    """True while a process with this id is running (Windows), False when it has exited."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


FPGA_SECTIONS = ('vcu118', 'kv260')  # aggregator.json sections read by RelayLogPoller


class RelayLogPoller(RtxPoller):
    """An FPGA miner on this PC: reads its relay's log and pid file into the RTX /status shape.

    The relay logs hashrate, chip temperature and share counts every few seconds; nothing is sent
    to the relay or the board. A relay that has exited reads as stopped, a silent one as 失聯.
    Each board has its own files in the shared log folder (mining_ctl.py BOARDS): the VCU118 the
    defaults below, the KV260 (2026-11-04) `log_prefix` kv260_mining_ and its own pid and stop mark.
    """

    def __init__(self, cfg, section, alive=None):
        self.cfg, self.lock = cfg[section], threading.Lock()
        self.result = None
        self.alive = alive or process_alive

    def powered_off(self):
        return False

    def poll_once(self, now=None):
        checked = now or datetime.now(UTC)
        folder = Path(self.cfg['log_dir'])
        try:
            logs = sorted(folder.glob(self.cfg.get('log_prefix', 'mining_') + '*.err'), key=lambda item: item.stat().st_mtime)
            pid = int((folder / self.cfg.get('pid_file', 'mining.pid')).read_text(encoding='ascii').strip())
        except (OSError, ValueError):
            logs, pid = [], None
        if not logs or pid is None:
            with self.lock:
                self.result = (checked, 'noDataYet', None)
            return
        status, rate, accepted_at = parse_relay_log(logs[-1])
        running = self.alive(pid)
        line_at = datetime.strptime(status[1], '%Y-%m-%d %H:%M:%S').astimezone() if status else None
        payload = {
            # A stopped relay writes nothing more, so its state is as of now rather than its last line.
            'data_at': (line_at if running and line_at else checked).isoformat(),
            'miner_running': running, 'expected_state': 'running',
            'hashrate': {'value': round(rate, 2) if running and status else None,
                         'unit': 'MH/s', 'data_at': line_at.isoformat() if line_at else None},
            'shares': {'accepted': int(status[4]), 'stale': 0, 'rejected': int(status[5])} if status else {},
            'shares_data_at': line_at.isoformat() if line_at else None,
            'last_accepted_share_at': accepted_at.isoformat() if accepted_at else None,
            'gpus': [{'index': 0, 'temperature_c': float(status[3])}] if status and running else [],
            'gpu_data_at': line_at.isoformat() if line_at else None,
            # mining_ctl.py stop leaves this mark (and start removes it): a stop someone asked for.
            'stop_reason': None if running else 'user_requested' if (folder / self.cfg.get('stop_mark', 'mining.stopped')).exists() else 'unknown',
        }
        with self.lock:
            self.result = (checked, 'ok', payload)


def rtx_machine(cfg, result, tracker, now=None, section='rtx'):
    """A machine from a monitor status in the RTX shape (the RTX monitor, or the laptop guard)."""
    now = now or datetime.now(UTC)
    rtx = cfg[section]
    if result is None:
        return empty_machine(rtx['machine_id'], rtx['display_name'], rtx.get('worker'), 'noDataYet', None)
    checked, status, data = result
    machine = empty_machine(rtx['machine_id'], rtx['display_name'], rtx.get('worker'), status, checked)
    if status != 'ok':
        tracker.update(None, None, None, now)
        return machine

    observed = parse_time(data.get('data_at'))
    running = data.get('miner_running')
    state = {True: 'running', False: 'stopped'}.get(running, 'unknown') if isinstance(running, bool) or running is None else 'unknown'
    hashrate = data.get('hashrate') if isinstance(data.get('hashrate'), dict) else {}
    shares = data.get('shares') if isinstance(data.get('shares'), dict) else {}
    hash_at = parse_time(hashrate.get('data_at'))
    shares_at = parse_time(data.get('shares_data_at'))
    gpu_at = parse_time(data.get('gpu_data_at'))
    accepted = count(shares.get('accepted'))
    session_id, sequence = tracker.update(running if isinstance(running, bool) else None, accepted, observed, now)

    gpus = None
    if isinstance(data.get('gpus'), list):
        gpus = [{'index': row['index'], 'temperatureC': number(row.get('temperature_c'), -40, 150),
                 'fanPercent': number(row.get('fan_percent'), 0, 100), 'powerW': number(row.get('power_w'), 0),
                 'observedAt': iso(gpu_at)}
                for row in data['gpus'] if isinstance(row, dict) and count(row.get('index')) is not None]

    unit = hashrate.get('unit')
    expected = {'running': True, 'stopped': False}.get(data.get('expected_state'))
    stopped = state == 'stopped'
    machine.update({
        'sessionId': session_id, 'sampleSequence': sequence, 'observedAt': iso(observed),
        'expectedRunning': expected,
        'minerRunning': {'state': state, 'observedAt': iso(observed)},
        'hashrate': {'value': number(hashrate.get('value'), 0) if unit in UNITS else None,
                     'unit': UNITS.get(unit), 'observedAt': iso(hash_at)},
        'gpus': gpus,
        'shares': {'accepted': accepted, 'stale': count(shares.get('stale')), 'rejected': count(shares.get('rejected')),
                   'observedAt': iso(shares_at), 'lastAcceptedAt': iso(parse_time(data.get('last_accepted_share_at')))},
        'stop': {'reason': RTX_STOP_REASONS.get(data.get('stop_reason'), 'unknown') if stopped else None,
                 'sessionId': session_id if stopped else None,
                 'observedAt': iso(observed) if stopped else None},
    })
    return machine


def median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def coin_per_day(hashrate, network_hashrate, block_time, block_reward, fee):
    """PPS estimate: this hashrate's share of the network's daily block rewards, after the fee."""
    if None in (hashrate, network_hashrate, block_time, block_reward, fee) or network_hashrate <= 0 or block_time <= 0:
        return None
    return hashrate / network_hashrate * (86400 / block_time) * block_reward * (1 - fee)


CALIBRATION = ROOT / 'calibration.json'


def calibration_factor(coin):
    """The factor `calibrate.py` keeps per coin from comparing our estimate with what the pool's
    dashboard actually credited, hour by hour (1.0 when there is none). Read on every use, so a new
    factor applies without restarting this service."""
    try:
        saved = json.loads(Path(CALIBRATION).read_text(encoding='utf-8'))
        return number((saved.get(coin) or {}).get('factor'), 0.5, 2.0) or 1.0
    except (OSError, ValueError, AttributeError):
        return 1.0


SAFE_URL = re.compile(r'^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/?=&%,-]*$')  # inert inside "…" on the far side


class KryptexRoutes:
    """Pool and price queries straight from this PC, or through another rig over SSH (`curl.exe` on
    it) when this PC's address is refused. Whichever route fails, the other one answers, and the
    failed route rests `rest_seconds` before it is tried first again, so a blocked address is not
    kept knocking (2026-10-27: Kryptex's CrowdSec banned this PC's address for hours).
    """

    def __init__(self, relay=None, direct=None, run=subprocess.run, clock=time.monotonic, rest_seconds=1800):
        self.relay, self.run, self.clock, self.rest_seconds = relay, run, clock, rest_seconds
        self.direct = direct or EarningsPoller.get_json
        self.lock = threading.Lock()
        self.resting = {}  # route -> monotonic time until which it is tried last
        self.last_route = None

    def via_relay(self, url):
        if not SAFE_URL.match(url):
            raise ValueError('URL not safe to pass to a remote shell')
        ssh = self.relay
        remote = f'curl.exe -s -m 15 -A MinerStatusAggregator/1 -H "Accept: application/json" -w "\\n%{{http_code}}" "{url}"'
        done = self.run([ssh['client'], '-i', ssh['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                         '-o', f"UserKnownHostsFile={ssh['known_hosts']}", '-o', 'ConnectTimeout=10',
                         f"{ssh['user']}@{ssh['host']}", remote],
                        capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if done.returncode != 0:
            raise OSError(f'relay exit {done.returncode}')
        body, _, code = done.stdout[:2 * 1048576].decode('utf-8', 'replace').rstrip().rpartition('\n')
        if code.strip() != '200':
            raise OSError(f'relay HTTP {code.strip()}')
        return json.loads(body)

    def get_json(self, url):
        routes = ['direct'] + (['relay'] if self.relay else [])
        now = self.clock()
        with self.lock:
            routes.sort(key=lambda route: self.resting.get(route, 0) > now)  # resting routes last
        error = None
        for route in routes:
            try:
                result = self.direct(url) if route == 'direct' else self.via_relay(url)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:  # URLError and HTTPError are OSErrors
                error = exc
                with self.lock:
                    self.resting[route] = now + self.rest_seconds
                logging.warning('Kryptex query via %s failed (%s)', route, exc)
                continue
            with self.lock:
                if route != self.last_route:
                    logging.info('Kryptex queries now go %s', 'straight from this PC' if route == 'direct' else 'through the relay rig')
                self.last_route = route
            return result
        raise error


class EarningsPoller:
    """Every few minutes: pool worker hashrates, network stats and prices, turned into an estimate.

    The pool account name stays in the local config and is only sent to the pool itself, which
    already receives it as the mining login. A failed refresh keeps no stale numbers.
    """

    def __init__(self, cfg, fetch=None, section='earnings'):
        # A second coin (`earnings_qtc`) has its own section; the pool account is shared.
        self.cfg = dict(cfg[section], account=cfg[section].get('account') or (cfg.get('earnings') or {}).get('account'))
        self.lock, self.result = threading.Lock(), None
        self.workers = None  # {'checked_at': datetime, 'rows': {worker: pool row}}, refreshed more often
        self.fetch = fetch or self.get_json

    @staticmethod
    def get_json(url):
        request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'MinerStatusAggregator/1'})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(2 * 1048576).decode('utf-8'))

    def refresh(self, now=None):
        now = now or datetime.now(UTC)
        cfg, pool = self.cfg, 'https://pool.kryptex.com'
        slug = cfg['pool_coin']
        stats = self.fetch(f'{pool}/{slug}/api/v1/pool/stats')
        blocks = self.fetch(f'{pool}/{slug}/api/v1/pool/blocks')
        workers = self.fetch(f"{pool}/{slug}/api/v3/miner/workers/{cfg['account']}")
        rewards = []
        for block in blocks.get('results', []):
            try:
                value = number(float(block.get('reward')), 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if value:
                rewards.append(value)
        # The newest page can be all pending (no reward yet); then the pool's nominal reward.
        reward = median(rewards) or number(stats.get('block_reward'), 0)
        fee = number(stats.get('fee'), 0, 1)
        block_time = number(stats.get('block_time'), 0)
        network = number(stats.get('net_hashrate'), 0)
        # The pool's own figure, coin per H/s per day, is what its web page multiplies by a worker's
        # 30-minute hashrate (checked against the page 2026-10-28: ours ran 13 % low for PRL). A
        # PROP coin (QTC) publishes none; then the network share of block rewards is the estimate.
        try:
            per_hash_day = number(float(stats.get('estimated_profit_day')), 0) or None
        except (TypeError, ValueError):
            per_hash_day = None
        per_hash_day = per_hash_day or coin_per_day(1.0, network, block_time, reward, fee)
        if per_hash_day:
            per_hash_day *= calibration_factor(cfg['coin'])
        estimates = {}
        for row in workers.get('results', []):
            if not isinstance(row, dict) or not isinstance(row.get('worker'), str):
                continue
            hashrate = None
            for key in ('avg_hashrate_30m', 'avg_hashrate_3h'):
                try:
                    hashrate = number(float(row.get(key)), 0)
                except (TypeError, ValueError):
                    hashrate = None
                if hashrate:
                    break
            estimates[row['worker']] = (hashrate, hashrate * per_hash_day if hashrate and per_hash_day else None)

        def last_price(coin):
            try:
                points = self.fetch(f'{pool}/api/v1/coin/{coin}/price/chart?time_range=day')
                return number(points[-1]['price'], 0)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                return None

        coin_usd, btc_usd = last_price(slug), last_price('btc')
        usd_twd = None
        try:
            quote = self.fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,twd')['bitcoin']
            if number(quote.get('usd'), 0) and number(quote.get('twd'), 0):
                usd_twd = quote['twd'] / quote['usd']
        except (OSError, ValueError, KeyError, TypeError):
            usd_twd = None
        market = {'coin': cfg['coin'], 'coinUsd': coin_usd, 'btcUsd': btc_usd or None,
                  'usdTwd': usd_twd, 'observedAt': iso(now)}
        with self.lock:
            self.result = {'checked_at': now, 'workers': estimates, 'market': market,
                           'per_th_day': per_hash_day * 1e12 if per_hash_day else None}

    def refresh_workers(self, now=None):
        """Pool-side view of each worker (online, last share, shares, 30 min hashrate)."""
        now = now or datetime.now(UTC)
        data = self.fetch(f"https://pool.kryptex.com/{self.cfg['pool_coin']}/api/v3/miner/workers/{self.cfg['account']}")
        rows = {row['worker']: row for row in data.get('results', [])
                if isinstance(row, dict) and isinstance(row.get('worker'), str)}
        with self.lock:
            self.workers = {'checked_at': now, 'rows': rows}

    def run(self):
        last_full = 0.0
        while True:
            try:
                self.refresh_workers()
            except Exception:
                logging.exception('Pool worker refresh failed')
                with self.lock:
                    self.workers = None
            if time.monotonic() - last_full >= self.cfg['poll_seconds']:
                try:
                    self.refresh()
                except Exception:
                    logging.exception('Earnings refresh failed')
                    with self.lock:
                        self.result = None
                last_full = time.monotonic()
            time.sleep(self.cfg.get('worker_poll_seconds', 120))

    def latest(self):
        with self.lock:
            return self.result

    def worker(self, name):
        """Pool row for one worker, or None when missing or older than 10 minutes."""
        with self.lock:
            snapshot = self.workers
        if not snapshot or (datetime.now(UTC) - snapshot['checked_at']).total_seconds() > 600:
            return None
        return snapshot['rows'].get(name)


def ago(seconds):
    if seconds < 60:
        return f'{int(seconds)} 秒'
    if seconds < 3600:
        return f'{int(seconds // 60)} 分鐘'
    hours, minutes = divmod(int(seconds // 60), 60)
    return f'{hours} 小時 {minutes} 分' if minutes else f'{hours} 小時'


def epoch_ms(value):
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(number_value / 1000, UTC) if number_value > 0 else None


STOP_REASON_TEXT = {'userRequested': '手動停止', 'protectionTriggered': '保護機制觸發（例如溫度）',
                    'abnormalExit': '礦工異常結束'}


class ShareRhythm:
    """Usual time between accepted shares, measured here from each machine's own counter.

    The pool's `valid` is a rolling 24-hour count while `opened_at` is the current connection, so
    dividing one by the other collapsed to "about 0 s per share" right after a reconnect (seen on
    2026-10-26 when all rigs moved to the Hong Kong node). Here a run starts when the session id
    changes or the counter drops; five shares into a run give a fresh value, and until then the last
    value is kept, so a reconnect never resets the rhythm to zero.
    """

    def __init__(self):
        self.runs = {}

    def update(self, machine, now):
        key, session = machine['machineId'], machine.get('sessionId')
        accepted = (machine.get('shares') or {}).get('accepted')
        run = self.runs.get(key)
        if accepted is None or session is None:
            return run['expected'] if run else None
        if not run or run['session'] != session or accepted < run['base']:
            run = self.runs[key] = {'session': session, 'since': now, 'base': accepted, 'last': accepted,
                                    'expected': run['expected'] if run else None}
        # Only when the counter moves: measuring up to "now" would stretch the rhythm through a
        # quiet spell, so a drought would never look long.
        if accepted > run['last']:
            run['last'] = accepted
            gained = accepted - run['base']
            if gained >= 5:
                run['expected'] = (now - run['since']).total_seconds() / gained
        return run['expected']


def assess(machine, pool_row, temperature_limit, now=None, stale_after=120, rhythm=None, giving_way=None):
    """The publisher's light, title and plain reason, plus the health details behind it.

    Green needs a running miner, a non-zero hashrate and an accepted share within this machine's
    own normal rhythm (7 × its usual share interval, at least 10 minutes), so sparse but healthy
    share flow stays green. Pool data (online, last share, share counts) is used when available.
    """
    now = now or datetime.now(UTC)
    observed = parse_time(machine.get('observedAt'))
    shares = machine['shares']
    gpus = [g for g in (machine.get('gpus') or []) if g.get('temperatureC') is not None]
    max_temp = max((g['temperatureC'] for g in gpus), default=None)

    opened = epoch_ms(pool_row.get('opened_at')) if pool_row else None
    pool_last = epoch_ms(pool_row.get('last_share')) if pool_row else None
    local_last = parse_time(shares.get('lastAcceptedAt'))
    last_share = max([t for t in (local_last, pool_last) if t], default=None)
    pool_online = (pool_row.get('status') == 'online') if pool_row and pool_row.get('status') else None
    try:
        pool_rate = number(float(pool_row.get('avg_hashrate_30m')), 0) if pool_row else None
    except (TypeError, ValueError):
        pool_rate = None

    session = re.search(r'(\d{8}-\d{6})', machine.get('sessionId') or '')
    utc_session = re.search(r'(\d{8}T\d{6})Z', machine.get('sessionId') or '')  # rtx5080-20261031T034526Z
    started = opened or (datetime.strptime(session[1], '%Y%m%d-%H%M%S').astimezone() if session else
                         datetime.strptime(utc_session[1], '%Y%m%dT%H%M%S').replace(tzinfo=UTC)
                         if utc_session else None)
    uptime = (now - started).total_seconds() if started and started <= now else None

    valid = count(pool_row.get('valid')) if pool_row else None
    expected = rhythm.update(machine, now) if rhythm else None
    if expected is None and valid and valid >= 20:
        expected = 86400 / valid  # rough until measured: the pool's rolling 24-hour count
    if pool_row and valid is not None:
        bad = (count(pool_row.get('stale')) or 0) + (count(pool_row.get('invalid')) or 0)
        valid_percent = valid / (valid + bad) * 100 if valid + bad else None
    elif shares.get('accepted') is not None:
        total = shares['accepted'] + (shares.get('stale') or 0) + (shares.get('rejected') or 0)
        valid_percent = shares['accepted'] / total * 100 if total else None
    else:
        valid_percent = None

    health = {
        'uptimeSeconds': uptime, 'lastShareAt': iso(last_share), 'expectedShareSeconds': expected,
        'poolHashrate': pool_rate, 'poolOnline': pool_online, 'validSharePercent': valid_percent,
        'maxGpuTemperatureC': max_temp, 'temperatureLimitC': temperature_limit, 'observedAt': iso(now),
    }

    def verdict(light, title, reason):
        return {'light': light, 'title': title, 'reason': reason, 'observedAt': iso(now)}, health

    if machine['sourceStatus'] != 'ok' or observed is None or (now - observed).total_seconds() > stale_after:
        return verdict('gray', '失聯', f'超過 {stale_after // 60} 分鐘拿不到這台的狀態資料（不一定代表停機）')
    state = machine['minerRunning']['state']
    if state == 'stopped':
        why = STOP_REASON_TEXT.get(machine['stop']['reason'], '原因不明')
        return verdict('red', '已停止', f'礦工已停止：{why}')
    if state != 'running':
        return verdict('yellow', '狀態未知', '拿得到資料，但無法確認礦工是否在跑')
    rhythm = f'（平常約 {ago(expected)}一個）' if expected else ''
    if temperature_limit is not None and max_temp is not None and max_temp >= temperature_limit - 3:
        return verdict('yellow', '溫度偏高', f'最高 {max_temp:.0f}°C，距保護線 {temperature_limit - max_temp:.0f}°C')
    # A miner at idle GPU priority (the 5080, `giving_way` in the config) slows down while a game or
    # streaming takes the card, which stays loaded: that is giving way, not a fault (green, no notice).
    rate = machine['hashrate']['value']
    if giving_way and uptime and uptime > 180 and rate is not None and rate < giving_way['normal_th'] / 2:
        watts = sum(g.get('powerW') or 0 for g in machine.get('gpus') or [])
        if watts >= giving_way.get('min_gpu_w', 100):
            return verdict('green', '讓路中', f'有其他程式在用顯卡，挖礦自動降速到 {rate:.0f} TH/s（沒有停）')
    # A missing reading (the monitor's log read failed or went stale for a moment) is not a zero:
    # then the shares below decide, so a blip does not show as 算力為 0.
    if rate == 0:
        return verdict('yellow', '算力為 0', '礦工在跑，但回報的算力是 0')
    if pool_online is False:
        return verdict('yellow', '礦池顯示離線', '本機在跑，但礦池那邊顯示這台離線')
    if last_share is None:
        return verdict('yellow', '還沒有份額', '礦工在跑，還沒有被接受的份額')
    quiet = (now - last_share).total_seconds()
    allowed = max(600, 7 * expected) if expected else 900
    if quiet > allowed:
        return verdict('yellow', '太久沒有份額', f'已 {ago(quiet)}沒有新份額{rhythm}')
    return verdict('green', '挖礦中', f'最後份額 {ago(max(0, quiet))}前{rhythm}')


TAIPOWER_TARIFF_URL = 'https://service.taipower.com.tw/data/opendata/apply/file/d007008/001.json'
CALENDAR_DATASET_URL = 'https://data.gov.tw/api/v2/rest/dataset/14718'
# The windows the app's TouTariff implements; a different published text means the app needs updating.
EXPECTED_WINDOWS = {('尖峰時間', '夏月'): '09:00~24:00', ('尖峰時間', '非夏月'): '06:00~11:00/14:00~24:00',
                    ('離峰時間', '夏月'): '00:00~09:00', ('離峰時間', '非夏月'): '00:00~06:00/11:00~14:00'}


def parse_taipower_tariff(document):
    """Simple two-stage TOU rates and effective date from Taipower's open-data price table."""
    section = document['data']['簡易型時間電價(二段式)']['流動電費']
    weekday = section['週一至週五']
    rates = {
        'summerPeak': number(weekday['尖峰時間']['夏月']['單價'], 0),
        'summerOffPeak': number(weekday['離峰時間']['夏月']['單價'], 0),
        'nonSummerPeak': number(weekday['尖峰時間']['非夏月']['單價'], 0),
        'nonSummerOffPeak': number(weekday['離峰時間']['非夏月']['單價'], 0),
    }
    if None in rates.values():
        raise ValueError('missing or invalid rate')
    windows_match = all(weekday.get(period, {}).get(season, {}).get('時段') == text
                        for (period, season), text in EXPECTED_WINDOWS.items())
    weekend = section.get('週六、週日及離峰日', {}).get('離峰時間', {})
    windows_match = windows_match and all(weekend.get(season, {}).get('時段') == '全日' for season in ('夏月', '非夏月'))
    effective = document.get('metadata', {}).get('實施日期')
    if not (isinstance(effective, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective)):
        effective = None
    return dict(rates, effectiveDate=effective, periodsMatch=windows_match)


def calendar_resource(dataset, roc_year):
    """The newest non-Google CSV of the office calendar for one ROC year, or None."""
    resources = [(item.get('resourceDescription') or '', item.get('resourceDownloadUrl'))
                 for item in dataset.get('result', {}).get('distribution', [])
                 if (item.get('resourceFormat') or '').upper() == 'CSV']
    matches = [(text, url) for text, url in resources
               if url and text.startswith(f'{roc_year}年') and '辦公日曆表' in text and 'Google' not in text]
    updated = [match for match in matches if '更新' in match[0]]
    return (updated or matches)[-1][1] if (updated or matches) else None


def weekday_holidays(csv_text):
    """Dates (YYYY-MM-DD) marked as days off that fall on weekdays: off-peak days for the tariff."""
    import csv
    import io
    days = []
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) >= 3 and re.fullmatch(r'\d{8}', row[0].strip()) and row[2].strip() == '2' and row[1].strip() not in ('六', '日'):
            stamp = row[0].strip()
            days.append(f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}')
    return days


class TariffPoller:
    """Once a day: Taipower's published TOU rates and the government calendar's weekday holidays.

    A failed refresh keeps the last good result (also cached on disk for restarts), because a
    missing price would be worse than yesterday's price. A change in the published time windows
    is reported once, since the app's own window rules would then be out of date.
    """

    def __init__(self, cfg, fetch=None, fetch_text=None, cache=None, notify=None):
        self.cfg = cfg.get('tariff', {})
        self.lock, self.result = threading.Lock(), None
        self.fetch = fetch or EarningsPoller.get_json
        self.fetch_text = fetch_text or self.get_text
        self.cache = cache or ROOT / 'tariff-cache.json'
        self.notify = notify
        self.reported_mismatch = False
        try:
            self.result = json.loads(Path(self.cache).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.result = None

    @staticmethod
    def get_text(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'MinerStatusAggregator/1'})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(2 * 1048576)
        for encoding in ('utf-8-sig', 'cp950'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError('undecodable calendar')

    def refresh(self, now=None):
        now = now or datetime.now(UTC)
        tariff = parse_taipower_tariff(self.fetch(TAIPOWER_TARIFF_URL))
        local = now.astimezone(TAIPEI)
        holidays = []
        try:
            dataset = self.fetch(CALENDAR_DATASET_URL)
            for year in (local.year, local.year + 1):
                url = calendar_resource(dataset, year - 1911)
                if url:
                    holidays += weekday_holidays(self.fetch_text(url))
        except (OSError, ValueError, KeyError, TypeError):
            holidays = (self.result or {}).get('offPeakDates', [])  # keep the last known calendar
        today = local.strftime('%Y-%m-%d')
        upcoming = sorted({day for day in holidays if day >= today})[:60]
        result = dict(tariff, name='台電簡易型時間電價（二段式）', offPeakDates=upcoming, observedAt=iso(now))
        with self.lock:
            self.result = result
        try:
            Path(self.cache).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        except OSError:
            logging.exception('Tariff cache write failed')
        if not tariff['periodsMatch'] and not self.reported_mismatch and self.notify:
            try:
                self.notify('⚠️ 台電時間電價時段已變更', '台電公告的尖離峰時段和 App 的計算規則不同，電費估算可能不準，需要更新程式。', 3, ['warning'])
                self.reported_mismatch = True
            except Exception:
                logging.exception('Tariff change notification failed')

    def run(self):
        while True:
            try:
                self.refresh()
            except Exception:
                logging.exception('Tariff refresh failed; keeping the last good tariff')
            time.sleep(self.cfg.get('poll_seconds', 86400))

    def latest(self):
        with self.lock:
            return self.result


def earnings_for(cfg, machine_id, result, section='earnings'):
    """The machine's earnings object, or None when there is no estimate for it."""
    settings = cfg.get(section) or {}
    worker = (settings.get('workers') or {}).get(machine_id)
    billed = (settings.get('electricity_billed') or {}).get(machine_id)
    if result is None or worker is None:
        return None
    hashrate, per_day = result['workers'].get(worker, (None, None))
    return {'coinPerDay': per_day, 'poolHashrate': hashrate,
            'electricityBilled': billed if isinstance(billed, bool) else None,
            'observedAt': iso(result['checked_at'])}


def ramp_up_earnings(machine, earnings, per_th_day):
    """Right after a miner starts, the pool's 3-hour average is still climbing and made a freshly
    started laptop look like a loss (seen 2026-10-26: 10 TH/s at the pool against 25 locally).
    While the pool shows under 70 % of the running miner's own hashrate, estimate from the local
    hashrate instead; a machine that keeps losing shares is caught by the assessment, not here."""
    rate = machine.get('hashrate') or {}
    if (not earnings or per_th_day is None or (machine.get('minerRunning') or {}).get('state') != 'running'
            or rate.get('unit') not in HASH_SCALE or not rate.get('value')):
        return earnings
    local = rate['value'] * HASH_SCALE[rate['unit']] * 1e12  # H/s
    pool = earnings.get('poolHashrate') or 0
    if pool >= 0.7 * local:
        return earnings
    return dict(earnings, coinPerDay=per_th_day * local / 1e12)


TARIFF_KEYS = ('name', 'effectiveDate', 'summerPeak', 'summerOffPeak', 'nonSummerPeak', 'nonSummerOffPeak',
               'periodsMatch', 'offPeakDates', 'observedAt')


def tariff_for_feed(result, now):
    """Whitelisted tariff with only today's and later off-peak days, or None."""
    if not result:
        return None
    today = now.astimezone(TAIPEI).strftime('%Y-%m-%d')
    public = {key: result.get(key) for key in TARIFF_KEYS}
    public['offPeakDates'] = [day for day in result.get('offPeakDates') or [] if day >= today][:60]
    return public


def is_off_day(tariff, when):
    """A weekend or a published off-peak day (national holiday): off-peak all day."""
    local = when.astimezone(TAIPEI)
    return local.weekday() >= 5 or local.strftime('%Y-%m-%d') in ((tariff or {}).get('offPeakDates') or [])


def tou_price(tariff, when):
    """NT$/kWh under Taipower's simple two-stage TOU at this moment, or None without a tariff.
    Weekends and the published off-peak days are off-peak all day."""
    if not tariff:
        return None
    local = when.astimezone(TAIPEI)
    summer = 6 <= local.month <= 9
    peak = local.hour >= 9 if summer else (6 <= local.hour < 11 or local.hour >= 14)
    if is_off_day(tariff, when) or not peak:
        return number(tariff.get('summerOffPeak' if summer else 'nonSummerOffPeak'), 0)
    return number(tariff.get('summerPeak' if summer else 'nonSummerPeak'), 0)


HASH_SCALE = {'H/s': 1e-12, 'kH/s': 1e-9, 'MH/s': 1e-6, 'GH/s': 1e-3, 'TH/s': 1.0, 'PH/s': 1e3}
PROFIT_FIELDS = ('time', 'seconds', 'running', 'hashrate_th', 'gpu_w', 'wall_w', 'coin_per_th_day', 'coin_usd',
                 'usd_twd', 'price_kwh', 'revenue_twd', 'cost_twd', 'idle_cost_twd')


def profit_sample(machine, market, tariff, settings, now, seconds):
    """One interval of estimated income and electricity cost for a machine, as a CSV row.

    Income is this machine's own reported hashrate times the pool estimate per TH/s, so stopped
    minutes earn nothing even while the pool's 3 h average still shows hashrate. Electricity is
    the GPU's reported power plus a fixed allowance for the rest of the machine, through the
    adapter's efficiency; idle_cost is what the machine would draw switched on but not mining.
    """
    running = (machine.get('minerRunning') or {}).get('state') == 'running'
    rate = machine.get('hashrate') or {}
    hashrate = number(rate.get('value'), 0)
    hashrate = hashrate * HASH_SCALE[rate['unit']] if running and hashrate is not None and rate.get('unit') in HASH_SCALE else 0.0
    gpu_w = sum(number(gpu.get('powerW'), 0) or 0 for gpu in machine.get('gpus') or []) if running else 0.0
    wall_w = (gpu_w + settings.get('extra_watts', 10)) / settings.get('adapter_efficiency', 0.88) if running else 0.0
    earnings, market = machine.get('earnings') or {}, market or {}
    per_th = None
    if number(earnings.get('coinPerDay'), 0) is not None and number(earnings.get('poolHashrate'), 0):
        per_th = earnings['coinPerDay'] / (earnings['poolHashrate'] * 1e-12)
    coin_usd, usd_twd = number(market.get('coinUsd'), 0), number(market.get('usdTwd'), 0)
    price = tou_price(tariff, now)
    revenue = None
    if per_th is not None and coin_usd and usd_twd:
        revenue = hashrate * per_th * coin_usd * usd_twd * seconds / 86400
    cost = wall_w * seconds / 3.6e6 * price if price is not None else None
    idle = settings.get('idle_watts', 20) * seconds / 3.6e6 * price if price is not None else None
    return {'time': iso(now), 'seconds': round(seconds, 1), 'running': int(running), 'hashrate_th': round(hashrate, 3),
            'gpu_w': round(gpu_w, 1), 'wall_w': round(wall_w, 1), 'coin_per_th_day': per_th, 'coin_usd': coin_usd,
            'usd_twd': usd_twd, 'price_kwh': price, 'revenue_twd': revenue, 'cost_twd': cost, 'idle_cost_twd': idle}


class ProfitLog:
    """Every few minutes, appends each configured machine's estimated income and electricity cost
    to profit-log/<machine>.csv, for weighing whether a billed machine is worth running.
    Gaps while this service was down are not filled in: an interval counts at most twice the
    sampling period."""

    def __init__(self, cfg, aggregator, directory=None):
        self.settings = cfg.get('profit_log') or {}
        self.aggregator, self.directory = aggregator, Path(directory or ROOT / 'profit-log')
        self.last = {}

    def sample(self, now=None):
        import csv
        now = now or datetime.now(UTC)
        interval = self.settings.get('interval_seconds', 300)
        feed = self.aggregator.feed(now, include_earnings=True)
        tariff = self.aggregator.tariff.latest() if self.aggregator.tariff is not None else None
        self.directory.mkdir(exist_ok=True)
        for machine in feed['machines']:
            machine_id = machine['machineId']
            if machine_id not in (self.settings.get('machines') or []):
                continue
            previous = self.last.get(machine_id)
            self.last[machine_id] = now
            if previous is None:
                continue  # The first sample only starts the clock.
            seconds = min((now - previous).total_seconds(), 2.0 * interval)
            row = profit_sample(machine, feed.get('market'), tariff, self.settings, now, seconds)
            path = self.directory / f'{machine_id}.csv'
            new = not path.exists()
            with path.open('a', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, PROFIT_FIELDS)
                if new:
                    writer.writeheader()
                writer.writerow(row)

    def run_forever(self):
        while True:
            try:
                self.sample()
            except Exception:
                logging.exception('Profit log sample failed')
            time.sleep(self.settings.get('interval_seconds', 300))


POOL_BALANCE = ROOT / 'pool-balance.json'


def balance_for_feed(market, path=None):
    """The `balance` extension: the pool account's balance as its dashboard showed it (a scheduled task
    reads the logged-in page every 2 hours and writes pool-balance.json; there is no API for it), with
    NT$ at the current BTC price. None when there is no reading yet."""
    try:
        saved = json.loads(Path(path or POOL_BALANCE).read_text(encoding='utf-8'))
        btc = number(saved.get('balanceBtc'), 0)
        observed = parse_time(saved.get('observedAt'))
    except (OSError, ValueError, AttributeError):
        return None
    if btc is None or observed is None:
        return None
    market = market or {}
    btc_usd, usd_twd = number(market.get('btcUsd'), 0), number(market.get('usdTwd'), 0)
    return {'balanceBtc': btc, 'twd': btc * btc_usd * usd_twd if btc_usd and usd_twd else None,
            'observedAt': iso(observed)}


class EarningsLedger:
    """Running total of each machine's estimated earnings since `since`, kept across restarts, so the
    user can hold it against the account's BTC at the end of the month (the pool converts every coin
    to BTC and its account balance is not readable).

    Every interval, a running miner adds its current estimate (the same coin/day the feed shows) for
    the time since the last step, in its own coin and in NT$ at this moment's price. A stopped miner
    adds nothing; minutes while this service was down are not filled in (an interval counts at most
    twice the period).

    The total starts over at 00:00 Taipei time on the 1st of each month (user, 2026-10-31); the month
    that ended is kept in `months` (the last 12) to check against the pool's monthly payout."""

    def __init__(self, aggregator, path=None, interval=300):
        self.aggregator, self.interval = aggregator, interval
        self.path = Path(path or ROOT / 'earnings-ledger.json')
        self.lock = threading.Lock()
        self.last = None
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        self.since = saved.get('since')
        self.updated = saved.get('updated')
        self.machines = {key: value for key, value in (saved.get('machines') or {}).items()
                         if isinstance(value, dict) and {'coin', 'amount', 'twd'} <= set(value)}
        # Per machine, BTC added in each Taipei hour before calibration ('2026-10-29T10': btc), the last
        # 8 days: `calibrate.py` holds these against the pool dashboard's hourly earnings.
        self.hours = {key: value for key, value in (saved.get('hours') or {}).items() if isinstance(value, dict)}
        self.months = [item for item in (saved.get('months') or []) if isinstance(item, dict)]
        # Today's actual mining (user, 2026-11-12: the laptop only mines 00–06, so a "current rate
        # times 24 h" day was ~6x off): per machine, NT$ earned and, per Taipei hour, the seconds it
        # mined and the GPU watt-hours, so the app prices the electricity with its own tariff and
        # extra watts. Starts over at 00:00 Taipei time.
        today = saved.get('today') if isinstance(saved.get('today'), dict) else {}
        self.today_date = today.get('date')
        self.today = {key: value for key, value in (today.get('machines') or {}).items()
                      if isinstance(value, dict) and isinstance(value.get('hours'), dict)}

    def add_today(self, machine_id, when, seconds, gpu_w, twd):
        """Adds one interval of mining to today's per-hour record (`when` is the interval's midpoint)."""
        here = when.astimezone(TAIPEI)
        date = here.strftime('%Y-%m-%d')
        if date != self.today_date:
            self.today_date, self.today = date, {}
        entry = self.today.setdefault(machine_id, {'twd': 0.0, 'hours': {}})
        entry['twd'] += twd
        hour = entry['hours'].setdefault(str(here.hour), {'seconds': 0.0, 'gpuWh': 0.0})
        hour['seconds'] += seconds
        hour['gpuWh'] += gpu_w * seconds / 3600

    def new_month(self, now):
        """Closes the running total when `now` is in a later Taipei month than `since`."""
        here = now.astimezone(TAIPEI)
        start = here.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = parse_time(self.since)
        if since is None or since.astimezone(TAIPEI) >= start:
            return
        if self.machines:
            btc = [value.get('btc') for value in self.machines.values()]
            self.months = (self.months + [{
                'since': self.since, 'until': iso(start), 'machines': self.machines,
                'totalTwd': sum(value['twd'] for value in self.machines.values()),
                'totalBtc': sum(btc) if None not in btc else None}])[-12:]
        self.machines, self.since = {}, iso(start)
        logging.info('Earnings ledger: a new month, the running total starts over')

    def step(self, now=None):
        now = now or datetime.now(UTC)
        feed = self.aggregator.feed(now, include_earnings=True, include_coins=True)
        previous, self.last = self.last, now
        if previous is None:
            return  # The first step only starts the clock.
        seconds = min((now - previous).total_seconds(), 2.0 * self.interval)
        market = feed.get('market') or {}
        usd_twd, btc_usd = number(market.get('usdTwd'), 0), number(market.get('btcUsd'), 0)
        with self.lock:
            self.new_month(now)
            self.since = self.since or iso(previous)
            middle = now - timedelta(seconds=seconds / 2)
            for machine in feed['machines']:
                if (machine.get('minerRunning') or {}).get('state') != 'running':
                    continue
                earnings = machine.get('earnings') or {}
                coin = earnings.get('coin') or market.get('coin')
                coin_usd = number(earnings.get('coinUsd') if earnings.get('coin') else market.get('coinUsd'), 0)
                per_day = number(earnings.get('coinPerDay'), 0)
                gpu_w = sum(number(gpu.get('powerW'), 0) or 0 for gpu in machine.get('gpus') or [])
                priced = bool(per_day and coin and coin_usd and usd_twd)
                amount = per_day * seconds / 86400 if priced else 0.0
                # Electricity is spent whether or not there is a price to value the coins at.
                self.add_today(machine['machineId'], middle, seconds, gpu_w, amount * coin_usd * usd_twd if priced else 0.0)
                if not priced:
                    continue
                entry = self.machines.setdefault(machine['machineId'], {'coin': coin, 'amount': 0.0, 'twd': 0.0, 'btc': 0.0})
                if entry['coin'] != coin:
                    continue  # A machine moved to another coin keeps its old total; delete the file to restart.
                if 'btc' not in entry:  # Totals from before BTC was kept: converted once at today's prices.
                    entry['btc'] = entry['twd'] / usd_twd / btc_usd if btc_usd else 0.0
                entry['amount'] += amount
                entry['twd'] += amount * coin_usd * usd_twd
                # The pool converts every coin to BTC, so the account is checked in BTC.
                entry['btc'] += amount * coin_usd / btc_usd if btc_usd else 0.0
                if btc_usd:
                    hour = (now - timedelta(seconds=seconds / 2)).astimezone(TAIPEI).strftime('%Y-%m-%dT%H')
                    hours = self.hours.setdefault(machine['machineId'], {})
                    hours[hour] = hours.get(hour, 0.0) + amount * coin_usd / btc_usd / calibration_factor(coin)
            oldest = (now - timedelta(days=8)).astimezone(TAIPEI).strftime('%Y-%m-%dT%H')
            self.hours = {key: {hour: btc for hour, btc in hours.items() if hour >= oldest}
                          for key, hours in self.hours.items()}
            self.updated = iso(now)
            snapshot = {'since': self.since, 'updated': self.updated, 'machines': self.machines, 'hours': self.hours,
                        'months': self.months, 'today': {'date': self.today_date, 'machines': self.today}}
            try:
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
                temporary.replace(self.path)
            except OSError:
                logging.exception('Earnings ledger not saved')

    def for_feed(self):
        """The `cumulative` extension, or None before the first accrual."""
        with self.lock:
            if not self.since or not self.machines:
                return None
            machines = [{'machineId': key, 'coin': value['coin'], 'amount': value['amount'], 'twd': value['twd']}
                        for key, value in sorted(self.machines.items())][:8]
            btc = [value.get('btc') for value in self.machines.values()]
            return {'since': self.since, 'observedAt': self.updated,
                    'totalTwd': sum(item['twd'] for item in machines),
                    'totalBtc': sum(btc) if None not in btc else None, 'machines': machines}

    def today_for_feed(self, now=None):
        """The `today` extension: what each machine actually mined since 00:00 Taipei time, with the
        seconds and GPU watt-hours of every hour it mined. Machines switched off now (the laptop after
        06:00) are still in it. Empty machines on a day nothing has mined yet."""
        now = now or datetime.now(UTC)
        date = now.astimezone(TAIPEI).strftime('%Y-%m-%d')
        with self.lock:
            records = self.today if self.today_date == date else {}
            machines = [{'machineId': key, 'twd': value['twd'],
                         'hours': [{'hour': int(hour), 'miningSeconds': item['seconds'], 'gpuWh': item['gpuWh']}
                                   for hour, item in sorted(value['hours'].items(), key=lambda pair: int(pair[0]))]}
                        for key, value in sorted(records.items())][:8]
            return {'date': date, 'observedAt': self.updated, 'machines': machines}

    def run_forever(self):
        while True:
            try:
                self.step()
            except Exception:
                logging.exception('Earnings ledger step failed')
            time.sleep(self.interval)


# Weekday peak hours under the simple two-stage TOU: 15 a day in both seasons (summer 09–24,
# non-summer 06–11 and 14–24); weekends are off-peak all day.
PEAK_SHARE = 5 * 15 / (7 * 24)


def average_price(tariff, now):
    """A week's average NT$/kWh for round-the-clock use in the current season, or None."""
    if not tariff:
        return None
    summer = 6 <= now.astimezone(TAIPEI).month <= 9
    peak = number(tariff.get('summerPeak' if summer else 'nonSummerPeak'), 0)
    off = number(tariff.get('summerOffPeak' if summer else 'nonSummerOffPeak'), 0)
    if peak is None or off is None:
        return None
    return PEAK_SHARE * peak + (1 - PEAK_SHARE) * off


def profile_watts(profile, idle=False):
    """Wall draw while mining: the meter reading `wall_watts` when the profile has one, otherwise the
    GPU plus an allowance through the adapter. `idle` adds the idle draw on top."""
    watts = profile.get('wall_watts') or (profile['gpu_w'] + profile.get('extra_watts', 0)) / profile.get('adapter_efficiency', 1.0)
    return watts + (profile.get('idle_watts', 0) if idle else 0)


def monthly_net(profile, per_th_day, coin_usd, usd_twd, price):
    """Estimated NT$ a month from mining round the clock with this profile, after electricity,
    or None without market data. `count_idle` also charges the idle draw (for a machine that
    would otherwise be switched off)."""
    if None in (per_th_day, coin_usd, usd_twd, price):
        return None
    income = profile['hashrate_th'] * per_th_day * coin_usd * usd_twd * 30
    return income - profile_watts(profile, profile.get('count_idle')) * 24 * 30 / 1000 * price


class Advisor:
    """Says when a billed machine is worth mining, from the current pool estimate and tariff.

    Each machine has a fixed profile (its measured hashrate and power), so the advice also
    covers a machine that is switched off. The recommendation turns to 'mine' when the average
    over `hold_hours` reaches `start_at`, and to 'rest' when it falls below `stop_below`; the
    gap keeps it from flapping. Each change is sent once to ntfy and kept across restarts.
    """

    TEXT = {
        'mine': ('💰 {name}：收益回來了，可以開機挖', '{reason}'),
        'rest': ('💤 {name}：收益太差，建議停挖關機', '{reason}'),
    }

    def __init__(self, cfg, earnings, tariff, notify=None, path=None):
        self.settings = cfg.get('advice') or {}
        self.earnings, self.tariff, self.notify = earnings, tariff, notify
        # Machines the automation starts and stops itself: their advice changes need no notification.
        self.automatic = set((cfg.get('auto_mine') or {}).get('machines') or {})
        self.path = Path(path or ROOT / 'advice-state.json')
        self.lock = threading.Lock()
        self.state = self.load()

    def load(self):
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        state = {}
        for machine_id, profile in (self.settings.get('machines') or {}).items():
            entry = saved.get(machine_id) or {}
            state[machine_id] = {
                'recommendation': entry.get('recommendation') if entry.get('recommendation') in self.TEXT else profile['initial'],
                'since': entry.get('since'),
                'samples': [s for s in entry.get('samples') or [] if isinstance(s, list) and len(s) == 2],
            }
        return state

    def save(self):
        try:
            self.path.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
        except OSError:
            logging.exception('Advice state not saved')

    def reason(self, profile, recommendation, net, average, hold):
        if net is None:
            return '收益資料不足，暫時無法估算'
        if recommendation == 'mine':
            if average is not None and average >= profile['start_at']:
                return f"估算每月淨收益 NT${average:.0f}，已連續 {hold:g} 小時達開挖門檻 NT${profile['start_at']:.0f}"
            return f"估算每月淨收益 NT${net:.0f}，低於 NT${profile['stop_below']:.0f} 會提醒停挖"
        if average is not None and average < profile['stop_below']:
            return f"估算每月淨收益 NT${average:.0f}，連續 {hold:g} 小時低於 NT${profile['stop_below']:.0f}，挖了不划算"
        return f"估算每月淨收益 NT${net:.0f}，未達開挖門檻 NT${profile['start_at']:.0f}"

    def step(self, now=None):
        now = now or datetime.now(UTC)
        result = self.earnings.latest() if self.earnings is not None else None
        market = (result or {}).get('market') or {}
        price = average_price(self.tariff.latest() if self.tariff is not None else None, now)
        hold = self.settings.get('hold_hours', 6)
        changes = []
        with self.lock:
            for machine_id, profile in (self.settings.get('machines') or {}).items():
                entry = self.state[machine_id]
                net = monthly_net(profile, (result or {}).get('per_th_day'), market.get('coinUsd'),
                                  market.get('usdTwd'), price)
                cutoff = (now - timedelta(hours=hold)).timestamp()
                samples = [s for s in entry['samples'] if s[0] >= cutoff]
                if net is not None:
                    samples.append([now.timestamp(), net])
                entry['samples'], entry['net'] = samples, net
                if profile.get('auto'):
                    continue  # The automation decides hour by hour and reports through set_auto.
                interval = self.settings.get('interval_seconds', 600)
                covered = len(samples) * interval >= 0.8 * hold * 3600
                average = sum(s[1] for s in samples) / len(samples) if covered else None
                target = entry['recommendation']
                if average is not None and target == 'rest' and average >= profile['start_at']:
                    target = 'mine'
                elif average is not None and target == 'mine' and average < profile['stop_below']:
                    target = 'rest'
                if target != entry['recommendation']:
                    entry['recommendation'], entry['since'] = target, iso(now)
                    if machine_id not in self.automatic:
                        changes.append((profile, target, net, average))
                entry['reason'] = self.reason(profile, target, net, average, hold)
                entry['observedAt'] = iso(now) if net is not None else entry.get('observedAt')
            self.save()
        for profile, target, net, average in changes:
            heading, body = self.TEXT[target]
            text = self.reason(profile, target, net, average, hold)
            if self.notify:
                try:
                    self.notify(heading.format(name=profile['display_name']), body.format(reason=text), 4,
                                ['moneybag' if target == 'mine' else 'zzz'])
                except Exception:
                    logging.exception('Advice notification failed')

    def set_auto(self, machine_id, recommendation, reason, now):
        """The automation's current decision for a machine it runs (profile `auto`)."""
        with self.lock:
            entry = self.state.get(machine_id)
            if entry is None:
                return
            if recommendation != entry['recommendation']:
                entry['recommendation'], entry['since'] = recommendation, iso(now)
            entry['reason'] = ('自動：' + reason)[:200]
            entry['observedAt'] = iso(now)

    def for_feed(self, can_wake=None):
        """The `advice` extension: one item per configured machine. With `can_wake` (feature `wake`),
        each item also says whether the app may offer a remote power-on."""
        items = []
        with self.lock:
            for machine_id, profile in (self.settings.get('machines') or {}).items():
                entry = self.state[machine_id]
                net = entry.get('net')
                item = {'machineId': machine_id, 'displayName': profile['display_name'],
                        'recommendation': entry['recommendation'],
                        'monthlyNetTwd': round(net, 1) if net is not None else None,
                        'startAtTwd': profile['start_at'], 'stopBelowTwd': profile['stop_below'],
                        'reason': entry.get('reason') or '收益資料不足，暫時無法估算',
                        'since': entry.get('since'), 'observedAt': entry.get('observedAt')}
                if can_wake is not None:
                    item['canWake'] = bool(can_wake(machine_id))
                items.append(item)
        return items

    def run_forever(self):
        time.sleep(60)  # Give the earnings poller its first fetch, or the first step has nothing to judge.
        while True:
            try:
                self.step()
            except Exception:
                logging.exception('Advice step failed')
            time.sleep(self.settings.get('interval_seconds', 600))


class Aggregator:
    def __init__(self, cfg, poller, earnings=None, tariff=None, extra=None):
        self.cfg, self.poller, self.earnings, self.tariff = cfg, poller, earnings, tariff
        self.lock = threading.Lock()
        instance = datetime.now(UTC).strftime('r%Y%m%dT%H%M%SZ')
        self.office_tracker = SessionTracker(cfg['office']['machine_id'], instance)
        self.rtx_tracker = SessionTracker(cfg['rtx']['machine_id'])
        # Further machines in the RTX status shape, each read by its own poller: {section: poller}.
        self.extra = extra or {}
        self.extra_trackers = {section: SessionTracker(cfg[section]['machine_id']) for section in self.extra}
        self.rhythm = ShareRhythm()
        self.coins = {}  # Pollers for machines mining another coin: {config section: EarningsPoller}

    def earnings_source(self, machine_id):
        """(config section, poller) that holds this machine's pool worker: its own coin's, else the main one."""
        for section, poller in self.coins.items():
            if machine_id in (self.cfg[section].get('workers') or {}):
                return section, poller
        return 'earnings', self.earnings

    def feed(self, now=None, include_earnings=False, include_assessment=False, include_control=False,
             include_advice=False, include_wake=False, include_coins=False, include_cumulative=False,
             include_balance=False, include_today=False):
        """Base whitelist feed; optional extensions only when asked for, so apps that do not know
        these keys (and reject unknown keys) keep working.

        The app prices every machine in the feed's one coin, so a machine mining another coin gets no
        earnings there; with `coins` (the desktop console) it gets its own, tagged with coin and price."""
        now = now or datetime.now(UTC)
        with self.lock:
            machines = [office_machine(self.cfg, self.office_tracker, now),
                        rtx_machine(self.cfg, self.poller.latest(), self.rtx_tracker, now)]
            # A machine that may be switched off (`hide_when_off`) leaves the list while it is off,
            # so it neither shows as 失聯 nor raises alerts; its advice row offers a wake-up instead.
            machines += [rtx_machine(self.cfg, poller.latest(), self.extra_trackers[section], now, section)
                         for section, poller in self.extra.items()
                         if not (self.cfg[section].get('hide_when_off') and poller.powered_off())]
        feed = {'schemaVersion': 1, 'dataOrigin': 'live', 'demoNotice': None,
                'publishedAt': iso(now), 'machines': machines}
        if include_earnings and self.earnings is not None:
            result = self.earnings.latest()
            feed['market'] = result['market'] if result else None
            for machine in machines:
                section, source = self.earnings_source(machine['machineId'])
                if section == 'earnings':
                    machine['earnings'] = ramp_up_earnings(machine, earnings_for(self.cfg, machine['machineId'], result),
                                                           (result or {}).get('per_th_day'))
                    continue
                own = source.latest() if include_coins else None
                item = ramp_up_earnings(machine, earnings_for(self.cfg, machine['machineId'], own, section),
                                        (own or {}).get('per_th_day'))
                market = (own or {}).get('market') or {}
                # Only the keys the app's schema allows: the exchange rate stays the feed market's.
                machine['earnings'] = dict(item, coin=market.get('coin'), coinUsd=market.get('coinUsd')) \
                    if item and market.get('coin') else None
        if include_earnings and self.tariff is not None:
            feed['tariff'] = tariff_for_feed(self.tariff.latest(), now)
        ledger = getattr(self, 'ledger', None)
        if include_balance:
            latest = self.earnings.latest() if self.earnings is not None else None
            feed['poolBalance'] = balance_for_feed((latest or {}).get('market'))
        if include_cumulative and ledger is not None:
            feed['cumulative'] = ledger.for_feed()
        if include_today and ledger is not None:
            feed['today'] = ledger.today_for_feed(now)
        advisor = getattr(self, 'advisor', None)
        controller = getattr(self, 'controller', None)
        if include_advice and advisor is not None:
            wake = controller.can_wake if include_wake and controller is not None else (lambda _: False) if include_wake else None
            feed['advice'] = advisor.for_feed(wake)
        if controller is not None:
            for machine in machines:
                if machine['machineId'] not in controller.stopped_by_phone:
                    continue
                if (machine.get('minerRunning') or {}).get('state') == 'running':
                    controller.stopped_by_phone.discard(machine['machineId'])  # started again some other way
                elif (machine.get('stop') or {}).get('reason') in (None, 'unknown'):
                    machine['stop']['reason'] = 'userRequested'
        if include_control and controller is not None:
            for machine in machines:
                if controller.can_control(machine['machineId']):
                    machine['control'] = control_for_feed(machine)
        if include_assessment:
            limits = self.cfg.get('temperature_limits') or {}
            giving_way = self.cfg.get('giving_way') or {}
            for machine in machines:
                section, source = self.earnings_source(machine['machineId'])
                worker = ((self.cfg.get(section) or {}).get('workers') or {}).get(machine['machineId'])
                row = source.worker(worker) if source is not None and worker else None
                machine['assessment'], machine['health'] = assess(machine, row, limits.get(machine['machineId']), now,
                                                                  rhythm=self.rhythm,
                                                                  giving_way=giving_way.get(machine['machineId']))
        return feed


class Notifier:
    """Phone notifications through ntfy, from the same assessment the app shows.

    Red, gray, hot and other yellow states must each last their configured time before they
    notify; each notifies once until the machine is green again. Since 11/02 only what stays
    stuck long enough to need the user is sent (the delays in aggregator.json); recoveries are not.
    Messages carry the machine's display name and the assessment text only, never addresses.
    """

    TEXT = {
        'stopped': ('🛑 {name} 已停止挖礦', 4, ['rotating_light']),
        'offline': ('⚠️ {name} 失聯', 4, ['warning']),
        'hot': ('🌡️ {name} 溫度偏高', 4, ['thermometer']),
        'attention': ('⚠️ {name} 需要注意：{title}', 3, ['warning']),
    }

    def __init__(self, cfg, send=None):
        self.cfg = cfg['notify']
        self.send = send or self.post
        self.state = {}

    def post(self, title, message, priority, tags):
        body = json.dumps({'topic': self.cfg['topic'], 'title': title, 'message': message,
                           'priority': priority, 'tags': tags}, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.cfg['server'], data=body, method='POST',
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read(4096)

    def conditions(self, machine, track, now):
        """Conditions that have lasted long enough to notify, keyed to their message text."""
        assessment, health = machine['assessment'], machine.get('health') or {}
        light = assessment['light']
        limit, hottest = health.get('temperatureLimitC'), health.get('maxGpuTemperatureC')
        hot = limit is not None and hottest is not None and hottest >= limit - 3
        # A stop someone asked for (the phone, the desktop shortcut or the automation) is not news.
        wanted = (machine.get('stop') or {}).get('reason') == 'userRequested'
        active = {'stopped': light == 'red' and not wanted, 'offline': light == 'gray', 'hot': hot,
                  'attention': light == 'yellow' and not hot}
        delays = {'stopped': self.cfg['stopped_after_seconds'], 'offline': self.cfg['offline_after_seconds'],
                  'hot': self.cfg.get('hot_after_seconds', 60), 'attention': self.cfg.get('attention_after_seconds', 600)}
        found = {}
        for key, is_active in active.items():
            track[key] = (track.get(key) or now) if is_active else None
            # A delay of null turns that kind off (shown in the app only).
            if is_active and delays[key] is not None and (now - track[key]).total_seconds() >= delays[key]:
                found[key] = assessment['reason']
        return found

    def check(self, feed, now=None):
        now = now or datetime.now(UTC)
        for machine in feed['machines']:
            if not machine.get('assessment'):
                continue
            track = self.state.setdefault(machine['machineId'], {'notified': set()})
            found = self.conditions(machine, track, now)
            name, title = machine['displayName'], machine['assessment']['title']
            for key in found.keys() - track['notified']:
                heading, priority, tags = self.TEXT[key]
                try:
                    self.send(heading.format(name=name, title=title), found[key], priority, tags)
                    track['notified'].add(key)
                except Exception:
                    logging.exception('Notification failed; will retry')
            if track['notified'] and not found and machine['assessment']['light'] == 'green':
                # Recovered: nothing for the user to do, so not sent (11/02); the next stop is told again.
                logging.info('%s recovered', name)
                track['notified'].clear()

    def run(self, aggregator):
        while True:
            try:
                self.check(aggregator.feed(include_assessment=True))
            except Exception:
                logging.exception('Notifier check failed')
            time.sleep(self.cfg['check_seconds'])


class NodeWatcher:
    """Every `interval_seconds`, asks each rig (from the rig itself) how fast the Kryptex nodes are
    and which one its miner is on; moves a rig only when that is clearly worth a miner restart.

    A move needs the current node to be unreachable from that rig, or at least `min_gain_ms` slower
    and `min_ratio` times the best one; one move per rig per `cooldown_seconds`. The move is a
    SWITCH_NODES file the rig's guard reads: it restarts only the miner with the new order, keeping
    every thermal and safety rule. krig itself already fails over when a node drops; this covers a
    node that stays connected but gets slow or unreachable from one place.
    """

    def __init__(self, cfg, notify=None, run=subprocess.run):
        self.cfg = cfg['node_watch']
        self.machines = self.cfg['machines']
        self.notify, self.run = notify, run
        self.lock = threading.Lock()
        self.state = {}  # machine id -> {'probe', 'checkedAt', 'lastSwitch', 'error'}

    def probe_script(self, machine):
        nodes = ','.join(f"'{node}'" for node in self.cfg['nodes'])
        return (
            "$r=[ordered]@{}; foreach($n in @(" + nodes + ")){ $s=@(); for($i=0;$i -lt 3;$i++){ "
            "$c=New-Object Net.Sockets.TcpClient; $w=[Diagnostics.Stopwatch]::StartNew(); "
            "try{ if($c.ConnectAsync($n,8048).Wait(3000) -and $c.Connected){ $s+=$w.Elapsed.TotalMilliseconds } }catch{} "
            "finally{ $c.Close() } }; $r[$n]= if($s.Count){ ($s|Sort-Object)[[int][Math]::Floor($s.Count/2)] } else { $null } }; "
            f"$log = Get-ChildItem '{machine['log_dir']}' -Filter 'mining-*.log' -ErrorAction SilentlyContinue | "
            "Sort-Object LastWriteTime -Descending | Select-Object -First 1; $cur=$null; "
            # krig logs "connected: stratum+ssl://<node>:8048", SRBMiner "Connected to <node>:8048".
            "if($log){ $m = Select-String -Path $log.FullName -Pattern '(?:connected: stratum\\+ssl://|Connected to )([^:/\\s]+)' | Select-Object -Last 1; "
            "if($m){ $cur=$m.Matches[0].Groups[1].Value } }; "
            "$run = [bool](Get-Process krig-miner,SRBMiner-MULTI -ErrorAction SilentlyContinue); "
            "[ordered]@{latency=$r; current=$cur; running=$run} | ConvertTo-Json -Compress")

    def execute(self, machine, script, timeout=60):
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        remote = f'powershell -NoProfile -NonInteractive -EncodedCommand {encoded}'
        ssh = machine.get('ssh')
        if ssh:
            command = [ssh['client'], '-i', ssh['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                       '-o', f"UserKnownHostsFile={ssh['known_hosts']}", '-o', 'ConnectTimeout=10',
                       f"{ssh['user']}@{ssh['host']}", remote]
        else:
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
        done = self.run(command, capture_output=True, timeout=timeout,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if done.returncode != 0:
            raise OSError(f'exit {done.returncode}')
        return done.stdout.decode('utf-8', 'replace').strip()

    @staticmethod
    def decide(probe, nodes, last_switch, now, rules):
        """New node order for a rig, or None to leave it alone."""
        if not isinstance(probe, dict) or not probe.get('running') or not probe.get('current'):
            return None  # not mining, or the node in use is unknown: nothing to judge
        latency = probe.get('latency') or {}
        reachable = {node: ms for node, ms in latency.items()
                     if node in nodes and isinstance(ms, (int, float)) and not isinstance(ms, bool)}
        if not reachable:
            return None
        best = min(reachable, key=reachable.get)
        current = probe['current']
        if current == best:
            return None
        now_ms = reachable.get(current)
        if now_ms is not None and (now_ms - reachable[best] < rules['min_gain_ms']
                                   or now_ms < rules['min_ratio'] * reachable[best]):
            return None
        if last_switch and (now - last_switch).total_seconds() < rules['cooldown_seconds']:
            return None
        return sorted(reachable, key=reachable.get) + [node for node in nodes if node not in reachable]

    def check_once(self, now=None):
        for machine_id, machine in self.machines.items():
            now = datetime.now(UTC) if now is None else now
            with self.lock:
                entry = self.state.setdefault(machine_id, {'probe': None, 'checkedAt': None, 'lastSwitch': None, 'error': None})
            try:
                probe = json.loads(self.execute(machine, self.probe_script(machine)) or 'null')
                entry.update(probe=probe, checkedAt=now, error=None)
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                entry.update(checkedAt=now, error=str(error)[:200])
                continue
            order = self.decide(probe, self.cfg['nodes'], entry['lastSwitch'], now, self.cfg)
            if not order:
                continue
            body = '\r\n'.join(order)
            script = f"Set-Content -Path '{machine['switch_path']}' -Value \"{body}\" -Encoding ascii; 'ok'"
            try:
                self.execute(machine, script)
            except (OSError, subprocess.TimeoutExpired) as error:
                entry['error'] = f'switch failed: {error}'[:200]
                continue
            entry['lastSwitch'] = now
            # Logged only: an automatic switch needs nothing from the user (no ntfy since 2026-10-28).
            logging.warning('node watch: %s %s -> %s', machine_id, probe.get('current'), order[0])

    def snapshot(self):
        with self.lock:
            return {machine_id: {'current': (entry['probe'] or {}).get('current'),
                                 'running': (entry['probe'] or {}).get('running'),
                                 'latency': (entry['probe'] or {}).get('latency'),
                                 'checkedAt': iso(entry['checkedAt']), 'lastSwitch': iso(entry['lastSwitch']),
                                 'error': entry['error']}
                    for machine_id, entry in self.state.items()}

    def run_forever(self):
        time.sleep(60)  # let the rigs settle after a start
        while True:
            try:
                self.check_once()
            except Exception:
                logging.exception('node watch failed')
            time.sleep(self.cfg['interval_seconds'])


class CommandControl:
    """Start/stop through one command per action whose last output line is the result key.

    Used for the RTX 5080 over SSH (an administrator login there, so its guard needs no UAC prompt,
    like the laptop) and for the FPGA's own control script on this PC. Every command returns within
    `timeout`, under the app's 30 s wait for an answer."""

    def __init__(self, cfg, section, commands, timeout=24, run=subprocess.run):
        self.cfg, self.commands, self.timeout, self.run = cfg[section], commands, timeout, run

    def act(self, action):
        try:
            done = self.run(self.commands[action], capture_output=True, timeout=self.timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            return None
        # The last line is the answer whatever the exit code (the FPGA script exits 1 on start-failed);
        # an ssh that cannot connect prints nothing to stdout.
        lines = done.stdout.decode('utf-8', 'replace').strip().splitlines()
        return lines[-1].strip() if lines else None


class DetachedCommandControl(CommandControl):
    """CommandControl for an FPGA's own script on this PC (mining_ctl.py), run detached with its output in
    a file, so a start that outlasts the app's wait keeps going instead of being killed half way: a KV260
    just powered up is JTAG-loaded first (20 s or more) before its 8 s self-test and the relay's 12 s
    (2026-11-04). Past `timeout` without an answer a start reads as 'started' and the machine's own status
    shows what came of it; a second press meanwhile does not launch the script again."""

    FLAGS = (getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
             | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: outlive this service too

    def __init__(self, cfg, section, commands, timeout=25, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep):
        super().__init__(cfg, section, commands, timeout)
        self.popen, self.clock, self.sleep = popen, clock, sleep
        self.running = {}  # action -> the process still working on it

    def act(self, action):
        if self.running.get(action) is not None and self.running[action].poll() is None:
            return 'started' if action == 'start' else None
        out = Path(self.cfg['log_dir']) / f"ctl_{action}_{self.cfg['machine_id']}_status.log"
        try:
            with out.open('wb') as sink:
                spawn = lambda flags: self.popen(self.commands[action], stdout=sink, stderr=subprocess.STDOUT,
                                                 stdin=subprocess.DEVNULL, creationflags=flags)
                try:
                    proc = spawn(self.FLAGS | self.BREAKAWAY)
                except OSError:  # breakaway refused by the job this service runs in
                    proc = spawn(self.FLAGS)
        except OSError:
            return None
        self.running[action] = proc
        deadline = self.clock() + self.timeout
        while proc.poll() is None and self.clock() < deadline:
            self.sleep(0.5)
        if proc.poll() is None:
            return 'started' if action == 'start' else None
        lines = out.read_text(encoding='utf-8', errors='replace').strip().splitlines()
        return lines[-1].strip() if lines else None


def ssh_powershell(ssh, script, connect_timeout=8):
    """ssh command running a PowerShell script, sent encoded so no quoting passes through ssh and cmd."""
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return [ssh['client'], '-i', ssh['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', f"UserKnownHostsFile={ssh['known_hosts']}", '-o', f'ConnectTimeout={connect_timeout}',
            f"{ssh['user']}@{ssh['host']}", f'powershell -NoProfile -NonInteractive -EncodedCommand {encoded}']


def rtx_control_commands(control):
    """Start and stop for the RTX 5080's session.py (Pearl-mining folder `control.root` on that PC).

    Start runs the PearlAutoStart task (2026-11-01): a supervisor that waits for the GPU to cool and keeps
    session.py running whatever ends it, until a stop. Without the task, session.py is launched directly as
    before (it refuses a GPU at 60 °C or more, reported as start-failed). Stop first leaves HOLD, which
    the supervisor obeys even between sessions, then asks the guard through STOP_REQUEST, which stops krig
    and lets session.py restore the GPU. A guard started before STOP_REQUEST existed does not see the
    file: then the session processes are ended; their job object ends krig and the restore watcher that
    session.py left running returns the GPU to its own settings."""
    root = control['root']
    running = "Get-Process krig-miner,SRBMiner-MULTI -ErrorAction SilentlyContinue"
    start = ("if (" + running + ") { 'already-running' } else { "
             f"$root = '{root}'; Remove-Item -Force -ErrorAction SilentlyContinue \"$root\\STOP_REQUEST\", \"$root\\HOLD\"; "
             "if (Get-ScheduledTask -TaskName 'PearlAutoStart' -ErrorAction SilentlyContinue) { "
             "Start-ScheduledTask -TaskName 'PearlAutoStart'; Start-Sleep -Seconds 8; "
             "if ((Get-ScheduledTask -TaskName 'PearlAutoStart').State -eq 'Running') { 'started' } else { 'start-failed' } } else { "
             "$line = '\"' + (Join-Path $root '.venv\\Scripts\\python.exe') + '\" \"' + (Join-Path $root 'session.py') + '\"'; "
             "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $line; CurrentDirectory = $root}; "
             "if ($r.ReturnValue -ne 0) { 'start-failed' } else { Start-Sleep -Seconds 8; "
             "if (Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue) { 'started' } else { 'start-failed' } } } }")
    wait = "for ($i = 0; $i -lt {n}; $i++) {{ Start-Sleep -Seconds 2; if (-not (" + running + ")) {{ break }} }}; "
    # HOLD first and always: the supervisor may be between sessions (cooling down) with no miner running.
    stop = (f"$root = '{root}'; New-Item -ItemType File -Force -Path \"$root\\HOLD\" | Out-Null; "
            "if (-not (" + running + ")) { 'not-running' } else { "
            "New-Item -ItemType File -Force -Path \"$root\\STOP_REQUEST\" | Out-Null; "
            + wait.format(n=6) +
            "if (" + running + ") { "
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'session\\.py' -and $_.CommandLine -notmatch '--restore' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
            + wait.format(n=3) + "}; "
            "Remove-Item -Force -ErrorAction SilentlyContinue \"$root\\STOP_REQUEST\"; "
            "if (" + running + ") { 'stop-failed' } else { 'stopped' } }")
    return {'start': ssh_powershell(control['ssh'], start), 'stop': ssh_powershell(control['ssh'], stop)}


class OfficeControl:
    """Start/stop of this PC's mining through the AI console, which owns it.

    The console gets the same request its desktop shortcuts send, so its own checks still apply (an
    image being made or Hermes answering refuses the switch), and it starts the miner through the
    PearlPanelBridge task, without a UAC prompt. A closed console is opened in mining mode. Answers
    once the miner is preparing (start) or gone (stop), within the app's 30 s wait."""

    REFUSALS = ('有圖片', 'Hermes 正在', '偵測到獨立')

    def __init__(self, cfg, launch=subprocess.Popen, clock=time.time, sleep=time.sleep, alive=None, wait=20):
        self.cfg, self.launch, self.clock, self.sleep, self.wait = cfg['office'], launch, clock, sleep, wait
        self.alive = alive or process_alive
        control = self.cfg['control']
        self.console = Path(control['console_dir'])
        self.python = control['console_python']

    @staticmethod
    def read(path):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def mining(self):
        state = self.read(self.cfg['state_file'])
        return state.get('status') in ('running', 'preparing') and self.clock() - (state.get('updated') or 0) < 90

    def console_status(self):
        """The console window's status line, or None when it is not running."""
        state = self.read(self.console / 'media-control-state.json')
        if state.get('closed') or not state.get('pid') or not self.alive(state['pid']):
            return None
        return state.get('status') or ''

    def request(self, action):
        path = self.console / 'media-control-request.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'action': action, 'time': self.clock()}), encoding='utf-8')
        temporary.replace(path)

    def act(self, action):
        if action == 'stop':
            if not self.mining():
                return 'not-running'
            if self.console_status() is None:
                return 'stop-failed'  # Mining without its console is not something this PC does; look at it.
            self.request('Stop')
            for _ in range(self.wait):
                self.sleep(1)
                if not self.mining():
                    return 'stopped'
            return 'stop-requested'
        if self.mining():
            return 'already-running'
        if self.console_status() is None:
            self.launch([self.python, str(self.console / 'media_control.py'), '--mode', 'Mining'], cwd=str(self.console),
                        creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        else:
            self.request('Mining')
        for _ in range(self.wait):
            self.sleep(1)
            if self.mining():
                return 'started'
            # Once the console has taken the request, its status line is the answer to it.
            status = self.console_status()
            if status and status.startswith(self.REFUSALS) and not (self.console / 'media-control-request.json').exists():
                return 'start-refused'
        return 'start-failed'


class Controller:
    """Start/stop for machines listed in `control.machines`, the only write path in this service.

    A request needs the shared token (constant-time compare). Five wrong tokens lock control for
    ten minutes; one action per machine per 15 s. Every action is logged (not sent to ntfy: the
    person pressing the button sees the answer in the app). Starting runs the machine's own guard, which
    keeps every thermal, battery and emergency rule; stopping uses the guard's STOP file.
    """

    COOLDOWN, MAX_FAILURES, LOCK = 15, 5, 600

    MESSAGES = {
        'started': '已送出開始指令；保護程式會先檢查溫度與電源',
        'already-running': '已經在挖礦了',
        'stop-requested': '已送出停止指令；保護程式會停止並還原時脈',
        'stopped': '已停止挖礦',
        'not-running': '目前沒有在挖礦',
        'start-failed': '保護程式沒有啟動（可能溫度還太高或資源被占用），請稍後再試',
        'start-refused': '電腦正在生圖或 Hermes 回答中，等它完成再開挖',
        'stop-failed': '停止沒有完成，請到電腦上確認',
        'woke': '已送出開機指令；約 1 分鐘後開機完成，之後可以遠端開始挖礦',
        'already-on': '這台已經開著',
    }

    def __init__(self, cfg, pollers, notify=None, run=subprocess.run, clock=time.monotonic, drivers=None):
        settings = cfg.get('control') or {}
        self.token = settings.get('token') or ''
        allowed = settings.get('machines') or []
        self.machines = {cfg[section]['machine_id']: poller for section, poller in pollers.items()
                         if cfg[section]['machine_id'] in allowed}
        # Machines started and stopped by their own means (CommandControl, OfficeControl): {machine_id: driver}
        self.drivers = {machine_id: driver for machine_id, driver in (drivers or {}).items() if machine_id in allowed}
        self.machines.update(self.drivers)
        self.notify, self.run, self.clock = notify, run, clock
        self.lock = threading.Lock()
        self.failures, self.locked_until, self.last_action = [], 0.0, {}
        self.on_manual = None  # machine_id -> None, called after a successful action from the phone
        # Machines stopped from the phone, until they run again: the feed marks them userRequested, so
        # neither the app nor a notification treats a stop the user asked for as trouble.
        self.stopped_by_phone = set()

    def can_control(self, machine_id):
        return bool(self.token) and machine_id in self.machines

    def can_wake(self, machine_id):
        poller = self.machines.get(machine_id)
        return (self.can_control(machine_id) and bool(poller.cfg.get('wake'))
                and poller.powered_off())

    def wake(self, machine_id, run=None):
        """Wake-on-LAN for a switched-off machine, sent by a machine on its own network (`wake.relay`),
        since the magic packet is a local broadcast and the sleeping machine has no VPN address.
        'woke', 'already-on', or None when the relay cannot be reached."""
        poller = self.machines[machine_id]
        if not poller.powered_off():
            return 'already-on'
        wake = poller.cfg['wake']
        mac = ','.join(f'0x{part}' for part in wake['mac'].split('-'))
        targets = ','.join(f"'{address}'" for address in wake['broadcasts'])
        script = (f"$mac = [byte[]]({mac}); $packet = [byte[]](,0xFF * 6) + ($mac * 16); "
                  "$udp = New-Object Net.Sockets.UdpClient; $udp.EnableBroadcast = $true; "
                  f"foreach ($t in {targets}) {{ foreach ($p in 9, 7) {{ [void]$udp.Send($packet, $packet.Length, $t, $p) }} }}; "
                  "$udp.Close(); 'woke'")
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        relay = wake['relay']
        command = [relay['client'], '-i', relay['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', f"UserKnownHostsFile={relay['known_hosts']}", '-o', 'ConnectTimeout=10',
                   f"{relay['user']}@{relay['host']}", f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"]
        try:
            done = (run or self.run)(command, capture_output=True, timeout=40,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            return None
        lines = done.stdout.decode('utf-8', 'replace').strip().splitlines() if done.returncode == 0 else []
        return 'woke' if lines and lines[-1].strip() == 'woke' else None

    def act(self, machine_id, action):
        """Runs start, stop or wake on the machine; the result key, or None when it cannot be reached."""
        if action == 'wake':
            return self.wake(machine_id)
        if machine_id in self.drivers:
            return self.drivers[machine_id].act(action)
        poller = self.machines[machine_id]
        try:
            done = self.run(poller.command(self.remote_script(poller.cfg, action)), capture_output=True,
                            timeout=poller.cfg['timeout_seconds'] + 20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            return None
        lines = done.stdout.decode('utf-8', 'replace').strip().splitlines() if done.returncode == 0 else []
        return lines[-1].strip() if lines else None

    @staticmethod
    def remote_script(cfg, action):
        """PowerShell for the machine, sent encoded so no quoting passes through ssh and cmd."""
        if action == 'stop':
            script = ("if (Get-Process krig-miner,SRBMiner-MULTI -ErrorAction SilentlyContinue) { "
                      f"New-Item -ItemType File -Force -Path '{cfg['stop_path']}' | Out-Null; 'stop-requested' }} "
                      "else { 'not-running' }")
        else:
            guard = cfg['guard_path']
            # Appended, so an earlier run's stop reason is still there after the next start.
            launch = (f"cmd.exe /c powershell -NoProfile -ExecutionPolicy Bypass -File {guard} "
                      f">> {cfg['guard_console']} 2>&1")
            folder = guard.rsplit('\\', 1)[0]  # Windows path on the far machine
            script = ("if (Get-Process krig-miner,SRBMiner-MULTI -ErrorAction SilentlyContinue) { 'already-running' } else { "
                      "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
                      f"@{{CommandLine='{launch}'; CurrentDirectory='{folder}'}}; "
                      "if ($r.ReturnValue -eq 0) { 'started' } else { 'start-failed' } }")
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"

    def handle(self, machine_id, action, token):
        """(HTTP status, JSON body) for one control request."""
        if not self.token:
            return 503, {'ok': False, 'error': 'control-disabled', 'message': '電腦端未開啟控制功能'}
        now = self.clock()
        with self.lock:
            if now < self.locked_until:
                return 429, {'ok': False, 'error': 'locked', 'message': '密碼錯誤太多次，控制已暫停 10 分鐘'}
            if not hmac.compare_digest((token or '').encode('utf-8'), self.token.encode('utf-8')):
                self.failures = [t for t in self.failures if now - t < self.LOCK] + [now]
                if len(self.failures) >= self.MAX_FAILURES:
                    self.locked_until, self.failures = now + self.LOCK, []
                    logging.warning('control locked after repeated wrong tokens')
                return 401, {'ok': False, 'error': 'unauthorized', 'message': '控制密碼不正確'}
            known = action in ('start', 'stop') or (action == 'wake' and self.machines.get(machine_id) is not None
                                                     and self.machines[machine_id].cfg.get('wake'))
            if not known or machine_id not in self.machines:
                return 404, {'ok': False, 'error': 'not-found', 'message': '這台機器不能從手機控制'}
            if now - self.last_action.get(machine_id, -1e9) < self.COOLDOWN:
                return 429, {'ok': False, 'error': 'busy', 'message': '剛剛才送過指令，請稍等幾秒'}
            self.last_action[machine_id] = now
        result = self.act(machine_id, action)
        if result not in self.MESSAGES:
            logging.warning('control %s %s: no answer from the machine', action, machine_id)
            text = '代發開機指令的電腦連不到' if action == 'wake' else '連不到這台機器'
            return 502, {'ok': False, 'error': 'unreachable', 'message': text}
        logging.warning('control %s %s: %s', action, machine_id, result)
        # No ntfy for these: the person who pressed the button already sees the answer in the app
        # (2026-10-28, too many notifications). The log above keeps every action.
        if result in ('started', 'stop-requested', 'stopped', 'woke') and self.on_manual:
            self.on_manual(machine_id)
        if action == 'stop' and result in ('stop-requested', 'stopped', 'not-running'):
            self.stopped_by_phone.add(machine_id)
        elif action == 'start' and result in ('started', 'already-running'):
            self.stopped_by_phone.discard(machine_id)
        return 200, {'ok': result not in ('start-failed', 'start-refused', 'stop-failed'), 'result': result,
                     'message': self.MESSAGES[result]}


IDLE_FIELD = re.compile(r'^(?:(\d+)\+)?(?:(\d+):)?(\d+)$')


def user_idle_minutes(text):
    """Shortest idle time, in minutes, of the signed-in sessions in `query user` output; None when
    nobody is signed in. Disconnected sessions do not count. Idle reads ".", "none" or "無" when
    in use, minutes, "h:mm", or "d+h:mm"."""
    best = None
    for line in text.splitlines()[1:]:
        fields = line.lstrip('>').split()
        if len(fields) < 5 or any(word in line for word in ('Disc', '中斷')):
            continue
        # USERNAME [SESSIONNAME] ID STATE IDLE LOGON-TIME...: the idle field follows the state.
        ids = [i for i, field in enumerate(fields[:3]) if field.isdigit()]
        if not ids or ids[0] + 2 >= len(fields):
            continue
        idle = fields[ids[0] + 2]
        if idle in ('.', 'none', '無'):
            minutes = 0
        else:
            match = IDLE_FIELD.match(idle)
            if not match:
                continue
            days, hours, mins = (int(part) if part else 0 for part in match.groups())
            minutes = (days * 24 + hours) * 60 + mins
        best = minutes if best is None else min(best, minutes)
    return best


class AutoMiner:
    """Mines a billed, wake-capable machine only while it pays at the current electricity price.

    Every minute: this hour's estimated income at the machine's measured hashrate, minus its mining
    and idle power at the current TOU price (the alternative is switching it off). Worth it: wake the
    machine if it is off and start the miner. Not worth it: stop the miner, and switch the machine
    off when this automation switched it on or started its miner. Nobody's work is interrupted: while someone used the
    machine in the last `user_idle_minutes`, it neither starts mining nor shuts down. A start, stop
    or wake from the phone pauses the automation until the next price change. Each action is
    logged and sent to ntfy at low priority; the advice row in the app shows the current decision.
    """

    def __init__(self, cfg, controller, earnings, tariff, advisor=None, notify=None, path=None, clock=None):
        self.settings = cfg.get('auto_mine') or {}
        self.profiles = (cfg.get('advice') or {}).get('machines') or {}
        self.controller, self.earnings, self.tariff = controller, earnings, tariff
        self.advisor, self.notify = advisor, notify
        self.clock = clock or (lambda: datetime.now(UTC))
        self.path = Path(path or ROOT / 'auto-state.json')
        try:
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.state = {}
        self.lock = threading.Lock()

    def save(self):
        try:
            self.path.write_text(json.dumps(self.state), encoding='utf-8')
        except OSError:
            logging.exception('Auto-mine state not saved')

    def entry(self, machine_id):
        return self.state.setdefault(machine_id, {'owned': False, 'override_until': None, 'last_try': {},
                                                   'wake_failures': 0})

    def override(self, machine_id, now=None):
        """After a manual action: leave the machine alone until the price next changes."""
        now = now or self.clock()
        with self.lock:
            entry = self.entry(machine_id)
            entry['override_until'] = next_price_change(now).isoformat()
            entry['owned'] = False
            self.save()

    def hourly_net(self, profile, now):
        result = self.earnings.latest() if self.earnings is not None else None
        market = (result or {}).get('market') or {}
        per_th, coin, twd = (result or {}).get('per_th_day'), market.get('coinUsd'), market.get('usdTwd')
        tariff = self.tariff.latest() if self.tariff is not None else None
        price = tou_price(tariff, now)
        if None in (per_th, coin, twd, price):
            return None, price
        income = profile['hashrate_th'] * per_th * coin * twd / 24
        return income - profile_watts(profile, idle=True) / 1000 * price, price

    def idle_minutes(self, poller):
        """Minutes since anyone used the machine; None when nobody is signed in or it cannot be asked."""
        script = "query user 2>$null | Out-String -Width 200; 'end'"
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        try:
            done = self.controller.run(poller.command(f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"),
                                       capture_output=True, timeout=poller.cfg['timeout_seconds'] + 20,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            return None
        return user_idle_minutes(done.stdout.decode('cp950', 'replace')) if done.returncode == 0 else None

    def tell(self, title, text, priority=2, tags=('robot',)):
        logging.warning('auto-mine: %s %s', title, text)
        # Only warnings reach the phone (the user has to do something); what the automation did on
        # its own is in the log and in the app (2026-10-28, too many notifications).
        if self.notify and 'warning' in tags:
            try:
                self.notify(title, text, priority, list(tags))
            except OSError:
                logging.exception('auto-mine notification failed')

    def due(self, entry, action, now, seconds):
        last = entry['last_try'].get(action)
        if last and (now - datetime.fromisoformat(last)).total_seconds() < seconds:
            return False
        entry['last_try'][action] = now.isoformat()
        return True

    def step_machine(self, machine_id, settings, now):
        profile = self.profiles.get(machine_id)
        poller = self.controller.machines.get(machine_id)
        if profile is None or poller is None:
            return None
        name = profile['display_name']
        net, price = self.hourly_net(profile, now)
        entry = self.entry(machine_id)
        period = next_price_change(now).isoformat()
        if entry.get('period') != period:  # A new price period: wake-ups get a fresh set of tries.
            entry['period'], entry['wake_failures'] = period, 0
        if net is None:
            return 'rest' if not poller.last_running else 'mine', '收益或電價資料不足，暫時維持現狀'
        off_day = is_off_day(self.tariff.latest(), now)
        kind = '假日離峰' if off_day else '離峰' if price <= min_price(self.tariff.latest(), now) else '尖峰'
        money = f'{kind} {price:.2f} 元／度，每小時{"淨賺" if net >= 0 else "約虧"} NT${abs(net):.2f}'
        # Two thresholds and a minimum time in each state: at peak the net hovered around a single
        # threshold and the laptop was woken and shut down twice in a morning (2026-10-27).
        start_at = settings.get('start_net_per_hour', settings.get('min_net_per_hour', 0.05))
        stop_below = settings.get('stop_net_per_hour', 0.0)
        # Weekends and national holidays are off-peak all day: mine then on a lower threshold
        # (2026-11-01). A run started that way carries on through the off-peak hours that follow
        # (Monday 00–06 costs the same as Sunday) and ends at the first peak price.
        # Weekday nights (`night_hours`, 00–06) are off-peak too: mine on `night_start_net_per_hour`
        # (2026-11-10, the user wanted the laptop to use them). Such a run, like an off-day one, ends
        # at the first peak price instead of carrying on while it still pays a little.
        night = (not off_day and kind == '離峰' and 'night_start_net_per_hour' in settings
                 and in_hours(settings.get('night_hours', [0, 6]), now))
        low_run = off_day or night
        if off_day:
            start_at = settings.get('off_day_start_net_per_hour', start_at)
        elif night:
            start_at = settings['night_start_net_per_hour']
        elif entry.get('active') and entry.get('off_day_run') and kind == '尖峰':
            stop_below = start_at
        active = entry.get('active', False) or poller.last_running is True
        want = net >= (stop_below if active else start_at)
        changed = entry.get('changed_at')
        if (changed and want != active
                and (now - datetime.fromisoformat(changed)).total_seconds() < settings.get('min_state_minutes', 60) * 60):
            want = active
        override = entry.get('override_until')
        if override and now < datetime.fromisoformat(override):
            until = datetime.fromisoformat(override).astimezone(TAIPEI).strftime('%H:%M')
            return ('mine' if want else 'rest'), f'{money}；剛剛手動操作過，自動化暫停到 {until}'
        entry['override_until'] = None
        latest = poller.latest()
        reachable = latest is not None and latest[1] == 'ok'
        running = reachable and poller.last_running is True
        busy_after = settings.get('user_idle_minutes', 15)
        if want:
            if poller.powered_off():
                if entry['wake_failures'] >= 3:
                    return 'mine', f'{money}；叫不醒（可能沒插電或網路線），下次換時段再試'
                if self.due(entry, 'wake', now, 600):
                    if self.controller.wake(machine_id) == 'woke':
                        entry['owned'] = True
                        if not entry.get('active'):
                            entry['active'], entry['changed_at'], entry['off_day_run'] = True, now.isoformat(), low_run
                        self.tell(f'⚡ 自動開機挖礦：{name}', money)
                    entry['wake_failures'] += 1
                    if entry['wake_failures'] >= 3:
                        self.tell(f'⚠️ 自動開機失敗：{name}', '連試 3 次都沒開機，請確認插著電源與網路線', 3, ('warning',))
                return 'mine', f'{money}；自動開機中'
            entry['wake_failures'] = 0
            if reachable and not running:
                idle = self.idle_minutes(poller)
                if idle is not None and idle < busy_after:
                    return 'mine', f'{money}；有人正在使用筆電，暫不自動開挖'
                if self.due(entry, 'start', now, 300):
                    if self.controller.act(machine_id, 'start') == 'started':
                        # Nobody was using it: this run is the automation's, and so is the shutdown
                        # when it stops (2026-11-01, the user wants the laptop off after mining).
                        entry['owned'] = True
                        if not entry.get('active'):
                            entry['active'], entry['changed_at'], entry['off_day_run'] = True, now.isoformat(), low_run
                        self.tell(f'▶️ 自動開始挖礦：{name}', money)
                return 'mine', f'{money}；自動開始挖礦'
            return 'mine', f'{money}；自動挖礦中'
        if running:
            if self.due(entry, 'stop', now, 300) and self.controller.act(machine_id, 'stop') == 'stop-requested':
                entry['active'], entry['changed_at'] = False, now.isoformat()
                self.tell(f'⏸️ 自動停挖：{name}', money)
            return 'rest', f'{money}；自動停挖'
        # `shutdown_when_idle` (2026-11-10): also switch off a machine the automation did not switch on,
        # once nobody has touched it for `idle_shutdown_minutes`. A hand-made stop/start on 11/08 cleared
        # `owned`, and the laptop then sat on, not mining, from 11/09 06:00 for a day and a half.
        owned = entry.get('owned')
        if reachable and (owned or settings.get('shutdown_when_idle')):
            idle = self.idle_minutes(poller)
            if idle is not None and idle < busy_after:
                entry['owned'] = False  # Someone is using it now: it is theirs, never shut it down.
                return 'rest', f'{money}；有人正在使用筆電，不自動關機'
            wait = settings.get('idle_shutdown_minutes', 30)
            if not owned and idle is not None and idle < wait:
                return 'rest', f'{money}；開著沒在挖，沒人用滿 {wait} 分鐘就關機'
            if self.due(entry, 'shutdown', now, 600):
                answer = poller.remote("shutdown /s /t 60 /c 'Auto mining: price too high, shutting down.'; 'shutting-down'")
                if answer == 'shutting-down':
                    entry['owned'] = False
                    self.tell(f'💤 自動關機：{name}', money)
            return 'rest', f'{money}；自動停挖關機'
        if entry.get('active'):
            entry['active'], entry['changed_at'] = False, now.isoformat()
        rule = f'未達開挖門檻（每小時淨賺 NT${start_at:.2f}）'
        return 'rest', f'{money}；{rule}，維持關機' if poller.powered_off() else f'{money}；{rule}，不挖'

    def next_step(self, machine_id, decision, now):
        """When the automation expects to act next, for the advice row (asked 11/01): mining runs to
        the first weekday peak; resting waits for the next weekend or holiday. An estimate: the
        market can move either way."""
        tariff = self.tariff.latest() if self.tariff is not None else None
        if not tariff:
            return None
        if decision == 'mine':
            when = first_weekday_peak(tariff, now)
            owned = self.entry(machine_id).get('owned')
            return f'預計 {when_text(when, now)} 停挖{"關機" if owned else ""}' if when else None
        if is_off_day(tariff, now):
            return None  # Resting on an off day: it does not pay even now.
        when = next_off_day(tariff, now)
        settings = (self.settings.get('machines') or {}).get(machine_id) or {}
        if 'night_start_net_per_hour' in settings:
            night = next_night(settings.get('night_hours', [0, 6]), now)
            when = min(night, when) if when else night
        poller = self.controller.machines.get(machine_id)
        start = '自動開機挖' if poller is not None and poller.powered_off() else '自動開挖'
        return f'下次{start}：{when_text(when, now)}' if when else None

    def power_label(self, machine_id):
        """Whether the machine is on, for the advice row in the app and the AI panel (2026-11-01)."""
        poller = self.controller.machines.get(machine_id)
        latest = poller.latest() if poller is not None else None
        if poller is None or latest is None:
            return '狀態未知'
        if poller.powered_off():
            return '關機中'
        if latest[1] != 'ok':
            return '連不到'
        return '開機・挖礦中' if poller.last_running is True else '開機・沒在挖'

    def step(self, now=None):
        now = now or self.clock()
        with self.lock:
            for machine_id, settings in (self.settings.get('machines') or {}).items():
                try:
                    decision = self.step_machine(machine_id, settings, now)
                except Exception:
                    logging.exception('auto-mine step failed for %s', machine_id)
                    continue
                if decision and self.advisor is not None:
                    reason = f'{self.power_label(machine_id)}｜{decision[1]}'
                    plan = self.next_step(machine_id, decision[0], now)
                    self.advisor.set_auto(machine_id, decision[0], f'{reason}；{plan}' if plan else reason, now)
            self.save()

    def run_forever(self):
        time.sleep(90)  # Let the pollers and the earnings poller have their first answers.
        while True:
            self.step()
            time.sleep(self.settings.get('check_seconds', 60))


class Keeper:
    """Starts mining again on a machine that stopped without anyone asking (`keep_mining`).

    User, 2026-11-01: every machine should get back to mining on its own. A machine in the list that
    is reachable, stopped, and not stopped by a person (the phone, the console, a STOP file: all
    read as 'userRequested') gets the same start the phone sends, after waiting `delays_seconds`
    (longer after each quick failure; 30 minutes of mining resets it). A GPU reading taken in the
    last two minutes must be below `cool_below_c`; with no fresh reading each guard's own start check
    refuses a hot card. After `warn_after` restarts in a row that did not last, one warning goes to
    the phone. Machines with their own keeper (the RTX 5080) or price automation (the laptop) stay
    out of the list."""

    def __init__(self, cfg, aggregator, notify=None, clock=time.monotonic):
        settings = cfg.get('keep_mining') or {}
        self.machines = settings.get('machines') or []
        self.cool_below = settings.get('cool_below_c', 60)
        self.delays = settings.get('delays_seconds') or [60, 300, 900, 1800]
        self.warn_after = settings.get('warn_after', 3)
        self.aggregator, self.notify, self.clock = aggregator, notify, clock
        self.entries = {}

    def step(self, now=None):
        now = now or datetime.now(UTC)
        tick = self.clock()
        controller = getattr(self.aggregator, 'controller', None)
        if controller is None:
            return []
        started = []
        for machine in self.aggregator.feed(now=now)['machines']:
            machine_id = machine['machineId']
            if machine_id not in self.machines or not controller.can_control(machine_id):
                continue
            entry = self.entries.setdefault(machine_id, {'failures': 0, 'stopped_at': None, 'running_at': None,
                                                         'warned': False})
            state = (machine.get('minerRunning') or {}).get('state')
            if state == 'running':
                entry['stopped_at'] = None
                entry['running_at'] = entry['running_at'] or tick
                if tick - entry['running_at'] >= 1800:
                    entry['failures'], entry['warned'] = 0, False
                continue
            entry['running_at'] = None
            reason = (machine.get('stop') or {}).get('reason')
            if machine.get('sourceStatus') != 'ok' or state != 'stopped' or reason == 'userRequested':
                entry['stopped_at'] = None
                if reason == 'userRequested':
                    entry['failures'], entry['warned'] = 0, False
                continue
            entry['stopped_at'] = entry['stopped_at'] or tick
            wait = self.delays[min(entry['failures'], len(self.delays) - 1)]
            if tick - entry['stopped_at'] < wait:
                continue
            temps = [row.get('temperatureC') for row in machine.get('gpus') or []
                     if row.get('temperatureC') is not None and parse_time(row.get('observedAt'))
                     and (now - parse_time(row['observedAt'])).total_seconds() <= 120]
            if temps and max(temps) >= self.cool_below:
                continue
            result = controller.act(machine_id, 'start')
            entry['failures'] += 1
            entry['stopped_at'] = tick  # the next try waits again, whatever this one did
            logging.warning('keep-mining: %s stopped (%s); start -> %s (try %d)', machine_id, reason, result,
                            entry['failures'])
            started.append((machine_id, result))
            if entry['failures'] >= self.warn_after and not entry['warned'] and self.notify:
                entry['warned'] = True
                try:
                    self.notify(f"⚠️ {machine['displayName']} 一直無法恢復挖礦",
                                f"已自動重開 {entry['failures']} 次都沒有撐過 30 分鐘，請到電腦上看看", 4, ['warning'])
                except OSError:
                    logging.exception('keep-mining notification failed')
        return started

    def run_forever(self):
        time.sleep(120)  # Let every poller have its first answer.
        while True:
            try:
                self.step()
            except Exception:
                logging.exception('keep-mining step failed')
            time.sleep(30)


def first_weekday_peak(tariff, now):
    """The first hour from `now` priced at peak, on a day that is not a weekend or holiday."""
    hour = now.astimezone(TAIPEI).replace(minute=0, second=0, microsecond=0)
    for step in range(1, 24 * 15):
        when = hour + timedelta(hours=step)
        if not is_off_day(tariff, when) and tou_price(tariff, when) > min_price(tariff, when):
            return when
    return None


def next_off_day(tariff, now):
    """00:00 of the next weekend day or holiday after today."""
    day = now.astimezone(TAIPEI).replace(hour=0, minute=0, second=0, microsecond=0)
    for step in range(1, 60):
        when = day + timedelta(days=step)
        if is_off_day(tariff, when):
            return when
    return None


def in_hours(hours, now):
    """Whether the Taipei hour of `now` is in [start, end)."""
    start, end = hours
    return start <= now.astimezone(TAIPEI).hour < end


def next_night(hours, now):
    """The next start of the weekday-night window after the current hour (the window may be on now,
    but then the automation already had its chance and is resting for a reason)."""
    day = now.astimezone(TAIPEI).replace(hour=0, minute=0, second=0, microsecond=0)
    when = day + timedelta(hours=hours[0])
    return when if when > now else when + timedelta(days=1)


def when_text(when, now):
    """'今天 06:00', '明天 00:00' or '11/7（六）00:00', in Taipei time."""
    local, today = when.astimezone(TAIPEI), now.astimezone(TAIPEI).date()
    days = (local.date() - today).days
    if days in (0, 1):
        return f"{'今天' if days == 0 else '明天'} {local:%H:%M}"
    return f"{local.month}/{local.day}（{'一二三四五六日'[local.weekday()]}）{local:%H:%M}"


def min_price(tariff, now):
    """The off-peak price of the current season."""
    summer = 6 <= now.astimezone(TAIPEI).month <= 9
    return number((tariff or {}).get('summerOffPeak' if summer else 'nonSummerOffPeak'), 0) or 0


def next_price_change(now):
    """The next hour at which the simple two-stage TOU price may change (06, 09, 11, 14 or 00 local)."""
    local = now.astimezone(TAIPEI)
    for hour in (6, 9, 11, 14, 24):
        if local.hour < hour:
            break
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (day + timedelta(hours=hour)).astimezone(UTC)


def control_for_feed(machine):
    """Which control buttons make sense now, from the machine's reported miner state."""
    state = (machine.get('minerRunning') or {}).get('state')
    return {'canStart': state != 'running', 'canStop': state != 'stopped'}


def handler_for(aggregator, networks):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'MinerStatusAggregator'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass

        def send_json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            try:
                peer = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            return any(peer in network for network in networks)

        def do_GET(self):
            if not self.allowed():
                return self.send_json(403, {'error': 'forbidden'})
            path = self.path.split('?', 1)[0]
            if path == '/status':
                features = {item.strip() for item in (self.headers.get('X-Miner-Status-Features') or '').split(',')}
                try:
                    return self.send_json(200, aggregator.feed(include_earnings='earnings' in features,
                                                               include_assessment='assessment' in features,
                                                               include_control='control' in features,
                                                               include_advice='advice' in features,
                                                               include_wake='wake' in features,
                                                               include_coins='coins' in features,
                                                               include_cumulative='cumulative' in features,
                                                               include_balance='balance' in features,
                                                               include_today='today' in features))
                except Exception:
                    logging.exception('Feed failed')
                    return self.send_json(500, {'error': 'internal'})
            if path == '/health':
                return self.send_json(200, {'ok': True})
            if path == '/nodes':
                watcher = getattr(aggregator, 'node_watcher', None)
                return self.send_json(200, watcher.snapshot() if watcher else {})
            return self.send_json(404, {'error': 'not found'})

        def denied(self):
            self.send_json(405, {'error': 'method not allowed'})

        def do_POST(self):
            """Only /control/<machine>/<start|stop|wake>, with `Authorization: Bearer <token>`."""
            if not self.allowed():
                return self.send_json(403, {'error': 'forbidden'})
            parts = self.path.split('?', 1)[0].strip('/').split('/')
            controller = getattr(aggregator, 'controller', None)
            if len(parts) != 3 or parts[0] != 'control' or controller is None:
                return self.denied()
            try:
                length = min(int(self.headers.get('Content-Length') or 0), 1024)
            except ValueError:
                length = 0
            if length:
                self.rfile.read(length)  # No body is used; drain a small one.
            auth = self.headers.get('Authorization') or ''
            code, body = controller.handle(parts[1], parts[2], auth[7:] if auth.startswith('Bearer ') else '')
            return self.send_json(code, body)

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = denied

    return Handler


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    networks = [ipaddress.ip_network(item) for item in cfg['allowed_networks']]
    bind = ipaddress.ip_address(cfg['bind_ip'])
    if bind.is_unspecified or not any(bind in network for network in networks):
        raise RuntimeError('bind_ip must be a specific address inside allowed_networks (no 0.0.0.0)')
    return cfg, networks


def main():
    import sys
    cfg, networks = load_config(ROOT / 'aggregator.json')
    if '--test-notify' in sys.argv:
        Notifier(cfg).post('✅ 礦機通知測試', '電腦端通知設定成功：之後停止、失聯或太久沒份額時會通知你。', 3, ['white_check_mark'])
        print('Test notification sent.')
        return
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(ROOT / 'aggregator.log', maxBytes=1048576, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S'))
    logger.addHandler(handler)
    poller = RtxPoller(cfg)
    threading.Thread(target=poller.run, daemon=True).start()
    earnings = None
    routes = KryptexRoutes(relay=cfg.get('kryptex_relay'))  # shared: both coins come from the same address
    if cfg.get('earnings', {}).get('account'):
        earnings = EarningsPoller(cfg, fetch=routes.get_json)
        threading.Thread(target=earnings.run, daemon=True).start()
    notify = Notifier(cfg).post if cfg.get('notify', {}).get('topic') else None
    tariff = TariffPoller(cfg, notify=notify)
    threading.Thread(target=tariff.run, daemon=True).start()
    extra = {}
    for section in ('laptop', 'office2'):  # guard status files read over SSH (laptop_miner.ps1)
        if section in cfg:
            extra[section] = SshStatusPoller(cfg, section, notify)
            threading.Thread(target=extra[section].run, daemon=True).start()
    for section in FPGA_SECTIONS:
        if section in cfg:
            extra[section] = RelayLogPoller(cfg, section)
            threading.Thread(target=extra[section].run, daemon=True).start()
    aggregator = Aggregator(cfg, poller, earnings, tariff, extra)
    if earnings is not None and 'earnings_qtc' in cfg:
        aggregator.coins['earnings_qtc'] = EarningsPoller(cfg, fetch=routes.get_json, section='earnings_qtc')
        threading.Thread(target=aggregator.coins['earnings_qtc'].run, daemon=True).start()
    drivers = {}
    if cfg['rtx'].get('control'):
        drivers[cfg['rtx']['machine_id']] = CommandControl(cfg, 'rtx', rtx_control_commands(cfg['rtx']['control']))
    if cfg['office'].get('control'):
        drivers[cfg['office']['machine_id']] = OfficeControl(cfg)
    for section in FPGA_SECTIONS:
        if (cfg.get(section) or {}).get('control_command'):  # the FPGA's own script: <command> start|stop
            command = cfg[section]['control_command']
            # Its start runs a self-test first (about 21 s; a KV260 just powered up is JTAG-loaded before
            # that), so it runs detached and answers within the app's 30 s whatever it takes.
            drivers[cfg[section]['machine_id']] = DetachedCommandControl(cfg, section, {action: command + [action] for action in ('start', 'stop')})
    aggregator.controller = Controller(cfg, extra, notify, drivers=drivers)
    if cfg.get('node_watch'):
        aggregator.node_watcher = NodeWatcher(cfg, notify)
        threading.Thread(target=aggregator.node_watcher.run_forever, daemon=True).start()
    if notify:
        threading.Thread(target=Notifier(cfg).run, args=(aggregator,), daemon=True).start()
    if (cfg.get('advice') or {}).get('machines'):
        aggregator.advisor = Advisor(cfg, earnings, tariff, notify)
        threading.Thread(target=aggregator.advisor.run_forever, daemon=True).start()
    if (cfg.get('auto_mine') or {}).get('machines'):
        auto = AutoMiner(cfg, aggregator.controller, earnings, tariff, getattr(aggregator, 'advisor', None), notify)
        aggregator.controller.on_manual = auto.override
        threading.Thread(target=auto.run_forever, daemon=True).start()
    if (cfg.get('keep_mining') or {}).get('machines'):
        threading.Thread(target=Keeper(cfg, aggregator, notify).run_forever, daemon=True).start()
    if (cfg.get('profit_log') or {}).get('machines'):
        threading.Thread(target=ProfitLog(cfg, aggregator).run_forever, daemon=True).start()
    if earnings is not None:
        aggregator.ledger = EarningsLedger(aggregator)
        threading.Thread(target=aggregator.ledger.run_forever, daemon=True).start()
    while True:
        try:
            server = ThreadingHTTPServer((cfg['bind_ip'], cfg['port']), handler_for(aggregator, networks))
            break
        except OSError as exc:
            if getattr(exc, 'winerror', None) != 10049:
                raise
            time.sleep(10)  # Wait for ZeroTier; never fall back to another interface.
    logging.info('Read-only aggregator listening on the configured ZeroTier address')
    if sys.stdout:  # None under pythonw (started hidden by the AI console or the desktop launcher)
        print(f"Listening on {cfg['bind_ip']}:{cfg['port']}.")
    server.daemon_threads = True
    server.serve_forever(poll_interval=1)


if __name__ == '__main__':
    main()

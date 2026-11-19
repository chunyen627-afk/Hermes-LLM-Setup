"""One visible control window; all service descendants belong to a kill-on-close job."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import msvcrt
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import ttk
import webbrowser
import requests

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'media-control-state.json'
REQUEST = ROOT / 'media-control-request.json'
MINING = ROOT.parent / 'Pearl-mining'
PANEL_TASK = 'PearlPanelBridge'  # runs Pearl-mining\panel_task.py with highest privileges

STATUS_PYTHONW = Path(r'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\pythonw.exe')
STATUS_SCRIPT = Path(r'C:\Users\pjunm\dev\miner-status-ios\pc-service\office_aggregator.py')
STATUS_URL = 'http://10.35.219.64:18786/status'
RIG_COLORS = {'green': ('#15803d', '綠'), 'yellow': ('#b45309', '黃'), 'red': ('#b42318', '紅'), 'gray': ('#6b7280', '灰')}
MESSAGE_KEEP_S = 600  # a button's answer stays 10 minutes, then the line clears (11/13)
FPGA_RIGS = ('vcu118', 'zcu102', 'u250')  # shown on the FPGA tab (11/04); every other rig is a GPU. KV260 retired 11/05.

NODE_NAMES = {'prl-hk.kryptex.network': '香港', 'prl-sg.kryptex.network': '新加坡', 'prl.kryptex.network': '德國'}
# Whole-machine draw while mining, billed instead of the GPU figure (the user wants the whole PC, 2026-11-10).
# rtx5080: power-meter reading (360-400 W). laptop3070: estimate, not measured: GPU 61 W + 10 W through a 0.88
# adapter, the same figure the mining advice uses.
WALL_WATTS = {'rtx5080': 380, 'laptop3070': 81}
QTC_PYTHON = Path(r'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe')
QTC_HOST = Path(r'C:\Users\pjunm\dev\qtc-fpga\host')  # mining_ctl.py starts/stops the VCU118 relay
TAIPEI = timezone(timedelta(hours=8))

def tariff_now(tariff, now):
    """(尖峰/離峰, NT$/kWh now, 'HH:MM 起改為…' or '', today's average NT$/kWh hour by hour) for
    Taipower's simple two-stage TOU, the same rules as the phone app; None without a tariff."""
    if not tariff:
        return None
    summer = 6 <= now.month <= 9
    peak_rate = tariff['summerPeak' if summer else 'nonSummerPeak']
    off_rate = tariff['summerOffPeak' if summer else 'nonSummerOffPeak']
    off_days = set(tariff.get('offPeakDates') or [])

    def is_peak(moment):
        if moment.weekday() >= 5 or moment.strftime('%Y-%m-%d') in off_days:
            return False
        return moment.hour >= 9 if summer else (6 <= moment.hour < 11 or moment.hour >= 14)

    peak = is_peak(now)
    following = ''
    moment = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(72):
        moment += timedelta(hours=1)
        if is_peak(moment) != peak:
            day = '' if moment.date() == now.date() else ('明天 ' if moment.date() == (now + timedelta(days=1)).date() else moment.strftime('%m/%d '))
            following = f"{day}{moment:%H:%M} 起改為{'尖峰' if not peak else '離峰'} {peak_rate if not peak else off_rate:.2f} 元"
            break
    # A day's electricity is priced on today's own hours, as the phone app does (a weekend is all off-peak).
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = sum(peak_rate if is_peak(midnight + timedelta(hours=hour)) else off_rate for hour in range(24)) / 24
    hourly = [peak_rate if is_peak(midnight + timedelta(hours=hour)) else off_rate for hour in range(24)]
    return ('尖峰' if peak else '離峰', peak_rate if peak else off_rate, following, today, hourly)

def today_actual(record, tariff, billed):
    """(NT$ earned, NT$ electricity) actually since 00:00 from the service's `today` record (2026-11-12:
    the laptop mines only 00-06, so the current rate times 24 h was ~6x off). Each hour it mined is priced
    at that hour's rate, on the whole-machine draw where known, else the GPU watt-hours; only billed
    machines pay. None without a record or tariff."""
    if not record or not tariff:
        return None
    revenue = electricity = 0.0
    for machine in record.get('machines') or []:
        revenue += machine.get('twd') or 0
        if machine.get('machineId') not in billed:
            continue
        wall = WALL_WATTS.get(machine.get('machineId'))
        for hour in machine.get('hours') or []:
            wh = wall * hour['miningSeconds'] / 3600 if wall else hour['gpuWh']
            electricity += wh / 1000 * tariff[4][hour['hour']]
    return revenue, electricity

def ago_text(stamp, now):
    try:
        seconds = (now - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return ''
    if seconds < 90:
        return f'{max(0, int(seconds))} 秒前'
    if seconds < 5400:
        return f'{int(seconds // 60)} 分鐘前'
    return f'{seconds / 3600:.1f} 小時前'

def rig_summary():
    """Everything the phone app shows, as display lines, from the status service; None when it does
    not answer. {'rigs': [(light, line, detail)], 'tariff': str, 'total': str, 'advice': [(mine, line)]}"""
    try:
        feed = requests.get(STATUS_URL, headers={'X-Miner-Status-Features': 'earnings,assessment,advice,coins,cumulative,wake,balance,today'}, timeout=4).json()
    except Exception:
        return None
    try:
        nodes = requests.get(STATUS_URL.rsplit('/', 1)[0] + '/nodes', timeout=4).json()
    except Exception:
        nodes = {}
    now = datetime.now(TAIPEI)
    market = feed.get('market') or {}
    rate = (market.get('coinUsd') or 0) * (market.get('usdTwd') or 0)
    tariff = tariff_now(feed.get('tariff'), now)
    rigs, revenue, electricity = [], 0.0, 0.0
    for m in feed.get('machines', []):
        verdict, health, earn = m.get('assessment') or {}, m.get('health') or {}, m.get('earnings') or {}
        parts = [m.get('displayName', '?'), verdict.get('title', '')]
        node = NODE_NAMES.get((nodes.get(m.get('machineId')) or {}).get('current'))
        if node:
            parts.append(node)
        hashrate = m.get('hashrate') or {}
        if m.get('minerRunning', {}).get('state') == 'running' and hashrate.get('value') is not None:
            parts.append(f"{hashrate['value']:.1f} {hashrate.get('unit') or ''}")
        temps = [g['temperatureC'] for g in m.get('gpus') or [] if g.get('temperatureC') is not None]
        watts = sum(g.get('powerW') or 0 for g in m.get('gpus') or [])
        if temps:
            parts.append(f'{max(temps):.0f}°C')
        if watts:
            parts.append(f'{watts:.0f} W')
        money = ''
        # A machine on another coin (the FPGA on QTC) carries its own price; the rest use the feed's market.
        own = (earn.get('coinUsd') or 0) * (market.get('usdTwd') or 0) if earn.get('coin') else rate
        if earn.get('coinPerDay') is not None and own:
            day = earn['coinPerDay'] * own
            revenue += day
            money = f'收益約 NT${day:.0f}/天' + (f'（{earn["coin"]}）' if earn.get('coin') else '')
            if earn.get('electricityBilled') and tariff and watts:
                cost = WALL_WATTS.get(m.get('machineId'), watts) * 24 / 1000 * tariff[3]
                electricity += cost
                money += f'・電費 NT${cost:.0f}・淨 NT${day - cost:.0f}'
            elif earn.get('electricityBilled') is False:
                money += '・電費不計'
        detail = '　' + verdict.get('reason', '')
        if money:
            detail += f'　｜　{money}'
        rigs.append({'id': m.get('machineId'), 'name': m.get('displayName', '?'), 'light': verdict.get('light', 'gray'), 'line': '・'.join(p for p in parts if p),
                     'detail': detail, 'running': (m.get('minerRunning') or {}).get('state') == 'running'})
    tariff_line = ''
    if tariff:
        tariff_line = f'今日電價：現在{tariff[0]} {tariff[1]:.2f} 元／度' + (f'，{tariff[2]}' if tariff[2] else '')
    total = ''
    # Billed machines, including one switched off now (only billed machines get mine-or-rest advice).
    billed = {m.get('machineId') for m in feed.get('machines', []) if (m.get('earnings') or {}).get('electricityBilled')}
    billed |= {item.get('machineId') for item in feed.get('advice') or []}
    actual = today_actual(feed.get('today'), tariff, billed)
    if actual:
        total = (f'今日（00:00 到現在，實際）：收益 NT${actual[0]:.0f}・電費 NT${actual[1]:.0f}・淨收益 NT${actual[0] - actual[1]:.0f}'
                 + (f'　（照目前狀態挖滿一天：淨 NT${revenue - electricity:.0f}/天）' if revenue else ''))
    elif revenue:
        total = f'今日估算：收益 NT${revenue:.0f}/天・電費 NT${electricity:.0f}/天・淨收益 NT${revenue - electricity:.0f}/天'
    advice, asleep = [], []
    for item in feed.get('advice') or []:
        if item.get('canWake'):  # switched off, so not in the machine list: offer a remote power-on instead
            asleep.append({'id': item['machineId'], 'name': item.get('displayName', '?')})
        mine = item.get('recommendation') == 'mine'
        net = item.get('monthlyNetTwd')
        month = f'（每月淨收益約 NT${net:.0f}）' if net is not None else ''
        advice.append((mine, f"{'⚡' if mine else '💤'} {item.get('displayName', '?')}："
                             f"{'建議挖礦' if mine else '建議停挖'}{month}　{item.get('reason', '')}"))
    # Power lights (asked 2026-11-10, after the laptop sat on unmining for a day): every GPU rig, on or
    # off, from the service's own polls; the window asks again every 15 s.
    power = []
    for m in feed.get('machines', []):
        if m.get('machineId') in FPGA_RIGS:
            continue
        if m.get('sourceStatus') != 'ok':
            light, text = 'red', '連不到'
        elif (m.get('minerRunning') or {}).get('state') == 'running':
            light, text = 'green', '開機・挖礦中'
        else:
            light, text = 'yellow', '開機・沒在挖'
        power.append((light, m.get('displayName', '?'), text))
    power += [('gray', sleeper['name'], '關機') for sleeper in asleep]
    return {'rigs': rigs, 'tariff': tariff_line, 'total': total, 'advice': advice, 'asleep': asleep,
            'power': power, 'checked': now.strftime('%H:%M:%S'),
            'cumulative': cumulative_line(feed.get('cumulative')), 'prices': price_line(feed),
            'balance': balance_line(feed.get('poolBalance'))}

CONTROL_CONFIG = STATUS_SCRIPT.parent / 'aggregator.json'

def rig_control(machine_id, action):
    """start / stop / wake through the status service, the same path as the phone app; a short
    Chinese answer for the window. The password is read from the service's own config each time."""
    try:
        token = json.loads(CONTROL_CONFIG.read_text(encoding='utf-8-sig'))['control']['token']
        answer = requests.post(STATUS_URL.rsplit('/', 1)[0] + f'/control/{machine_id}/{action}',
                               headers={'Authorization': f'Bearer {token}'}, timeout=40).json()
        return answer.get('message') or answer.get('result') or '沒有回應'
    except Exception as error:
        return f'送不出去：{error}'

def balance_line(balance):
    """'錢包（Kryptex 帳上餘額）：0.00043477 BTC ≈ NT$1,102（10:07 讀取）', or '' before the first reading."""
    if not balance or balance.get('balanceBtc') is None:
        return ''
    twd = f"　≈ NT${balance['twd']:,.0f}" if balance.get('twd') is not None else ''
    try:
        when = datetime.fromisoformat(balance['observedAt']).astimezone(TAIPEI).strftime('%m/%d %H:%M')
    except (KeyError, TypeError, ValueError):
        when = '?'
    return f"錢包（Kryptex 帳上餘額）：{balance['balanceBtc']:.8f} BTC{twd}（{when} 讀取，每 2 小時更新）"

def price_line(feed):
    """'幣價：PRL US$1.54（NT$50.2）・QTC US$73.46・BTC US$77,459（00:44 更新，每 10 分鐘）', or '' without prices."""
    market = feed.get('market') or {}
    usd_twd = market.get('usdTwd') or 0
    coins = {}
    if market.get('coinUsd'):
        coins[market.get('coin') or 'PRL'] = market['coinUsd']
    for m in feed.get('machines', []):
        earn = m.get('earnings') or {}
        if earn.get('coin') and earn.get('coinUsd'):
            coins.setdefault(earn['coin'], earn['coinUsd'])
    if not coins:
        return ''
    parts = [f'{coin} US${usd:,.2f}' + (f'（NT${usd * usd_twd:,.1f}）' if usd_twd else '') for coin, usd in coins.items()]
    if market.get('btcUsd'):
        parts.append(f"BTC US${market['btcUsd']:,.0f}")
    try:
        when = datetime.fromisoformat(market['observedAt']).astimezone(TAIPEI).strftime('%H:%M')
        stamp = f'（{when} 更新，每 10 分鐘）'
    except (KeyError, TypeError, ValueError):
        stamp = ''
    return '幣價：' + '・'.join(parts) + stamp

def cumulative_line(cumulative):
    """'本月累計估算（自 11/01 00:00 起，每月 1 號重算，不是實際入帳）：NT$123・PRL 45.678・QTC 0.012', or '' before any."""
    if not cumulative:
        return ''
    try:
        since = datetime.fromisoformat(cumulative['since']).astimezone(TAIPEI).strftime('%m/%d %H:%M')
    except (KeyError, TypeError, ValueError):
        return ''
    coins = {}
    for item in cumulative.get('machines') or []:
        coins[item['coin']] = coins.get(item['coin'], 0) + item['amount']
    parts = [f"NT${cumulative.get('totalTwd') or 0:,.0f}"] + [f'{coin} {amount:,.3f}' for coin, amount in sorted(coins.items())]
    if cumulative.get('totalBtc') is not None:  # The pool account is kept in BTC: check against this.
        parts.append(f"約當 {cumulative['totalBtc']:.8f} BTC")
    return f'本月累計估算（自 {since} 起，每月 1 號重算，不是實際入帳）：' + '・'.join(parts)

def ensure_status_service():
    """Starts the phone's miner status service (miner-status-ios/pc-service) with Pearl mining when
    it is not running yet. It runs hidden and on its own: it also watches the other rigs and sends
    the alerts, so switching to AI or closing this console does not stop it."""
    try:
        probe = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                                "@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                                "Where-Object { $_.CommandLine -like '*office_aggregator.py*' }).Count"],
                               creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, text=True, timeout=20)
        if probe.stdout.strip() not in ('', '0') or not STATUS_SCRIPT.exists():
            return
        command = [str(STATUS_PYTHONW), '-I', str(STATUS_SCRIPT)]
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # pythonw: no window. Outside any job of this console, so it outlives the console.
            subprocess.Popen(command, cwd=str(STATUS_SCRIPT.parent), creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError:
            subprocess.Popen(command, cwd=str(STATUS_SCRIPT.parent), creationflags=flags)
    except Exception:
        pass  # Never let the status service block mining.

def mining_state():
    try: return json.loads((MINING/'panel-mining-state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): return {}

def qtc_running(pid_file='mining.pid'):
    """True when the relay named in qtc-fpga\\logs\\<pid_file> is alive (VCU118 mining.pid, KV260 kv260_mining.pid)."""
    try:
        import psutil
        pid = int((QTC_HOST.parent/'logs'/pid_file).read_text().strip())
        return any('relay.py' in part for part in psutil.Process(pid).cmdline())
    except Exception: return False

def qtc_reading(prefix='mining_'):
    """'120.0 MH/s・晶片 46 °C・穩壓器 92 °C・VCCINT 69.5 A' from the newest relay status line (written every 5 s),
    or '' when there is none in the last minute. The relay pauses at regulator 110 °C and resumes at 95 °C.
    The KV260 (prefix kv260_mining_, 11/04) has no regulator readings: '16.7 MH/s・晶片 40 °C（72 °C 自動停）'."""
    try:
        log = max((QTC_HOST.parent/'logs').glob(prefix + '*.err'), key=lambda p: p.stat().st_mtime)
        if time.time() - log.stat().st_mtime > 60: return ''
        with open(log, 'rb') as f:
            f.seek(max(0, log.stat().st_size - 4096))
            tail = f.read().decode('utf-8', errors='replace')
        line = [s for s in tail.splitlines() if 'regulator' in s][-1]
        mhs = re.search(r'([\d.]+) MH/s', line).group(1)
        chip = re.search(r'\| ([\d.]+) C', line).group(1)
        if prefix == 'kv260_mining_':
            return f'{float(mhs):.1f} MH/s・晶片 {float(chip):.0f} °C（72 °C 自動停）'
        reg = int(re.search(r'regulator (\d+) C', line).group(1))
        amps = re.search(r'VCCINT ([\d.]+) A', line).group(1)
        warn = '⚠ ' if reg >= 100 else ''
        return f'{float(mhs):.1f} MH/s・晶片 {float(chip):.0f} °C・{warn}穩壓器 {reg} °C（110 °C 自動暫停）・VCCINT {amps} A'
    except Exception: return ''

def qtc_control(action, board='vcu118'):
    """Runs `mining_ctl.py start|stop` through WMI, so the relay belongs to no console or Claude process and
    keeps mining when this window closes (10/30: a relay started from a Claude shell died with it).
    Returns the last line mining_ctl printed: started / already-running / start-failed / stopped / not-running.
    The KV260 (11/04) is JTAG-loaded first after a power cycle (about 20 s more), so it gets a longer wait."""
    log = QTC_HOST.parent/'logs'/(f'ctl_{action}_panel.log' if board == 'vcu118' else f'ctl_{action}_{board}_panel.log')
    log.unlink(missing_ok=True)
    line = f'cmd.exe /c ""{QTC_PYTHON}" -u mining_ctl.py --board {board} {action} > "{log}" 2>&1"'
    command = ("Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
               f"@{{CommandLine='{line}'; CurrentDirectory='{QTC_HOST}'}} | Out-Null")
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                   creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=30)
    deadline = time.time() + (75 if board == 'vcu118' else 150)  # start: self-test about 21 s, then 12 s for the relay
    while time.time() < deadline:
        time.sleep(2)
        try: lines = [s.strip() for s in log.read_text(encoding='utf-8', errors='replace').splitlines() if s.strip()]
        except OSError: continue
        if lines and lines[-1] in ('started', 'already-running', 'start-failed', 'stopped', 'not-running'):
            return lines[-1] if lines[-1] != 'start-failed' or len(lines) < 2 else 'start-failed：' + lines[-2]
    return 'timeout'
K = C.WinDLL('kernel32', use_last_error=True)
SIZE = C.c_size_t

class Limits(C.Structure):
    _fields_ = [('process_time', C.c_longlong), ('job_time', C.c_longlong),
                ('flags', W.DWORD), ('min_ws', SIZE), ('max_ws', SIZE),
                ('active_limit', W.DWORD), ('affinity', SIZE),
                ('priority', W.DWORD), ('scheduling', W.DWORD)]

class IO(C.Structure):
    _fields_ = [(n, C.c_ulonglong) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]

class Extended(C.Structure):
    _fields_ = [('limits', Limits), ('io', IO), ('process_memory', SIZE),
                ('job_memory', SIZE), ('peak_process_memory', SIZE), ('peak_job_memory', SIZE)]

class Startup(C.Structure):
    _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR),
                ('title', W.LPWSTR), ('x', W.DWORD), ('y', W.DWORD),
                ('width', W.DWORD), ('height', W.DWORD), ('chars_x', W.DWORD),
                ('chars_y', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD),
                ('show', W.WORD), ('reserved_size', W.WORD), ('reserved_ptr', C.c_void_p),
                ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE)]

class ProcessInfo(C.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]

for name, restype, argtypes in [
    ('CreateMutexW', W.HANDLE, [C.c_void_p, W.BOOL, W.LPCWSTR]),
    ('CreateJobObjectW', W.HANDLE, [C.c_void_p, W.LPCWSTR]),
    ('SetInformationJobObject', W.BOOL, [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]),
    ('QueryInformationJobObject', W.BOOL, [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p]),
    ('CreateProcessW', W.BOOL, [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.POINTER(Startup), C.POINTER(ProcessInfo)]),
    ('AssignProcessToJobObject', W.BOOL, [W.HANDLE, W.HANDLE]),
    ('ResumeThread', W.DWORD, [W.HANDLE]),
    ('TerminateProcess', W.BOOL, [W.HANDLE, W.UINT]),
    ('GetExitCodeProcess', W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
    ('CloseHandle', W.BOOL, [W.HANDLE]),
]:
    fn = getattr(K, name)
    fn.restype, fn.argtypes = restype, argtypes

def checked(value):
    if not value: raise C.WinError(C.get_last_error())
    return value

def save_json(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # 11/19: a reader holding the file open (helper/bridge polling it) makes the rename fail with
    # WinError 5 for a moment; retry briefly instead of surfacing a console error.
    for attempt in range(10):
        try:
            temporary.replace(path); return
        except PermissionError:
            if attempt == 9: raise
            time.sleep(0.05)

class ServiceJob:
    def __init__(self):
        self.handle = checked(K.CreateJobObjectW(None, None))
        self.process = None
        limits = Extended()
        limits.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try: checked(K.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)))
        except Exception:
            self.close()
            raise

    def launch(self, command):
        info, start = ProcessInfo(), Startup()
        start.cb = C.sizeof(start)
        start.flags = 0x100  # STARTF_USESTDHANDLES
        with open(ROOT/'media-control-startup.log', 'ab', buffering=0) as log, open(os.devnull, 'rb') as stdin:
            for f in (log, stdin): os.set_inheritable(f.fileno(), True)
            start.stdin = msvcrt.get_osfhandle(stdin.fileno())
            start.stdout = start.stderr = msvcrt.get_osfhandle(log.fileno())
            checked(K.CreateProcessW(None, C.create_unicode_buffer(subprocess.list2cmdline(command)),
                    None, None, True, 0x08000004, None, str(ROOT), C.byref(start), C.byref(info)))
        try:
            # Suspended until assigned: even very fast children cannot escape ownership.
            checked(K.AssignProcessToJobObject(self.handle, info.process))
            if K.ResumeThread(info.thread) == 0xFFFFFFFF: raise C.WinError(C.get_last_error())
            self.process = info.process
        except Exception:
            K.TerminateProcess(info.process, 1)
            K.CloseHandle(info.process)
            raise
        finally: K.CloseHandle(info.thread)

    def pids(self):
        if not self.handle: return []
        buf = C.create_string_buffer(16384)
        checked(K.QueryInformationJobObject(self.handle, 3, buf, len(buf), None))
        count = W.DWORD.from_buffer(buf, 4).value
        return list((SIZE * count).from_buffer(buf, 8))

    def exit_code(self):
        result = W.DWORD(259)
        if self.process: checked(K.GetExitCodeProcess(self.process, C.byref(result)))
        return result.value

    def close(self):
        if self.handle: K.CloseHandle(self.handle); self.handle = None
        if self.process: K.CloseHandle(self.process); self.process = None

def fetch(port, path):
    try:
        response = requests.get(f'http://127.0.0.1:{port}{path}', timeout=1)
        response.raise_for_status()
        return response.json()
    except requests.RequestException: return {}

def speed_text(live):
    state = live.get('state')
    if state == 'generating':
        rate, mean = live.get('tok_s'), live.get('tok_s_mean')
        if not isinstance(rate, (int, float)):
            return '目前 TPS：計算中…'
        text = f'目前 TPS：{rate:.1f} tokens/秒'
        if isinstance(mean, (int, float)):
            text += f'　｜　本輪平均：{mean:.1f}'
        return text
    if state == 'reading':
        return '目前 TPS：讀取輸入中，尚未開始生成'
    if state == 'idle':
        return '目前 TPS：未生成（待機）'
    return '目前 TPS：—（尚未取得生成資訊）'

def health_indicator(mode, busy, error, mining, token, current, now, ai_alive=True):
    if mode is None: return '#6b7280', '待機／手動停止'
    if error: return '#b42318', '異常：請查看下方訊息'
    if mode == 'Mining':
        if mining.get('token') != token:
            return '#b77900', '啟動中' if busy else '狀態資料暫時無法讀取'
        state = mining.get('status')
        if state in ('stopped', 'error'): return '#b42318', '挖礦異常停止'
        if now-mining.get('updated',0)>15: return '#b77900', '狀態資料過期，無法確認運作'
        if state != 'running': return '#b77900', '啟動中'
        if any(g.get('temperature',0)>=76 for g in mining.get('gpus',[])):
            return '#b77900', '溫度偏高／降載保護中'
        if not mining.get('th_s') or not mining.get('accepted'):
            return '#b77900', '運轉中，等待算力／礦池接受確認'
        return '#15803d', '正常挖礦（本機狀態）'
    if busy: return '#b77900', '啟動中'
    if not ai_alive: return '#b42318', 'AI 服務已退出'
    if now-current.get('checked_at',0)>15: return '#b77900', '等待最新 AI 狀態'
    if current.get('loaded'): return '#15803d', 'AI 模型正常運作'
    return '#b77900', '模型載入中／暫時無法連線'

class Panel:
    def __init__(self, mode):
        self.root = tk.Tk()
        self.root.title('本機 AI 控制台')
        self.root.geometry('940x820')
        self.root.minsize(760, 640)
        self.root.option_add('*Font', 'Microsoft\ JhengHei 11')
        self.job, self.mode, self.busy, self.closed = None, None, False, False
        self.poller, self.results = None, queue.Queue()
        self.generation = 0
        self.mining_token = None
        self.pending_mode = None
        self.admin_result = queue.Queue()
        self.current, self.last_error = {}, ''
        # Tabs: the AI services, and mining with one switch per machine (2026-10-31), split into GPU and
        # FPGA (11/04). Each scrolls: the rig list grows with every machine and can be taller than the window.
        self.tabs = ttk.Notebook(self.root); self.tabs.pack(fill='both', expand=True)
        self.canvases, self.wrap, self.wrapped = [], 800, []  # wrap: text width that fits the window
        frame = self.scroll_tab('AI 服務')
        mining = self.scroll_tab('GPU 挖礦')
        fpga = self.scroll_tab('FPGA 挖礦')
        camera = self.scroll_tab('鏡頭')  # 11/19: the office webcam, live like the phone's 看鏡頭 page
        thermo = self.scroll_tab('溫濕度')  # 11/19: the thermometers, like the App's 溫濕度 tab
        self.root.bind_all('<MouseWheel>', lambda e: self.canvases[self.tabs.index('current')].yview_scroll(int(-e.delta/120), 'units'))
        ttk.Label(frame, text='本機 AI 控制台', font=('Microsoft JhengHei',20,'bold')).pack(anchor='w')
        ttk.Label(frame, text='服務集中在背景執行，只保留這一個視窗。挖礦在「GPU 挖礦」「FPGA 挖礦」分頁。').pack(anchor='w', pady=(7,14))
        self.health_light = ttk.Label(frame, text='● 待機／手動停止', foreground='#6b7280', font=('Microsoft JhengHei',15,'bold'))
        self.health_light.pack(anchor='w', pady=(0,10))
        # Miner status service (also read by the phone app): the same rigs, earnings, tariff and
        # advice as the app, refreshed about every 15 s, each machine with its own switch.
        ttk.Label(mining, text='GPU 挖礦（PRL）', font=('Microsoft JhengHei',20,'bold')).pack(anchor='w')
        self.wrapping(ttk.Label(mining, text='每台各自開停，跟手機 App 走同一條路；本機（office）開挖會先停掉 AI 服務（同一張顯卡）。')).pack(anchor='w', pady=(7,4))
        self.rig_message = tk.StringVar(value='')
        self.wrapping(ttk.Label(mining, textvariable=self.rig_message, foreground='#1d4ed8')).pack(anchor='w', pady=(0,8))
        self.rig_frame = ttk.Frame(mining); self.rig_frame.pack(anchor='w', fill='x', pady=(0,10))
        self.rig_results, self.rig_countdown, self.rig_busy = queue.Queue(), 0, False
        self.rig_actions, self.rig_pending, self.last_summary = queue.Queue(), {}, None
        self.message_at = {}
        ttk.Label(self.rig_frame, text='礦機狀態：檢查中…', foreground='#6b7280').pack(anchor='w')
        ttk.Label(fpga, text='FPGA 挖礦（QTC）', font=('Microsoft JhengHei',20,'bold')).pack(anchor='w')
        self.wrapping(ttk.Label(fpga, text='FPGA 不用這台電腦的顯卡：關閉此視窗或按「停止全部」都不會停它們。')).pack(anchor='w', pady=(7,4))
        self.fpga_message = tk.StringVar(value='')
        self.wrapping(ttk.Label(fpga, textvariable=self.fpga_message, foreground='#1d4ed8')).pack(anchor='w', pady=(0,8))
        self.fpga_frame = ttk.Frame(fpga); self.fpga_frame.pack(anchor='w', fill='x', pady=(0,10))
        ttk.Label(self.fpga_frame, text='礦機狀態：檢查中…', foreground='#6b7280').pack(anchor='w')
        self.status = tk.StringVar(value='待機：請選擇要啟動的模式。')
        self.detail = tk.StringVar(value='尚未載入模型或生圖／影片服務。')
        self.speed = tk.StringVar(value='目前 TPS：未生成（待機）')
        ttk.Label(frame, textvariable=self.status, wraplength=560).pack(anchor='w')
        ttk.Label(frame, textvariable=self.detail, wraplength=560).pack(anchor='w', pady=(5,12))
        ttk.Label(frame, textvariable=self.speed, font=('Microsoft JhengHei',13,'bold')).pack(anchor='w')
        ttk.Label(frame, text='TPS 為文字生成速度；約每 1.5 秒更新，包含推理與工具輸出。').pack(anchor='w', pady=(4,12))
        modes = ttk.Frame(frame); modes.pack(fill='x')
        ttk.Button(modes, text='模型＋靜態生圖', command=lambda:self.start('Image')).pack(side='left', padx=(0,8))
        ttk.Button(modes, text='模型＋生影片', command=lambda:self.start('Video')).pack(side='left')
        # VCU118 FPGA on QTC: independent of the GPU modes and of 停止全部 (it uses no PC resources).
        self.qtc_on, self.qtc_busy, self.qtc_results, self.qtc_countdown = None, False, queue.Queue(), 0
        self.qtc_text = tk.StringVar(value='QTC（VCU118 FPGA）：檢查中…')
        ttk.Label(fpga, text='板子即時讀數', font=('Microsoft JhengHei',12,'bold')).pack(anchor='w')
        self.wrapping(ttk.Label(fpga, textvariable=self.qtc_text, foreground='#374151')).pack(anchor='w', pady=(0,8))
        # The KV260 (11/04 experiment) was retired 11/05 by the user (about NT$2 a day); its line and button are gone.
        for page in (mining, fpga):
            ttk.Button(page, text='礦池收益（Kryptex 網頁）', command=lambda:webbrowser.open('https://www.kryptex.com/site/hardware')).pack(anchor='w')
        actions = ttk.Frame(frame); actions.pack(fill='x', pady=12)
        self.web_button = ttk.Button(actions, text='開啟生圖／影片網頁', command=self.open_page, state='disabled')
        self.web_button.pack(side='left', padx=(0,8))
        tk.Button(actions, text='停止 AI 服務・回到挖礦', bg='#b42318', fg='white', relief='flat',
                  padx=12, pady=4, command=self.back_to_mining).pack(side='left')
        # Mining is the default (2026-11-10, the user: 「預設開挖，除非選本地模型功能才切換」): the console
        # starts it when nothing runs, a model mode stops it for its GPUs, and stopping the model mode
        # starts it again. Closing or restarting the console (to update it) leaves mining alone; only
        # 本機 → 停止 on the GPU tab stops it.
        self.wrapping(ttk.Label(frame, text='關閉此視窗只會停止 AI 服務（模型、生圖、生影片），本機挖礦照常；要停本機挖礦，'
                                             '到「GPU 挖礦」分頁按本機的「停止」。Hermes 桌面程式和其他礦機不受影響。')).pack(anchor='w', pady=(2,0))
        try:
            import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
            from camera_tab import CameraTab
            self.camera_tab = CameraTab(self, camera, tab_index=3)
        except Exception as error:  # the rest of the console must still work without the camera tab
            ttk.Label(camera, text='鏡頭分頁無法載入：' + str(error), foreground='#b42318').pack(anchor='w')
        try:
            from thermo_tab import ThermoTab
            self.thermo_tab = ThermoTab(self, thermo, tab_index=4)
        except Exception as error:
            ttk.Label(thermo, text='溫濕度分頁無法載入：' + str(error), foreground='#b42318').pack(anchor='w')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, (lambda:self.start(mode)) if mode else self.default_mode)
        self.root.after(500, self.tick)

    def scroll_tab(self, title):
        """A notebook tab whose content scrolls; returns the frame to fill."""
        outer = ttk.Frame(self.tabs); self.tabs.add(outer, text=f'  {title}  ')
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y'); canvas.pack(side='left', fill='both', expand=True)
        frame = ttk.Frame(canvas, padding=22)
        content = canvas.create_window((0,0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: (canvas.itemconfigure(content, width=e.width), self.resized(e.width)))
        self.canvases.append(canvas)
        return frame

    def wrapping(self, label):
        """A label whose text wraps to the window's width (fixed widths overflowed at 200 % display scaling)."""
        label.configure(wraplength=self.wrap)
        self.wrapped.append(label)
        return label

    def resized(self, width):
        wrap = max(360, width - 70)
        if abs(wrap - self.wrap) < 20: return
        self.wrap = wrap
        for label in self.wrapped: label.configure(wraplength=wrap)
        if self.last_summary: self.show_rigs(self.last_summary)

    def say(self, machine_id, text):
        """A machine's message on its own tab: FPGA or GPU. Stamped with the time and cleared after
        MESSAGE_KEEP_S, so an old answer (e.g. 'RTX 5080：連不到這台機器' from last night) does not
        sit there looking like the current state."""
        var = self.fpga_message if machine_id in FPGA_RIGS else self.rig_message
        var.set(datetime.now().strftime('%H:%M ') + text)
        self.message_at[str(var)] = time.time()

    def expire_messages(self):
        for var in (self.rig_message, self.fpga_message):
            at = self.message_at.get(str(var))
            if at and time.time() - at > MESSAGE_KEEP_S:
                var.set('')
                self.message_at.pop(str(var), None)

    def rig_switch(self, machine_id, action, name):
        """One machine's button. This PC (office) is the console's own Mining mode; the FPGA goes
        through mining_ctl as before; the others through the status service, like the phone app."""
        if machine_id in self.rig_pending: return
        if machine_id == 'office':
            if action == 'start': self.start('Mining')
            else: self.stop_mining()
            self.say(machine_id, f'{name}：' + ('開挖中，約 1～2 分鐘後出算力（進度在「AI 服務」分頁上方）' if action == 'start' else '已停止挖礦'))
            self.rig_countdown = 0
            return
        self.rig_pending[machine_id] = action
        verb = {'start': '開始中', 'stop': '停止中', 'wake': '送出開機指令中'}[action]
        self.say(machine_id, f'{name}：{verb}…' + ('板子自檢約 30 秒' if action == 'start' and machine_id == 'vcu118' else ''))
        if machine_id == 'vcu118':
            self.qtc_action(action)
        else:
            threading.Thread(target=lambda: self.rig_actions.put((machine_id, name, rig_control(machine_id, action))), daemon=True).start()
        if self.last_summary: self.show_rigs(self.last_summary)

    def start(self, mode):
        self.root.deiconify(); self.root.lift()
        if self.busy: return
        if self.job and self.mode == mode and self.job.pids(): return
        state = fetch(8001, '/v1/status')
        for port in (8188, 8189):
            q = fetch(port, '/queue')
            if q.get('queue_running') or q.get('queue_pending'):
                self.status.set('有圖片／影片正在生成，請等完成後再切換。'); return
        if state.get('activity', {}).get('in_flight'):
            self.status.set('Hermes 正在回答，請先停止工作或等它完成再切換。'); return
        if state and not self.job:
            self.status.set('偵測到獨立啟動的模型；請先關閉原服務視窗，再選擇模式。'); return
        self.stop()
        self.busy = True
        self.mode, self.last_error = mode, ''
        label = {'Image':'模型＋靜態生圖','Video':'模型＋生影片','Mining':'珍珠挖礦'}[mode]
        self.status.set(label + ' 啟動中，約需 1～2 分鐘…')
        self.detail.set('請稍候，無須重複點擊。')
        self.speed.set('目前 TPS：—（服務啟動中）')
        # Allow killed processes to release their CUDA contexts before the next allocation.
        generation = self.generation
        self.root.after(2500, lambda:self.prepare_mode(mode, generation, time.time()+120))

    def prepare_mode(self, mode, generation, deadline):
        if self.closed or not self.busy or generation != self.generation: return
        previous = mining_state()
        if previous.get('status') in ('running','preparing') and time.time()-previous.get('updated',0)<90:
            self.status.set('等待挖礦停止並釋放 GPU…')
            if time.time()>deadline:
                self.busy=False; self.status.set('停止逾時，請關閉原挖礦視窗後重試。'); return
            self.root.after(1000,lambda:self.prepare_mode(mode,generation,deadline)); return
        token = str(time.time())
        self.mining_token = token
        self.status.set('請允許系統管理員授權，正在套用 GPU 設定…')
        threading.Thread(target=self.admin_launch,args=(mode,token,generation),daemon=True).start()

    def admin_launch(self, mode, token, generation):
        action = 'mining' if mode == 'Mining' else 'restore'
        if mode == 'Mining': ensure_status_service()
        try:
            error = self.task_launch(action, token)
            if error is None:  # The scheduled task is not installed: ask for elevation the old way.
                python = str(Path(os.__file__).parent.parent/'python.exe')
                arguments = subprocess.list2cmdline([str(MINING/'panel_bridge.py'),action,str(os.getpid()),token])
                quote = lambda s: "'"+s.replace("'","''")+"'"
                command = '$ErrorActionPreference="Stop"; $p=Start-Process -FilePath '+quote(python)+' -ArgumentList '+quote(arguments)+' -Verb RunAs -WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode'
                result = subprocess.run(['powershell.exe','-NoProfile','-Command',command],creationflags=subprocess.CREATE_NO_WINDOW,capture_output=True,text=True)
                error = result.stderr.strip() if result.returncode else ''
            self.admin_result.put((generation,mode,token,error))
        except Exception as exc: self.admin_result.put((generation,mode,token,str(exc)))

    def task_launch(self, action, token):
        """Runs panel_bridge through the PearlPanelBridge task (highest privileges, so no UAC prompt and a
        start from the phone works) and waits for it like Start-Process -Wait. None when not installed."""
        hidden = subprocess.CREATE_NO_WINDOW
        if subprocess.run(['schtasks','/query','/tn',PANEL_TASK],creationflags=hidden,capture_output=True).returncode:
            return None
        save_json(MINING/'panel-launch.json', {'action':action,'pid':os.getpid(),'token':token})
        run = subprocess.run(['schtasks','/run','/tn',PANEL_TASK],creationflags=hidden,capture_output=True,text=True)
        if run.returncode: return '排程工作無法啟動：'+(run.stderr or run.stdout).strip()
        deadline, started = time.time()+90, False
        while True:
            time.sleep(2)
            state = mining_state()
            if state.get('token') == token:
                started = True
                if state.get('status') not in ('preparing','running'): return ''
                if time.time()-state.get('updated',0) > 120: return '挖礦程序沒有回應'
            # A read that lands on the state file being replaced comes back empty: only a session that
            # never showed up is a failed start (seen 2026-10-28: a false 異常 after hours of mining).
            elif not started and time.time() > deadline:
                return '排程工作沒有開始執行（詳見 Pearl-mining\\panel-task.log）'

    def launch(self):
        if self.closed or not self.busy: return
        try:
            self.job = ServiceJob()
            self.job.launch(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                             str(ROOT/'start_media.ps1'),'-Media',self.mode,'-NoBrowser','-Managed'])
        except Exception as error:
            if self.job: self.job.close(); self.job = None
            self.last_error = str(error)
            self.status.set('啟動失敗：' + str(error))
        finally: self.busy = False

    def default_mode(self):
        """At start-up: a mining session that is already running (the console was closed or
        restarted while mining) is taken over as it is; otherwise mining is started, unless a model
        was started on its own outside the console."""
        if self.closed: return
        state = mining_state()
        if state.get('status') in ('running','preparing') and time.time()-state.get('updated',0) < 90:
            self.adopt_mining(state)
        elif not fetch(8001, '/v1/status'):
            self.start('Mining')

    def adopt_mining(self, state):
        """Shows a mining session this console did not start (the token is the bridge's)."""
        self.mode, self.mining_token, self.busy, self.last_error = 'Mining', state.get('token'), False, ''
        self.status.set('珍珠挖礦中（接手先前已在跑的挖礦）')
        self.detail.set('關閉此視窗不會停止挖礦；要停請到「GPU 挖礦」分頁按本機的「停止」。')
        self.speed.set('本機算力：讀取中…')
        self.write_state()

    def back_to_mining(self):
        """The red button: ends the model mode (its job dies with the handle) and goes back to the
        default, mining. Already mining: nothing to do."""
        state = mining_state()
        if self.mode == 'Mining' and state.get('status') in ('running','preparing') and time.time()-state.get('updated',0) < 90:
            self.status.set('本機已在挖礦；要停請到「GPU 挖礦」分頁按本機的「停止」。'); return
        self.start('Mining')

    def stop_mining(self):
        """本機 → 停止 on the GPU tab: the one place that stops this PC's mining on purpose."""
        self.stop()

    def stop(self):
        """Stops everything this console manages, mining included: for a model mode that needs the
        GPUs, 本機 → 停止, and `--action Stop` (PREPARE-MINING asks for it before a manual start)."""
        save_json(MINING/'panel-stop.json', {'time':time.time()})
        self.busy = False
        self.generation += 1
        if self.job: self.job.close(); self.job = None
        self.mode, self.current = None, {}
        self.last_error = ''
        self.health_light.configure(text='● 待機／手動停止', foreground='#6b7280')
        state = mining_state()
        active = state.get('status') in ('running','preparing') and time.time()-state.get('updated',0)<90
        self.status.set('正在停止挖礦、恢復自動風扇…' if active else '已停止控制台管理的服務。')
        self.detail.set('要再次使用，選擇上方任一模式。')
        self.speed.set('目前 TPS：未生成（待機）')
        self.web_button.configure(state='disabled')
        self.write_state()

    def qtc_action(self, action):
        if self.qtc_busy: return
        self.qtc_busy = True
        self.qtc_text.set('QTC（VCU118 FPGA）：' + ('啟動中，板子自檢約 30 秒…' if action == 'start' else '停止中…'))
        threading.Thread(target=lambda: self.qtc_results.put(('action', action, qtc_control(action))), daemon=True).start()

    def refresh_qtc(self):
        while not self.qtc_results.empty():
            kind, action, result = self.qtc_results.get()
            if kind == 'action':
                self.qtc_busy = False
                self.qtc_countdown = self.rig_countdown = 0  # re-read the relay and the rig list right away
                self.rig_pending.pop('vcu118', None)
                failed = result.startswith('start-failed') or result == 'timeout'
                words = {'started': '已開始挖礦', 'already-running': '已經在挖礦了', 'stopped': '已停止', 'not-running': '本來就沒在挖'}
                self.say('vcu118', 'VCU118：' + ('啟動失敗－' + result.split('：', 1)[-1] if failed else words.get(result, result)))
                if failed:
                    self.qtc_text.set('QTC（VCU118 FPGA）：啟動失敗－' + result.split('：', 1)[-1])
                    continue
            elif not self.qtc_busy:
                self.qtc_on, reading = result
                self.qtc_text.set('QTC（VCU118 FPGA）：' + (f'挖礦中 {reading}；關閉此視窗或按停止全部都不會停它' if self.qtc_on and reading
                                                       else '挖礦中（算力見上方清單）；關閉此視窗或按停止全部都不會停它'
                                                       if self.qtc_on else '已停止'))
        self.qtc_countdown -= 1
        if self.qtc_countdown > 0 or self.qtc_busy: return
        self.qtc_countdown = 10  # tick is 1.5 s
        threading.Thread(target=lambda: self.qtc_results.put(('state', None, (qtc_running(), qtc_reading()))), daemon=True).start()

    def close(self):
        """Closing the window ends the model services (the job handle closes with the process) and
        leaves mining running; the next console takes it over (default_mode)."""
        self.closed = True
        self.busy = False
        self.generation += 1
        if self.job: self.job.close(); self.job = None
        if self.mode != 'Mining': self.mode, self.current = None, {}
        self.write_state()
        self.root.destroy()

    def open_page(self):
        if self.job: webbrowser.open('http://127.0.0.1:8189/' if self.mode == 'Video' else 'http://127.0.0.1:5000/')

    def poll(self, generation):
        status = fetch(8001, '/v1/status')
        metrics = fetch(8001, '/metrics') if status.get('loaded') else {}
        # Readiness of the launcher is authoritative; status polling does not send inference.
        self.results.put({'generation':generation, 'checked_at':time.time(), 'loaded':bool(status.get('loaded')), 'context':status.get('cache_max_tokens'),
                          'activity':status.get('activity'), 'hardware':metrics.get('hardware',{}),
                          'engine':metrics.get('engine',{}), 'live':metrics.get('live',{})})

    def write_state(self):
        save_json(STATE, {'pid':os.getpid(),'updated':time.time(),'mode':self.mode,
                         'owned_pids':self.job.pids() if self.job else [],
                         'status':self.status.get(),'detail':self.detail.get(),'speed':self.speed.get(),
                         'error':self.last_error,'current':self.current,'closed':self.closed})

    def refresh_rigs(self):
        self.expire_messages()
        while not self.rig_actions.empty():
            machine_id, name, answer = self.rig_actions.get()
            self.rig_pending.pop(machine_id, None)
            self.say(machine_id, f'{name}：{answer}')
            self.rig_countdown = 0  # show the new state soon
            if self.last_summary: self.show_rigs(self.last_summary)
        while not self.rig_results.empty():
            self.show_rigs(self.rig_results.get())
            self.rig_busy = False
        self.rig_countdown -= 1
        if self.rig_countdown > 0 or self.rig_busy: return
        self.rig_countdown, self.rig_busy = 10, True  # tick is 1.5 s
        threading.Thread(target=lambda: self.rig_results.put(rig_summary()), daemon=True).start()

    def show_rigs(self, summary):
        """The same information as the phone app: rigs (each with its own switch), today's tariff,
        totals and the advice."""
        self.last_summary = summary
        for parent in (self.rig_frame, self.fpga_frame):
            for child in parent.winfo_children(): child.destroy()
        if summary is None:
            ttk.Label(self.rig_frame, text='礦機狀態服務：未執行（按本機的「開始」會自動啟動）', foreground='#6b7280').pack(anchor='w')
            # This PC can still be started without the service; the service comes up with it.
            ttk.Button(self.rig_frame, text='本機開始挖礦', command=lambda: self.rig_switch('office', 'start', '本機')).pack(anchor='w', pady=(4,0))
            ttk.Label(self.fpga_frame, text='礦機狀態服務：未執行（到「GPU 挖礦」分頁按本機的「開始」會自動啟動）', foreground='#6b7280',
                      wraplength=self.wrap).pack(anchor='w')
            return
        gpus = [rig for rig in summary['rigs'] if rig['id'] not in FPGA_RIGS]
        fpgas = [rig for rig in summary['rigs'] if rig['id'] in FPGA_RIGS]
        if summary.get('power'):
            ttk.Label(self.rig_frame, text=f"開機狀態（每 15 秒檢查・最後 {summary['checked']}）",
                      font=('Microsoft JhengHei',12,'bold')).pack(anchor='w')
            strip = ttk.Frame(self.rig_frame); strip.pack(anchor='w', fill='x', pady=(2,8))
            for index, (light, name, text) in enumerate(summary['power']):
                ttk.Label(strip, text=f'● {name}：{text}', foreground=RIG_COLORS.get(light, RIG_COLORS['gray'])[0],
                          font=('Microsoft JhengHei',11,'bold')).grid(row=index // 2, column=index % 2, sticky='w', padx=(0,18))
        ttk.Label(self.rig_frame, text='GPU 礦機', font=('Microsoft JhengHei',12,'bold')).pack(anchor='w')
        ttk.Label(self.fpga_frame, text='FPGA 礦機', font=('Microsoft JhengHei',12,'bold')).pack(anchor='w')
        if not gpus and not summary.get('asleep'):
            ttk.Label(self.rig_frame, text='目前沒有機器在清單上', foreground='#6b7280').pack(anchor='w')
        if not fpgas:
            ttk.Label(self.fpga_frame, text='目前沒有機器在清單上', foreground='#6b7280').pack(anchor='w')
        def row(light, line, detail, label, command, pending, parent=self.rig_frame):
            box = ttk.Frame(parent); box.pack(anchor='w', fill='x', pady=(6,0))
            head = ttk.Frame(box); head.pack(anchor='w', fill='x')
            button = ttk.Button(head, text='處理中…' if pending else label, width=10, command=command)
            if pending: button.state(['disabled'])
            button.pack(side='left', padx=(0,10))
            color = RIG_COLORS.get(light, RIG_COLORS['gray'])[0]
            ttk.Label(head, text=f'● {line}', foreground=color, font=('Microsoft JhengHei',11,'bold'),
                      wraplength=max(200, self.wrap - button.winfo_reqwidth() - 20)).pack(side='left')
            if detail:
                ttk.Label(box, text=detail, foreground='#374151', wraplength=self.wrap).pack(anchor='w')
        for rig in summary['rigs']:
            action = 'stop' if rig['running'] else 'start'
            row(rig['light'], rig['line'], rig['detail'], '停止' if rig['running'] else '開始',
                lambda r=rig, a=action: self.rig_switch(r['id'], a, r['name']), rig['id'] in self.rig_pending,
                self.fpga_frame if rig['id'] in FPGA_RIGS else self.rig_frame)
        if summary.get('prices'):  # the QTC price is what the FPGA page earns in
            ttk.Label(self.fpga_frame, text=summary['prices'], wraplength=self.wrap).pack(anchor='w', pady=(4,0))
        for sleeper in summary.get('asleep') or []:
            row('gray', f"{sleeper['name']}・關機中", '　開機後再按「開始」才會挖礦', '遠端開機',
                lambda s=sleeper: self.rig_switch(s['id'], 'wake', s['name']), sleeper['id'] in self.rig_pending)
        for text in (summary['total'], summary.get('balance'), summary.get('cumulative'), summary.get('prices'), summary['tariff']):
            if text:
                ttk.Label(self.rig_frame, text=text, wraplength=self.wrap).pack(anchor='w', pady=(4,0))
        if summary['advice']:
            ttk.Label(self.rig_frame, text='開挖建議（要算電費的機器）', font=('Microsoft JhengHei',12,'bold')).pack(anchor='w', pady=(8,0))
            for mine, text in summary['advice']:
                ttk.Label(self.rig_frame, text=text, foreground='#15803d' if mine else '#b45309', wraplength=self.wrap).pack(anchor='w')

    def tick(self):
        self.refresh_rigs()
        self.refresh_qtc()
        if self.closed: return
        try:
            while not self.admin_result.empty():
                generation, mode, token, error = self.admin_result.get_nowait()
                if generation != self.generation: continue
                state = mining_state()
                if error or state.get('token') != token or state.get('status') == 'error':
                    self.busy=False
                    self.last_error=error or state.get('error','啟動未完成')
                    self.status.set('啟動失敗：'+self.last_error)
                elif mode != 'Mining' and state.get('status') == 'restored':
                    self.launch()
                else:
                    self.busy=False
                    self.status.set('挖礦已停止；可重新選擇模式。')
            if self.mode == 'Mining':
                state = mining_state()
                if state.get('token') == self.mining_token and state.get('status') == 'running':
                    self.busy=False
                    self.status.set('珍珠挖礦中（76°C 降載／80°C 最低負載／82°C 保護停止）')
                    self.detail.set('　'.join(f"GPU{g['index']}: {g['temperature']:.0f}°C / {g['watts']:.0f}W" for g in state.get('gpus',[])))
                    rate = state.get('th_s')
                    speed = f'{rate:.2f} TH/s' if isinstance(rate,(int,float)) else '計算中'
                    self.speed.set(f"本機算力：{speed}　｜　已接受份額：{state.get('accepted',0)}")
            elif not self.mode and self.status.get().startswith('正在停止挖礦'):
                if mining_state().get('status') not in ('running','preparing'):
                    self.status.set('已停止全部服務。')
            if REQUEST.exists():
                request = json.loads(REQUEST.read_text(encoding='utf-8'))
                REQUEST.unlink()
                if time.time()-request.get('time',0) < 60:
                    action = request.get('action')
                    if action in ('Image','Video','Mining'): self.start(action)
                    elif action == 'Stop': self.stop()
                    elif action == 'Quit': self.close(); return
                    else: self.root.deiconify(); self.root.lift()
            while not self.results.empty():
                update = self.results.get_nowait()
                if not self.job or update['generation'] != self.generation: continue
                self.current = update
                self.speed.set(speed_text(update.get('live',{})))
                if self.job:
                    code = self.job.exit_code()
                    if code not in (0,259):
                        self.last_error = f'啟動器回報 {code}；詳見 media-control-startup.log'
                        self.status.set(self.last_error)
                    elif self.current.get('loaded') and code == 0:
                        name = '模型＋靜態生圖' if self.mode == 'Image' else '模型＋生影片'
                        self.status.set(name + ' 已就緒')
                        self.web_button.configure(state='normal')
                        hw = self.current.get('hardware',{})
                        used, total = hw.get('ram_used',0)/2**30, hw.get('ram_total',0)/2**30
                        self.detail.set(f"上下文 {self.current['context']//1024}K · 記憶體 {used:.1f} / {total:.1f} GiB")
            if self.job and (self.poller is None or not self.poller.is_alive()):
                self.poller = threading.Thread(target=self.poll,args=(self.generation,),daemon=True); self.poller.start()
            color, label = health_indicator(self.mode,self.busy,self.last_error,mining_state(),
                                            self.mining_token,self.current,time.time(),
                                            bool(self.job and self.job.pids()))
            self.health_light.configure(text='● '+label, foreground=color)
            try:
                self.write_state()
            except OSError:
                pass  # 11/19: transient lock on the state file; the next tick (1.5 s) writes it again
        except Exception as error:
            # 11/19: not stored in last_error — that made one passing glitch show 異常 forever while
            # mining overwrote the message. The next successful tick repaints the light.
            self.status.set('控制台錯誤：'+str(error))
            self.health_light.configure(text='● 控制台異常', foreground='#b42318')
        self.root.after(1500,self.tick)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['Image','Video','Mining'])
    parser.add_argument('--action', choices=['Show','Stop','Quit'])
    args=parser.parse_args()
    mutex=checked(K.CreateMutexW(None,True,'Local\\Hermes-Strata-One-Control-Window'))
    existing=C.get_last_error()==183
    # A shortcut can be clicked while the previous window is finishing its close.
    for _ in range(25):
        if not existing: break
        try: closing=json.loads(STATE.read_text(encoding='utf-8')).get('closed',False)
        except (OSError,ValueError): closing=False
        if not closing: break
        K.CloseHandle(mutex)
        time.sleep(.2)
        mutex=checked(K.CreateMutexW(None,True,'Local\\Hermes-Strata-One-Control-Window'))
        existing=C.get_last_error()==183
    try:
        if existing:
            save_json(REQUEST,{'action':args.action or args.mode or 'Show','time':time.time()}); return
        if args.action in ('Stop','Quit'): return
        REQUEST.unlink(missing_ok=True)
        panel=Panel(args.mode)
        try: panel.root.mainloop()
        finally:
            if panel.job: panel.job.close()
    finally: K.CloseHandle(mutex)

if __name__=='__main__':
    try: main()
    except Exception:
        import traceback
        with (ROOT/'media-control-error.log').open('a',encoding='utf-8') as log: traceback.print_exc(file=log)
        raise

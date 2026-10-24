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
import win32api
import win32con
import win32job

ROOT = Path(__file__).resolve().parent

def read_gpus():
    output = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,temperature.gpu,power.draw,power.limit,clocks.current.graphics,memory.used', '--format=csv,noheader,nounits'], text=True, timeout=8)
    return {int(r[0]): dict(uuid=r[1].strip(), temperature=float(r[2]), watts=float(r[3]), power=float(r[4]), core=float(r[5]), memory=float(r[6])) for r in csv.reader(output.splitlines())}

def check(config, rows, idle=False):
    for gpu in config['gpus']:
        row = rows[gpu['index']]
        if row['uuid'] != gpu['uuid']:
            raise RuntimeError('GPU identity mismatch')
        if row['temperature'] >= config['stop_c']:
            raise RuntimeError('Temperature cutoff')
        if row['power'] > gpu['power_w']+1 or row['core'] > gpu['core_mhz']+30:
            raise RuntimeError('Apply locally validated power/core settings before mining')
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

def main():
    config = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig'))
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
    job = win32job.CreateJobObject(None, '')
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    process = None
    started = time.monotonic()
    reason = 'Stopped'
    try:
        process = subprocess.Popen([str(ROOT/'runtime'/'krig-miner.exe'), '--coin', 'pearl', '--url', 'stratum+ssl://prl.kryptex.network:8048', '--user', config['mining_user']+'.'+config['worker'], '--devices', ','.join(str(g['index']) for g in config['gpus']), '--no-rocm', '--no-intel', '--gpu-no-reset-oc', '--no-tui', '--log-file', str(log)])
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        last_share, accepted = time.monotonic(), 0
        print('Continuous mining; close window or Ctrl+C to stop. Thermal and no-share protection active.', flush=True)
        with (logs/f'gpu-{stamp}.jsonl').open('w', encoding='utf-8') as output:
            while True:
                rows = read_gpus()
                check(config, rows)
                output.write(json.dumps({'time': datetime.datetime.now().astimezone().isoformat(), 'gpus': rows})+'\n')
                output.flush()
                if any(rows[g['index']]['temperature'] >= config['warn_c'] for g in config['gpus']):
                    print('WARNING: high temperature', flush=True)
                if process.poll() is not None:
                    raise RuntimeError(f'Miner exited {process.returncode}')
                if log.exists():
                    with log.open('rb') as source:
                        source.seek(max(0, source.seek(0, 2)-16000))
                        counts = re.findall(rb'shares:\s*(\d+) accepted', source.read())
                    count = max(map(int, counts), default=0)
                    if count > accepted:
                        accepted, last_share = count, time.monotonic()
                if time.monotonic()-last_share > config['no_share_seconds']:
                    raise RuntimeError('No accepted shares within timeout')
                time.sleep(2)
    except KeyboardInterrupt:
        reason = 'User stopped'
    except Exception as error:
        reason = str(error)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        job.Close()
        (logs/f'result-{stamp}.json').write_text(json.dumps({'reason': reason, 'seconds': time.monotonic()-started}, indent=2), encoding='utf-8')
        print('STOPPED: '+reason, flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('NOT STARTED: '+str(error), flush=True)
        raise SystemExit(1)

"""Manual GPU takeover with guarded cooling and Afterburner recovery."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import msvcrt
import pynvml as nv
import win32api
import win32event
import win32con
import shutil

ROOT = Path(__file__).resolve().parent
AFTERBURNER = r'C:\Program Files (x86)\MSI Afterburner\MSIAfterburner.exe'

def clock_offset(h, value=None):
    info = nv.c_nvmlClockOffset_t(nv.nvmlClockOffset_v1, nv.NVML_CLOCK_GRAPHICS, 0, 0, 0, 0)
    if value is not None:
        info.clockOffsetMHz = value
    function = 'nvmlDeviceGetClockOffsets' if value is None else 'nvmlDeviceSetClockOffsets'
    result = nv._nvmlGetFunctionPointer(function)(h, ctypes.byref(info))
    nv._nvmlCheckReturn(result)
    return info.clockOffsetMHz

def fan_target(temp):
    points = [(35,40),(50,50),(60,65),(65,80),(69,100)]
    for (low, a), (high, b) in zip(points, points[1:]):
        if temp <= high:
            return max(40, round(a+(temp-low)*(b-a)/(high-low)))
    return 100

def restore(h, state, state_path):
    errors = []
    actions = [lambda: nv.nvmlDeviceResetGpuLockedClocks(h),
               lambda: nv.nvmlDeviceSetPowerManagementLimit(h, state['power_mw'])]
    if state.get('memory_mhz'):
        actions.append(lambda: nv.nvmlDeviceResetMemoryLockedClocks(h))
    if 'original_core_offset' in state:
        actions.append(lambda: clock_offset(h, state['original_core_offset']))
    for action in actions:
        try: action()
        except Exception as exc: errors.append(str(exc))
    for fan in range(nv.nvmlDeviceGetNumFans(h)):
        try: nv.nvmlDeviceSetDefaultFanSpeed_v2(h, fan)
        except Exception as exc: errors.append(str(exc))
    subprocess.Popen([AFTERBURNER, '-Profile1'], creationflags=subprocess.CREATE_NO_WINDOW)
    if errors:
        Path(str(state_path)+'.error').write_text(json.dumps(errors))
        raise RuntimeError('Recovery errors: '+str(errors))
    Path(str(state_path)+'.restored').write_text('GPU locks released; fans automatic; Afterburner Profile1 started')

def device():
    nv.nvmlInit()
    cfg = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig'))
    return nv.nvmlDeviceGetHandleByUUID(cfg['gpus'][0]['uuid'])

def restore_watcher(pid, state_path):
    # Hold an actual process handle, so PID reuse cannot delay recovery.
    parent = win32api.OpenProcess(0x00100000, False, int(pid))
    state = json.loads(Path(state_path).read_text())
    h = device()
    Path(state_path + '.ready').touch()
    win32event.WaitForSingleObject(parent, win32event.INFINITE)
    if Path(state_path + '.restored').exists():
        return
    try:
        restore(h, state, state_path)
    except Exception as exc:
        Path(state_path + '.error').write_text(str(exc))

def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('Use the desktop shortcut and accept Windows administrator prompt.')
    # Distribution safeguard: reject the unconfigured template before touching GPU settings.
    cfg = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig'))
    if cfg.get('validated_for_this_machine') is not True:
        raise RuntimeError('Local inventory, backup and configuration validation required first.')
    lock = (ROOT/'session.lock').open('a+b')
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    h = device()
    if nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU) >= 60:
        raise RuntimeError('GPU must cool below 60 C before starting.')
    original = nv.nvmlDeviceGetPowerManagementLimit(h)
    logs = ROOT/'logs'
    logs.mkdir(exist_ok=True)
    state_path = logs/f'power-{time.time_ns()}.json'
    cfg = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig'))
    state = {'power_mw':original,'pid':os.getpid(),'memory_mhz':cfg.get('memory_mhz',0)}
    offset = cfg.get('core_offset_mhz',0)
    if not 0 <= offset <= 200:
        raise RuntimeError('Core offset exceeds conservative tuning range')
    if offset:
        state['original_core_offset'] = clock_offset(h)
    state_path.write_text(json.dumps(state))
    subprocess.Popen([sys.executable, __file__, '--restore', str(os.getpid()), str(state_path)],
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)
    for _ in range(50):
        if Path(str(state_path)+'.ready').exists():
            break
        time.sleep(.1)
    else:
        raise RuntimeError('Recovery helper did not start; no GPU settings changed.')
    try:
        # Keep the computer awake only while this mining session is alive.
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
            raise RuntimeError('Unable to prevent sleep during mining')
        subprocess.run(['taskkill', '/IM', 'MSIAfterburner.exe', '/F'], capture_output=True)
        time.sleep(1)
        power_w = cfg['gpus'][0]['power_w']
        if not 250 <= power_w <= 275:
            raise RuntimeError('Power limit outside this installation validated tuning range')
        nv.nvmlDeviceSetPowerManagementLimit(h, power_w*1000)
        if nv.nvmlDeviceGetPowerManagementLimit(h) != power_w*1000:
            raise RuntimeError('Power limit did not apply.')
        downclock_c = cfg.get('downclock_c',65)
        if not 60 <= downclock_c <= 67:
            raise RuntimeError('Invalid downclock threshold')
        print(f'RTX 5080 | {power_w} W cap | {downclock_c} C downclock / 70 C stop', flush=True)
        cfg = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig'))
        core = cfg['gpus'][0]['core_mhz']
        if offset:
            clock_offset(h, offset)
            if clock_offset(h) != offset:
                raise RuntimeError('Core offset did not apply')
        nv.nvmlDeviceSetGpuLockedClocks(h, core, core)
        if cfg.get('memory_mhz'):
            nv.nvmlDeviceSetMemoryLockedClocks(h, cfg['memory_mhz'], cfg['memory_mhz'])
        time.sleep(3)
        for fan in range(nv.nvmlDeviceGetNumFans(h)):
            initial_fan = 80 if cfg.get('fan_profile') == 'performance' else 45
            nv.nvmlDeviceSetFanSpeed_v2(h, fan, initial_fan)
            if nv.nvmlDeviceGetTargetFanSpeed(h, fan) != initial_fan:
                raise RuntimeError('Fan control readback failed')
        time.sleep(3)
        print(f'Mining controls GPU: {core} MHz core, automatic guarded fan curve. Close window or Ctrl+C to stop.', flush=True)
        import portable_guard
        print('GPU before launch: '+json.dumps(portable_guard.read_gpus()), flush=True)
        def tick(rows):
            temp = rows[0]['temperature']
            target = fan_target(temp)
            if cfg.get('fan_profile') == 'performance':
                target = max(80, min(100, round(55+(temp-35)*1.5)))
            if temp >= downclock_c:
                current = rows[0]['core']
                nv.nvmlDeviceSetGpuLockedClocks(h, max(600,int(current)-150), max(600,int(current)-150))
            for fan in range(nv.nvmlDeviceGetNumFans(h)):
                nv.nvmlDeviceSetFanSpeed_v2(h, fan, target)
                if abs(nv.nvmlDeviceGetTargetFanSpeed(h, fan)-target)>1:
                    raise RuntimeError('Fan control readback failed')
        portable_guard.TICK = tick
        if '--tune' in sys.argv:
            tune_start = time.monotonic()
            stage = [-1]
            old_tick = tick
            def tuning_tick(rows):
                old_tick(rows)
                candidates = [2400,2700] if cfg.get('memory_mhz') else [1500,1800,2100,2400,2700]
                index = min(len(candidates)-1, int((time.monotonic()-tune_start)/65))
                if index != stage[0] and rows[0]['temperature'] < 63:
                    frequency = candidates[index]
                    nv.nvmlDeviceSetGpuLockedClocks(h, frequency, frequency)
                    print(f'TUNE {time.time():.3f} core={frequency}', flush=True)
                    stage[0] = index
            portable_guard.TICK = tuning_tick
        if '--test' in sys.argv:
            portable_guard.TEST_SECONDS = int(sys.argv[sys.argv.index('--test')+1])
        portable_guard.main()
    finally:
        try:
            restore(h, state, state_path)
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        print('GPU returned to MSI Afterburner Profile1.', flush=True)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--restore':
        restore_watcher(sys.argv[2], sys.argv[3])
    else:
        if '--test' in sys.argv:
            sys.stdout = (ROOT/'test-console.log').open('w', buffering=1)
            sys.stderr = sys.stdout
        try:
            main()
        except Exception as exc:
            print('NOT STARTED / STOPPED: '+str(exc), flush=True)
            sys.exit(1)

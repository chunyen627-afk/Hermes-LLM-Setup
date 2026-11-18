"""Elevated, cancellable panel entry point; no credentials in IPC."""
import json, os, pathlib, subprocess, sys, time, uuid
import win32api, win32event
ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT/'panel-mining-state.json'
STOP = ROOT/'panel-stop.json'

def save(value, *, strict=True):
    """Atomic snapshot; Windows readers may briefly deny rename/delete sharing.

    Display telemetry is best effort. Safety checks and stop requests remain
    independent and fail closed; never let a dashboard refresh stop the miner.
    """
    snapshot = dict(value, updated=time.time())
    tmp = STATE.with_name(f'{STATE.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_text(json.dumps(snapshot), encoding='utf-8')
        for attempt in range(6):
            try:
                tmp.replace(STATE)
                return True
            except PermissionError:
                if attempt == 5: raise
                time.sleep(.02 * 2**attempt)
    except OSError as exc:
        if strict: raise
        print(f'WARNING: dashboard snapshot skipped; mining safety remains active: {exc}', flush=True)
        return False
    finally:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass

def cancelled(owner, started):
    """Only a stop request written after this session started (the console's own 停止, or a mode
    that needs the GPUs) ends mining. The console closing or restarting does not (2026-11-10, the
    user: 「我不想要 AI 控制器關掉會連 OFFICE 挖礦一起停，除非我按停止挖礦」); `owner` is kept
    for the callers' sake and no longer waited on."""
    try: return json.loads(STOP.read_text())['time'] >= started
    except (FileNotFoundError, ValueError, KeyError): return False

def run_script(name, *args):
    subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/name),*args], check=True)

def main():
    action, pid, stamp = sys.argv[1:]
    started = float(stamp)
    try: owner = win32api.OpenProcess(0x100000, False, int(pid))
    except Exception: owner = None  # the console may already be gone: mining runs on its own anyway
    state = dict(token=stamp, status='preparing', action=action)
    try:
        if cancelled(owner, started): return
        save(state)
        # Never change clocks while any miner is still alive.
        check = subprocess.run(['powershell.exe','-NoProfile','-Command',
            "if(Get-Process krig-miner -ErrorAction SilentlyContinue){exit 1}"], check=False)
        if check.returncode: raise RuntimeError('Mining already running; close its existing window first.')
        if action == 'restore':
            run_script('RESTORE-AI-POWER.ps1')
            state['status']='restored'
        else:
            run_script('PREPARE-MINING.ps1','-SkipConsoleStop')
            if cancelled(owner, started): return
            run_script('SET-LOW-CLOCKS.ps1')
            if cancelled(owner, started): return
            os.environ['PEARL_PANEL_OWNER']=pid
            os.environ['PEARL_PANEL_TOKEN']=stamp
            import guarded_miner
            guarded_miner.main()
            state['status']='stopped'
    except Exception as exc:
        state.update(status='error', error=str(exc))
    finally:
        if state['status'] == 'preparing': state['status']='stopped'
        save(state)
        if owner is not None: owner.Close()

if __name__ == '__main__': main()

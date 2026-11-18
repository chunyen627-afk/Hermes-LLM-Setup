"""Started by the scheduled task PearlPanelBridge, which runs with highest privileges, so the AI console
can start mining without a UAC prompt, also when the phone asks for it (the RTX 5080 and the laptop are
started over SSH, which is elevated already).

Runs panel_bridge for the request the console wrote to panel-launch.json, only when it is fresh and its
owner is the running AI console; anything else is dropped. The task takes no arguments, so nothing but
this file decides what runs elevated."""
import json, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent
LAUNCH = ROOT/'panel-launch.json'
LOG = ROOT/'panel-task.log'

def note(text):
    with LOG.open('a', encoding='utf-8') as log:
        log.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text + '\n')

def command_line(pid):
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                           f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine'],
                          capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return done.stdout.strip()

def main():
    try:
        request = json.loads(LAUNCH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return note('no launch request')
    finally:
        LAUNCH.unlink(missing_ok=True)
    action, pid, token = request.get('action'), request.get('pid'), str(request.get('token'))
    try:
        age = time.time() - float(token)
        pid = int(pid)
    except (TypeError, ValueError):
        return note('malformed request dropped')
    if action not in ('mining', 'restore') or not 0 <= age < 60:
        return note(f'request dropped: action {action!r}, age {age:.0f} s')
    owner = command_line(pid)
    if 'media_control.py' not in owner:
        return note(f'request dropped: owner {pid} is not the AI console')
    note(f'{action} for console {pid}')
    sys.argv = [str(ROOT/'panel_bridge.py'), action, str(pid), token]
    import panel_bridge
    panel_bridge.main()

if __name__ == '__main__':
    main()

"""Starts the miner status service (office_aggregator.py) when it is not running.

Run every minute by the scheduled task MinerStatusService (pythonw, so no window flashes). The AI
console also starts the service when it starts mining, but after a reboot the service can exit at
once when the ZeroTier address is not up yet (it binds that address), and nothing else would start
it again. This only launches when no office_aggregator.py process exists, so it never makes a second
copy of the one the console or a manual restart started.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'office_aggregator.py'
PYTHONW = Path(sys.executable).with_name('pythonw.exe')
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def running():
    """Number of processes whose command line names office_aggregator.py (None if unknown)."""
    probe = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         "@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
         "Where-Object { $_.CommandLine -like '*office_aggregator.py*' }).Count"],
        creationflags=NO_WINDOW, capture_output=True, text=True, timeout=60)
    try:
        return int(probe.stdout.strip())
    except ValueError:
        return None


def main():
    count = running()
    if count != 0:  # running, or the check failed: do nothing rather than risk a second copy
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    command = [str(PYTHONW), '-I', str(SCRIPT)]
    try:  # outside the task's job, so it lives on after this script ends
        subprocess.Popen(command, cwd=str(HERE), creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(command, cwd=str(HERE), creationflags=flags)


if __name__ == '__main__':
    main()

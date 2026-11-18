"""Watches the running relay's log and exits (so whoever started it gets notified) when something needs
attention: die temperature at or above --hot, no status line for --stale seconds (FPGA lost its
configuration, relay died), or the hashrate below --min-mhs for three lines in a row.
Prints one summary line every --every seconds while all is well.

  python mining_watch.py [--hot 65] [--stale 60] [--min-mhs 100] [--every 600]
"""
import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / 'logs'
STATUS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ relay INFO ([\d.]+) MHz ([\d.]+) MH/s \| (-?[\d.]+) C '
                    r'[\d.]+ V \| cands \d+ \(hw err (\d+)\) shares \d+ acc (\d+) rej (\d+)')


def last_status(path):
    with open(path, 'rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 20000))
        lines = f.read().decode(errors='replace').splitlines()
    found = [m for m in map(STATUS.match, lines) if m]
    return found[-3:]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hot', type=float, default=65.0)
    ap.add_argument('--stale', type=float, default=60.0)
    ap.add_argument('--min-mhs', type=float, default=100.0)
    ap.add_argument('--every', type=float, default=600.0)
    a = ap.parse_args()
    t_print, t_max = 0.0, -100.0
    while True:
        log = max(LOGS.glob('mining_*.err'), key=lambda p: p.stat().st_mtime)
        st = last_status(log)
        if not st:
            print(f'{time.strftime("%H:%M:%S")} ALERT no status line in {log.name}', flush=True)
            return 1
        m = st[-1]
        age = (datetime.now() - datetime.strptime(m[1], '%Y-%m-%d %H:%M:%S')).total_seconds()
        temp, mhs = float(m[4]), float(m[3])
        t_max = max(t_max, temp)
        line = (f'{m[1]} {m[2]} MHz {mhs:.1f} MH/s {temp:.1f} C (max {t_max:.1f}) hw err {m[5]} '
                f'acc {m[6]} rej {m[7]}')
        if temp >= a.hot:
            print(f'ALERT HOT: {line}', flush=True)
            return 2
        if age > a.stale:
            mark = LOGS / 'mining.stopped'       # written by mining_ctl.py stop (phone app)
            if mark.exists() and mark.stat().st_mtime >= log.stat().st_mtime - 30:
                if time.monotonic() - t_print >= a.every:
                    print(f'{time.strftime("%H:%M:%S")} stopped on purpose ({mark.read_text().strip()}), waiting',
                          flush=True)
                    t_print = time.monotonic()
                time.sleep(15)
                continue
            print(f'ALERT STALE {age:.0f} s (FPGA lost or relay stopped): {line}', flush=True)
            return 3
        if len(st) == 3 and all(float(x[3]) < a.min_mhs for x in st):
            print(f'ALERT LOW RATE: {line}', flush=True)
            return 4
        if time.monotonic() - t_print >= a.every:
            print(line, flush=True)
            t_print = time.monotonic()
        time.sleep(15)


if __name__ == '__main__':
    sys.exit(main())

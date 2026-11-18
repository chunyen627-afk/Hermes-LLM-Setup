"""Records the VCU118's mining every 10 minutes for a day, for the "is a second board worth it" review.

Reads only the miner-status service (the same numbers the AI console shows: pool 3 h hashrate,
QTC price, exchange rate, light) and appends one CSV row per sample; the relay's own log keeps the
share-by-share record. Runs hidden: pythonw observe_day.py [hours]
"""
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

URL = 'http://10.35.219.64:18786/status'
OUT = Path(__file__).resolve().parent.parent / 'logs' / f"observe_{datetime.now():%Y%m%d_%H%M}.csv"
FIELDS = ['time', 'state', 'light', 'title', 'hashrate_mhs', 'temp_c', 'accepted', 'rejected',
          'pool_3h_mhs', 'coin_per_day', 'qtc_usd', 'usd_twd']


def sample():
    request = urllib.request.Request(URL, headers={'X-Miner-Status-Features': 'earnings,assessment,coins'})
    with urllib.request.urlopen(request, timeout=15) as response:
        feed = json.loads(response.read())
    m = next((m for m in feed['machines'] if m['machineId'] == 'vcu118'), None)
    if m is None:
        return {'time': datetime.now().isoformat(timespec='seconds'), 'state': 'missing'}
    earn, verdict = m.get('earnings') or {}, m.get('assessment') or {}
    gpus = m.get('gpus') or [{}]
    return {'time': datetime.now().isoformat(timespec='seconds'), 'state': m['minerRunning']['state'],
            'light': verdict.get('light'), 'title': verdict.get('title'),
            'hashrate_mhs': m['hashrate'].get('value'), 'temp_c': gpus[0].get('temperatureC'),
            'accepted': m['shares'].get('accepted'), 'rejected': m['shares'].get('rejected'),
            'pool_3h_mhs': round(earn['poolHashrate'] / 1e6, 2) if earn.get('poolHashrate') else None,
            'coin_per_day': earn.get('coinPerDay'), 'qtc_usd': earn.get('coinUsd'), 'usd_twd': earn.get('usdTwd')}


def main():
    end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 25) * 3600
    with OUT.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        while time.time() < end:
            try:
                row = sample()
            except Exception as error:  # the service restarting must not end the record
                row = {'time': datetime.now().isoformat(timespec='seconds'), 'state': f'error {type(error).__name__}'}
            writer.writerow(row)
            handle.flush()
            time.sleep(600)


if __name__ == '__main__':
    main()

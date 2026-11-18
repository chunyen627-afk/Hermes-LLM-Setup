"""Nudges a coin's estimate toward what the pool actually credited.

The Kryptex dashboard (logged in, no API) shows each worker's earnings per hour in satoshi; that page's
data is saved as JSON (a list of {"timestamp": ms, "<worker>": "<sats>", ...}) and passed here. The
earnings ledger keeps our uncalibrated estimate per machine per Taipei hour. Over the last 7 days,
the hours a machine was mining (plus the hour after, where late credits land) are summed on both
sides; once there is enough, the factor moves halfway toward web/estimate, and only when they differ
by more than the noise band, so it settles slowly instead of chasing one lucky hour.

A coin mined by several workers (PRL) is fitted on their sum, so the factor is one per coin.

The compact form {"worker": "vcu118", "start": ms, "step": 3600000, "v": [sats, ...]} is accepted too, and
so is {"start": ms, "step": 3600000, "workers": {"llmrig": [sats, ...], ...}} for several workers.

Usage: python calibrate.py <dashboard.json> [coin ...]   (default coin: QTC; the daily task passes PRL QTC)
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import office_aggregator as agg

MIN_SATS = 600      # about 18 hours of the FPGA: less than this is mostly luck
BAND = 0.03         # differences this small are left alone
STEP = 0.5          # move halfway toward the measured ratio
SANE = (0.7, 1.4)   # outside this something is broken (pool not crediting, wrong worker): flag, don't fit


def hour_key(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, agg.TAIPEI).strftime('%Y-%m-%dT%H')


def compare(web_rows, ledger_hours, worker, now, days=7):
    """(web sats, estimated sats, mining hours) over the comparable hours."""
    web = {}
    for row in web_rows:
        try:
            web[hour_key(row['timestamp'])] = float(row.get(worker) or 0)
        except (KeyError, TypeError, ValueError):
            continue
    if not web or not ledger_hours:
        return 0.0, 0.0, 0
    # The dashboard's newest row is the hour still running; the hour before it is the last complete one.
    complete = sorted(web)[:-1]
    if not complete:
        return 0.0, 0.0, 0
    last = complete[-1]

    def after(hour):
        return (datetime.strptime(hour, '%Y-%m-%dT%H') + timedelta(hours=1)).strftime('%Y-%m-%dT%H')

    # The hour the ledger started keeping hours was only partly recorded, and its late credits land in
    # the next one: both are left out.
    start = after(min(ledger_hours))
    oldest = (now - timedelta(days=days)).astimezone(agg.TAIPEI).strftime('%Y-%m-%dT%H')
    mined = [hour for hour in ledger_hours if start < hour < last and hour >= oldest]
    # Credits for the last minutes before a stop land in the hour after it; a run still going past
    # the window keeps its next hour out, as the estimate for it is out too.
    window = set(mined) | {after(hour) for hour in mined if after(hour) not in ledger_hours}
    web_sats = sum(web.get(hour, 0.0) for hour in window)
    est_sats = sum(ledger_hours[hour] for hour in mined) * 1e8
    return web_sats, est_sats, len(mined)


def next_factor(old, web_sats, est_sats):
    """(new factor, note)."""
    if est_sats < MIN_SATS:
        return old, f'too little yet ({est_sats:.0f} of {MIN_SATS} sats estimated)'
    ratio = web_sats / est_sats
    if not SANE[0] <= ratio <= SANE[1]:
        return old, f'WARN ratio {ratio:.3f} outside {SANE}: not applied'
    if abs(ratio - old) <= BAND:
        return old, f'ratio {ratio:.3f} within {BAND:.0%} of {old:.3f}: unchanged'
    new = round(old + STEP * (ratio - old), 4)
    return new, f'ratio {ratio:.3f}: factor {old:.3f} -> {new:.3f}'


def main(argv, now=None, root=agg.ROOT):
    now = now or datetime.now(agg.UTC)
    web_rows = json.loads(Path(argv[0]).read_text(encoding='utf-8'))
    if isinstance(web_rows, dict):  # a compact form: sats per hour from "start" ms, one list per worker
        series = web_rows.get('workers') or {web_rows['worker']: web_rows['v']}
        rows = {}
        for worker, values in series.items():
            for index, value in enumerate(values):
                stamp = web_rows['start'] + index * web_rows['step']
                rows.setdefault(stamp, {'timestamp': stamp})[worker] = value
        web_rows = [rows[stamp] for stamp in sorted(rows)]
    coins = argv[1:] or ['QTC']
    cfg = json.loads((root / 'aggregator.json').read_text(encoding='utf-8-sig'))
    ledger = json.loads((root / 'earnings-ledger.json').read_text(encoding='utf-8'))
    path = root / 'calibration.json'
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    lines = []
    for section in ('earnings', 'earnings_qtc'):
        settings = cfg.get(section) or {}
        coin = settings.get('coin')
        if coin not in coins:
            continue
        web_sats = est_sats = 0.0
        hours = 0
        for machine_id, worker in (settings.get('workers') or {}).items():
            got = compare(web_rows, (ledger.get('hours') or {}).get(machine_id) or {}, worker, now)
            web_sats, est_sats, hours = web_sats + got[0], est_sats + got[1], hours + got[2]
        entry = saved.get(coin) or {'factor': 1.0, 'history': []}
        old = entry.get('factor', 1.0)
        new, note = next_factor(old, web_sats, est_sats)
        entry['factor'] = new
        entry['updated'] = agg.iso(now)
        entry['history'] = (entry.get('history') or [])[-29:] + [{
            'at': agg.iso(now), 'webSats': round(web_sats, 1), 'estimateSats': round(est_sats, 1),
            'miningHours': hours, 'factor': new, 'note': note}]
        saved[coin] = entry
        lines.append(f'{coin}: web {web_sats:.0f} sats, estimate {est_sats:.0f} sats over {hours} h; {note}')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding='utf-8')
    temporary.replace(path)
    return lines


if __name__ == '__main__':
    for line in main(sys.argv[1:]):
        print(line)

"""Summarises profit-log/<machine>.csv (written by office_aggregator.ProfitLog) per day and in total.

    python profit_report.py [machine] [--days N]

Both nets are estimates: income from the pool's per-TH/s estimate and the machine's own hashrate,
electricity from GPU power plus a fixed allowance at the Taipower TOU price of the moment.
"Net if mining stops" assumes the machine stays switched on anyway; "net vs switching it off"
also charges the idle draw, which switching off would save.
"""
import csv
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def value(row, key):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def summarise(rows):
    total = {'seconds': 0.0, 'mining_seconds': 0.0, 'th_seconds': 0.0, 'kwh': 0.0, 'revenue': 0.0, 'cost': 0.0,
             'idle': 0.0, 'missing': 0}
    for row in rows:
        seconds = value(row, 'seconds') or 0.0
        total['seconds'] += seconds
        if row.get('running') == '1':
            total['mining_seconds'] += seconds
            total['th_seconds'] += (value(row, 'hashrate_th') or 0.0) * seconds
        total['kwh'] += (value(row, 'wall_w') or 0.0) * seconds / 3.6e6
        revenue, cost, idle = value(row, 'revenue_twd'), value(row, 'cost_twd'), value(row, 'idle_cost_twd')
        if revenue is None or cost is None:
            total['missing'] += 1
        total['revenue'] += revenue or 0.0
        total['cost'] += cost or 0.0
        total['idle'] += idle or 0.0
    return total


def line(label, t):
    hours = t['seconds'] / 3600
    mining = t['mining_seconds'] / 3600
    average = t['th_seconds'] / t['mining_seconds'] if t['mining_seconds'] else 0.0
    net = t['revenue'] - t['cost']
    return (f"{label:<12} 紀錄 {hours:5.1f} h  挖礦 {mining:5.1f} h  平均 {average:5.1f} TH/s  "
            f"{t['kwh']:5.2f} kWh  收入 NT${t['revenue']:7.2f}  電費 NT${t['cost']:6.2f}  "
            f"淨 NT${net:7.2f}  對比關機 NT${net - t['idle']:7.2f}"
            + (f"  （{t['missing']} 筆缺資料）" if t['missing'] else ''))


def main():
    sys.stdout.reconfigure(encoding='utf-8')  # The Chinese labels garble under a cp950 console otherwise.
    args =[a for a in sys.argv[1:] if not a.startswith('--')]
    machine = args[0] if args else 'laptop3070'
    days = None
    if '--days' in sys.argv:
        days = int(sys.argv[sys.argv.index('--days') + 1])
    path = ROOT / 'profit-log' / f'{machine}.csv'
    with path.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if days is not None and rows:
        cutoff = datetime.fromisoformat(rows[-1]['time']) - timedelta(days=days)
        rows = [r for r in rows if datetime.fromisoformat(r['time']) >= cutoff]
    by_day = OrderedDict()
    for row in rows:
        by_day.setdefault(row['time'][:10], []).append(row)
    for day, day_rows in by_day.items():
        print(line(day, summarise(day_rows)))
    total = summarise(rows)
    print(line('合計', total))
    if total['seconds']:
        scale = 30 * 86400 / total['seconds']
        net = total['revenue'] - total['cost']
        print(f"換算 30 天：收入 NT${total['revenue'] * scale:.0f}、電費 NT${total['cost'] * scale:.0f}、"
              f"淨 NT${net * scale:.0f}、對比關機淨 NT${(net - total['idle']) * scale:.0f}")


if __name__ == '__main__':
    main()

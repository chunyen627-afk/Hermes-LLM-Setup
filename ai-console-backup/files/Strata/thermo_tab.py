# -*- coding: utf-8 -*-
"""The console's 溫濕度 tab (11/19, the user: 「新增一頁跟APP一樣的溫溼度」).

Same source and wording as the toolbox App's 溫濕度 tab: each gateway's GET /sensors (read every
30 s, only while this tab is on screen) and GET /history?id=&range=day|week|month for the chart.
Thermometers are grouped by the room the gateway keeps, with comfort (Mi Home's 20–26 °C,
40–60 %), age and battery; clicking one shows its day line or week/month daily low–high bars.
Names and rooms are set from the phone (they live on the gateway), so this page only reads them.
Hiding (right-click a row, or the box under the chart) is kept on this PC only, as the phone
keeps its own; hidden ones sit in a folded 已隱藏（n） group at the bottom.
"""
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from tkinter import ttk

# The App's gateway list (one per line in its settings): the office PC's gateway and, through the
# home PC's port forward, the ESP32 at home. Both answer only inside the ZeroTier network.
GATEWAYS = [('公司', 'http://10.35.219.64:18787'), ('家裡', 'http://10.35.219.158:18787')]
POLL_S = 30
HISTORY_S = 300
COMFORT = {'temperature': (20.0, 26.0), 'humidity': (40.0, 60.0)}   # ThermoComfortZone.standard
NO_ROOM = '未指定房間'
RANGES = [('day', '日'), ('week', '週'), ('month', '月')]
FONT = 'Microsoft JhengHei'
SETTINGS = Path(__file__).resolve().parent / 'thermo-tab.json'   # {"hidden": [address, ...]}


def load_hidden(path=SETTINGS):
    try:
        return set(json.loads(path.read_text(encoding='utf-8')).get('hidden') or [])
    except (OSError, ValueError, AttributeError):
        return set()


def save_hidden(hidden, path=SETTINGS):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps({'hidden': sorted(hidden)}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def get_json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def comfort_text(temperature, humidity):
    """'舒適', or what is off, joined with '・' as the App (ThermoComfort.level)."""
    if temperature is None and humidity is None:
        return None
    parts = []
    if temperature is not None:
        if temperature < COMFORT['temperature'][0]: parts.append('偏冷')
        if temperature > COMFORT['temperature'][1]: parts.append('偏熱')
    if humidity is not None:
        if humidity < COMFORT['humidity'][0]: parts.append('乾燥')
        if humidity > COMFORT['humidity'][1]: parts.append('潮濕')
    return '・'.join(parts) if parts else '舒適'


def age_text(updated, now):
    if not updated:
        return '尚無讀數'
    age = max(0, now - updated)
    if age < 60: return '剛剛'
    if age < 3600: return f'{age // 60} 分鐘前'
    if age < 86400: return f'{age // 3600} 小時前'
    return f'{age // 86400} 天前'


def freshness(updated, now):
    if not updated: return 'never'
    age = max(0, now - updated)
    return 'fresh' if age <= 600 else 'stale' if age <= 3600 else 'lost'


def fmt_temp(value):
    return '--' if value is None else f'{value:.1f}°'


def fmt_hum(value):
    return '--' if value is None else f'{value:.0f}%'


def battery_low(value):
    return value is not None and value < 20


def by_room(rows):
    """[(room, [row...])]: named rooms sorted, then the unassigned ones; rows sorted by name."""
    groups = {}
    for row in sorted(rows, key=lambda r: (r['sensor'].get('name') or '', r['sensor']['id'])):
        groups.setdefault(row['sensor'].get('room') or NO_ROOM, []).append(row)
    named = sorted(room for room in groups if room != NO_ROOM)
    return [(room, groups[room]) for room in named + ([NO_ROOM] if NO_ROOM in groups else [])]


def summary(points, metric):
    lows = [p.get(metric + '_min', p.get(metric)) for p in points]
    highs = [p.get(metric + '_max', p.get(metric)) for p in points]
    means = [p.get(metric) for p in points if p.get(metric) is not None]
    lows, highs = [v for v in lows if v is not None], [v for v in highs if v is not None]
    if not means or not lows or not highs:
        return None
    return min(lows), max(highs), sum(means) / len(means)


class ThermoTab:
    def __init__(self, panel, frame, tab_index):
        self.panel, self.root, self.tab_index = panel, panel.root, tab_index
        self.active = False
        self.feeds = {}            # gateway label -> {'feed':..., 'at': local time} or {'error':...}
        self.results, self.history_results = queue.Queue(), queue.Queue()
        self.history_wanted = queue.Queue()
        self.selected = None       # (gateway label, sensor id)
        self.range, self.metric = tk.StringVar(value='day'), tk.StringVar(value='temperature')
        self.history, self.history_key, self.history_at, self.history_error = None, None, 0.0, None
        self.next_poll = 0.0
        self.hidden, self.show_hidden = load_hidden(), False

        ttk.Label(frame, text='溫濕度', font=(FONT, 20, 'bold')).pack(anchor='w')
        panel.wrapping(ttk.Label(frame, text='跟手機 App 的「溫濕度」分頁同一個來源（公司閘道、家裡閘道），只在這一頁開著時'
                                             '每 30 秒更新。點一顆看日／週／月紀錄；按右鍵可隱藏（只存在這台電腦）；'
                                             '名稱和房間在手機上改。')).pack(anchor='w', pady=(7, 8))
        self.status = tk.StringVar(value='切到這一頁後開始讀取…')
        panel.wrapping(ttk.Label(frame, textvariable=self.status, foreground='#374151')).pack(anchor='w', pady=(0, 6))
        self.notices = ttk.Frame(frame); self.notices.pack(anchor='w', fill='x')
        self.list = ttk.Frame(frame); self.list.pack(anchor='w', fill='x')
        self.detail = ttk.Frame(frame); self.detail.pack(anchor='w', fill='x', pady=(14, 0))
        threading.Thread(target=self.read_sensors, name='thermo-sensors', daemon=True).start()
        threading.Thread(target=self.read_history, name='thermo-history', daemon=True).start()
        self.root.after(300, self.update)

    # Worker threads: network only; results go back through queues.
    def read_sensors(self):
        while True:
            if not self.active or time.monotonic() < self.next_poll:
                time.sleep(0.3)
                continue
            self.next_poll = time.monotonic() + POLL_S
            for label, base in GATEWAYS:
                try:
                    self.results.put((label, base, {'feed': get_json(base + '/sensors'), 'at': time.time()}))
                except Exception as error:
                    reason = '閘道沒回應' if isinstance(error, (TimeoutError, OSError)) else str(error)
                    self.results.put((label, base, {'error': reason}))

    def read_history(self):
        while True:
            key = self.history_wanted.get()
            label, sensor_id, range_name = key
            base = dict(GATEWAYS)[label]
            try:
                data = get_json(f'{base}/history?id={sensor_id}&range={range_name}', timeout=15)
                self.history_results.put((key, data, None))
            except Exception as error:
                self.history_results.put((key, None, '讀不到紀錄：' + str(error)))

    # Tk thread
    def update(self):
        try:
            try:
                current = self.panel.tabs.index('current')
            except tk.TclError:
                current = -1
            was = self.active
            self.active = (current == self.tab_index and self.root.state() != 'iconic'
                           and not getattr(self.panel, 'closed', False))
            if self.active and not was:
                self.next_poll = 0.0          # read at once when the user comes back to the tab
            changed = False
            while True:
                try:
                    label, base, result = self.results.get_nowait()
                except queue.Empty:
                    break
                self.feeds[label] = dict(result, base=base)
                changed = True
            while True:
                try:
                    key, data, error = self.history_results.get_nowait()
                except queue.Empty:
                    break
                if key == self.history_key:
                    self.history, self.history_error, self.history_at = data, error, time.monotonic()
                    changed = True
            if self.active and self.selected and time.monotonic() - self.history_at > HISTORY_S:
                self.ask_history()
            if changed:
                self.render()
        except Exception as error:
            self.status.set('溫濕度分頁錯誤：' + str(error))
        self.root.after(300, self.update)

    def rows(self):
        out = []
        for label, _ in GATEWAYS:
            entry = self.feeds.get(label) or {}
            feed = entry.get('feed')
            if not feed:
                continue
            now = int(feed.get('time') or 0) + int(time.time() - entry['at'])
            for sensor in feed.get('sensors') or []:
                out.append({'gateway': label, 'title': feed.get('gateway') or label, 'sensor': sensor, 'now': now})
        return out

    def render(self):
        for child in list(self.notices.winfo_children()) + list(self.list.winfo_children()):
            child.destroy()
        wrap = self.panel.wrap
        stamp = None
        for label, _ in GATEWAYS:
            entry = self.feeds.get(label) or {}
            if entry.get('error'):
                ttk.Label(self.notices, text=f"⚠ {label}閘道：{entry['error']}" + ('（家裡電腦關機時看不到）' if label == '家裡' else ''),
                          foreground='#b42318', wraplength=wrap).pack(anchor='w')
            elif entry.get('at'):
                stamp = max(stamp or 0, entry['at'])
        rows = self.rows()
        visible = [r for r in rows if r['sensor']['id'] not in self.hidden]
        hidden = [r for r in rows if r['sensor']['id'] in self.hidden]
        low = [r['sensor'].get('name') or r['sensor']['id'] for r in visible if battery_low(r['sensor'].get('battery'))]
        if low:
            ttk.Label(self.notices, text='🔋 ' + '、'.join(low) + ' 電量不足，請更換電池',
                      foreground='#b42318', wraplength=wrap).pack(anchor='w')
        self.status.set(f'{len(visible)} 顆溫濕度計' + (f'（另有 {len(hidden)} 顆隱藏）' if hidden else '') +
                        f'；最後讀取 {datetime.fromtimestamp(stamp).strftime("%H:%M:%S")}，每 30 秒更新。'
                        if stamp else '正在讀取閘道…')
        for room, items in by_room(visible):
            ttk.Label(self.list, text=room, font=(FONT, 11, 'bold'), foreground='#6b7280').pack(anchor='w', pady=(10, 2))
            for row in items:
                self.row(row)
        if hidden:                               # folded like the App's 已隱藏（n） group
            toggle = ttk.Label(self.list, text=('▾ ' if self.show_hidden else '▸ ') + f'已隱藏（{len(hidden)}）',
                               font=(FONT, 11, 'bold'), foreground='#6b7280', cursor='hand2')
            toggle.pack(anchor='w', pady=(14, 2))
            toggle.bind('<Button-1>', lambda _e: self.fold())
            if self.show_hidden:
                for row in sorted(hidden, key=lambda r: (r['sensor'].get('name') or '', r['sensor']['id'])):
                    self.row(row)
        self.render_detail(rows)

    def fold(self):
        self.show_hidden = not self.show_hidden
        self.render()

    def set_hidden(self, sensor_id, hide):
        (self.hidden.add if hide else self.hidden.discard)(sensor_id)
        try:
            save_hidden(self.hidden)
        except OSError as error:
            self.status.set('隱藏設定存不進去：' + str(error))
        self.render()

    def menu(self, event, sensor_id):
        hidden = sensor_id in self.hidden
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label='取消隱藏' if hidden else '在列表中隱藏',
                         command=lambda: self.set_hidden(sensor_id, not hidden))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def row(self, row):
        sensor, now = row['sensor'], row['now']
        key = (row['gateway'], sensor['id'])
        bg = '#e8eefc' if key == self.selected else '#ffffff'
        box = tk.Frame(self.list, bg=bg, highlightthickness=1, highlightbackground='#e5e7eb', padx=12, pady=6)
        box.pack(anchor='w', fill='x', pady=2)
        left = tk.Frame(box, bg=bg); left.pack(side='left', fill='x', expand=True)
        right = tk.Frame(box, bg=bg); right.pack(side='right')
        tk.Label(left, text=sensor.get('name') or sensor['id'], font=(FONT, 13, 'bold'), bg=bg, anchor='w').pack(anchor='w')
        comfort = comfort_text(sensor.get('temperature'), sensor.get('humidity'))
        if comfort:
            tk.Label(left, text=comfort, font=(FONT, 10), bg=bg,
                     fg='#15803d' if comfort == '舒適' else '#c2410c').pack(anchor='w')
        fresh = freshness(sensor.get('updated'), now)
        age_color = {'fresh': '#6b7280', 'stale': '#c2410c'}.get(fresh, '#b42318')
        line = tk.Frame(left, bg=bg); line.pack(anchor='w')
        tk.Label(line, text=('🕒 ' if fresh == 'fresh' else '⚠ ') + age_text(sensor.get('updated'), now),
                 font=(FONT, 10), fg=age_color, bg=bg).pack(side='left')
        if sensor.get('battery') is not None:
            tk.Label(line, text=f"　🔋 {sensor['battery']}%", font=(FONT, 10), bg=bg,
                     fg='#b42318' if battery_low(sensor['battery']) else '#6b7280').pack(side='left')
        if sensor.get('error'):
            tk.Label(left, text=sensor['error'], font=(FONT, 10), fg='#b42318', bg=bg).pack(anchor='w')
        tk.Label(right, text=fmt_temp(sensor.get('temperature')), font=(FONT, 24, 'bold'), bg=bg,
                 fg='#111827' if fresh == 'fresh' else '#9ca3af').pack(anchor='e')
        tk.Label(right, text='💧 ' + fmt_hum(sensor.get('humidity')), font=(FONT, 12), fg='#2563eb', bg=bg).pack(anchor='e')
        for widget in [box, left, right, line] + list(left.winfo_children()) + list(right.winfo_children()) + list(line.winfo_children()):
            widget.bind('<Button-1>', lambda _e, k=key: self.select(k))
            widget.bind('<Button-3>', lambda e, s=sensor['id']: self.menu(e, s))
            widget.configure(cursor='hand2')

    def select(self, key):
        self.selected = None if key == self.selected else key
        self.history, self.history_error = None, None
        if self.selected:
            self.ask_history()
        self.render()

    def ask_history(self):
        self.history_key = (self.selected[0], self.selected[1], self.range.get())
        self.history_at = time.monotonic()       # one request at a time; the answer refreshes it again
        self.history_wanted.put(self.history_key)

    def choose(self):
        self.history, self.history_error = None, None
        if self.selected:
            self.ask_history()
        self.render()

    def render_detail(self, rows):
        for child in self.detail.winfo_children():
            child.destroy()
        found = next((r for r in rows if (r['gateway'], r['sensor']['id']) == self.selected), None)
        if not found:
            if rows:
                ttk.Label(self.detail, text='點上面任一顆看它的紀錄曲線。', foreground='#6b7280').pack(anchor='w')
            return
        sensor, now = found['sensor'], found['now']
        ttk.Label(self.detail, text=sensor.get('name') or sensor['id'], font=(FONT, 16, 'bold')).pack(anchor='w')
        bar = ttk.Frame(self.detail); bar.pack(anchor='w', pady=(4, 4))
        for value, text in RANGES:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.range, command=self.choose).pack(side='left', padx=(0, 6))
        ttk.Label(bar, text='　').pack(side='left')
        for value, text in (('temperature', '溫度'), ('humidity', '濕度')):
            ttk.Radiobutton(bar, text=text, value=value, variable=self.metric, command=self.render).pack(side='left', padx=(0, 6))
        metric = self.metric.get()
        unit = '°' if metric == 'temperature' else '%'
        points = (self.history or {}).get('points') or []
        if self.history_error:
            ttk.Label(self.detail, text=self.history_error, foreground='#b42318').pack(anchor='w')
        elif self.history is None:
            ttk.Label(self.detail, text='讀取紀錄中…', foreground='#6b7280').pack(anchor='w')
        elif not points:
            ttk.Label(self.detail, text='這段期間沒有紀錄。', foreground='#6b7280').pack(anchor='w')
        else:
            stats = summary(points, metric)
            if stats:
                ttk.Label(self.detail, text=f'最低 {stats[0]:.1f}{unit}　最高 {stats[1]:.1f}{unit}　平均 {stats[2]:.1f}{unit}',
                          font=(FONT, 11)).pack(anchor='w')
            self.chart(points, metric, unit)
        info = [f"最後更新 {age_text(sensor.get('updated'), now)}",
                f"電量 {sensor.get('battery', '--')}%",
                f"電壓 {sensor['voltage']:.3f} V" if sensor.get('voltage') is not None else None,
                f"訊號 {sensor['rssi']} dBm" if sensor.get('rssi') is not None else None,
                f"閘道 {found['title']}", f"位址 {sensor['id']}"]
        # plain wraplength: these labels are rebuilt on every refresh, so they must not join panel.wrapped
        ttk.Label(self.detail, text='　'.join(x for x in info if x), foreground='#6b7280',
                  wraplength=self.panel.wrap).pack(anchor='w', pady=(6, 0))
        if battery_low(sensor.get('battery')):
            ttk.Label(self.detail, text='電量不足，請更換 CR2032 電池。', foreground='#b42318').pack(anchor='w')
        hide = tk.BooleanVar(value=sensor['id'] in self.hidden)
        self.hide_var = hide                     # keep a reference while the checkbox lives
        ttk.Checkbutton(self.detail, text='在列表中隱藏（只存在這台電腦）', variable=hide,
                        command=lambda: self.set_hidden(sensor['id'], hide.get())).pack(anchor='w', pady=(6, 0))

    def chart(self, points, metric, unit):
        width, height = max(360, min(self.panel.wrap, 900)), 240
        left, right, top, bottom = 48, 12, 10, 28
        canvas = tk.Canvas(self.detail, width=width, height=height, bg='#ffffff', highlightthickness=1,
                           highlightbackground='#e5e7eb')
        canvas.pack(anchor='w', pady=(4, 0))
        daily = (self.history or {}).get('range') != 'day'
        lows = [p.get(metric + '_min', p.get(metric)) for p in points]
        highs = [p.get(metric + '_max', p.get(metric)) for p in points]
        values = [v for v in lows + highs if v is not None]
        if not values:
            return
        lo, hi = min(values), max(values)
        pad = max(0.5, (hi - lo) * 0.15)
        lo, hi = lo - pad, hi + pad
        t0, t1 = points[0]['t'], points[-1]['t']
        bucket = (self.history or {}).get('bucket') or 1800
        span = max(bucket, t1 - t0 + (bucket if daily else 0))
        x = lambda t: left + (t - t0) / span * (width - left - right)
        y = lambda v: top + (hi - v) / (hi - lo) * (height - top - bottom)
        band = COMFORT[metric]                                   # the comfortable range, shaded
        if band[1] > lo and band[0] < hi:
            canvas.create_rectangle(left, y(min(band[1], hi)), width - right, y(max(band[0], lo)), fill='#ecfdf3', outline='')
        for i in range(5):
            v = lo + (hi - lo) * i / 4
            canvas.create_line(left, y(v), width - right, y(v), fill='#f3f4f6')
            canvas.create_text(left - 6, y(v), text=f'{v:.0f}{unit}' if hi - lo > 4 else f'{v:.1f}{unit}',
                               anchor='e', fill='#6b7280', font=(FONT, 8))
        color = '#ea580c' if metric == 'temperature' else '#2563eb'
        if daily:
            step = (width - left - right) * bucket / span        # one day's width, missing days left empty
            for p, low, high in zip(points, lows, highs):
                if low is None or high is None:
                    continue
                cx = x(p['t'] + bucket / 2)
                canvas.create_line(cx, y(high), cx, y(low), fill=color, width=max(3, min(14, step * 0.5)), capstyle='round')
            every = 1 if len(points) <= 8 else 5
            for i, p in enumerate(points):
                if i % every == 0:
                    canvas.create_text(x(p['t'] + bucket / 2), height - 14, fill='#6b7280', font=(FONT, 8),
                                       text=datetime.fromtimestamp(p['t']).strftime('%m/%d').lstrip('0'))
        else:
            segment = []
            for i, p in enumerate(points):           # a gap of more than two buckets breaks the line
                if p.get(metric) is None or (segment and p['t'] - points[i - 1]['t'] > 2 * bucket):
                    if len(segment) > 1: canvas.create_line(*segment, fill=color, width=2, smooth=True)
                    segment = []
                if p.get(metric) is not None:
                    segment += [x(p['t']), y(p[metric])]
            if len(segment) > 1: canvas.create_line(*segment, fill=color, width=2, smooth=True)
            for p in points:
                stamp = datetime.fromtimestamp(p['t'])
                if stamp.minute == 0 and stamp.hour % 4 == 0:
                    canvas.create_text(x(p['t']), height - 14, text=stamp.strftime('%H:00'), fill='#6b7280', font=(FONT, 8))

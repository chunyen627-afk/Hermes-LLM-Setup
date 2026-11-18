# -*- coding: utf-8 -*-
"""The console's 鏡頭 tab (11/19, the user: 「AI控制台新增一頁跟APP一樣看鏡頭即時畫面的功能」).

Same source as the phone's 看鏡頭 page: the desk helper's GET /v1/camera/frame (one JPEG per request,
?clear=1 for the clear picture) and GET /v1/light?days=1 for the watched light. Frames are asked for
only while this tab is the selected one and the window is not minimised, so the helper closes the
camera about 5 s after the user leaves the tab, exactly as when the phone leaves its page.
The helper's address and password are read from its own config at runtime; none is kept here.
"""
import io
import json
import queue
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import ttk

HELPER_CONFIG = Path.home() / 'AppData' / 'Local' / 'DeskRemote' / 'config.json'
LIGHT_EVERY = 30.0      # seconds between light readings while the tab is open, as on the phone
MAX_WIDTH = 960         # the clear picture's long side; never scaled up beyond it


def helper_address():
    """(base url, headers) of this PC's desk helper, or raises with a readable reason."""
    try:
        cfg = json.loads(HELPER_CONFIG.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise RuntimeError('找不到桌面遙控小幫手的設定，這台電腦沒裝小幫手')
    host = cfg.get('bind_ip') or '127.0.0.1'
    return f"http://{host}:{int(cfg.get('port', 18790))}", {'Authorization': 'Bearer ' + cfg['token']}


def fetch(path, timeout=15):
    base, headers = helper_address()
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get('error', {}).get('message')
        except Exception:
            message = None
        raise RuntimeError(message or f'小幫手回應 {error.code}')
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('連不到桌面遙控小幫手（小幫手沒在執行？）')


def light_headline(light):
    """The same wording as the phone's DeskLightPanel."""
    if not light.get('watching'):
        state = '非監看時段'
    elif light.get('on') is not None:
        state = '亮著' if light['on'] else '暗的'
    else:
        state = light.get('error') or '判斷中'
    line = f"{light.get('name', '燈光')}：{state}"
    if light.get('watching') and light.get('level') is not None:
        line += f"（亮度 {round(light['level'])}）"
    since = light.get('since') or ''
    if light.get('watching') and light.get('on') is not None and len(since) >= 16:
        line += f'，自 {since[11:16]}'
    if light.get('on') is not None and light.get('error'):
        line += f"（{light['error']}）"
    return line


def light_today(light):
    hours = light.get('hours') or []
    window = (f"{'上班日 ' if light.get('weekdaysOnly') else ''}{hours[0]}:00–{hours[1]}:00 監看"
              if len(hours) == 2 else '')
    days = light.get('days') or []
    if not days:
        return window
    changes = days[0].get('changes') or []
    if not changes:
        return '今天還沒有變化' + ('；' + window if window else '')
    text = '、'.join(f"{c.get('time', '')[:5]} {'亮' if c.get('on') else '暗'}" for c in changes)
    return '今天：' + text + ('；' + window if window else '')


class CameraTab:
    def __init__(self, panel, frame, tab_index):
        self.panel, self.root, self.tab_index = panel, panel.root, tab_index
        self.active = False            # read by the worker threads; set only on the Tk thread
        self.clear = tk.BooleanVar(value=False)
        self.clear_on = False          # the checkbox, copied for the worker (Tk variables are Tk-thread only)
        self.frames, self.light = queue.Queue(maxsize=2), queue.Queue()
        self.recent, self.last_bytes, self.frame_at, self.count = [], 0, None, 0
        self.error, self.photo = None, None
        ttk.Label(frame, text='辦公室鏡頭', font=('Microsoft JhengHei', 20, 'bold')).pack(anchor='w')
        panel.wrapping(ttk.Label(frame, text='跟手機 App 的「看鏡頭」同一個來源。鏡頭只在這一頁開著時開啟，'
                                             '切到別頁或縮小視窗約 5 秒後就關掉。')).pack(anchor='w', pady=(7, 8))
        row = ttk.Frame(frame); row.pack(anchor='w', fill='x', pady=(0, 6))
        ttk.Checkbutton(row, text='清晰（約 34 KB／張；不勾＝省流量約 15 KB）', variable=self.clear).pack(side='left')
        self.status = tk.StringVar(value='省流量模式。鏡頭只在這一頁開著時開啟，離開這頁就關掉。')
        panel.wrapping(ttk.Label(frame, textvariable=self.status, foreground='#374151')).pack(anchor='w', pady=(0, 6))
        self.light_line = tk.StringVar(value='')
        self.light_label = ttk.Label(frame, textvariable=self.light_line, font=('Microsoft JhengHei', 12, 'bold'))
        self.light_label.pack(anchor='w')
        self.light_days = tk.StringVar(value='')
        panel.wrapping(ttk.Label(frame, textvariable=self.light_days, foreground='#6b7280')).pack(anchor='w', pady=(0, 8))
        self.picture = ttk.Label(frame, text='切到這一頁後開始讀鏡頭…', foreground='#6b7280')
        self.picture.pack(anchor='w')
        threading.Thread(target=self.read_frames, name='camera-frames', daemon=True).start()
        threading.Thread(target=self.read_light, name='camera-light', daemon=True).start()
        self.root.after(200, self.update)

    # Worker threads: only network work, results go through the queues.
    def read_frames(self):
        while True:
            if not self.active:
                time.sleep(0.3)
                continue
            try:
                data = fetch('/v1/camera/frame' + ('?clear=1' if self.clear_on else ''))
                try:
                    self.frames.put_nowait(('frame', data))
                except queue.Full:
                    pass
            except Exception as error:
                self.frames.put(('error', str(error)))
                time.sleep(3)

    def read_light(self):
        next_at = 0.0
        while True:
            if not self.active or time.monotonic() < next_at:
                time.sleep(0.5)
                continue
            try:
                self.light.put(json.loads(fetch('/v1/light?days=1')))
                next_at = time.monotonic() + LIGHT_EVERY
            except Exception:
                next_at = time.monotonic() + 60    # no light watch on this PC, or the helper is restarting

    # Tk thread
    def update(self):
        try:
            self.active = (self.tabs_current() == self.tab_index and self.root.state() != 'iconic'
                           and not getattr(self.panel, 'closed', False))
            self.clear_on = bool(self.clear.get())
            latest = None
            while True:
                try:
                    kind, value = self.frames.get_nowait()
                except queue.Empty:
                    break
                if kind == 'frame':
                    latest = value
                else:
                    self.error = value
            if latest:
                self.show(latest)
            while True:
                try:
                    light = self.light.get_nowait()
                except queue.Empty:
                    break
                self.light_line.set('● ' + light_headline(light))
                on = light.get('watching') and light.get('on') is True
                self.light_label.configure(foreground='#c2410c' if on else '#111827')
                self.light_days.set(light_today(light))
            self.status.set(self.status_text())
        except Exception as error:
            self.status.set('鏡頭分頁錯誤：' + str(error))
        self.root.after(100, self.update)

    def tabs_current(self):
        try:
            return self.panel.tabs.index('current')
        except tk.TclError:
            return -1

    def show(self, data):
        from PIL import Image, ImageTk
        image = Image.open(io.BytesIO(data))
        width = min(MAX_WIDTH, max(320, self.panel.wrap))
        if image.width != width:
            image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)      # keep a reference, or Tk shows nothing
        self.picture.configure(image=self.photo, text='')
        now = time.time()
        self.count += 1
        self.frame_at, self.last_bytes, self.error = now, len(data), None
        self.recent = [t for t in self.recent if now - t <= 3] + [now]

    def status_text(self):
        mode = '清晰' if self.clear.get() else '省流量'
        if self.error and (self.frame_at is None or time.time() - self.frame_at > 5):
            return f'{mode}：{self.error}'
        if not self.active:
            return f'{mode}模式。鏡頭只在這一頁開著時開啟，離開這頁就關掉。'
        if self.frame_at is None:
            return '正在打開鏡頭…'
        per_second = len([t for t in self.recent if time.time() - t <= 3]) / 3
        stamp = datetime.fromtimestamp(self.frame_at).strftime('%H:%M:%S')
        return (f'{mode}：每秒約 {per_second:.1f} 張、每張約 {round(self.last_bytes / 1024)} KB，'
                f'最新畫面 {stamp}；離開這頁鏡頭就關。')

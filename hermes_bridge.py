import http.server, json, os, re, subprocess, sys, threading, time, urllib.error, urllib.request

GPU = sys.argv[1] if len(sys.argv) > 1 else '10.35.219.64'
UP = f'http://{GPU}:8001'

# 每幾次推論印一行 context 用量；傳 0 可關掉。
# 用次數不用秒數 —— 閒著的時候不該一直洗版面，有在跑才需要看 ctx。
WATCH = int(sys.argv[2]) if len(sys.argv) > 2 else 10
# 單輪輸出上限，防止模型卡在自我重複時燒掉幾十分鐘 GPU。
# 正常回合幾百到幾千 token 就夠，8192 綽綽有餘。
MAX_OUT = 8192
# 閒置多久之後開始報「沒有新請求」。橋接器只在有請求時才印東西，
# 停了就整片安靜 —— 分不出「還在想」和「已經停了」。
IDLE_REPORT_SEC = 300
_last_req = 0.0
_idle_reported = False

# 「模型還在推理」這種回報不需要每 30 秒一次 —— 長任務會洗版。
# 間隔從 BUSY_REPORT_MIN 開始，每印一次就加倍，最多 BUSY_REPORT_MAX。
# 注意 IDLE_REPORT_SEC 也要夠長，否則「第一則」還是會太早出現。
BUSY_REPORT_MIN = 300      # 5 分鐘
BUSY_REPORT_MAX = 900      # 15 分鐘
_busy_gap = BUSY_REPORT_MIN
_busy_next = 0.0

# 停止偵測本來是一次性的：報過一次 [STOP] 之後就靠旗標壓住，不再判斷。
# 問題是那一刻 autoguard 若因故沒接（防呆擋下、或它自己出錯），
# 時機就過了 —— 之後任務永遠停在那裡等人。
# 改成定期重試：只要還是閒置狀態，每 RETRY_STOP_SEC 再問一次 autoguard。
RETRY_STOP_SEC = 600       # 10 分鐘重試一次
RETRY_STOP_MAX = 6         # 最多重試幾次（之後就真的等人）
_stop_retry_next = 0.0
_stop_retry_n = 0

# 偵測到任務停掉時，要不要自動接續。
# 預設關閉 —— 這會在你不在場時執行外部程式跑新任務，判斷錯就會亂跑。
# 要開：設環境變數 HERMES_AUTORESUME=1（或桌面的「自動接續」捷徑）
AUTORESUME = os.environ.get('HERMES_AUTORESUME', '') == '1'
AUTORELAY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '_autorelay.py')
_autoresume_count = 0
AUTORESUME_MAX = 5          # 最多自動接續幾次，防無限迴圈
# 圖片是否要先繞道 Gemini 轉成文字。模型掛了 mmproj 之後應該是 False，
# 否則圖會在這裡被抽掉，本地視覺等於沒開。
STRIP_IMAGES = False
_TOOLS_REPORTED = False

# Hermes 會探測這些 Ollama / LM Studio 風格的端點，llama-server 沒有這些路徑。
# 直接回空清單，不往上游打，也不印紅字。
PROBE_PATHS = ('/api/v1/models', '/api/tags', '/api/version',
               '/v1/props', '/props', '/api/v0/models')

_n = 0


def _gpu():
    """讀三張卡的 VRAM 與使用率。只在 GPU 本機跑得到，遠端就略過。"""
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.used,utilization.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=6).stdout.strip()
        rows = [r.split(',') for r in out.splitlines() if r.strip()]
        used = [int(r[0]) for r in rows]
        util = [int(r[1]) for r in rows]
        return sum(used), '/'.join(str(u) for u in used), max(util) if util else 0
    except Exception:
        return None, None, None


def _watch_once():
    """讀一次 /slots 印出 context 用量。壞掉不能影響轉發。"""
    if True:
        try:
            with urllib.request.urlopen(UP+'/slots', timeout=8) as r:
                _all = json.loads(r.read().decode('utf-8')) or [{}]
            # 共享模式有多個 slot，正在跑的不一定是 slot 0 ——
            # 挑真正在用的那個，沒人在跑就挑吃最多 ctx 的（剛跑完那個）。
            _busy = [x for x in _all if x.get('is_processing')]
            s = max(_busy or _all, key=lambda x: x.get('n_prompt_tokens') or 0)
            _idx = _all.index(s)
            n = s.get('n_ctx') or 0
            used = s.get('n_prompt_tokens') or 0
            cache = s.get('n_prompt_tokens_cache') or 0
            proc = s.get('n_prompt_tokens_processed') or 0
            pct = used * 100 // max(n, 1)
            hit = cache * 100 // max(used, 1)
            busy = bool(s.get('is_processing'))
            state = '推理中' if busy else '閒置'
            _tag = f'slot{_idx} ' if len(_all) > 1 else ''
            line = (f'[ctx ] {_tag}{used:,} / {n:,} ({pct}%)  剩 {n-used:,}  '
                    f'快取 {hit}%  重算 {proc:,}  {state}')

            task = s.get('id_task')
            if task is not None:
                line += f'  task#{task}'

            vram_sum, vram_each, gpu_util = _gpu()
            if vram_sum is not None:
                line += f'  VRAM {vram_sum/1024:.1f}G ({vram_each})  GPU {gpu_util}%'

            print(line, flush=True)

            # 今天查出來的三個拖慢徵兆，出現就明講
            warn = []
            if busy and used and proc > 1000 and hit < 60 and proc < used * 0.9:
                warn.append(f'快取只有 {hit}% —— 可能有第二個對話在搶 slot')
            if busy and proc > 20000 and proc < used * 0.9:
                warn.append(f'重算 {proc:,} tokens —— 約 {proc//490} 秒純等待')
            # 壓縮觸發點（Hermes 的 compression.threshold 設 0.8）
            trigger = int(n * 0.8)
            if used >= trigger:
                warn.append(f'★ 已過壓縮觸發點 {trigger:,} —— 下次請求會壓縮，'
                            f'那一次要等 2-5 分鐘，不是當機')
            elif trigger - used < 10000:
                warn.append(f'★ 離壓縮觸發點只剩 {trigger-used:,} tokens')
            elif pct > 85:
                warn.append(f'ctx 用了 {pct}% —— 快滿了')
            if warn:
                print('       ⚠ ' + ' | '.join(warn), flush=True)

        except Exception as e:
            print(f'[ctx ] 讀不到用量: {e}', flush=True)



# === Gemini 看圖 ==========================================================
# llama-server 沒掛 mmproj（掛了會爆 VRAM），所以圖片在這裡先轉成文字。
# 手機 App 直連 :1234 就有視覺能力，本地模型完全不知道有圖這回事。
# 只從環境變數讀，不要寫死 —— 這支檔案會同步到公開倉庫。
# 設定：[Environment]::SetEnvironmentVariable('GEMINI_API_KEY','...','User')
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = 'gemini-2.5-flash'
VISION_PROMPT = ('請把這張圖的內容轉成詳盡的純文字描述：所有文字、數字、'
                 'UI 佈局、程式碼、錯誤訊息都要寫出來，有介面元素請說明位置。')


def _gemini_describe(data_url, user_text):
    """把 data URL 的圖送 Gemini，回傳文字描述。失敗就丟例外給呼叫端處理。"""
    raw = data_url
    mime = 'image/png'
    if raw.startswith('data:'):
        head, _, b64 = raw.partition(',')
        mime = head.split(':', 1)[1].split(';', 1)[0] or mime
        raw = b64
    prompt = VISION_PROMPT
    if user_text:
        prompt += '\n使用者的問題是：' + user_text
    payload = {'contents': [{'parts': [
        {'text': prompt},
        {'inline_data': {'mime_type': mime, 'data': raw}},
    ]}]}
    url = ('https://generativelanguage.googleapis.com/v1beta/models/'
           + GEMINI_MODEL + ':generateContent?key=' + GEMINI_KEY)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode('utf-8'))
    return d['candidates'][0]['content']['parts'][0]['text']


def _strip_images(body):
    """把請求裡的圖片換成 Gemini 的文字描述。

    沒有圖就原樣回傳（零成本）。OpenAI 格式的 content 可以是字串或
    [{type:text},{type:image_url}] 陣列，只有後者需要處理。
    """
    if not body or b'image_url' not in body:
        return body, 0
    try:
        j = json.loads(body)
    except Exception:
        return body, 0

    n = 0
    for m in j.get('messages', []):
        c = m.get('content')
        if not isinstance(c, list):
            continue
        texts, images = [], []
        for part in c:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'text':
                texts.append(part.get('text') or '')
            elif part.get('type') == 'image_url':
                u = (part.get('image_url') or {}).get('url') or ''
                if u:
                    images.append(u)
        if not images:
            continue
        joined = '\n'.join(t for t in texts if t)
        descs = []
        for u in images:
            try:
                descs.append('[圖片內容]\n' + _gemini_describe(u, joined))
                n += 1
            except Exception as e:
                descs.append('[圖片解析失敗：' + str(e)[:80] + ']')
        m['content'] = '\n\n'.join(descs + ([joined] if joined else []))

    return json.dumps(j, ensure_ascii=False).encode('utf-8'), n


class B(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass

    def handle_one_request(self):
        # 用戶端中途斷線時不要噴一整串 traceback，會蓋掉真正的錯誤
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _send(self, c, d):
        self.send_response(c); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length', str(len(d))); self.end_headers(); self.wfile.write(d)

    def _log_infer(self, dt, d):
        global _n
        _n += 1
        info = ''
        try:
            usage = (json.loads(d) or {}).get('usage') or {}
            tok = usage.get('completion_tokens') or usage.get('output_tokens')
            if tok:
                info += f', {tok} tokens'
                if dt > 0:
                    info += f', {tok/dt:.1f} tok/s'
            cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens')
            if cached:
                info += f', cache {cached}'
        except Exception:
            info = ''
        print(f'[{_n:>4}] 本機推論 OK  {dt:.1f}s{info}', flush=True)
        global _last_req, _idle_reported, _busy_gap, _busy_next
        global _stop_retry_next, _stop_retry_n
        _last_req = time.time()
        _idle_reported = False
        _stop_retry_next = 0.0      # 任務又動了，重試計數歸零
        _stop_retry_n = 0
        _busy_gap = BUSY_REPORT_MIN      # 新請求進來，退避重新從最短間隔算
        _busy_next = 0.0
        if WATCH > 0 and _n % WATCH == 0:
            _watch_once()

    def _fwd(self, body=None):
        if any(self.path.startswith(p) for p in PROBE_PATHS):
            self._send(200, b'{"data":[],"models":[],"object":"list"}')
            return

        # Hermes 會問單一模型的詳情（/v1/models/<id>），llama-server 沒這個端點 → 404。
        # 自己組一份回它，不要往上游打，也不要變成 502 紅字。
        one = re.match(r'^/v\d+/models/(.+)$', self.path.split('?')[0])
        if one:
            mid = one.group(1)
            self._send(200, json.dumps({
                'id': mid, 'object': 'model', 'created': 0, 'owned_by': 'local',
                'capabilities': ['completion', 'tools', 'chat'],
            }).encode())
            return
        is_infer = 'chat/completions' in self.path or 'messages' in self.path

        # 2026-08-29 起模型自己有眼睛（mmproj 掛上，/props 回報 vision:true），
        # 圖片直接原樣轉發給 llama-server，不再繞道 Gemini。
        # 舊行為（_strip_images 把圖換成 Gemini 的文字描述）是 mmproj 之前的
        # 權宜之計 —— 留著的話 mmproj 等於白掛，圖根本到不了模型眼前。
        # 實測 27B 自己看波形圖比 Gemini 準（4 題對 3.5 vs 對 2）。
        # 若哪天拿掉 mmproj，把 STRIP_IMAGES 改回 True 就能退回舊行為。
        if STRIP_IMAGES and is_infer and body:
            try:
                body, n_img = _strip_images(body)
                if n_img:
                    print('[img ] %d 張圖已由 Gemini 轉成文字描述' % n_img,
                          flush=True)
            except Exception as e:
                print('[img ] 圖片處理失敗，原樣轉發:', str(e)[:80], flush=True)
        # 串流時 llama-server 預設不回 usage，Hermes 就看不到 prompt_tokens，
        # 它的自動壓縮判斷 (last_prompt_tokens < threshold) 永遠為 0 -> 不壓縮 -> 撞牆。
        # 2026-08-27 彈珠台任務就是這樣一路長到 116K 才發現。
        if is_infer and body:
            try:
                j = json.loads(body)
                changed = False
                # 每次啟動報一次工具定義的固定成本。disabled_toolsets 名稱
                # 寫錯會靜默失效，看這行數字有沒有變才知道設定真的生效。
                global _TOOLS_REPORTED
                if not _TOOLS_REPORTED and j.get('tools'):
                    tj = json.dumps(j['tools'], ensure_ascii=False)
                    print('[tool] %d 個工具定義，%d 字元（約 %d tokens/每輪固定成本）'
                          % (len(j['tools']), len(tj), len(tj) // 4), flush=True)
                    _TOOLS_REPORTED = True
                if j.get('stream') and 'stream_options' not in j:
                    j['stream_options'] = {'include_usage': True}
                    changed = True
                # 單輪輸出上限。Hermes 預設送 max_tokens=65536，而 OpenAI 相容端點的
                # max_tokens 會「覆蓋」llama-server 的 --n-predict（不是取較小值），
                # 所以只能在這裡攔。2026-08-29 踩到：一個開放式題目讓模型連續生成
                # 2.5 萬 token 都不呼叫工具（卡在自我重複），還剩 4.2 萬額度。
                # 殺客戶端沒用 —— server 不知道對方斷線，會一路吐完，最後只能重啟。
                mt = j.get('max_tokens')
                if not isinstance(mt, int) or mt > MAX_OUT:
                    j['max_tokens'] = MAX_OUT
                    changed = True
                if changed:
                    body = json.dumps(j).encode()
            except Exception:
                pass
        t0 = time.time()
        try:
            r = urllib.request.Request(UP+self.path, data=body,
                headers={'Content-Type':'application/json','Authorization':'Bearer lmstudio'})
            with urllib.request.urlopen(r, timeout=1800) as resp:
                d = resp.read()
                if self.path.split('?')[0].rstrip('/').endswith('/models'):      # 關鍵：補 tools 能力
                    j = json.loads(d)
                    for k in ('models','data'):
                        for m in j.get(k, []):
                            m['capabilities'] = ['completion','tools','chat']
                    d = json.dumps(j).encode()
                if is_infer:
                    self._log_infer(time.time()-t0, d)
                self._send(resp.status, d)
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:400]
            except Exception:
                pass
            print('ERR', self.path, 'HTTP', e.code, detail, flush=True)
            self._send(502, json.dumps({'error': 'upstream', 'code': e.code,
                                        'detail': detail}).encode())
        except Exception as e:
            print('ERR', self.path, repr(e)[:300], flush=True)
            self._send(502, json.dumps({'error': 'upstream',
                                        'detail': repr(e)[:300]}).encode())

    def do_GET(self): self._fwd()
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); self._fwd(self.rfile.read(n))

print(f'bridge: localhost:1234 -> {UP}')
print('每次推論會印一行 —— 有印就代表走本機，不是雲端。')
if WATCH > 0:
    print(f'每 {WATCH} 次推論印一行 context 用量。')

try:
    with urllib.request.urlopen(UP+'/v1/models', timeout=10) as r:
        r.read()
except urllib.error.HTTPError as e:
    # 連得到，只是還沒 ready（例如 503 Loading model）——不是連線問題，照常啟動
    print(f'上游回應 HTTP {e.code}（可能還在載入模型），橋接器照常啟動。')
except Exception as e:
    print(f'\n連不到上游 {UP}：{e}')
    print('請確認 GPU 那台已開機（跑 0-MENU.bat）、ZeroTier 在同一個網路。')
    input('\n按 Enter 關閉...')
    sys.exit(1)

def _find_project(sid):
    """從它這輪寫過的檔案往上找專案根目錄。

    不用 session 的 cwd —— 那是 CLI 的啟動目錄，通常不是專案所在。
    以「有 HANDOFF.md 或 ARCHITECTURE.md」當專案根的判準。
    """
    import sqlite3
    db = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'hermes', 'state.db')
    try:
        c = sqlite3.connect(db)
        rows = c.execute(
            "SELECT coalesce(content,'') FROM messages "
            "WHERE session_id=? AND role='tool' "
            "ORDER BY rowid DESC LIMIT 120", (sid,)).fetchall()
        c.close()
    except Exception:
        return ''
    roots = {}
    for (t,) in rows:
        for m in re.finditer(r'"resolved_path":\s*"([^"]+)"', t):
            d = os.path.dirname(m.group(1).replace('\\\\', '\\'))
            for _ in range(4):
                if not d or not os.path.isdir(d):
                    break
                if os.path.exists(os.path.join(d, 'HANDOFF.md')) or \
                   os.path.exists(os.path.join(d, 'ARCHITECTURE.md')):
                    roots[d] = roots.get(d, 0) + 1
                    break
                d = os.path.dirname(d)
    return max(roots, key=roots.get) if roots else ''


def _open_menu(reason, project):
    """另開一個 console 視窗跳選單。

    橋接器自己是背景執行的（Hermes 一直在打它的 API），
    視窗讀不到鍵盤，所以選單要獨立開一個視窗。
    """
    menu = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '_stopmenu.py')
    if not os.path.exists(menu):
        return
    try:
        subprocess.Popen(
            ['cmd', '/c', 'start', '', sys.executable, menu,
             '--reason', reason, '--project', project or ''],
            shell=False)
        print('       ★ 已跳出選單視窗 —— 選一個數字就好，'
              '不用記指令。', flush=True)
    except Exception as e:
        print(f'       （選單開不起來：{str(e)[:60]}）', flush=True)


def _suggest_next(sid):
    """任務正常結束時，掃一遍實際狀態，告訴使用者該做什麼。

    判斷依據是「檔案和資料庫裡的事實」，不是模型自己的說法 ——
    它說「全部完成」不代表真的完成。
    """
    import sqlite3
    db = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'hermes', 'state.db')
    tips = []

    # 1. 它最後講了什麼（只取結論那幾行，不是整篇）
    last = ''
    try:
        c = sqlite3.connect(db)
        r = c.execute(
            "SELECT coalesce(content,'') FROM messages "
            "WHERE session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
        c.close()
        last = (r[0] or '') if r else ''
    except Exception:
        pass

    # 2. 它自己有沒有說還沒做完
    if re.search(r'尚未完成|還沒|沒做到|卡住|failed|FAIL|未完成', last, re.I):
        tips.append('它自己說有沒做完的部分 —— 看一下最後那則訊息再決定')

    cwd_hint = _find_project(sid)
    if cwd_hint and not os.path.exists(os.path.join(cwd_hint, 'HANDOFF.md')):
        tips.append(f'沒有 HANDOFF.md —— 接續前先叫它寫一份（{cwd_hint}）')

    if tips:
        print('       注意：', flush=True)
        for t in tips:
            print(f'         - {t}', flush=True)

    _act_on_stop('done', cwd_hint, sid)


def _handle_stop():
    """任務停掉時：判斷是「撞上限」還是「真的做完」，決定要不要接續。

    撞上限 = 還沒做完，可以用它自己寫的交接報告接續。
    正常結束 = 它講完收尾了，不該自動再派工。
    """
    global _autoresume_count
    if not os.path.exists(AUTORELAY):
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('_ar', AUTORELAY)
        ar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ar)
        sid = ar.latest_cli_session()
        report = ar.hit_limit(sid) if sid else None
    except Exception as e:
        print(f'       （判斷停止原因時出錯：{str(e)[:60]}）', flush=True)
        return

    if not report:
        print('       原因：正常結束（不是撞上限）。', flush=True)
        _suggest_next(sid)
        return

    print('       原因：撞到工具呼叫上限，任務其實還沒做完。', flush=True)
    _act_on_stop('limit', _find_project(sid), sid)
    return

def _act_on_stop(reason, project, sid):
    """任務停了要做什麼：自動模式先問防呆守衛，手動模式跳選單。"""
    here = os.path.dirname(os.path.abspath(__file__))
    guard = os.path.join(here, '_autoguard.py')
    menu = os.path.join(here, '_stopmenu.py')

    _print_session_summary(sid, project)

    if not AUTORESUME:
        if os.path.exists(menu):
            try:
                subprocess.Popen(
                    ['cmd', '/c', 'start', '', sys.executable, menu,
                     '--reason', reason, '--project', project or ''])
                print('       ★ 已跳出選單視窗 —— 按 Enter 用預設就好。',
                      flush=True)
            except Exception as e:
                print(f'       （選單開不起來：{str(e)[:60]}）', flush=True)
        return

    # 全自動：先過防呆
    if os.path.exists(guard):
        try:
            r = subprocess.run(
                [sys.executable, guard, '--json', '--commit'],
                capture_output=True, text=True, timeout=60)
            info = json.loads((r.stdout or '{}').strip().splitlines()[-1])
        except Exception as e:
            print(f'       防呆檢查出錯，保守起見不自動接續：{str(e)[:60]}',
                  flush=True)
            return
        if not info.get('go'):
            print(f'       [防呆] 不自動接續 —— {info.get("reason","")}',
                  flush=True)
            print('       要手動處理的話，跑 _stopmenu.py 或直接派新任務。',
                  flush=True)
            return
        print(f'       [防呆] 通過 —— {info.get("reason","")}', flush=True)

    print('       ★ 自動接續中…', flush=True)
    try:
        subprocess.Popen(
            ['cmd', '/c', 'start', '', sys.executable, menu,
             '--reason', reason, '--project', project or '', '--auto'])
    except Exception as e:
        print(f'       接續失敗：{str(e)[:80]}', flush=True)


def _print_session_summary(sid, project):
    """把上一輪做了什麼印出來，讓使用者一眼看懂現在的狀況。"""
    import sqlite3
    db = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'hermes', 'state.db')
    try:
        c = sqlite3.connect(db)
        s = c.execute(
            'SELECT api_call_count, started_at, ended_at FROM sessions '
            'WHERE id=?', (sid,)).fetchone()
        rows = c.execute(
            "SELECT coalesce(tool_name,''), coalesce(content,'') "
            "FROM messages WHERE session_id=? AND role='tool'",
            (sid,)).fetchall()
        last = c.execute(
            "SELECT coalesce(content,'') FROM messages WHERE session_id=? "
            "AND role='assistant' ORDER BY rowid DESC LIMIT 1",
            (sid,)).fetchone()
        c.close()
    except Exception:
        return

    calls = s[0] if s else '?'
    mins = ((s[2] or time.time()) - s[1]) / 60 if s and s[1] else 0
    files, tools = set(), {}
    for name, content in rows:
        tools[name] = tools.get(name, 0) + 1
        for m in re.finditer(r'"resolved_path":\s*"([^"]+)"', content):
            files.add(os.path.basename(m.group(1).replace('\\\\', '\\')))

    print('       ── 上一輪做了什麼 ' + '─' * 30, flush=True)
    print(f'       時間 {mins:.0f} 分鐘 / 工具呼叫 {calls} 次 / '
          f'動了 {len(files)} 個檔案', flush=True)
    if project:
        print(f'       專案 {project}', flush=True)
    if tools:
        top = sorted(tools.items(), key=lambda kv: -kv[1])[:5]
        print('       主要動作 ' + '、'.join(f'{k}×{v}' for k, v in top),
              flush=True)
    if files:
        shown = sorted(files)[:8]
        print('       檔案 ' + '、'.join(shown)
              + (f' …等 {len(files)} 個' if len(files) > 8 else ''),
              flush=True)
    if last and last[0]:
        head = [l.strip() for l in last[0].splitlines() if l.strip()][:3]
        print('       它最後說：', flush=True)
        for h in head:
            print(f'         {h[:76]}', flush=True)
    print('       ' + '─' * 48, flush=True)


def _idle_watch():
    """定期回報「有沒有在跑」。

    橋接器原本只在有請求時才印東西，任務停了就整片安靜 ——
    使用者分不出「模型還在想」和「任務已經結束」。
    這裡每 IDLE_REPORT_SEC 秒檢查一次，閒置就明講。
    """
    global _idle_reported, _busy_gap, _busy_next
    global _stop_retry_next, _stop_retry_n
    while True:
        time.sleep(30)
        try:
            if _last_req <= 0:
                continue
            idle = time.time() - _last_req
            if idle < IDLE_REPORT_SEC:
                continue

            # 上游 slot 還在跑嗎？跑著就是「在想」，沒跑就是「真的停了」
            busy = None
            try:
                with urllib.request.urlopen(UP + '/slots', timeout=8) as r:
                    slots = json.loads(r.read().decode('utf-8')) or []
                busy = any(s.get('is_processing') for s in slots)
            except Exception:
                pass

            ts = time.strftime('%H:%M:%S')
            now = time.time()
            if busy:
                # 還在推理，只是這一輪很長（長任務常見）。
                # 這種情況不需要一直提醒 —— 間隔越拉越長，最多每 15 分鐘一次。
                if now >= _busy_next:
                    print(f'[idle] {ts} 已 {idle/60:.0f} 分鐘沒有新請求，'
                          f'但模型仍在推理中（同一輪還沒結束）', flush=True)
                    _busy_gap = min(_busy_gap * 2, BUSY_REPORT_MAX)
                    _busy_next = now + _busy_gap
                _idle_reported = False
            elif not _idle_reported:
                print(f'[STOP] {ts} 已 {idle/60:.0f} 分鐘沒有新請求，'
                      f'而且模型也沒在推理 —— 任務應該已經結束或中斷了',
                      flush=True)
                _idle_reported = True     # 這行訊息只印一次，不洗版
                _stop_retry_next = now + RETRY_STOP_SEC
                _stop_retry_n = 0
                _handle_stop()
            elif (_stop_retry_n < RETRY_STOP_MAX
                  and now >= _stop_retry_next > 0):
                # 還是閒置 —— 再問一次 autoguard。它可能因為冷卻、
                # 或上次判斷時的暫時狀況而沒接續，現在條件可能已經變了。
                _stop_retry_n += 1
                _stop_retry_next = now + RETRY_STOP_SEC
                print(f'[STOP] {ts} 仍然閒置，再檢查一次是否該接續 '
                      f'（第 {_stop_retry_n}/{RETRY_STOP_MAX} 次）', flush=True)
                _handle_stop()
        except Exception:
            pass


threading.Thread(target=_idle_watch, daemon=True).start()

try:
    http.server.ThreadingHTTPServer(('127.0.0.1', 1234), B).serve_forever()
except KeyboardInterrupt:
    pass
except Exception as e:
    print(f'\n橋接器停止：{e}')
    input('\n按 Enter 關閉...')
    sys.exit(1)

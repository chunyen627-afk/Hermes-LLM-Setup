import http.server, json, os, re, subprocess, sys, threading, time, urllib.error, urllib.request

GPU = sys.argv[1] if len(sys.argv) > 1 else '10.35.219.64'
UP = f'http://{GPU}:8001'

# 每幾次推論印一行 context 用量；傳 0 可關掉。
# 用次數不用秒數 —— 閒著的時候不該一直洗版面，有在跑才需要看 ctx。
WATCH = int(sys.argv[2]) if len(sys.argv) > 2 else 10
# 單輪輸出上限，防止模型卡在自我重複時燒掉幾十分鐘 GPU。
# 正常回合幾百到幾千 token 就夠，8192 綽綽有餘。
MAX_OUT = 8192
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
                s = (json.loads(r.read().decode('utf-8')) or [{}])[0]
            n = s.get('n_ctx') or 0
            used = s.get('n_prompt_tokens') or 0
            cache = s.get('n_prompt_tokens_cache') or 0
            proc = s.get('n_prompt_tokens_processed') or 0
            pct = used * 100 // max(n, 1)
            hit = cache * 100 // max(used, 1)
            busy = bool(s.get('is_processing'))
            state = '推理中' if busy else '閒置'
            line = (f'[ctx ] {used:,} / {n:,} ({pct}%)  剩 {n-used:,}  '
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
GEMINI_KEY = os.environ.get('GEMINI_API_KEY',
                            '')
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

if WATCH > 0:
    pass      # 改成推論驅動，不需要背景定時執行緒

try:
    http.server.ThreadingHTTPServer(('127.0.0.1', 1234), B).serve_forever()
except KeyboardInterrupt:
    pass
except Exception as e:
    print(f'\n橋接器停止：{e}')
    input('\n按 Enter 關閉...')
    sys.exit(1)

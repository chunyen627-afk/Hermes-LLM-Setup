import http.server, json, sys, threading, time, urllib.error, urllib.request

GPU = sys.argv[1] if len(sys.argv) > 1 else '10.35.219.64'
UP = f'http://{GPU}:8001'

# 每隔幾秒印一次 context 用量；傳 0 可關掉
WATCH = int(sys.argv[2]) if len(sys.argv) > 2 else 300

# Hermes 會探測這些 Ollama / LM Studio 風格的端點，llama-server 沒有這些路徑。
# 直接回空清單，不往上游打，也不印紅字。
PROBE_PATHS = ('/api/v1/models', '/api/tags', '/api/version',
               '/v1/props', '/props', '/api/v0/models')

_n = 0


def _watch(interval):
    """定期讀 GPU 那台的 /slots，印一行 context 用量。壞掉不能影響轉發。"""
    time.sleep(5)
    while True:
        try:
            with urllib.request.urlopen(UP+'/slots', timeout=8) as r:
                s = (json.loads(r.read().decode('utf-8')) or [{}])[0]
            n = s.get('n_ctx') or 0
            used = s.get('n_prompt_tokens') or 0
            cache = s.get('n_prompt_tokens_cache') or 0
            proc = s.get('n_prompt_tokens_processed') or 0
            pct = used * 100 // max(n, 1)
            hit = cache * 100 // max(used, 1)
            state = '推理中' if s.get('is_processing') else '閒置'
            line = (f'[ctx ] {used:,} / {n:,} ({pct}%)  '
                    f'快取 {hit}%  重算 {proc:,}  {state}')
            if pct > 85:
                line += '  << 快滿了'
            print(line, flush=True)
        except Exception as e:
            print(f'[ctx ] 讀不到用量: {e}', flush=True)
        time.sleep(interval)


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

    def _fwd(self, body=None):
        if any(self.path.startswith(p) for p in PROBE_PATHS):
            self._send(200, b'{"data":[],"models":[],"object":"list"}')
            return
        is_infer = 'chat/completions' in self.path or 'messages' in self.path
        t0 = time.time()
        try:
            r = urllib.request.Request(UP+self.path, data=body,
                headers={'Content-Type':'application/json','Authorization':'Bearer lmstudio'})
            with urllib.request.urlopen(r, timeout=1800) as resp:
                d = resp.read()
                if '/models' in self.path:      # 關鍵：補 tools 能力
                    j = json.loads(d)
                    for k in ('models','data'):
                        for m in j.get(k, []):
                            m['capabilities'] = ['completion','tools','chat']
                    d = json.dumps(j).encode()
                if is_infer:
                    self._log_infer(time.time()-t0, d)
                self._send(resp.status, d)
        except Exception as e:
            print('ERR', self.path, e, flush=True); self._send(502, b'{"error":"upstream"}')

    def do_GET(self): self._fwd()
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); self._fwd(self.rfile.read(n))

print(f'bridge: localhost:1234 -> {UP}')
print('每次推論會印一行 —— 有印就代表走本機，不是雲端。')
if WATCH > 0:
    print(f'每 {WATCH} 秒印一行 context 用量。')

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
    threading.Thread(target=_watch, args=(WATCH,), daemon=True).start()

try:
    http.server.ThreadingHTTPServer(('127.0.0.1', 1234), B).serve_forever()
except KeyboardInterrupt:
    pass
except Exception as e:
    print(f'\n橋接器停止：{e}')
    input('\n按 Enter 關閉...')
    sys.exit(1)

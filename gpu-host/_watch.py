"""
GPU 主機端的即時監控 —— 看得到所有客戶端（含遠端那台）的活動

用法：
    python _watch.py          每 10 秒更新
    python _watch.py 5        每 5 秒更新

顯示：context 用量、快取命中、每輪耗時、GPU 使用率。
不管請求來自本機還是遠端 Hermes，都會出現在這裡。
"""
import json
import sys
import time
import subprocess
import urllib.request

INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 10
URL = 'http://127.0.0.1:8001/slots'


def gpu_util():
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5).stdout
        return '/'.join(x.strip() for x in out.strip().split('\n'))
    except Exception:
        return '?'


def main():
    print('=' * 62)
    print('  Qwen3.8 GPU 主機監控  (Ctrl+C 停止)')
    print(f'  每 {INTERVAL} 秒更新 —— 所有客戶端的請求都會顯示在這裡')
    print('=' * 62)
    print()

    last_busy = None
    busy_since = None
    last_task = None

    while True:
        try:
            with urllib.request.urlopen(URL, timeout=8) as r:
                s = (json.loads(r.read().decode('utf-8')) or [{}])[0]
        except Exception as e:
            print(f'{time.strftime("%H:%M:%S")}  連不到伺服器: {e}', flush=True)
            time.sleep(INTERVAL)
            continue

        n = s.get('n_ctx') or 0
        used = s.get('n_prompt_tokens') or 0
        cache = s.get('n_prompt_tokens_cache') or 0
        proc = s.get('n_prompt_tokens_processed') or 0
        busy = bool(s.get('is_processing'))
        task = s.get('id_task')

        pct = used * 100 // max(n, 1)
        hit = cache * 100 // max(used, 1)

        # 偵測狀態切換，估算單輪耗時
        note = ''
        now = time.time()
        if busy and not last_busy:
            busy_since = now
            note = '  << 開始新一輪'
        elif not busy and last_busy and busy_since:
            note = f'  << 這輪花了 {now - busy_since:.0f} 秒'
            busy_since = None
        elif busy and busy_since and (now - busy_since) > 300:
            note = f'  << 已跑 {(now - busy_since)/60:.0f} 分鐘'

        # context 大幅下降 = 被壓縮了
        if last_task is not None and used < last_task * 0.6 and used > 0:
            note += '  << context 被壓縮'

        bar_len = pct * 30 // 100
        bar = '#' * bar_len + '.' * (30 - bar_len)

        print(f'{time.strftime("%H:%M:%S")}  '
              f'[{bar}] {pct:>2}%  '
              f'{used:>7,}/{n:,}  '
              f'快取{hit:>3}%  '
              f'重算{proc:>6,}  '
              f'GPU {gpu_util():>10}  '
              f'{"推理中" if busy else "閒置  "}{note}',
              flush=True)

        if pct > 85:
            print('           ⚠ context 快滿了，Hermes 會開始壓縮（之後幾輪會變慢）',
                  flush=True)

        last_busy = busy
        last_task = used
        time.sleep(INTERVAL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已停止。')

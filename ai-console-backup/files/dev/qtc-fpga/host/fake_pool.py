"""Local stand-in for the Kryptex QTC stratum endpoint, for offline relay tests.

Speaks the dialect in docs/stratum_protocol.md (login / job / submit / keepalived), hands out a
4-byte extranonce, pushes a new job every --job-every seconds, and fully verifies each share with
the Python reference (128-hex nonce with the extranonce prefix, hash < (2^512-1)//difficulty,
`result` equals the hash, no duplicates, current job only).

  python fake_pool.py --port 3333 [--cert c.pem --key k.pem] [--difficulty 2000]
"""
import argparse
import asyncio
import json
import os
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ref_model'))
import qpow_ref as q   # noqa: E402

U512_MAX = (1 << 512) - 1


class FakePool:
    def __init__(self, difficulty, job_every):
        self.difficulty, self.job_every = difficulty, job_every
        self.stats = dict(logins=0, accepted=0, rejected=0, reasons={})
        self.seq = 0

    def new_job(self):
        self.seq += 1
        return dict(job_id=f'{os.urandom(4).hex()}_{self.difficulty}', mining_hash=os.urandom(32).hex(),
                    difficulty=self.difficulty, extranonce=None,
                    target=f'{(U512_MAX // self.difficulty) >> 448:016x}')

    def reject(self, reason):
        self.stats['rejected'] += 1
        self.stats['reasons'][reason] = self.stats['reasons'].get(reason, 0) + 1
        return {'code': -1, 'message': reason}

    async def handle(self, reader, writer):
        extranonce = os.urandom(4).hex()
        session = os.urandom(8).hex()
        job, seen, logged_in = None, set(), False

        async def send(obj):
            writer.write((json.dumps(obj) + '\n').encode())
            await writer.drain()

        async def pusher():
            nonlocal job
            while True:
                await asyncio.sleep(self.job_every)
                job = dict(self.new_job(), extranonce=extranonce)
                await send({'jsonrpc': '2.0', 'method': 'job', 'params': {'clean_jobs': True, 'job': job}})

        push_task = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                msg = json.loads(line)
                rid, method, p = msg.get('id'), msg.get('method'), msg.get('params') or {}
                if method == 'login':
                    if '/' not in p.get('login', ''):
                        await send({'id': rid, 'error': {'code': -1, 'message': 'Invalid address'}, 'result': None})
                        continue
                    self.stats['logins'] += 1
                    logged_in = True
                    job = dict(self.new_job(), extranonce=extranonce)
                    await send({'id': rid, 'jsonrpc': '2.0', 'error': None, 'result': {
                        'id': session, 'status': 'OK', 'extensions': ['keepalive'], 'job': job}})
                    push_task = asyncio.create_task(pusher())
                elif method == 'keepalived':
                    await send({'id': rid, 'error': None, 'result': {'status': 'KEEPALIVED'}})
                elif method == 'submit' and logged_in:
                    err = self.check(p, job, extranonce, seen, session)
                    if err:
                        await send({'id': rid, 'error': self.reject(err), 'result': None})
                    else:
                        self.stats['accepted'] += 1
                        await send({'id': rid, 'error': None, 'result': {'status': 'OK'}})
                else:
                    await send({'id': rid, 'error': {'code': -1, 'message': 'Unknown method'}, 'result': None})
        finally:
            if push_task:
                push_task.cancel()
            writer.close()

    def check(self, p, job, extranonce, seen, session):
        if p.get('id') != session:
            return 'Unauthenticated'
        if p.get('job_id') != job['job_id']:
            return 'Stale share'
        nonce_hex = p.get('nonce', '')
        if len(nonce_hex) != 128 or not nonce_hex.startswith(extranonce):
            return 'Invalid nonce'
        if nonce_hex in seen:
            return 'Duplicate share'
        h = q.nonce_hash(bytes.fromhex(job['mining_hash']), bytes.fromhex(nonce_hex))
        if p.get('result') != h.hex():
            return 'Wrong result'
        if int.from_bytes(h, 'big') >= U512_MAX // job['difficulty']:
            return 'Low difficulty share'
        seen.add(nonce_hex)
        return None


async def serve(port, difficulty, job_every, cert=None, key=None, ready=None):
    pool = FakePool(difficulty, job_every)
    ctx = None
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
    server = await asyncio.start_server(pool.handle, '127.0.0.1', port, ssl=ctx)
    if ready:
        ready.set_result(pool)
    async with server:
        await server.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=3333)
    ap.add_argument('--difficulty', type=int, default=2000)
    ap.add_argument('--job-every', type=float, default=30.0)
    ap.add_argument('--cert')
    ap.add_argument('--key')
    a = ap.parse_args()
    asyncio.run(serve(a.port, a.difficulty, a.job_every, a.cert, a.key))


if __name__ == '__main__':
    main()

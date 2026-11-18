"""Kryptex QTC stratum client (XMRig-style dialect: login / job / submit / keepalived).

See docs/stratum_protocol.md. Newline-delimited JSON over TLS (self-signed certificate, so no
verification; optionally pinned by SHA-256 fingerprint) or plain TCP.
"""
import asyncio
import hashlib
import json
import logging
import ssl

log = logging.getLogger('stratum')


class PoolError(Exception):
    pass


class StratumClient:
    def __init__(self, host, port, login, password='x', tls=True, fingerprint=None,
                 agent='qtc-fpga-relay/0.1', on_job=None, idle_timeout=90.0):
        self.host, self.port, self.login, self.password = host, port, login, password
        self.tls, self.fingerprint, self.agent = tls, fingerprint, agent
        self.on_job = on_job                     # callback(job dict); called for every new job
        self.idle_timeout = idle_timeout
        self.reader = self.writer = None
        self.session = None
        self.keepalive = False
        self.next_id = 2
        self.pending = {}
        self.last_send = 0.0
        self.connected = asyncio.Event()

    async def _open(self):
        ctx = None
        if self.tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ctx), timeout=20)
        if self.tls and self.fingerprint:
            der = self.writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
            got = hashlib.sha256(der).hexdigest()
            if got != self.fingerprint.lower():
                self.writer.close()
                raise PoolError(f'certificate fingerprint mismatch: {got}')

    async def _send(self, obj):
        self.writer.write((json.dumps(obj, separators=(',', ':')) + '\n').encode())
        await self.writer.drain()
        self.last_send = asyncio.get_running_loop().time()

    async def _request(self, method, params, rid=None, timeout=20.0):
        rid = rid or self.next_id
        if rid == self.next_id:
            self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self._send({'id': rid, 'method': method, 'params': params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    async def submit(self, job_id, nonce64, hash64):
        """Returns (accepted, message)."""
        try:
            msg = await self._request('submit', {'id': self.session, 'job_id': job_id,
                                                 'nonce': nonce64.hex(), 'result': hash64.hex()})
        except PoolError as e:
            return False, str(e)
        except asyncio.TimeoutError:
            return False, 'timeout'
        status = (msg or {}).get('status') if isinstance(msg, dict) else msg
        return status == 'OK', str(status)

    def _dispatch(self, msg):
        if msg.get('method') == 'job':
            params = msg.get('params') or {}
            job = params.get('job', params)
            if self.session and self.on_job:
                self.on_job(job)
            return
        rid = msg.get('id')
        fut = self.pending.get(rid)
        if fut and not fut.done():
            if msg.get('error'):
                err = msg['error']
                fut.set_exception(PoolError(err.get('message', str(err)) if isinstance(err, dict) else str(err)))
            else:
                fut.set_result(msg.get('result'))

    async def _reader_loop(self):
        while True:
            line = await asyncio.wait_for(self.reader.readline(), timeout=self.idle_timeout)
            if not line:
                raise ConnectionError('pool closed the connection')
            try:
                msg = json.loads(line)
            except ValueError:
                log.warning('non-JSON line from pool: %r', line[:200])
                continue
            self._dispatch(msg)

    async def _keepalive_loop(self):
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(5)
            if self.keepalive and loop.time() - self.last_send > 30:
                try:
                    await self._request('keepalived', {'id': self.session})
                except (PoolError, asyncio.TimeoutError) as e:
                    log.warning('keepalive failed: %s', e)

    async def run_forever(self):
        """Connect, log in, read; reconnect with backoff 1 s .. 30 s."""
        backoff = 1
        while True:
            reader_task = None
            try:
                await self._open()
                self.session = None
                reader_task = asyncio.create_task(self._reader_loop())
                result = await self._request('login', {'agent': self.agent, 'login': self.login,
                                                       'pass': self.password}, rid=1)
                self.session = result.get('id')
                self.keepalive = 'keepalive' in (result.get('extensions') or [])
                log.info('logged in to %s:%s (keepalive=%s)', self.host, self.port, self.keepalive)
                self.connected.set()
                backoff = 1
                if result.get('job') and self.on_job:
                    self.on_job(result['job'])
                ka = asyncio.create_task(self._keepalive_loop())
                try:
                    await reader_task
                finally:
                    ka.cancel()
            except (OSError, ConnectionError, PoolError, asyncio.TimeoutError, ssl.SSLError) as e:
                log.warning('pool connection lost: %s', e)
            finally:
                self.connected.clear()
                self.session = None
                if reader_task:
                    reader_task.cancel()
                for fut in self.pending.values():
                    if not fut.done():
                        fut.set_exception(PoolError('disconnected'))
                if self.writer:
                    self.writer.close()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

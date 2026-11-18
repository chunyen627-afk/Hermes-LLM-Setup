"""ZeroTier-only inference entrance, owned by the existing media control job."""
import http.server
import importlib.util
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
BRIDGE = ROOT.parent / 'OneDrive/Desktop/Qwen3.8-27B/hermes_bridge.py'
spec = importlib.util.spec_from_file_location('local_hermes_bridge', BRIDGE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
MAX_BODY = 32 * 1024 * 1024


class RestrictedBridge(bridge.Bridge):
    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def permitted(self):
        # Trust the TCP peer only, never X-Forwarded-For supplied by a caller.
        if self.client_address[0] not in self.server.allowed_clients:
            self.send_body(403, b'{"error":"ZeroTier client is not allowed"}')
            self.close_connection = True
            return False
        parsed = urlsplit(self.path)
        get_paths = {'/v1/models', '/v1/status', '/props', '/v1/props'}
        is_model = parsed.path.startswith('/v1/models/') and '..' not in parsed.path
        valid = ((self.command == 'GET' and (parsed.path in get_paths or is_model)) or
                 (self.command == 'POST' and parsed.path == '/v1/chat/completions'))
        if parsed.scheme or parsed.netloc or parsed.query or not valid:
            self.send_body(404, b'{"error":"Only the model inference API is exposed"}')
            self.close_connection = True
            return False
        return True

    def do_GET(self):
        if self.permitted():
            super().do_GET()

    def do_POST(self):
        if not self.permitted():
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            valid = 0 < length <= MAX_BODY and not self.headers.get('Transfer-Encoding')
        except ValueError:
            valid = False
        if not valid:
            self.send_body(413, b'{"error":"A Content-Length of 1 to 32 MiB is required"}')
            self.close_connection = True
            return
        super().do_POST()


def main():
    config = json.loads((ROOT / 'zerotier-api.json').read_text(encoding='utf-8'))
    bind = str(ipaddress.IPv4Address(config['bind_ip']))
    if bind in ('0.0.0.0', '127.0.0.1'):
        raise ValueError('Bind to the assigned ZeroTier address only')
    allowed = {str(ipaddress.IPv4Address(ip)) for ip in config['allowed_clients']}
    if config['upstream'] != 'http://127.0.0.1:8001':
        raise ValueError('Only the local model backend is permitted')
    log = RotatingFileHandler(ROOT / 'zerotier-api.log', maxBytes=1024*1024,
                              backupCount=2, encoding='utf-8')
    log.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    bridge.LOG.addHandler(log)
    bridge.LOG.setLevel(logging.INFO)
    server = http.server.ThreadingHTTPServer((bind, config['port']), RestrictedBridge)
    server.allowed_clients = allowed
    server.upstream = config['upstream']
    bridge.LOG.info('ZeroTier API %s:%s; peer allowlist=%s', bind, config['port'], sorted(allowed))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

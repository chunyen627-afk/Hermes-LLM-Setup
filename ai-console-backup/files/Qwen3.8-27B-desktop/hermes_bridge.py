"""Small Hermes -> llama.cpp bridge: compatibility, output cap, request logs."""
import argparse
import base64
import hashlib
import http.server
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

MAX_OUT = 8192
PROBE_PATHS = {"/api/v1/models", "/api/tags", "/api/version",
               "/v1/props", "/props", "/api/v0/models"}
LOG = logging.getLogger("hermes_bridge")


def localize_images(payload, directory):
    """Expose attachments as local files when the loaded model has no vision."""
    for message in payload.get("messages", []):
        if message.get("role") != "user" or not isinstance(message.get("content"), list):
            continue
        parts = []
        for part in message["content"]:
            if part.get("type") != "image_url":
                parts.append(part)
                continue
            image = part.get("image_url", {})
            url = image.get("url", "") if isinstance(image, dict) else image
            match = re.fullmatch(r"data:image/(png|jpe?g|webp);base64,([A-Za-z0-9+/=\s]+)", url)
            if not match:
                parts.append({"type": "text", "text": "[Image attachment: vision is disabled. For local H3 video generation, provide a local image path or attach a PNG/JPEG/WebP image.]"})
                continue
            encoded = re.sub(r"\s", "", match[2])
            if len(encoded) > 40 * 1024 * 1024:
                raise ValueError("Image attachment exceeds 30 MiB")
            raw = base64.b64decode(encoded, validate=True)
            valid = (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")
                     or (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))
            if not valid or len(raw) > 30 * 1024 * 1024:
                raise ValueError("Invalid image attachment")
            directory.mkdir(parents=True, exist_ok=True)
            suffix = "jpg" if match[1] in ("jpg", "jpeg") else match[1]
            target = directory / (hashlib.sha256(raw).hexdigest() + "." + suffix)
            if not target.exists():
                # Content-addressed names prevent traversal and repeated copies.
                try:
                    with target.open("xb") as output:
                        output.write(raw)
                except FileExistsError:
                    pass  # Another chat already materialized the same attachment.
            parts.append({"type": "text", "text": "[Local image attachment; vision is disabled. This is the reference image for H3. Do not invent its contents or call vision_analyze. path: " + target.as_posix() + "]"})
        message["content"] = parts


def prepare_body(body, image_directory=None):
    """Cap output; optionally convert images to paths for a text-only model."""
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    if image_directory is not None:
        localize_images(payload, image_directory)
    cap_key = ("max_completion_tokens" if "max_completion_tokens" in payload
               else "max_tokens")
    value = payload.get(cap_key)
    payload[cap_key] = (value if type(value) is int and 0 < value <= MAX_OUT
                        else MAX_OUT)
    if "max_tokens" in payload and cap_key != "max_tokens":
        value = payload["max_tokens"]
        payload["max_tokens"] = (value if type(value) is int and 0 < value <= MAX_OUT
                                 else MAX_OUT)
    if payload.get("stream"):
        options = dict(payload.get("stream_options") or {})
        options["include_usage"] = True
        payload["stream_options"] = options
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def model_capabilities(document):
    for group in ("models", "data"):
        for model in document.get(group, []):
            caps = model.get("capabilities") or []
            model["capabilities"] = list(dict.fromkeys(caps + ["completion", "tools", "chat"]))
    return document


def setup_logging():
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOG.addHandler(console)
    try:
        logfile = RotatingFileHandler(Path(__file__).with_name("bridge_status.log"),
                                      maxBytes=1024 * 1024, backupCount=2,
                                      encoding="utf-8")
        logfile.setFormatter(formatter)
        LOG.addHandler(logfile)
    except OSError:
        LOG.warning("File log unavailable; using console only")


class Bridge(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def send_body(self, status, data, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def forward(self, body=None):
        path = self.path.split("?", 1)[0].rstrip("/")
        started = time.monotonic()
        status = 502
        usage = {}
        streaming_started = False
        try:
            if self.command == "GET" and path in PROBE_PATHS:
                status = 200
                self.send_body(status, b'{"data":[],"models":[],"object":"list"}')
                return
            match = re.fullmatch(r"/v\d+/models/(.+)", path)
            if self.command == "GET" and match:
                status = 200
                data = {"id": match.group(1), "object": "model", "created": 0,
                        "owned_by": "local", "capabilities": ["completion", "tools", "chat"]}
                self.send_body(status, json.dumps(data).encode())
                return
            inference = path.endswith(("/chat/completions", "/messages", "/completions"))
            if inference and body:
                try:
                    image_directory = None
                    if b'"image_url"' in body:
                        # Probe only requests carrying images. Unknown capabilities
                        # preserve the previous API behavior; no model is loaded.
                        try:
                            with urllib.request.urlopen(self.server.upstream + "/props", timeout=5) as props:
                                vision = json.load(props).get("modalities", {}).get("vision")
                            if vision is False:
                                image_directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" / "image_cache" / "h3-inputs"
                        except (OSError, ValueError):
                            pass
                    body = prepare_body(body, image_directory=image_directory)
                except (ValueError, TypeError):
                    status = 400
                    self.send_body(status, b'{"error":"Invalid inference JSON"}')
                    return
            request = urllib.request.Request(
                self.server.upstream + self.path, data=body, method=self.command,
                headers={"Content-Type": self.headers.get("Content-Type", "application/json"),
                         "Authorization": "Bearer lmstudio"})
            with urllib.request.urlopen(request, timeout=1800) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
                if content_type.lower().startswith("text/event-stream"):
                    # Flush each SSE line immediately; do not buffer the whole generation.
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    streaming_started = True
                    for line in response:
                        self.wfile.write(line)
                        self.wfile.flush()
                        if line.startswith(b"data:"):
                            try:
                                event = json.loads(line[5:])
                                usage = event.get("usage") or usage
                            except (ValueError, AttributeError):
                                pass
                    return
                data = response.read()
                if path.endswith("/models"):
                    data = json.dumps(model_capabilities(json.loads(data))).encode()
                elif inference:
                    try:
                        usage = json.loads(data).get("usage") or {}
                    except (ValueError, AttributeError):
                        pass
                self.send_body(status, data, content_type)
        except urllib.error.HTTPError as error:
            status = error.code
            self.send_body(status, error.read(),
                           error.headers.get("Content-Type", "application/json"))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as error:
            status = "connection-closed"
            LOG.warning("Connection closed during %s: %s", path, type(error).__name__)
            self.close_connection = True
        except Exception as error:
            LOG.warning("Upstream failure: %s", type(error).__name__)
            # A streaming response has already sent headers; close it on failure.
            if streaming_started:
                self.close_connection = True
                return
            self.send_body(502, b'{"error":"Upstream connection failed"}')
        finally:
            tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
            LOG.info("%s %s status=%s %.1fs prompt=%s output=%s",
                     self.command, path, status, time.monotonic() - started,
                     usage.get("prompt_tokens", 0), tokens)

    def do_GET(self):
        self.forward()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            self.send_body(400, b'{"error":"Invalid Content-Length"}')
            self.close_connection = True
            return
        self.forward(self.rfile.read(length))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gpu", nargs="?", default="10.35.219.64")
    parser.add_argument("legacy_watch", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--upstream-port", type=int, default=8001)
    args = parser.parse_args()
    setup_logging()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Bridge)
    server.upstream = f"http://{args.gpu}:{args.upstream_port}"
    LOG.info("bridge 127.0.0.1:%s -> %s; output cap=%s; no automatic task dispatch",
             args.port, server.upstream, MAX_OUT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""Generate a real local image while the selected chat profile answers a request."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import time

from PIL import Image, ImageStat
import requests

MODEL = "orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs"
ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--context', type=int, default=262144)
parser.add_argument('--label', default='')
args = parser.parse_args()
assert all(c.isalnum() or c in '-_' for c in args.label)
SUFFIX = ('-' + args.label) if args.label else ''


def generate():
    response = requests.get("http://127.0.0.1:8003/generate", params={
        "prompt": "a yellow rubber duck floating on a calm blue pond, soft sunlight, no text",
        "style": "anime", "seed": "20261007", "steps": "10", "cfg": "6",
        "size": "1024x1024", "rules": "000111000"}, timeout=(10, 900))
    response.raise_for_status()
    assert response.headers.get("Content-Type", "").startswith("image/"), "Image API returned no image"
    image = Image.open(io.BytesIO(response.content))
    image.load()
    assert image.size == (1024, 1024), "Unexpected output size"
    assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 5, "Uniform/invalid output"
    path = ROOT / ("static-coexistence-duck" + SUFFIX + ".png")
    path.write_bytes(response.content)
    return {"path": str(path), "bytes": len(response.content), "size": list(image.size)}


def main():
    status = requests.get("http://127.0.0.1:8001/v1/status", timeout=10).json()
    assert status["model"] == MODEL and status["cache_max_tokens"] == args.context
    queue = requests.get("http://127.0.0.1:8188/queue", timeout=10).json()
    assert not queue["queue_running"] and not queue["queue_pending"], "Existing image job is active"
    report = {"status": "failed", "context": args.context, "scope": "Actual image generation and overlapping chat; not a full context input stress test."}
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(generate)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                q = requests.get("http://127.0.0.1:8188/queue", timeout=10).json()
                if q["queue_running"]:
                    break
                if future.done():
                    raise AssertionError("Image completed before concurrency could be checked")
                time.sleep(0.5)
            else:
                raise AssertionError("Image did not start within 30 seconds")
            response = requests.post("http://127.0.0.1:1234/v1/chat/completions", json={
                "model": MODEL, "messages": [{"role": "user", "content": "請只回覆 IMAGE_CHAT_OK。"}],
                "max_tokens": 64, "temperature": 0, "reasoning_effort": "none"}, timeout=(10, 300))
            response.raise_for_status()
            result = response.json()
            assert "IMAGE_CHAT_OK" in result["choices"][0]["message"].get("content", "")
            report["chat"] = result
            report["image"] = future.result()
        report["status"] = "passed"
    finally:
        report["elapsed_s"] = round(time.monotonic() - started, 2)
        path = ROOT / ("static-coexistence-verification" + SUFFIX + ".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "elapsed_s": report["elapsed_s"], "report": str(path)}), flush=True)


if __name__ == "__main__":
    main()

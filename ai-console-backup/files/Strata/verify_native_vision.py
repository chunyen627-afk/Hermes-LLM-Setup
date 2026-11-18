"""The requested image-only code is not included in the text prompt."""
import base64
import argparse
import json
import os
from pathlib import Path
import time
from PIL import Image, ImageDraw, ImageFont
import requests

ROOT = Path(__file__).resolve().parent
MODEL = "orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='')
    args = parser.parse_args()
    assert all(c.isalnum() or c in '-_' for c in args.label)
    path = ROOT / "vision-evidence.png"
    image = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((60, 80, 240, 260), fill="red")
    draw.rectangle((390, 80, 570, 260), fill="blue")
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 48)
    draw.text((175, 310), "AQUA-731", font=font, fill="black")
    image.save(path)
    base = os.environ.get("STRATA_TEST_BASE", "http://127.0.0.1:8080").rstrip("/")
    status = requests.get(base + "/v1/status", timeout=10).json()
    assert status["model"] == MODEL and status["vision"]["enabled"]
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    started = time.monotonic()
    response = requests.post(base + "/v1/chat/completions", json={
        "model": MODEL, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "In English, describe the two colored shapes and transcribe the printed code exactly."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}]}],
        "temperature": 0, "reasoning_effort": "none", "max_tokens": 160}, timeout=(10, 300))
    response.raise_for_status()
    result = response.json()
    content = result["choices"][0]["message"].get("content") or ""
    passed = all(word in content.lower() for word in ("red", "circle", "blue", "square", "aqua-731"))
    report = {"status": "passed" if passed else "failed", "model": MODEL, "image": str(path),
              "elapsed_s": round(time.monotonic() - started, 2), "result": result}
    suffix = ('-' + args.label) if args.label else ''
    output = ROOT / ("native-vision-verification" + suffix + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "content": content, "elapsed_s": report["elapsed_s"]}), flush=True)
    assert passed, "Image-only code or shapes were not read correctly"


if __name__ == "__main__":
    main()

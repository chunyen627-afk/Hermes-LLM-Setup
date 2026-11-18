"""Measure the same real-model prompts after each selected GPU configuration.

This script does not start/stop any service or alter settings.
Run once per configuration: --label 2gpu or --label 3gpu.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import time

import psutil
import requests

ROOT = Path(__file__).resolve().parent
MODEL = "orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs"
PROMPTS = [
    "Write one paragraph of about 120 English words explaining how RAM and GPU memory cooperate in running a large local language model.",
    "Write one paragraph of about 120 English words explaining why a local image-to-video system should verify its output before announcing success.",
    "Write one paragraph of about 120 English words explaining how a short Chinese motion description can be expanded into a video prompt while preserving the source image.",
]


def snapshot():
    raw = subprocess.check_output([
        "nvidia-smi", "--query-gpu=index,name,memory.used,memory.total",
        "--format=csv,noheader,nounits"], text=True)
    return {"gpu_memory": raw.strip().splitlines(),
            "ram_available_GiB": round(psutil.virtual_memory().available / 1024**3, 2),
            "ram_used_GiB": round(psutil.virtual_memory().used / 1024**3, 2)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=["2gpu", "3gpu"], required=True)
    args = parser.parse_args()
    report = {"label": args.label, "model": MODEL, "before": snapshot(), "measurements": []}
    names = requests.get("http://127.0.0.1:8080/v1/models", timeout=10).json()
    assert MODEL in {m["id"] for m in names.get("data", [])}, "Wrong backend"
    for i, prompt in enumerate(["Reply with READY."] + PROMPTS):
        started = time.monotonic()
        response = requests.post("http://127.0.0.1:8080/v1/chat/completions", json={
            "model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "reasoning_effort": "none", "temperature": 0, "seed": 42, "max_tokens": 256},
            timeout=(10, 600))
        response.raise_for_status()
        result = response.json()
        elapsed = time.monotonic() - started
        content = result["choices"][0]["message"].get("content") or ""
        assert content.strip(), "Empty answer"
        if i:
            report["measurements"].append({"prompt": prompt, "elapsed_s": round(elapsed, 2),
                "content": content, "usage": result.get("usage"), "timings": result.get("timings"),
                "memory_after": snapshot()})
        print(json.dumps({"label": args.label, "request": i,
                          "elapsed_s": round(elapsed, 2), "timings": result.get("timings")}), flush=True)
    values = [r["timings"].get("predicted_per_second") for r in report["measurements"] if r.get("timings")]
    values = [v for v in values if isinstance(v, (int, float))]
    report["median_decode_tokens_per_second"] = statistics.median(values) if values else None
    report["after"] = snapshot()
    report["scope"] = "Same short prompts, one warm-up; does not prove long-context or media coexistence."
    (ROOT / ("benchmark-" + args.label + ".json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

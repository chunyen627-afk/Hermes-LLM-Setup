"""Local H3 image-to-video jobs. No daemon, model launch, or background poller."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlencode

import av
from PIL import Image, ImageOps
import requests

ROOT = Path(r"C:\Users\pjunm\ComfyUI-H3")
JOBS = ROOT / "user" / "default" / "h3_jobs"
BASE = "http://127.0.0.1:8189"


def record_path(job):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job):
        raise ValueError("request_id must contain 1-64 letters, digits, underscore or hyphen")
    return JOBS / (job + ".json")


def save(record):
    target = record_path(record["job_id"])
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)


def api(path):
    response = requests.get(BASE + path, timeout=15)
    response.raise_for_status()
    return response.json()


def build_graph(image, prompt, width, height, frames, steps, seed, job):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors", "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_int8_convrot.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "width": width, "height": height, "length": frames, "first_frame": ["5", 0]}},
        "7": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["6", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["10", 0], "guider": ["7", 0], "sampler": ["8", 0], "sigmas": ["9", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "fps": 24.0, "audio": ["13", 0]}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": "video/H3_hermes_" + job, "format": "mp4", "codec": "h264"}},
    }


def normalize(request):
    source = Path(request["image_path"]).expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("Image must be a local file no larger than 30 MiB")
    prompt = str(request["prompt_en"]).strip()
    if not 10 <= len(prompt) <= 8000:
        raise ValueError("prompt_en must contain 10-8000 characters")
    seconds = float(request.get("seconds", 5))
    steps = int(request.get("steps", 12))
    if not 3 <= seconds <= 10 or not 8 <= steps <= 20:
        raise ValueError("Use 3-10 seconds and 8-20 steps; default 5 seconds / 12 steps")
    # Native H3 accepts lengths congruent to 5 modulo 17.
    frames = 5 + 17 * max(0, math.ceil((seconds * 24 - 5) / 17))
    with Image.open(source) as opened:
        if opened.width * opened.height > 40_000_000:
            raise ValueError("Image is too large (maximum 40 megapixels)")
        image = ImageOps.exif_transpose(opened).convert("RGB")
    scale = 512 / max(image.size)
    width, height = [max(128, min(512, round(n * scale / 32) * 32)) for n in image.size]
    fitted = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), image.resize((1, 1)).getpixel((0, 0)))
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    data = output.getvalue()
    seed = int(request.get("seed", secrets.randbits(48)))
    if not 0 <= seed < 2**63:
        raise ValueError("seed must be between 0 and 2^63-1")
    settings = {"image_path": str(source), "image_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "prompt_en": prompt, "frames": frames, "seconds": frames / 24,
                "width": width, "height": height, "steps": steps, "seed": seed}
    return settings, data


def recover(record):
    """Recover an uncertain POST by client_id; never blindly submit it again."""
    queue = api("/queue")
    entries = queue.get("queue_running", []) + queue.get("queue_pending", [])
    entries += [h["prompt"] for h in api("/history?max_items=100").values() if "prompt" in h]
    for entry in entries:
        if len(entry) > 3 and entry[3].get("client_id") == "hermes-h3-" + record["job_id"]:
            record.update(prompt_id=entry[1], state="submitted")
            save(record)
            return


def submit(request_file):
    request = json.loads(Path(request_file).read_text(encoding="utf-8-sig"))
    job = request["request_id"]
    target = record_path(job)
    settings, image = normalize(request)
    fingerprint = hashlib.sha256(json.dumps({"source": settings["image_sha256"], "request": request}, sort_keys=True).encode()).hexdigest()
    if target.exists():
        record = json.loads(target.read_text(encoding="utf-8"))
        if record["fingerprint"] != fingerprint:
            raise ValueError("request_id already belongs to a different request; choose a new id")
        return status(job)
    queue = api("/queue")
    if queue.get("queue_running") or queue.get("queue_pending"):
        return {"state": "busy", "error": "H3 is working. Wait for the existing job before submitting."}
    filename = "hermes_" + hashlib.sha256(image).hexdigest()[:24] + ".png"
    response = requests.post(BASE + "/upload/image", files={"image": (filename, image, "image/png")}, data={"type": "input", "overwrite": "false"}, timeout=30)
    response.raise_for_status()
    uploaded = response.json()
    image_name = "/".join(filter(None, [uploaded.get("subfolder", ""), uploaded["name"]]))
    if ".." in Path(image_name).parts or Path(image_name).is_absolute():
        raise ValueError("Unexpected upload path")
    graph = build_graph(image_name, settings["prompt_en"], settings["width"], settings["height"], settings["frames"], settings["steps"], settings["seed"], job)
    record = {"job_id": job, "fingerprint": fingerprint, "state": "submitting", "created": time.time(), "settings": settings}
    save(record)
    try:
        response = requests.post(BASE + "/prompt", json={"prompt": graph, "client_id": "hermes-h3-" + job}, timeout=30)
        result = response.json()
        if not response.ok or result.get("node_errors") or not result.get("prompt_id"):
            record.update(state="error", error=result)
        else:
            record.update(state="submitted", prompt_id=result["prompt_id"])
    except requests.RequestException as error:
        record.update(state="submission_unknown", error=str(error), advice="Use status; do not submit a new request until recovered.")
    save(record)
    return record


def validate_video(path, expected):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        size = (stream.width, stream.height)
        count = sum(1 for _ in container.decode(video=0))
    with av.open(str(path)) as container:
        audio = sum(1 for _ in container.decode(audio=0)) if container.streams.audio else 0
    if count != expected["frames"] or size != (expected["width"], expected["height"]) or not audio:
        raise ValueError("Video validation failed: unexpected frame count, dimensions, or missing audio")
    return {"decoded_frames": count, "width": size[0], "height": size[1], "decoded_audio_frames": audio, "bytes": path.stat().st_size}


def status(job):
    record = json.loads(record_path(job).read_text(encoding="utf-8"))
    if record["state"] in ("completed", "error"):
        if record["state"] == "completed" and not Path(record["output_path"]).is_file():
            return dict(record, state="output_missing")
        return record
    if not record.get("prompt_id"):
        recover(record)
        if not record.get("prompt_id"):
            return dict(record, state="submission_unknown", advice="No job found. Do not blindly retry; check the H3 console.")
    prompt_id = record["prompt_id"]
    entry = api("/history/" + prompt_id).get(prompt_id)
    if not entry:
        queue = api("/queue")
        for state, group in (("running", "queue_running"), ("queued", "queue_pending")):
            if any(item[1] == prompt_id for item in queue.get(group, [])):
                return dict(record, state=state)
        # Recheck after /queue to cover completion between the two reads.
        entry = api("/history/" + prompt_id).get(prompt_id)
        if not entry:
            return dict(record, state="unknown", advice="Job absent from queue/history; H3 may have restarted. Do not automatically resubmit.")
    result = entry.get("status", {})
    if result.get("status_str") == "error":
        record.update(state="error", error=result.get("messages", []))
    elif result.get("completed"):
        files = entry.get("outputs", {}).get("15", {}).get("images", [])
        files += entry.get("outputs", {}).get("15", {}).get("videos", [])
        media = next((f for f in files if f.get("filename", "").lower().endswith(".mp4")), None)
        if not media or media.get("type") != "output":
            raise ValueError("H3 reports completion but no MP4 output is available")
        output = (ROOT / "output" / media.get("subfolder", "") / media["filename"]).resolve(strict=True)
        if not output.is_relative_to((ROOT / "output").resolve()):
            raise ValueError("Output path escaped H3 output directory")
        verified = validate_video(output, record["settings"])
        record.update(state="completed", output_path=str(output), view_url=BASE + "/view?" + urlencode({k: media[k] for k in ("filename", "subfolder", "type")}), verification=verified)
    else:
        return dict(record, state="running")
    save(record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["submit", "status", "wait"])
    parser.add_argument("--request")
    parser.add_argument("--job")
    parser.add_argument("--timeout", type=int, default=40)
    args = parser.parse_args()
    JOBS.mkdir(parents=True, exist_ok=True)
    # Serialize short operations so two chats cannot double-submit.
    import msvcrt
    lock = (JOBS / "operation.lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    try:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            if args.action == "submit":
                if not args.request:
                    raise ValueError("submit requires --request")
                result = submit(args.request)
            else:
                if not args.job:
                    raise ValueError("status/wait requires --job")
                deadline = time.monotonic() + max(0, min(args.timeout, 45))
                while True:
                    result = status(args.job)
                    if args.action != "wait" or result["state"] not in ("running", "queued", "submitted") or time.monotonic() >= deadline:
                        break
                    time.sleep(3)
            public = {k: result[k] for k in ("job_id", "state", "prompt_id", "output_path", "view_url", "verification", "advice", "error") if k in result}
            if "settings" in result:
                public["seconds"] = result["settings"]["seconds"]
            print(json.dumps(public, ensure_ascii=True), flush=True)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception as error:
        print(json.dumps({"state": "error", "error": str(error)}, ensure_ascii=True), flush=True)
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

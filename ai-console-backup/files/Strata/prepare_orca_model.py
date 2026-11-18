"""Download and verify the approved Orca model, then prepare its native pack.

Run with the existing ComfyUI-H3 Python, which supplies huggingface_hub.
This helper never changes Hermes, starts a server, or deletes old models.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

from huggingface_hub import HfApi, get_token, hf_hub_download

ROOT = Path(__file__).resolve().parent
assert ROOT == Path(r"C:\Users\pjunm\Strata")
DEST = ROOT.parent / "Strata-data" / "models" / "orca-iq3_xxs"
REPO = "orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF"
REVISION = "e43d00f4e2b8b40b89f75e9adeb1045ac34c8acc"
FILES = [
    ("Qwen3.8-Flash-Next-Uncensored-IQ3_XXS-00001-of-00002.gguf", 44637691008,
     "aaf57046943c6638480e8984835ce5ec29c486180ff71169d3c22c6929851b7b"),
    ("Qwen3.8-Flash-Next-Uncensored-IQ3_XXS-00002-of-00002.gguf", 40564977024,
     "a19cf9bbe87bce45f312ef11401c88b675f3784c927d02fa2272822229e6a224"),
]


def status(message, **kwargs):
    line = datetime.now().isoformat(timespec="seconds") + " " + str(message)
    print(line, flush=True)
    with (ROOT / "model-preparation.log").open("a", encoding="utf-8") as output:
        output.write(line + "\n")


def main():
    token = get_token()
    if not token:
        raise RuntimeError("Please sign in with hf auth login first.")
    api = HfApi(token=token)
    api.auth_check(repo_id=REPO, repo_type="model")
    info = api.model_info(REPO, revision=REVISION, files_metadata=True)
    actual = {f.rfilename: f for f in info.siblings}
    for name, size, sha in FILES:
        item = actual[name]
        if item.size != size or item.lfs.sha256 != sha:
            raise RuntimeError("The pinned model file metadata does not match.")
    DEST.mkdir(parents=True, exist_ok=True)
    for name, size, sha in FILES:
        status("Downloading: " + name)
        path = Path(hf_hub_download(REPO, name, revision=REVISION,
                                   local_dir=DEST, token=token))
        if path.stat().st_size != size:
            raise RuntimeError("Downloaded model file has the wrong size.")
        status("Checking SHA256: " + name)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(32 * 1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != sha:
            raise RuntimeError("Downloaded model file failed SHA256 verification.")
        status("Verified: " + name)
    env = dict(os.environ)
    env["STRATA_GGUF_PY"] = str(ROOT / "third_party" / "llama.cpp" / "gguf-py")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    subprocess.run([str(python), "-u", str(ROOT / "tools" / "iq_pack.py"),
                    "--gguf", str(DEST / FILES[0][0]), "--out",
                    str(ROOT / "packs" / "orca-iq3_xxs"), "--compat-bf16"],
                   cwd=ROOT, env=env, check=True)
    status("Model preparation complete. Hermes still uses its existing backend.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Keep authorization headers and signed URLs out of logs.
        response = getattr(error, "response", None)
        code = getattr(response, "status_code", None)
        status(json.dumps({"result": "not_ready", "error_type": type(error).__name__,
                           "http_status": code}))
        if code in (401, 403):
            print("Please grant this signed-in account access on the OrcaRouter model page.")
        sys.exit(1)

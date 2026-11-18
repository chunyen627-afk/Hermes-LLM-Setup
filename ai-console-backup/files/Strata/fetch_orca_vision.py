"""Fetch and verify this checkpoint's own vision projector; no service changes."""
import hashlib
from pathlib import Path
from huggingface_hub import hf_hub_download

DEST = Path(r"C:\Users\pjunm\Strata-data\models\orca-iq3_xxs")
NAME = "mmproj-Qwen3.8-Flash-Next-Uncensored-F16.gguf"
SHA = "f0f352a97a62a057f3aecdb597cac664762cea2ca23f7b16ec92eee28c5572d9"

path = Path(hf_hub_download("orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF", NAME,
                           revision="e43d00f4e2b8b40b89f75e9adeb1045ac34c8acc", local_dir=DEST))
assert path.stat().st_size == 907543296
digest = hashlib.sha256()
with path.open("rb") as file:
    while chunk := file.read(16 * 1024 * 1024):
        digest.update(chunk)
assert digest.hexdigest() == SHA, "Vision file SHA256 mismatch"
print("Own vision projector downloaded and SHA256 verified.", flush=True)

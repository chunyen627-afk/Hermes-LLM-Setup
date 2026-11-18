"""Read-only inventory. Produces a proposal; never removes models."""
import json
from pathlib import Path

HOME = Path(r"C:\Users\pjunm")
HUB = HOME / ".cache" / "huggingface" / "hub"
LM = HOME / ".lmstudio" / "models"
NEW = HOME / "Strata-data" / "models" / "orca-iq3_xxs"


def describe(path):
    target = path.resolve()
    return {"path": str(path), "resolved_path": str(target),
            "bytes": path.stat().st_size, "symlink": path.is_symlink(),
            "physical_file_id": [path.stat().st_dev, path.stat().st_ino]}


def main():
    proposed = []
    preserved = []
    for root in (HUB, LM):
        if not root.exists():
            continue
        for path in root.rglob("*.gguf"):
            relative = str(path.relative_to(root)).replace("\\", "/")
            name = path.name.lower()
            projector = "mmproj" in name or "projector" in name
            chat = (
                (root == HUB and relative.startswith("Qwen3.8-27B-"))
                or "models--unsloth--Qwen3.6-35B-A3B-GGUF/" in relative
                or (root == LM and any(p in relative for p in (
                    "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive/",
                    "DeepSeek-V4-Lite-Instruct-GGUF/",
                    "Qwen3-Coder-30B-A3B-Instruct-GGUF/")))
            )
            item = describe(path)
            if chat and not projector:
                item["reason"] = "Superseded chat model or its speculative draft"
                proposed.append(item)
            else:
                item["reason"] = "Vision/projector asset or outside the explicit chat cleanup list"
                preserved.append(item)
    for path in NEW.glob("*.gguf"):
        item = describe(path)
        item["reason"] = "New candidate; always preserve"
        preserved.append(item)
    unique = {tuple(p["physical_file_id"]): p["bytes"] for p in proposed}
    report = {"executed": False,
              "required_before_deletion": ["Live model acceptance", "Hermes desktop backend",
                                           "Restart recovery", "Static image coexistence", "H3 coexistence"],
              "proposed_files": proposed, "preserved_gguf_files": preserved,
              "proposed_unique_GiB": round(sum(unique.values()) / 1024**3, 2),
              "preserve_other_assets": ["ComfyUI-H3 models and outputs", "Static-image models and outputs",
                                        "Qwen3-VL safetensors", "FunctionGemma projects/checkpoints",
                                        "User documents", "Cloud/network files"],
              "note": "A proposal only. Symlink/blob references and running processes must be checked before cleanup."}
    output = Path(__file__).with_name("model-cleanup-plan.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"executed": False, "proposed_count": len(proposed),
                      "proposed_unique_GiB": report["proposed_unique_GiB"], "plan": str(output)}))


if __name__ == "__main__":
    main()

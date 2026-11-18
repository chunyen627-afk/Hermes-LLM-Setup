"""Exercise the real Hermes desktop agent builder after switching its config.

Does not automate or claim to inspect the desktop renderer. Video mode submits
one real, short local H3 job through the model's tools.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

HOME = Path(r"C:\Users\pjunm\AppData\Local\hermes")
ROOT = HOME / "hermes-agent"
MODEL = "orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs"
os.environ["HERMES_HOME"] = str(HOME)
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["chat", "tool", "vision", "video"], required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    assert all(c.isalnum() or c in '-_' for c in args.label)
    import yaml
    cfg = yaml.safe_load((HOME / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["model"]["default"] == MODEL, "Hermes has not switched to the candidate"
    assert cfg["model"]["base_url"].rstrip("/") == "http://127.0.0.1:1234/v1"
    if args.mode == "vision":
        assert cfg["model"].get("supports_vision") is True
        assert cfg.get("agent", {}).get("image_input_mode") == "native"
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((60, 80, 240, 260), fill="red")
        draw.rectangle((390, 80, 570, 260), fill="blue")
        draw.text((155, 310), "STONE-864", fill="black",
                  font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 48))
        image.save(Path(__file__).with_name("vision-hermes-evidence.png"))
    from tui_gateway.server import _make_agent
    test_id = "strata-acceptance-" + args.mode + "-" + uuid.uuid4().hex
    agent = None
    report = {"mode": args.mode, "id": test_id, "status": "failed",
              "scope": "Real desktop backend agent; desktop renderer not observed."}
    suffix = ('-' + args.label) if args.label else ''
    output = Path(__file__).with_name("hermes-" + args.mode + "-verification" + suffix + ".json")
    started = time.monotonic()
    job_started = time.time()
    try:
        agent = _make_agent(test_id, test_id, platform_override="desktop")
        assert agent.model == MODEL, "Wrong model selected by desktop builder"
        agent.max_iterations = 30 if args.mode == "video" else 12
        agent.run_budget_seconds = 1200 if args.mode == "video" else 400
        if args.mode == "chat":
            prompt = "你好。這是本機聊天連線測試，請只回覆『聊天正常』，不要使用工具。"
        elif args.mode == "tool":
            prompt = ("請用 terminal 工具執行 printf STRATA_TOOL_OK，讀取實際結果後回覆結果。"
                      "這是唯讀工具測試，不要修改檔案，不要背景執行。")
        elif args.mode == "vision":
            prompt = ("請看這張本機圖片 C:/Users/pjunm/Strata/vision-hermes-evidence.png，"
                      "簡短回報圖中兩個色塊的顏色及形狀，並讀出圖片上的英文代碼。"
                      "請直接使用你的原生視覺，不要使用 terminal 或讀檔工具猜答案。")
        else:
            prompt = ("請把 C:/Users/pjunm/ComfyUI-H3/input/h3_duck_input.png 轉成3秒影片，"
                      "主體輕輕左右搖動，保持原貌。請使用 h3-local-video 技能，8步即可。"
                      "提示詞由你整理，用唯一的新工作ID，實際等待完成後給我影片路徑。")
        print(json.dumps({"stage": "agent_ready", "mode": args.mode,
                          "model": agent.model}, ensure_ascii=False), flush=True)
        result = agent.run_conversation(prompt)
        report["result"] = result
        assert not result.get("failed") and not result.get("interrupted"), "Agent failed or was interrupted"
        assert result.get("completed"), "Agent did not complete"
        final = result.get("final_response") or ""
        messages = result.get("messages") or []
        calls = [c for m in messages if m.get("role") == "assistant"
                 for c in m.get("tool_calls", [])]
        report["tool_names"] = [c.get("function", {}).get("name") for c in calls]
        if args.mode == "chat":
            assert "聊天正常" in final, "Chat response missing"
        elif args.mode == "tool":
            assert "terminal" in report["tool_names"], "No real terminal call"
            assert any(m.get("role") == "tool" and "STRATA_TOOL_OK" in str(m.get("content"))
                       for m in messages), "No terminal output"
            assert "STRATA_TOOL_OK" in final, "Tool result missing from answer"
        elif args.mode == "vision":
            assert not any(n in {"terminal", "read_file"} for n in report["tool_names"])
            assert "STONE-864" in final, "New image-only code was not read"
        else:
            assert "skill_view" in report["tool_names"] and "terminal" in report["tool_names"]
            jobs = Path(r"C:\Users\pjunm\ComfyUI-H3\user\default\h3_jobs")
            records = []
            for path in jobs.glob("*.json"):
                if path.stat().st_mtime < job_started:
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("state") == "completed" and record.get("verification"):
                    video = Path(record["output_path"])
                    if (video.is_file() and video.stat().st_mtime >= job_started
                            and str(video).replace("\\", "/") in final.replace("\\", "/")):
                        records.append(record)
            assert records, "No newly completed, decoded video delivered by agent"
            report["verified_video_jobs"] = records
        report["status"] = "passed"
    finally:
        if agent:
            agent.close()
        report["elapsed_s"] = round(time.monotonic() - started, 2)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"status": report["status"], "elapsed_s": report["elapsed_s"],
                          "report": str(output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

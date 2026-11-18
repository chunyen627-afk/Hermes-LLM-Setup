"""Live model acceptance checks. Requires the real Strata server on localhost."""
import json
import os
from pathlib import Path
import time

import requests

BASE = os.environ.get("STRATA_TEST_BASE", "http://127.0.0.1:8080").rstrip("/")
MODEL = "orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs"
RESULTS = []


def call(messages, **kwargs):
    body = {"model": MODEL, "messages": messages, "max_tokens": 256,
            "temperature": 0, "reasoning_effort": "none", **kwargs}
    start = time.monotonic()
    response = requests.post(BASE + "/v1/chat/completions", json=body, timeout=(10, 300))
    response.raise_for_status()
    result = response.json()
    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def run():
    health = requests.get(BASE + "/health", timeout=15)
    health.raise_for_status()
    models = requests.get(BASE + "/v1/models", timeout=15).json()
    assert MODEL in {m["id"] for m in models.get("data", [])}, "Wrong model served"
    result = call([{"role": "user", "content": "Compute 218 * 34. Reply only with the integer."}])
    assert "7412" in result["choices"][0]["message"].get("content", ""), "Arithmetic failed"
    RESULTS.append({"test": "arithmetic", "result": result})

    history = [{"role": "user", "content": "請記住代號 RIVER-927。只回覆收到。"}]
    first = call(history)
    history.extend([first["choices"][0]["message"], {"role": "user", "content": "剛才的代號是什麼？"}])
    second = call(history)
    assert "RIVER-927" in second["choices"][0]["message"].get("content", ""), "Multi-turn failed"
    RESULTS.append({"test": "chinese_multi_turn", "result": second})

    tools = [{"type": "function", "function": {"name": "multiply", "description": "Multiply two integers.",
              "parameters": {"type": "object", "properties": {"a": {"type": "integer"},
               "b": {"type": "integer"}}, "required": ["a", "b"], "additionalProperties": False}}}]
    messages = [{"role": "user", "content": "Use the multiply tool to compute 27 times 19."}]
    step = call(messages, tools=tools)
    message = step["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    assert calls and len(calls) == 1, "No single tool call returned"
    tool = calls[0]
    assert tool["function"]["name"] == "multiply", "Wrong function"
    args = json.loads(tool["function"]["arguments"])
    assert set(args) == {"a", "b"} and all(type(v) is int for v in args.values()), "Invalid tool arguments"
    assert {args["a"], args["b"]} == {27, 19}, "Wrong tool operands"
    messages.extend([message, {"role": "tool", "tool_call_id": tool["id"], "content": "513"}])
    finish = call(messages, tools=tools)
    assert "513" in finish["choices"][0]["message"].get("content", ""), "Tool result not used"
    RESULTS.append({"test": "tool_round_trip", "first": step, "result": finish})

    body = {"model": MODEL, "messages": [{"role": "user", "content": "Reply with STREAM_OK."}],
            "max_tokens": 128, "reasoning_effort": "none", "temperature": 0, "stream": True}
    chunks, done = [], False
    with requests.post(BASE + "/v1/chat/completions", json=body, stream=True,
                       timeout=(10, 300)) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith(b"data:"):
                continue
            value = line[5:].strip()
            if value == b"[DONE]":
                done = True
                break
            event = json.loads(value)
            for choice in event.get("choices", []):
                chunks.append(choice.get("delta", {}).get("content") or "")
    assert done and "STREAM_OK" in "".join(chunks), "Streaming did not finish correctly"
    RESULTS.append({"test": "streaming", "content": "".join(chunks), "done": done})

    # Closing a live response must release the single inference queue.
    body["messages"] = [{"role": "user", "content": "Write a detailed essay about mountain ecology."}]
    body["max_tokens"] = 512
    saw_content = False
    with requests.post(BASE + "/v1/chat/completions", json=body, stream=True,
                       timeout=(10, 300)) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                event = json.loads(line[5:].strip())
                if any(c.get("delta", {}).get("content") for c in event.get("choices", [])):
                    saw_content = True
                    break
    assert saw_content, "Cancellation check received no generated content"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = requests.get(BASE + "/v1/status", timeout=10).json()
        if not state.get("activity", {}).get("in_flight"):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("Cancelled stream did not release the inference queue")
    RESULTS.append({"test": "stream_cancel_queue_recovery", "result": "idle"})

    for number in range(3):
        answer = call([{"role": "user", "content": "Reply only with READY."}])
        assert "READY" in answer["choices"][0]["message"].get("content", ""), "Repeated request failed"
        RESULTS.append({"test": "repeat_" + str(number + 1), "result": answer})


if __name__ == "__main__":
    status = "failed"
    try:
        run()
        status = "passed"
    finally:
        report = {"status": status, "model": MODEL, "base_url": BASE, "checks": RESULTS,
                  "scope": "Live API checks only. Does not establish Hermes, media coexistence or restart acceptance."}
        target = Path(__file__).with_name("live-api-verification.json")
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": status, "checks_completed": len(RESULTS), "report": str(target)}))

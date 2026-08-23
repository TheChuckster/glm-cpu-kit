#!/usr/bin/env python3
"""Bounded live regression matrix for Kimi K3 chat termination and tools."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


STRUCTURAL = "<|"


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("K3_BASE_URL", "http://127.0.0.1:8080/v1"))
    parser.add_argument("--api-key", default=os.environ.get("K3_API_KEY"))
    parser.add_argument("--api-key-file", default=os.environ.get("K3_API_KEY_FILE", "~/.glm-api-key"))
    parser.add_argument("--model", default=os.environ.get("K3_MODEL", "kimi-k3"))
    parser.add_argument("--seeds", type=int, default=5, help="number of deterministic short-chat seeds")
    parser.add_argument("--tool-repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--long-agent", action="store_true", help="also send a roughly OpenCode-sized agent prompt")
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")
    if args.seeds < 1 or args.tool_repeats < 1 or args.timeout < 1:
        parser.error("--seeds, --tool-repeats, and --timeout must all be positive")
    if not args.api_key:
        key_path = Path(args.api_key_file).expanduser()
        if not key_path.is_file():
            parser.error(f"API key not supplied and file is unreadable: {key_path}")
        args.api_key = key_path.read_text().strip()
    return args


class Matrix:
    def __init__(self, args):
        self.args = args
        self.failures = []
        self.passes = 0

    def fail(self, name, detail):
        self.failures.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")

    def pass_(self, name, detail):
        self.passes += 1
        print(f"PASS  {name}: {detail}")

    def request(self, payload):
        request = urllib.request.Request(
            f"{self.args.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.args.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=self.args.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc

    def complete(self, payload):
        with self.request(payload) as response:
            return json.load(response)

    def stream(self, payload):
        payload = dict(payload)
        payload["stream"] = True
        content, reasoning, raw_deltas = [], [], []
        finish = predicted = None
        tool_deltas = 0
        tool_calls = {}
        done = False
        with self.request(payload) as response:
            for raw_line in response:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    done = True
                    break
                event = json.loads(data)
                timings = event.get("timings") or {}
                if isinstance(timings.get("predicted_n"), int):
                    predicted = timings["predicted_n"]
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    raw_deltas.append(json.dumps(delta))
                    if delta.get("content"):
                        content.append(delta["content"])
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                    if delta.get("tool_calls"):
                        tool_deltas += 1
                        for part in delta["tool_calls"]:
                            index = part.get("index", 0)
                            call = tool_calls.setdefault(index, {"name": "", "arguments": ""})
                            function = part.get("function") or {}
                            call["name"] += function.get("name") or ""
                            call["arguments"] += function.get("arguments") or ""
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        return {
            "content": "".join(content),
            "reasoning": "".join(reasoning),
            "finish": finish,
            "predicted": predicted,
            "tool_deltas": tool_deltas,
            "tool_calls": tool_calls,
            "done": done,
            "raw": "".join(raw_deltas),
        }

    @staticmethod
    def message_errors(response, finish, limit, require_content=True):
        errors = []
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        completion = (response.get("usage") or {}).get("completion_tokens")
        if choice.get("finish_reason") != finish:
            errors.append(f"finish_reason={choice.get('finish_reason')!r}")
        if not isinstance(completion, int) or completion >= limit:
            errors.append(f"completion_tokens={completion!r}/{limit}")
        if STRUCTURAL in json.dumps(message):
            errors.append("structural marker leaked")
        if require_content and not message.get("content"):
            errors.append("empty content")
        return errors, message, completion

    def short_chats(self):
        limit = 300
        for offset in range(self.args.seeds):
            seed = 424242 + offset
            name = f"chat seed {seed}"
            try:
                response = self.complete({
                    "model": self.args.model,
                    "seed": seed,
                    "max_tokens": limit,
                    "messages": [{"role": "user", "content": "hi"}],
                })
                errors, _, completion = self.message_errors(response, "stop", limit)
                if errors:
                    self.fail(name, "; ".join(errors))
                else:
                    self.pass_(name, f"{completion} generated tokens")
            except Exception as exc:
                self.fail(name, str(exc))

    def streaming_chat(self):
        name = "streaming chat"
        limit = 300
        try:
            result = self.stream({
                "model": self.args.model,
                "seed": 424242,
                "max_tokens": limit,
                "messages": [{"role": "user", "content": "hi"}],
            })
            errors = []
            if not result["done"]: errors.append("missing [DONE]")
            if result["finish"] != "stop": errors.append(f"finish={result['finish']!r}")
            if not result["content"]: errors.append("empty content")
            if result["predicted"] is None or result["predicted"] >= limit:
                errors.append(f"predicted_n={result['predicted']!r}/{limit}")
            if STRUCTURAL in result["raw"]: errors.append("structural marker leaked")
            if errors:
                self.fail(name, "; ".join(errors))
            else:
                self.pass_(name, f"{result['predicted']} generated tokens, clean [DONE]")
        except Exception as exc:
            self.fail(name, str(exc))

    @staticmethod
    def weather_tool():
        return {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }

    def tool_calls(self):
        prompt = "What is the weather in Oslo? You must call get_weather exactly once; do not answer from memory."
        first_message = None
        for offset in range(self.args.tool_repeats):
            seed = 525252 + offset
            name = f"tool call seed {seed}"
            try:
                response = self.complete({
                    "model": self.args.model,
                    "seed": seed,
                    "max_tokens": 500,
                    "tool_choice": "auto",
                    "messages": [{"role": "user", "content": prompt}],
                    "tools": [self.weather_tool()],
                })
                errors, message, completion = self.message_errors(response, "tool_calls", 500, False)
                calls = message.get("tool_calls") or []
                if len(calls) != 1:
                    errors.append(f"tool call count={len(calls)}")
                else:
                    function = calls[0].get("function") or {}
                    try:
                        tool_args = json.loads(function.get("arguments") or "")
                    except Exception:
                        tool_args = None
                    if function.get("name") != "get_weather": errors.append("wrong tool name")
                    if not isinstance(tool_args, dict) or tool_args.get("city") != "Oslo":
                        errors.append(f"bad typed arguments={tool_args!r}")
                if errors:
                    self.fail(name, "; ".join(errors))
                else:
                    self.pass_(name, f"{completion} generated tokens, typed Oslo argument")
                    first_message = first_message or message
            except Exception as exc:
                self.fail(name, str(exc))
        return prompt, first_message

    def streaming_tool(self):
        name = "streaming tool call"
        try:
            result = self.stream({
                "model": self.args.model,
                "seed": 525252,
                "max_tokens": 500,
                "tool_choice": "auto",
                "messages": [{
                    "role": "user",
                    "content": "What is the weather in Oslo? You must call get_weather exactly once.",
                }],
                "tools": [self.weather_tool()],
            })
            errors = []
            if not result["done"]: errors.append("missing [DONE]")
            if result["finish"] != "tool_calls": errors.append(f"finish={result['finish']!r}")
            if result["tool_deltas"] < 1: errors.append("no tool-call deltas")
            if result["predicted"] is None or result["predicted"] >= 500:
                errors.append(f"predicted_n={result['predicted']!r}/500")
            if STRUCTURAL in result["raw"]: errors.append("structural marker leaked")
            calls = result["tool_calls"]
            try:
                tool_args = json.loads(calls[0]["arguments"]) if len(calls) == 1 else None
            except Exception:
                tool_args = None
            if (len(calls) != 1 or calls.get(0, {}).get("name") != "get_weather" or
                    not isinstance(tool_args, dict) or tool_args.get("city") != "Oslo"):
                errors.append("invalid reconstructed tool call")
            if errors:
                self.fail(name, "; ".join(errors))
            else:
                self.pass_(name, f"{result['tool_deltas']} deltas, {result['predicted']} generated tokens")
        except Exception as exc:
            self.fail(name, str(exc))

    def replay(self, prompt, assistant):
        name = "tool-result replay"
        if assistant is None:
            self.fail(name, "no valid initial tool call to replay")
            return
        call = assistant["tool_calls"][0]
        replay_assistant = {
            "role": "assistant",
            "content": assistant.get("content") or "",
            "reasoning_content": assistant.get("reasoning_content") or "",
            "tool_calls": assistant["tool_calls"],
        }
        try:
            response = self.complete({
                "model": self.args.model,
                "seed": 626262,
                "max_tokens": 600,
                "messages": [
                    {"role": "user", "content": prompt},
                    replay_assistant,
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": "get_weather",
                        "content": "{\"temp_c\":3,\"conditions\":\"light rain\"}",
                    },
                ],
                "tools": [self.weather_tool()],
            })
            errors, message, completion = self.message_errors(response, "stop", 600)
            content = message.get("content") or ""
            if "3" not in content or "rain" not in content.lower():
                errors.append("did not use supplied tool result")
            if errors:
                self.fail(name, "; ".join(errors))
            else:
                self.pass_(name, f"{completion} generated tokens, used supplied result")
        except Exception as exc:
            self.fail(name, str(exc))

    def long_agent(self):
        if not self.args.long_agent:
            return
        name = "long agent-shaped prompt"
        filler = "\n".join(
            f"reference_item_{index:04d}: deterministic context for CPU agent validation and no requested action."
            for index in range(450)
        )
        prompt = filler + "\nIgnore the reference material and answer in one short sentence: what is 2+2?"
        try:
            response = self.complete({
                "model": self.args.model,
                "seed": 727272,
                "max_tokens": 800,
                "tool_choice": "auto",
                "messages": [{"role": "user", "content": prompt}],
                "tools": [self.weather_tool()],
            })
            errors, message, completion = self.message_errors(response, "stop", 800)
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            text = content + reasoning
            if "4" not in content: errors.append("never answered 2+2")
            windows = Counter(text[i:i + 60] for i in range(0, max(0, len(text) - 60), 20))
            repeated = windows.most_common(1)[0][1] if windows else 0
            if repeated >= 5: errors.append(f"60-character window repeated {repeated} times")
            if errors:
                self.fail(name, "; ".join(errors))
            else:
                self.pass_(name, f"{completion} generated tokens, answered without degeneration")
        except Exception as exc:
            self.fail(name, str(exc))


def main():
    args = arguments()
    matrix = Matrix(args)
    print(f"K3 live smoke: model={args.model} endpoint={args.base_url}")
    matrix.short_chats()
    matrix.streaming_chat()
    prompt, assistant = matrix.tool_calls()
    matrix.streaming_tool()
    matrix.replay(prompt, assistant)
    matrix.long_agent()
    print(f"\n{matrix.passes} passed; {len(matrix.failures)} failed")
    if matrix.failures:
        for failure in matrix.failures:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Phase 23.5.2, carl-compaction's long-turn case (user, 2026-10-13: "We'll need to test the final assistance
message"): can llama.cpp continue a reply from a final assistant message (the partial reply), with the A3B's Qwen
template as CARL sets it up? For each case: one request capped with max_tokens (it ends as "length", as CARL's cap
would make it), then the same request with the partial reply as the final assistant message; the joined text against
one uncapped request (temperature 0). Cases: plain text (thinking off), a reply cut inside its thinking, a reply cut
after its thinking inside the text, a reply cut inside a tool call. Also how /apply-template renders a final assistant
message.

    python3 continue_test.py http://127.0.0.1:8199     # a llama-server started as CARL starts it
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199"
TOOLS = [{"type": "function", "function": {"name": "read", "description": "Read a file.",
                                           "parameters": {"type": "object", "properties": {"filePath": {"type": "string"}},
                                                          "required": ["filePath"]}}}]


def post(path: str, body: dict) -> dict:
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=600) as f:
        return json.load(f)


def chat(messages: list, think: bool, max_tokens: int, tools: bool = False) -> dict:
    body = {"messages": messages, "temperature": 0, "max_tokens": max_tokens, "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": think, "preserve_thinking": True}}
    if tools:
        body["tools"] = TOOLS
    r = post("/v1/chat/completions", body)
    c = r["choices"][0]
    m = c["message"]
    return {"finish": c["finish_reason"], "content": m.get("content") or "", "reasoning": m.get("reasoning_content") or "",
            "tool_calls": m.get("tool_calls") or [], "n": r.get("usage", {}).get("completion_tokens")}


def render(messages: list, think: bool) -> str:
    return post("/apply-template", {"messages": messages, "chat_template_kwargs": {"enable_thinking": think,
                                                                                    "preserve_thinking": True}})["prompt"]


def show(name: str, full: dict, part: dict, cont: dict, joined_c: str, joined_r: str) -> None:
    print(f"\n=== {name}")
    print(f"uncapped: finish {full['finish']}, {full['n']} tokens; reasoning {len(full['reasoning'])} chars, "
          f"content {full['content'][:160]!r}, calls {json.dumps(full['tool_calls'])[:200]}")
    print(f"capped:   finish {part['finish']}; reasoning {part['reasoning'][-80:]!r}; content {part['content'][-80:]!r}")
    print(f"continued: finish {cont['finish']}; reasoning {cont['reasoning'][:120]!r}; content {cont['content'][:160]!r}; "
          f"calls {json.dumps(cont['tool_calls'])[:200]}")
    print(f"joined content == uncapped content: {joined_c == full['content']}; joined reasoning == uncapped: "
          f"{joined_r == full['reasoning']}")


def main() -> int:
    user = {"role": "user", "content": "List the first twelve chemical elements, one per line, as 'N. Name (Symbol)'."}
    msgs = [{"role": "system", "content": "You are a helpful assistant."}, user]
    print("render of a final assistant message (thinking off):")
    print(repr(render(msgs + [{"role": "assistant", "content": "1. Hydrogen (H)\n2. Helium"}], False)[-160:]))
    print("render of a final assistant message with partial reasoning (thinking on):")
    print(repr(render(msgs + [{"role": "assistant", "content": "", "reasoning_content": "The user wants the first"}], True)[-200:]))

    # 1. plain text, thinking off
    full = chat(msgs, False, 400)
    part = chat(msgs, False, 25)
    cont = chat(msgs + [{"role": "assistant", "content": part["content"]}], False, 400)
    show("plain text, thinking off", full, part, cont, part["content"] + cont["content"], "")

    # 2. thinking on, cut inside the thinking
    full = chat(msgs, True, 3000)
    part = chat(msgs, True, 40)
    cont = chat(msgs + [{"role": "assistant", "content": part["content"], "reasoning_content": part["reasoning"]}],
                True, 3000)
    show("thinking on, cut inside the thinking", full, part, cont, part["content"] + cont["content"],
         part["reasoning"] + cont["reasoning"])

    # 3. thinking on, cut after the thinking, inside the text
    n_think = None
    if full["finish"] == "stop":
        cut = len(full["reasoning"]) // 3 + 1      # a cap past the thinking: thinking tokens + some of the text
        n_think = int(len(full["reasoning"]) / 3.3) + 30
        part = chat(msgs, True, n_think)
        cont = chat(msgs + [{"role": "assistant", "content": part["content"], "reasoning_content": part["reasoning"]}],
                    True, 3000)
        show(f"thinking on, cut in the text (max_tokens {n_think})", full, part, cont, part["content"] + cont["content"],
             part["reasoning"] + cont["reasoning"])
        del cut

    # 4. a tool call, cut inside it (thinking off)
    tmsgs = [{"role": "system", "content": "You are a coding agent. Use the tools."},
             {"role": "user", "content": "Read the file /work/app/notes/store.py and tell me what it does."}]
    full = chat(tmsgs, False, 400, tools=True)
    part = chat(tmsgs, False, 12, tools=True)
    cont = chat(tmsgs + [{"role": "assistant", "content": part["content"]}], False, 400, tools=True)
    show("a tool call, cut inside it (thinking off)", full, part, cont, part["content"] + cont["content"], "")
    return 0


if __name__ == "__main__":
    sys.exit(main())

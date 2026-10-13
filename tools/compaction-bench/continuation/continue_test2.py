#!/usr/bin/env python3
"""Phase 23.5.2, the continuation test, round 2 (round 1: continue_test.py). Round 1 showed llama.cpp returns the
whole reply (the part sent as the final assistant message, then its continuation), and that a cut inside a tool call
is dropped by the server's parser. Here: (a) the continued reply against the uncapped one, exactly (text, thinking);
(b) a tool call cut inside it, taken raw from /completion (the rendered prompt, n_predict capped) and sent back as
the final assistant message: does the call come back whole and the same; (c) the same for a long file write, thinking
on (the raw text split at </think> into reasoning_content and content).

    python3 continue_test2.py http://127.0.0.1:8199
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199"
TOOLS = [{"type": "function", "function": {"name": "read", "description": "Read a file.",
                                           "parameters": {"type": "object", "properties": {"filePath": {"type": "string"}},
                                                          "required": ["filePath"]}}},
         {"type": "function", "function": {"name": "write", "description": "Write a file (its whole content).",
                                           "parameters": {"type": "object", "properties": {
                                               "filePath": {"type": "string"}, "content": {"type": "string"}},
                                               "required": ["filePath", "content"]}}}]


def post(path: str, body: dict) -> dict:
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=900) as f:
        return json.load(f)


def kw(think: bool) -> dict:
    return {"enable_thinking": think, "preserve_thinking": True}


def chat(messages: list, think: bool, max_tokens: int, tools: bool = False) -> dict:
    body = {"messages": messages, "temperature": 0, "max_tokens": max_tokens, "cache_prompt": False,
            "chat_template_kwargs": kw(think)}
    if tools:
        body["tools"] = TOOLS
    r = post("/v1/chat/completions", body)
    c = r["choices"][0]
    m = c["message"]
    calls = [{"name": t["function"]["name"], "args": json.loads(t["function"]["arguments"])} for t in m.get("tool_calls") or []]
    return {"finish": c["finish_reason"], "content": m.get("content") or "", "reasoning": m.get("reasoning_content") or "",
            "calls": calls}


def raw(messages: list, think: bool, n: int, tools: bool) -> str:
    """The model's raw text for the request, cut at n tokens (/completion on the rendered prompt)."""
    body = {"messages": messages, "chat_template_kwargs": kw(think)}
    if tools:
        body["tools"] = TOOLS
    prompt = post("/apply-template", body)["prompt"]
    return str(post("/completion", {"prompt": prompt, "n_predict": n, "temperature": 0, "cache_prompt": False})["content"])


def as_assistant(text: str, think: bool) -> dict:
    """The raw partial reply as a final assistant message: with thinking, the part before </think> is its reasoning
    (the rendered prompt already opened <think>)."""
    if think:
        if "</think>" in text:
            r, c = text.split("</think>", 1)
            return {"role": "assistant", "reasoning_content": r.strip("\n"), "content": c.lstrip("\n")}
        return {"role": "assistant", "reasoning_content": text.strip("\n"), "content": ""}
    return {"role": "assistant", "content": text}


def main() -> int:
    user = {"role": "user", "content": "List the first twelve chemical elements, one per line, as 'N. Name (Symbol)'."}
    msgs = [{"role": "system", "content": "You are a helpful assistant."}, user]
    print("(a) the continued reply against the uncapped one")
    for think, cap in ((False, 25), (True, 40), (True, 465)):
        full = chat(msgs, think, 3000)
        part = chat(msgs, think, cap)
        cont = chat(msgs + [{"role": "assistant", "content": part["content"], "reasoning_content": part["reasoning"]}],
                    think, 3000)
        print(f"  thinking {think}, cut at {cap}: finish {part['finish']} -> {cont['finish']}; content same "
              f"{cont['content'] == full['content']}; reasoning same {cont['reasoning'] == full['reasoning']}")

    print("(b) a tool call cut inside it (thinking off), raw")
    tmsgs = [{"role": "system", "content": "You are a coding agent. Use the tools."},
             {"role": "user", "content": "Read the file /work/app/notes/store.py and tell me what it does."}]
    full = chat(tmsgs, False, 400, tools=True)
    text = raw(tmsgs, False, 14, True)
    print(f"  raw partial: {text!r}")
    cont = chat(tmsgs + [as_assistant(text, False)], False, 400, tools=True)
    print(f"  uncapped {full['calls']}; continued finish {cont['finish']}, calls {cont['calls']}, content {cont['content'][:80]!r}; "
          f"same {cont['calls'] == full['calls']}")

    print("(c) a long write cut inside its content (thinking on), raw")
    wmsgs = [{"role": "system", "content": "You are a coding agent. Use the tools."},
             {"role": "user", "content": "Write /work/app/notes/model.py: a dataclass Note (id, text, done, due, tags) "
                                         "with to_dict and from_dict, docstrings on everything. Use the write tool."}]
    full = chat(wmsgs, True, 6000, tools=True)
    n_full = len(json.dumps(full["calls"])) // 3 + len(full["reasoning"]) // 3
    text = raw(wmsgs, True, int(n_full * 0.7), True)
    print(f"  uncapped: finish {full['finish']}, reasoning {len(full['reasoning'])} chars, calls "
          f"{[(c['name'], c['args'].get('filePath'), len(c['args'].get('content', ''))) for c in full['calls']]}")
    print(f"  raw partial ends: {text[-120:]!r}")
    cont = chat(wmsgs + [as_assistant(text, True)], True, 6000, tools=True)
    print(f"  continued: finish {cont['finish']}, calls "
          f"{[(c['name'], c['args'].get('filePath'), len(c['args'].get('content', ''))) for c in cont['calls']]}; "
          f"same call {cont['calls'] == full['calls']}; reasoning same {cont['reasoning'] == full['reasoning']}")
    if cont["calls"] and full["calls"] and cont["calls"] != full["calls"]:
        a, b = full["calls"][0]["args"].get("content", ""), cont["calls"][0]["args"].get("content", "")
        k = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
        print(f"  first difference at char {k}: uncapped {a[k-40:k+40]!r} / continued {b[k-40:k+40]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

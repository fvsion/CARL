#!/usr/bin/env python3
"""Phase 23.5.2, the continuation test, round 3 (user, 2026-10-13: "continue the testing"). What carl-compaction's
long-turn case needs beyond rounds 1-2: (A) does a continued reply stream only the new part (CARL stitches the stream
into the client's one response)? (B) real agent requests from the bench (OpenCode, thinking on, the tools, 44-61K
tokens): capped inside the next tool call while streaming, the cut call rebuilt as the template's text from the
streamed partial arguments (not from raw /completion), sent back as the final assistant message: does the same call
come back?

    python3 continue_test3.py http://127.0.0.1:8199 long-opencode-pycli.json long-opencode-pylib.json
"""
from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199"


def post(path: str, body: dict) -> dict:
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=1800) as f:
        return json.load(f)


def stream(path: str, body: dict) -> List[dict]:
    """The SSE chunks of a streamed request."""
    r = urllib.request.Request(BASE + path, data=json.dumps(dict(body, stream=True)).encode(),
                               headers={"Content-Type": "application/json"})
    out = []
    with urllib.request.urlopen(r, timeout=1800) as f:
        for line in f:
            line = line.decode().strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                out.append(json.loads(line[6:]))
    return out


def collect(chunks: List[dict]) -> Dict[str, Any]:
    """The streamed reply: its reasoning, its text, its tool calls (name and the arguments' text, maybe partial)."""
    reasoning, content, calls, finish = "", "", {}, None
    for ch in chunks:
        for c in ch.get("choices") or []:
            d = c.get("delta") or {}
            reasoning += d.get("reasoning_content") or ""
            content += d.get("content") or ""
            for tc in d.get("tool_calls") or []:
                k = tc.get("index", 0)
                e = calls.setdefault(k, {"name": "", "arguments": ""})
                fn = tc.get("function") or {}
                e["name"] += fn.get("name") or ""
                e["arguments"] += fn.get("arguments") or ""
            finish = c.get("finish_reason") or finish
    return {"reasoning": reasoning, "content": content, "calls": [calls[k] for k in sorted(calls)], "finish": finish}


def partial_args(text: str) -> List[Tuple[str, str, bool]]:
    """A JSON object's text, maybe cut anywhere, as (key, value, complete) pairs: strings decoded, other values as
    their text."""
    out: List[Tuple[str, str, bool]] = []
    i, n = text.find("{") + 1, len(text)

    def string(i: int) -> Tuple[Optional[str], int, bool]:
        j, buf = i + 1, []
        while j < n:
            ch = text[j]
            if ch == "\\":
                if j + 1 >= n:
                    break
                buf.append(text[j:j + 2])
                j += 2
                continue
            if ch == '"':
                return json.loads('"' + "".join(buf) + '"'), j + 1, True
            buf.append(ch)
            j += 1
        raw = "".join(buf)
        try:
            return json.loads('"' + raw + '"'), n, False
        except ValueError:
            return json.loads('"' + raw[:raw.rfind("\\")] + '"') if "\\" in raw else raw, n, False

    while i < n:
        while i < n and text[i] in " \n\t,":
            i += 1
        if i >= n or text[i] == "}":
            break
        key, i, done = string(i)
        if not done:
            break
        while i < n and text[i] in " \n\t:":
            i += 1
        if i >= n:
            out.append((str(key), "", False))
            break
        if text[i] == '"':
            val, i, done = string(i)
            out.append((str(key), str(val), done))
            if not done:
                break
        else:
            j = i
            while j < n and text[j] not in ",}":
                j += 1
            out.append((str(key), text[i:j].strip(), j < n))
            i = j
    return out


def as_template(name: str, args_text: str, after_text: bool) -> str:
    """A partial tool call as the A3B's template writes one (its own render of a call), left open at the cut."""
    s = ("\n\n" if after_text else "") + f"<tool_call>\n<function={name}>\n"
    for k, v, done in partial_args(args_text):
        s += f"<parameter={k}>\n{v}" + ("\n</parameter>\n" if done else "")
    return s


def calls_of(msg: dict) -> List[dict]:
    return [{"name": t["function"]["name"], "args": json.loads(t["function"]["arguments"])} for t in msg.get("tool_calls") or []]


def test_stream() -> None:
    msgs = [{"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "List the first twelve chemical elements, one per line, as 'N. Name (Symbol)'."},
            {"role": "assistant", "content": "1. Hydrogen (H)\n2. Helium (He)\n3. Lithium (Li)\n4"}]
    body = {"messages": msgs, "temperature": 0, "max_tokens": 200, "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": False, "preserve_thinking": True}}
    chunks = stream("/v1/chat/completions", body)
    got = collect(chunks)
    first = next((c["choices"][0]["delta"].get("content") for c in chunks if c.get("choices") and
                  c["choices"][0].get("delta", {}).get("content")), "")
    print(f"(A) streamed continuation: first text delta {first!r}; the streamed text starts {got['content'][:40]!r}; "
          f"repeats the part sent: {got['content'].startswith('1. Hydrogen')}; finish {got['finish']}")


def test_long(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        b = json.load(f)
    truth = b.pop("_truth", None)
    for k in ("stream", "stream_options", "id_slot"):
        b.pop(k, None)
    b.update(temperature=0, cache_prompt=True)
    full = post("/v1/chat/completions", dict(b, max_tokens=8192))
    fm = full["choices"][0]["message"]
    n_out = full["usage"]["completion_tokens"]
    fcalls = calls_of(fm)
    print(f"\n(B) {path}: prompt {full['usage']['prompt_tokens']} tokens; uncapped: finish "
          f"{full['choices'][0]['finish_reason']}, {n_out} tokens out, reasoning {len(fm.get('reasoning_content') or '')} "
          f"chars, calls {[(c['name'], {k: str(v)[:40] for k, v in c['args'].items()}) for c in fcalls]}; the session's "
          f"real next call {[t['function']['name'] for t in (truth or {}).get('tool_calls') or []]}")
    if not fcalls:
        print("   no call to cut")
        return
    arg_tokens = int(len(json.dumps(fcalls[0]["args"])) / 3.3)
    cap = max(8, n_out - arg_tokens // 2)                     # half-way through the call's arguments
    got = collect(stream("/v1/chat/completions", dict(b, max_tokens=cap)))
    print(f"   capped at {cap}: finish {got['finish']}; reasoning {len(got['reasoning'])} chars; text {got['content'][-60:]!r}; "
          f"partial call {[(c['name'], c['arguments'][-80:]) for c in got['calls']]}")
    text = got["content"]
    if got["calls"]:
        text += as_template(got["calls"][0]["name"], got["calls"][0]["arguments"], bool(got["content"].strip()))
    cont = post("/v1/chat/completions", dict(b, max_tokens=8192, messages=b["messages"] + [
        {"role": "assistant", "content": text, "reasoning_content": got["reasoning"]}]))
    ccalls = calls_of(cont["choices"][0]["message"])
    print(f"   continued: finish {cont['choices'][0]['finish_reason']}; calls "
          f"{[(c['name'], {k: str(v)[:40] for k, v in c['args'].items()}) for c in ccalls]}; the same call as uncapped: "
          f"{ccalls == fcalls}")
    if ccalls and ccalls != fcalls:
        for k in fcalls[0]["args"]:
            a, c = str(fcalls[0]["args"].get(k)), str(ccalls[0]["args"].get(k))
            if a != c:
                i = next((x for x in range(min(len(a), len(c))) if a[x] != c[x]), min(len(a), len(c)))
                print(f"   {k}: first difference at {i} of {len(a)}/{len(c)}: {a[i-30:i+30]!r} / {c[i-30:i+30]!r}")


def main() -> int:
    test_stream()
    for p in sys.argv[2:]:
        test_long(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""How many tokens a client's request costs before the user's own words: the system prompt
and the tool definitions, measured with the model's own chat template and tokenizer.

  python3 tools/prompt-size.py BODY.json [--server http://127.0.0.1:8080] [--per-tool]

BODY.json: a chat request as the client sent it (tools/req-capture-proxy.py --bodies DIR).
The server renders it with /apply-template (the chat template llama-server uses, tools
included) and counts the result with /tokenize. Reported: the whole prompt, the tool
definitions (with tools - without), the system prompt (the system messages alone, less an
empty conversation) and, with --per-tool, each tool's own share. The API key comes from
API_KEY_FILE or ~/.config/carl/api-key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List

JsonObj = Dict[str, Any]


def post(server: str, path: str, body: JsonObj, key: str) -> JsonObj:
    req = urllib.request.Request(server.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out: JsonObj = json.loads(r.read())
    return out


def tokens(server: str, key: str, messages: List[JsonObj], tools: List[JsonObj]) -> int:
    """Tokens of the prompt the chat template makes of these messages and tools."""
    body: JsonObj = {"messages": messages}
    if tools:
        body["tools"] = tools
    prompt = post(server, "/apply-template", body, key)["prompt"]
    return len(post(server, "/tokenize", {"content": prompt}, key)["tokens"])


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("body", help="a captured chat request (JSON)")
    ap.add_argument("--server", default="http://127.0.0.1:8080")
    ap.add_argument("--per-tool", action="store_true", help="each tool's share")
    a = ap.parse_args(argv)
    key_file = os.environ.get("API_KEY_FILE") or os.path.expanduser("~/.config/carl/api-key")
    with open(key_file, encoding="utf-8") as f:
        key = f.read().strip()
    with open(a.body, encoding="utf-8") as f:
        req: JsonObj = json.load(f)
    msgs: List[JsonObj] = req.get("messages") or []
    tools: List[JsonObj] = req.get("tools") or []
    system = [m for m in msgs if m.get("role") == "system"]
    probe = [{"role": "user", "content": "x"}]
    total = tokens(a.server, key, msgs, tools)
    no_tools = tokens(a.server, key, msgs, [])
    base = tokens(a.server, key, probe, [])
    sys_only = tokens(a.server, key, system + probe, [])
    print(f"whole prompt          {total:>7} tokens ({len(msgs)} messages, {len(tools)} tools)")
    print(f"tool definitions      {total - no_tools:>7}")
    print(f"system prompt         {sys_only - base:>7} ({sum(len(str(m.get('content', ''))) for m in system)} chars)")
    print(f"system + tools        {sys_only - base + total - no_tools:>7}")
    if a.per_tool and tools:
        print("\nper tool (the prompt without it, less):")
        shares = []
        for i, t in enumerate(tools):
            name = (t.get("function") or {}).get("name", f"#{i}")
            shares.append((total - tokens(a.server, key, msgs, tools[:i] + tools[i + 1:]), name))
        for n, name in sorted(shares, reverse=True):
            print(f"  {name:<28} {n:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

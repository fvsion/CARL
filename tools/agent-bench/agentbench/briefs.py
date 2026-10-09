"""The coder briefs of a run (Phase 23.4.3: the measurement of the brief's format): the full text of every task the
main agent gave the coder, its format, CARL's check of it, whether CARL refused it, and its tokens.

    collect(client, tools)      the briefs of a run's tool calls (the main agent's), in order
    check_texts(texts)          CARL's check, through node and the repo's client/shared/carl-brief.js (one checker)
    tokenizer(url, key)         a function that counts a text's tokens with the model server's POST /tokenize
    fill_tokens(doc, count)     fills a result line's missing brief_tokens (bench.py tokens)

A brief is one coder task: OpenCode's task call with subagent_type coder (or carl-coder): its prompt; Pi's subagent
call with agent coder: its task, and each coder item of its tasks and chain lists (a chain's {previous} as written).
refused: the call's result starts with CARL's refusal mark (events.BRIEF_MARK; after a client's own "Error:").
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import HERE, REPO
from . import events as ev

CHECKER = os.path.join(HERE, "brief_check.mjs")
BRIEF_LIB = os.path.join(REPO, "client", "shared", "carl-brief.js")
FORMATS = ("toml", "json", "kv", "other")
Brief = Dict[str, Any]
Counter = Callable[[str], Optional[int]]
Checker = Callable[[Sequence[str]], List[Dict[str, Any]]]


def coder_texts(client: str, tool: str, args: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """The coder tasks of one tool call, as (item, text): item is "prompt" (OpenCode), "task", "tasks[N]" or
    "chain[N]" (Pi). [] when the call gives the coder no task."""
    names = {c.lower() for c in ev.CODER_NAMES}

    def coder(v: Any) -> bool:
        return isinstance(v, str) and v.strip().lower() in names

    if client == "opencode":
        if tool == "task" and coder(args.get("subagent_type")):
            return [("prompt", _text(args.get("prompt")))]
        return []
    if client != "pi" or tool != "subagent":
        return []
    out: List[Tuple[str, str]] = []
    if coder(args.get("agent")):
        out.append(("task", _text(args.get("task"))))
    for key in ("tasks", "chain"):
        items = args.get(key)
        for n, item in enumerate(items if isinstance(items, list) else []):
            if isinstance(item, dict) and coder(item.get("agent")):
                out.append((f"{key}[{n}]", _text(item.get("task"))))
    return out


def _text(v: Any) -> str:
    if isinstance(v, str):
        return v
    return "" if v is None else json.dumps(v, ensure_ascii=False)


def check_texts(texts: Sequence[str], lib: str = BRIEF_LIB, node: Optional[str] = None,
                timeout: float = 60.0) -> List[Dict[str, Any]]:
    """CARL's check of each text (brief_check.mjs with the repo's carl-brief.js): {format, valid, problems}. Without
    node, or when it fails: format "unknown", valid None, and the reason in problems."""
    if not texts:
        return []
    exe = node or shutil.which("node")
    why = "node is not installed" if not exe else ""
    if exe:
        try:
            p = subprocess.run([exe, CHECKER, lib], input=json.dumps(list(texts)), capture_output=True, text=True,
                               timeout=timeout)
            if p.returncode == 0:
                out = json.loads(p.stdout)
                if isinstance(out, list) and len(out) == len(texts):
                    return [dict(x) if isinstance(x, dict) else {} for x in out]
            why = f"the check failed: {(p.stderr or p.stdout).strip()[-300:]}"
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            why = f"the check failed: {e}"
    return [{"format": "unknown", "valid": None, "problems": [f"Not checked: {why}."]} for _ in texts]


def collect(client: str, tools: Sequence[ev.ToolCall], check: Checker = check_texts) -> List[Brief]:
    """The briefs of a run's tool calls (the main agent's), in order: text (full), format, valid, problems, refused,
    and where it was (tool, item; continues: OpenCode's task_id, a task that continues an earlier one, which CARL
    does not check)."""
    found: List[Tuple[ev.ToolCall, str, str]] = []
    for call in tools:
        for item, text in coder_texts(client, call.name, call.args):
            found.append((call, item, text))
    checks = check([t for _, _, t in found])
    out: List[Brief] = []
    for (call, item, text), c in zip(found, checks):
        b: Brief = {"text": text, "format": c.get("format", "unknown"), "valid": c.get("valid"),
                    "problems": list(c.get("problems") or []),
                    "refused": call.result is not None and ev.is_refusal(call.result),
                    "tool": call.name, "item": item}
        if client == "opencode" and call.args.get("task_id"):
            b["continues"] = True
        out.append(b)
    return out


# ------------------------------------------------------------------------------------------------ tokens
def tokenize(url: str, key: str, text: str, timeout: float = 30.0) -> Optional[int]:
    """The number of tokens of a text: the llama.cpp server's POST /tokenize (no special tokens). None on an
    error."""
    body = json.dumps({"content": text, "add_special": False}).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url.rstrip("/") + "/tokenize", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        e.close()
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None
    tokens = doc.get("tokens") if isinstance(doc, dict) else None
    return len(tokens) if isinstance(tokens, list) else None


def tokenizer(url: str, key: str = "") -> Counter:
    """A token counter for one server (bench.py run: CARL's server of the batch; bench.py tokens: --url)."""
    def count(text: str) -> Optional[int]:
        return tokenize(url, key, text)
    return count


def count_all(briefs: Sequence[Brief], count: Optional[Counter]) -> List[Optional[int]]:
    """brief_tokens for a result line: one count for each brief, None when no server is known."""
    return [count(str(b.get("text", ""))) if count is not None else None for b in briefs]


def fill_tokens(doc: Dict[str, Any], count: Counter) -> int:
    """Fill a result line's missing brief_tokens (None, or a list shorter than its briefs). The number filled."""
    briefs = doc.get("briefs")
    if not isinstance(briefs, list) or not briefs:
        return 0
    have = doc.get("brief_tokens")
    tokens: List[Optional[int]] = list(have) if isinstance(have, list) else []
    tokens += [None] * (len(briefs) - len(tokens))
    filled = 0
    for i, b in enumerate(briefs):
        if tokens[i] is None and isinstance(b, dict):
            n = count(str(b.get("text", "")))
            if n is not None:
                tokens[i] = n
                filled += 1
    doc["brief_tokens"] = tokens[:len(briefs)]
    return filled

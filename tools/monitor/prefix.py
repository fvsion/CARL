"""A pre-read prompt cache for OpenCode (Phase 11): the KV cache of the prompt prefix every
OpenCode session shares (the tool definitions, then the system prompt up to its <env> block),
saved once per model and restored into slot 0 after a server start, so the first request
reads only what is new (measured on the 35B IQ3: 768 tokens in 1.6 s instead of ~9.9K in 17 s).

Where the pieces come from:
  - the spec: OpenCode's carl-prefix-cache plugin writes ~/.config/carl/prefix/MODEL.json
    (the system text and the tools as they are sent) whenever they change;
  - the prompt: the server renders it with the model's own chat template (/apply-template)
    and tokenizes it (/tokenize); the prefix is the tokens shared with the full prompt up
    to the environment block (where the working folder and the date start);
  - the state: /completion with exactly those tokens (n_predict 0) fills an idle slot, and
    /slots/N?action=save writes it to ~/.config/carl/slots (--slot-save-path). The hybrid Qwen
    models can't roll back their recurrent state, so the slot must end exactly at the prefix;
  - the slot: an idle, empty one; else the idle one holding least, whose conversation first
    moves to the RAM prompt cache (llama.cpp does that when a slot takes a new task: a
    1-token task, then the restore). After a start, and again when the spec changes.

Safe by construction: the file name carries a hash of the model file, the KV type, the
llama.cpp build and the prefix tokens, so a changed prefix (a new tool, a new prompt) gets a
new file, built at the next start; and if a request still differs inside a restored prefix,
llama.cpp reads the whole prompt as it would without one. Pure, except the file helpers.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import List, Optional, Sequence, Tuple

from .model import JSONDict, jdict

ENV_MARKER = "\nHere is some useful information about the environment you are running in:"
MIN_TOKENS = 1024                   # a shorter prefix is not worth a file
SPEC_SCHEMA = 1


def load_spec(folder: str, model: str) -> Optional[JSONDict]:
    """The spec for a model: its own, else the newest one (CARL's prompts name the model:
    with_model() changes that); None when there is none."""
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json")]
    except OSError:
        return None
    own = f"{model}.json"
    order = ([own] if own in names else []) + sorted((n for n in names if n != own),
                                                      key=lambda n: -os.path.getmtime(os.path.join(folder, n)))
    for n in order:
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                doc = jdict(json.load(f))
        except (OSError, ValueError):
            continue
        if doc.get("schema") == SPEC_SCHEMA and isinstance(doc.get("system"), str) and isinstance(doc.get("tools"), list):
            return doc
    return None


def with_model(spec: JSONDict, model: str) -> Tuple[str, List[object]]:
    """(system text, tools) for `model`: OpenCode's system prompt names the model it runs."""
    system = str(spec.get("system", ""))
    old = str(spec.get("model", ""))
    if old and old != model:
        system = system.replace(old, model)
    tools = spec.get("tools")
    return system, list(tools) if isinstance(tools, list) else []


def template_body(system: str, tools: Sequence[object], model: Optional[str]) -> JSONDict:
    """The /apply-template request: the system prompt, a placeholder user turn, the tools."""
    body: JSONDict = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "x"}],
                      "tools": list(tools)}
    if model:
        body["model"] = model                       # a router routes by it
    return body


def cut_text(rendered: str, system: str) -> str:
    """The rendered prompt up to the environment block (or the end of the system text)."""
    i = rendered.find(ENV_MARKER)
    if i < 0:
        j = rendered.find(system)
        i = j + len(system) if j >= 0 else -1
    return rendered[:i] if i > 0 else ""


def choose_slot(slots: Sequence[Tuple[int, bool, int]]) -> Optional[Tuple[int, bool]]:
    """(slot id, its conversation must first go to the RAM cache) for (id, busy, prompt tokens)
    slots: an idle empty slot, else the idle slot holding least; None when all are busy."""
    idle = [(n, sid) for sid, busy, n in slots if not busy]
    if not idle:
        return None
    n, sid = min(idle)
    return sid, n > 0


def common_prefix(a: Sequence[int], b: Sequence[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def slot_file(model: str, model_file: str, kv: str, build: str, tokens: Sequence[int]) -> str:
    """The saved slot's file name: the model, then a hash of everything the state depends on."""
    try:
        st = os.stat(model_file)
        ident = f"{model_file}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        ident = model_file
    h = hashlib.sha256(json.dumps([ident, kv, build, list(tokens)]).encode()).hexdigest()[:16]
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in model)[:80]
    return f"carl-prefix-{safe}-{h}.bin"


def stale_files(folder: str, keep: str) -> List[str]:
    """This model's other saved prefixes (replaced by `keep`)."""
    head = keep.rsplit("-", 1)[0] + "-"
    try:
        return [os.path.join(folder, n) for n in os.listdir(folder)
                if n.startswith(head) and n.endswith(".bin") and n != keep]
    except OSError:
        return []

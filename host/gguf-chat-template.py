#!/usr/bin/env python3
"""Write a GGUF's embedded chat template, patched so OpenAI-style "no
reasoning" efforts turn thinking off, for llama-server --chat-template-file.

Why: Qwen3.8's template only disables thinking via enable_thinking=false
(chat_template_kwargs). OpenCode's TUI sends reasoning_effort but does not
forward custom variant options such as chat_template_kwargs (verified with a
request-capture proxy, 2026-09-24), so reasoning_effort "none" must map to
enable_thinking=false inside the template. Everything else is unchanged.

Usage: gguf-chat-template.py MODEL.gguf OUT.jinja
Exit 3 if the template has no enable_thinking switch (nothing to patch).
"""
import struct, sys

model, out = sys.argv[1], sys.argv[2]
PATCH = (
    "{#- serve-llama.sh: reasoning_effort none/minimal/off => thinking off (OpenCode) -#}\n"
    "{%- if reasoning_effort is defined and reasoning_effort in "
    "('none', 'minimal', 'off', 'disable', 'disabled') %}"
    "{%- set enable_thinking = false %}{%- endif %}\n"
)

with open(model, "rb") as f:
    head = f.read(64 * 1024 * 1024)          # KV metadata sits at the front
if head[:4] != b"GGUF":
    sys.exit(f"not a GGUF file: {model}")
o = 4
def rd(fmt):
    global o
    v = struct.unpack_from(fmt, head, o); o += struct.calcsize(fmt); return v[0]
def rstr():
    global o
    n = rd("<Q"); s = head[o:o + n]; o += n; return s
SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
rd("<I"); rd("<Q"); nkv = rd("<Q")
tpl = None
for _ in range(nkv):
    key = rstr().decode(); t = rd("<I")
    if t == 8:
        val = rstr()
        if key == "tokenizer.chat_template":
            tpl = val.decode(); break
    elif t == 9:
        at = rd("<I"); n = rd("<Q")
        if at == 8:
            for _ in range(n): rstr()
        else:
            o += SIZES[at] * n
    else:
        o += SIZES[t]
if tpl is None:
    sys.exit("no tokenizer.chat_template in GGUF")
if "enable_thinking" not in tpl:
    sys.exit(3)
with open(out, "w") as f:
    f.write(PATCH + tpl)

#!/usr/bin/env python3
"""Write a GGUF's embedded chat template, patched so OpenAI-style "no
reasoning" efforts turn thinking off, for llama-server --chat-template-file.

Why: Qwen3.8's template only disables thinking via enable_thinking=false
(chat_template_kwargs). OpenCode's TUI sends reasoning_effort but does not
forward custom variant options such as chat_template_kwargs (verified with a
request-capture proxy, 2026-09-24), so reasoning_effort "none" must map to
enable_thinking=false inside the template. A Qwen template without preserve_thinking gets it
(see patched()). Everything else is unchanged.

Usage: gguf-chat-template.py MODEL.gguf OUT.jinja
Exit 3 if the template has no enable_thinking switch (nothing to patch).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.dont_write_bytecode = True                    # keep the repo free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

# The one GGUF metadata parser (tools/carl_core/domain/gguf.py): chat_template, GGUFError.
from carl_core.domain.gguf import LOCAL_HEADER_BYTES, GGUFError, chat_template  # noqa: E402

__all__ = ["GGUFError", "chat_template", "patched", "main"]

EXIT_NOTHING_TO_PATCH = 3
PATCH = (
    "{#- serve-llama.sh: reasoning_effort none/minimal/off => thinking off (OpenCode) -#}\n"
    "{%- if reasoning_effort is defined and reasoning_effort in "
    "('none', 'minimal', 'off', 'disable', 'disabled') %}"
    "{%- set enable_thinking = false %}{%- endif %}\n"
)


KEEP_REASONING = "{%- if loop.index0 > ns.last_query_index %}"
KEEP_REASONING_PATCHED = ("{%- if (preserve_thinking is defined and preserve_thinking is true) or "
                          "(loop.index0 > ns.last_query_index) %}")


def patched(template: str) -> str | None:
    """The template with the thinking-off rule in front; None if it has no switch. A Qwen template
    without preserve_thinking (the 9B's) also gets it, as the 35B's and 27B's have it: earlier
    replies keep their reasoning when the server asks (CARL does), so the next prompt starts with
    exactly what the model generated and a saved conversation can be continued (carl-cache.js)."""
    if "enable_thinking" not in template:
        return None
    if "preserve_thinking" not in template:
        template = template.replace(KEEP_REASONING, KEEP_REASONING_PATCHED)
    return PATCH + template


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Write a GGUF's chat template with a reasoning_effort=none => "
                                             "thinking-off rule (exit 3: no enable_thinking switch).")
    ap.add_argument("model", help="the .gguf file")
    ap.add_argument("out", help="the .jinja file to write")
    a = ap.parse_args(argv)
    try:
        with open(a.model, "rb") as f:
            head = f.read(LOCAL_HEADER_BYTES)  # the metadata sits at the front
        tpl = chat_template(head)
    except OSError as e:
        sys.exit(f"cannot read {a.model}: {e.strerror}")
    except GGUFError as e:
        sys.exit(f"{e}: {a.model}")
    if tpl is None:
        sys.exit("no tokenizer.chat_template in GGUF")
    out = patched(tpl)
    if out is None:
        return EXIT_NOTHING_TO_PATCH
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

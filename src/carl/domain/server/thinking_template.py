"""The thinking toggle's chat template: a model's own chat template with one rule in front, so OpenAI-style "no
reasoning" efforts turn thinking off (llama-server --chat-template-file).

Why: Qwen3.8's template only disables thinking via enable_thinking=false (chat_template_kwargs). OpenCode's TUI
sends reasoning_effort but does not forward custom variant options such as chat_template_kwargs (verified with a
request-capture proxy, 2026-09-24), so reasoning_effort "none" must map to enable_thinking=false inside the
template. A Qwen template without preserve_thinking gets it (see patched()). Everything else is unchanged.

Pure: the template text comes in, the patched text goes out. The files are adapters/common/template_files.py's
(TemplateStore, contract C-028; the layout C-217); host/gguf-chat-template.py writes one by hand.
"""
from __future__ import annotations

from typing import Optional

PATCH = (
    "{#- serve-llama.sh: reasoning_effort none/minimal/off => thinking off (OpenCode) -#}\n"
    "{%- if reasoning_effort is defined and reasoning_effort in "
    "('none', 'minimal', 'off', 'disable', 'disabled') %}"
    "{%- set enable_thinking = false %}{%- endif %}\n"
)
KEEP_REASONING = "{%- if loop.index0 > ns.last_query_index %}"
KEEP_REASONING_PATCHED = ("{%- if (preserve_thinking is defined and preserve_thinking is true) or "
                          "(loop.index0 > ns.last_query_index) %}")
SUFFIX = ".thinking-toggle.jinja"               # NAME.thinking-toggle.jinja in the templates folder


def patched(template: str) -> Optional[str]:
    """The template with the thinking-off rule in front; None if it has no switch. A Qwen template
    without preserve_thinking (the 9B's) also gets it, as the 35B's and 27B's have it: earlier
    replies keep their reasoning when the server asks (CARL does), so the next prompt starts with
    exactly what the model generated and a saved conversation can be continued (carl-cache.js)."""
    if "enable_thinking" not in template:
        return None
    if "preserve_thinking" not in template:
        template = template.replace(KEEP_REASONING, KEEP_REASONING_PATCHED)
    return PATCH + template

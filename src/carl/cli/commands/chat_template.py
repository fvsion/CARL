"""`carl chat-template MODEL.gguf OUT.jinja` (hidden): a GGUF's own chat template, with the thinking toggle's rule
(domain/server/thinking_template.py: reasoning_effort "none" turns thinking off), for llama-server's
--chat-template-file. host/gguf-chat-template.py's command, with its texts and exit codes: exit 3 when the template
has no enable_thinking switch (nothing to patch), exit 1 with one line when the file cannot be read. Contract C-414.
"""
from __future__ import annotations

import sys
from typing import List

from ...domain.memory.gguf import LOCAL_HEADER_BYTES, GGUFError, chat_template
from ...domain.server.thinking_template import patched
from .base import Cli

EXIT_NOTHING_TO_PATCH = 3
USAGE = "usage: chat-template MODEL.gguf OUT.jinja"


def run(cli: Cli, argv: List[str]) -> int:
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    model, out = argv
    try:
        with open(model, "rb") as f:
            head = f.read(LOCAL_HEADER_BYTES)              # the metadata sits at the front
        tpl = chat_template(head)
    except OSError as e:
        print(f"cannot read {model}: {e.strerror}", file=sys.stderr)
        return 1
    except GGUFError as e:
        print(f"{e}: {model}", file=sys.stderr)
        return 1
    if tpl is None:
        print("no tokenizer.chat_template in GGUF", file=sys.stderr)
        return 1
    text = patched(tpl)
    if text is None:
        return EXIT_NOTHING_TO_PATCH
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return 0

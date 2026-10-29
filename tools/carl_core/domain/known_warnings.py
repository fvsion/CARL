"""The warning and error lines llama.cpp writes in CARL's normal work, each with its reason (Phase 23.5 item 8). The
dashboard shows such a line grey with its reason under it, and its HEALTH card counts it as a routine notice, not as a
warning. Collected from the logs of llama.cpp 0.5 and 0.6. Pure.

Not here on purpose: "unauthorized: Invalid API Key" (a client with an old key: a real problem for it), "no API key is
set" (a server CARL did not start), "n_ctx_seq (N) > n_ctx_train (N)" (a false alarm with 2 slots, whose shared size
is slots x window, but a real one when one slot's window is over the trained length: the line cannot tell which).
"""
from __future__ import annotations

from typing import Optional, Tuple

# (a part of the line, why it is normal), first match wins
KNOWN: Tuple[Tuple[str, str], ...] = (
    ("stop: cancel task", "a client stopped a request (Esc, a closed connection, a compaction)"),
    ("erasing old context checkpoint", "llama.cpp drops the oldest checkpoint as a session grows"),
    ("making room for prompt cache entry", "the RAM cache is full: its oldest saved conversation goes"),
    ("server default port will be changed", "llama.cpp's own default port changes; CARL always sets the port"),
    ("chat template supports preserving reasoning", "CARL keeps the model's thinking on purpose (preserve_thinking)"),
    ("model has unused tensor", "the model's MTP head is not used by this speculation setting"),
    ("requires ctx_other to be set", "llama.cpp's memory probe with the Gemma 4 drafter (an error line in normal work)"),
    ("control-looking token", "Gemma 4's vocabulary: llama.cpp sets a token's type when the model loads"),
    ("special_eog_ids contains", "Gemma 4's vocabulary: its end-of-text tokens, when the model loads"),
    ("sharing with layer", "Gemma 4's last layers share the cache of earlier layers (the model's design)"),
    ("using full-size SWA cache", "the full cache (cache.swa): saved sessions can be restored"),
    ("need to evaluate at least", "the whole prompt was in the slot already (restored or repeated): one token is "
                                  "read again"),
    ("n_past was set to", "with the line before it: a prompt that was in the slot already"),
    ("router mode enabled", "llama.cpp's advice for router mode; CARL listens on this Mac or the VM address only"),
    ("failed to measure the memory of the extra model", "llama.cpp's memory probe with a drafter (an older start)"),
)


def known(line: str) -> Optional[str]:
    """The reason when the line is a known one, else None."""
    return next((why for part, why in KNOWN if part in line), None)

"""The server log's RAM cache lines, and which lines of a -lv 4 server CARL keeps in its log (Phase 23.4.4 item 12).

llama.cpp reports its RAM cache (--cache-ram) only at log verbosity 4 ("-lv 4"):
    0.03.729.393 I srv        update:  - cache state: 1 prompts, 3.409 MiB (limits: 2048.000 MiB, 8192 tokens, 25831 est)
No endpoint has it (/metrics, /slots: llama.cpp 0.6.0), so CARL starts the server at -lv 4 and writes its log
through a filter (tools/llama-log-filter.py) that keeps the log about the size it has at the default level.

The user's decision (2026-10-09): "filter plus upstream doc". Measured on llama.cpp 0.6.0 (build 11429), the E4B,
four requests (docs: phase23.4.4/lv4-sample): llama.cpp writes its -lv 4 lines with the same "I" prefix as its
default ones, so the level cannot tell them apart. The filter drops the kinds of lines seen only at -lv 4 in normal
work (LV4_ONLY: the per-request detail) and keeps everything else: the default lines, every warning and error, the
RAM cache's lines, the one-time start detail (about 200 lines per start: the model's metadata and buffers) and any
line it does not know. So an unknown line is never lost; a new -lv 4 kind in a later llama.cpp only makes the log
longer. Per request: 41 lines kept against 29 at the default level and 148 at -lv 4. Pure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

VERBOSITY = 4           # the log verbosity that has the RAM cache's lines

_LINE = re.compile(r"^\d+\.\d+\.\d+\.\d+ ([IWED]) \S+\s+(\S+?):\s?(.*)$")   # --log-timestamps --log-prefix
# The RAM cache's own lines: always kept.
_CACHE = re.compile(r"cache state:|prompt_save:|making room for prompt cache")
# The kinds of line seen only at -lv 4 in normal work (llama.cpp 0.6.0), as "function: message" (the function's name
# as llama.cpp cuts it to 12 characters).
LV4_ONLY = tuple(re.compile(p) for p in (
    r"^update_slots: all slots are idle",
    r"^server_strea: conv_id=",
    r"^operator\(\): chat format:",
    r"^get_availabl: id .* - (skipping|checking sim)",
    r"^get_availabl: (updating prompt cache|prompt cache update took)",
    r"^load: +- (looking for better prompt|prompt with length)",
    r"^update: +- prompt 0x",
    r"^launch_slot_: .*sampler (chain|params)",
    r"^operator\(\): id .* \| (new prompt|cached n_tokens|checking checkpoint|restored context checkpoint"
    r"|erased invalidated)",
    r"^create_check:",
    r"^init_sampler:",
))


def keep_line(line: str, previous_kept: bool = True) -> bool:
    """Whether the log keeps this line of a -lv 4 server. A line without llama.cpp's prefix continues the line before
    it (the sampler's parameters, a template) and goes with it."""
    m = _LINE.match(line)
    if not m:
        return previous_kept
    level, func, msg = m.groups()
    text = f"{func}: {msg}"
    if _CACHE.search(text) or level in ("W", "E"):
        return True
    return not any(p.search(text) for p in LV4_ONLY)


@dataclass(frozen=True)
class CacheState:
    """The RAM cache as the server's last "cache state" line says: the prompts it holds (each a session or a saved
    prompt that left a slot), their memory and its limits."""
    prompts: int
    bytes: int
    limit_bytes: int
    limit_tokens: int


_STATE = re.compile(r"cache state: (\d+) prompts?, ([\d.]+) MiB \(limits: ([\d.]+) MiB, (\d+) tokens")
MIB = 2 ** 20


def cache_state(line: str) -> Optional[CacheState]:
    """The RAM cache's state from a "cache state" line; None for any other line."""
    m = _STATE.search(line)
    if not m:
        return None
    return CacheState(prompts=int(m.group(1)), bytes=int(float(m.group(2)) * MIB),
                      limit_bytes=int(float(m.group(3)) * MIB), limit_tokens=int(m.group(4)))


def counts_cache(cmd: str) -> bool:
    """Whether a server's command line has the verbosity that reports the RAM cache (-lv / --log-verbosity N, N >= 4)."""
    m = re.search(r"(?:^|\s)(?:-lv|--log-verbosity|--verbosity)[ =](\d+)\b", cmd)
    return bool(m) and int(m.group(1)) >= VERBOSITY

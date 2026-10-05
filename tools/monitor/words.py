"""The glossary's names for values the server and config.json keep in their own form
(docs/phase21/glossary.md): speculation modes, the context memory type, the network, the download
status, Auto fit's goal and candidates, counts with their plural, and the server's uptime. Config
keys and flags keep their names; the full detail level shows them next to these words. Pure."""
from __future__ import annotations

from typing import Optional

from carl_core.domain.units import duration

SPEC_NAMES = {"none": "none", "ngram-mod": "n-gram", "draft-mtp": "MTP", "draft-mtp,ngram-mod": "MTP + n-gram"}
KV_NAMES = {"q4_0": "q4 (small)", "q8_0": "q8 (large)", "f16": "f16 (largest)"}
STATUS_NAMES = {"downloaded": "downloaded", "partial": "partial", "missing": "not downloaded", "bad": "bad file"}
GOAL_NAMES = {"everyday": "everyday (fast first)", "hard-code": "hard code (better code, slower)"}
SCOPE_NAMES = {"catalogue": "catalogue", "downloaded": "downloaded only"}
DEPTH_NAMES = {"quick": "quick", "default": "normal", "long": "long"}
THINKING_NAMES = {"on-off": "on / off", "effort": "levels (low, medium, high) and off", "always": "always on",
                  "none": "no thinking"}


def plural(n: float, word: str, many: Optional[str] = None) -> str:
    """'1 slot', '2 slots' (many: an irregular plural)."""
    k = int(n)
    return f"{k} {word}" if k == 1 else f"{k} {many or word + 's'}"


def spec_name(spec: object, n: object = None) -> str:
    """A speculation mode in the glossary's words, with its guesses: 'MTP + n-gram, 1 guess'."""
    name = SPEC_NAMES.get(str(spec), str(spec))
    if n in (None, "") or str(spec) == "none":
        return name
    return f"{name}, {plural(int(str(n)) if str(n).isdigit() else 1, 'guess', 'guesses')}"


def kv_name(kv: object, short: bool = False) -> str:
    """The context memory type: 'q4 (small)' (short: 'q4')."""
    text = KV_NAMES.get(str(kv), str(kv))
    return text.split(" ")[0] if short else text


def net_name(net: object) -> str:
    """Who can connect: local, vm or one address (config llama.net / llama.host)."""
    v = str(net)
    if v == "local":
        return "this Mac only"
    if v == "vm":
        return "this Mac and the VM"
    return f"only {v} (other computers)" if v.count(".") == 3 else v


def status_name(status: object) -> str:
    """A download status: downloaded, not downloaded, partial, bad file."""
    return STATUS_NAMES.get(str(status), str(status))


def goal_name(goal: object) -> str:
    return GOAL_NAMES.get(str(goal), str(goal))


def scope_name(scope: object) -> str:
    return SCOPE_NAMES.get(str(scope), str(scope))


def etime_seconds(etime: Optional[str]) -> Optional[float]:
    """ps's elapsed time ([[dd-]hh:]mm:ss) in seconds; None when it can't be read."""
    if not etime:
        return None
    try:
        days, _, rest = etime.rpartition("-")
        parts = [int(x) for x in rest.split(":")]
    except ValueError:
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs + (int(days) * 86400 if days.isdigit() else 0)


def uptime(etime: Optional[str]) -> str:
    """The server's uptime as '1 h 12 min' ('' when unknown)."""
    s = etime_seconds(etime)
    return duration(s) if s is not None else ""

"""What CARL's pieces are called in the clients' folders now, and what earlier versions left there that is still
recognised as CARL's own. Pure: the earlier names and values come in as a Legacy (data/legacy.json).

- Renamed in 1.2.0 (CARL was called LLM-Deploy): an install with the old names (state files, prompt folder, provider
  id, markers) is moved to the new ones; only CARL's own pieces.
- Removed in 1.2.0 (CARL is llama.cpp only): our MTPLX provider, the OpenCode plugin mtplx-session-headers and the Pi
  extension mtplx-request-policy.ts are taken out of an earlier install; a provider, plugin or extension of the
  user's own with those names stays.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, FrozenSet, Tuple

PROVIDER = "llamacpp"


@dataclass(frozen=True)
class Names:
    """What CARL's own pieces are called in the clients' folders."""
    alt: str            # our provider's id when the user owns one called PROVIDER
    state: str          # the state file next to each config
    prompt_dir: str     # OpenCode: the folder of the coder prompt and the delegation rule
    block: str          # Pi APPEND_SYSTEM.md: <!-- {block}:delegation begin --> ... end -->
    ext_marker: str     # marks CARL's changes in the vendored Pi subagent extension
    key_file: str       # the API-key file our providers read (under the home folder)


NAMES = Names(alt="carl", state="carl.json", prompt_dir="carl", block="carl", ext_marker="CARL:",
              key_file=".config/carl/api-key")
# Our providers' display names (recognised as ours with or without the state file): the name has the server's computer
# in it (data/words.json, host), so there is one for each OS.
OUR_NAMES: FrozenSet[str] = frozenset({"llama.cpp (Mac host)", "llama.cpp (Windows host)", "llama.cpp (Linux host)",
                                       "MTPLX (Mac host, ≤48K)", "MTPLX (Mac host)"})
# Pieces of the MTPLX support removed in 1.2.0 (taken out of an earlier install).
OLD_MTPLX_IDS = ("mtplx", "llm-deploy-mtplx")
OLD_OC_PLUGIN = "plugins/mtplx-session-headers"
OLD_OC_PLUGIN_SIG = "MTPLXSessionHeaders"
OLD_PI_EXT = "extensions/mtplx-request-policy.ts"
OLD_PI_EXT_SIG = "Pi <-> MTPLX request bridge"


@dataclass(frozen=True)
class Legacy:
    """The earlier names and values: CARL's names before the rename (old), every API-key file our providers read
    (now and before; '/'-separated, under the home folder), and the default models earlier versions set."""
    old: Names
    key_files: Tuple[str, ...]
    old_defaults: FrozenSet[str]

    @property
    def our_keys(self) -> Tuple[str, ...]:
        """The key files as the ends of a path ("/.config/carl/api-key")."""
        return tuple("/" + k for k in self.key_files)

    def has_our_key(self, ref: object) -> bool:
        """An apiKey reference that reads one of our key files (the current or an earlier path), with either
        separator (a Windows home gives "\\")."""
        text = str(ref).replace("\\", "/")
        return any(k in text for k in self.our_keys)


def parse(doc: Any) -> Legacy:
    """data/legacy.json, checked; ValueError naming the problem."""
    if not isinstance(doc, dict):
        raise ValueError("legacy.json: not an object")
    old, keys, defaults = doc.get("old_names"), doc.get("key_files"), doc.get("old_defaults")
    names = [f.name for f in fields(Names)]
    if not (isinstance(old, dict) and set(old) == set(names) and all(isinstance(v, str) and v for v in old.values())):
        raise ValueError(f"legacy.json: old_names must give {', '.join(names)}")
    if not (isinstance(keys, list) and all(isinstance(k, str) and k for k in keys)):
        raise ValueError("legacy.json: key_files must be a list of paths")
    if not (isinstance(defaults, dict) and all(isinstance(defaults.get(k), list) for k in ("models", "as_is"))):
        raise ValueError("legacy.json: old_defaults must have models and as_is")
    o = Names(**{k: str(old[k]) for k in names})
    models = [str(m) for m in defaults["models"]]
    return Legacy(o, (NAMES.key_file, o.key_file, *keys),
                  frozenset({f"{p}/{m}" for p in (PROVIDER, o.alt) for m in models} | {str(x) for x in defaults["as_is"]}))

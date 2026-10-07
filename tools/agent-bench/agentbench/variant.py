"""The variants: how the clients are set up before a batch. One module per variant in tools/agent-bench/variants/
(the file name is the variant's name). Each module has:

    DESCRIPTION: str                      one line: what the variant changes
    def apply(home: str) -> list[str]:    change the harness HOME's configs or plugin files; return what changed

apply() runs after each config refresh (./setup writes the configs again for every model), so a variant starts
from the configs as CARL's setup wrote them. "baseline" changes nothing.
"""
from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass
from typing import Callable, List

from . import BENCH_DIR

VARIANTS_DIR = os.path.join(BENCH_DIR, "variants")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    apply: Callable[[str], List[str]]


def available(folder: str = VARIANTS_DIR) -> List[str]:
    return sorted(f[:-3] for f in os.listdir(folder) if f.endswith(".py") and _NAME.match(f[:-3]))


def load(name: str, folder: str = VARIANTS_DIR) -> Variant:
    if not _NAME.match(name):
        raise ValueError(f"bad variant name {name!r}")
    path = os.path.join(folder, name + ".py")
    if not os.path.isfile(path):
        raise ValueError(f"no variant {name!r} (in {folder}: {', '.join(available(folder)) or 'none'})")
    spec = importlib.util.spec_from_file_location(f"agentbench_variant_{name}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "apply", None)
    desc = getattr(mod, "DESCRIPTION", "")
    if not callable(fn) or not isinstance(desc, str):
        raise ValueError(f"{path}: a variant needs DESCRIPTION (str) and apply(home) -> list of changed files")

    def apply(home: str) -> List[str]:
        changed = fn(home)
        if not isinstance(changed, list):
            raise ValueError(f"variant {name}: apply() must return a list")
        return [str(c) for c in changed]
    return Variant(name, desc, apply)

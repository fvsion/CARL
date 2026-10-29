"""Paths and module loading shared by the tests in tests/scripts/."""
from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.join(REPO, "tools")
HOST = os.path.join(REPO, "host")
CLIENT = os.path.join(REPO, "client")

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


def load_script(path: str, name: str) -> ModuleType:
    """Import a script whose file name is not a module name (e.g. gguf-chat-template.py)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod             # dataclasses look their module up while it loads
    spec.loader.exec_module(mod)
    return mod

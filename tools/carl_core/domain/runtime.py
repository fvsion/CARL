"""What this Mac's clients need to know about the running server (Phase 23.5): ~/.config/carl/server-PORT.json.

llama.cpp's /props does not say how a model's cache was set up, and carl-cache needs it: with the window cache
(a sliding-window model without --swa-full) a saved prompt or session cannot be reused, so restoring and saving
them only costs time and disk. The launcher writes this file at each start (serve-llama.sh; router mode: from its
presets); carl-cache reads it (client/shared/carl-cache.js cacheMode()), the dashboard serves it to other computers.

    {"version": 1, "port": 9275, "mode": "single", "written": "2026-10-10T14:00:00",
     "models": {"gemma-4-e4b": {"swa": "window", "kv": "q4_0", "ctx": 98304, "slots": 2}}}

swa: "full" (sliding-window layers at full length: saved states restore), "window" (the window cache: they do not),
"none" (no sliding-window layers). Pure except write() and read().
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Mapping, Optional

VERSION = 1
SWA = ("full", "window", "none")
# CARL's server port when none is set (PORT, --port): 9275, a port no common software uses (user, 2026-10-10: "8080 is
# too common ... a 9000 level port that nothing else generally uses"; not 9931, llama.cpp's own coming default, so a
# plain llama-server does not take CARL's port). host/common.sh CARL_DEFAULT_PORT and client/install.sh say the same.
DEFAULT_PORT = 9275


def path(conf_dir: str, port: int) -> str:
    return os.path.join(conf_dir, f"server-{int(port)}.json")


def model_entry(swa: str, kv: str = "", ctx: int = 0, slots: int = 0) -> Dict[str, Any]:
    """One model's cache set-up; swa "-" (the launcher's word for a model with no sliding window) is "none"."""
    swa = "none" if swa in ("-", "") else swa
    if swa not in SWA:
        raise ValueError(f"swa takes {', '.join(SWA)}, not {swa!r}")
    out: Dict[str, Any] = {"swa": swa}
    if kv:
        out["kv"] = kv
    if ctx:
        out["ctx"] = int(ctx)
    if slots:
        out["slots"] = int(slots)
    return out


def document(port: int, mode: str, models: Mapping[str, Mapping[str, Any]], now: Optional[float] = None) -> Dict[str, Any]:
    if mode not in ("single", "router"):
        raise ValueError(f"mode takes single or router, not {mode!r}")
    return {"version": VERSION, "port": int(port), "mode": mode,
            "written": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "models": {k: dict(v) for k, v in models.items()}}


def write(conf_dir: str, port: int, mode: str, models: Mapping[str, Mapping[str, Any]]) -> str:
    """Write the file (owner only, replaced whole); its path."""
    p = path(conf_dir, port)
    os.makedirs(conf_dir, exist_ok=True)
    tmp = f"{p}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(document(port, mode, models), f, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


def set_api_port(conf_dir: str, port: int, api_port: int) -> bool:
    """The dashboard API's real port into the server's file (the dashboard, once its API runs); False when there is no
    file for that port."""
    doc = read(conf_dir, port)
    if not doc:
        return False
    doc["api_port"] = int(api_port)
    p = path(conf_dir, port)
    tmp = f"{p}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return True


def carl_id(conf_dir: str) -> str:
    """This CARL's ID (config dir/carl-id, made at the first use): the dashboard API's /carl/ping answers it without a
    key, and a client sends its key only to an API that answers the ID it knows (Phase 23.5 item 7). Not a secret."""
    p = os.path.join(conf_dir, "carl-id")
    try:
        with open(p, encoding="utf-8") as f:
            v = f.read().strip()
        if len(v) == 32 and all(c in "0123456789abcdef" for c in v):
            return v
    except OSError:
        pass
    v = os.urandom(16).hex()
    try:
        os.makedirs(conf_dir, exist_ok=True)
        tmp = f"{p}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(v + "\n")
        os.replace(tmp, p)
    except OSError:
        pass
    return v


def read(conf_dir: str, port: int) -> Dict[str, Any]:
    """The file's contents; {} when there is none or it cannot be read."""
    try:
        with open(path(conf_dir, port), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}

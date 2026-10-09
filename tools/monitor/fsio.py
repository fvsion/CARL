"""Small file reads and writes: the API key, client config templates, job logs, settings."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List, Optional, Tuple


def read_key(path: str) -> str:
    """The API key in path, "" if there is none."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def read_text(path: str, errors: str = "strict") -> Optional[str]:
    """The text in path, None if it can't be read (errors: how to decode bad bytes)."""
    try:
        with open(path, errors=errors) as f:
            return f.read()
    except OSError:
        return None


def read_lines(path: str) -> List[str]:
    """The lines of a job's output file, [] if it does not exist yet."""
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def file_size(path: str) -> int:
    """Bytes in path, 0 if it does not exist (yet)."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def write_private(path: str, text: str) -> None:
    """Replace path with text, readable by this user only (settings, state): a temporary
    file of its own (mkstemp: 0600, a unique name, so two threads never share it), then
    renamed over path so a reader never sees half of it."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# The state file client/configure.py keeps next to each client's config, and its name
# before the rename in 1.2.0 (read until the next client/install.sh moves it).
STATE_FILES = ("carl.json", "llm-deploy.json")


def client_state(folder: str) -> object:
    """The parsed state file in a client's config folder (STATE_FILES, the first that
    exists), None when there is none or it can't be read."""
    for name in STATE_FILES:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    state: object = json.load(f)
                return state
            except (OSError, ValueError):
                return None
    return None


def coder_model_here(base: str, home: str) -> str:
    """The coder's model of OpenCode and Pi on THIS Mac (Phase 23.4.5; the Clients list): "main" (the main session's
    model, on this server) or the external PROVIDER/MODEL that the setup wrote ("coder_model" in the state file of the
    first client set up for base); "" when none is set up for base."""
    for folder in (".config/opencode", ".pi/agent"):
        st = client_state(os.path.join(home, folder))
        if isinstance(st, dict) and st.get("base_url") == f"{base}/v1":
            m = st.get("coder_model")
            return m if isinstance(m, str) and m and len(m) <= 200 and m.isprintable() and " " not in m else "main"
    return ""


CLIENT_CONFIGS = {"OpenCode": (".config/opencode", "opencode.json"), "Pi": (".pi/agent", "models.json")}


def _window(entry: object, client: str) -> int:
    """A model entry's context window (OpenCode limit.context, Pi contextWindow; 0 if unknown)."""
    if not isinstance(entry, dict):
        return 0
    v = (entry.get("limit") or {}).get("context") if client == "OpenCode" else entry.get("contextWindow")
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def listed_models(home: str, client: str, provider: str) -> Optional[Dict[str, int]]:
    """The model ids a client's config lists under our provider, with each one's context window
    (0 if unknown); None when the config can't be read."""
    folder, name = CLIENT_CONFIGS[client]
    try:
        with open(os.path.join(home, folder, name)) as f:
            cfg = json.load(f)
        ms = cfg["provider" if client == "OpenCode" else "providers"][provider]["models"]
        if isinstance(ms, dict):
            return {str(k): _window(v, client) for k, v in ms.items()}
        return {str(m["id"]): _window(m, client) for m in ms if isinstance(m, dict) and "id" in m}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def installed_here(base: str, home: str) -> List[Tuple[str, str]]:
    """(client, provider id) pairs that install.sh on THIS Mac already pointed at base."""
    out = []
    for client, folder in (("OpenCode", ".config/opencode"), ("Pi", ".pi/agent")):
        st = client_state(os.path.join(home, folder))
        if isinstance(st, dict) and st.get("base_url") == f"{base}/v1":
            prov = st.get("providers")
            out.append((client, str(prov.get("llamacpp", "llamacpp") if isinstance(prov, dict) else "llamacpp")))
    return out

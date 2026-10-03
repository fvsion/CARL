"""Small file reads and writes: the API key, client config templates, job logs, settings."""
from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple


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
    """Replace path with text, readable by this user only (settings): a temporary
    file created 0600, then renamed over path so a reader never sees half of it."""
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def installed_here(base: str, home: str) -> List[Tuple[str, str]]:
    """(client, provider id) pairs that install.sh on THIS Mac already pointed at base."""
    out = []
    for client, rel in (("OpenCode", ".config/opencode/llm-deploy.json"), ("Pi", ".pi/agent/llm-deploy.json")):
        try:
            with open(os.path.join(home, rel)) as f:
                st = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and st.get("base_url") == f"{base}/v1":
            prov = st.get("providers")
            out.append((client, str(prov.get("llamacpp", "llamacpp") if isinstance(prov, dict) else "llamacpp")))
    return out

"""The names of OpenCode's and Pi's sessions on this Mac (Phase 23.5 item 4): a saved session's file is named by the
client's session ID (it survives a rename), and the listings show the session's current name next to it.

- OpenCode keeps its sessions in ~/.local/share/opencode/opencode.db (table session: id, title), read here read-only.
- Pi keeps each session in ~/.pi/agent/sessions/--<folder>--/<time>_<id>.jsonl: its name is the last
  {"type": "session_info", "name": ...} line (/name), else Pi shows the first message, cut.

Never part of a key; any failure (no client, a locked or newer database, an unreadable file) gives no name.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
from typing import Dict, Iterable

FIRST_CHARS = 40        # Pi's first message as the name of a session without one, cut here


def opencode_titles(home: str, ids: Iterable[str]) -> Dict[str, str]:
    want = sorted({i for i in ids if i.startswith("ses_")})
    db = os.path.join(home, ".local", "share", "opencode", "opencode.db")
    if not want or not os.path.isfile(db):
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = con.execute(f"SELECT id, title FROM session WHERE id IN ({','.join('?' * len(want))})", want).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {str(i): str(t) for i, t in rows if t}


def _first_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def pi_name(path: str) -> str:
    """A Pi session file's name: its last /name, else its first message (one line, cut)."""
    name, first = "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"session_info"' not in line and (first or '"user"' not in line):
                    continue
                try:
                    doc = json.loads(line)
                except ValueError:
                    continue
                if doc.get("type") == "session_info" and isinstance(doc.get("name"), str):
                    name = doc["name"]
                elif not first and doc.get("type") == "message" and (doc.get("message") or {}).get("role") == "user":
                    first = " ".join(_first_text((doc.get("message") or {}).get("content")).split())
    except OSError:
        return ""
    if name:
        return name
    return first if len(first) <= FIRST_CHARS else first[:FIRST_CHARS - 1].rstrip() + "…"


def pi_names(home: str, ids: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    base = os.path.join(home, ".pi", "agent", "sessions")
    for i in {i for i in ids if i and not i.startswith("ses_") and "/" not in i}:
        files = sorted(glob.glob(os.path.join(glob.escape(base), "*", f"*_{glob.escape(i)}.jsonl")))
        if files:
            n = pi_name(files[-1])
            if n:
                out[i] = n
    return out


def names(home: str, ids: Iterable[str]) -> Dict[str, str]:
    """Session ID -> its name, for the IDs found in either client's store on this Mac."""
    ids = list(ids)
    return {**pi_names(home, ids), **opencode_titles(home, ids)}


def session_id(file_name: str) -> str:
    """The session ID in a saved session's file name (carl-session+MODEL+KEY+SESSION.bin); "" for another file."""
    parts = file_name[:-len(".bin")].split("+") if file_name.endswith(".bin") else []
    return parts[3] if len(parts) == 4 and parts[0] == "carl-session" else ""

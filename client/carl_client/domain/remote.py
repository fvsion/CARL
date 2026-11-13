"""The server as a client folder knows it: remote.json (the server writes it at every start, next to the client's
files), the dashboard API's place and CARL's ID, where a moved API is looked for, and the texts a sync shows when
the folder cannot reach the server. Pure: the server's default port comes in (data/server.json).

Before the key goes out, the dashboard API must answer /carl/ping (no key) with the ID in remote.json; when it moved
(its port was taken, or the server's port changed), the next ports are asked the same way.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

from .words import COMMAND, SERVER

CARL_ID_RE = re.compile(r"[0-9a-f]{32}")
VERSION_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")        # the config's version: written to the state file and the log
VERSION_TEXT_RE = re.compile(r"[0-9A-Za-z.+-]{1,40}")    # a CARL version (1.7.0), as /carl shows it
MAX_CONFIG = 1 << 20         # the published config (the installed models) is a few KB
REMOTE_FILE = "remote.json"
KEY_FILE = "api-key"


def remake(server: str = SERVER) -> str:
    """What to do when the folder lacks the server's address or key, or the key is refused (server: the server's
    computer in a sentence: data/words.json)."""
    return f"Make a new client package on {server} ({COMMAND} package), unzip it over this folder and run ./setup."


@dataclass(frozen=True)
class Server:
    """CARL's server as a client knows it before remote.json says more (data/server.json)."""
    default_port: int
    find_span: int           # a moved API is looked for on this many ports after the server's (and the default's)


def parse_server(doc: Any) -> Server:
    """data/server.json, checked; ValueError naming the problem."""
    port, span = (doc.get("default_port"), doc.get("find_span")) if isinstance(doc, dict) else (None, None)
    if not (isinstance(port, int) and 0 < port < 65536 and isinstance(span, int) and 0 <= span < 1000):
        raise ValueError("server.json: default_port must be a TCP port and find_span a count")
    return Server(port, span)


def api_of(remote: Mapping[str, Any], server: str = SERVER) -> str:
    """The dashboard API remote.json names (no trailing /); ValueError (the text for the user) when there is none."""
    api = remote.get("cache_api")
    url = urllib.parse.urlsplit(api) if isinstance(api, str) else None
    if url is None or url.scheme not in ("http", "https") or not url.netloc:
        raise ValueError(f"There is no {REMOTE_FILE} (the server's address) in the client folder. {remake(server)}")
    return str(api).rstrip("/")


def no_key_text(server: str = SERVER) -> str:
    return f"There is no API key next to carl-sync.py or in ~/.config/carl/{KEY_FILE}. {remake(server)}"


def refused_text(server: str = SERVER) -> str:
    return f"The server refused the API key. {remake(server)} The package has the key."


def unreachable_text(reason: object) -> str:
    return f"The dashboard does not answer: {reason}. Make sure that the dashboard runs on the server."[:200]


def no_api_text(api: str) -> str:
    return f"no CARL dashboard API answers at {api}"


def carl_id(remote: Mapping[str, Any]) -> str:
    """The ID this folder knows (remote.json "id"), or "" (a package from before Phase 23.5)."""
    cid = remote.get("id")
    return cid if isinstance(cid, str) and CARL_ID_RE.fullmatch(cid) else ""


def is_ours(answer: Optional[str], want: str) -> bool:
    """The key goes there: the ping answered the ID this folder knows; a folder that knows none takes any CARL
    dashboard (answer "": an older one that has no ping but says it is CARL)."""
    return answer is not None and (answer == want if want else True)


def candidates(api: str, remote: Mapping[str, Any], server: Server) -> List[str]:
    """Where a moved dashboard API is looked for: the next ports after the server's (remote.json "port", else the
    default) and after CARL's default, on the host remote.json names; none without a known ID."""
    url = urllib.parse.urlsplit(api)
    if not carl_id(remote) or not url.hostname:
        return []
    port = remote.get("port") if isinstance(remote.get("port"), int) else server.default_port
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    tried = {url.port}
    out = []
    for p in [*range(port + 1, port + 1 + server.find_span),
              *range(server.default_port + 1, server.default_port + 1 + server.find_span)]:
        if p in tried or not 0 < p < 65536:
            continue
        tried.add(p)
        out.append(f"{url.scheme}://{host}:{p}")
    return out


def moved_port(doc: Any, remote: Mapping[str, Any]) -> Optional[int]:
    """The server's port the dashboard gave (/carl/client/server) when it is not remote.json's; else None."""
    port = doc.get("port") if isinstance(doc, dict) else None
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536 or port == remote.get("port"):
        return None
    return port


def check_config(doc: Any) -> Mapping[str, Any]:
    """The published config (/carl/client/config), checked; ValueError (the text for the user) for anything else."""
    if not (isinstance(doc, dict) and isinstance(doc.get("models"), dict)
            and isinstance(doc.get("version"), str) and VERSION_RE.fullmatch(doc["version"])):
        raise ValueError("The server sent data that is not a client config.")
    return doc

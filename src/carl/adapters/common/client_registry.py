"""The computers that sync with this dashboard (client/carl-sync.py), as the dashboard API sees them: each one's
X-Carl-Client header, checked, and the list kept in ~/.config/carl/clients.json across dashboard restarts (written
0600 through fsio.write_private)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ...domain.server.snapshot import jdict
from . import installed_clients as fsio

CLIENT_ID = re.compile(r"[0-9a-f]{6,32}")
CLIENT_KEYS = ("host", "user", "os", "applied", "mode")
# the coder's model a client reports (client/carl-sync.py who(), Phase 23.4.5): "main" or the client's PROVIDER/MODEL
CODER_MODEL = re.compile(r"main|[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,150}")


@dataclass
class Client:
    """A client that syncs (client/carl-sync.py), as it last said it is."""
    id: str
    host: str = ""
    user: str = ""
    os: str = ""
    applied: str = ""                    # the client config version it has
    auto_apply: bool = True
    mode: str = ""                       # "service" (it listens) or "check" (OpenCode / Pi check at start)
    address: str = ""
    last_seen: float = 0.0
    connected: int = 0                   # its open event streams (the service: 1)
    coder_model: str = ""                # the coder's model there (Phase 23.4.5): "main" or PROVIDER/MODEL; "" not said


def parse_client(header: str) -> Optional[Client]:
    """A client from its X-Carl-Client header (JSON, checked: the id, short printable fields); None if bad."""
    if not header or len(header) > 1024:
        return None
    try:
        doc = jdict(json.loads(header))
    except ValueError:
        return None
    cid = doc.get("id")
    if not isinstance(cid, str) or not CLIENT_ID.fullmatch(cid):
        return None
    f = {k: "".join(c for c in str(doc.get(k) or "") if c.isprintable())[:60] for k in CLIENT_KEYS}
    coder = doc.get("coder_model")
    return Client(cid, host=f["host"], user=f["user"], os=f["os"], applied=f["applied"], mode=f["mode"],
                  auto_apply=doc.get("auto_apply") is not False,
                  coder_model=coder if isinstance(coder, str) and CODER_MODEL.fullmatch(coder) else "")


class Registry:
    """The clients seen (~/.config/carl/clients.json: kept across dashboard restarts)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.clients: Dict[str, Client] = {}
        self.saved_at = 0.0
        try:
            with open(path, encoding="utf-8") as f:
                for cid, v in jdict(json.load(f)).items():
                    c = parse_client(json.dumps({"id": cid, **jdict(v)}))
                    if c:
                        c.address, c.last_seen = str(jdict(v).get("address", ""))[:60], float(jdict(v).get("last_seen") or 0)
                        self.clients[cid] = c
        except (OSError, ValueError, TypeError):
            pass

    def seen(self, c: Client, address: str, connected: int = 0) -> None:
        with self.lock:
            old = self.clients.get(c.id)
            c.address, c.last_seen = address, time.time()
            c.connected = (old.connected if old else 0) + connected
            changed = old is None or (old.applied, old.mode, old.auto_apply, old.coder_model) != \
                (c.applied, c.mode, c.auto_apply, c.coder_model)
            self.clients[c.id] = c
            if changed or time.time() - self.saved_at > 60:
                self._save()

    def gone(self, cid: str) -> None:
        """A client's event stream ended: one connection less, seen now; what it reported since (its
        applied config) stays, not the values from when the stream started."""
        with self.lock:
            c = self.clients.get(cid)
            if c:
                c.connected, c.last_seen = max(c.connected - 1, 0), time.time()
                self._save()

    def _save(self) -> None:
        doc = {cid: {k: getattr(c, k) for k in (*CLIENT_KEYS, "auto_apply", "coder_model", "address", "last_seen")}
               for cid, c in self.clients.items()}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fsio.write_private(self.path, json.dumps(doc, indent=1))
            self.saved_at = time.time()
        except OSError:
            pass

    def forget(self, cid: str) -> None:
        with self.lock:
            self.clients.pop(cid, None)
            self._save()

    def list(self) -> List[Client]:
        with self.lock:
            return sorted(self.clients.values(), key=lambda c: -c.last_seen)

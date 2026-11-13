"""The sync's files (the SyncStore port): its state ~/.config/carl/client-sync.json, the setup's switches
~/.config/carl/client-install.env, and next to the client's files remote.json, api-key and installed-models.json.
Each file is written whole (a temp file, then a rename), the private ones with mode 0600."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..domain.models import LIST_FILE
from ..domain.remote import KEY_FILE, REMOTE_FILE


def load(path: str) -> Dict[str, Any]:
    """A JSON object from a file; {} when it is missing, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


class SyncFiles:
    """conf: the settings folder (~/.config/carl); here: the client's folder (carl-sync.py's); lock: the Lock port."""

    def __init__(self, conf: str, here: str, lock: Any) -> None:
        self.conf = conf
        self.here = here
        self.lock = lock
        self.state_file = os.path.join(conf, "client-sync.json")
        self.log = os.path.join(conf, "client-sync.log")
        self.sync_lock = os.path.join(conf, "client-sync.lock")
        self.state_lock = os.path.join(conf, "client-sync.state.lock")
        self.install_env = os.path.join(conf, "client-install.env")

    def state(self) -> Dict[str, Any]:
        return {"auto_apply": True, **load(self.state_file)}

    def save_state(self, doc: Dict[str, Any]) -> None:
        os.makedirs(self.conf, exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_file)

    def update(self, **kv: Any) -> Dict[str, Any]:
        os.makedirs(self.conf, exist_ok=True)
        with self.lock.hold(self.state_lock):    # the service, the setup and the plugins all write it
            doc = {**self.state(), **kv}
            self.save_state(doc)
        return doc

    def install_lines(self) -> List[str]:
        try:
            with open(self.install_env, encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def write_install(self, text: str) -> None:
        os.makedirs(self.conf, exist_ok=True)
        tmp = self.install_env + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.install_env)

    def remote(self) -> Dict[str, Any]:
        return load(os.path.join(self.here, REMOTE_FILE))

    def write_remote(self, **kv: Any) -> None:
        """Change remote.json next to the client's files (the dashboard API or the server's port moved)."""
        path = os.path.join(self.here, REMOTE_FILE)
        doc = {**load(path), **kv}
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2)
        os.chmod(path + ".tmp", 0o600)
        os.replace(path + ".tmp", path)

    def key(self) -> str:
        for f in (os.path.join(self.here, KEY_FILE), os.path.join(self.conf, KEY_FILE)):
            try:
                with open(f, encoding="utf-8") as fh:
                    key = fh.read().strip()
            except OSError:
                continue
            if key:
                return key
        return ""

    def slots_file(self) -> str:
        """Where the installer writes the slot count it read, for this process (CARL_SLOTS_OUT)."""
        return os.path.join(self.conf, f".set-slots-{os.getpid()}")

    def read_slots(self, path: str) -> Optional[int]:
        """The slot count the installer wrote to `path` (the file goes); None when it wrote none."""
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read(32).strip()
            os.remove(path)
        except OSError:
            return None
        return int(text) if text.isdigit() and 0 < int(text) < 1000 else None

    def write_models(self, doc: Any) -> None:
        path = os.path.join(self.here, LIST_FILE)
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=1)
        os.replace(path + ".tmp", path)

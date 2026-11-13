"""The running server's files in the settings folder: server-PORT.json and carl-id (their layout:
domain/server/runtime_doc.py). Written with the one atomic writer (json_files.write_private): mode 0600, indent 2
and a trailing newline. The settings folder, when it is made here, gets the default mode, as the launcher's own.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Mapping, Optional

from ...domain.server import runtime_doc as rd
from ...domain.shared.contracts import ensure, require
from .json_files import write_private
from .private_files import Note, Private, create_private

DIR_MODE = 0o777                    # os.makedirs' default: the umask decides (0755), as before the one writer
LAYOUT_KEYS = ("version", "port", "mode", "written", "models")    # server-PORT.json's keys (C-205)


def path(conf_dir: str, port: int) -> str:
    return os.path.join(conf_dir, rd.file_name(port))


def now_text() -> str:
    """The local time for "written"."""
    return time.strftime(rd.TIME_FORMAT, time.localtime())


def read(conf_dir: str, port: int) -> Dict[str, Any]:
    """The file's contents; {} when there is none or it cannot be read."""
    try:
        with open(path(conf_dir, port), encoding="utf-8") as f:
            return rd.loads(f.read())
    except (OSError, ValueError):
        return {}


def write_doc(conf_dir: str, port: int, doc: Mapping[str, Any]) -> str:
    """Write a document as the file (owner only, replaced whole); its path."""
    p = path(conf_dir, port)
    write_private(p, rd.dumps(doc), dir_mode=DIR_MODE)
    return p


def write(conf_dir: str, port: int, mode: str, models: Mapping[str, Mapping[str, Any]]) -> str:
    """Write the file for a start (written: now); its path."""
    return write_doc(conf_dir, port, rd.document(port, mode, models, now_text()))


def set_api_port(conf_dir: str, port: int, api_port: int) -> bool:
    """The dashboard API's real port into the server's file (the dashboard, once its API runs); False when there is no
    file for that port."""
    doc = read(conf_dir, port)
    if not doc:
        return False
    write_doc(conf_dir, port, rd.with_api_port(doc, api_port))
    return True


def carl_id(conf_dir: str) -> str:
    """This CARL's ID (config dir/carl-id, made at the first use): the dashboard API's /carl/ping answers it without a
    key, and a client sends its key only to an API that answers the ID it knows (Phase 23.5 item 7). Not a secret."""
    p = os.path.join(conf_dir, rd.CARL_ID_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            v = f.read().strip()
        if rd.is_carl_id(v):
            return v
    except OSError:
        pass
    v = os.urandom(16).hex()
    try:
        write_private(p, v + "\n", dir_mode=DIR_MODE)
    except OSError:
        pass
    return v


class RuntimeFiles:
    """The server files of one settings folder (implements RuntimeStore, contract C-025; the layout: C-205)."""

    def __init__(self, conf_dir: str) -> None:
        self.conf_dir = conf_dir

    def read(self, port: int) -> Dict[str, Any]:
        return read(self.conf_dir, port)

    def write(self, port: int, doc: Mapping[str, Any]) -> str:
        require(doc.get("port") == port, "C-025", "the document is the file's own port's")
        require(all(k in doc for k in LAYOUT_KEYS), "C-205", "the document has every key of the layout")
        p = write_doc(self.conf_dir, port, doc)
        ensure(os.path.basename(p) == rd.file_name(port), "C-025", "the file is server-PORT.json of that port")
        return p

    def remove(self, port: int) -> None:
        try:
            os.remove(path(self.conf_dir, port))
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- the client folder's connection files (the outbox)
REMOTE = "remote.json"
CLIENT_KEY = "api-key"


def remote_text(host: str, port: int, carl_id_value: str, version: str, written: str) -> str:
    """client/remote.json's text (C-219): the server's address and port, the dashboard API at port + 1 (the dashboard
    writes its real port when it takes another), CARL's ID, the version and the time, one key a line."""
    return (f'{{\n  "host": "{host}",\n  "port": {port},\n  "cache_api": "http://{host}:{port + 1}",\n'
            f'  "id": "{carl_id_value}",\n  "version": "{version}",\n  "written": "{written}"\n}}\n')


def write_client_package(client_dir: str, host: str, port: int, key_file: str, carl_id_value: str, version: str,
                         written: str, private: Optional[Private] = None, note: Optional[Note] = None) -> None:
    """The client folder's connection files, so a copy of client/ (carl package zips it) needs nothing else:
    remote.json and api-key (a copy of the server's key, C-220), both readable by the owner only. Written at every
    start (the address can change between --local and --vm); nothing when the folder or the key is missing, and a
    write that fails is no error (host/common.sh write_client_package). private: on Windows, where a mode is nothing,
    the function that leaves a file to its owner (the key's own, adapters/windows/secrets.py): each file is made
    private before its content is written, and a file that cannot be is kept with one warning through note."""
    if not os.path.isdir(client_dir) or not _has_text(key_file):
        return
    old = os.umask(0o077)
    try:
        remote, tmp = os.path.join(client_dir, REMOTE), os.path.join(client_dir, REMOTE + ".tmp")
        create_private(tmp, remote, private, note)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(remote_text(host, port, carl_id_value if rd.is_carl_id(carl_id_value) else "", version, written))
        os.replace(tmp, remote)
        key, tmp = os.path.join(client_dir, CLIENT_KEY), os.path.join(client_dir, CLIENT_KEY + ".tmp")
        create_private(tmp, key, private, note)
        with open(key_file, "rb") as src, open(tmp, "wb") as f:
            f.write(src.read())
        os.replace(tmp, key)
    except OSError:
        return
    finally:
        os.umask(old)
    for name in (REMOTE, CLIENT_KEY):
        try:
            os.chmod(os.path.join(client_dir, name), 0o600)
        except OSError:
            pass


def _has_text(path: str) -> bool:
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False

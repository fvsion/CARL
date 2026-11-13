"""The client package's use case (the rules: domain/clients/package.py): the zip of client/ with the server's address and
key. `carl package` and the dashboard's Connect > Setup both call PackageService.make(), so they make the same zip.
The files, the version and the zip come through the ports (ClientFiles, VersionSource, PackageWriter); the composition
builds the service (composition/package.py).
"""
from __future__ import annotations

import json
import os
from typing import Callable, List, Mapping, Optional, Sequence

from ..domain.clients import package as pk
from ..domain.shared.contracts import ensure
from ..domain.shared.errors import ConfigError
from ..domain.shared.platform_words import MACOS, PlatformWords
from ..ports import ClientFiles, JsonDocument, PackageEntry, PackageWriter, VersionSource
from ..ports.plugin import ClientFile


def _load_json(raw: Optional[bytes]) -> object:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class PackageService:
    """Makes the client package. now: the time of the files the package makes itself; host_name: this computer's
    name for the file name of the zip ("" when unknown). Contract C-113."""

    def __init__(self, files: ClientFiles, writer: PackageWriter, version: VersionSource, now: Callable[[], float],
                 host_name: Callable[[], str]) -> None:
        self.files = files
        self.writer = writer
        self.version = version
        self.now = now
        self.host_name = host_name

    def make(self, repo: str, client_dir: str, out_dir: str, models: object, cfg: Mapping[str, object],
             words: PlatformWords = MACOS, anyway: bool = False, plugin_files: Sequence[ClientFile] = ()) -> pk.Outcome:
        """Write dist/carl-client-VERSION-HOST.zip: the files of client/ that the repository lists, the entry
        points, the files the plugins that are on add (plugin_files: PluginHost.client_files; a target that client/
        has already, or one that is not there, is left out), and from the server remote.json, api-key, the installed
        models (models: what the client-models command gives), VERSION and the platform words (words: the server's
        own). client_dir holds the server's remote.json and api-key (normally repo/client). Nothing is written when
        the server serves only this computer (unless anyway) or when the server never started."""
        remote_path, key_path = os.path.join(client_dir, "remote.json"), os.path.join(client_dir, "api-key")
        remote_raw, key_raw = self.files.server_file(remote_path), self.files.server_file(key_path)
        missing = [n for n, raw in (("remote.json", remote_raw), ("api-key", key_raw)) if not (raw or b"").strip()]
        if missing:
            return pk.missing_refusal(missing, client_dir, words)
        remote = pk.parse_remote(_load_json(remote_raw))
        cmd = words.command
        if remote is None:
            return pk.Outcome("", f"error: {remote_path} is not a valid connection file.",
                              (f"Start the server again ({cmd}): it writes the file again. Then run {cmd} package "
                               f"again.",))
        if pk.is_local(remote.host) and not anyway:
            return pk.local_refusal(remote, cfg, words)
        src = os.path.join(repo, "client")
        entries: List[PackageEntry] = []
        for rel in pk.select(self.files.client_files(repo)):
            path = os.path.join(src, rel)
            got = self.files.client_file(path)
            if got is None:
                if rel in pk.ENTRY_POINTS:
                    return pk.Outcome("", f"error: {path} is not there. The client folder is not complete.",
                                      ("Get the CARL folder again (git pull, or a new copy).",))
                continue                                # in the list, but deleted here: not in the package
            data, executable, mtime = got
            entries.append((rel, data, pk.mode_of(rel, executable), mtime))
        have = {e[0] for e in entries} | set(pk.GENERATED)
        for cf in plugin_files:
            got = None if cf.target in have else self.files.client_file(cf.source)
            if got is not None:
                data, executable, mtime = got
                entries.append((cf.target, data, pk.mode_of(cf.target, executable), mtime))
                have.add(cf.target)
        version, now = self.version.version(), self.now()
        entries += [("remote.json", remote_raw or b"", 0o600, now),
                    ("api-key", key_raw or b"", 0o600, now),
                    ("installed-models.json", (json.dumps(models, indent=2) + "\n").encode(), 0o644, now),
                    ("VERSION", f"{version}\n".encode(), 0o644, now),
                    (pk.WORDS_FILE, pk.client_words_text(words).encode(), 0o644, now)]
        ensure({e[0] for e in entries} >= set(pk.ENTRY_POINTS) | set(pk.GENERATED), "C-113",
               "the entry points and the generated files are in the zip")
        out = os.path.join(os.path.abspath(out_dir), pk.zip_name(version, self.host_name() or remote.host))
        size = self.writer.write_zip(out, entries)
        return pk.Outcome(out, "", pk.done_notes(out, remote, cfg, words), len(entries), size)


def publish_api_port(remote: JsonDocument, port: int, cache_api: str) -> bool:
    """The dashboard API's real address into client/remote.json (remote: that file; Phase 23.5 item 7): the
    launcher writes the server's port + 1 at each start; when the API took another port, the file (and the next
    package) says so. Only for the file of this server (its "port"); False when nothing was changed."""
    try:
        doc = remote.load()
    except ConfigError:
        return False
    if not isinstance(doc, dict) or doc.get("port") != port or doc.get("cache_api") == cache_api:
        return False
    try:
        remote.save({**doc, "cache_api": cache_api})
    except OSError:
        return False
    return True

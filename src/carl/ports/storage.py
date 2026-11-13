"""Ports of CARL's own files: the settings, the runtime files, the server's key, the chat templates, the
version and the client package."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from ..domain.shared.types import JsonValue

# One file of the client package: (path in client/, content, mode, mtime).
PackageEntry = Tuple[str, bytes, int, float]
# One file of the client folder as the package reads it: (content, executable, mtime).
PackageFile = Tuple[bytes, bool, float]


class JsonDocument(Protocol):
    """One JSON file (config.json, models.json, the catalogue). Contract C-005."""

    def load(self) -> Optional[JsonValue]:
        """The parsed document, None when the file doesn't exist. Raises ConfigError when
        it can't be read or parsed."""
        ...

    def save(self, doc: Mapping[str, object]) -> None:
        """Atomic write, readable by the user only."""
        ...


class LegacyEnv(Protocol):
    """The KEY=value file earlier versions wrote (llama.env). Contract C-006."""

    def read(self) -> Dict[str, str]: ...


class RuntimeStore(Protocol):
    """The running server's file, server-PORT.json in the settings folder (its layout: domain/server/runtime_doc).
    Contract C-025."""

    def read(self, port: int) -> Dict[str, Any]:
        """The document ({} when there is none or it can't be read)."""
        ...

    def write(self, port: int, doc: Mapping[str, Any]) -> str:
        """Atomic, readable by the user only, indent 2 and a trailing newline; the file's path."""
        ...

    def remove(self, port: int) -> None: ...


class KeyStore(Protocol):
    """The server's API key file. Contract C-026."""

    def path(self) -> str:
        """The key file the server uses (API_KEY_FILE, CARL's path, or an earlier one that has a key)."""
        ...

    def read(self) -> str:
        """The key, "" when there is none."""
        ...

    def ensure(self) -> str:
        """Make a key when there is none (readable by the user only); the key file's path."""
        ...


class ConfDirMigration(Protocol):
    """Settings that earlier versions kept elsewhere, moved into the settings folder once. Contract C-027."""

    def pending(self) -> bool: ...

    def migrate(self) -> List[str]:
        """Move them; what moved, in words ([] when nothing did)."""
        ...


class TemplateStore(Protocol):
    """The chat templates CARL writes for llama-server. Contract C-028."""

    def ensure(self, model_path: str) -> str:
        """The template file for a model, written again when the template rules changed; "" when the model needs
        none."""
        ...


class VersionSource(Protocol):
    """CARL's version. Contract C-029."""

    def version(self) -> str:
        """CARL's version: the newest CHANGELOG entry, else git describe, else "unknown"."""
        ...


class ClientFiles(Protocol):
    """The files the client package is made of: the client folder and the server's connection files. Contract C-035."""

    def client_files(self, repo: str) -> List[str]:
        """The files of client/ in a clone, relative to it: the tracked ones; every file of the folder when the
        clone has no repository (a copy of the share zip). [] when there is no client folder. The caller leaves the
        caches out."""
        ...

    def client_file(self, path: str) -> Optional[PackageFile]:
        """A regular file of the client folder (a link is none); None when it is not there."""
        ...

    def server_file(self, path: str) -> Optional[bytes]:
        """A file the server wrote (remote.json, api-key); None when it cannot be read."""
        ...


class PackageWriter(Protocol):
    """The client package's zip. Contract C-030."""

    def write_zip(self, path: str, entries: Sequence[PackageEntry]) -> int:
        """The zip at path (its folder made), readable by its owner only, written atomically; its size."""
        ...

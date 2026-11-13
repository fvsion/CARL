"""JSON and KEY=value files on disk: config.json, models.json, the catalogue, the old *.env."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, Mapping, Optional

from ...domain.shared.contracts import require
from ...domain.shared.errors import ConfigError
from ...domain.shared.types import JsonValue
from .private_dirs import make_dirs


def read_json(path: str) -> Optional[JsonValue]:
    """The parsed file, None when it doesn't exist; ConfigError when it can't be read."""
    try:
        with open(path, encoding="utf-8") as f:
            data: JsonValue = json.load(f)
            return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: {e}") from None


def write_private(path: str, text: str, mode: int = 0o600, dir_mode: int = 0o700) -> None:
    """CARL's one atomic writer: a temp file of its own in the same folder (mkstemp: mode 0600, a unique name, so
    two writers never share it), written, flushed to disk, then renamed over path. A crash never leaves a
    half-written file, a reader never sees half of it, and other users never see it. mode: the file's mode when it
    is not the user's only (a file other programs read, set before the rename). dir_mode: the folder's mode when it
    is made (its parents get the default). The text goes as UTF-8 with "\\n" line ends on every OS."""
    folder = os.path.dirname(path) or "."
    make_dirs(folder, dir_mode)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)   # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode != 0o600:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path: str, doc: object) -> None:
    write_private(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


class JsonFile:
    """One JSON document on disk (implements the JsonDocument port, contract C-005)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> Optional[JsonValue]:
        return read_json(self.path)

    def save(self, doc: Mapping[str, object]) -> None:
        require(isinstance(doc, Mapping), "C-005", "the document is a JSON object")
        write_json(self.path, doc)


def read_env_file(path: str) -> Dict[str, str]:
    """KEY=value lines ({} when the file is missing); comments skipped, values not evaluated."""
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    out[k] = v
    except OSError:
        pass
    return out


class LegacyEnvFiles:
    """llama.env written by earlier monitors (implements LegacyEnv)."""

    def __init__(self, llama_env: str) -> None:
        self.llama_env = llama_env

    def read(self) -> Dict[str, str]:
        return read_env_file(self.llama_env)

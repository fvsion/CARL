"""JSON and KEY=value files on disk: config.json, models.json, the catalogue, the old *.env."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, Mapping, Optional

from ..domain.errors import ConfigError
from ..domain.types import JsonValue


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


def write_private(path: str, text: str) -> None:
    """Atomic write (temp file in the same folder + rename) readable by the user only, so a
    crash never leaves a half-written file and other users never see it."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)   # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path: str, doc: object) -> None:
    write_private(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


class JsonFile:
    """One JSON document on disk (implements the JsonDocument port)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> Optional[JsonValue]:
        return read_json(self.path)

    def save(self, doc: Mapping[str, object]) -> None:
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

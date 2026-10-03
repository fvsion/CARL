"""Ports: what the domain and the application services need from the outside world.

Adapters in carl_core.adapters implement them; tests use in-memory fakes.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Protocol, Tuple

from .gguf import ModelShape
from .types import HfRef, JsonObject, JsonValue


class ModelFolder(Protocol):
    """The model files on disk (the models folder and any custom paths)."""

    def exists(self, path: str) -> bool: ...

    def size(self, path: str) -> int: ...

    def gguf_names(self, directory: str) -> List[str]:
        """Names of the .gguf files in a folder ([] when it can't be read)."""
        ...

    def free_bytes(self, directory: str) -> Optional[int]:
        """Free disk space of the folder's volume (None when the folder doesn't exist)."""
        ...

    def make_dir(self, directory: str) -> None: ...

    def sha256(self, path: str) -> str: ...

    def remove(self, path: str) -> None:
        """Delete a file if it exists."""
        ...

    def rename(self, src: str, dst: str) -> None: ...


class ShapeReader(Protocol):
    """GGUF header shapes: of a local file, or of a file on Hugging Face (HTTP range read)."""

    def local(self, path: str) -> ModelShape: ...

    def remote(self, ref: HfRef) -> ModelShape: ...


class GpuLimit(Protocol):
    def limit(self) -> Tuple[int, str]:
        """(bytes the GPU may use, how it was found)."""
        ...


class JsonDocument(Protocol):
    """One JSON file (config.json, models.json, the catalogue)."""

    def load(self) -> Optional[JsonValue]:
        """The parsed document, None when the file doesn't exist. Raises ConfigError when
        it can't be read or parsed."""
        ...

    def save(self, doc: Mapping[str, object]) -> None:
        """Atomic write, readable by the user only."""
        ...


class LegacyEnv(Protocol):
    """The KEY=value files earlier versions wrote (llama.env, mtplx.env)."""

    def read(self) -> Tuple[Dict[str, str], Dict[str, str]]: ...


class HubClient(Protocol):
    """The Hugging Face API (JSON answers, not yet validated)."""

    def get(self, api_path: str) -> JsonValue: ...


class Downloader(Protocol):
    def fetch(self, url: str, directory: str, file_name: str) -> int:
        """Download (resuming a part file) into directory/file_name; the exit code."""
        ...


class Clock(Protocol):
    def today(self) -> str:
        """YYYY-MM-DD."""
        ...


class Console(Protocol):
    """Progress and results for the person at the terminal."""

    def info(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...


class TuneServer(Protocol):
    """A llama-server Auto-tune starts once per speculation mode."""

    def start(self, spec: str, n: int, ctx: int) -> float:
        """Start (stopping a previous one) and wait until it serves; seconds it took."""
        ...

    def stop(self) -> None: ...

    def timings(self, body: JsonObject, timeout: float) -> Dict[str, float]:
        """POST a chat completion; the numeric "timings" of the answer."""
        ...

    def count_tokens(self, text: str) -> Optional[int]:
        """Tokens in a text (None when the server can't tell)."""
        ...


class Progress(Protocol):
    """Auto-tune's progress lines ("STEP i/N ..." and indented notes the monitor shows)."""

    def step(self, text: str) -> None: ...

    def note(self, text: str) -> None: ...

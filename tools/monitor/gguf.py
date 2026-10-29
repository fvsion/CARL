"""A model file's GGUF memory shape and the GPU limit, from carl_core (the same code the launcher
and ./carl.sh fit use; the KV maths: carl_core.domain.gguf)."""
from __future__ import annotations

from typing import Tuple

from carl_core.adapters.gguf_reader import local_meta
from carl_core.domain.gguf import model_shape
from carl_core.wiring import GPU

from .model import Shape


def read_shape(path: str) -> Shape:
    """The memory shape of a local GGUF file (reads up to 64 MB of its header)."""
    return model_shape(local_meta(path))


def gpu_limit() -> Tuple[int, str]:
    """(bytes, how it was found) the GPU may use (may run a Swift probe, up to 2 min, once)."""
    limit, how = GPU.limit()
    return int(limit), str(how)

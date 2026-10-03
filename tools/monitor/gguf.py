"""Typed access to tools/gguf_shape.py: GGUF headers and the KV memory maths."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import gguf_shape as _gguf_shape

from .model import Shape

_g: Any = _gguf_shape          # untyped module: its results are taken as documented

GIB: int = _g.GIB
OVERHEAD: float = _g.OVERHEAD               # compute buffers, MTP draft context, allocator slack
KV_BPE: Dict[str, float] = _g.KV_BPE        # bytes per element of each KV cache type


def kv_bytes_per_token(shape: Shape, ktype: str = "q4_0", vtype: Optional[str] = None) -> float:
    """KV cache bytes per token of context."""
    return float(_g.kv_bytes_per_token(shape, ktype, vtype))


def read_shape(path: str) -> Shape:
    """The memory shape of a local GGUF file (reads up to 64 MB of its header)."""
    shape: Shape = _g.model_shape(_g.local_meta(path))
    return shape


def gpu_limit() -> Tuple[int, str]:
    """(bytes, how it was found) the GPU may use (may run a Swift probe, up to 2 min, once)."""
    limit, how = _g.gpu_limit()
    return int(limit), str(how)

"""GGUF header reading and memory maths shared by tools/llama-monitor.py and
tools/llama-fit.py. Standard library only.

model_shape() describes how much context memory a model needs:
  KV bytes/token = attention layers x KV heads x (key + value length) x bytes per element
  recurrent state = Gated-DeltaNet layers x (heads x d_k x d_v + conv state) x 4 bytes
Qwen3.8 / Qwen3.6 are hybrids: only every 4th layer (full_attention_interval)
keeps a KV cache, the rest have a fixed-size recurrent state.

A thin facade: the parsing and maths live in carl_core.domain.gguf, the file, HTTP and
Metal-limit access in carl_core.adapters.
"""
import os
import sys
from typing import Tuple

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from carl_core.adapters.gguf_reader import local_meta as local_meta  # noqa: E402  (re-exported)
from carl_core.adapters.gguf_reader import remote_meta as _remote_meta  # noqa: E402
from carl_core.domain.gguf import (FILE_TYPES as FILE_TYPES, GGUF_TYPES as GGUF_TYPES, GIB as GIB,  # noqa: E402
                                   KV_BPE as KV_BPE, OVERHEAD as OVERHEAD, Meta, ModelShape as ModelShape,
                                   kv_bytes_per_token as kv_bytes_per_token, model_shape as model_shape,
                                   parse_meta as parse_meta)
from carl_core.wiring import GPU, SHAPE_CACHE  # noqa: E402


def remote_meta(repo: str, rev: str, file: str, cache: str = SHAPE_CACHE) -> Meta:
    """Header of a not-yet-downloaded file (first 24 MB via an HTTP range), cached."""
    return _remote_meta({"repo": repo, "revision": rev, "file": file}, cache)


def gpu_limit() -> Tuple[int, str]:
    """(bytes, how) the GPU may use. An iogpu.wired_limit_mb override wins;
    otherwise Metal's recommendedMaxWorkingSetSize (via a cached Swift probe),
    else an estimate (2/3 of RAM up to 32 GB, 3/4 above)."""
    return GPU.limit()

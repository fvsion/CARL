"""GGUF header reading and memory maths for the dashboard (tools/monitor/gguf.py).
Standard library only.

model_shape() describes how much context memory a model needs:
  KV bytes/token = attention layers x KV heads x (key + value length) x bytes per element
  recurrent state = Gated-DeltaNet layers x (heads x d_k x d_v + conv state) x 4 bytes
Qwen3.8 / Qwen3.6 are hybrids: only every 4th layer (full_attention_interval)
keeps a KV cache, the rest have a fixed-size recurrent state.

A thin facade: the parsing and maths live in carl_core.domain.gguf, the file and
Metal-limit access in carl_core.adapters.
"""
import os
import sys
from typing import Tuple

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from carl_core.adapters.gguf_reader import local_meta  # noqa: E402
from carl_core.domain.gguf import GIB, KV_BPE, kv_bytes_per_token, model_shape  # noqa: E402
from carl_core.wiring import GPU  # noqa: E402

__all__ = ["GIB", "KV_BPE", "gpu_limit", "kv_bytes_per_token", "local_meta", "model_shape"]


def gpu_limit() -> Tuple[int, str]:
    """(bytes, how) the GPU may use. An iogpu.wired_limit_mb override wins;
    otherwise Metal's recommendedMaxWorkingSetSize (via a cached Swift probe),
    else an estimate (2/3 of RAM up to 32 GB, 3/4 above)."""
    return GPU.limit()

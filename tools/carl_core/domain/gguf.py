"""GGUF header parsing and the memory maths of a model's context.

model_shape() describes how much context memory a model needs:
  KV bytes/token = attention layers x KV heads x (key + value length) x bytes per element
  recurrent state = Gated-DeltaNet layers x (heads x d_k x d_v + conv state) x 4 bytes
Qwen3.8 / Qwen3.6 are hybrids: only every 4th layer (full_attention_interval)
keeps a KV cache, the rest have a fixed-size recurrent state.
"""
from __future__ import annotations

import struct
from typing import Dict, Optional, TypedDict, Union

MetaValue = Union[int, float, bool, str]
Meta = Dict[str, MetaValue]

GIB = 2 ** 30
# compute buffers, MTP draft context and allocator slack: a conservative allowance
OVERHEAD = 1.0 * GIB
DEFAULT_CTX_TRAIN = 262144                  # when a header doesn't say

GGUF_TYPES: Dict[int, str] = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
                              10: "<Q", 11: "<q", 12: "<d"}
GGUF_STRING, GGUF_ARRAY = 8, 9
FILE_TYPES: Dict[int, str] = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
    12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
    19: "IQ2_XXS", 20: "IQ2_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M",
    28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}
# bytes per element of KV cache types (block formats hold 32 values)
KV_BPE: Dict[str, float] = {"f32": 4, "f16": 2, "bf16": 2, "q8_0": 34 / 32, "q4_0": 18 / 32, "q4_1": 20 / 32,
                            "q5_0": 22 / 32, "q5_1": 24 / 32, "iq4_nl": 18 / 32}
# The header read: the chat template and tokenizer arrays come before the tensors, so a
# 64 MB prefix holds every scalar CARL needs (24 MB is enough over HTTP for these models).
LOCAL_HEADER_BYTES = 64 * 1024 * 1024
REMOTE_HEADER_BYTES = 24 * 1024 * 1024


class _Shape(TypedDict):
    """What the context memory of a model depends on (from its GGUF header)."""
    arch: str
    blocks: int
    nextn: int
    attn_layers: int
    rec_layers: int
    kv_elems_per_token: int
    kv_elems_per_token_mtp: int
    rs_bytes: int
    experts: int
    experts_used: int
    ctx_train: int
    ftype: str
    kvh: int
    kl: int
    vl: int
    effort_levels: bool
    thinking_switch: bool


class ModelShape(_Shape, total=False):
    """_Shape, and the sliding-window layers (Gemma): swa_window = the window (0: none), and
    kv_elems_per_token_swa = their KV elements per token (kv_elems_per_token then counts only the
    full-attention layers). Such layers keep only the window unless the server runs --swa-full,
    which a restored state needs (fit.need_bytes(swa_full)). Absent in shapes cached before 11.6."""
    swa_window: int
    kv_elems_per_token_swa: int


class _Reader:
    """Sequential little-endian reader over a header prefix."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def scalar(self, fmt: str) -> MetaValue:
        v: MetaValue = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += struct.calcsize(fmt)
        return v

    def uint(self, fmt: str) -> int:
        v = self.scalar(fmt)
        if not isinstance(v, int):
            raise struct.error("not an integer")
        return v

    def string(self) -> bytes:
        n = self.uint("<Q")
        s = self.data[self.pos:self.pos + n]
        self.pos += n
        return s

    def skip(self, n: int) -> None:
        self.pos += n


def parse_meta(head: bytes) -> Meta:
    """Scalar metadata from the first bytes of a GGUF file (arrays skipped). The prefix
    may cut the header short: a read past its end or an unknown type ends the parse and
    what was read before it is kept."""
    out: Meta = {}
    if head[:4] != b"GGUF":
        return out
    r = _Reader(head)
    r.skip(4)
    try:
        r.uint("<I")                                 # version
        r.uint("<Q")                                 # tensor count
        for _ in range(r.uint("<Q")):
            key = r.string().decode(errors="replace")
            t = r.uint("<I")
            if t == GGUF_STRING:
                v = r.string()
                if key == "tokenizer.chat_template":
                    # only what the monitor needs to know about the template
                    out["_has_reasoning_effort"] = b"reasoning_effort" in v
                    out["_has_enable_thinking"] = b"enable_thinking" in v
                else:
                    out[key] = v.decode(errors="replace")
            elif t == GGUF_ARRAY:
                at, cnt = r.uint("<I"), r.uint("<Q")
                if at == GGUF_STRING:
                    for _ in range(cnt):
                        r.string()
                elif key.endswith(".attention.sliding_window_pattern") and cnt <= 4096:
                    # which layers use the sliding window, as "1" / "0" per layer (model_shape)
                    out[key] = "".join("1" if r.scalar(GGUF_TYPES[at]) else "0" for _ in range(cnt))
                else:
                    r.skip(struct.calcsize(GGUF_TYPES[at]) * cnt)
            else:
                out[key] = r.scalar(GGUF_TYPES[t])
    except (struct.error, KeyError):
        pass
    return out


def _num(meta: Meta, key: str, default: int = 0) -> int:
    v = meta.get(key, default)
    return int(v) if isinstance(v, (int, float)) else default


def model_shape(meta: Meta) -> ModelShape:
    a = str(meta.get("general.architecture", ""))

    def g(key: str, default: int = 0) -> int:
        return _num(meta, f"{a}.{key}", default)

    blocks, nextn = g("block_count"), g("nextn_predict_layers")
    main = blocks - nextn
    interval = g("full_attention_interval", 1) or 1
    attn = main // interval
    rec = main - attn if interval > 1 else 0
    kvh, kl, vl = g("attention.head_count_kv"), g("attention.key_length"), g("attention.value_length")
    if not kl:
        kl = vl = g("embedding_length") // max(g("attention.head_count", 1), 1)
    kv_full, kv_swa = attn * kvh * (kl + vl), 0
    window = g("attention.sliding_window")
    pattern = meta.get(f"{a}.attention.sliding_window_pattern")
    own = main - g("attention.shared_kv_layers")     # Gemma 4: the last layers reuse earlier layers' KV
    if window and isinstance(pattern, str) and 0 < own <= len(pattern):
        n_swa = pattern[:own].count("1")
        kv_full = (own - n_swa) * kvh * (kl + vl)
        kv_swa = n_swa * kvh * (g("attention.key_length_swa", kl) + g("attention.value_length_swa", vl))
    inner, rank, state = g("ssm.inner_size"), g("ssm.time_step_rank"), g("ssm.state_size")
    conv, groups = g("ssm.conv_kernel"), g("ssm.group_count")
    rs_layer = 0
    if rec and rank and state:
        rs_layer = rank * state * (inner // rank) * 4 + max(conv - 1, 0) * (inner + 2 * groups * state) * 4
    ft = meta.get("general.file_type")
    return {
        "arch": a, "blocks": blocks, "nextn": nextn, "attn_layers": attn, "rec_layers": rec,
        "kv_elems_per_token": kv_full, "kv_elems_per_token_mtp": nextn * kvh * (kl + vl),
        "rs_bytes": rec * rs_layer, "experts": g("expert_count"), "experts_used": g("expert_used_count"),
        "ctx_train": g("context_length"),
        "ftype": FILE_TYPES.get(ft, str(ft)) if isinstance(ft, int) else ("?" if ft is None else str(ft)),
        "kvh": kvh, "kl": kl, "vl": vl,
        "effort_levels": bool(meta.get("_has_reasoning_effort")),
        "thinking_switch": bool(meta.get("_has_enable_thinking")),
        "swa_window": window, "kv_elems_per_token_swa": kv_swa,
    }


def kv_bytes_per_token(shape: ModelShape, ktype: str = "q4_0", vtype: Optional[str] = None) -> float:
    """KV cache bytes per token of context (K and V may use different cache types)."""
    half = shape["kv_elems_per_token"] / 2
    return half * KV_BPE.get(ktype, 2) + half * KV_BPE.get(vtype or ktype, 2)


def swa_bytes_per_token(shape: ModelShape, kv: str = "q4_0") -> float:
    """KV bytes per token of the sliding-window layers (0 for other models)."""
    return shape.get("kv_elems_per_token_swa", 0) * KV_BPE.get(kv, 2)


def ctx_train(shape: ModelShape) -> int:
    """The trained context length, or 256K when the header doesn't say."""
    return shape["ctx_train"] or DEFAULT_CTX_TRAIN

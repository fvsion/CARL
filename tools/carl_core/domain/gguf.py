"""GGUF header parsing and the memory maths of a model's context.

model_shape() describes how much context memory a model needs:
  KV bytes/token = attention layers x KV heads x (key + value length) x bytes per element
  recurrent state = Gated-DeltaNet layers x (heads x d_k x d_v + conv state) x 4 bytes
Qwen3.8 / Qwen3.6 are hybrids: only every 4th layer (full_attention_interval)
keeps a KV cache, the rest have a fixed-size recurrent state.
"""
from __future__ import annotations

import struct
from typing import Dict, Iterator, List, Optional, Tuple, TypedDict, Union

MetaValue = Union[int, float, bool, str]
Meta = Dict[str, MetaValue]

GIB = 2 ** 30
# compute buffers, MTP draft context and allocator slack: a conservative allowance
OVERHEAD = 1.0 * GIB
DEFAULT_CTX_TRAIN = 262144                  # when a header doesn't say

GGUF_TYPES: Dict[int, str] = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
                              10: "<Q", 11: "<q", 12: "<d"}
GGUF_U32, GGUF_STRING, GGUF_ARRAY, GGUF_U64 = 4, 8, 9, 10
CHAT_TEMPLATE_KEY = "tokenizer.chat_template"
MAX_LAYERS = 4096                           # a per-layer array longer than this is not read
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
META_VERSION = 2          # what parse_meta keeps (2: per-layer KV heads, the SWA pattern); cached headers carry it


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


class GGUFError(ValueError):
    """The bytes are not a GGUF header, or the header runs past the bytes read."""


class _Reader:
    """Sequential little-endian reader over a header prefix. A read past the end of the
    prefix, or a value type GGUF does not define, raises GGUFError."""

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def scalar(self, vtype: int) -> MetaValue:
        fmt = _fmt(vtype)
        try:
            v: MetaValue = struct.unpack_from(fmt, self.data, self.pos)[0]
        except struct.error:
            raise GGUFError("metadata runs past the part of the file read") from None
        self.pos += struct.calcsize(fmt)
        return v

    def uint(self, vtype: int) -> int:
        v = self.scalar(vtype)
        if not isinstance(v, int) or isinstance(v, bool):
            raise GGUFError("not an integer")
        return v

    def string(self) -> bytes:
        n = self.uint(GGUF_U64)
        if self.pos + n > len(self.data):
            raise GGUFError("metadata runs past the part of the file read")
        s = self.data[self.pos:self.pos + n]
        self.pos += n
        return s

    def skip_value(self, vtype: int) -> None:
        """Move past one value of this type (a string, an array or a number)."""
        if vtype == GGUF_STRING:
            self.string()
        elif vtype == GGUF_ARRAY:
            self.skip_array(self.uint(GGUF_U32), self.uint(GGUF_U64))
        else:
            self.pos += struct.calcsize(_fmt(vtype))

    def skip_array(self, atype: int, n: int) -> None:
        """Move past the n elements of an array (its type and count already read)."""
        if atype == GGUF_STRING:
            for _ in range(n):
                self.string()
        else:
            self.pos += struct.calcsize(_fmt(atype)) * n


def _fmt(vtype: int) -> str:
    if vtype not in GGUF_TYPES:
        raise GGUFError(f"unknown metadata value type {vtype}")
    return GGUF_TYPES[vtype]


def _entries(head: bytes) -> Iterator[Tuple[str, int, _Reader]]:
    """Each metadata key of a GGUF header with its value type, and the reader placed at its
    value. The caller may read the value; when it does not, it is skipped. Raises GGUFError
    when head is not a GGUF header or ends before the metadata does."""
    if head[:4] != b"GGUF":
        raise GGUFError("not a GGUF file")
    r = _Reader(head, 4)
    r.uint(GGUF_U32)                                 # version
    r.uint(GGUF_U64)                                 # tensor count
    for _ in range(r.uint(GGUF_U64)):
        key = r.string().decode("utf-8", errors="replace")
        vtype = r.uint(GGUF_U32)
        start = r.pos
        yield key, vtype, r
        if r.pos == start:
            r.skip_value(vtype)


def chat_template(head: bytes) -> Optional[str]:
    """The tokenizer.chat_template string of a GGUF header, None when it has none. Raises
    GGUFError when head is not a GGUF header, ends before the template, or the template is
    not UTF-8 (host/gguf-chat-template.py)."""
    for key, vtype, r in _entries(head):
        if key == CHAT_TEMPLATE_KEY and vtype == GGUF_STRING:
            try:
                return r.string().decode("utf-8")
            except UnicodeDecodeError:
                raise GGUFError("the chat template is not UTF-8") from None
    return None


def parse_meta(head: bytes) -> Meta:
    """Scalar metadata from the first bytes of a GGUF file (arrays skipped). The prefix
    may cut the header short: a read past its end or an unknown type ends the parse and
    what was read before it is kept."""
    out: Meta = {}
    try:
        for key, t, r in _entries(head):
            if t == GGUF_STRING:
                v = r.string()
                if key == CHAT_TEMPLATE_KEY:
                    # only what the monitor needs to know about the template
                    out["_has_reasoning_effort"] = b"reasoning_effort" in v
                    out["_has_enable_thinking"] = b"enable_thinking" in v
                else:
                    out[key] = v.decode(errors="replace")
            elif t == GGUF_ARRAY:
                pattern = key.endswith(".attention.sliding_window_pattern")
                if not (pattern or key.endswith(".attention.head_count_kv")):
                    continue                         # skipped by _entries
                at, cnt = r.uint(GGUF_U32), r.uint(GGUF_U64)
                if at == GGUF_STRING or cnt > MAX_LAYERS:
                    r.skip_array(at, cnt)
                elif pattern:
                    # which layers use the sliding window, as "1" / "0" per layer (model_shape)
                    out[key] = "".join("1" if r.scalar(at) else "0" for _ in range(cnt))
                else:
                    # KV heads per layer (Gemma 4 26B / 31B: fewer on the full-attention layers), "8,8,2,..."
                    out[key] = ",".join(str(int(r.scalar(at))) for _ in range(cnt))
            else:
                out[key] = r.scalar(t)
    except GGUFError:
        pass
    return out


def _num(meta: Meta, key: str, default: int = 0) -> int:
    v = meta.get(key, default)
    return int(v) if isinstance(v, (int, float)) else default


def _per_layer(v: object, layers: int) -> List[int]:
    """A per-layer list ("8,8,2,...", parse_meta) for `layers` layers; [] for a single number."""
    if not isinstance(v, str) or not v:
        return []
    try:
        heads = [int(x) for x in v.split(",")]
    except ValueError:
        return []
    return heads[:layers] if len(heads) >= layers else []


def model_shape(meta: Meta) -> ModelShape:
    a = str(meta.get("general.architecture", ""))

    def g(key: str, default: int = 0) -> int:
        return _num(meta, f"{a}.{key}", default)

    blocks, nextn = g("block_count"), g("nextn_predict_layers")
    main = blocks - nextn
    interval = g("full_attention_interval", 1) or 1
    attn = main // interval
    rec = main - attn if interval > 1 else 0
    kl, vl = g("attention.key_length"), g("attention.value_length")
    if not kl:
        kl = vl = g("embedding_length") // max(g("attention.head_count", 1), 1)
    per_layer = _per_layer(meta.get(f"{a}.attention.head_count_kv"), main)    # KV heads of each layer
    kvh = max(per_layer) if per_layer else g("attention.head_count_kv")
    # an array: the layers with KV heads (a recurrent layer has none); a number: every attention layer
    kv_full, kv_swa = (sum(per_layer) if per_layer else attn * kvh) * (kl + vl), 0
    window = g("attention.sliding_window")
    pattern = meta.get(f"{a}.attention.sliding_window_pattern")
    own = main - g("attention.shared_kv_layers")     # Gemma 4: the last layers reuse earlier layers' KV
    if window and isinstance(pattern, str) and 0 < own <= len(pattern):
        heads = per_layer or [kvh] * own
        kv_full = sum(heads[i] for i in range(own) if pattern[i] == "0") * (kl + vl)
        kv_swa = (sum(heads[i] for i in range(own) if pattern[i] == "1")
                  * (g("attention.key_length_swa", kl) + g("attention.value_length_swa", vl)))
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

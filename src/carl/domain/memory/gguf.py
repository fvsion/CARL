"""GGUF header parsing: the metadata, the chat template and the tensor table.

The header is the metadata (key, type, value), then one entry per tensor (name, dimensions,
ggml type, offset), then the tensor data at the next multiple of general.alignment. The
tensor table gives the weights' bytes: the MoE experts of each layer (what --n-cpu-moe moves
to RAM), token_embd and output. The memory maths over the header is shape.py.
"""
from __future__ import annotations

import re
import struct
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple

# the shape's names stay importable from here (the old domain/gguf.py) until W9.4
from .shape import (ATTN_WIDTH_KEY, DEFAULT_CTX_TRAIN, EMBD_BYTES_KEY, EXPERT_BYTES_KEY, EXPERT_DOWN_KEY,  # noqa: F401
                    EXPERT_FUSED_KEY, EXTRA_WIDTH_KEY,
                    FF_WIDTH_KEY, FILE_TYPES, GIB, KV_BPE, LAYER_WIDTH_KEY, NEXTN_TENSORS_KEY, OUTPUT_BYTES_KEY,
                    PER_LAYER_BYTES_KEY, TENSOR_BYTES_KEY, Meta, MetaValue, ModelShape, ctx_train, kv_bytes_per_token, model_shape,
                    swa_bytes_per_token)

GGUF_TYPES: Dict[int, str] = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
                              10: "<Q", 11: "<q", 12: "<d"}
GGUF_U32, GGUF_STRING, GGUF_ARRAY, GGUF_U64 = 4, 8, 9, 10
CHAT_TEMPLATE_KEY = "tokenizer.chat_template"
ALIGNMENT_KEY = "general.alignment"
DEFAULT_ALIGNMENT = 32                      # the tensor data's alignment when the header doesn't say
MAX_LAYERS = 4096                           # a per-layer array longer than this is not read
MAX_TENSORS = 1 << 20                       # a tensor count above this is not a model's
MAX_DIMS = 4                                # GGML_MAX_DIMS
# ggml types: (values per block, bytes per block), by the type id in the tensor table (ggml.h;
# the ids ggml removed are absent). A tensor of another type leaves the sums out.
GGML_BLOCKS: Dict[int, Tuple[int, int]] = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24), 8: (32, 34), 9: (32, 36),
    10: (256, 84), 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210), 15: (256, 292),
    16: (256, 66), 17: (256, 74), 18: (256, 98), 19: (256, 50), 20: (32, 18), 21: (256, 110), 22: (256, 82),
    23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4), 27: (1, 8), 28: (1, 8), 29: (256, 56), 30: (1, 2),
    34: (256, 54), 35: (256, 66), 39: (32, 17)}
# The tensors --n-cpu-moe N keeps in RAM: the experts of blocks 0 to N-1. llama.cpp b11429 and b11738 match
# "blk\.<i>\.ffn_(up|down|gate|gate_up)_(ch|)exps" anywhere in the name (weights, biases and scales).
EXPERTS_RE = re.compile(r"blk\.(\d+)\.ffn_(up|down|gate|gate_up)_(ch|)exps")
# The header read: the chat template and tokenizer arrays come before the tensors, so a
# 64 MB prefix holds every scalar CARL needs and the tensor table (24 MB is enough over HTTP for these models).
LOCAL_HEADER_BYTES = 64 * 1024 * 1024
REMOTE_HEADER_BYTES = 24 * 1024 * 1024
# what parse_meta keeps (2: per-layer KV heads, the SWA pattern; 3: the tensor table's sums; 4: the matrices' widths; 5: the
# experts' down matrices); cached headers carry it. The count of the MTP head's tensors (A73) is no new version: a cached
# entry without it leaves has_head to the header's nextn_predict_layers alone.
META_VERSION = 5


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


def _open(head: bytes) -> Tuple[_Reader, int, int]:
    """A reader placed at the first metadata key, the tensor count and the key count. Raises
    GGUFError when head is not a GGUF header."""
    if head[:4] != b"GGUF":
        raise GGUFError("not a GGUF file")
    r = _Reader(head, 4)
    r.uint(GGUF_U32)                                 # version
    return r, r.uint(GGUF_U64), r.uint(GGUF_U64)


def _entries(head: bytes) -> Iterator[Tuple[str, int, _Reader]]:
    """Each metadata key of a GGUF header with its value type, and the reader placed at its
    value. The caller may read the value; when it does not, it is skipped. Raises GGUFError
    when head is not a GGUF header or ends before the metadata does."""
    r, _, keys = _open(head)
    return _keys(r, keys)


def _keys(r: _Reader, count: int) -> Iterator[Tuple[str, int, _Reader]]:
    """The `count` metadata keys at the reader (_entries); the reader ends after the last value."""
    for _ in range(count):
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
    """Scalar metadata from the first bytes of a GGUF file (arrays skipped), and the tensor
    table's sums (tensor_sums) when the file has tensors. The prefix may cut the header short:
    a read past its end or an unknown type ends the parse and what was read before it is kept
    (the sums only whole)."""
    out: Meta = {}
    try:
        reader, tensors, keys = _open(head)
        for key, t, r in _keys(reader, keys):
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
        if tensors:
            align = out.get(ALIGNMENT_KEY)
            table = _tensors(reader, tensors, align if isinstance(align, int) and not isinstance(align, bool)
                             else DEFAULT_ALIGNMENT)
            used = out.get(f"{out.get('general.architecture', '')}.expert_used_count", 0)
            out.update(tensor_sums(table.tensors, used if isinstance(used, int) and not isinstance(used, bool) else 0))
    except GGUFError:
        pass
    return out


class Tensor(NamedTuple):
    """One entry of the tensor table."""
    name: str
    ggml_type: int
    dims: Tuple[int, ...]                   # ggml order: the row length first
    offset: int                             # from the start of the tensor data


class TensorTable(NamedTuple):
    tensors: List[Tensor]
    data_start: int                         # where the tensor data starts in the file (the header's size)


def tensor_table(head: bytes) -> TensorTable:
    """The tensor table of a GGUF header. Raises GGUFError when head is not a GGUF header or
    ends before the table does."""
    r, tensors, keys = _open(head)
    alignment = DEFAULT_ALIGNMENT
    for key, vtype, kr in _keys(r, keys):
        if key == ALIGNMENT_KEY and vtype not in (GGUF_STRING, GGUF_ARRAY):
            v = kr.scalar(vtype)                     # any integer type, as parse_meta takes it
            if isinstance(v, int) and not isinstance(v, bool):
                alignment = v
    return _tensors(r, tensors, alignment)


def _tensors(r: _Reader, count: int, alignment: int) -> TensorTable:
    """The `count` tensor entries at the reader (placed after the metadata)."""
    if count > MAX_TENSORS:
        raise GGUFError(f"{count} tensors")
    out: List[Tensor] = []
    for _ in range(count):
        name = r.string().decode("utf-8", errors="replace")
        n_dims = r.uint(GGUF_U32)
        if n_dims > MAX_DIMS:
            raise GGUFError(f"tensor {name} has {n_dims} dimensions")
        dims = tuple(r.uint(GGUF_U64) for _ in range(n_dims))
        out.append(Tensor(name, r.uint(GGUF_U32), dims, r.uint(GGUF_U64)))
    if alignment <= 0:
        alignment = DEFAULT_ALIGNMENT
    return TensorTable(out, -(-r.pos // alignment) * alignment)


def tensor_bytes(t: Tensor) -> int:
    """The bytes of one tensor's data. Raises GGUFError for a ggml type CARL does not know, or
    a row that is not a whole number of blocks."""
    if t.ggml_type not in GGML_BLOCKS:
        raise GGUFError(f"tensor {t.name} has the unknown ggml type {t.ggml_type}")
    per_block, block_bytes = GGML_BLOCKS[t.ggml_type]
    row = t.dims[0] if t.dims else 1
    if row % per_block:
        raise GGUFError(f"tensor {t.name}: a row of {row} is not whole blocks of {per_block}")
    rows = 1
    for d in t.dims[1:]:
        rows *= d
    return row // per_block * block_bytes * rows


BLOCK_RE = re.compile(r"blk\.(\d+)\.(.*)\.weight$")
FF_RE = re.compile(r"ffn_(up|gate)(_exps)?$|ffn_gate_up_exps$")
ATTN_RE = re.compile(r"(attn|ssm)_")


def matrix_width(t: Tensor, used: int) -> int:
    """The elements one token gets out of a weight matrix: its second dimension (the first is the row it reads); an
    experts tensor (three dimensions) gives that for each of the `used` experts a token picks. A table that is read by
    index (Gemma's per-layer table) gives one row: the first dimension; token_embd and the output layer give none (the
    output is read for the few tokens that are kept). A vector, a convolution (ssm_conv1d) and the MTP head (nextn) give
    none."""
    name = t.name
    if len(t.dims) < 2 or "nextn" in name or "conv" in name:
        return 0
    if name.startswith("token_embd.") or name.startswith("output."):
        return 0
    if "_embd." in name:
        return t.dims[0]
    if len(t.dims) == 3:
        return t.dims[1] * (used if "exps" in name else 1)
    return t.dims[1]


def width_sums(tensors: Sequence[Tensor], used: int) -> Meta:
    """The widths calibration.py builds the compute buffer from (shape.LAYER_WIDTH_KEY): the widest layer's matrices'
    outputs added, the matrices outside the layers, the widest feed-forward intermediate (the gate and the up matrix: a
    fused gate_up_exps holds both, so half) and the widest layer's attention (attn_*) and recurrent (ssm_*) matrices."""
    layers: Dict[int, int] = {}
    attn: Dict[int, int] = {}
    extra = ff = 0
    for t in tensors:
        w = matrix_width(t, used)
        m = BLOCK_RE.match(t.name)
        if not m:
            extra += w
            continue
        layers[int(m.group(1))] = layers.get(int(m.group(1)), 0) + w
        if ATTN_RE.match(m.group(2)):
            attn[int(m.group(1))] = attn.get(int(m.group(1)), 0) + w
        if w and FF_RE.match(m.group(2)):
            ff = max(ff, w // 2 if m.group(2) == "ffn_gate_up_exps" else w)
    return {LAYER_WIDTH_KEY: max(layers.values(), default=0), EXTRA_WIDTH_KEY: extra, FF_WIDTH_KEY: ff,
            ATTN_WIDTH_KEY: max(attn.values(), default=0)}


def tensor_sums(tensors: Sequence[Tensor], used: int = 0) -> Meta:
    """The sums model_shape takes, as header scalars: every tensor, the experts of each layer
    (by blk index, as --n-cpu-moe counts them), token_embd, output and Gemma's per-layer embeddings, and the matrices'
    widths (width_sums; `used`: the experts a token picks). Raises GGUFError when a tensor's size is unknown
    (tensor_bytes)."""
    total = embd = output = per_layer = nextn = 0
    experts: Dict[int, int] = {}
    down: Dict[int, int] = {}
    fused = 0
    for t in tensors:
        n = tensor_bytes(t)
        total += n
        nextn += "nextn" in t.name
        m = EXPERTS_RE.search(t.name)
        if m:
            layer = int(m.group(1))
            experts[layer] = experts.get(layer, 0) + n
            if m.group(2) == "down":
                down[layer] = down.get(layer, 0) + n
            elif m.group(2) == "gate_up":
                fused = 1
        elif t.name.startswith("token_embd."):
            embd += n
        elif t.name.startswith("output."):
            output += n
        elif t.name.startswith("per_layer_token_embd."):
            per_layer += n
    layers = max(experts) + 1 if experts else 0
    if layers > MAX_LAYERS:
        raise GGUFError(f"an expert tensor in layer {layers - 1}")
    return {TENSOR_BYTES_KEY: total, EXPERT_BYTES_KEY: ",".join(str(experts.get(i, 0)) for i in range(layers)),
            EXPERT_DOWN_KEY: ",".join(str(down.get(i, 0)) for i in range(layers)), EXPERT_FUSED_KEY: fused,
            EMBD_BYTES_KEY: embd, OUTPUT_BYTES_KEY: output, PER_LAYER_BYTES_KEY: per_layer,
            NEXTN_TENSORS_KEY: nextn, **width_sums(tensors, used)}

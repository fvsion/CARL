"""The memory maths of a model's context and weights, from its GGUF header (gguf.py reads it).

model_shape() describes how much context memory a model needs:
  KV bytes/token = attention layers x KV heads x (key + value length) x bytes per element
  recurrent state = Gated-DeltaNet layers x (heads x d_k x d_v + conv state) x 4 bytes
Qwen3.8 / Qwen3.6 are hybrids: only every 4th layer (full_attention_interval)
keeps a KV cache, the rest have a fixed-size recurrent state.
From the tensor table it also takes the weights' bytes: the MoE experts of each layer
(what --n-cpu-moe keeps in RAM), token_embd and output.
"""
from __future__ import annotations

from typing import Dict, List, Optional, TypedDict, Union

MetaValue = Union[int, float, bool, str]
Meta = Dict[str, MetaValue]                 # the header's scalars, as gguf.parse_meta keeps them

GIB = 2 ** 30
DEFAULT_CTX_TRAIN = 262144                  # when a header doesn't say

FILE_TYPES: Dict[int, str] = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
    12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
    19: "IQ2_XXS", 20: "IQ2_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M",
    28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}
# bytes per element of KV cache types (block formats hold 32 values)
KV_BPE: Dict[str, float] = {"f32": 4, "f16": 2, "bf16": 2, "q8_0": 34 / 32, "q4_0": 18 / 32, "q4_1": 20 / 32,
                            "q5_0": 22 / 32, "q5_1": 24 / 32, "iq4_nl": 18 / 32}
# The tensor table's sums in the header's scalars (gguf.tensor_sums writes them; only when the table was read)
TENSOR_BYTES_KEY = "_tensor_bytes"          # every tensor
EXPERT_BYTES_KEY = "_expert_bytes"          # the experts of each layer, "0,0,123,..." (layer = blk index)
EXPERT_DOWN_KEY = "_expert_down_bytes"      # the down matrices of the experts of each layer, as the list above (A60)
EXPERT_FUSED_KEY = "_expert_fused"          # 1: the gate and the up matrix of the experts are one tensor (gate_up), else 0
EMBD_BYTES_KEY = "_token_embd_bytes"
OUTPUT_BYTES_KEY = "_output_bytes"          # 0: the output shares token_embd
PER_LAYER_BYTES_KEY = "_per_layer_embd_bytes"   # Gemma's per-layer embeddings (per_layer_token_embd); 0: none
# The widths of the matrices (elements out of a matrix for one token; gguf.tensor_sums writes them with the sums above)
LAYER_WIDTH_KEY = "_layer_width"            # the widest layer: its matrices' outputs added (the experts of the tokens' choice)
EXTRA_WIDTH_KEY = "_extra_width"            # the matrices that are not in a layer (Gemma's per-layer inputs)
FF_WIDTH_KEY = "_ff_width"                  # the widest feed-forward intermediate (the experts' choice or the dense width)
ATTN_WIDTH_KEY = "_attn_width"              # the widest layer's attention (and recurrent) matrices' outputs added
NEXTN_TENSORS_KEY = "_nextn_tensors"        # how many tensors are the MTP head's (nextn in their names; A73)


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
    which a restored state needs (fit.need_bytes(swa_full)). Absent in shapes cached before 11.6.
    The compute buffers (fit.compute_bytes, 23.4.4; calibration.py, A60): embd = the embedding length;
    layer_width, extra_width, ff_width and attn_width = the matrices' widths (the tensor table: LAYER_WIDTH_KEY); attn_elems = the K + V elements per token of the
    widest full-attention layer (attn_elems_swa: of the widest sliding-window layer), act_width = the
    widest activation of one token (the FFN, the MoE experts' outputs, the recurrent projections).
    The weights (the tensor table, W6.3; absent when the header read did not reach it): tensor_bytes =
    every tensor, expert_bytes = the MoE experts of each layer by blk index (what --n-cpu-moe N keeps in
    RAM: the first N entries; [] for a dense model), expert_down_bytes = their down matrices of each layer (A60: the
    compute buffer's staged copy follows them), expert_fused = 1 when the gate and the up matrix are one tensor, embd_bytes = token_embd, output_bytes = output
    (0 when it shares token_embd), per_layer_bytes = Gemma's per-layer embeddings (0: the model has none; absent
    in a shape cached before them). nextn_tensors = how many tensors of the table are the MTP head's (A73; absent
    with the weights, and in a shape cached before it)."""
    swa_window: int
    kv_elems_per_token_swa: int
    attn_elems: int
    attn_elems_swa: int
    act_width: int
    tensor_bytes: int
    expert_bytes: List[int]
    expert_down_bytes: List[int]
    expert_fused: int
    embd_bytes: int
    output_bytes: int
    per_layer_bytes: int
    embd: int
    layer_width: int
    extra_width: int
    ff_width: int
    attn_width: int
    nextn_tensors: int


def _num(meta: Meta, key: str, default: int = 0) -> int:
    v = meta.get(key, default)
    return int(v) if isinstance(v, (int, float)) else default


def _per_layer(v: object, layers: int) -> List[int]:
    """A per-layer list ("8,8,2,...", parse_meta) for `layers` layers; [] for a single number."""
    heads = _int_list(v)
    return heads[:layers] if len(heads) >= layers else []


NOT_LM_ARCHS = ("t5encoder", "wavtokenizer-dec")            # besides every architecture with "bert" in its name


def not_language_model_arch(arch: str) -> bool:
    """The GGUF architecture is not a language model's (bert, nomic-bert, modern-bert, jina-bert-v2 ...: a model that
    embeds or classifies text, not one that writes it): no list of models shows it (decision A68)."""
    a = arch.lower()
    return "bert" in a or a in NOT_LM_ARCHS


def has_head(shape: ModelShape) -> bool:
    """The file carries an MTP head (A73): the header says nextn_predict_layers (a layer of the head at the end of the
    blocks) and, when the tensor table was read, the table holds the head's nextn tensors. A conversion that dropped
    the layers (the heretic's i1-IQ3_XXS: 40 blocks, no nextn_predict_layers) has none; the stock 35B-A3B has 41 blocks
    and nextn_predict_layers 1. The one place that says it: the mark of a quantization, mtp_source and mtp_spec."""
    if shape.get("nextn", 0) <= 0:
        return False
    tensors = shape.get("nextn_tensors")
    return tensors is None or tensors > 0


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
    width, width_swa = kvh * (kl + vl), 0                 # the widest layer's K + V elements per token
    window = g("attention.sliding_window")
    pattern = meta.get(f"{a}.attention.sliding_window_pattern")
    own = main - g("attention.shared_kv_layers")     # Gemma 4: the last layers reuse earlier layers' KV
    if window and isinstance(pattern, str) and 0 < own <= len(pattern):
        heads = per_layer or [kvh] * own
        kls = g("attention.key_length_swa", kl) + g("attention.value_length_swa", vl)
        full_heads = [heads[i] for i in range(own) if pattern[i] == "0"]
        swa_heads = [heads[i] for i in range(own) if pattern[i] == "1"]
        kv_full, kv_swa = sum(full_heads) * (kl + vl), sum(swa_heads) * kls
        width, width_swa = max(full_heads, default=0) * (kl + vl), max(swa_heads, default=0) * kls
    inner, rank, state = g("ssm.inner_size"), g("ssm.time_step_rank"), g("ssm.state_size")
    conv, groups = g("ssm.conv_kernel"), g("ssm.group_count")
    rs_layer = 0
    if rec and rank and state:
        rs_layer = rank * state * (inner // rank) * 4 + max(conv - 1, 0) * (inner + 2 * groups * state) * 4
    embd, used = g("embedding_length"), g("expert_used_count")
    # the widest activation of one token: the FFN; an MoE layer's expert FFNs (+ the shared one) and the
    # experts' outputs before they are summed (embd x used); a recurrent layer's q, k, v and gate projections
    act = max(embd, g("feed_forward_length"),
              g("expert_feed_forward_length") * used + g("expert_shared_feed_forward_length"),
              embd * used if g("expert_count") else 0,
              2 * groups * state + 2 * inner if rec else 0)
    ft = meta.get("general.file_type")
    shape: ModelShape = {
        "arch": a, "blocks": blocks, "nextn": nextn, "attn_layers": attn, "rec_layers": rec,
        "kv_elems_per_token": kv_full, "kv_elems_per_token_mtp": nextn * kvh * (kl + vl),
        "rs_bytes": rec * rs_layer, "experts": g("expert_count"), "experts_used": g("expert_used_count"),
        "ctx_train": g("context_length"),
        "ftype": FILE_TYPES.get(ft, str(ft)) if isinstance(ft, int) else ("?" if ft is None else str(ft)),
        "kvh": kvh, "kl": kl, "vl": vl,
        "effort_levels": bool(meta.get("_has_reasoning_effort")),
        "thinking_switch": bool(meta.get("_has_enable_thinking")),
        "swa_window": window, "kv_elems_per_token_swa": kv_swa,
        "attn_elems": width, "attn_elems_swa": width_swa, "act_width": act,
    }
    tensors = meta.get(TENSOR_BYTES_KEY)
    if isinstance(tensors, int) and not isinstance(tensors, bool) and tensors > 0:
        # the weights, when the header read reached the tensor table (gguf.tensor_sums)
        shape["tensor_bytes"] = tensors
        shape["expert_bytes"] = _int_list(meta.get(EXPERT_BYTES_KEY))
        shape["expert_down_bytes"] = _int_list(meta.get(EXPERT_DOWN_KEY))      # a cache entry from before A60 has none
        shape["expert_fused"] = _num(meta, EXPERT_FUSED_KEY)
        shape["embd_bytes"] = _num(meta, EMBD_BYTES_KEY)
        shape["output_bytes"] = _num(meta, OUTPUT_BYTES_KEY)
        shape["per_layer_bytes"] = _num(meta, PER_LAYER_BYTES_KEY)
        if NEXTN_TENSORS_KEY in meta:                     # a cache entry from before A73 has none: the key alone says
            shape["nextn_tensors"] = _num(meta, NEXTN_TENSORS_KEY)
        if LAYER_WIDTH_KEY in meta:                       # a cache entry from before A60 has none: no widths
            shape["embd"] = embd
            shape["layer_width"] = _num(meta, LAYER_WIDTH_KEY)
            shape["extra_width"] = _num(meta, EXTRA_WIDTH_KEY)
            shape["ff_width"] = _num(meta, FF_WIDTH_KEY)
            shape["attn_width"] = _num(meta, ATTN_WIDTH_KEY)
    return shape


def _int_list(v: object) -> List[int]:
    """A list of numbers kept as "1,2,3" in the header's scalars; [] when empty or damaged."""
    if not isinstance(v, str) or not v:
        return []
    try:
        return [int(x) for x in v.split(",")]
    except ValueError:
        return []


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


"""What a start loads of the model file (decision A52). Pure.

A model with an MTP head (Qwen: `nextn_predict_layers` blocks at the end of the file) runs the head only with MTP.
Without MTP, llama.cpp (b11738) does not load those blocks at all: not their experts, not their other weights. The
estimate counts only what loads, in one place (this module), for the one-device need (fit.need_parts), the split
(split.load_of) and the experts in the RAM (offload.plan_of). Before A52 it counted the head's experts on the card
whenever fewer than all expert layers were in the RAM (278 MiB at the A3B IQ3, 8% to 16% over the process on the Windows test PC).

The head's bytes are its experts (exact, from the tensor table) and its share of the other block weights (the file
less the experts, token_embd, output and Gemma's per-layer embeddings, divided over the blocks). Only a calibrated
backend leaves them out (Calibration.idle_head: CUDA, measured on CUDA); Metal's numbers do not change.
The block count stays: the split's cut points count the head's blocks as layers with no cost.
"""
from __future__ import annotations

from typing import List

from .calibration import Calibration
from .gguf import ModelShape


def head_blocks(shape: ModelShape) -> int:
    """The blocks at the end of the file that belong to the MTP head (0: the model has none, or its block count is not
    known)."""
    nextn, blocks = int(shape.get("nextn", 0) or 0), int(shape.get("blocks", 0) or 0)
    return nextn if 0 < nextn < blocks else 0


def idle(shape: ModelShape, mtp: bool, calib: Calibration) -> bool:
    """The start leaves the MTP head's blocks out: the model has a head, MTP does not run, and the backend does not
    load an idle head."""
    return calib.idle_head and not mtp and head_blocks(shape) > 0


def experts(shape: ModelShape, mtp: bool, calib: Calibration) -> List[float]:
    """The bytes of the MoE experts of each block that loads, by blk index ([] for a dense model)."""
    out = [float(e) for e in (shape.get("expert_bytes") or [])]
    return out[:int(shape.get("blocks", 0)) - head_blocks(shape)] if idle(shape, mtp, calib) else out


def head_bytes(shape: ModelShape, file: float, mtp: bool, calib: Calibration) -> float:
    """The bytes of the MTP head's blocks in a model file of `file` bytes that the start does not load (0: it loads
    them)."""
    if not idle(shape, mtp, calib):
        return 0.0
    blocks, head = int(shape.get("blocks", 0)), head_blocks(shape)
    all_experts = [float(e) for e in (shape.get("expert_bytes") or [])]
    embd, out = float(shape.get("embd_bytes", 0) or 0), float(shape.get("output_bytes", 0) or 0)
    rest = max(file - sum(all_experts) - embd - out - calib.ram_bytes(shape), 0.0)
    return rest / blocks * head + sum(all_experts[blocks - head:])


def trimmed(shape: ModelShape, mtp: bool, calib: Calibration) -> ModelShape:
    """The shape with only the experts of the blocks that load (the same shape when the head loads)."""
    if not idle(shape, mtp, calib) or not shape.get("expert_bytes"):
        return shape
    out = ModelShape(**shape)                     # a copy: the cached shape is never changed
    out["expert_bytes"] = [int(e) for e in experts(shape, mtp, calib)]
    return out

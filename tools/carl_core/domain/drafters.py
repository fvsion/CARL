"""The MTP drafter for a custom Gemma 4 model (Phase 21.1).

Google ships one MTP drafter for each Gemma 4 size; the catalogue pins each one (the `draft`
field of its Gemma entry). A Gemma 4 GGUF from another repo (unsloth, bartowski, a fine-tune)
has the same size class, so the catalogue's drafter of that size fits it. The size class comes
from the file's header: architecture gemma4, its layer count and its expert count (the same in
every repackaging, measured 2026-10-04 on the ggml-org and unsloth files). A fine-tune can
accept fewer of the drafter's guesses: Auto-tune measures that, and keeps n-gram when MTP loses.
Pure.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .gguf import ModelShape
from .types import Catalog, HfRef

# (layers, experts) of each Gemma 4 size -> the catalogue model whose drafter it uses
GEMMA4_SIZES: Dict[Tuple[int, int], str] = {
    (42, 0): "gemma-4-e4b",
    (48, 0): "gemma-4-12b",
    (30, 128): "gemma-4-26b-a4b",
    (60, 0): "gemma-4-31b",
}


def matching_drafter(shape: Optional[ModelShape], catalog: Catalog) -> Optional[Tuple[str, HfRef]]:
    """(the catalogue model of the same size, its pinned drafter) for a Gemma 4 header; None for
    any other model, an unknown size, or a catalogue entry without a drafter."""
    if shape is None or shape.get("arch") != "gemma4":
        return None
    name = GEMMA4_SIZES.get((int(shape.get("blocks", 0)), int(shape.get("experts", 0))))
    entry = next((m for m in catalog.get("models", []) if m.get("name") == name), None)
    draft = entry.get("draft") if entry else None
    return (str(name), draft) if draft else None

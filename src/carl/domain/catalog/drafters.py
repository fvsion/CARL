"""The MTP drafter for a custom Gemma 4 model (Phase 21.1).

Google ships one MTP drafter for each Gemma 4 size; the catalogue pins each one (in the `drafts`
list of its Gemma entry, the drafter of kind mtp-file: drafts.py) and says which size it fits (the `draft_size` field: the GGUF header's
architecture, layer count and expert count). A Gemma 4 GGUF from another repo (unsloth, bartowski,
a fine-tune) has the same size class, so the catalogue's drafter of that size fits it. The size
class comes from the file's header (the same in every repackaging, measured 2026-10-04 on the
ggml-org and unsloth files). A fine-tune can accept fewer of the drafter's guesses: Auto-tune
measures that, and keeps n-gram when MTP loses.
Pure.
"""
from __future__ import annotations

from typing import Optional, Tuple, cast

from ..memory.gguf import ModelShape
from ..shared.types import Catalog, HfRef
from .drafts import MTP_FILE, draft_of_kind


def matching_drafter(shape: Optional[ModelShape], catalog: Catalog) -> Optional[Tuple[str, HfRef]]:
    """(the catalogue model of the same size, its pinned drafter) for a header; None for
    any model that no catalogue drafter fits, an unknown size, or a catalogue entry without a drafter."""
    if shape is None:
        return None
    size = (str(shape.get("arch", "")), int(shape.get("blocks", 0)), int(shape.get("experts", 0)))
    for entry in catalog.get("models", []):
        fits = entry.get("draft_size") or {}
        draft = draft_of_kind(entry, MTP_FILE)
        if draft and (fits.get("arch"), fits.get("blocks"), fits.get("experts")) == size:
            return (str(entry.get("name")), cast(HfRef, {k: v for k, v in draft.items() if k != "kind"}))
    return None

"""Sorting and filtering of the model lists (the Models panel and the model drop-down).

Pure: the caller passes the models and, for "fits this Mac", a function giving each model's
largest window per slot (None when unknown). Both lists use the same order, so a selected
row always means the same model in the panel and in its actions.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from carl_core.domain.tuning import weighted_score

from .model import ModelInfo, jdict

# (label shown in the header, sort key). Quality = the catalogue rank (1 = best: parameters and
# density first, then quantization). Speed = measured decode speed (speed_of: this Mac's
# Auto-tune, else the catalogue's figure from another Mac), fastest first; models never
# measured come after, MoE before dense, then the smaller file (fewer bytes read per token).
SORTS: Tuple[str, ...] = ("downloaded first", "quality", "speed", "size", "name")
# (label, test). Use-case filters are the catalogue's "good for" tags.
FILTERS: Tuple[str, ...] = ("all", "agent coding", "hard code", "chat & writing", "uncensored", "stock",
                            "dense", "MoE", "downloaded", "fits this Mac")
USE_CASES = ("agent coding", "hard code", "chat & writing", "uncensored")
UNRANKED = 99                     # no rank: a custom model whose card (yet) has none
MIN_FIT = 32768                   # "fits this Mac": at least a 32K window with 1 slot


def _rank(m: ModelInfo) -> int:
    r = m.get("rank")
    return r if isinstance(r, int) and not isinstance(r, bool) else UNRANKED


def _is_moe(m: ModelInfo) -> bool:
    return str(m.get("arch", "")).lower() == "moe"


def speed_of(m: ModelInfo) -> Optional[Tuple[float, bool]]:
    """(tok/s, measured on this Mac) of a model: Auto-tune's score for its chosen mode here,
    else the catalogue's figure (another Mac); None when neither exists. The score is
    Auto-tune's weighted mean of prose, code and re-emit speeds (what it chooses a mode by)."""
    tune = jdict(jdict(m.get("local")).get("tune"))
    st = jdict(tune.get("settings"))
    best = jdict(jdict(jdict(tune.get("results")).get("speculation")).get(f"{st.get('spec')}:{st.get('spec_n')}"))
    if best.get("score") is not None:
        return float(best["score"]), True
    sp = jdict(m.get("speed"))
    try:
        return weighted_score({k: float(sp[k]) for k in ("prose", "code", "edit")}), False
    except (KeyError, TypeError, ValueError):
        return None


def sort_key(sort: str) -> Callable[[ModelInfo], Tuple[object, ...]]:
    """The key function for one of SORTS (the name breaks ties, so the order is stable)."""
    def quality(m: ModelInfo) -> Tuple[object, ...]:
        # with no rank (a custom model whose card has none), the larger file first (more weights: a rough guess)
        r = _rank(m)
        return (r, not _is_moe(m), -m.get("bytes", 0) if r == UNRANKED else 0, m["name"])
    if sort == "speed":
        def speed(m: ModelInfo) -> Tuple[object, ...]:
            s = speed_of(m)
            return (s is None, -(s[0] if s else 0.0), not _is_moe(m), m.get("bytes", 0), m["name"])
        return speed
    if sort == "size":
        return lambda m: (m.get("bytes", 0), m["name"])
    if sort == "name":
        return lambda m: (m["name"],)
    if sort == "downloaded first":
        return lambda m: (m.get("status") != "downloaded", *quality(m))
    return quality


def keep(filt: str, m: ModelInfo, max_ctx: Callable[[ModelInfo], Optional[int]]) -> bool:
    """Does model m pass one of FILTERS?"""
    if filt in USE_CASES:
        return filt in (m.get("good_for") or [])
    if filt == "stock":
        return not m.get("abliterated")
    if filt == "dense":
        return str(m.get("arch", "")).lower() == "dense"
    if filt == "MoE":
        return _is_moe(m)
    if filt == "downloaded":
        return m.get("status") == "downloaded"
    if filt == "fits this Mac":
        mx = max_ctx(m)
        return mx is not None and mx >= MIN_FIT
    return True


def arrange(models: Sequence[ModelInfo], sort: int, filt: int,
            max_ctx: Callable[[ModelInfo], Optional[int]]) -> List[ModelInfo]:
    """The models that pass FILTERS[filt], in SORTS[sort] order (indexes wrap)."""
    s, f = SORTS[sort % len(SORTS)], FILTERS[filt % len(FILTERS)]
    return sorted((m for m in models if keep(f, m, max_ctx)), key=sort_key(s))


def label(sort: int, filt: int) -> Tuple[str, str]:
    """The current sort and filter names, for the list header."""
    return SORTS[sort % len(SORTS)], FILTERS[filt % len(FILTERS)]

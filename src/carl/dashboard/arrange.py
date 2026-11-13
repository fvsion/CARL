"""Sorting and filtering of the model lists (the Models panel and the model drop-down).

Pure: the caller passes the models and, for the filter "fits this Mac" (the platform's word for the computer), a
function giving each model's largest window per slot (None when unknown). Both lists use the same order, so a
selected row always means the same model in the panel and in its actions.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple, overload

from ..domain.server.snapshot import ModelInfo, jdict
from ..ui.words import computer

# (label shown in the header, sort key). Quality = the catalogue rank (1 = best: parameters and
# density first, then quantization). Speed = measured decode speed (speed_of: this Mac's
# Auto-tune, else the catalogue's figure from another Mac), fastest first; models never
# measured come after, MoE before dense, then the smaller file (fewer bytes read per token).
SORTS: Tuple[str, ...] = ("downloaded first", "quality", "speed", "size", "name")


def fits_label() -> str:
    """The last filter's name: fits this Mac (the computer is the platform's word)."""
    return f"fits {computer()}"


class _Filters(Sequence[str]):
    """FILTERS: the filters' names, the use-case filters (the catalogue's "good for" tags) among them. The last one
    names the computer, so the names are read from the platform's words when they are used, not at import."""
    FIXED: Tuple[str, ...] = ("all", "agent coding", "hard code", "chat & writing", "uncensored", "stock", "dense",
                              "MoE", "downloaded")

    def __len__(self) -> int:
        return len(self.FIXED) + 1

    @overload
    def __getitem__(self, i: int) -> str: ...

    @overload
    def __getitem__(self, i: slice) -> Tuple[str, ...]: ...

    def __getitem__(self, i: "int | slice") -> "str | Tuple[str, ...]":
        return (*self.FIXED, fits_label())[i]


FILTERS = _Filters()
USE_CASES = ("agent coding", "hard code", "chat & writing", "uncensored")
UNRANKED = 99                     # no rank: a custom model whose card (yet) has none
MIN_FIT = 49152                   # "fits this Mac" (fits_label): at least a 48K window with 1 slot (Auto fit's floor)


def downloaded(m: ModelInfo) -> bool:
    """The model is downloaded: the file shown, or (a model with several quantizations) any of them."""
    return m.get("status") == "downloaded" or any(q.get("status") == "downloaded" for q in m.get("quants") or [])


def _rank(m: ModelInfo) -> int:
    r = m.get("rank")
    return r if isinstance(r, int) and not isinstance(r, bool) else UNRANKED


def _is_moe(m: ModelInfo) -> bool:
    return str(m.get("arch", "")).lower() == "moe"


def speed_of(m: ModelInfo) -> Optional[Tuple[float, bool]]:
    """(prose tok/s, measured on this Mac) of a model: Auto-tune's prose speed for its chosen mode here, else
    the catalogue's (another Mac); None when neither exists. The lists show and sort by this one number (Phase
    23.2: the weighted score did not match the prose / code / edit figures shown beside it)."""
    tune = jdict(jdict(m.get("local")).get("tune"))
    st = jdict(tune.get("settings"))
    best = jdict(jdict(jdict(tune.get("results")).get("speculation")).get(f"{st.get('spec')}:{st.get('spec_n')}"))
    if best.get("prose") is not None:
        return float(best["prose"]), True
    sp = jdict(m.get("speed"))
    try:
        return float(sp["prose"]), False
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
        return lambda m: (not downloaded(m), *quality(m))
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
        return downloaded(m)
    if filt == fits_label():
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

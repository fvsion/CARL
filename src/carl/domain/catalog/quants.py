"""One entry per model, its quantizations beside it (decision A68), each marked with the MTP it carries (decision A73).

A model is what the layout calls a model (layout.py: the model folder, made from the file name): Qwen3.6-35B-A3B in
UD-Q4_K_M and in UD-IQ3_XXS is one model with two quantizations, though the catalogue lists a file each (an entry is
one file, and that stays the unit of a download, a tune and a start). The lists a person reads (the dashboard's
model picker and Models panel, the model card, `carl models`) show a model once, with a quantization selector. A
start names a model and, when it has several, a quantization: `--model NAME --quant Q`, in one word NAME:Q.

A quantization carries an MTP head or it does not (mtp_kind), and the quantizations of one model can differ: the
heretic's Q4_K_M has its head (41 blocks, nextn_predict_layers 1, like the stock model) and its i1-IQ3_XXS has none (40
blocks), and they are one model (the catalogue's model field). The mark is what the file's header says (the application
sets ModelInfo.head from it), else the catalogue's word. Speculation follows the quantization that is selected: MTP and
n-gram where the head is, n-gram where it is not.

A start on the CPU alone has its own order (decision A72, measured on a Ryzen 9 3900X: the Qwen3.6-35B-A3B in IQ4_NL
read an 8K prompt at 87 tok/s and wrote at 12.9, in Q4_K_M at 70 and 10.3; the llama.cpp CPU kernels run the legacy
4-bit types faster): IQ4_NL and Q4_0 come before the K-quants of the same bit class (cpu_ladder), where the catalogue
or the model folder holds both.

Pure: the order of the choice is here, what a quantization fits is given by the caller.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple, cast

from ..shared.errors import ConfigError
from .drafts import MTP_FILE, draft_of_kind
from .hf import local_file_name, model_name
from .layout import FALLBACK_QUANT, names_model, split_file
from ..shared.paths import policy_for
from ..shared.types import ModelInfo, QuantInfo

SPEC_SEP = ":"
_PREFIX = re.compile(r"^(?:ud|i1)-", re.IGNORECASE)             # UD-IQ3_XXS and IQ3_XXS name one quantization
CPU_FAST = ("IQ4_NL", "Q4_0")                                   # the types the CPU kernels run fastest at about 4 bits
_BITS = re.compile(r"^i?q(\d)", re.IGNORECASE)


@dataclass(frozen=True)
class ModelGroup:
    """One model: its name, its key (the model folder, lower case) and its entries, the largest first. The name is the
    first catalogue entry's, a name a start already takes; a model of files only (custom or scanned) is named by its
    folder, so that no entry's own name (foo-q4_k_m) is also the name of the model."""
    name: str
    key: str
    entries: Tuple[ModelInfo, ...]

    @property
    def several(self) -> bool:
        return len(self.entries) > 1


def file_name_of(m: ModelInfo) -> str:
    """The file name of an entry: its file's, else the catalogue's."""
    path = m.get("path", "")
    if path:
        return policy_for(path).basename(path)
    try:
        return local_file_name((m.get("hf") or {}).get("file", ""))
    except ConfigError:                                   # an entry without a file (a made-up one)
        return m.get("name", "")


def model_key(m: ModelInfo) -> str:
    """The model an entry is of: the model the catalogue names for it (model: two makers' files of one model, the
    heretic's Q4_K_M and i1-IQ3_XXS, are one), else the name of its model folder when the file is in one
    (gguf/MODEL/QUANT/FILE or gguf/MODEL/FILE, whatever prefix the file name has), else the model that the file name
    gives (layout.split_file)."""
    named = m.get("model")
    if named:
        return named.lower()
    name, quant = split_file(file_name_of(m))
    path = m.get("path", "")
    if path:
        pp = policy_for(path)
        parent = pp.dirname(path)
        mdir = pp.basename(pp.dirname(parent))
        if mdir and pp.basename(parent).lower() == quant.lower() and quant != FALLBACK_QUANT:
            return mdir.lower()
        if names_model(pp.basename(parent), name):
            return pp.basename(parent).lower()
    return name.lower()


def quant_of(m: ModelInfo) -> str:
    """The quantization of an entry as the layout names it (UD-IQ3_XXS); default when the file name has none."""
    return split_file(file_name_of(m))[1]


def group_models(models: Sequence[ModelInfo]) -> List[ModelGroup]:
    """The models of a list: the entries that share a model folder are one, in the order their first entry comes."""
    order: List[str] = []
    members: Dict[str, List[ModelInfo]] = {}
    for m in models:
        key = model_key(m)
        if key not in members:
            order.append(key)
        members.setdefault(key, []).append(m)
    out: List[ModelGroup] = []
    for k in order:
        entries = tuple(sorted(members[k], key=lambda m: (-int(m.get("bytes", 0)), m.get("name", ""))))
        listed = next((m for m in members[k] if not m.get("custom")), None)         # in the order of the list
        name = listed.get("name", "") if listed is not None else (
            entries[0].get("name", "") if len(entries) == 1 else model_name(k))
        out.append(ModelGroup(name, k, entries))
    return out


def mtp_kind(m: ModelInfo) -> str:
    """The MTP an entry carries: "head" (in the file), "drafter" (a file of its own, -md) or "" (none). The head is the
    header's word (ModelInfo.head, set from the file or its cached header), else the catalogue's: mtp without a drafter."""
    head = m.get("head")
    if head is None:
        head = bool(m.get("mtp")) and not draft_of_kind(m, MTP_FILE)
    return "head" if head else "drafter" if draft_of_kind(m, MTP_FILE) else ""


def mtp_mark(kind: str, long: bool = False) -> str:
    """The word a list shows for mtp_kind: MTP (the head is in the file), drafter (long: MTP drafter), else nothing."""
    return "MTP" if kind == "head" else ("MTP drafter" if long else "drafter") if kind == "drafter" else ""


def quant_infos(group: ModelGroup) -> List[QuantInfo]:
    return [{"name": m.get("name", ""), "quant": quant_of(m), "bytes": int(m.get("bytes", 0)),
             "status": m.get("status", "missing"), "mtp": mtp_kind(m)} for m in group.entries]


def row_of(group: ModelGroup, shown: ModelInfo) -> ModelInfo:
    """The entry that stands for the model in a list: the shown quantization's entry, with the model's name (group)
    and every quantization (quants). Its name is still the entry's, so each action of a panel acts on that file."""
    row = cast(ModelInfo, dict(shown))
    row["group"] = group.name
    row["quants"] = quant_infos(group)
    return row


def matches(group: ModelGroup, text: str) -> List[ModelInfo]:
    """The entries a quantization name points at: the quantization (UD-IQ3_XXS, with or without UD- and i1-) or the
    entry's name, whole; else the entries whose quantization starts with it (iq3, q4). Case does not matter."""
    want = text.strip().lower()
    if not want:
        return []
    whole = [m for m in group.entries if want in (quant_of(m).lower(), _PREFIX.sub("", quant_of(m)).lower(),
                                                  m.get("name", "").lower())]
    return whole or [m for m in group.entries if _PREFIX.sub("", quant_of(m)).lower().startswith(want)]


def tune_order(group: ModelGroup) -> List[ModelInfo]:
    """The entries with a tune record (Auto-tune ran on this computer), the latest first."""
    tuned = [m for m in group.entries if ((m.get("local") or {}).get("tune") or {}).get("settings")]
    return sorted(tuned, key=lambda m: str(((m.get("local") or {}).get("tune") or {}).get("date", "")), reverse=True)


def _plain(quant: str) -> str:
    return _PREFIX.sub("", quant).upper()


def cpu_fast(quant: str) -> bool:
    """A quantization the CPU runs faster than a K-quant of its size: IQ4_NL or Q4_0 (UD- and i1- do not matter)."""
    return _plain(quant) in CPU_FAST


def _k_quant(quant: str) -> bool:
    return "_K" in _plain(quant)


def _bits(quant: str) -> int:
    m = _BITS.match(_plain(quant))
    return int(m.group(1)) if m else 0


def cpu_over(m: ModelInfo, entries: Sequence[ModelInfo]) -> List[ModelInfo]:
    """The K-quants of m's bit class among entries, when m is IQ4_NL or Q4_0: what m comes before on the CPU ([] else)."""
    if not cpu_fast(quant_of(m)):
        return []
    return [e for e in entries if e.get("name") != m.get("name") and _k_quant(quant_of(e))
            and _bits(quant_of(e)) == _bits(quant_of(m))]


def cpu_ladder(entries: Sequence[ModelInfo]) -> List[ModelInfo]:
    """The entries of one model (largest first) in the order of a start on the CPU alone: each IQ4_NL or Q4_0 moves
    before the first K-quant of its bit class (Q4_K_M, Q4_K_XL), the rest keeps its order. Without such a pair the
    order is the one given."""
    place: Dict[int, float] = {id(m): float(i) for i, m in enumerate(entries)}
    for m in entries:
        over = cpu_over(m, entries)
        if over:
            place[id(m)] = min(place[id(m)], min(place[id(e)] for e in over) - 0.5)
    return sorted(entries, key=lambda m: place[id(m)])


def cpu_quant_note(m: ModelInfo, entries: Sequence[ModelInfo]) -> str:
    """Why m comes first on the CPU, when it is an IQ4_NL or Q4_0 and entries (the model's other quantizations) hold a
    K-quant of its bit class ("" else): the sentence of the pick line."""
    over = cpu_over(m, entries)
    if not over:
        return ""
    k = over[0]
    return (f"On the CPU {_PREFIX.sub('', quant_of(m))} comes before {_PREFIX.sub('', quant_of(k))}: it reads and writes "
            f"faster at about the same size")


def default_quant(group: ModelGroup, chosen: Sequence[str] = (),
                  fits: Callable[[ModelInfo], bool] = lambda m: True, cpu: bool = False) -> ModelInfo:
    """The quantization a start takes when none is named. Among the downloaded entries: the one a person last chose
    (chosen: entry names, the first that is downloaded wins, for example config.json's llama.model), else the one
    with the latest Auto-tune record, else the largest that fits (fits: the fit check against the context ladder),
    else the largest. No entry downloaded: the first of the catalogue (the entry the model is named for). cpu: a start
    on the CPU alone, whose order puts IQ4_NL and Q4_0 before the K-quants of their size (cpu_ladder)."""
    ready = [m for m in group.entries if m.get("status") == "downloaded"]
    if not ready:
        return next((m for m in group.entries if m.get("name") == group.name), group.entries[0])
    if len(ready) == 1:
        return ready[0]
    for name in chosen:
        hit = next((m for m in ready if m.get("name") == name), None)
        if hit is not None:
            return hit
    tuned = [m for m in tune_order(group) if m in ready]
    if tuned:
        return tuned[0]
    if cpu:
        ready = cpu_ladder(ready)
    return next((m for m in ready if fits(m)), ready[0])         # ready is the largest first


def join_spec(name: str, quant: str) -> str:
    """NAME:QUANT, the one word of a model and its quantization (a name alone when quant is empty)."""
    return f"{name}{SPEC_SEP}{quant}" if quant else name


def split_spec(spec: str) -> Tuple[str, str]:
    """(name, quant) of NAME:QUANT; a path (a separator, or .gguf) is not split."""
    if "/" in spec or "\\" in spec or spec.lower().endswith(".gguf") or SPEC_SEP not in spec:
        return spec, ""
    name, _, quant = spec.rpartition(SPEC_SEP)
    return (name, quant) if name and quant else (spec, "")


def quant_names(group: ModelGroup) -> str:
    """The quantizations of a model for a message: UD-Q4_K_M, UD-IQ3_XXS."""
    return ", ".join(quant_of(m) for m in group.entries)

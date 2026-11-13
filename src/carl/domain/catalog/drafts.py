"""The drafters and the vision projector of a catalogue model (WCATX, decision A74).

A catalogue entry names its drafters in `drafts`, a list in which each drafter has its kind: mtp-file (an MTP drafter in
a file of its own: Gemma 4's), dflash, dspark (DeepSeek's DSpark: DFlash with a Markov head) and eagle3. Each kind has a
folder in the model's folder (layout.DRAFTER_KINDS) and a llama-server --spec-type (SPEC_TYPE); llama-server loads the
file with -md. The older single `draft` of an entry still reads: it is an mtp-file drafter. `vision` names the vision
projector (mmproj*.gguf, llama-server --mmproj). One source: everything else asks drafts_of and vision_of.
Pure.
"""
from __future__ import annotations

from typing import List, Mapping, Optional, Tuple, cast

from .layout import DRAFTER_KINDS
from ..shared.types import DraftInfo, HfRef

MTP_FILE = "mtp-file"
KINDS = tuple(DRAFTER_KINDS)                                    # mtp-file, dflash, dspark, eagle3
SPEC_TYPE = {MTP_FILE: "draft-mtp", "dflash": "draft-dflash", "dspark": "draft-dspark", "eagle3": "draft-eagle3"}
KIND_OF_SPEC = {spec: kind for kind, spec in SPEC_TYPE.items()}
KIND_WORDS = {MTP_FILE: "MTP drafter", "dflash": "DFlash drafter", "dspark": "DSpark drafter",
              "eagle3": "EAGLE-3 drafter"}
# the --spec-type values whose drafter is a file other than MTP's: a start passes the file with -md (one at a time)
FILE_SPECS = tuple(SPEC_TYPE[k] for k in KINDS if k != MTP_FILE)
# the guesses (--spec-draft-n-max) Auto-tune tries for each of them: DFlash and DSpark draft a block (trained size 16),
# EAGLE-3 a short chain (llama.cpp's own default is 3). Starting points, measured by the tune.
TUNE_NS = {"draft-dflash": (4, 8), "draft-dspark": (4, 8), "draft-eagle3": (2, 4)}
VISION_WORDS = "vision projector"


def drafts_of(entry: Mapping[str, object]) -> List[DraftInfo]:
    """The drafters of a catalogue entry (or a custom one), each with its kind: its `drafts`, else its older single
    `draft` as an mtp-file drafter. [] for an entry without any. The entry's order is kept."""
    listed = entry.get("drafts")
    if isinstance(listed, list):
        return [cast(DraftInfo, d) for d in listed if isinstance(d, dict)]
    old = entry.get("draft")
    return [cast(DraftInfo, {**cast(HfRef, old), "kind": MTP_FILE})] if isinstance(old, dict) and old else []


def draft_of_kind(entry: Mapping[str, object], kind: str) -> Optional[DraftInfo]:
    """The entry's drafter of this kind (None: it has none)."""
    return next((d for d in drafts_of(entry) if d.get("kind") == kind), None)


def vision_of(entry: Mapping[str, object]) -> Optional[HfRef]:
    """The entry's vision projector (None: it has none)."""
    v = entry.get("vision")
    return cast(HfRef, v) if isinstance(v, dict) and v else None


def spec_kinds(spec: str) -> List[str]:
    """The kinds of drafter file a --spec-type value uses, MTP's excluded (it has its own source: the head, or the
    drafter of inventory.mtp_source): "draft-dflash,ngram-mod" is [dflash]."""
    return [KIND_OF_SPEC[s] for s in spec.split(",") if s in FILE_SPECS]


def without_file_kinds(spec: str) -> Tuple[str, List[str]]:
    """(the speculation with the drafter-file kinds taken out, the kinds taken out): what a start falls back to when
    a drafter is not there. Nothing left is none; n-gram stays."""
    parts = spec.split(",")
    kept = [s for s in parts if s not in FILE_SPECS]
    return (",".join(kept) or "none"), [KIND_OF_SPEC[s] for s in parts if s in FILE_SPECS]

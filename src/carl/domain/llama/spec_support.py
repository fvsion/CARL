"""The speculative kinds a llama.cpp build supports (WCATX, decision A74). Pure.

Read from the source of llama.cpp b11738 (the pinned release) and b11429 (the Macs' tested build) on 2026-11-10:
common/speculative.cpp names the --spec-type values draft-simple, draft-eagle3, draft-mtp, draft-dflash, draft-dspark and
ngram-simple, ngram-map-k, ngram-map-k4v, ngram-mod, ngram-cache; common/arg.cpp's --spec-draft-model (-md,
--model-draft) loads the drafter file of every draft kind, and --spec-draft-n-max is the length of every one of them
(DFlash and DSpark clamp it to the block size their file was trained with). A DSpark file is told from a DFlash file
by its Markov head, and llama.cpp reads the kind from the file when --spec-type is not given. Both builds name all four.
Builds before b11429 were not read: a kind is offered only to an engine whose build is known and not older than that.
When a pin moves, read common/speculative.cpp of the new tag again and change FROM_BUILD here only.
"""
from __future__ import annotations

from typing import Dict, Optional

from .version import LlamaVersion

# the first build read that names the kind; a kind that is not here is not one CARL starts
FROM_BUILD: Dict[str, int] = {"draft-mtp": 11429, "draft-dflash": 11429, "draft-dspark": 11429, "draft-eagle3": 11429}


def supports(spec_type: str, engine: Optional[LlamaVersion]) -> bool:
    """llama.cpp of this version supports the --spec-type value: its build number is known and is not older than the
    first build read to have the kind. An engine whose version is unknown, or older, is offered none."""
    need = FROM_BUILD.get(spec_type)
    return need is not None and engine is not None and engine.build is not None and engine.build >= need


def unused_note(name: str, words: str, engine: Optional[LlamaVersion]) -> str:
    """A sentence for a drafter that is cataloged or downloaded and not used, because the engine does not support its
    kind: for the tune's report and the models list."""
    return (f"The {words} of {name} is not used: llama.cpp {engine.text() if engine else 'with an unknown version'} "
            "does not support this kind of speculation. It stays in the models folder for a newer llama.cpp.")

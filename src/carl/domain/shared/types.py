"""Typed shapes of CARL's data.

The catalogue, models.json and the model records the monitor reads are JSON objects, and
the public API (application/api.py) hands them out as plain dicts. They are modelled as
TypedDicts so the code is type checked while the data stays dict-compatible.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple, TypedDict, Union

JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]
JsonObject = Dict[str, JsonValue]

# One setting value after validation (config.json, catalogue tune, Auto-tune result).
SettingValue = Union[str, int, float, bool, List[str]]
Settings = Dict[str, SettingValue]

Status = Literal["downloaded", "partial", "missing"]
# Where an effective setting came from (shown by the monitor and in CARL_SOURCES).
SettingSource = Literal["default", "catalogue", "header", "auto-tune", "config", "auto-fit"]
Zone = Literal["good", "slow", "very_slow"]


class HfRef(TypedDict, total=False):
    """A pinned Hugging Face file."""
    repo: str
    revision: str
    file: str
    sha256: str
    bytes: int


class DraftInfo(HfRef, total=False):
    """A drafter of a catalogue model (the entry's drafts list): the pinned file and its kind; in the models list also
    where it is (path, status) and the --spec-type of its kind (spec)."""
    kind: str                   # mtp-file | dflash | dspark | eagle3 (layout.DRAFTER_KINDS)
    path: str
    status: Status
    spec: str                   # draft-mtp | draft-dflash | draft-dspark | draft-eagle3 (drafts.SPEC_TYPE)


class CtxZones(TypedDict):
    """Context per slot: <= good is fast, <= slow is usable, above very_slow is very slow."""
    good: int
    slow: int
    very_slow: int


class ModeResult(TypedDict):
    """Decode speed (tok/s) of one speculation mode and its weighted score."""
    prose: float
    code: float
    edit: float
    score: float


class KvResult(TypedDict):
    """One context memory type measured on the CPU alone: the prompt read and the write speed (tok/s)."""
    read: float
    write: float


class _TuneResultsBase(TypedDict):
    speculation: Dict[str, ModeResult]
    prompt_read: List[List[float]]


class TuneResults(_TuneResultsBase, total=False):
    decode_at_depth: List[List[float]]      # long mode: (tokens read, decode tok/s after them)
    parallel: List[List[float]]             # (requests at once, decode tok/s in total, prompt tok/s in total)
    ub: List[List[float]]                   # off a Mac: (-ub, prompt tok/s at that batch size)
    speculation_ram: Dict[str, ModeResult]  # a model with an MTP head and experts in the RAM: n-gram and MTP at the
    #                                         setup the tune chose (A51)
    threads_ram: Dict[str, ModeResult]      # experts in the RAM: the threads pinned and unpinned at the setup (A54)
    kv: Dict[str, "KvResult"]               # the CPU alone (A71): each context memory type that fit, by its name (f16)
    prompt_read_estimate: List[List[float]]  # the CPU reads up to 8K tokens (A71): (tokens, tok/s) estimated from the slope


class TuneRecord(TypedDict, total=False):
    """An Auto-tune result for one model on this Mac (models.json models.NAME.tune)."""
    date: str
    machine: str
    llama_cpp: str
    backend: str                # the backend it was measured on: cpu, cuda-13, vulkan, metal ... (A72; "": not kept)
    settings: Settings
    ctx_zones: CtxZones
    results: TuneResults
    max_ctx: Dict[str, int]
    depth: str                  # quick | default | long
    ub: int                     # the batch size (-ub) chosen for this model (A45; a record without it: the default)
    ub_note: str                # why, in one line (scoring.ub_words)
    spec_note: str              # why the speculation, with experts in the RAM, in one line (scoring.ram_spec_words)
    load_mode: str              # the load mode the tune measured with: none | mapped ("" not known; A44)
    pin: str                    # experts in the RAM: pinned | unpinned, what the start follows (A54; "": unpinned, A57)
    pin_note: str               # why, in one line (scoring.pin_words)
    kv_note: str                # the CPU alone: the context memory types measured and the one kept (scoring.kv_words)
    read_note: str              # the CPU alone: the prompt reads stop at 8K tokens, longer ones are an estimate


class LocalEntry(TypedDict, total=False):
    """models.json models.NAME: a custom model and/or this Mac's Auto-tune result."""
    path: str
    source: str
    hf: HfRef
    draft: HfRef            # a custom Gemma 4 model's MTP drafter (domain/drafters.py)
    label: str
    alias: str
    summary: str
    description: str
    tune: TuneRecord
    verified: str
    added: str
    card: "CustomCard"


class LocalDb(TypedDict):
    schema: int
    models: Dict[str, LocalEntry]


class PickInstead(TypedDict):
    """A nearby alternative on a model card: which model, and when it is the better pick."""
    model: str
    when: str


class CustomCard(TypedDict, total=False):
    """The user's card for a custom model (models.json models.NAME.card): the catalogue's card
    fields plus what CARL can't know about a file it did not ship. thinking: "on-off" (thinking
    on or off only) or "effort" (effort levels); auto_fit: the user lets auto fit pick it (it
    needs a rank, an arch and a stock model)."""
    label: str
    role: str
    good_for: List[str]
    why_use: str
    trade_offs: str
    hardware: str
    uncensored: str
    abliterated: bool
    arch: str
    quant: str
    rank: int
    pick_instead: List[PickInstead]
    thinking: str
    auto_fit: bool
    engine: str


class SpeedRecord(TypedDict, total=False):
    """A catalogue model's measured decode speed (tok/s) on a named Mac, in its tuned mode."""
    prose: float
    code: float
    edit: float
    machine: str
    date: str
    mode: str


class CatalogEntry(TypedDict, total=False):
    """One src/carl/data/catalog.json model. The card fields (role, good_for, why_use, trade_offs,
    pick_instead, hardware, uncensored, rank) tell the user what the model is for."""
    name: str
    label: str
    family: str
    thinking: str
    speed: SpeedRecord
    summary: str
    description: str
    arch: str
    quant: str
    mtp: bool                   # MTP speculation available: a head in the model file, or the drafter
    model: str                  # the model this entry is a quantization of, when its file name does not say (quants.py)
    fast: bool                  # a small dense model auto fit's everyday goal takes with the MoE builds
    abliterated: bool
    params: str
    min_ram_gb: int
    tested: bool
    hf: HfRef
    draft: HfRef                # a separate MTP drafter (Gemma 4: mtp-*.gguf, llama-server -md); older than drafts
    drafts: List[DraftInfo]     # the drafters, each with its kind (mtp-file, dflash, dspark, eagle3); one source (drafts.py)
    vision: HfRef               # the vision projector (mmproj*.gguf, llama-server --mmproj)
    draft_size: Dict[str, Union[str, int]]   # the header of the size it fits: arch, blocks, experts (drafters.py)
    tune: Settings
    why: Dict[str, str]
    ctx_zones: Optional[CtxZones]
    measured: List[Dict[str, str]]
    role: str
    good_for: List[str]
    why_use: str
    trade_offs: str
    pick_instead: List[PickInstead]
    hardware: str
    uncensored: str
    rank: int
    engine: str                 # the engine that serves it (domain/server/engines.py); absent: llama.cpp


class Catalog(TypedDict, total=False):
    schema: int
    default: str
    default_small: str
    models: List[CatalogEntry]


class QuantInfo(TypedDict):
    """One quantization of a model, as a list of the model's quantizations holds it (domain/catalog/quants.py)."""
    name: str               # the entry's name (what a start or an action takes)
    quant: str              # UD-IQ3_XXS
    bytes: int
    status: Status
    mtp: str                # head | drafter | "" (quants.mtp_kind): the mark of the quantization (A73)


class ModelInfo(CatalogEntry, total=False):
    """A model CARL knows (catalogue, custom download or a file in the models folder), as
    all_models() returns it: the catalogue fields plus where it is and its status."""
    source: str
    path: str
    bytes: int
    status: Status
    local: LocalEntry
    custom: bool
    thinking: str           # custom models: from the user's card
    auto_fit: bool
    draft_path: str         # the drafter's file (a catalogue model with "draft")
    draft_status: Status
    vision_path: str        # the projector's file (a catalogue model with "vision")
    vision_status: Status
    draft_offer: HfRef      # a custom Gemma 4 model: the catalogue drafter of its size, not recorded yet
    draft_for: str          # the catalogue model whose drafter that is
    head: bool              # the file carries an MTP head: its header's word when the file (or its cached header) is read
    #                         (application/inventory.py), else the catalogue's (quants.mtp_kind; A73)
    group: str              # a list's row: the name of the model the entry is a quantization of (quants.py)
    quants: List[QuantInfo]  # ...and every quantization of it, the largest first


class CustomInfo(TypedDict, total=False):
    """What a GGUF header says about a model that is not in the catalogue."""
    arch: str
    mtp: bool
    quant: str
    ctx_train: int
    thinking: str           # on-off | effort (the chat template's)


# [(file, bytes, sha256)] of a Hugging Face repo's GGUF files.
HfFileList = List[Tuple[str, int, str]]

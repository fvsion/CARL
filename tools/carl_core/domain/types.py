"""Typed shapes of CARL's data.

The catalogue, models.json and the model records the monitor reads are JSON objects, and
the public API (tools/carl.py) hands them out as plain dicts. They are modelled as
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
SettingSource = Literal["default", "catalogue", "header", "auto-tune", "config"]
Zone = Literal["good", "slow", "very_slow"]


class HfRef(TypedDict, total=False):
    """A pinned Hugging Face file."""
    repo: str
    revision: str
    file: str
    sha256: str
    bytes: int


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


class TuneResults(TypedDict):
    speculation: Dict[str, ModeResult]
    prompt_read: List[List[float]]


class TuneRecord(TypedDict, total=False):
    """An Auto-tune result for one model on this Mac (models.json models.NAME.tune)."""
    date: str
    machine: str
    llama_cpp: str
    settings: Settings
    ctx_zones: CtxZones
    results: TuneResults
    max_ctx: Dict[str, int]


class LocalEntry(TypedDict, total=False):
    """models.json models.NAME: a custom model and/or this Mac's Auto-tune result."""
    path: str
    source: str
    hf: HfRef
    label: str
    alias: str
    summary: str
    description: str
    tune: TuneRecord
    verified: str
    added: str


class LocalDb(TypedDict):
    schema: int
    models: Dict[str, LocalEntry]


class PickInstead(TypedDict):
    """A nearby alternative on a model card: which model, and when it is the better pick."""
    model: str
    when: str


class CatalogEntry(TypedDict, total=False):
    """One host/catalog.json model. The card fields (role, good_for, why_use, trade_offs,
    pick_instead, hardware, uncensored, rank) tell the user what the model is for."""
    name: str
    label: str
    family: str
    alias: str
    summary: str
    description: str
    arch: str
    quant: str
    mtp: bool
    abliterated: bool
    params: str
    min_ram_gb: int
    tested: bool
    hf: HfRef
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


class Catalog(TypedDict, total=False):
    schema: int
    default: str
    default_small: str
    models: List[CatalogEntry]


class ModelInfo(CatalogEntry, total=False):
    """A model CARL knows (catalogue, custom download or a file in the models folder), as
    all_models() returns it: the catalogue fields plus where it is and its status."""
    source: str
    path: str
    bytes: int
    status: Status
    local: LocalEntry
    custom: bool


class CustomInfo(TypedDict, total=False):
    """What a GGUF header says about a model that is not in the catalogue."""
    arch: str
    mtp: bool
    quant: str
    ctx_train: int


# [(file, bytes, sha256)] of a Hugging Face repo's GGUF files.
HfFileList = List[Tuple[str, int, str]]

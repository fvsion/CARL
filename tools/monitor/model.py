"""Data the dashboard collects and shows, as types. Pure: no I/O.

Server JSON (/slots, /props) and carl.py's model records are
untrusted dicts: read them with jdict / jlist / jnum / jint, which fall back to an
empty or default value when a field has an unexpected type."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Union

from carl_core.domain.gguf import ModelShape

JSONDict = Dict[str, Any]
TaskId = Union[int, str, None]      # the slot's task number


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    """text without control characters (newlines and tabs stay): text from the server, its
    log or the network can't move the cursor or send escape sequences to the terminal."""
    return _CONTROL.sub("", text)


def clean_json(v: object) -> object:
    """Parsed JSON with every string (keys too) cleaned."""
    if isinstance(v, str):
        return clean(v)
    if isinstance(v, list):
        return [clean_json(x) for x in v]
    if isinstance(v, dict):
        return {clean(k) if isinstance(k, str) else k: clean_json(x) for k, x in v.items()}
    return v


def jdict(v: object) -> JSONDict:
    """v if it is a JSON object, else {}."""
    return v if isinstance(v, dict) else {}


def jlist(v: object) -> List[Any]:
    """v if it is a JSON array, else []."""
    return v if isinstance(v, list) else []


def jnum(v: object) -> Optional[float]:
    """v if it is a JSON number (not a bool), else None."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def jint(v: object, default: int = 0) -> int:
    """v as an int if it is a JSON number, else default."""
    n = jnum(v)
    return int(n) if n is not None else default


def jtask(v: object) -> TaskId:
    """A task / request id: an int or a string, else None."""
    return v if isinstance(v, (int, str)) and not isinstance(v, bool) else None


# Long names of the short flags looked up: a router's model servers get the long forms
# (the router writes its presets' keys as --long-name).
FLAG_SYNONYMS = {"-ub": "--ubatch-size", "-b": "--batch-size", "-fa": "--flash-attn", "--temp": "--temperature",
                 "-ngl": "--n-gpu-layers", "-sps": "--slot-prompt-similarity", "-md": "--spec-draft-model"}


def flag(cmd: str, *names: str, default: Optional[str] = None) -> Optional[str]:
    """The value of the first of names given on a command line ("-c 4096" or "-c=4096"); a
    short name also matches its long form (FLAG_SYNONYMS)."""
    names = tuple(x for n in names for x in (n, FLAG_SYNONYMS.get(n)) if x)
    for n in names:
        m = re.search(r"(?:^|\s)" + re.escape(n) + r"[ =](\S+)", cmd)
        if m:
            return m.group(1)
    return default


def flag_int(cmd: str, *names: str, default: int) -> int:
    """A flag's value as an int; default when it is missing, empty or not a number."""
    v = flag(cmd, *names)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


Shape = ModelShape        # a GGUF model's memory shape (carl_core.domain.gguf)


class ModelInfo(TypedDict, total=False):
    """One model as tools/carl.py all_models() describes it (catalogue, models folder or custom)."""
    name: str
    label: str
    source: str
    path: str
    bytes: int
    status: str             # downloaded | partial | missing
    summary: str
    description: str
    arch: str
    quant: str
    mtp: bool
    abliterated: bool
    custom: bool
    alias: str
    hf: JSONDict            # repo, file, revision, bytes
    draft: JSONDict         # a separate MTP drafter (Gemma 4): repo, file, revision, bytes
    draft_path: str
    draft_status: str       # downloaded | partial | missing
    local: JSONDict         # this Mac's record: tune (date, machine, settings, results, ctx_zones), ...
    tune: JSONDict
    why: JSONDict
    measured: List[JSONDict]
    speed: JSONDict         # the catalogue's tok/s (prose, code, edit) on another Mac
    # the model card (host/catalog.json): what the model is for and why
    role: str
    good_for: List[str]
    why_use: str
    trade_offs: str
    pick_instead: List[JSONDict]   # model, when
    hardware: str
    uncensored: str
    rank: int
    # a custom model's card from models.json (the user's), joined in by carl.py
    thinking: str           # on-off | effort
    auto_fit: bool          # auto fit may pick it


def draft_bytes(m: ModelInfo) -> int:
    """The weights of a model's MTP drafter (0 without one): the dashboard counts them with the model's
    wherever it checks a fit, as auto fit does."""
    return jint(jdict(m.get("draft")).get("bytes"))


def drafter_missing(m: ModelInfo) -> bool:
    """The model has a separate MTP drafter that is not downloaded (a start uses n-gram until it is)."""
    return bool(m.get("draft")) and m.get("draft_status") != "downloaded"


@dataclass
class SlotInfo:
    """One llama.cpp slot from /slots."""
    id: Optional[int]
    busy: bool
    task: TaskId
    n_ctx: int
    prompt: int
    cached: int
    processed: int
    decoded: int

    @classmethod
    def from_json(cls, x: object) -> "SlotInfo":
        s = jdict(x)
        nt = jdict((jlist(s.get("next_token")) or [{}])[0])
        sid = s.get("id")
        return cls(id=sid if isinstance(sid, int) and not isinstance(sid, bool) else None,
                   busy=bool(s.get("is_processing", False)), task=jtask(s.get("id_task")),
                   n_ctx=jint(s.get("n_ctx")), prompt=jint(s.get("n_prompt_tokens")),
                   cached=jint(s.get("n_prompt_tokens_cache")), processed=jint(s.get("n_prompt_tokens_processed")),
                   decoded=jint(nt.get("n_decoded")))


@dataclass
class ProcInfo:
    """The server process as ps shows it."""
    rss: int        # bytes
    cpu: float      # percent
    etime: str      # [[dd-]hh:]mm:ss
    cmd: str


@dataclass
class SystemStats:
    """This Mac's memory, swap, GPU and power."""
    wired: int = 0
    active: int = 0
    comp: int = 0
    free: int = 0
    pressure: str = "?"
    swap: Tuple[float, float] = (0.0, 0.0)        # (used, total) bytes
    gpu: Optional[int] = None                     # utilisation, percent
    gpumem: Optional[int] = None                  # bytes mapped by all apps
    power: str = ""
    load: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def used(self) -> int:
        return self.wired + self.active + self.comp


SleepEvent = Tuple[str, str, str]   # (HH:MM:SS, Sleep|Wake|DarkWake, reason)


@dataclass
class SlowStats:
    """Slow-changing, expensive stats, refreshed once a minute in the background."""
    thermal: Optional[str] = None
    sleep_events: List[SleepEvent] = field(default_factory=list)
    disk: Optional[Tuple[int, int]] = None        # (free, total) bytes of ~/models


@dataclass
class RouterModel:
    """One model a llama.cpp router offers (/models) and its state."""
    id: str
    status: str             # unloaded | loading | loaded | sleeping | downloading
    failed: bool = False    # its last load failed (exit_code in /models)
    args: List[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status in ("loading", "loaded", "sleeping")


@dataclass
class RouterInfo:
    """A router (llama.mode = router): its models and the one loaded (or loading) now."""
    models: List[RouterModel] = field(default_factory=list)

    @property
    def current(self) -> Optional[RouterModel]:
        return next((m for m in self.models if m.active), None)

    @classmethod
    def from_json(cls, x: object) -> "RouterInfo":
        out = []
        for m in jlist(jdict(x).get("data")):
            md = jdict(m)
            st = jdict(md.get("status"))
            out.append(RouterModel(id=str(md.get("id", "")), status=str(st.get("value", "?")),
                                   failed=bool(st.get("failed")), args=[str(a) for a in jlist(st.get("args"))]))
        return cls(out)


@dataclass
class ServerData:
    """One snapshot of the server and the Mac (taken every refresh)."""
    t: float = 0.0
    up: bool = False
    pid: Optional[int] = None
    target_pid: Optional[int] = None    # the server to stop: the launcher's, else the one on the port
    exited: bool = False                # the launcher's server process has exited
    rss: Optional[int] = None
    cpu: float = 0.0
    etime: Optional[str] = None
    awake: bool = False                 # caffeinate keeps the Mac awake for it
    conns: List[Tuple[str, str]] = field(default_factory=list)   # (client address, port)
    cmd: str = ""
    shape: Optional[Shape] = None
    health_ms: Optional[float] = None
    slots: bool = False                 # llama.cpp's /slots answered
    slot_list: List[SlotInfo] = field(default_factory=list)
    n_ctx: int = 0
    busy: bool = False
    prompt: int = 0
    cached: int = 0
    processed: int = 0
    decoded: int = 0
    task: TaskId = None
    metrics: Dict[str, float] = field(default_factory=dict)        # /metrics without the llamacpp: prefix
    accepted_by_pos: Dict[int, float] = field(default_factory=dict)  # draft tokens accepted per position
    props: JSONDict = field(default_factory=dict)
    pp_rate: Optional[float] = None     # prompt tokens read per second, live
    tg_rate: Optional[float] = None     # tokens generated per second, live
    system: SystemStats = field(default_factory=SystemStats)
    log_path: Optional[str] = None
    router: Optional[RouterInfo] = None # a llama.cpp router: its models (props, slots, cmd: the loaded one's)

    @property
    def alias(self) -> str:
        """The served model's name: llama.cpp's alias ("" if unknown)."""
        return str(self.props.get("model_alias") or "")

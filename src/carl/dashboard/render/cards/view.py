"""The View the cards draw from, the server's state word and the Live tab's first sentence.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ....composition.cache_api import Client
from ....domain.llama.version import VersionCheck
from ....domain.server.logbook import LogBook
from ....domain.server.snapshot import ServerData, SlotInfo, SlowStats
from ....domain.shared.units import duration, percent, speed
from ....ports.plugin import CardView, HealthRow, InfoLine
from ....ui.fmt import CardLine

STOPPED, LOADING, IDLE, READING, WRITING = "STOPPED", "LOADING", "IDLE", "READING", "WRITING"


@dataclass
class View:
    """Everything the cards show besides the snapshot: the detail level, card levels (open or
    collapsed), the connection, the log, and what the app worked out (the disk cache, the client
    setup, the next start)."""
    levels: Dict[str, int]
    host: str
    port: int
    base: str
    key: str
    key_file: str
    key_shown: bool
    server_pid: Optional[int]           # the launcher's server (--server-pid)
    log: LogBook
    log_path: Optional[str]
    model_path: Optional[str]           # the GGUF the server loaded
    model_size: Optional[int]
    gpu_limit: Optional[Tuple[int, str]]
    slow: SlowStats
    total_mem: int
    home: str
    wrap: bool = False                  # log lines wrapped
    errors_only: bool = False
    log_scroll: int = 0                 # lines back from the end
    level_override: Dict[str, int] = field(default_factory=dict)
    cache: str = ""                     # what the disk cache holds (slot_folder.py)
    api: str = ""                       # the dashboard API for other computers (host:port; "" when not running)
    listeners: int = 0                  # other computers holding its config event stream (their sync service)
    pushed: str = ""                    # the last config sent: "VERSION at TIME"
    unsent: bool = False                # the config CARL would send now differs from the last one sent (23.4.4)
    single: str = ""                    # single-model mode: the model the server runs (the configs list only it)
    clients: Tuple[Client, ...] = ()    # the other computers that sync (client_registry.Client), newest first
    detail: str = "simple"              # simple | full
    drafter_size: int = 0               # the MTP drafter file the server loaded (-md), bytes
    model_quant: str = ""               # the catalogue's quantization of the loaded model
    here: str = ""                      # OpenCode and Pi on this Mac: "set up", "update needed", "not set up"
    coder_here: str = ""                # the coder's model on this Mac: "main", PROVIDER/MODEL, "" (not set up; 23.4.5)
    next_start: List[CardLine] = field(default_factory=list)    # the SERVER card when no server runs (app.py)
    reuse_from: str = ""                # where the busy request's reused tokens came from (Phase 23.5; mocked now)
    cache_slots: Dict[int, Tuple[str, str, str]] = field(default_factory=dict)   # slot -> (from, saved, session) (23.5.1.1)
    cache_column: bool = False          # the SLOTS card shows them (the Live tab at 160 columns and wider)
    # OpenCode and Pi on this Mac: (client, installed, tested, tested major) (Phase 23.5.1.11, the Connect tab)
    client_versions: Tuple[Tuple[str, str, str, str], ...] = ()
    selected: str = ""                  # the selected section (Tab): its title is drawn reversed
    gate: int = 0                       # config.json delegation.gate: carl-delegation's new-file gate (0 = off)
    llama: Optional[VersionCheck] = None    # the installed llama.cpp against the tested version (HEALTH)
    plugin_cards: Tuple[CardView, ...] = ()     # the plugins' cards of the Live tab, made for this frame (WP)
    plugin_health: Tuple[HealthRow, ...] = ()   # the plugins' checks and problems (HEALTH)
    vram_keep: Optional[int] = None     # the memory kept free on each GPU (gpu.vram_keep, bytes; a PC's MEMORY card)
    own_load: str = ""                  # the user's own load option in llama.extra_args (the MODEL card's load mode)
    request_cards: Tuple[CardView, ...] = ()    # the plugins' cards of the Requests tab, made for this frame
    # the plugins' lines under a finished request (the Requests tab's full level), by the request's task
    request_lines: Dict[int, Tuple[InfoLine, ...]] = field(default_factory=dict)

    @property
    def full(self) -> bool:
        return self.detail == "full"

    def level(self, name: str) -> int:
        """A section's level: 0 collapsed, 1 simple, 2 full (two-level sections: 0 or 1)."""
        return self.level_override.get(name, self.levels.get(name, 1))


# ---------------------------------------------------------------- the state
def busy_slots(d: ServerData) -> List[SlotInfo]:
    return [x for x in d.slot_list if x.busy]


def status_of(d: ServerData, server_pid: Optional[int]) -> Tuple[str, str]:
    """(word, pill background) for the header: STOPPED, LOADING, IDLE, READING, WRITING, BUSY ×N."""
    if d.exited or (not d.up and not d.pid):
        return STOPPED, "41"
    if not d.up:
        return LOADING, "43"
    if d.router is not None and not d.alias:
        cur = d.router.current
        return (LOADING, "43") if cur and cur.status == "loading" else (IDLE, "42")
    n = len(busy_slots(d)) or (1 if d.busy else 0)
    if not d.slots or n == 0:
        return IDLE, "42"
    if n > 1:
        return f"BUSY ×{n}", "46"
    return (READING, "43") if d.decoded == 0 else (WRITING, "46")


def _slot_list(ids: List[int]) -> str:
    names = [str(i) for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def read_left(d: ServerData) -> int:
    """Prompt tokens of the busy request still to read."""
    return max(d.prompt - d.cached - d.processed, 0)


def live_sentence(d: ServerData, port: int) -> str:
    """The Live tab's first line: the state in one plain sentence (the next action when stopped is in the
    SERVER card)."""
    label, _ = status_of(d, None)
    if label == STOPPED:
        return "The server stopped." if d.exited else "The server is not running."
    if label == LOADING:
        cur = d.router.current if d.router is not None else None
        what = f"The router is loading {cur.id}" if cur else "The server is starting: the model is loading"
        return f"{what}. This takes 30 s to 2 min."
    if d.router is not None and not d.alias:
        bad = [m.id for m in d.router.models if m.failed]
        if bad:
            return f"The router could not load {bad[0]}: the log (tab 4) says why. OpenCode and Pi can ask for another model."
        return "The server is running in router mode. No model is loaded: OpenCode and Pi load one when they ask for it."
    if not d.slots:
        return f"A server runs on port {port}, but it does not answer like llama.cpp. CARL shows only some figures."
    busy = busy_slots(d)
    free = [x.id for x in d.slot_list if not x.busy and x.id is not None]
    parts: List[str] = []
    if len(busy) > 1:
        doing = [f"slot {x.id} {'is writing an answer' if x.decoded else 'is reading a prompt'}" for x in busy]
        parts.append("Busy: " + ", ".join(doing) + ".")
    elif busy or d.busy:
        sid = busy[0].id if busy else 0
        if d.decoded == 0:
            done = d.cached + d.processed
            left = read_left(d)
            eta = f", about {duration(left / d.pp_rate)} left" if d.pp_rate else ""
            rate = f" at {speed(d.pp_rate)}" if d.pp_rate else ""
            parts.append(f"Reading a prompt in slot {sid}{rate}: {percent(done / d.prompt if d.prompt else 0)} "
                         f"done{eta}.")
        else:
            parts.append(f"Writing an answer in slot {sid}" + (f" at {speed(d.tg_rate)}." if d.tg_rate else "."))
    else:
        n = len(d.slot_list)
        parts.append("No request is running." + (f" The {n} slots are free." if n > 1 else ""))
    if (busy or d.busy) and free:
        parts.append(f"Slot {free[0]} is free." if len(free) == 1 else f"Slots {_slot_list(free)} are free.")
    return " ".join(parts)

"""The dashboard's UI state: the tab, scroll positions, dialogs, the Settings panels and
the background jobs they started. Plain data; app.App changes it."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple, Union

from .card_form import CardForm
from .cards import LEVEL_NAMES
from .model import ModelInfo
from .settings import Pending
from .store import HFFile

TABS = ["Overview", "Connect", "Requests", "Log", "Settings"]
SUBPANELS = ["Server", "Models", "Auto fit", "Auto-tune", "Router"]
SP_SERVER, SP_MODELS, SP_FIT, SP_TUNE, SP_ROUTER = range(len(SUBPANELS))


class Process(Protocol):
    """A child process (subprocess.Popen)."""
    pid: int

    @property
    def returncode(self) -> Optional[int]: ...
    def poll(self) -> Optional[int]: ...


PickItem = Tuple[str, Union[str, ModelInfo]]    # (value, its line: text, or a model drawn as a model line)


@dataclass
class Picker:
    """A drop-down list (models, Hugging Face files, the model to tune)."""
    title: str
    items: List[PickItem]
    on_pick: str                    # pickmodel | pickhf | picktune
    sel: int = 0
    header: Optional[str] = None
    foot: Optional[str] = None
    noun: str = "models"
    reopen: Optional[str] = None    # sort / filter picker: reopen the model drop-down on this model
    mark: Optional[str] = None      # the model drop-down: auto fit's pick (★)
    note: str = ""                  # shown below the list while the first item ("auto") is selected


@dataclass
class Confirm:
    """A yes / no question (delete a model, stop the server for Auto-tune)."""
    title: str
    lines: List[str]
    yes: str                        # the action Yes runs
    model: Optional[str] = None


@dataclass
class TextPrompt:
    """A line of text being typed (a Hugging Face repo)."""
    prompt: str
    on_enter: str
    value: str = ""


@dataclass
class HFLookup:
    """A Hugging Face repo being looked up, or its GGUF files."""
    repo: str
    status: str = ""
    files: List[HFFile] = field(default_factory=list)


@dataclass
class Download:
    """A model download (tools/carl.py download): progress from the file's size."""
    name: str
    path: Optional[str]
    total: int
    proc: Process
    log: str
    hist: List[Tuple[float, int]] = field(default_factory=list)     # (time, bytes) of the last samples
    have: int = 0
    rate: float = 0.0               # bytes per second
    tail: List[str] = field(default_factory=list)                   # the last line of its output
    done: bool = False


@dataclass
class TuneRun:
    """An Auto-tune run (tools/carl-tune.py)."""
    model: str
    proc: Process
    log: str
    restart: bool                   # it stopped the server: start it again afterwards
    lines: List[str] = field(default_factory=list)                  # its output so far
    done: bool = False


@dataclass
class InstallRun:
    """./carl.sh install from the Connect tab (host/serve.sh install): clients and configs, or
    the configs only."""
    what: str                       # "clients and configs" | "configs"
    proc: Process
    log: str
    lines: List[str] = field(default_factory=list)                  # its output so far
    done: bool = False


@dataclass
class UIState:
    """Everything the screen shows besides the snapshot: tab, scroll, dialogs, Settings, jobs."""
    tab: int = 0
    scroll: int = 0                 # Overview
    prev_scroll: int = 0            # Connect: the config preview
    req_scroll: int = 0
    log_scroll: int = 0             # lines back from the end
    lines: int = 6                  # log lines on the Overview tab
    wrap: bool = False
    errors_only: bool = False
    key_shown: bool = False
    help: bool = False
    quit: bool = False              # the quit dialog is open
    stopping: Optional[Tuple[int, float]] = None    # (server pid, SIGKILL deadline)
    exit_msg: str = ""
    toast_msg: Tuple[str, float] = ("", 0.0)        # (message, shown until)
    preview: str = "opencode"       # Connect: opencode | pi | curl
    copied: Optional[str] = None
    levels: Dict[str, int] = field(default_factory=lambda: {x: 1 for x in LEVEL_NAMES})
    # Settings
    sp: int = SP_SERVER             # panel: Server, Models, Auto fit, Auto-tune, Router
    pending: Optional[Pending] = None               # Server panel: the values being chosen
    set_run: Pending = field(default_factory=dict)  # what ran when they were first shown
    set_row: int = 0
    set_scroll: int = 0             # Server panel: rows scrolled off the top
    edit: Optional[str] = None      # a number being typed into the selected row
    confirm: bool = False           # Apply: "restart?" asked
    restart: Optional[str] = None   # what a restart is doing now
    mrow: int = 0                   # Models panel: the selected model (in the sorted, filtered list)
    msort: int = 0                  # model lists: index into arrange.SORTS
    slist: bool = False             # Server panel: the model list beside the settings has the keys
    srow: int = 0                   # Server panel: the cursor in that list
    mfilter: int = 0                # model lists: index into arrange.FILTERS
    fit_scroll: int = 0             # Auto fit panel: lines scrolled off the top
    card: Optional[CardForm] = None # Models panel: a custom model's card being edited (e)
    picker: Optional[Picker] = None
    confirm2: Optional[Confirm] = None
    text: Optional[TextPrompt] = None
    hf: Optional[HFLookup] = None
    dl: Optional[Download] = None
    tune: Optional[TuneRun] = None
    tune_model: Optional[str] = None
    tune_quick: bool = False
    # Connect
    install_ask: Optional[str] = None               # "all" | "config": Install asked "run it?"
    install: Optional[InstallRun] = None
    install_shown: bool = False     # its output replaces the config preview until a copy button
    install_after_restart: bool = False             # a mode switch: update this Mac's configs once it is up

    def toast(self, msg: str, secs: float = 4) -> None:
        """Show msg in the footer for secs seconds."""
        self.toast_msg = (msg, time.time() + secs)

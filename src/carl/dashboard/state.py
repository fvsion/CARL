"""The dashboard's UI state: the tab, scroll positions, dialogs, the Settings panels and
the background jobs they started (their records: application/server_jobs/records.py, set here by the jobs' notifier,
dashboard/actions/notifier.py). Plain data; app.App changes it."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Union

from ..application.server_jobs.records import (TUNE_ALL, Download, Drain, HFLookup, InstallRun,  # noqa: F401
                                               PackageRun, Process, TuneRun)
from ..application.settings_service import Pending
from ..domain.server.snapshot import ModelInfo
from ..domain.server.ub_order import UbAsk
from ..domain.shared.constants import DETAILS  # noqa: F401  (the detail levels; the controller imports them from here)
from ..ports.plugin import GROUP_CONTEXT
from ..ui.fmt import RED
from .forms.card_form import CardForm
from .sections import LEVEL_NAMES

TABS = ["Live", "Connect", "Requests", "Log", "Settings"]
SUBPANELS = ["Server", "Models", "Agents", "Auto fit", "Auto-tune", "Router", "Caching"]
SUBPANEL_GROUP = {"Caching": GROUP_CONTEXT}     # the panel bar's groups (A73): Caching opens Context management, and the
#                                                 plugins' panels of the group follow it (the registry orders them so)
CONNECT_SUBPANELS = ["Setup", "Clients"]
SP_SERVER, SP_MODELS, SP_AGENTS, SP_FIT, SP_TUNE, SP_ROUTER, SP_CACHE = range(len(SUBPANELS))


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
    yes_label: str = "Yes"          # the Yes button's verb ("Delete", "Download", ...)
    alt: str = ""                   # a second answer instead of Cancel's neighbour (the -ub question: Keep); "" none
    alt_label: str = ""             # ...its button (the key is k)


@dataclass
class TextPrompt:
    """A line of text being typed (a Hugging Face repo, a card field, the Hugging Face token). secret: it is drawn as
    dots, never as the text."""
    prompt: str
    on_enter: str
    value: str = ""
    secret: bool = False


@dataclass
class UIState:
    """Everything the screen shows besides the snapshot: tab, scroll, dialogs, Settings, jobs."""
    tab: int = 0
    detail: str = "simple"          # simple | full (D; saved in the dashboard's own file, uiprefs.py)
    scroll: int = 0                 # Live
    prev_scroll: int = 0            # Connect: the config preview
    conn_scroll: int = 0            # Connect: the page (rows off the top)
    connect_sp: int = 0             # Connect: 0 Setup, 1 Clients (CONNECT_SUBPANELS)
    req_scroll: int = 0
    log_scroll: int = 0             # lines back from the end
    lines: int = 0                  # log lines on the Live tab (+ -, --lines); 0 = by the screen's height
    wrap: bool = False
    errors_only: bool = False
    key_shown: bool = False
    help: bool = False
    keys: List[Tuple[str, str]] = field(default_factory=list)   # the shown panel's keys (the footer; set as it draws)
    keys_more: List[str] = field(default_factory=list)         # ... and what the ? card adds
    quit: bool = False              # the quit dialog is open
    stopping: Optional[Tuple[int, float]] = None    # (server pid, SIGKILL deadline)
    exit_msg: str = ""
    toast_msg: Tuple[str, float] = ("", 0.0)        # (message, shown until): the message line
    toast_error: bool = False       # the message reports a failure: red
    messages: List[Tuple[float, str]] = field(default_factory=list)   # the last messages (the ? card)
    start_error: List[str] = field(default_factory=list)   # the launcher's "error:" lines of a start that failed
    preview: str = "opencode"       # Connect: opencode | pi | curl
    copied: Optional[str] = None
    levels: Dict[str, int] = field(default_factory=lambda: {x: 1 for x in LEVEL_NAMES})   # 0 collapsed, 1 simple, 2 full
    opened: Set[str] = field(default_factory=set)   # sections whose level the user set (L or a click) in this run:
                                                    # a plugin's card is collapsed to keep the Log on screen unless here
    section: str = ""               # the selected section (Tab / Shift-Tab; L changes its level)
    revealed: str = ""              # the section the page last scrolled to (ui.fmt.reveal)
    sections: List[str] = field(default_factory=list)   # the sections of the screen shown, in order (set as it draws)
    more: bool = False              # the page shown goes on below the screen (the footer says "↓ more")
    # Settings
    sp: int = SP_SERVER             # panel: Server, Models, Agents, Auto fit, Auto-tune, Router, Caching
    pending: Optional[Pending] = None               # Server panel: the values being chosen
    set_run: Pending = field(default_factory=dict)  # what ran when they were first shown
    set_row: int = 0
    set_scroll: int = 0             # Server panel: rows scrolled off the top
    page_scroll: int = 0            # Auto-tune, Router, Caching: rows scrolled off the top (0 when the panel changes)
    edit: Optional[str] = None      # a number being typed into the selected row
    confirm: bool = False           # Apply: "restart?" asked
    confirm_port: Optional[int] = None   # Apply with the port taken by another program: the free port offered
    confirm_taken: str = ""         # ...and what holds the port ("ssh, pid 4242")
    api_port: Optional[int] = None  # the dashboard API's port now (app.cache_api; None: not running)
    api_note: str = ""              # why it runs elsewhere, or why it does not run
    restart: Optional[str] = None   # what a restart is doing now
    restart_t: float = 0.0          # when it began
    mrow: int = 0                   # Models panel: the selected model (in the sorted, filtered list)
    msort: int = 0                  # model lists: index into arrange.SORTS
    slist: bool = False             # Server panel: the model list beside the settings has the keys
    srow: int = 0                   # Server panel: the cursor in that list
    mfilter: int = 0                # model lists: index into arrange.FILTERS
    mspeeds: bool = False           # Models panel: the list shows the speeds of each source (t)
    mquants: Dict[str, str] = field(default_factory=dict)   # model lists: the quantization shown for a model (its
                                                            # name -> the entry's name; none: the default one, A68)
    fit_scroll: int = 0             # Auto fit panel: lines scrolled off the top
    cache_row: int = 0              # Caching panel: the selected row
    cache_window: int = 0           # Caching panel: WHAT THE CACHE DID's time (cache_events.py WINDOWS; t changes it)
    router_row: int = 0             # Router panel: the selected model
    plugin_row: int = 0             # a plugin's panel (the panels after Caching, sp >= len(SUBPANELS)): the selected row
    agents_row: int = 1             # Agents panel: the selected row (0 the model, 1 Main thinking, 2 Coder thinking)
    agents_model: str = ""          # Agents panel: the model shown ("": the running model, else the Server panel's)
    agents_undo: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)  # Agents: r restores these
    card: Optional[CardForm] = None # Models panel: a custom model's card being edited (e)
    picker: Optional[Picker] = None
    confirm2: Optional[Confirm] = None
    ub_ask: Optional[UbAsk] = None  # Apply: the -ub question that confirm2 asks (A51)
    drain: Optional[Drain] = None     # a reply runs: stop now, or wait for it
    text: Optional[TextPrompt] = None
    hf: Optional[HFLookup] = None
    hf_token_rev: int = 0           # the Hugging Face token was set or cleared: the Models panel asks its status again
    dl: Optional[Download] = None
    tune: Optional[TuneRun] = None
    tune_model: Optional[str] = None
    tune_depth: str = "default"     # Auto-tune: quick | default | long
    # Connect
    install_ask: Optional[str] = None               # "all" | "config": Install asked "run it?"
    install: Optional[InstallRun] = None
    install_shown: bool = False     # its output replaces the config preview until a copy button
    install_after_restart: bool = False             # a mode switch: update this Mac's configs once it is up
    package: Optional[PackageRun] = None            # the last client package (z) made in this session
    package_shown: bool = False     # its card replaces the config preview until a copy button or x

    @property
    def full(self) -> bool:
        """The full detail level: the reasons, the figures, the flags and keys too."""
        return self.detail == "full"

    def toast(self, msg: str, secs: float = 6, error: bool = False) -> None:
        """Show msg on the message line (above the keys) for secs seconds; the ? card keeps the last ones. error
        (or a message that starts in red): the line is red, else green."""
        self.toast_msg = (msg, time.time() + secs)
        self.toast_error = error or msg.startswith(RED)
        self.messages = [*self.messages[-7:], (time.time(), msg)]

"""The plugin seam's ports (WP, decisions 10, A27, A28, A30 and A33): what a plugin is, and what the rest of CARL sees
of the plugins.

  Plugin      what a plugin implements (it is an adapter of this port): an id, a title, and `parts()`, the hooks it adds.
              Every hook is optional: a part it does not use stays empty. Contract C-055.
  PluginHost  what the application and the driving side ask of the plugins that are installed: their setting keys, the
              launch steps, the configuration panels, the dashboard cards and their actions, the lines under a request,
              the commands, the environment checks, the client files, what they provide by name, and the problems. The registry (src/carl/plugins) is its adapter. A plugin that fails
              never stops CARL: its hook answers nothing and the problem is listed (HEALTH card). Contract C-056.
  PluginComputer  what CARL tells a plugin's hooks of this computer (PluginContext.computer): the GPU, the llama.cpp
              programs (found, or fetched), the folder of the language models. The composition is its adapter.
              Contract C-066.
  SessionFolders  a folder for each client session, kept when the session's cache expires (PluginContext.sessions):
              a plugin keeps its files for a session there. Its adapter is adapters/common/session_folders.py.
              Contract C-067.

The values here are plain data: a plugin describes a card, a panel or a row, and the dashboard draws it with its own
layout (widths, order, narrow screens, colours), so a plugin cannot break a screen. The domain never imports a plugin;
the launch step's values (LaunchFacts, LaunchStep) and the setting keys (SettingSpec) are the domain's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Protocol, Sequence, Tuple

from ..domain.config.settings import SettingSpec
from ..domain.server.launch_plan import LaunchFacts, LaunchStep
from ..domain.shared.types import SettingValue

# The dashboard's slots for a card. The Live tab's columns: main, the end of the first column; side, the end of the last
# one (the cards of both stack in the one column of a narrow screen, main first). The Requests tab: requests, under the
# REQUESTS table, as wide as it.
SLOT_MAIN = "live.main"
SLOT_SIDE = "live.side"
SLOT_REQUESTS = "requests.side"
LIVE_SLOTS = (SLOT_MAIN, SLOT_SIDE)
SLOTS = (SLOT_MAIN, SLOT_SIDE, SLOT_REQUESTS)

# The groups of the Settings tab (A73): a plugin's panel may name one, and it is drawn in that group of the panel bar,
# beside CARL's own panels of the group (Context management: Caching, then the panels of the plugins in that group).
# "" is no group: the panel comes after every group's.
GROUP_CONTEXT = "context"
GROUPS: Mapping[str, str] = {GROUP_CONTEXT: "Context management"}

# A card or a panel is a section of the dashboard (its level, Tab and clicks); its name is built here, once.
SECTION_PREFIX = "plugin-"


def section_name(plugin: str, part: str) -> str:
    """The name of a plugin's section: plugin-ID-PART (a card's id, a panel's id)."""
    return f"{SECTION_PREFIX}{plugin}-{part}"


# How a line of a card or a panel is coloured (the dashboard picks the colours).
TONES = ("", "ok", "warn", "bad", "dim")


@dataclass(frozen=True)
class SlotSession:
    """A slot of the server and the session in it: the slot's number, the session's ID ("" when no session was placed
    there) and its name (the name the clients give it, else its ID cut short; "" with no session). For a finished
    request: the slot it ran on and the session that slot held when it started."""
    slot: int
    session: str = ""
    name: str = ""


@dataclass(frozen=True)
class GpuRoom:
    """What CARL read of this computer's GPU: api, metal (a Mac), cuda (an NVIDIA GPU) or other ("": no GPU, or it could
    not be read); room, the bytes a model may use on it now; cuda, the CUDA major version of the llama.cpp build CARL
    uses (0: not known, or no NVIDIA GPU)."""
    api: str = ""
    room: int = 0
    cuda: int = 0


@dataclass(frozen=True)
class ProgramFetch:
    """What a fetch of a llama.cpp program gave: whether the program is there now, a line that says what happened, and
    whether something was downloaded."""
    ok: bool
    text: str
    fetched: bool = False


class PluginComputer(Protocol):
    """What CARL tells a plugin's hooks of this computer, read when a hook asks (contract C-066). A program's name may
    list alternatives joined by "|" ("llama-embedding|llama-server"): the first one this computer has counts."""

    def gpu(self) -> GpuRoom:
        """The GPU now; GpuRoom() when there is none or it cannot be read."""
        ...

    def find_program(self, name: str) -> Optional[str]:
        """The path of a llama.cpp program in the llama.cpp CARL uses (else on PATH); None when there is none."""
        ...

    def fetch_how(self, name: str) -> str:
        """How the program would be fetched, in words (a step of a plan the user agrees to)."""
        ...

    def fetch_program(self, name: str, say: Callable[[str], None]) -> ProgramFetch:
        """The program fetched when this computer lacks it (Homebrew's llama.cpp on a Mac, the release CARL pins on a
        PC); a program it has is not fetched again."""
        ...

    def models_dir(self) -> str:
        """The folder of the language models (the models folder setting)."""
        ...


class SessionFolders(Protocol):
    """A folder for each client session, in the settings folder's sessions folder (contract C-067): what belongs to a
    session and outlives its cache. A plugin keeps there only files whose name starts with its id (`ID.json`). CARL
    keeps the newest sessions (domain/server/session_folders.KEEP); an older one's folder goes whole."""

    def path(self, session: str) -> str:
        """The session's folder, made or not ("" for an empty ID); nothing is read or written."""
        ...

    def make(self, session: str) -> str:
        """The session's folder, made when it is not there and marked as used now; the oldest folders past the ones
        kept go. "" for an empty ID or a folder that cannot be made."""
        ...


@dataclass(frozen=True)
class PluginContext:
    """What a hook gets: the plugin's id, this computer's OS (macos, windows, linux), the plugin's own data folder (the
    plugin makes it when it needs it; a plugin writes nowhere else but in its files of a session's folder, `sessions`),
    and its settings now (every key, the default where nothing is saved). slots: the slots of the model the server runs
    and the session each holds (the dashboard's cards and request lines get them; () elsewhere and when no model is
    loaded). computer: what CARL tells of this computer (PluginComputer; None where the registry was given none).
    sessions: the sessions' own folders (SessionFolders; None where the registry was given none)."""
    plugin: str
    os_name: str
    data_dir: str
    settings: Mapping[str, SettingValue]
    slots: Tuple[SlotSession, ...] = ()
    computer: Optional[PluginComputer] = None
    sessions: Optional[SessionFolders] = None


@dataclass(frozen=True)
class InfoLine:
    """One reading of a card or a panel: a label and a text (no label: a plain line), and a note drawn dim after the
    text."""
    label: str = ""
    text: str = ""
    tone: str = ""
    note: str = ""


@dataclass(frozen=True)
class CardAction:
    """A button of a card: its id, its label, an optional key (one character; it works while the card is the selected
    section) and what it does (it answers the message the dashboard shows)."""
    id: str
    label: str
    run: Callable[[PluginContext], str]
    key: str = ""


@dataclass(frozen=True)
class CardSpec:
    """A card in a slot of the dashboard (SLOTS). lines: its readings (made each time the card is drawn). order: its
    place among the cards of its slot (then by plugin id). min_cols: the card is left out of a screen narrower than
    this. stopped: it also shows when no server runs. shrinks: where the Live tab has no room for it beside the RECENT
    REQUESTS and the LOG, it is drawn collapsed (a card must not push the Log off the screen) unless the user set its
    level. about: what the card shows, a line of its tab's ? card ("": none). On the Requests tab a card is as wide as
    the table, each reading takes one line (a long one is cut) and its summary is drawn dim."""
    id: str
    title: str
    lines: Callable[[PluginContext], Sequence[InfoLine]]
    slot: str = SLOT_MAIN
    summary: Callable[[PluginContext], str] = field(default=lambda ctx: "")
    actions: Tuple[CardAction, ...] = ()
    order: int = 0
    min_cols: int = 0
    stopped: bool = False
    shrinks: bool = False
    about: str = ""


@dataclass(frozen=True)
class PanelRow:
    """A setting in a panel: its key (one of the plugin's), its label, what it does, and the values the dashboard
    offers for a number or a text (a choice or a bool offers its own)."""
    key: str
    label: str
    help: str = ""
    offered: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PanelSpec:
    """A panel in the Settings tab: the settings it shows, a text about the plugin, and readings it adds. group: the
    group of the panel bar it belongs to (a key of GROUPS; "" for none). lines_title: the title of the readings' own
    section under the settings ("": the readings go under the text about the plugin)."""
    id: str
    title: str
    rows: Tuple[PanelRow, ...] = ()
    about: str = ""
    lines: Callable[[PluginContext], Sequence[InfoLine]] = field(default=lambda ctx: ())
    group: str = ""
    lines_title: str = ""


@dataclass(frozen=True)
class PluginCommand:
    """A command (`carl NAME ...`). run gets the arguments after the word and a function that prints a line; it answers
    the exit code. listed: `carl -h` lists it, with its help, in a group of the plugins' commands while the plugin is
    on (False: a hidden command, which no help lists)."""
    name: str
    run: Callable[[PluginContext, Sequence[str], Callable[[str], None]], int]
    help: str = ""
    listed: bool = False


@dataclass(frozen=True)
class ClientFile:
    """A file a plugin adds to the client package (the OpenCode and Pi parts): source, its path on this computer (an
    absolute path; a plugin names the files beside its own module); target, its path in the package, under client/
    (relative, with /: for example opencode/plugins/NAME/index.js). The package holds it only while the plugin is on."""
    source: str
    target: str


@dataclass(frozen=True)
class CheckResult:
    """The answer of an environment check: whether it passed, and a text (what is wrong, or what was found)."""
    ok: bool
    text: str = ""


@dataclass(frozen=True)
class PluginCheck:
    """An environment check of a plugin; the HEALTH card shows its result."""
    id: str
    label: str
    run: Callable[[PluginContext], CheckResult]


@dataclass(frozen=True)
class PluginParts:
    """What a plugin adds. Each part is optional (empty: nothing).
      settings   its keys of config.json plugins.<id>.<key>, and what each does (help, by key)
      panels     its panels in the Settings tab
      cards      its cards in the dashboard's slots
      launch     the step it adds to a start of the server (None: it adds none)
      commands   its commands (hidden, or listed in `carl -h`)
      checks     its environment checks
      request_lines  the lines it adds under a finished request on the Requests tab (the full level): a function of
                     the context and the request's slot and session (SlotSession)
      client_files   the files it adds to the client package (ClientFile)
      provides   what it offers to code that asks for it by name: a name and a function of the context that gives the
                 object (the seam reads neither; the asker and the plugin agree on both)
      setting_env  the variable of the environment that wins over a setting, by the setting's key (W8.5): the hooks of
                   the plugin (its card, its panel's readings, what it provides) get the variable's value in place of
                   the saved one, so a setting has one source whoever asks"""
    settings: Mapping[str, SettingSpec] = field(default_factory=dict)
    help: Mapping[str, str] = field(default_factory=dict)
    panels: Tuple[PanelSpec, ...] = ()
    cards: Tuple[CardSpec, ...] = ()
    launch: Optional[Callable[[PluginContext, LaunchFacts], Optional[LaunchStep]]] = None
    commands: Tuple[PluginCommand, ...] = ()
    checks: Tuple[PluginCheck, ...] = ()
    request_lines: Optional[Callable[[PluginContext, SlotSession], Sequence[InfoLine]]] = None
    client_files: Tuple[ClientFile, ...] = ()
    provides: Mapping[str, Callable[[PluginContext], object]] = field(default_factory=dict)
    setting_env: Mapping[str, str] = field(default_factory=dict)


class Plugin(Protocol):
    """A plugin. id: lower-case letters, digits and "_" (its section of config.json and its data folder). title: its
    name on the screen. default_enabled: whether it is on until the user says (plugins.<id>.enabled). Contract C-055."""
    id: str
    title: str
    default_enabled: bool

    def parts(self) -> PluginParts:
        """What the plugin adds. Called once, when the registry loads the plugin."""
        ...


# ---------------------------------------------------------------- what the plugin host gives back
@dataclass(frozen=True)
class PluginInfo:
    """An installed plugin: its id and title, where it came from (builtin or the folder's path), whether it is on, and
    why it did not load ("" when it did)."""
    id: str
    title: str
    source: str
    enabled: bool
    failed: str = ""


@dataclass(frozen=True)
class ActionView:
    """A card's button as the dashboard draws it."""
    id: str
    label: str
    key: str = ""


@dataclass(frozen=True)
class CardView:
    """A card ready to draw. name: its section's name (its level, Tab and clicks); lines are made already."""
    name: str
    plugin: str
    id: str
    title: str
    summary: str
    lines: Tuple[InfoLine, ...]
    slot: str
    actions: Tuple[ActionView, ...] = ()
    min_cols: int = 0
    stopped: bool = False
    shrinks: bool = False
    about: str = ""


@dataclass(frozen=True)
class RowView:
    """A setting of a panel: its schema entry, its value now, its labels. choices: the values the dashboard offers."""
    key: str
    label: str
    kind: str
    value: SettingValue
    default: SettingValue
    choices: Tuple[str, ...]
    help: str = ""


@dataclass(frozen=True)
class PanelView:
    """A panel ready to draw. name: its section's name; enabled is its first row's value (the plugin's on/off switch);
    group and lines_title: the spec's (PanelSpec)."""
    name: str
    plugin: str
    id: str
    title: str
    heading: str
    about: str
    rows: Tuple[RowView, ...]
    lines: Tuple[InfoLine, ...]
    group: str = ""
    lines_title: str = ""


@dataclass(frozen=True)
class CommandInfo:
    """A command of a plugin. listed: `carl -h` lists it."""
    name: str
    plugin: str
    help: str = ""
    listed: bool = False


@dataclass(frozen=True)
class HealthRow:
    """A line of the HEALTH card from the plugins: a check's result, or why a plugin or one of its hooks failed."""
    plugin: str
    label: str
    ok: bool
    text: str


class PluginHost(Protocol):
    """The plugins that are installed, as the rest of CARL sees them. Contract C-056."""

    def plugins(self) -> Sequence[PluginInfo]:
        """Every plugin found, built in or beside CARL, in the order of its id."""
        ...

    def setting_keys(self) -> Mapping[str, Mapping[str, SettingSpec]]:
        """The keys of config.json plugins.<id>.<key> that the plugins declare (an "enabled" key for each plugin
        too), for the plugins that loaded."""
        ...

    def setting_help(self) -> Mapping[str, Mapping[str, str]]:
        """What each key does, by plugin id and key (only the keys that have a text); the plugins that declare
        settings of their own are listed."""
        ...

    def launch_steps(self, facts: LaunchFacts) -> Sequence[LaunchStep]:
        """The launch steps of the plugins that are on, in the order of their ids; a step that fails is left out."""
        ...

    def panels(self) -> Sequence[PanelView]:
        """The configuration panels of the plugins (a plugin that is off: its Turned on row only): those of a group
        first, in the order of GROUPS, then the others; within each, in the order of the plugin ids."""
        ...

    def cards(self, slot: str, slots: Sequence[SlotSession] = ()) -> Sequence[CardView]:
        """The cards of the plugins that are on in a slot, by their order. slots: the server's slots and their sessions
        now (PluginContext.slots)."""
        ...

    def request_lines(self, requests: Sequence[SlotSession], slots: Sequence[SlotSession] = ()) -> Sequence[
            Tuple[InfoLine, ...]]:
        """The lines the plugins that are on add under each of these finished requests (each: its slot and the session
        that slot held), in the same order; within a request, by plugin id. slots: as for cards."""
        ...

    def run_action(self, plugin: str, card: str, action: str) -> str:
        """A card's button: the message to show ("" when the plugin, the card or the action is not there)."""
        ...

    def commands(self) -> Sequence[CommandInfo]:
        """The commands of the plugins that are on, by name."""
        ...

    def run_command(self, name: str, argv: Sequence[str], out: Callable[[str], None]) -> int:
        """A plugin's command: its exit code (1 when it failed, after printing why; 2: no such command)."""
        ...

    def client_files(self) -> Sequence[ClientFile]:
        """The files the plugins that are on add to the client package, by plugin id (a target two plugins name: the
        first plugin's)."""
        ...

    def provided(self, name: str) -> Sequence[object]:
        """What the plugins that are on provide under a name (PluginParts.provides), in the order of their ids; a hook
        that raises gives nothing."""
        ...

    def health(self) -> Sequence[HealthRow]:
        """The checks' results and the problems the plugins had (a plugin that did not load, a hook that failed)."""
        ...

    def set_setting(self, plugin: str, key: str, value: SettingValue) -> None:
        """Save plugins.<plugin>.<key> in config.json (checked against the key's schema; raises ConfigError)."""
        ...

    def refresh(self) -> None:
        """Read the plugins' settings again at the next question."""
        ...

"""The plugin registry (WP): the adapter of the plugin host port (ports/plugin.py, contract C-056).

It finds the plugins by looking, not by name: the built-in ones are the sub-packages of this package, the others are
the folders (or single .py files) of the folders it is given, installed beside CARL. A plugin module has a function
`plugin()` that returns an object with the shape of the Plugin port (an id, a title, default_enabled and `parts()`).
No plugin is named in this module, and none outside this tree is named anywhere in CARL.

A plugin never stops CARL. A module that does not import, a `plugin()` or `parts()` that raises, an id or a key with a
bad name, a hook that raises: each is recorded (problems, shown in the HEALTH card), the plugin or the hook then gives
nothing, and the rest of CARL goes on. A plugin is on until plugins.<id>.enabled says false (or off until it says true,
for a plugin whose default_enabled is false); a plugin that is off adds nothing but its setting keys, and its panels
show only the Enabled row, so it can be switched on in the dashboard.

Only the composition builds the registry (rule R20). Nothing is read until the first question.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
import re
import sys
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..domain.config.settings import SettingSpec
from ..domain.server.launch_plan import LaunchFacts, LaunchStep
from ..domain.shared.errors import ConfigError
from ..domain.shared.types import SettingValue
from ..ports.plugin import (GROUPS, SLOTS, TONES, ActionView, CardView, CheckResult, ClientFile, CommandInfo,
                            HealthRow, InfoLine, PanelView, Plugin, PluginComputer, PluginContext, PluginInfo,
                            PluginParts, RowView, SessionFolders, SlotSession, section_name)

ENABLED = "enabled"                              # plugins.<id>.enabled: the switch of every plugin
BUILTIN = "builtin"
CHECK_TTL = 10.0                                 # seconds a check's result stays
SETTINGS_TTL = 1.0                               # seconds the plugins' saved settings stay
_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
_TARGET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*")     # a client file's place
SettingsRead = Callable[[], Mapping[str, Mapping[str, SettingValue]]]
SettingWrite = Callable[[str, str, SettingValue], None]


@dataclass
class _Loaded:
    plugin: Plugin
    source: str
    parts: PluginParts


def _enabled_spec(default: bool) -> SettingSpec:
    return SettingSpec("bool", default)


def _why(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}".strip().replace("\n", " ")[:200]


class PluginRegistry:
    """The plugins of this CARL. folders: where plugins installed beside CARL are (a folder that does not exist is
    skipped); os_name: the profile's OS; data_root: the folder of the plugins' data (one folder for each id inside it);
    read_settings: the saved plugins.* settings now; write_setting: saves one; now: the clock in seconds. builtin:
    look for the built-in plugins (this package's sub-packages); extra: plugin objects handed in (tests). env: the
    environment's variables (the composition's copy), for the settings a plugin says a variable wins over
    (PluginParts.setting_env): the registry reads them here, the plugins read no variable. computer: what CARL tells
    them of this computer (PluginContext.computer); sessions: the sessions' own folders (PluginContext.sessions)."""

    def __init__(self, folders: Sequence[str], os_name: str, data_root: str, read_settings: SettingsRead,
                 write_setting: SettingWrite, now: Callable[[], float], builtin: bool = True,
                 extra: Sequence[Plugin] = (), env: Optional[Mapping[str, str]] = None,
                 computer: Optional[PluginComputer] = None, sessions: Optional[SessionFolders] = None) -> None:
        self.folders, self.os_name, self.data_root = list(folders), os_name, data_root
        self.computer, self.sessions = computer, sessions
        self._env = dict(env or {})
        self._read, self._write, self._now = read_settings, write_setting, now
        self._builtin, self._extra = builtin, list(extra)
        self._loaded: Optional[List[_Loaded]] = None
        self._failed: Dict[str, Tuple[str, str]] = {}               # name -> (source, why) of what did not load
        self._problems: Dict[Tuple[str, str], str] = {}             # (plugin id, hook) -> why
        self._saved: Optional[Tuple[float, Mapping[str, Mapping[str, SettingValue]]]] = None
        self._checks: Dict[Tuple[str, str], Tuple[float, CheckResult]] = {}

    # ------------------------------------------------------------ finding the plugins
    def _load(self) -> List[_Loaded]:
        if self._loaded is None:
            self._loaded = []
            found: List[Tuple[str, str, Callable[[], Plugin]]] = []
            if self._builtin:
                found += self._builtin_modules()
            for folder in self.folders:
                found += self._folder_modules(folder)
            found += [(getattr(p, "id", "?"), "given", (lambda p=p: p)) for p in self._extra]  # type: ignore[misc]
            seen: Dict[str, str] = {}
            for name, source, make in found:
                self._add(name, source, make, seen)
            self._loaded.sort(key=lambda x: x.plugin.id)
        return self._loaded

    def _add(self, name: str, source: str, make: Callable[[], Plugin], seen: Dict[str, str]) -> None:
        assert self._loaded is not None
        try:
            plugin = make()
            pid = plugin.id
            if not isinstance(pid, str) or not _ID.fullmatch(pid):
                raise ValueError(f"the id {pid!r} is not lower-case letters, digits and _ (at most 32)")
            if pid in seen:
                raise ValueError(f"the id {pid} is already used by the plugin from {seen[pid]}")
            parts = plugin.parts()
            self._check_parts(parts)
        except Exception as e:                                  # a plugin that does not load never stops CARL
            self._failed[name] = (source, _why(e))
            return
        seen[pid] = source
        self._loaded.append(_Loaded(plugin, source, parts))

    @staticmethod
    def _check_parts(parts: PluginParts) -> None:
        for key in parts.settings:
            if not _ID.fullmatch(key) or key == ENABLED:
                raise ValueError(f"the setting key {key!r} is not allowed (lower-case letters, digits and _; "
                                 f"{ENABLED} is every plugin's own)")
        keys = set(parts.settings) | {ENABLED}
        for panel in parts.panels:
            if panel.group and panel.group not in GROUPS:
                raise ValueError(f"panel {panel.id} is in the group {panel.group!r}; the groups are {', '.join(GROUPS)}")
            for r in panel.rows:
                if r.key not in keys:
                    raise ValueError(f"panel {panel.id} shows {r.key}, which is not one of the plugin's settings")
        for card in parts.cards:
            if card.slot not in SLOTS:
                raise ValueError(f"card {card.id} is in the slot {card.slot!r}; the slots are {', '.join(SLOTS)}")
            if not _ID.fullmatch(card.id):
                raise ValueError(f"the card id {card.id!r} is not lower-case letters, digits and _")
            for act in card.actions:
                if not _ID.fullmatch(act.id) or len(act.key) > 1:
                    raise ValueError(f"card {card.id}: the action {act.id!r} or its key {act.key!r} is not allowed")
        for cmd in parts.commands:
            if not _NAME.fullmatch(cmd.name):
                raise ValueError(f"the command name {cmd.name!r} is not lower-case letters, digits and -")
        for cf in parts.client_files:
            if not os.path.isabs(cf.source) or not _TARGET.fullmatch(cf.target) or ".." in cf.target.split("/"):
                raise ValueError(f"the client file {cf.target!r} ({cf.source!r}) is not allowed (an absolute source; a "
                                 f"relative target with /)")

    def _builtin_modules(self) -> List[Tuple[str, str, Callable[[], Plugin]]]:
        out: List[Tuple[str, str, Callable[[], Plugin]]] = []
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):    # type: ignore[name-defined]
            if info.name.startswith("_"):
                continue
            out.append((info.name, BUILTIN, self._importer(f"{__name__}.{info.name}", None, None)))
        return out

    def _folder_modules(self, folder: str) -> List[Tuple[str, str, Callable[[], Plugin]]]:
        out: List[Tuple[str, str, Callable[[], Plugin]]] = []
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return out
        for name in names:
            path = os.path.join(folder, name)
            if name.startswith(("_", ".")):
                continue
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
                out.append((name, path, self._importer(f"carl_ext_plugin_{name}", os.path.join(path, "__init__.py"),
                                                       path)))
            elif name.endswith(".py") and os.path.isfile(path):
                out.append((name[:-3], path, self._importer(f"carl_ext_plugin_{name[:-3]}", path, None)))
        return out

    @staticmethod
    def _importer(module: str, file: Optional[str], package: Optional[str]) -> Callable[[], Plugin]:
        def make() -> Plugin:
            if file is None:
                mod = importlib.import_module(module)
            else:
                spec = importlib.util.spec_from_file_location(
                    module, file, submodule_search_locations=[package] if package else None)
                if spec is None or spec.loader is None:
                    raise ImportError(f"{file} cannot be loaded")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module] = mod
                try:
                    spec.loader.exec_module(mod)
                except BaseException:
                    sys.modules.pop(module, None)
                    raise
            factory = getattr(mod, "plugin", None)
            if not callable(factory):
                raise ImportError("the module has no function plugin()")
            return factory()  # type: ignore[no-any-return]
        return make

    # ------------------------------------------------------------ the settings
    def _saved_settings(self) -> Mapping[str, Mapping[str, SettingValue]]:
        now = self._now()
        if self._saved is None or now - self._saved[0] >= SETTINGS_TTL:
            try:
                self._saved = (now, self._read())
            except Exception:                                   # config.json that cannot be read: the defaults
                self._saved = (now, {})
        return self._saved[1]

    def _settings(self, x: _Loaded) -> Dict[str, SettingValue]:
        out: Dict[str, SettingValue] = {k: s.default for k, s in x.parts.settings.items()}
        out[ENABLED] = x.plugin.default_enabled
        out.update(self._saved_settings().get(x.plugin.id, {}))
        return out

    def _on(self, x: _Loaded) -> bool:
        return bool(self._settings(x)[ENABLED])

    def _ctx(self, x: _Loaded, slots: Sequence[SlotSession] = ()) -> PluginContext:
        """What a hook gets: the settings, where a variable of the environment wins over the saved value of the setting
        that names it (the panel's rows show the saved value, which is what the user sets); the server's slots and
        their sessions where the caller gave them; what CARL tells of this computer."""
        settings = self._settings(x)
        for key, var in x.parts.setting_env.items():
            if key in x.parts.settings and self._env.get(var):
                settings[key] = self._env[var]
        return PluginContext(x.plugin.id, self.os_name, os.path.join(self.data_root, x.plugin.id), settings,
                             tuple(slots), self.computer, self.sessions)

    def _active(self) -> List[_Loaded]:
        return [x for x in self._load() if self._on(x)]

    # ------------------------------------------------------------ calling a hook
    def _problem(self, pid: str, where: str, e: BaseException) -> None:
        self._problems[(pid, where)] = _why(e)

    def _call(self, x: _Loaded, where: str, fn: Callable[[], object], default: object) -> object:
        """fn(), or `default` when it raises (the problem is recorded; a later success clears it)."""
        try:
            out = fn()
        except Exception as e:
            self._problem(x.plugin.id, where, e)
            return default
        self._problems.pop((x.plugin.id, where), None)
        return out

    @staticmethod
    def _lines(raw: object) -> Tuple[InfoLine, ...]:
        """The lines a hook gave, as InfoLines (anything else raises, which the caller records)."""
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise TypeError("lines must be a list of InfoLine")
        out = []
        for ln in raw:
            if not isinstance(ln, InfoLine):
                raise TypeError(f"{ln!r} is not an InfoLine")
            out.append(ln if ln.tone in TONES else replace(ln, tone=""))
        return tuple(out)

    # ------------------------------------------------------------ PluginHost
    def plugins(self) -> Sequence[PluginInfo]:
        out = [PluginInfo(x.plugin.id, x.plugin.title, x.source, self._on(x)) for x in self._load()]
        out += [PluginInfo(name, name, source, False, why) for name, (source, why) in sorted(self._failed.items())]
        return sorted(out, key=lambda i: i.id)

    def setting_keys(self) -> Mapping[str, Mapping[str, SettingSpec]]:
        return {x.plugin.id: {ENABLED: _enabled_spec(x.plugin.default_enabled), **x.parts.settings}
                for x in self._load()}

    def setting_help(self) -> Mapping[str, Mapping[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for x in self._load():
            if x.parts.settings:
                out[x.plugin.id] = {ENABLED: f"Whether {x.plugin.title} is on.", **x.parts.help}
        return out

    def launch_steps(self, facts: LaunchFacts) -> Sequence[LaunchStep]:
        out: List[LaunchStep] = []
        for x in self._active():
            hook = x.parts.launch
            if hook is None:
                continue
            ctx = self._ctx(x)

            def run(hook=hook, ctx=ctx) -> Optional[LaunchStep]:       # type: ignore[no-untyped-def]
                step = hook(ctx, facts)
                if step is not None and not isinstance(step, LaunchStep):
                    raise TypeError("the launch step must be a LaunchStep or None")
                return step
            step = self._call(x, "launch step", run, None)
            if isinstance(step, LaunchStep):
                out.append(step)
        return out

    def panels(self) -> Sequence[PanelView]:
        out: List[PanelView] = []
        for x in self._load():
            if not x.parts.panels:
                continue
            on, ctx, saved = self._on(x), self._ctx(x), self._settings(x)
            for panel in x.parts.panels:
                rows = []
                for r in ([] if not on else list(panel.rows)):
                    if r.key == ENABLED:
                        continue
                    spec = x.parts.settings[r.key]
                    rows.append(RowView(r.key, r.label, spec.kind, saved[r.key], spec.default,
                                        self._choices(spec, r.offered), r.help))
                switch = RowView(ENABLED, "Turned on", "bool", saved[ENABLED], x.plugin.default_enabled,
                                 ("true", "false"), "Turns the plugin on or off.")
                lines = (self._call(x, f"panel {panel.id}", lambda p=panel: self._lines(p.lines(ctx)), ())
                         if on else ())
                out.append(PanelView(section_name(x.plugin.id, panel.id), x.plugin.id, panel.id, panel.title,
                                     x.plugin.title, panel.about, (switch, *rows), lines,  # type: ignore[arg-type]
                                     panel.group, panel.lines_title))
        order = [*GROUPS, ""]
        return sorted(out, key=lambda pv: order.index(pv.group))      # a group's panels first (a stable sort)

    @staticmethod
    def _choices(spec: SettingSpec, offered: Tuple[str, ...]) -> Tuple[str, ...]:
        if spec.kind == "choice":
            return tuple(spec.choices)
        if spec.kind == "bool":
            return ("true", "false")
        return tuple(offered)

    def cards(self, slot: str, slots: Sequence[SlotSession] = ()) -> Sequence[CardView]:
        found = []
        for x in self._active():
            ctx = self._ctx(x, slots)
            for card in x.parts.cards:
                if card.slot != slot:
                    continue
                lines = self._call(x, f"card {card.id}", lambda c=card, k=ctx: self._lines(c.lines(k)), None)
                if lines is None:
                    continue
                summary = self._call(x, f"card {card.id} summary", lambda c=card, k=ctx: str(c.summary(k)), "")
                acts = tuple(ActionView(a.id, a.label, a.key) for a in card.actions)
                found.append((card.order, x.plugin.id, card.id,
                              CardView(section_name(x.plugin.id, card.id), x.plugin.id, card.id, card.title,
                                       str(summary), lines, card.slot, acts, card.min_cols,  # type: ignore[arg-type]
                                       card.stopped, card.shrinks, card.about)))
        return [c for _, _, _, c in sorted(found, key=lambda t: t[:3])]

    def request_lines(self, requests: Sequence[SlotSession], slots: Sequence[SlotSession] = ()) -> Sequence[
            Tuple[InfoLine, ...]]:
        out: List[List[InfoLine]] = [[] for _ in requests]
        for x in self._active():
            hook = x.parts.request_lines
            if hook is None:
                continue
            ctx = self._ctx(x, slots)
            got = self._call(x, "request lines", lambda h=hook, k=ctx: [self._lines(h(k, r)) for r in requests], None)
            if isinstance(got, list):
                for i, lines in enumerate(got):
                    out[i] += lines
        return [tuple(lines) for lines in out]

    def run_action(self, plugin: str, card: str, action: str) -> str:
        for x in self._active():
            if x.plugin.id != plugin:
                continue
            for c in x.parts.cards:
                if c.id == card:
                    for a in c.actions:
                        if a.id == action:
                            ctx = self._ctx(x)
                            try:
                                msg = str(a.run(ctx))
                            except Exception as e:
                                self._problem(plugin, f"card {card} action {action}", e)
                                return f"{x.plugin.title}: {a.label} failed: {_why(e)}"
                            self._problems.pop((plugin, f"card {card} action {action}"), None)
                            self.refresh()
                            return msg
        return ""

    def commands(self) -> Sequence[CommandInfo]:
        out: Dict[str, CommandInfo] = {}
        for x in self._active():
            for c in x.parts.commands:
                out.setdefault(c.name, CommandInfo(c.name, x.plugin.id, c.help, c.listed))
        return sorted(out.values(), key=lambda c: c.name)

    def run_command(self, name: str, argv: Sequence[str], out: Callable[[str], None]) -> int:
        for x in self._active():
            for c in x.parts.commands:
                if c.name == name:
                    try:
                        return int(c.run(self._ctx(x), list(argv), out))
                    except Exception as e:
                        self._problem(x.plugin.id, f"command {name}", e)
                        out(f"error: {x.plugin.title}: {name} failed: {_why(e)}")
                        return 1
        return 2

    def client_files(self) -> Sequence[ClientFile]:
        out: Dict[str, ClientFile] = {}
        for x in self._active():
            for cf in x.parts.client_files:
                out.setdefault(cf.target, cf)
        return list(out.values())

    def provided(self, name: str) -> Sequence[object]:
        out: List[object] = []
        missing = object()
        for x in self._active():
            hook = x.parts.provides.get(name)
            if hook is None:
                continue
            got = self._call(x, f"provides {name}", lambda h=hook, k=self._ctx(x): h(k), missing)
            if got is not missing:
                out.append(got)
        return out

    def health(self) -> Sequence[HealthRow]:
        rows: List[HealthRow] = []
        for name, (source, why) in sorted(self._failed.items()):
            rows.append(HealthRow(name, name, False, f"did not load: {why}"))
        for x in self._active():
            ctx, now = self._ctx(x), self._now()
            for chk in x.parts.checks:
                key = (x.plugin.id, chk.id)
                cached = self._checks.get(key)
                if cached is None or now - cached[0] >= CHECK_TTL:
                    res = self._call(x, f"check {chk.id}", lambda c=chk, k=ctx: c.run(k), None)
                    if isinstance(res, CheckResult):
                        self._checks[key] = cached = (now, res)
                    else:
                        cached = None
                if cached is not None:
                    rows.append(HealthRow(x.plugin.id, chk.label, cached[1].ok, cached[1].text))
        for (pid, where), why in sorted(self._problems.items()):
            rows.append(HealthRow(pid, pid, False, f"{where}: {why}"))
        return rows

    def set_setting(self, plugin: str, key: str, value: SettingValue) -> None:
        keys = self.setting_keys()
        if plugin not in keys or key not in keys[plugin]:
            raise ConfigError(f"plugins.{plugin}.{key}: not a setting of an installed plugin")
        self._write(plugin, key, value)
        self.refresh()

    def refresh(self) -> None:
        self._saved = None
        self._checks.clear()

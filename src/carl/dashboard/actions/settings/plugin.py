"""A plugin's panel (WP): its rows and values, and the text typed for a value."""
from __future__ import annotations

from typing import Optional

from ....domain.shared.errors import ConfigError
from ....ports.plugin import PanelView
from ....ui.fmt import R, RED
from ...act import Action, Verb
from ...keys import DOWN, ENTER, LEFTKEY, RIGHT, UP
from ...render.settings.page import plugin_value
from ...state import SUBPANELS, TextPrompt
from .context import Context


class PluginActions:
    """Acts on the plugins' panels."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def view(self) -> Optional[PanelView]:
        """The plugin panel shown (ui.sp counts from len(SUBPANELS)), None when it is gone (the plugin turned off)."""
        n = self.c.ui.sp - len(SUBPANELS)
        panels = self.c.plugin_panels()
        return panels[n] if 0 <= n < len(panels) else None

    def set(self, plugin: str, key: str, value: str) -> None:
        """plugins.PLUGIN.KEY = value, saved at once (a wrong value says why and changes nothing)."""
        host = self.c.plugins()
        if host is None:
            return
        try:
            host.set_setting(plugin, key, value)
        except ConfigError as e:
            self.c.ui.toast(f"{RED}{e}{R}", 8, error=True)

    def act(self, a: Action) -> None:
        """A click in a plugin's panel: plugrow:N selects a row, plugset:PLUGIN:KEY:VALUE sets a value."""
        ui = self.c.ui
        if a.verb is Verb.PLUGROW:
            ui.plugin_row = int(a.arg)
        else:
            plugin, key, value = a.arg.split(":", 2)
            pv = self.view()
            if pv is not None:
                ui.plugin_row = next((i for i, r in enumerate(pv.rows) if r.key == key), ui.plugin_row)
            self.set(plugin, key, value)

    def typed(self, target: str, value: str) -> None:
        """The text that was typed for a value (Enter): target is "plugin:PLUGIN:KEY"."""
        _, plugin, key = target.split(":", 2)
        self.set(plugin, key, value)

    def key(self, k: str) -> bool:
        """A plugin's panel: ↑↓ a setting, ← → the next or previous value of a setting that has choices, Enter the
        next choice or a value to type, x the default."""
        ui = self.c.ui
        pv = self.view()
        if pv is None:
            return False
        ui.plugin_row = max(0, min(ui.plugin_row, len(pv.rows) - 1))
        r = pv.rows[ui.plugin_row]
        if k == UP:
            ui.plugin_row = max(ui.plugin_row - 1, 0)
        elif k == DOWN:
            ui.plugin_row = min(ui.plugin_row + 1, len(pv.rows) - 1)
        elif k in (RIGHT, LEFTKEY) or (k in ENTER and r.choices):
            if r.choices:
                cur = plugin_value(r.value)
                i = r.choices.index(cur) if cur in r.choices else -1
                self.set(pv.plugin, r.key, r.choices[(i + (-1 if k == LEFTKEY else 1)) % len(r.choices)])
        elif k in ENTER:
            ui.text = TextPrompt(f"{r.label}:", f"plugin:{pv.plugin}:{r.key}")
        elif k == "x":
            self.set(pv.plugin, r.key, plugin_value(r.default))
        else:
            return False
        return True

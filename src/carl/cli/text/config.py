"""The text of `carl config show`: the user's settings file, then every setting of this OS with what it does, its
values and its default (cli/settings_help.py), in two columns."""
from __future__ import annotations

import json
from typing import List, Mapping, Optional, Tuple

from ...domain.config.settings import MODEL_KEYS, PLUGINS, SECTIONS, PluginKeys
from ...domain.shared.platform_words import PlatformWords
from ...domain.shared.types import JsonObject
from ...ui.layout import columns, wrap
from ..settings_help import key_help, value_text


def config_lines(cfg: JsonObject, config_file: str, words: PlatformWords, w: int, os_name: str = "macos",
                 plugin_keys: Optional[PluginKeys] = None,
                 plugin_help: Optional[Mapping[str, Mapping[str, str]]] = None) -> List[str]:
    """The settings (config_file: its path as the user writes it, with ~) and the explanation of every key of this
    OS (os_name: the profile's; a key of other OSes is left out: SettingSpec.platforms). plugin_keys, plugin_help: the
    keys of the plugins that declare settings, and what each does (the plugin host's); none: nothing is added."""
    out = [f"Your settings ({config_file}):"]
    out.extend(json.dumps(cfg, indent=2).split("\n"))
    out.append("")
    out.extend(wrap("Every setting: what it does, its values and its default. To change one: "
                    f"{words.command} config set KEY VALUE. A setting of one model: models.NAME.KEY (for example "
                    "models.gemma-4-12b.ctx).", w))
    rows: List[Tuple[str, str]] = []
    for sec, keys in SECTIONS.items():
        for k, spec in keys.items():
            if not spec.on(os_name):
                continue
            rows.append((f"{sec}.{k}", f"{key_help(f'{sec}.{k}', words)} Values: {value_text(spec)}".strip()))
    for k, spec in MODEL_KEYS.items():
        if not spec.on(os_name):
            continue
        rows.append((f"models.NAME.{k}", f"{key_help(f'models.NAME.{k}', words)} Values: "
                                         f"{value_text(spec).split('. Default')[0]}.".strip()))
    for pid, text in (plugin_help or {}).items():
        for k, spec in (plugin_keys or {}).get(pid, {}).items():
            rows.append((f"{PLUGINS}.{pid}.{k}", f"{text.get(k, '')} Values: {value_text(spec)}".strip()))
    out.extend(columns(rows, w, max_term=20))
    return out

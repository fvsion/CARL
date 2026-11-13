"""The user's settings: config.json, validated, and migrated once (from llama.env when it is missing, and from a
retired value); the models folder and the paths under the home folder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..domain.shared.paths import expand_home
from ..domain.config.settings import (Config, PluginKeys, coerce, migrate_config, migrate_env, models_dir_setting,
                                      validate_config)
from ..domain.shared.contracts import require
from ..domain.shared.errors import ConfigError
from ..domain.shared.platform_words import MACOS, PlatformWords
from ..domain.shared.types import SettingValue
from ..ports import Console, JsonDocument, LegacyEnv


@dataclass(frozen=True)
class Stores:
    """The JSON documents CARL reads and writes, and where they are (for messages)."""
    catalog: JsonDocument
    catalog_path: str
    config: JsonDocument
    local: JsonDocument
    local_path: str
    legacy: LegacyEnv


class ConfigService:
    """config.json and the paths of one user (one CONF_DIR). stores may be replaced after the start (the facade's
    stores); every other service reads it from here. Contract C-100."""

    def __init__(self, stores: Stores, console: Console, home: str, env_models_dir: Optional[str],
                 words: PlatformWords = MACOS, plugin_keys: Callable[[], Optional[PluginKeys]] = lambda: None) -> None:
        self.stores = stores
        self.console = console
        self.home = home
        self.env_models_dir = env_models_dir
        self.words = words                  # the platform words of every service's texts (the composition's)
        self.plugin_keys = plugin_keys      # the keys the installed plugins declare (None: no plugin seam here)

    def validate(self, raw: object) -> Tuple[Config, List[str]]:
        """(config, [warnings]) from a parsed config.json: the schema, and the plugins' keys when there are any."""
        return validate_config(raw, self.plugin_keys())

    def set_plugin_setting(self, plugin: str, key: str, value: SettingValue) -> Config:
        """plugins.<plugin>.<key> = value, checked against the key the plugin declares (ConfigError: not a key of
        an installed plugin, or a bad value), saved with the rest of config.json."""
        keys = self.plugin_keys() or {}
        spec = (keys.get(plugin) or {}).get(key)
        if spec is None:
            raise ConfigError(f"plugins.{plugin}.{key}: not a setting of an installed plugin")
        cfg = self.load_config()
        cfg.plugins.setdefault(plugin, {})[key] = coerce(spec, value, f"plugins.{plugin}.{key}")
        return self.save_config(cfg)

    def load_config(self) -> Config:
        """config.json, validated; migrated once from llama.env when missing, and once when it
        holds a retired value (a note on stderr says what changed)."""
        raw = self.stores.config.load()
        if raw is not None:
            fixed, notes = migrate_config(raw, self.words)
            cfg = self.validate(fixed)[0]
            if notes:
                self.save_config(cfg)
                for n in notes:
                    self.console.error(f"note: {n}")
            return cfg
        migrated = migrate_env(self.stores.legacy.read())
        if migrated is None:
            return Config()
        return self.save_config(migrated)

    def config_warnings(self) -> List[str]:
        """What config.json holds that CARL ignores (unknown or removed keys)."""
        raw = self.stores.config.load()
        return self.validate(raw)[1] if raw is not None else []

    def save_config(self, cfg: Config) -> Config:
        require(isinstance(cfg, Config), "C-100", "the settings are a checked Config")
        self.stores.config.save(cfg.to_file(self.words))
        return cfg

    def parse_config(self, cfg: Optional[object]) -> Config:
        """A config passed in as a dict (the public API), or config.json when None."""
        return self.load_config() if cfg is None else self.validate(cfg)[0]

    def swa_mode(self) -> str:
        """cache.swa (auto / full / window): how sliding-window models are planned; auto when the
        config can't be read."""
        try:
            return str(self.load_config().cache.get("swa") or "auto")
        except (ConfigError, OSError, ValueError):
            return "auto"

    def models_dir(self, cfg: Config) -> str:
        return self.expand(models_dir_setting(self.env_models_dir, cfg))

    def expand(self, path: str) -> str:
        return expand_home(path, self.home)

"""The KEY=value lines the launchers read (serve-llama.sh: launch-env, serve.sh: mtplx-env).

The shell scripts read these lines with `read`/`printf -v`, never eval, and every value is
also restricted to a safe character set, so a config value can't inject shell syntax.
"""
from __future__ import annotations

import re
from typing import Dict, Mapping

from .errors import ConfigError
from .settings import LLAMA_KEYS, MODEL_KEYS, MTPLX_KEYS, Config
from .types import ModelInfo, SettingSource, SettingValue, Settings

_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.,:/~+@ =-]*")
_SAFE_KEY = re.compile(r"[A-Z_][A-Z0-9_]*")
SOURCE_KEYS = ("ctx", "kv", "spec", "slots")       # reported in CARL_SOURCES


def shell_value(v: SettingValue) -> str:
    return "1" if v is True else "0" if v is False else str(v)


def shell_lines(env: Mapping[str, SettingValue]) -> str:
    """KEY=value lines; raises ConfigError on a key or value outside the safe character set."""
    out = []
    for k, v in env.items():
        s = shell_value(v)
        if not _SAFE_KEY.fullmatch(k):
            raise ConfigError(f"{k!r}: not a variable name")
        if not _SAFE_VALUE.fullmatch(s):
            raise ConfigError(f"{k}: unsafe characters in {s!r}")
        out.append(f"{k}={s}")
    return "\n".join(out)


def launch_env(m: ModelInfo, vals: Settings, src: Mapping[str, SettingSource], cfg: Config) -> Dict[str, SettingValue]:
    """Settings for serve-llama.sh: the model, its effective tune and the server-wide
    config values that are set (the script applies flags and environment on top)."""
    env: Dict[str, SettingValue] = {"MODEL": m.get("path", ""), "MODEL_NAME": m.get("name", ""), "ALIAS": vals["alias"]}
    for k, s in MODEL_KEYS.items():
        if k != "alias" and s.env:
            env[s.env] = vals[k]
    for k, s in LLAMA_KEYS.items():
        v = cfg.llama.get(k)
        if v is not None and s.env and v != "" and not (k == "cache_ram" and v == "auto"):
            env[s.env] = v
    extra = cfg.llama.get("extra_args")
    if isinstance(extra, list) and extra:
        env["EXTRA_ARGS"] = " ".join(extra)
    env["CARL_SOURCES"] = " ".join(f"{k}:{src[k]}" for k in SOURCE_KEYS)
    return env


def mtplx_env(cfg: Config) -> Dict[str, SettingValue]:
    """Settings for serve.sh grant|pocket: the mtplx values config.json sets."""
    return {s.env: cfg.mtplx[k] for k, s in MTPLX_KEYS.items() if s.env and cfg.mtplx.get(k) not in (None, "")}

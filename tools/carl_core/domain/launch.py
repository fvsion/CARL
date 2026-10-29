"""The KEY=value lines the launcher reads (serve-llama.sh: tools/carl.py launch-env).

The shell scripts read these lines with `read`/`printf -v`, never eval, and every value is
also restricted to a safe character set, so a config value can't inject shell syntax.
"""
from __future__ import annotations

import re
from typing import Dict, Mapping, Optional

from .errors import ConfigError
from .models import MtpSource
from .settings import LLAMA_KEYS, MODEL_KEYS, Config
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


def launch_env(m: ModelInfo, vals: Settings, src: Mapping[str, SettingSource], cfg: Config,
               swa: bool = False, mtp: Optional[MtpSource] = None) -> Dict[str, SettingValue]:
    """Settings for serve-llama.sh: the model, its effective tune and the server-wide
    config values that are set (the script applies flags and environment on top). swa: the
    model has sliding-window layers: SWA_MODE (cache.swa: auto, full, window; llama-fit --plan
    decides auto, and the launcher adds --swa-full for full). mtp: where its MTP speculation
    comes from (None: unknown): MTP_SOURCE, and DRAFT = the drafter's file when that is the
    source (the launcher passes -md DRAFT when SPEC uses draft-mtp, also a SPEC from the
    environment)."""
    env: Dict[str, SettingValue] = {"MODEL": m.get("path", ""), "MODEL_NAME": m.get("name", ""), "ALIAS": vals["alias"]}
    if mtp is not None:
        env["MTP_SOURCE"] = mtp
    if mtp == "drafter":
        env["DRAFT"] = m.get("draft_path", "")
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
    if swa:
        env["SWA_MODE"] = cfg.cache.get("swa") or "auto"
    env["CARL_SOURCES"] = " ".join(f"{k}:{src[k]}" for k in SOURCE_KEYS)
    return env


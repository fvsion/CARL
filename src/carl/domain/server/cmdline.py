"""A running llama-server's command line, read back into settings (contract C-134): the Settings tab's "running now"
values, and the environment that starts the same server again (the dashboard's rollback after a start that
failed). The launcher's variable of each setting comes from the settings schema (domain/config/settings.py), so
CLEAN_ENV, the variables a start from the dashboard drops from the environment it inherits, is the schema's too.
Pure."""
from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from ..config.settings import LLAMA_KEYS, MODEL_KEYS, SettingSpec
from ..shared import constants
from ..shared.paths import policy_for
from .launch_plan import LLAMA_DEVICES, N_CPU_MOE
from .snapshot import ModelInfo, ServerData, flag, flag_int

# The launcher's own variables of a start, beside the settings': the model file, its name in config.json, the two
# halves of the context memory type, the settings file (none: config.json is not read), and (a PC) the devices the
# server runs on (-dev: llama.cpp's names; the split over them is planned again, -ts is not kept) and the layers whose
# MoE experts stay in the RAM (--n-cpu-moe: llama.n_cpu_moe's variable, so the start keeps the same N; W6.7).
MODEL_FILE, MODEL_NAME, KV_K, KV_V, SETTINGS_FILE = "MODEL", "MODEL_NAME", "KV_K", "KV_V", "SETTINGS_FILE"

# The settings the rollback puts back from the command line: (the schema's entry, its flags, the value when the flag
# is missing; "": left to the start's own default).
ROLLBACK: Tuple[Tuple[SettingSpec, Tuple[str, ...], str], ...] = (
    (LLAMA_KEYS["host"], ("--host",), "127.0.0.1"),
    (MODEL_KEYS["slots"], ("--parallel", "-np"), "1"),
    (MODEL_KEYS["temp"], ("--temp",), str(constants.TEMP)),
    (MODEL_KEYS["top_p"], ("--top-p",), str(constants.TOP_P)),
    (MODEL_KEYS["top_k"], ("--top-k",), str(constants.TOP_K)),
    (MODEL_KEYS["min_p"], ("--min-p",), str(constants.MIN_P)),
    (MODEL_KEYS["presence"], ("--presence-penalty",), str(constants.PRESENCE)),
    (MODEL_KEYS["repeat"], ("--repeat-penalty",), ""),
    (LLAMA_KEYS["cache_ram"], ("--cache-ram",), ""),
    (MODEL_KEYS["alias"], ("--alias",), ""),
    (MODEL_KEYS["spec"], ("--spec-type",), "none"),
    (MODEL_KEYS["spec_n"], ("--spec-draft-n-max",), "1"),
    (LLAMA_KEYS["ub"], ("-ub",), ""),
    (LLAMA_KEYS["ckpt"], ("--ctx-checkpoints",), ""),
    (LLAMA_KEYS["ckpt_step"], ("--checkpoint-min-step",), ""),
)


def _env(spec: SettingSpec) -> str:
    assert spec.env, "a setting the launcher reads has a variable"
    return spec.env


# A start from the dashboard takes these from config.json (or the rollback's own environment), not from the
# environment the dashboard started in: the settings the rollback restores, the context and its memory type, the
# network, the mode (the Router panel), and the launcher's own variables of a start.
CLEAN_ENV: Tuple[str, ...] = tuple(dict.fromkeys(
    [_env(s) for s, _, _ in ROLLBACK] + [_env(MODEL_KEYS["ctx"]), _env(MODEL_KEYS["kv"])]
    + [_env(LLAMA_KEYS["net"]), _env(LLAMA_KEYS["mode"])]
    + [MODEL_FILE, MODEL_NAME, KV_K, KV_V, LLAMA_DEVICES, N_CPU_MOE]))
CPU_MOE_FLAGS = ("--n-cpu-moe", "-ncmoe")


def env_from_cmd(cmd: str) -> Dict[str, str]:
    """Environment that makes the launcher start the same llama.cpp server as cmd (the rollback): config.json is not
    read; a value the command line does not give is left out."""
    def f(*names: str, default: str = "") -> str:
        return flag(cmd, *names) or default
    ctx = f("--kv-unified-per-slot") or f("-c", "--ctx-size", default=str(constants.DEFAULT_CTX))
    e = {SETTINGS_FILE: "none", MODEL_FILE: f("-m", "--model"), _env(MODEL_KEYS["ctx"]): ctx,
         KV_K: f("-ctk", default="q4_0"), KV_V: f("-ctv", default="q4_0"), LLAMA_DEVICES: f("-dev", "--device"),
         N_CPU_MOE: f(*CPU_MOE_FLAGS)}
    for spec, flags, default in ROLLBACK:
        e[_env(spec)] = f(*flags, default=default)
    return {k: v for k, v in e.items() if v}


def running_settings(d: ServerData, vm_addr: str, find: Callable[[str], Optional[ModelInfo]]) -> Dict[str, object]:
    """The values the running llama.cpp server uses, by Settings row key, from its command line ({} when none runs).
    find: a model by name or file."""
    cmd = d.cmd
    host = flag(cmd, "--host", default="") or ""
    net = "vm" if host == vm_addr else "local" if host in ("127.0.0.1", "::1") else host or "N/A"
    if "llama-server" not in cmd:
        return {}
    mpath = flag(cmd, "-m", "--model") or ""
    base = policy_for(mpath).basename(mpath) if mpath else ""
    m = (find(mpath) if mpath else None) or (find(base) if base else None)

    def f(*names: str, default: str = "N/A") -> str:
        return flag(cmd, *names) or default

    return {"model": m["name"] if m else (base or "N/A"),
            "kv": f("-ctk", "--cache-type-k", default="f16"),
            "ctx": flag_int(cmd, "--kv-unified-per-slot", default=0) or d.n_ctx or "N/A",
            "slots": f("--parallel", "-np", default="1"), "cache": flag_int(cmd, "--cache-ram", default=0),
            "net": net, "temp": f("--temp"), "presence": f("--presence-penalty"),
            "spec": f("--spec-type", default="none"), "specn": f("--spec-draft-n-max", default="1"),
            "top_k": f("--top-k"), "top_p": f("--top-p"), "min_p": f("--min-p"), "repeat": f("--repeat-penalty"),
            "ub": f("-ub"), "ckpt": f("--ctx-checkpoints"), "ckstep": f("--checkpoint-min-step"),
            "swa": "full" if "--swa-full" in cmd else "window",
            "devices": f("-dev", "--device", default=""), "split": f("-ts", "--tensor-split", default=""),
            "cpu_moe": flag_int(cmd, *CPU_MOE_FLAGS, default=0)}

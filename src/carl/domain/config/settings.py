"""The settings schema, value validation and config.json (validation, migration, key paths).

config.json sections: llama (server-wide), models.<name> (per-model profile), paths, cache, delegation, gpu,
engine.
Every value is validated against its SettingSpec; unknown keys are ignored with a warning
so a newer config.json still loads in an older CARL. Keys of removed features (see
REMOVED) are ignored too and disappear the next time the file is saved.

A key's platforms say on which computers it does something (W6.1): the gpu keys, llama.path and llama.n_cpu_moe
are for Windows and Linux. Every computer reads and validates them, but only their platforms list them (config
show, launch-env), so the Mac's texts stay as they were.

A plugin adds keys of its own (WP): config.json "plugins.<id>.<key>", checked against the keys the plugin declares
(PluginKeys, which the caller hands in: the domain never imports a plugin). The section of a plugin that is not
installed is kept as it is and written back, so its settings survive. Without PluginKeys, "plugins" is an unknown
section, as before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from ..shared.constants import (DEFAULT_CTX, LOAD_RESERVE_GIB, MIN_P, PRESENCE, REPEAT, TEMP, TOP_K, TOP_P, UBATCH,
                                UBATCH_PC)
from ..shared.errors import ConfigError
from ..shared.platform_words import MACOS, PlatformWords
from ..catalog.drafts import FILE_SPECS
from ..catalog.thinking import CODER_VALUES, MAIN, MAIN_VALUES, Deferred
from ..hardware.devices import GIB, VRAM_KEEP
from ..llama.build import BACKENDS
from ..server.engines import AUTO as ENGINE_AUTO, ENGINES
from ..shared.types import JsonObject, JsonValue, SettingValue, Settings

SCHEMA = 1
DEFAULT_MODELS_DIR = "~/models/gguf"


def config_comment(words: PlatformWords = MACOS) -> str:
    """config.json's "_comment", for people who edit the file."""
    return ("CARL settings. Change them here or in the Settings tab of the dashboard. "
            "Sections: llama, models.<name> (the settings of one model), paths, cache (the disk cache), "
            "delegation (the hand-off to the coder). "
            "Order: flags > environment > this file > Auto-tune > catalogue. "
            f"{words.command} config show lists every setting.")


CONFIG_COMMENT = config_comment()
# Top-level keys of removed features: ignored on load (None = silently, else this warning)
# and dropped on the next save. "backend" chose llama.cpp or MTPLX before 1.2.0.
REMOVED: Dict[str, Optional[str]] = {
    "backend": None,
    "mtplx": "mtplx: MTPLX support was removed in CARL 1.2.0 (ignored; dropped when the settings are next saved)",
}

PLUGINS = "plugins"                            # config.json's section of the plugins' settings (plugins.<id>.<key>)
SettingType = Literal["int", "float", "intauto", "bool", "list", "choice", "str"]
Number = Union[int, float]
PluginKeys = Mapping[str, Mapping[str, "SettingSpec"]]      # plugin id -> its keys (the plugin port's setting_keys)
OFF_MAC = ("windows", "linux")                 # the platforms of a key for the computers with a discrete GPU


@dataclass(frozen=True)
class SettingSpec:
    """One setting: its type, default, allowed values and the launcher variable it maps to."""
    kind: SettingType
    default: SettingValue
    env: Optional[str] = None
    choices: Sequence[str] = ()                # a Deferred one is read when asked for (the thinking values)
    min: Optional[Number] = None
    max: Optional[Number] = None
    platforms: Tuple[str, ...] = ()            # the OSes it is for (macos, windows, linux); (): every OS
    items: str = ""                            # a list's items: "index" = whole numbers from 0 (gpu.devices)
    listed: bool = True                        # False: the key works, but no text lists it (engine.name, until a
    #                                            second engine exists)

    def on(self, os_name: str) -> bool:
        """The key does something on this OS (macos, windows, linux), so its texts list it."""
        return self.listed and (not self.platforms or os_name in self.platforms)

    def to_dict(self) -> Dict[str, JsonValue]:
        """The dict form the public API (carl.MODEL_KEYS) has always exposed."""
        out: Dict[str, JsonValue] = {"type": self.kind}
        if self.choices:
            out["choices"] = list(self.choices)
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        out["default"] = to_json(self.default)
        if self.env:
            out["env"] = self.env
        if self.platforms:
            out["platforms"] = list(self.platforms)
        return out


def _choice(default: str, choices: Sequence[str], env: Optional[str] = None) -> SettingSpec:
    return SettingSpec("choice", default, env, choices if isinstance(choices, Deferred) else tuple(choices))


def _int(default: int, lo: int, hi: int, env: Optional[str] = None) -> SettingSpec:
    return SettingSpec("int", default, env, min=lo, max=hi)


def _float(default: Number, lo: Number, hi: Number, env: str) -> SettingSpec:
    return SettingSpec("float", default, env, min=lo, max=hi)


def _str(default: str = "", env: Optional[str] = None) -> SettingSpec:
    return SettingSpec("str", default, env)


NET_CHOICES = ("local", "vm")
# Before 1.3.0 the default network mode was "auto": the VM address whenever VMware's network
# was up, which exposed the server to the VM network without being asked. A saved "auto" is
# converted to the default (local) once, with this note (migrate_config).
def net_auto_note(words: PlatformWords = MACOS) -> str:
    cmd = words.command
    return (f"llama.net: 'auto' was removed in CARL 1.3.0; the server now listens on {words.machine} only "
            f"(127.0.0.1). For a VM client: {cmd} --vm, or {cmd} config set llama.net vm")


NET_AUTO_NOTE = net_auto_note()

# Per-model settings (config.json "models.<name>", Auto-tune, catalogue "tune").
MODEL_KEYS: Dict[str, SettingSpec] = {
    "kv": _choice("q4_0", ("q4_0", "q8_0", "f16"), "KV"),
    "ctx": _int(DEFAULT_CTX, 4096, 262144, "CTX"),
    "slots": _choice("auto", ("auto", "1", "2", "3", "4"), "SLOTS"),    # 3-4: only where they fit (the start check)
    # none, MTP, n-gram and both (the first three are indexed by launch_plan), then a drafter file of another kind (DFlash,
    # DSpark, EAGLE-3: catalog/drafts.py FILE_SPECS; WCATX) alone or with n-gram. A kind needs its file and an engine that
    # supports it (llama/spec_support.py): without either the start falls back to n-gram.
    "spec": _choice("draft-mtp,ngram-mod", ("none", "draft-mtp", "ngram-mod", "draft-mtp,ngram-mod", *FILE_SPECS,
                                           *(f"{k},ngram-mod" for k in FILE_SPECS)), "SPEC"),
    "spec_n": _int(1, 1, 8, "SPEC_N"),
    "temp": _float(TEMP, 0, 2, "TEMP"),
    "top_p": _float(TOP_P, 0, 1, "TOP_P"),
    "top_k": _int(TOP_K, 0, 1000, "TOP_K"),
    "min_p": _float(MIN_P, 0, 1, "MIN_P"),
    "presence": _float(PRESENCE, 0, 2, "PRESENCE"),
    "repeat": _float(REPEAT, 0.5, 2, "REPEAT"),
    "alias": _str("", "ALIAS"),
    # -ub, the model's own choice (A51): the Batch size row, or the answer Keep to the start's question. ub_tune: the
    # tune record it was made against (inventory.tune_stamp): a later tune asks again. Neither is a launcher variable:
    # ub_order.py gives the start its -ub.
    "ub": _int(UBATCH, 64, 8192),
    "ub_tune": SettingSpec("str", "", listed=False),             # a stamp for the code, no text lists it
    # Thinking per role (Phase 23.4.4 item 11; domain/thinking.py): the clients' setting, not the server's (no
    # variable). The default depends on the model: thinking.defaults() is its catalogue layer (effective_tune). The
    # coder's "main": as the main session (the default).
    "thinking_main": _choice("on", MAIN_VALUES),
    "thinking_coder": _choice(MAIN, CODER_VALUES),
}
# Server-wide llama.cpp settings (config.json "llama").
LLAMA_KEYS: Dict[str, SettingSpec] = {
    "model": _str("auto"),                                  # auto = auto fit's pick for this Mac
    "auto_goal": _choice("everyday", ("everyday", "hard-code")),        # auto fit: fast first (MoE, small dense), or dense first
    "auto_fit": _choice("catalogue", ("catalogue", "downloaded")),      # auto fit picks from these models
    "mode": _choice("single", ("single", "router"), "LLAMA_MODE"),   # router: clients switch models (opt-in)
    "net": _choice("local", NET_CHOICES, "NET"),
    "host": _str("", "HOST"),                               # one address of this Mac (wins over net)
    "cache_ram": SettingSpec("intauto", "auto", "CACHE_RAM", min=0, max=65536),
    # the dashboard API's port (Phase 23.5): auto = the server's port + 1, else the next free one; or a fixed port
    "api_port": SettingSpec("intauto", "auto", None, min=1024, max=65535),
    # -ub for every model: how config.json held it before A51. Read as the value the start's question asks about for a
    # model with a tune record, and used for a model without one; the Batch size row and the answer Keep set the
    # model's own (models.NAME.ub). The environment's UB is a typed -ub.
    "ub": _int(UBATCH, 64, 8192, "UB"),
    "batch": _int(2048, 64, 16384, "BATCH"),
    "ckpt": _int(8, 0, 64, "CKPT"),
    "ckpt_step": _int(4096, 256, 65536, "CKPT_STEP"),
    "think_toggle": SettingSpec("bool", True, "THINK_TOGGLE"),
    # The vision projector (WCATX, A74): on, a start passes --mmproj with the model's projector when it is downloaded; off
    # (the default), --no-mmproj. The projector costs about 1 GB of memory, which the memory plan counts.
    "vision": SettingSpec("bool", False, "VISION"),
    "extra_args": SettingSpec("list", []),                  # passed to llama-server as-is
    # Load mode none (the weights read into the RAM) when the free RAM covers the server's RAM and this many GiB more;
    # else the mapped load (A44, A45; domain/memory/fit.py LoadMode)
    "load_reserve_gib": SettingSpec("float", LOAD_RESERVE_GIB, "LOAD_RESERVE_GIB", min=0, max=1024),
    # Off the Mac (W6.1 adds them; W6.2 and W6.7 wire them): the folder of the llama.cpp build (CARL_LLAMA_DIR wins),
    # and the layers whose MoE experts stay in the RAM (--n-cpu-moe; auto: CARL plans it).
    "path": SettingSpec("str", "", "CARL_LLAMA_DIR", platforms=OFF_MAC),
    "n_cpu_moe": SettingSpec("intauto", "auto", "N_CPU_MOE", min=0, max=999, platforms=OFF_MAC),
}


def default_ub(os_name: str) -> int:
    """The -ub of a start when no one set it and the model has no Auto-tune record: a Mac's 512, a PC's 2048
    (constants.py UBATCH_PC; decisions A44 and A45; ub_order.py has the order)."""
    return UBATCH if os_name == "macos" else UBATCH_PC


PATH_KEYS: Dict[str, SettingSpec] = {"models_dir": _str(DEFAULT_MODELS_DIR)}
# The disk cache in ~/.config/carl/slots (the dashboard's Settings > Caching panel): each agent's
# prompt and each session, saved by OpenCode and Pi (client/shared/carl-cache.js), within disk_gb
# (the oldest files go first). save: when a session is saved (auto = when the part not saved yet
# would take auto_s seconds to read again, and before it leaves the server). swa: the cache of models with
# sliding-window layers (full = restores work, more memory; auto = full when it fits). share:
# conversations stored as patches against their agent's prompt file (adapters/common/zstd_patch.py).
SAVE_CHOICES = ("auto", "turn", "switch", "stop")
SWA_CHOICES = ("auto", "full", "window")
# move: where the parts of the prompt that change per project go for a template CARL does not know (not Qwen, not
# Gemma 4): off (the default) = left in place, system text, but each new project reads the whole prompt; auto = the
# start of the first user message, so one saved prompt serves every project. Qwen and Gemma 4 are not changed by it
# (Phase 23.1).
MOVE_CHOICES = ("auto", "off")
CACHE_KEYS: Dict[str, SettingSpec] = {
    "disk_gb": _int(10, 1, 1000),
    "prefix": SettingSpec("bool", True),
    "sessions": SettingSpec("bool", True),
    "save": _choice("auto", SAVE_CHOICES),
    "auto_s": _int(120, 10, 3600),
    "share": SettingSpec("bool", True),
    "swa": _choice("auto", SWA_CHOICES, "SWA_MODE"),
    "move": _choice("off", MOVE_CHOICES),
}
# The hand-off to the coder (carl-delegation in OpenCode and Pi): gate = stop the main agent's write that makes its
# Nth new file in a turn and tell it to use the coder (0 = off, the default). Advanced, not recommended: a setting of
# the dashboard only (Connect > Setup; user, 2026-10-08); the clients read it from the dashboard (Phase 23.4).
DELEGATION_KEYS: Dict[str, SettingSpec] = {
    "gate": _int(0, 0, 99),
}
# The discrete GPUs (W6.1; Windows and Linux): vram_keep = the GiB kept free on each GPU (decisions 6 and A43), backend = the
# llama.cpp build (auto: CARL chooses), devices = CARL's numbers of the GPUs
# a model uses (carl gpu lists them; empty: every GPU). shared_limit_gib = the GiB a model may use on a GPU that shares
# the RAM (an APU; WAPU, decision A37), the carve-out included; 0: CARL works it out from
# the driver's figures. domain/memory/budget.py reads them; the variable wins.
GPU_KEYS: Dict[str, SettingSpec] = {
    "vram_keep": SettingSpec("float", VRAM_KEEP / GIB, "CARL_VRAM_KEEP_GIB", min=0, max=64, platforms=OFF_MAC),
    "backend": SettingSpec("choice", "auto", "CARL_GPU_BACKEND", BACKENDS, platforms=OFF_MAC),
    "devices": SettingSpec("list", [], "CARL_GPU_DEVICES", platforms=OFF_MAC, items="index"),
    "shared_limit_gib": SettingSpec("float", 0.0, "CARL_SHARED_LIMIT_GIB", min=0, max=1024, platforms=OFF_MAC),
}
# The engine that serves a model (WCLI, decision A39): auto = the engine on the model's card, else llama.cpp; or a name
# of domain/server/engines.py. CARL_ENGINE wins over it. With llama.cpp the only engine, no text lists the key
# (config show, the dashboard's rows): it is there for the second engine.
ENGINE_KEYS: Dict[str, SettingSpec] = {
    "name": SettingSpec("choice", ENGINE_AUTO, "CARL_ENGINE", (ENGINE_AUTO,) + ENGINES, listed=len(ENGINES) > 1),
}
SECTIONS: Dict[str, Dict[str, SettingSpec]] = {"llama": LLAMA_KEYS, "paths": PATH_KEYS, "cache": CACHE_KEYS,
                                               "delegation": DELEGATION_KEYS, "gpu": GPU_KEYS, "engine": ENGINE_KEYS}


def to_json(v: SettingValue) -> JsonValue:
    """A setting value as a JSON value (a new list, so callers can't alias the schema's)."""
    return list(v) if isinstance(v, list) else v


def _parse_int(v: object, allow_k: bool) -> int:
    if isinstance(v, str):
        if allow_k and v.lower().endswith("k"):
            return int(float(v[:-1]) * 1024)
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    raise TypeError(type(v).__name__)


def _parse_float(v: object) -> float:
    if isinstance(v, (str, int, float)):
        return float(v)
    raise TypeError(type(v).__name__)


def _convert(spec: SettingSpec, v: object, where: str) -> SettingValue:
    if spec.kind == "int":
        return _parse_int(v, allow_k=True)
    if spec.kind == "float":
        return _parse_float(v)
    if spec.kind == "intauto":
        return "auto" if str(v) == "auto" else _parse_int(v, allow_k=False)
    if spec.kind == "bool":
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    if spec.kind == "list":
        items = v if isinstance(v, list) else str(v).split()
        out = [str(x) for x in items]
        if spec.items == "index":                  # gpu.devices: "0 1" or "0,1" (CARL_GPU_DEVICES)
            out = [x for item in out for x in item.split(",") if x]
            if not all(x.isdigit() for x in out):
                raise ConfigError(f"{where}: {v!r} is not a list of GPU numbers (for example 0 1)")
        return out
    if spec.kind == "choice":
        s = str(v)
        if s not in spec.choices:
            raise ConfigError(f"{where}: {s!r} is not one of {', '.join(spec.choices)}")
        return s
    return str(v)


def coerce(spec: SettingSpec, v: object, where: str) -> SettingValue:
    """Validate and normalize one value against its schema entry ("64k" = 65536)."""
    try:
        out = _convert(spec, v, where)
    except (TypeError, ValueError, OverflowError) as e:
        if isinstance(e, ConfigError):
            raise
        raise ConfigError(f"{where}: {v!r} is not a valid {spec.kind}") from None
    if isinstance(out, (int, float)) and not isinstance(out, bool):
        lo = spec.min if spec.min is not None else out
        hi = spec.max if spec.max is not None else out
        if out < lo or out > hi:
            raise ConfigError(f"{where}: {out} is out of range {spec.min}..{spec.max}")
    return out


@dataclass
class Config:
    """A validated config.json. Absent values mean "not set" (a lower layer decides)."""
    llama: Settings = field(default_factory=dict)
    paths: Settings = field(default_factory=dict)
    cache: Settings = field(default_factory=dict)
    delegation: Settings = field(default_factory=dict)
    models: Dict[str, Settings] = field(default_factory=dict)
    gpu: Settings = field(default_factory=dict)
    engine: Settings = field(default_factory=dict)
    plugins: Dict[str, Settings] = field(default_factory=dict)     # plugins.<id>: the settings of an installed plugin
    kept: JsonObject = field(default_factory=dict)                 # the sections of plugins that are not installed

    def section(self, name: str) -> Settings:
        return {"llama": self.llama, "paths": self.paths, "cache": self.cache, "delegation": self.delegation,
                "gpu": self.gpu, "engine": self.engine}[name]

    def profile(self, model: str) -> Settings:
        """The per-model settings for one model ({} when there are none)."""
        return self.models.get(model) or {}

    def to_json(self) -> JsonObject:
        """The dict form load_config() returns (schema first, empty sections left out)."""
        out: JsonObject = {"schema": SCHEMA}
        for name in SECTIONS:
            sec = self.section(name)
            if sec:
                out[name] = {k: to_json(v) for k, v in sec.items()}
        models: JsonObject = {n: {k: to_json(v) for k, v in p.items()} for n, p in self.models.items() if p}
        if models:
            out["models"] = models
        plugins: JsonObject = {n: {k: to_json(v) for k, v in p.items()} for n, p in self.plugins.items() if p}
        plugins.update(self.kept)
        if plugins:
            out[PLUGINS] = plugins
        return out

    def to_file(self, words: PlatformWords = MACOS) -> JsonObject:
        """What save_config() writes: the dict form with a comment for people editing it."""
        doc = self.to_json()
        return {"schema": SCHEMA, "_comment": config_comment(words),
                **{k: v for k, v in doc.items() if k != "schema"}}


def _validate_section(raw: object, keys: Mapping[str, SettingSpec], where: str, warn: List[str]) -> Settings:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a JSON object")
    out: Settings = {}
    for k, v in raw.items():
        if k not in keys:
            warn.append(f"{where}.{k}: unknown setting (ignored)")
            continue
        out[str(k)] = coerce(keys[k], v, f"{where}.{k}")
    return out


def _validate_plugins(cfg: Config, raw: object, keys: PluginKeys, warn: List[str]) -> None:
    """plugins.<id>.<key> against the keys each installed plugin declares; the section of a plugin that is not
    installed is kept (a note says so), so the settings come back when the plugin does."""
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ConfigError("plugins: must be a JSON object")
    for pid, sec in raw.items():
        if str(pid) in keys:
            cfg.plugins[str(pid)] = _validate_section(sec, keys[str(pid)], f"plugins.{pid}", warn)
        elif isinstance(sec, dict):
            cfg.kept[str(pid)] = sec
            warn.append(f"plugins.{pid}: this plugin is not installed (its settings are kept)")
        else:
            raise ConfigError(f"plugins.{pid}: must be a JSON object")


def validate_config(raw: object, plugin_keys: Optional[PluginKeys] = None) -> Tuple[Config, List[str]]:
    """(config, [warnings]) from a parsed config.json. Raises ConfigError on a bad value. plugin_keys: the keys the
    installed plugins declare (None: there is no plugin seam here, and "plugins" is an unknown section)."""
    if not isinstance(raw, dict):
        raise ConfigError("config.json must hold a JSON object")
    cfg = Config()
    warn: List[str] = []
    for key, val in raw.items():
        if key in ("schema", "_comment"):
            continue
        if key in REMOVED:
            note = REMOVED[key]
            if note:
                warn.append(note)
        elif key in SECTIONS:
            cfg.section(key).update(_validate_section(val, SECTIONS[key], key, warn))
        elif key == PLUGINS and plugin_keys is not None:
            _validate_plugins(cfg, val, plugin_keys, warn)
        elif key == "models":
            if val is None:
                continue
            if not isinstance(val, dict):
                raise ConfigError("models: must be a JSON object")
            for name, prof in val.items():
                cfg.models[str(name)] = _validate_section(prof, MODEL_KEYS, f"models.{name}", warn)
        else:
            warn.append(f"{key}: unknown section (ignored)")
    return cfg, warn


def migrate_config(raw: object, words: PlatformWords = MACOS) -> Tuple[object, List[str]]:
    """(raw config.json with retired values converted, [notes]): the caller saves it when there are
    notes, so each note shows once. Today: llama.net "auto" -> unset (= local)."""
    if not isinstance(raw, dict):
        return raw, []
    llama = raw.get("llama")
    if isinstance(llama, dict) and llama.get("net") == "auto":
        return {**raw, "llama": {k: v for k, v in llama.items() if k != "net"}}, [net_auto_note(words)]
    return raw, []


def _env_to_key(keys: Mapping[str, SettingSpec]) -> Dict[str, str]:
    return {s.env: k for k, s in keys.items() if s.env}


def migrate_env(llama_env: Mapping[str, str]) -> Optional[Config]:
    """config.json from the old llama.env KEY=value file (written by earlier monitors).
    None when there is nothing to migrate or the old values are invalid."""
    if not llama_env:
        return None
    raw: Dict[str, Dict[str, object]] = {}
    env_llama, env_model = _env_to_key(LLAMA_KEYS), _env_to_key(MODEL_KEYS)
    model = llama_env.get("MODEL_NAME", "auto")
    profile: Dict[str, object] = {}
    for e, v in llama_env.items():
        if e == "MODEL_NAME":
            raw.setdefault("llama", {})["model"] = v
        elif e in env_llama:
            raw.setdefault("llama", {})[env_llama[e]] = v
        elif e in env_model and model != "auto":   # per-model values need a named model
            profile[env_model[e]] = v
    doc: Dict[str, object] = dict(raw)
    if profile:
        doc["models"] = {model: profile}
    try:
        return validate_config(migrate_config(doc)[0])[0]
    except ConfigError:
        return None


def models_dir_setting(env_dir: Optional[str], cfg: Config) -> str:
    """The models folder (unexpanded): MODELS_DIR > paths.models_dir > ~/models/gguf."""
    configured = cfg.paths.get("models_dir")
    return env_dir or (configured if isinstance(configured, str) and configured else DEFAULT_MODELS_DIR)


# ---------------------------------------------------------------- config get/set/unset KEY
def key_path(key: str) -> List[str]:
    """"llama.net" -> [llama, net]; model names may contain dots: models.a.b.ctx -> [models, a.b, ctx]."""
    parts = key.split(".")
    if parts[0] == "models" and len(parts) > 3:
        parts = ["models", ".".join(parts[1:-1]), parts[-1]]
    if any(not p for p in parts):
        raise ConfigError(f"{key}: not a setting name (like llama.net or models.NAME.ctx)")
    return parts


def get_path(doc: JsonObject, parts: Sequence[str]) -> JsonValue:
    """The value at a key path, None when any part is missing."""
    node: JsonValue = doc
    for p in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


def _parent(doc: JsonObject, parts: Sequence[str]) -> Optional[JsonObject]:
    """The object holding the last part of a key path, None when it doesn't exist."""
    node = doc
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):
            return None
        node = nxt
    return node


def set_path(doc: JsonObject, parts: Sequence[str], value: JsonValue) -> None:
    """Set a value, creating the sections on the way."""
    node = doc
    for p in parts[:-1]:
        nxt = node.setdefault(p, {})
        if not isinstance(nxt, dict):
            raise ConfigError(f"{'.'.join(parts)}: {p} is not a section")
        node = nxt
    node[parts[-1]] = value


def unset_path(doc: JsonObject, parts: Sequence[str]) -> None:
    parent = _parent(doc, parts)
    if parent is not None:
        parent.pop(parts[-1], None)

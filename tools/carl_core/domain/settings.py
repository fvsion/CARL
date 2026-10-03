"""The settings schema, value validation and config.json (validation, migration, key paths).

config.json sections: llama (server-wide), models.<name> (per-model profile), paths.
Every value is validated against its SettingSpec; unknown keys are ignored with a warning
so a newer config.json still loads in an older CARL. Keys of removed features (see
REMOVED) are ignored too and disappear the next time the file is saved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from .errors import ConfigError
from .types import JsonObject, JsonValue, SettingValue, Settings

SCHEMA = 1
DEFAULT_MODELS_DIR = "~/models/gguf"
CONFIG_COMMENT = ("CARL settings. Edit here or in the monitor's Settings tab. "
                  "Sections: llama, models.<name> (per-model profile), paths. "
                  "Precedence: flags > environment > this file > Auto-tune > catalogue. "
                  "./carl.sh config show lists every key.")
# Top-level keys of removed features: ignored on load (None = silently, else this warning)
# and dropped on the next save. "backend" chose llama.cpp or MTPLX before 1.2.0.
REMOVED: Dict[str, Optional[str]] = {
    "backend": None,
    "mtplx": "mtplx: MTPLX support was removed in CARL 1.2.0 (ignored; dropped when the settings are next saved)",
}

SettingType = Literal["int", "float", "intauto", "bool", "list", "choice", "str"]
Number = Union[int, float]


@dataclass(frozen=True)
class SettingSpec:
    """One setting: its type, default, allowed values and the launcher variable it maps to."""
    kind: SettingType
    default: SettingValue
    env: Optional[str] = None
    choices: Tuple[str, ...] = ()
    min: Optional[Number] = None
    max: Optional[Number] = None

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
        return out


def _choice(default: str, choices: Sequence[str], env: Optional[str] = None) -> SettingSpec:
    return SettingSpec("choice", default, env, tuple(choices))


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
NET_AUTO_NOTE = ("llama.net: 'auto' was removed in CARL 1.3.0; the server now listens on this Mac only "
                 "(127.0.0.1). For a VM client: ./carl.sh --vm, or ./carl.sh config set llama.net vm")

# Per-model settings (config.json "models.<name>", Auto-tune, catalogue "tune").
MODEL_KEYS: Dict[str, SettingSpec] = {
    "kv": _choice("q4_0", ("q4_0", "q8_0", "f16"), "KV"),
    "ctx": _int(98304, 4096, 262144, "CTX"),
    "slots": _choice("auto", ("auto", "1", "2", "3", "4"), "SLOTS"),    # 3-4: only where they fit (the start check)
    "spec": _choice("draft-mtp,ngram-mod", ("none", "draft-mtp", "ngram-mod", "draft-mtp,ngram-mod"), "SPEC"),
    "spec_n": _int(1, 1, 8, "SPEC_N"),
    "temp": _float(1.0, 0, 2, "TEMP"),
    "top_p": _float(0.95, 0, 1, "TOP_P"),
    "top_k": _int(20, 0, 1000, "TOP_K"),
    "min_p": _float(0, 0, 1, "MIN_P"),
    "presence": _float(0, 0, 2, "PRESENCE"),
    "repeat": _float(1.0, 0.5, 2, "REPEAT"),
    "alias": _str("", "ALIAS"),
}
# Server-wide llama.cpp settings (config.json "llama").
LLAMA_KEYS: Dict[str, SettingSpec] = {
    "model": _str("auto"),                                  # auto = auto fit's pick for this Mac
    "auto_goal": _choice("everyday", ("everyday", "hard-code")),        # auto fit: MoE first, or dense first
    "auto_fit": _choice("catalogue", ("catalogue", "downloaded")),      # auto fit picks from these models
    "mode": _choice("single", ("single", "router"), "LLAMA_MODE"),   # router: clients switch models (opt-in)
    "net": _choice("local", NET_CHOICES, "NET"),
    "host": _str("", "HOST"),                               # one address of this Mac (wins over net)
    "cache_ram": SettingSpec("intauto", "auto", "CACHE_RAM", min=0, max=65536),
    "ub": _int(512, 64, 8192, "UB"),
    "batch": _int(2048, 64, 16384, "BATCH"),
    "ckpt": _int(8, 0, 64, "CKPT"),
    "ckpt_step": _int(4096, 256, 65536, "CKPT_STEP"),
    "think_toggle": SettingSpec("bool", True, "THINK_TOGGLE"),
    "extra_args": SettingSpec("list", []),                  # passed to llama-server as-is
}
PATH_KEYS: Dict[str, SettingSpec] = {"models_dir": _str(DEFAULT_MODELS_DIR)}
SECTIONS: Dict[str, Dict[str, SettingSpec]] = {"llama": LLAMA_KEYS, "paths": PATH_KEYS}


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
        return [str(x) for x in items]
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
    models: Dict[str, Settings] = field(default_factory=dict)

    def section(self, name: str) -> Settings:
        return {"llama": self.llama, "paths": self.paths}[name]

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
        return out

    def to_file(self) -> JsonObject:
        """What save_config() writes: the dict form with a comment for people editing it."""
        doc = self.to_json()
        return {"schema": SCHEMA, "_comment": CONFIG_COMMENT, **{k: v for k, v in doc.items() if k != "schema"}}


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


def validate_config(raw: object) -> Tuple[Config, List[str]]:
    """(config, [warnings]) from a parsed config.json. Raises ConfigError on a bad value."""
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


def migrate_config(raw: object) -> Tuple[object, List[str]]:
    """(raw config.json with retired values converted, [notes]): the caller saves it when there are
    notes, so each note shows once. Today: llama.net "auto" -> unset (= local)."""
    if not isinstance(raw, dict):
        return raw, []
    llama = raw.get("llama")
    if isinstance(llama, dict) and llama.get("net") == "auto":
        return {**raw, "llama": {k: v for k, v in llama.items() if k != "net"}}, [NET_AUTO_NOTE]
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

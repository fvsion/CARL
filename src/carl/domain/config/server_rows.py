"""The Settings tab's Server panel rows, built from the settings schema (domain/config/settings.py, the one source):
each row's place in config.json, its default, its kind (numbers are typed, choices stepped) and the range a typed
value must be in. What the dashboard adds is only its own: the labels, the quick values ← → offers for a number, and
the choices it leaves out (f16 in Context memory). Pure.

Every key of the schema has a row here or is set somewhere else of the dashboard (ELSEWHERE, with where); a key only
for other platforms needs neither on this one. unplaced() lists the keys that have neither: a new setting with no row
fails a unit test.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple, Union

from ..shared.constants import DEFAULT_CTX
from .settings import CACHE_KEYS, GPU_KEYS, MODEL_KEYS, SECTIONS, SettingSpec

Value = Union[str, int]
Pending = Dict[str, Value]      # the values chosen in the Server panel, by row key
MODEL = "models"                # a row of the model's own settings (config.json models.<name>)


class SettingRow(NamedTuple):
    """One Settings row. loc = where config.json keeps it: "section:key", or "m:key" for
    the model's profile (models.<name>.key); None = not saved."""
    key: str
    label: str
    choices: Optional[List[Value]]
    loc: Optional[str]
    default: Value


# row key -> (section, schema key): the rows of the Server panel in order, then the More settings rows (the full detail
# level), then Auto fit's goal and candidates (its panel; saved at once)
PLACES: Dict[str, Tuple[str, str]] = {
    "model": ("llama", "model"), "ctx": (MODEL, "ctx"), "slots": (MODEL, "slots"), "spec": (MODEL, "spec"),
    "specn": (MODEL, "spec_n"), "kv": (MODEL, "kv"), "swa": ("cache", "swa"), "cache": ("llama", "cache_ram"),
    "net": ("llama", "net"), "api": ("llama", "api_port"), "temp": (MODEL, "temp"), "presence": (MODEL, "presence"),
}
ADV_PLACES: Dict[str, Tuple[str, str]] = {
    "top_k": (MODEL, "top_k"), "top_p": (MODEL, "top_p"), "min_p": (MODEL, "min_p"), "repeat": (MODEL, "repeat"),
    "ub": (MODEL, "ub"), "ckpt": ("llama", "ckpt"), "ckstep": ("llama", "ckpt_step"),
}
AUTO_PLACES: Dict[str, Tuple[str, str]] = {"goal": ("llama", "auto_goal"), "scope": ("llama", "auto_fit")}
# The GPU rows (W6.8): the settings of a PC with a GPU of its own (their platforms: off the Mac), after the Server
# panel's rows, only for an OS that is named and has them (the rows of "" are a Mac's).
GPU_PLACES: Dict[str, Tuple[str, str]] = {
    "keep": ("gpu", "vram_keep"), "backend": ("gpu", "backend"), "cpu_moe": ("llama", "n_cpu_moe"),
    "shared": ("gpu", "shared_limit_gib"),
}
LABELS = {
    "model": "Model", "ctx": "Context", "slots": "Slots", "spec": "Speculation", "specn": "Guesses",
    "kv": "Context memory", "swa": "Sliding window", "cache": "RAM cache", "net": "Network", "api": "API port",
    "temp": "Temperature", "presence": "Presence", "top_k": "Top k", "top_p": "Top p", "min_p": "Min p",
    "repeat": "Repeat penalty", "ub": "Batch size", "ckpt": "Checkpoints", "ckstep": "Checkpoint step",
    "goal": "Goal", "scope": "Candidates", "keep": "GPU memory kept", "backend": "GPU backend",
    "cpu_moe": "Experts in RAM", "shared": "Shared GPU limit",
}
KEEP = f"{GPU_KEYS['vram_keep'].default:.1f}"     # GPU memory kept: the schema's default, offered first
# The quick values ← → offers for a number (any value in the schema's range can be typed); the first is shown first.
OFFERED: Dict[str, List[Value]] = {
    "ctx": [32768, 49152, 65536, DEFAULT_CTX, 131072, 163840, 196608, 262144],
    "specn": ["1", "2", "3", "4"],          # shown inside Speculation
    "cache": ["auto", 1024, 2560, 4096, 6144, 8192],
    "api": ["auto"],                        # the dashboard API (Phase 23.5)
    "temp": ["1.0", "0.6"], "presence": ["0", "1.5"],
    "top_k": ["20", "40", "64", "0"], "top_p": ["0.95", "0.9", "0.8", "1.0"], "min_p": ["0", "0.05", "0.1"],
    "repeat": ["1.0", "1.05", "1.1"], "ub": ["512", "1024", "2048", "4096"], "ckpt": ["8", "4", "16"],
    "ckstep": ["4096", "1024", "2048", "8192"],
    "keep": [KEEP, *(v for v in ("0.5", "1.0", "1.5", "2.0") if v != KEEP)], "cpu_moe": ["auto", 0, 8, 16, 32], "shared": ["0.0", "16.0", "32.0", "64.0"],
}
HIDDEN = {"kv": ("f16",)}               # schema choices the panel does not offer (f16: the largest; never the best)
WHOLE = ("ctx", "cache", "api", "cpu_moe")     # rows whose value is a whole number, not text
TENTHS = ("temp", "repeat", "keep", "shared")   # rows whose number is shown with one decimal (1.0)
CONFIG_ONLY = "the config command only"
# The schema keys the dashboard sets elsewhere than the Server panel, or not at all, and where.
ELSEWHERE: Dict[Tuple[str, str], str] = {
    (MODEL, "alias"): "the model's name (a start sets it)",
    (MODEL, "ub_tune"): "the Batch size row (the tune its value was chosen against)",
    ("llama", "ub"): "the Batch size row (per model since A51; llama.ub is read once by the start's question)",
    (MODEL, "thinking_main"): "the Agents panel", (MODEL, "thinking_coder"): "the Agents panel",
    ("llama", "mode"): "the Router panel", ("llama", "host"): "the Network row (an address of this computer)",
    ("llama", "batch"): CONFIG_ONLY, ("llama", "think_toggle"): CONFIG_ONLY, ("llama", "vision"): CONFIG_ONLY,
    ("llama", "extra_args"): CONFIG_ONLY, ("llama", "path"): CONFIG_ONLY,
    ("llama", "load_reserve_gib"): CONFIG_ONLY,
    ("paths", "models_dir"): CONFIG_ONLY,
    **{("cache", k): "the Caching panel" for k in CACHE_KEYS if k != "swa"},
    ("delegation", "gate"): "the Connect tab (g)",
    ("gpu", "devices"): CONFIG_ONLY,
}


def spec_of(place: Tuple[str, str]) -> SettingSpec:
    """The schema entry of a row's place."""
    section, key = place
    return (MODEL_KEYS if section == MODEL else SECTIONS[section])[key]


def place_of(row_key: str) -> Tuple[str, str]:
    return {**PLACES, **ADV_PLACES, **AUTO_PLACES, **GPU_PLACES}[row_key]


def _loc(place: Tuple[str, str]) -> str:
    section, key = place
    return f"m:{key}" if section == MODEL else f"{section}:{key}"


def _default(row_key: str, spec: SettingSpec) -> Value:
    d = spec.default
    return d if row_key in WHOLE and isinstance(d, int) and not isinstance(d, bool) else str(d)


def make_row(row_key: str, choices: Sequence[str] = ()) -> SettingRow:
    """A row from the schema: its loc, its default (as the panel holds it), its choices (a choice's from the schema,
    less HIDDEN; a number's quick values). choices: a choice row's values that depend on this computer (the
    network)."""
    place = place_of(row_key)
    spec = spec_of(place)
    if row_key == "model":                  # the model list (every model), filled in when the rows are shown
        offered: Optional[List[Value]] = None
    elif spec.kind == "choice":
        offered = [c for c in (choices or spec.choices) if c not in HIDDEN.get(row_key, ())]
    else:
        offered = list(OFFERED[row_key])
    return SettingRow(row_key, LABELS[row_key], offered, _loc(place), _default(row_key, spec))


def kind_of(row_key: str) -> str:
    """The schema's kind of a row's setting (int, float, intauto, choice, str, ...)."""
    return spec_of(place_of(row_key)).kind


def on(row_key: str, os_name: str) -> bool:
    """The row's setting does something on this OS ("": every OS)."""
    return not os_name or spec_of(place_of(row_key)).on(os_name)


# Numbers are typed (Enter); whole numbers are kept as such.
NUMERIC = {k for k in {**PLACES, **ADV_PLACES, **GPU_PLACES} if kind_of(k) in ("int", "float", "intauto")}
INT_KEYS = {k for k in NUMERIC if kind_of(k) in ("int", "intauto")}
MODEL_ROW_KEYS = {k: p[1] for k, p in {**PLACES, **ADV_PLACES}.items() if p[0] == MODEL}   # row key -> profile key


def llama_rows(net_choices: Sequence[str], os_name: str = "") -> List[SettingRow]:
    """The llama.cpp rows (without the More settings rows of the full detail level) for this OS, then its GPU rows."""
    return [make_row(k, net_choices if k == "net" else ()) for k in PLACES if on(k, os_name)] + gpu_rows(os_name)


def gpu_rows(os_name: str) -> List[SettingRow]:
    """The GPU rows of a named OS that has them (Windows, Linux; none for a Mac or "")."""
    return [make_row(k) for k in GPU_PLACES if os_name and on(k, os_name)]


def adv_rows(os_name: str = "") -> List[SettingRow]:
    """The full detail level's rows (More settings)."""
    return [make_row(k) for k in ADV_PLACES if on(k, os_name)]


def auto_rows() -> List[SettingRow]:
    """Auto fit's goal and scope: chosen in the Auto fit panel (saved at once), not Server-panel rows."""
    return [make_row(k) for k in AUTO_PLACES]


def range_error(key: str, num: float) -> bool:
    """A typed number outside the schema's range for the row's setting."""
    spec = spec_of(place_of(key))
    return num < 0 or (spec.min is not None and num < spec.min) or (spec.max is not None and num > spec.max)


def parse_typed(key: str, text: str) -> Tuple[Optional[Value], str]:
    """A value typed for a numeric row ("96k", "0.6", "4096"): (value, "") if valid, else (None, why). The range is
    the schema's."""
    v = text.strip().lower()
    try:
        num = float(v[:-1]) * 1024 if v.endswith("k") else float(v)
        whole = int(num)
    except (ValueError, OverflowError):
        return None, f"That is not a number: {v}."
    if range_error(key, num):
        return None, f"{v} is out of range for {LABELS.get(key, key)}."
    if key in INT_KEYS:
        return (whole if key in WHOLE else str(whole)), ""
    return (f"{num:g}" if num != whole else f"{num:.1f}" if key in TENTHS else f"{whole}"), ""


def unplaced(os_name: str, sections: Optional[Mapping[str, Mapping[str, SettingSpec]]] = None) -> List[str]:
    """The settings of this OS (section.key; a model's: models.key) with no row and no place elsewhere: none, or a
    new setting needs its row (or its ELSEWHERE entry). sections: the schema (default: CARL's, models included)."""
    secs = sections if sections is not None else {**SECTIONS, MODEL: MODEL_KEYS}
    rowed = {place_of(k) for k in {**PLACES, **ADV_PLACES, **AUTO_PLACES, **GPU_PLACES} if on(k, os_name)}
    return [f"{sec}.{key}" for sec, keys in secs.items() for key, spec in keys.items()
            if spec.on(os_name) and (sec, key) not in rowed and (sec, key) not in ELSEWHERE]

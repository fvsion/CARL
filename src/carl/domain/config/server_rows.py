"""The Settings tab's Server panel rows, built from the settings schema (domain/config/settings.py, the one source):
each row's place in config.json, its default, its kind (numbers are typed, choices stepped) and the range a typed
value must be in. What the dashboard adds is only its own: the labels, the steps ← → take on a number, and the choices
it leaves out (f16 in Context memory). Pure.

The number rows (W1.6, decision B6): ← → step through the row's values from the value shown, a typed value too; they
stop at the ends and do not wrap, and auto sits below the lowest value where the row has one. At the simple level the
values are a short list of presets (PRESETS); at the full level they are the row's ladder (STEPS: runs of a step from
the schema's least value, or LADDERS: a list), and only there can a value be typed (the user, W1.6: "The preselected
values are fine in simple mode, but the granular selection should show up in full detail"). A top that depends on the
model or the computer (the model's layers with experts, the RAM, the smallest GPU) cuts both: step_number's top. The
Caching panel's Save after and Disk limit are number rows of the same rule (their keys: the schema's, auto_s and
disk_gb).

Every key of the schema has a row here or is set somewhere else of the dashboard (ELSEWHERE, with where); a key only
for other platforms needs neither on this one. unplaced() lists the keys that have neither: a new setting with no row
fails a unit test.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple, Union

from ..shared.constants import DEFAULT_CTX
from .settings import CACHE_KEYS, MODEL_KEYS, SECTIONS, SettingSpec

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
    "model": ("llama", "model"), "quant": ("llama", "model"), "ctx": (MODEL, "ctx"), "slots": (MODEL, "slots"),
    "spec": (MODEL, "spec"), "specn": (MODEL, "spec_n"), "kv": (MODEL, "kv"), "swa": ("cache", "swa"),
    "cache": ("llama", "cache_ram"), "net": ("llama", "net"), "api": ("llama", "api_port"), "temp": (MODEL, "temp"),
    "presence": (MODEL, "presence"),
}
ADV_PLACES: Dict[str, Tuple[str, str]] = {
    "top_k": (MODEL, "top_k"), "top_p": (MODEL, "top_p"), "min_p": (MODEL, "min_p"), "repeat": (MODEL, "repeat"),
    "ub": (MODEL, "ub"), "ckpt": ("llama", "ckpt"), "ckstep": ("llama", "ckpt_step"),
}
AUTO_PLACES: Dict[str, Tuple[str, str]] = {"goal": ("llama", "auto_goal"), "scope": ("llama", "auto_fit")}
# The GPU rows (W6.8): the settings of a PC with a GPU of its own (their platforms: off the Mac), after the Server
# panel's rows, only for an OS that is named and has them (the rows of "" are a Mac's).
GPU_PLACES: Dict[str, Tuple[str, str]] = {
    "keep": ("gpu", "vram_keep"), "backend": ("gpu", "backend"), "cpu_moe": (MODEL, "n_cpu_moe"),
    "shared": ("gpu", "shared_limit_gib"),
}
LABELS = {
    "model": "Model", "quant": "Quantization", "ctx": "Context", "slots": "Slots", "spec": "Speculation",
    "specn": "Guesses", "kv": "Context memory", "swa": "Sliding window", "cache": "RAM cache", "net": "Network",
    "api": "API port",
    "temp": "Temperature", "presence": "Presence", "top_k": "Top k", "top_p": "Top p", "min_p": "Min p",
    "repeat": "Repeat penalty", "ub": "Batch size", "ckpt": "Checkpoints", "ckstep": "Checkpoint step",
    "goal": "Goal", "scope": "Candidates", "keep": "GPU memory kept", "backend": "GPU backend",
    "cpu_moe": "Experts in RAM", "shared": "Shared GPU limit",
}
# The values of a row that is not stepped as a number: the Speculation row's guesses (inside Speculation) and the API
# port (auto, else a typed port: ← → give auto). Any value in the schema's range can be typed.
OFFERED: Dict[str, List[Value]] = {
    "specn": ["1", "2", "3", "4"],          # shown inside Speculation
    "api": ["auto"],                        # the dashboard API (Phase 23.5)
}
# The number rows' ladders (B6): runs of (up to, step) from the schema's least value, the last run up to its end ...
STEPS: Dict[str, Tuple[Tuple[float, float], ...]] = {
    "temp": ((2, 0.05),), "presence": ((2, 0.1),), "top_p": ((1, 0.05),), "min_p": ((0.2, 0.01), (1, 0.05)),
    "repeat": ((2, 0.05),), "ckpt": ((64, 1),), "cpu_moe": ((999, 1),), "cache": ((65536, 1024),),
    "keep": ((64, 0.25),), "shared": ((1024, 1),),
    "auto_s": ((120, 10), (600, 30), (3600, 60)), "disk_gb": ((10, 1), (50, 5), (200, 10), (1000, 50)),
}
# ... or a list of their own (the context's usual sizes; Top k sorted, 0 is off; powers of two)
LADDERS: Dict[str, Tuple[float, ...]] = {
    "ctx": (32768, 49152, 65536, DEFAULT_CTX, 131072, 163840, 196608, 262144),
    "top_k": (0, 10, 20, 40, 64, *range(100, 1001, 50)),
    "ub": tuple(64 * 2 ** i for i in range(8)), "ckstep": tuple(256 * 2 ** i for i in range(9)),
}
# The presets of the simple level (the quick values offered before B6, sorted; GPU memory kept and Save after the
# user's, W1.6): ← → step through them there, from the value shown. A row without presets has its ladder at both levels.
PRESETS: Dict[str, Tuple[float, ...]] = {
    "ctx": LADDERS["ctx"], "cache": (1024, 2560, 4096, 6144, 8192), "temp": (0.6, 1.0), "presence": (0, 1.5),
    "top_k": (0, 20, 40, 64), "top_p": (0.8, 0.9, 0.95, 1.0), "min_p": (0, 0.05, 0.1), "repeat": (1.0, 1.05, 1.1),
    "ub": (512, 1024, 2048, 4096), "ckpt": (4, 8, 16), "ckstep": (1024, 2048, 4096, 8192),
    "keep": (0.25, 0.5, 1.0, 1.5, 2.0), "cpu_moe": (0, 8, 16, 32), "shared": (0, 16, 32, 64),
    "auto_s": (15, 30, 45, 60, 90, 120, 180), "disk_gb": (2, 5, 10, 20, 50),
}
# The Caching panel's number rows (their keys are the schema's)
CACHE_NUMBERS: Dict[str, Tuple[str, str]] = {"auto_s": ("cache", "auto_s"), "disk_gb": ("cache", "disk_gb")}
HIDDEN = {"kv": ("f16",)}               # schema choices the panel does not offer (f16: the largest; never the best)
WHOLE = ("ctx", "cache", "api", "cpu_moe", "auto_s", "disk_gb")     # rows whose value is a whole number, not text
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
    ("llama", "n_cpu_moe"): "the Experts in RAM row's value for a model without its own (W1.6, B2); "
                            "the config command sets it",
    ("gpu", "devices"): CONFIG_ONLY,
}


def spec_of(place: Tuple[str, str]) -> SettingSpec:
    """The schema entry of a row's place."""
    section, key = place
    return (MODEL_KEYS if section == MODEL else SECTIONS[section])[key]


def place_of(row_key: str) -> Tuple[str, str]:
    return {**PLACES, **ADV_PLACES, **AUTO_PLACES, **GPU_PLACES, **CACHE_NUMBERS}[row_key]


# rows that are a view on another row's value, not saved (Quantization: the Model row's entry; B1)
VIEWS = ("quant",)


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
    if row_key in ("model", "quant"):       # the model list, the model's quantizations: filled in when shown
        offered: Optional[List[Value]] = None
    elif spec.kind == "choice":
        offered = [c for c in (choices or spec.choices) if c not in HIDDEN.get(row_key, ())]
    elif row_key in OFFERED:
        offered = list(OFFERED[row_key])
    else:                                   # a number row: its ladder (step_number steps it)
        offered = [held(row_key, v) for v in ladder(row_key)]
    return SettingRow(row_key, LABELS[row_key], offered, None if row_key in VIEWS else _loc(place),
                      _default(row_key, spec))


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


def number_text(key: str, num: float) -> str:
    """A number as a row holds it as text: 1.0 for a row of TENTHS, else as short as it goes (0.95, 4096)."""
    num = round(num, 4)
    if key in TENTHS and round(num, 1) == num:
        return f"{num:.1f}"
    return f"{int(num)}" if num == int(num) else f"{num:g}"


def held(key: str, num: float) -> Value:
    """A number in the form the row holds it: a whole number for WHOLE, a whole number's text for the other int rows,
    else number_text."""
    if key in WHOLE:
        return int(round(num))
    if spec_of(place_of(key)).kind in ("int", "intauto"):
        return str(int(round(num)))
    return number_text(key, num)


def stepped(key: str) -> bool:
    """A number row that ← → step through its ladder (B6)."""
    return key in STEPS or key in LADDERS


TYPED_SIMPLE = ("ctx",)         # number rows typed at the simple level too (W1.6 wave 3, the user: "Keep typing at
#                                 simple for context")


def typed_at(key: str, full: bool) -> bool:
    """A value can be typed into the row at this level: a number row of the sweep only at the full level (W1.6, B6),
    the Context row (TYPED_SIMPLE) and another typed row (the API port) at both."""
    return full or not stepped(key) or key in TYPED_SIMPLE


def ladder(key: str, top: Optional[float] = None, full: bool = True) -> List[float]:
    """The values ← → step through on a number row, smallest first, up to top (a top of the model or the computer)
    and the schema's largest value: its ladder at the full level, its presets (where it has them) at the simple
    level."""
    spec = spec_of(place_of(key))
    lo = float(spec.min or 0)
    hi = float(spec.max) if spec.max is not None else float("inf")
    if top is not None:
        hi = min(hi, top)
    if not full and key in PRESETS:
        vals = [float(v) for v in PRESETS[key]]
    elif key in LADDERS:
        vals = [float(v) for v in LADDERS[key]]
    else:
        vals, x = [], lo
        for end, step in STEPS[key]:
            while x < end - 1e-9:
                vals.append(x)
                x = round(x + step, 4)
        vals.append(x)
    return [v for v in vals if lo - 1e-9 <= v <= hi + 1e-9]


def step_number(key: str, value: Value, step: int, top: Optional[float] = None,
                auto_at: Optional[float] = None, full: bool = True) -> Value:
    """The value ← (step -1) or → (step 1) gives on a number row: the next value of its ladder (full) or its presets
    (the simple level) past the one shown (a value between two goes to the next one in that direction); at an end it
    stays. A row with auto: auto is below the lowest value (← from it gives auto), and from auto the steps start at
    auto_at, the value auto stands for now (the Experts in RAM row: auto's count), else → gives the lowest value. A
    value over the top (top, else the schema's largest): ← and → give the ladder's largest value."""
    vals = ladder(key, top, full)
    if not vals:
        return value
    ends = ladder(key, top)
    has_auto = spec_of(place_of(key)).kind == "intauto"
    if str(value) == "auto":
        if not has_auto:
            return value
        if auto_at is None:
            return held(key, vals[0]) if step > 0 else value
        cur = float(auto_at)
    else:
        try:
            cur = float(str(value))
        except ValueError:          # not a number (a stale value): the lowest step
            return held(key, vals[0])
    if ends and cur > ends[-1] + 1e-9:     # over the top (the model's, the computer's): the top
        return held(key, ends[-1])
    if step > 0:
        nxt = next((v for v in vals if v > cur + 1e-9), None)
        return held(key, nxt) if nxt is not None else value
    prev = next((v for v in reversed(vals) if v < cur - 1e-9), None)
    if prev is not None:
        return held(key, prev)
    return "auto" if has_auto and str(value) != "auto" else value


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
    return number_text(key, num), ""


def unplaced(os_name: str, sections: Optional[Mapping[str, Mapping[str, SettingSpec]]] = None) -> List[str]:
    """The settings of this OS (section.key; a model's: models.key) with no row and no place elsewhere: none, or a
    new setting needs its row (or its ELSEWHERE entry). sections: the schema (default: CARL's, models included)."""
    secs = sections if sections is not None else {**SECTIONS, MODEL: MODEL_KEYS}
    rowed = {place_of(k) for k in {**PLACES, **ADV_PLACES, **AUTO_PLACES, **GPU_PLACES} if on(k, os_name)}
    return [f"{sec}.{key}" for sec, keys in secs.items() for key, spec in keys.items()
            if spec.on(os_name) and (sec, key) not in rowed and (sec, key) not in ELSEWHERE]

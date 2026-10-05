"""The Settings tab's model: its rows, how values are shown and coloured, whether a setup
fits the GPU, and how the chosen values map to ~/.config/carl/config.json.

The Settings tab chooses the llama.cpp server's model and settings, saves them to
config.json (tools/carl.py; the launchers read it: flags > environment > config.json >
Auto-tune > catalogue) and restarts the server. Pure, except SettingsService, which reads
models, tunes and config.json through the ModelStore port."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from carl_core.domain.autofit import AutoFit, setup_text
from carl_core.domain.fit import check_start, max_ctx, need_bytes, prompt_cache_mib, reserve_bytes, swa_plan, swa_tokens
from carl_core.domain.gguf import OVERHEAD, kv_bytes_per_token, swa_bytes_per_token
from carl_core.domain.units import MIB, memory, tokens

from .fmt import GRN, R, RED, YEL, ctx_label
from .model import JSONDict, ModelInfo, ServerData, Shape, draft_bytes, drafter_missing, flag, flag_int, jdict
from .store import ModelList
from .words import kv_name, net_name, plural, spec_name

Value = Union[str, int]
Pending = Dict[str, Value]      # the values chosen in the Server panel, by row key


class SettingRow(NamedTuple):
    """One Settings row. loc = where config.json keeps it: "section:key", or "m:key" for
    the model's profile (models.<name>.key); None = not saved."""
    key: str
    label: str
    choices: Optional[List[Value]]
    loc: Optional[str]
    default: Value


LLAMA_ADV = [                       # the full detail level's rows (More settings)
    SettingRow("top_k", "Top k", ["20", "40", "64", "0"], "m:top_k", "20"),
    SettingRow("top_p", "Top p", ["0.95", "0.9", "0.8", "1.0"], "m:top_p", "0.95"),
    SettingRow("min_p", "Min p", ["0", "0.05", "0.1"], "m:min_p", "0"),
    SettingRow("repeat", "Repeat penalty", ["1.0", "1.05", "1.1"], "m:repeat", "1.0"),
    SettingRow("ub", "Batch size", ["512", "1024", "2048"], "llama:ub", "512"),
    SettingRow("ckpt", "Checkpoints", ["8", "4", "16"], "llama:ckpt", "8"),
    SettingRow("ckstep", "Checkpoint step", ["4096", "1024", "2048", "8192"], "llama:ckpt_step", "4096"),
]
MORE_SETTINGS = "presence, sampling, batch size, checkpoints"
SPEC_COMBOS = ["none|1", "ngram-mod|1", "ngram-mod|2", "ngram-mod|3", "draft-mtp|1", "draft-mtp|2", "draft-mtp|3",
               "draft-mtp|4", "draft-mtp,ngram-mod|1", "draft-mtp,ngram-mod|2", "draft-mtp,ngram-mod|3",
               "draft-mtp,ngram-mod|4"]          # the Speculation row: a mode and its guesses together


# Auto fit's goal and scope: chosen in the Auto fit panel (saved at once), not Server-panel rows.
AUTO_ROWS = [
    SettingRow("goal", "Goal", ["everyday", "hard-code"], "llama:auto_goal", "everyday"),
    SettingRow("scope", "Candidates", ["catalogue", "downloaded"], "llama:auto_fit", "catalogue"),
]


@dataclass(frozen=True)
class Schema:
    """The Server panel's rows. The network row offers local, vm and this Mac's own
    addresses (an address = llama.host in config.json)."""
    net_choices: Tuple[str, ...]

    @property
    def llama(self) -> List[SettingRow]:
        """The llama.cpp rows (without the More settings rows of the full detail level)."""
        return [
            SettingRow("model", "Model", None, "llama:model", "auto"),      # choices: the model list
            SettingRow("ctx", "Context", [32768, 49152, 65536, 98304, 131072, 163840, 196608, 262144], "m:ctx", 98304),
            SettingRow("slots", "Slots", ["auto", "1", "2", "3", "4"], "m:slots", "auto"),   # 3-4 only where they fit (rows)
            SettingRow("spec", "Speculation", ["none", "ngram-mod", "draft-mtp", "draft-mtp,ngram-mod"], "m:spec",
                       "draft-mtp,ngram-mod"),
            SettingRow("specn", "Guesses", ["1", "2", "3", "4"], "m:spec_n", "1"),    # shown inside Speculation
            SettingRow("kv", "Context memory", ["q4_0", "q8_0"], "m:kv", "q4_0"),
            SettingRow("cache", "RAM cache", ["auto", 1024, 2560, 4096, 6144, 8192], "llama:cache_ram", "auto"),
            SettingRow("net", "Network", list(self.net_choices), "llama:net", "local"),
            SettingRow("temp", "Temperature", ["1.0", "0.6"], "m:temp", "1.0"),
            SettingRow("presence", "Presence", ["0", "1.5"], "m:presence", "0"),
        ]

    def saved_rows(self) -> List[SettingRow]:
        """The rows saved to config.json (the More settings rows and the Auto fit panel's included)."""
        return self.llama + LLAMA_ADV + AUTO_ROWS

    def defaults(self) -> Pending:
        """Every row at its default."""
        return {r.key: r.default for r in self.saved_rows()}


def net_choices(addrs: Sequence[str]) -> Tuple[str, ...]:
    """The network row's choices: local, vm, then this Mac's addresses."""
    return ("local", "vm", *addrs)


# row key -> key of the model's profile (config.json models.<name>, Auto-tune, catalogue)
MODEL_ROW_KEYS = {r.key: r.loc[2:] for r in Schema(()).llama + LLAMA_ADV if r.loc and r.loc.startswith("m:")}
NUMERIC = {"ctx", "temp", "presence", "top_k", "top_p", "min_p", "repeat", "specn", "ub", "ckpt", "ckstep",
           "cache"}       # Enter types a value
INT_KEYS = {"ctx", "cache", "top_k", "specn", "ub", "ckpt", "ckstep"}
FROM_RUNNING = {"kv", "ctx", "temp", "presence", "spec", "specn", "top_k", "top_p", "min_p", "repeat", "ckpt", "ckstep",
                "ub", "slots"}
NOT_RUNNING = {"goal", "scope"}                         # rows without a "running now" value
FULL_ONLY = {"presence"} | {r.key for r in LLAMA_ADV}   # rows of the full detail level (More settings)
REINSTALL = {"ctx", "slots"}         # clients need install.sh again when these change

ADV_WARN = ("Caution: Auto-tune and tests measured these values (reference/performance.md). A change can make the model "
            "slower or its answers worse. Press x to use the recommended settings again.")
_NET_HELP = ("Who can connect. This Mac only: the default. This Mac and the VM: a VMware Fusion VM can connect too "
             "(192.168.42.1). An address: only that network interface. Other computers on that network can connect.")
SET_HELP = {
    "model": "The list has every model: the catalogue, the models folder and your Hugging Face downloads. auto = start "
             "Auto fit's choice for this Mac. The Auto fit panel (A) tells why. To add a model, use the Models panel: "
             "it downloads any GGUF file from Hugging Face.",
    "goal": "The goal of Auto fit. everyday: the fast models first (MoE and small dense models). They are usually good "
            "enough. hard code: the dense models first. They write better code, but they are slower.",
    "scope": "The models that Auto fit looks at. catalogue: every catalogue model. Auto fit then offers the download. "
             "Until the download is complete, a start uses the best downloaded model. downloaded only: only the models "
             "on this Mac.",
    "kv": "The memory that holds the context of all slots. q4 is smaller and is the tested default. q8 recalls text far "
          "back more exactly, but it uses about 2 × the memory.",
    "ctx": "How many tokens one slot can hold. The colour tells how fast this Mac reads a full context again: green is "
           "fast, yellow is slow, red is very slow.",
    "slots": "A slot is a place in the server for one session. auto: 2 slots when two fit (the main session and a "
             "subagent at the same time), else 1. 3 or 4: more subagents at the same time. The list offers 3 and 4 only "
             "when they fit this Mac. More slots write more tokens in total, but each slot is slower.",
    "spec": "The server guesses tokens ahead and checks them: more speed, the same answer. n-gram copies text that is "
            "in the context already (fast for edits). MTP: a small predictor guesses new text. Guesses: the tokens it "
            "guesses for each step. Auto-tune measures the best choice.",
    "specn": "The tokens the speculation guesses for each step. On Metal, more guesses do not make dense models faster.",
    "cache": "The RAM cache: the server keeps sessions that leave a slot in RAM while it runs. When a session comes "
             "back, the server does not read it all again. auto: CARL sizes it from the free memory at each start.",
    "net": _NET_HELP,
    "temp": "How random the answers are. The recommended value comes from the model card (Qwen in thinking mode and "
            "Gemma 4: 1.0). 0.6 gives more exact code on some models.",
    "presence": "0 is the default. 1.5 gives fewer repeat loops (the Qwen card, for general use).",
    "top_k": "The model picks from the k most likely tokens. Qwen: 20. Gemma 4: 64. 0 = off.",
    "top_p": "The model picks from the most likely tokens up to this total probability. 0.95 for most models.",
    "min_p": "The model ignores the tokens below min p × the top probability. Qwen: 0.",
    "repeat": "The repeat penalty. Qwen: 1.0 (off). Use presence instead.",
    "ub": "The batch that the GPU works on in one step (-ub). On Metal, 512 is the fastest: 90.5 tok/s, against 88.6 "
          "and 86.1 tok/s for 1024 and 2048.",
    "ckpt": "Saved points of the recurrent state, per slot. Each one uses about 63 MiB on the 35B and 150 MiB on the "
            "27B. More checkpoints did not help in the Phase 6 test.",
    "ckstep": "The smallest number of tokens between two checkpoints. In the Phase 6 test, 1024 and 4096 gave the same "
              "result.",
}


def row_instruction(key: str) -> str:
    """How to change a Settings row, for someone new to the dashboard."""
    if key == "model":
        return "Press Enter to choose a model from the list. Press A to see Auto fit's choice and why."
    if key in NUMERIC:
        return "Press ← → for the usual values. Or type a number, then press Enter."
    return "Press ← → to change the value."


def rows(p: Pending, schema: Schema, model_choices: Callable[[], List[str]], full: bool = False) -> List[SettingRow]:
    """The rows shown: the Speculation row holds its guesses; the full detail level adds presence and the
    More settings rows. The model row offers model_choices(); the speculation row the mode and guesses
    together (SPEC_COMBOS)."""
    out: List[SettingRow] = []
    for r in schema.llama:
        if r.key == "specn" or (r.key in FULL_ONLY and not full):
            continue
        if r.key == "model":
            r = r._replace(choices=list(model_choices()))
        elif r.key == "spec":
            r = r._replace(choices=list(SPEC_COMBOS))
        out.append(r)
    return out + (LLAMA_ADV if full else [])


def _is_number(v: object) -> bool:
    return str(v).replace(".", "", 1).isdigit()


def fmt_val(key: str, v: object) -> Value:
    """A config / tune value as the Settings rows show it."""
    if key in ("ctx", "cache"):
        return int(str(v)) if str(v).isdigit() else (v if isinstance(v, (str, int)) else str(v))
    if key in ("temp", "repeat") and _is_number(v):
        return f"{float(str(v)):.1f}"
    if key in ("presence", "min_p", "top_p") and _is_number(v):
        return f"{float(str(v)):g}"
    return str(v)


def shown_value(key: str, v: object, long: bool = False) -> str:
    """A row value on screen in the glossary's words: contexts as 96K (long: '96K tokens per slot'), the
    context memory type as q4 (small), the RAM cache in GiB, the network as who can connect."""
    if key == "ctx":
        return ctx_label(v) + (" tokens per slot" if long and str(v).isdigit() else "")
    if key == "kv":
        return kv_name(v, short=not long)
    if key == "cache":
        return memory(int(str(v)) * MIB) if str(v).isdigit() and int(str(v)) else str(v)
    if key == "net":
        return net_name(v)
    if key == "spec":
        return spec_name(v)
    return str(v)


def spec_value(p: Pending) -> str:
    """The Speculation row's value: the mode and its guesses ("draft-mtp|1")."""
    return f"{p.get('spec', 'none')}|{p.get('specn', '1')}"


def parse_typed(key: str, text: str) -> Tuple[Optional[Value], str]:
    """A value typed for a numeric row ("96k", "0.6", "4096"): (value, "") if valid, else (None, why)."""
    v = text.strip().lower()
    try:
        num = float(v[:-1]) * 1024 if v.endswith("k") else float(v)
        whole = int(num)
    except (ValueError, OverflowError):
        return None, f"That is not a number: {v}."
    if num < 0 or (key == "ctx" and not 4096 <= num <= 262144) or (key in ("top_p", "min_p") and num > 1):
        label = next((r.label for r in Schema(()).llama + LLAMA_ADV if r.key == key), key)
        return None, f"{v} is out of range for {label}."
    if key in INT_KEYS:
        return (whole if key in ("ctx", "cache") else str(whole)), ""
    return (f"{num:g}" if num != whole else f"{num:.1f}" if key in ("temp", "repeat") else f"{whole}"), ""


def step_choice(choices: Sequence[Value], cur: Value, step: int) -> Value:
    """The choice step places after cur (cyclic); from the first one if cur is not a choice."""
    j = next((n for n, c in enumerate(choices) if str(c) == str(cur)), 0)
    return choices[(j + step) % len(choices)]


def running_settings(d: ServerData, vm_addr: str, find: Callable[[str], Optional[ModelInfo]]) -> Pending:
    """The values the running llama.cpp server uses, from its command line ({} when none runs)."""
    cmd = d.cmd
    host = flag(cmd, "--host", default="") or ""
    net = "vm" if host == vm_addr else "local" if host in ("127.0.0.1", "::1") else host or "N/A"
    if "llama-server" not in cmd:
        return {}
    mpath = flag(cmd, "-m", "--model") or ""
    m = (find(mpath) if mpath else None) or (find(os.path.basename(mpath)) if os.path.basename(mpath) else None)

    def f(*names: str, default: str = "N/A") -> str:
        return flag(cmd, *names) or default

    return {"model": m["name"] if m else (os.path.basename(mpath) or "N/A"),
            "kv": f("-ctk", "--cache-type-k", default="f16"),
            "ctx": flag_int(cmd, "--kv-unified-per-slot", default=0) or d.n_ctx or "N/A",
            "slots": f("--parallel", "-np", default="1"), "cache": flag_int(cmd, "--cache-ram", default=0),
            "net": net, "temp": f("--temp"), "presence": f("--presence-penalty"),
            "spec": f("--spec-type", default="none"), "specn": f("--spec-draft-n-max", default="1"),
            "top_k": f("--top-k"), "top_p": f("--top-p"), "min_p": f("--min-p"), "repeat": f("--repeat-penalty"),
            "ub": f("-ub"), "ckpt": f("--ctx-checkpoints"), "ckstep": f("--checkpoint-min-step")}


def settings_to_config(p: Pending, cfg: JSONDict, schema: Schema, model: Optional[str],
                       tuned: Optional[JSONDict]) -> JSONDict:
    """config.json with the pending settings: server-wide values that differ from the defaults,
    and the model's profile = the values that differ from its tune (Auto-tune / catalogue).
    model / tuned: the model a start loads and its tune. Returns a new dict."""
    cfg = copy.deepcopy(cfg)
    for key, _, _, loc, default in schema.saved_rows():
        if not loc or loc.startswith("m:"):
            continue
        sec, ck = loc.split(":")
        s = cfg.setdefault(sec, {})
        v = p[key]
        if ck == "net":
            s.pop("host", None)
            if str(v).count(".") == 3:          # an address: that interface
                s["host"] = v
                s.pop("net", None)
                continue
        if str(v) == str(default):
            s.pop(ck, None)
        else:
            s[ck] = v
    if model is not None and tuned is not None:
        prof = {pk: p[key] for key, pk in MODEL_ROW_KEYS.items() if str(fmt_val(key, tuned[pk])) != str(p[key])}
        cfg.setdefault("models", {})
        if prof:
            cfg["models"][model] = prof
        else:
            cfg["models"].pop(model, None)
    return cfg


def env_from_cmd(cmd: str) -> Dict[str, str]:
    """Environment that makes serve-llama.sh start the same llama.cpp server as cmd (the rollback)."""
    def f(*names: str, default: str = "") -> str:
        return flag(cmd, *names) or default
    e = {"SETTINGS_FILE": "none", "MODEL": f("-m", "--model"), "HOST": f("--host", default="127.0.0.1"),
         "CTX": f("--kv-unified-per-slot") or f("-c", "--ctx-size", default="98304"),
         "SLOTS": f("--parallel", "-np", default="1"), "KV_K": f("-ctk", default="q4_0"),
         "KV_V": f("-ctv", default="q4_0"), "TEMP": f("--temp", default="1.0"),
         "TOP_P": f("--top-p", default="0.95"), "TOP_K": f("--top-k", default="20"),
         "MIN_P": f("--min-p", default="0"), "PRESENCE": f("--presence-penalty", default="0"),
         "CACHE_RAM": f("--cache-ram"), "ALIAS": f("--alias"),
         "SPEC": f("--spec-type", default="none"), "SPEC_N": f("--spec-draft-n-max", default="1")}
    return {k: v for k, v in e.items() if v}


# ---------------------------------------------------------------- fit maths
@dataclass(frozen=True)
class FitInfo:
    """Does a setup fit the GPU memory limit? The parts of the memory it needs (bytes), the slots a start
    uses (auto resolved), the sliding-window cache (full: True, window: False, None: no such layers),
    the largest context per slot that fits with these slots. ok = known, downloaded and fits."""
    name: str
    known: bool = True
    downloaded: bool = True
    need: float = 0
    limit: float = 0
    ctx: int = 0
    slots: int = 0
    kv: str = ""
    full: Optional[bool] = None
    largest: int = 0
    weights: int = 0
    drafter: int = 0
    context: float = 0
    state: float = 0
    buffers: float = 0
    error: str = ""

    @property
    def fits(self) -> bool:
        return self.known and self.downloaded and not self.error and self.need <= self.limit

    @property
    def ok(self) -> bool:
        return self.fits


def gib_pair(need: float, limit: float) -> Tuple[str, str]:
    """need and limit in GiB with one decimal, two when they round to the same text but differ."""
    a, b = memory(need), memory(limit)
    if a == b and need != limit:
        return f"{need / 2**30:.2f} GiB", f"{limit / 2**30:.2f} GiB"
    return a, b


def plan_words(slots: int, ctx: int) -> str:
    """'2 slots × 96K tokens' (carl_core's words, as the CLI says it)."""
    return setup_text(slots, ctx)


def fit_sentence(f: FitInfo) -> str:
    """The Memory status as plain sentences (coloured ✓ / ✗)."""
    if not f.known:
        return f"{RED}✗{R} {f.name} is not a known model."
    if not f.downloaded:
        return f"{RED}✗{R} {f.name} is not downloaded. To download it: the Models panel (]), then d."
    if f.error:
        return f"{RED}✗{R} CARL cannot check the memory: {f.error}."
    need, limit = gib_pair(f.need, f.limit)
    plan = plan_words(f.slots, f.ctx)
    if f.fits:
        return f"{GRN}✓{R} It fits. This model with {plan} needs {need}. This Mac gives the GPU {limit}."
    more = (f" With {plural(f.slots, 'slot')}, at most {tokens(f.largest)} tokens per slot fit." if f.largest
            else " The weights alone do not fit.")
    return f"{RED}✗{R} It does not fit. This model with {plan} needs {need}. This Mac gives the GPU {limit}.{more}"


def swa_sentence(f: FitInfo) -> str:
    """A Gemma model's sliding-window cache in one sentence ('' for other models)."""
    if f.full is None:
        return ""
    if f.full:
        return "This Gemma model keeps its full cache: CARL can restore saved sessions and prompts."
    return (f"This Gemma model uses the {YEL}window cache{R}: it uses less memory, but CARL cannot restore saved "
            f"sessions and prompts (Settings > Caching).")


def llama_fit(name: str, weights: int, shape: Shape, kv: str, ctx: int, slots: str, limit: int,
              swa: str = "auto", drafter: int = 0) -> FitInfo:
    """Does the model fit with these settings? Slots and, for a model with sliding-window layers, the
    full or the window cache as the launcher decides (fit.swa_plan with cache.swa); the same check the
    launcher refuses a start with (carl_core.domain.fit.check_start). weights include the drafter."""
    n, full = swa_plan(swa, shape, weights, ctx, slots, kv, limit)
    chk = check_start(shape, weights, ctx, n, kv, limit, full is not False)
    sw = full is not False
    context = kv_bytes_per_token(shape, kv) * ctx * n + swa_bytes_per_token(shape, kv) * swa_tokens(shape, ctx, sw) * n
    return FitInfo(name=name, need=chk.need, limit=limit, ctx=ctx, slots=n, kv=kv, full=full, largest=chk.largest,
                   weights=weights - drafter, drafter=drafter, context=context, state=shape["rs_bytes"] * n,
                   buffers=OVERHEAD)


class SettingsService:
    """Settings logic that needs the models and config.json (through the ModelStore port)."""

    def __init__(self, models: ModelList, schema: Schema, vm_addr: str, gpu_limit: Callable[[], int],
                 ram: int = 0) -> None:
        self.models = models
        self.ram = ram                      # this Mac's RAM, bytes (the RAM cache's auto size; 0 = unknown)
        self.full = False                   # the detail level is full (the More settings rows are shown)
        self.store = models.store
        self.schema = schema
        self.vm_addr = vm_addr
        self.gpu_limit = gpu_limit          # bytes; the collector's cached value when it has one
        self._fit_key: Optional[Tuple[Tuple[str, str], ...]] = None
        self._fit: FitInfo = FitInfo("")
        self._max: Dict[str, Tuple[float, Optional[int]]] = {}     # model -> (list time, largest window)
        self._plans: Dict[str, Tuple[float, Optional[FitInfo]]] = {}  # model -> (list time, its plan here)
        self._slots: Dict[Tuple[str, str, str, str], int] = {}      # (model, ctx, kv, swa) -> most slots that fit

    def rows(self, p: Pending) -> List[SettingRow]:
        """The rows shown for p (the model row offers every model; the slots row 3 and 4 only when
        they fit this Mac with the pending model, window and KV cache)."""
        out = rows(p, self.schema, self.models.choices, self.full)
        most = self.max_slots(p)
        return [r._replace(choices=[c for c in (r.choices or []) if not str(c).isdigit() or int(str(c)) <= max(most, 2)])
                if r.key == "slots" else r for r in out]

    def max_slots(self, p: Pending) -> int:
        """The most slots (up to 4) whose windows fit the GPU limit with these settings (2 when unknown);
        cached per model, window and KV type (it reads the GGUF header)."""
        key = (self.resolved_model(p), str(p.get("ctx")), str(p.get("kv")), self.swa_mode())
        if key not in self._slots:
            self._slots[key] = self._max_slots(p)
        return self._slots[key]

    def _max_slots(self, p: Pending) -> int:
        m = self.models.by_name(self.resolved_model(p))
        try:
            if not m or m["status"] != "downloaded":
                return 2
            shape, w = self.store.shape_of(m["path"]), self.weights(m)
            ctx, kv, limit = int(str(p["ctx"])), str(p["kv"]), self.gpu_limit()
            full = self.swa_mode() == "full"            # auto and window: the window when it has to
            return max([n for n in (1, 2, 3, 4) if need_bytes(shape, w, ctx, n, kv, full) <= limit] or [1])
        except Exception:           # an unreadable header, a value that is not a number: no extra slots offered
            return 2

    def swa_mode(self) -> str:
        """cache.swa (Settings > Caching): auto, full or window; auto when config.json can't be read."""
        try:
            return str(jdict(self.store.load_config().get("cache")).get("swa") or "auto")
        except Exception:           # an unreadable config: the default
            return "auto"

    @staticmethod
    def goal_scope(p: Pending) -> Tuple[str, str]:
        """Auto fit's goal and scope in these settings."""
        return str(p.get("goal", "everyday")), str(p.get("scope", "catalogue"))

    def resolved_model(self, p: Pending) -> str:
        """The model a start with these settings loads ("auto" resolved for this Mac: auto fit's
        pick, or the best downloaded model that fits while the pick isn't downloaded)."""
        name = str(p.get("model", "auto"))
        return name if name != "auto" else self.models.auto_model(*self.goal_scope(p))

    def auto_fit(self, p: Pending) -> Optional[AutoFit]:
        """Auto fit for this Mac with the goal and scope in p (None: unavailable, models.fit_error says why)."""
        return self.models.auto_fit(*self.goal_scope(p))

    def save_auto_choice(self, p: Pending, key: str, value: str) -> None:
        """The Auto fit panel's goal or scope: into p and config.json at once (llama.auto_goal /
        llama.auto_fit; the default is left out). ValueError for a value that isn't a choice."""
        row = next(r for r in AUTO_ROWS if r.key == key)
        if value not in (row.choices or []) or row.loc is None:
            raise ValueError(f"{row.label}: {value!r} is not a choice")
        p[key] = value
        cfg = self.store.load_config()
        sec = cfg.setdefault("llama", {})
        ck = row.loc.split(":")[1]
        if value == row.default:
            sec.pop(ck, None)
        else:
            sec[ck] = value
        self.store.save_config(cfg)

    def apply_auto_fit(self, p: Pending) -> Optional[AutoFit]:
        """Auto fit in one step: p gets the pick, its profile, and auto fit's context, slots and KV."""
        fit = self.auto_fit(p)
        if fit and fit.pick and fit.plan:
            p["model"] = fit.pick.name
            self.load_profile(p, fit.pick.name)
            p["ctx"], p["kv"] = fit.plan.ctx, fit.plan.kv
            p["slots"] = "auto" if fit.plan.slots == 2 else str(fit.plan.slots)    # auto = 2 when two fit
        return fit

    def recommended(self, name: str, with_config: bool = False) -> Tuple[JSONDict, Dict[str, str]]:
        """(values, source per key) for a model: Auto-tune > catalogue (> config.json when with_config).
        A config.json that can't be read counts as empty (the controller reports it)."""
        m = self.models.by_name(name)
        if not m:
            return self.store.builtin_tune(), {}
        cfg = self.store.empty_config()
        if with_config:
            try:
                cfg = self.store.load_config()
            except Exception:       # carl.ConfigError or a broken file: the tune without your overrides
                pass
        return self.store.effective_tune(m, cfg)

    def load_profile(self, p: Pending, name: str) -> None:
        """Model rows <- that model's profile (config.json > Auto-tune > catalogue)."""
        vals, _ = self.recommended(self.resolved_model({"model": name}), with_config=True)
        for key, pk in MODEL_ROW_KEYS.items():
            p[key] = fmt_val(key, vals[pk])

    def running(self, d: ServerData) -> Pending:
        """The settings the running server uses."""
        return running_settings(d, self.vm_addr, self.models.by_name)

    def pending_init(self, d: ServerData, cfg: JSONDict) -> Pending:
        """Start from config.json and the running server (so Apply without changes restarts the same setup)."""
        run = self.running(d)
        p: Pending = {}
        for key, _, _, loc, default in self.schema.saved_rows():
            if not loc or loc.startswith("m:"):
                continue
            sec, ck = loc.split(":")
            v = jdict(cfg.get(sec)).get(ck)
            p[key] = default if v in (None, "") else fmt_val(key, v)
        llama_cfg = jdict(cfg.get("llama"))
        if llama_cfg.get("host"):
            p["net"] = llama_cfg["host"]
        run_model = run.get("model")
        p["model"] = (run_model if isinstance(run_model, str) and self.models.by_name(run_model)
                      else p.get("model", "auto"))
        self.load_profile(p, str(p["model"]))
        if run:
            for key in FROM_RUNNING:
                if key in run and run[key] not in (None, "N/A"):
                    p[key] = fmt_val(key, run[key])
            if "net" not in llama_cfg and not llama_cfg.get("host"):
                p["net"] = run.get("net", p["net"]) if run.get("net") in self.schema.net_choices else p["net"]
        return p

    def defaults_for(self, p: Pending) -> Pending:
        """Every row at its default, the model (and Auto fit's goal and candidates) kept, the model rows at its
        recommended values."""
        q = self.schema.defaults()
        q.update(model=p.get("model", "auto"), goal=p.get("goal", "everyday"),
                 scope=p.get("scope", "catalogue"))
        vals = self.recommended(self.resolved_model(q))[0]           # the model's tune, without my overrides
        for key, pk in MODEL_ROW_KEYS.items():
            q[key] = fmt_val(key, vals[pk])
        return q

    def value_color(self, key: str, p: Pending) -> str:
        """Colour of a pending value: green = the tuned value for this model / a fast setting, yellow =
        changed from the tune or slower, red = very slow or broken on this model."""
        name = self.resolved_model(p)
        m = self.models.by_name(name)
        if key == "ctx" and m and str(p["ctx"]).isdigit():
            z = self.store.ctx_zone(m, int(p["ctx"]))
            return {"good": GRN, "slow": YEL, "very_slow": RED}[z]
        if key == "spec" and m:
            info = (self.store.header_info(m["path"]) if m["status"] == "downloaded"
                    else {"mtp": m.get("mtp"), "quant": m.get("quant", "")})
            spec = str(p["spec"])
            drafter = bool(m.get("draft")) and not drafter_missing(m)       # a separate drafter, downloaded
            if "draft-mtp" in spec and not info.get("mtp") and not drafter:
                return RED                                      # no MTP head in this file, no drafter for it
            if ("draft-mtp" in spec and str(p.get("specn")) != "1"
                    and str(info.get("quant", "")).upper().replace("UD-", "").startswith("IQ")):
                return RED                                      # MTP n>1 loses ~15% on IQ quants (measured 2026-10-03)
        if key in MODEL_ROW_KEYS:
            rec = self.recommended(name)[0][MODEL_ROW_KEYS[key]]
            return GRN if str(fmt_val(key, rec)) == str(p[key]) else YEL
        return ""

    def value_note(self, key: str, p: Pending) -> str:
        """Why a value is red (value_color), in one sentence ('' when it is not)."""
        if self.value_color(key, p) != RED:
            return ""
        if key == "ctx":
            m = self.models.by_name(self.resolved_model(p))
            slow = self.store.ctx_zones(m)[1] if m else 0
            return (f"Context: this Mac reads more than {tokens(slow)} tokens again very slowly (the context zones of "
                    f"this model).")
        if key == "spec" and str(p.get("specn")) != "1" and "draft-mtp" in str(p.get("spec")):
            m = self.models.by_name(self.resolved_model(p))
            info = self.store.header_info(m["path"]) if m and m["status"] == "downloaded" else {}
            if info.get("mtp") or (m and m.get("draft") and not drafter_missing(m)):
                return "Speculation: MTP with more than 1 guess is about 15% slower on IQ quantizations (measured)."
        if key == "spec":
            return ("Speculation: this model has no MTP head and no downloaded MTP drafter. The server uses n-gram "
                    "only.")
        return ""

    def fit_line(self, p: Pending) -> FitInfo:
        """Does the pending setup fit the GPU limit, and is the model downloaded?"""
        name = self.resolved_model(p)
        m = self.models.by_name(name)
        if not m:
            return FitInfo(name, known=False)
        if m["status"] != "downloaded":
            return FitInfo(name, downloaded=False, weights=int(m.get("bytes", 0)), drafter=draft_bytes(m))
        try:
            return llama_fit(name, self.weights(m), self.store.shape_of(m["path"]), str(p["kv"]),
                             int(p["ctx"]), str(p["slots"]), self.gpu_limit(), self.swa_mode(), draft_bytes(m))
        except Exception as e:      # a GGUF that can't be read, a value that is not a number, ...: say so
            return FitInfo(name, error=str(e))

    def fit_cached(self, p: Pending) -> FitInfo:
        """fit_line, recomputed only when a pending value changes (it reads the GGUF header)."""
        key = tuple(sorted((k, str(v)) for k, v in p.items())) + (("swa", self.swa_mode()),)
        if self._fit_key != key:
            self._fit_key, self._fit = key, self.fit_line(p)
        return self._fit

    def auto_cache_mib(self, p: Pending) -> Optional[int]:
        """The RAM cache that auto gives a start with these settings (MiB), as the launcher sizes it; None
        when RAM or the memory needed is unknown."""
        f = self.fit_cached(p)
        if not self.ram or not f.fits:
            return None
        return prompt_cache_mib(self.ram, f.need, reserve_bytes(None, False))

    def plan_of(self, m: ModelInfo) -> Optional[FitInfo]:
        """How a model runs on this Mac with its recommended context and slots (auto: 2 when two fit),
        cached per model list; None when its size is unknown. Downloaded or not (the header from
        Hugging Face, cached)."""
        hit = self._plans.get(m["name"])
        if hit is None or hit[0] != self.models.t:
            hit = self._plans[m["name"]] = (self.models.t, self._plan_of(m))
        return hit[1]

    def _plan_of(self, m: ModelInfo) -> Optional[FitInfo]:
        try:
            shape = self.store.shape_of(m["path"]) if m["status"] == "downloaded" else self.store.model_shape(m)
            if shape is None:
                return None
            weights = (self.store.file_size(m["path"]) if m["status"] == "downloaded" else int(m.get("bytes", 0)))
            vals = self.recommended(m["name"])[0]
            ctx = int(vals.get("ctx") or 98304)
            return llama_fit(m["name"], weights + draft_bytes(m), shape, str(vals.get("kv") or "q4_0"), ctx,
                             str(vals.get("slots") or "auto"), self.gpu_limit(), self.swa_mode(), draft_bytes(m))
        except Exception:           # an unreadable header, a value that is not a number: unknown
            return None

    def weights(self, m: ModelInfo) -> int:
        """A downloaded model's weights: its file and its MTP drafter's (a start with MTP loads both)."""
        return self.store.file_size(m["path"]) + draft_bytes(m)

    def max_ctx(self, m: ModelInfo) -> Optional[int]:
        """Largest window per slot (q4_0, 1 slot) that fits this Mac, None if unknown. A catalogue
        model not downloaded uses its header from Hugging Face (cached). Recomputed when the
        model list is re-read."""
        hit = self._max.get(m["name"])
        if hit is None or hit[0] != self.models.t:
            hit = self._max[m["name"]] = (self.models.t, self._max_ctx(m))
        return hit[1]

    def _max_ctx(self, m: ModelInfo) -> Optional[int]:
        try:
            full = self.swa_mode() == "full"
            if m["status"] == "downloaded":
                return max_ctx(self.store.shape_of(m["path"]), self.weights(m), self.gpu_limit(), 1, "q4_0", full)
            shape = self.store.model_shape(m)
            return None if shape is None else max_ctx(shape, int(m.get("bytes", 0)) + draft_bytes(m), self.gpu_limit(),
                                                      1, "q4_0", full)
        except Exception:           # unreadable GGUF header or file: unknown
            return None

    def config_with(self, p: Pending) -> JSONDict:
        """config.json as it would be saved with these settings."""
        cfg = self.store.load_config()
        name = self.resolved_model(p)
        return settings_to_config(p, cfg, self.schema, name, self.recommended(name)[0])

    def save(self, p: Pending) -> None:
        """Write the settings to config.json."""
        self.store.save_config(self.config_with(p))

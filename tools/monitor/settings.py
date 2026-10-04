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

from carl_core.domain.autofit import AutoFit
from carl_core.domain.fit import check_start, max_ctx, need_bytes, swa_plan

from .fmt import GRN, R, RED, YEL, ctx_label, size
from .model import JSONDict, ModelInfo, ServerData, Shape, flag, flag_int, jdict
from .store import ModelList

Value = Union[str, int]
Pending = Dict[str, Value]      # the values chosen in the Server panel, by row key
FitResult = Tuple[bool, str]    # (fits and is downloaded, the fit line)


class SettingRow(NamedTuple):
    """One Settings row. loc = where config.json keeps it: "section:key", or "m:key" for
    the model's profile (models.<name>.key); None = not saved."""
    key: str
    label: str
    choices: Optional[List[Value]]
    loc: Optional[str]
    default: Value


ADV_ROW = SettingRow("adv", "advanced", ["hidden", "shown"], None, "hidden")
LLAMA_ADV = [
    SettingRow("top_k", "top_k", ["20", "40", "0"], "m:top_k", "20"),
    SettingRow("top_p", "top_p", ["0.95", "0.9", "0.8", "1.0"], "m:top_p", "0.95"),
    SettingRow("min_p", "min_p", ["0", "0.05", "0.1"], "m:min_p", "0"),
    SettingRow("repeat", "repeat penalty", ["1.0", "1.05", "1.1"], "m:repeat", "1.0"),
    SettingRow("ub", "-ub batch", ["512", "1024", "2048"], "llama:ub", "512"),
    SettingRow("ckpt", "checkpoints", ["8", "4", "16"], "llama:ckpt", "8"),
    SettingRow("ckstep", "ckpt step", ["4096", "1024", "2048", "8192"], "llama:ckpt_step", "4096"),
]


# Auto fit's goal and scope: chosen in the Auto fit panel (saved at once), not Server-panel rows.
AUTO_ROWS = [
    SettingRow("goal", "auto goal", ["everyday", "hard-code"], "llama:auto_goal", "everyday"),
    SettingRow("scope", "auto from", ["catalogue", "downloaded"], "llama:auto_fit", "catalogue"),
]


@dataclass(frozen=True)
class Schema:
    """The Server panel's rows. The network row offers local, vm and this Mac's own
    addresses (an address = llama.host in config.json)."""
    net_choices: Tuple[str, ...]

    @property
    def llama(self) -> List[SettingRow]:
        """The llama.cpp rows (without the advanced ones)."""
        return [
            SettingRow("model", "model", None, "llama:model", "auto"),      # choices: the model list
            SettingRow("kv", "KV cache", ["q4_0", "q8_0"], "m:kv", "q4_0"),
            SettingRow("ctx", "context/slot", [32768, 49152, 65536, 98304, 131072, 163840, 196608, 262144], "m:ctx", 98304),
            SettingRow("slots", "slots", ["auto", "1", "2", "3", "4"], "m:slots", "auto"),   # 3-4 only where they fit (rows)
            SettingRow("spec", "speculation", ["none", "ngram-mod", "draft-mtp", "draft-mtp,ngram-mod"], "m:spec",
                       "draft-mtp,ngram-mod"),
            SettingRow("specn", "draft tokens", ["1", "2", "3"], "m:spec_n", "1"),
            SettingRow("cache", "RAM cache", ["auto", 1024, 2560, 4096, 6144, 8192], "llama:cache_ram", "auto"),
            SettingRow("net", "network", list(self.net_choices), "llama:net", "local"),
            SettingRow("temp", "temperature", ["1.0", "0.6"], "m:temp", "1.0"),
            SettingRow("presence", "presence", ["0", "1.5"], "m:presence", "0"),
        ]

    def saved_rows(self) -> List[SettingRow]:
        """The rows saved to config.json (the advanced ones and the Auto fit panel's included)."""
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
UNMARKED = {"slots", "cache", "net", "adv", "model", "goal", "scope"}   # no * when they differ
NOT_RUNNING = {"adv", "goal", "scope"}                  # rows without a "running now" value
REINSTALL = {"ctx", "slots"}         # clients need install.sh again when these change

ADV_WARN = "CAUTION: Auto-tune and tests measured these values (REFERENCE.md). A change can make the model " \
           "slower or its answers worse. Press x to set the tuned values again."
_NET_HELP = ("local = this Mac only (the default) · vm = this Mac and a VMware Fusion VM client (192.168.42.1) · "
             "an address = only that interface "
             "(LAN: other computers can connect)")
SET_HELP = {
    "model": "The list shows all models: the catalogue, the models folder and the Hugging Face downloads. auto is "
             "auto fit's pick for this Mac (★). The Auto fit panel tells why it picks that model and lets you set the "
             "goal (everyday / hard code). Its Use this button sets the model, context, slots and KV cache in one "
             "step. To add more models, use the Models panel: it downloads any GGUF from Hugging Face.",
    "goal": "The goal of auto fit. everyday: the MoE builds first (fast, usually good enough). hard-code: the dense "
            "builds first (better at code and hard tasks, but slower). Auto fit uses stock models only.",
    "scope": "The models that auto fit picks from. catalogue: all catalogue models. Auto fit then offers the "
             "download, and until the download is complete, a start uses the best downloaded model. downloaded: "
             "only the models on this Mac.",
    "kv": "q4_0 uses less memory and is the tested default. q8_0 recalls text far back more accurately, but uses "
          "about 2x the KV memory.",
    "ctx": "The number of tokens per slot. The colour shows how fast this Mac reads a full window cold: green = "
           "fast, yellow = slow, red = very slow (see the context zones).",
    "slots": "auto: 2 slots when two full windows fit (main session + coder subagent), else 1. 3-4: more subagents "
             "at the same time. The row shows 3-4 only when they fit this Mac with this model, window and KV cache. "
             "More slots give more tokens per second in total, but each slot is slower. The parallel step of "
             "Auto-tune measures this.",
    "spec": "Speculative decoding. n-gram copies repeated text. MTP makes drafts with the model's own head. "
            "Auto-tune measures which mode is faster.",
    "specn": "The number of draft tokens per speculation step. On Metal, more draft tokens do not make dense "
             "models faster.",
    "cache": "The RAM prompt cache in MiB. It keeps the prompts that leave the slots. When a session comes back, "
             "the server does not read it again in full.",
    "net": _NET_HELP,
    "temp": "1.0 = the Qwen value for thinking mode (the default) · 0.6 = more precise code (35B card)",
    "presence": "0 = the default · 1.5 = fewer repeat loops (35B card, general use)",
    "adv": "Shows more server settings: sampling, batch and checkpoints.",
    "top_k": "The model samples from the k most likely tokens. Qwen: 20 · 0 = off.",
    "top_p": "Nucleus sampling. Qwen: 0.95 with thinking, 0.8 without thinking (the client sends this value).",
    "min_p": "The model ignores the tokens below min_p × the top probability. Qwen: 0.",
    "repeat": "The repeat penalty. Qwen: 1.0 (off). Use presence instead.",
    "ub": "The -ub physical batch. On Metal, 512 is the fastest: 90.5 tok/s, against 88.6 / 86.1 tok/s for "
          "1024 / 2048.",
    "ckpt": "The context checkpoints per slot. Each one uses about 63 MiB on the 35B and 150 MiB on the 27B. More "
            "checkpoints did not help in the Phase 6 test.",
    "ckstep": "The minimum number of tokens between checkpoints. In the Phase 6 test, 1024 and 4096 gave the same "
              "result.",
}


def row_instruction(key: str) -> str:
    """How to change a Settings row, for someone new to the dashboard."""
    if key == "model":
        return ("Press Enter to choose a model from the list, or click a model. Press A to see auto fit's pick "
                "and the reason.")
    if key == "adv":
        return "Press ← → to show or hide the advanced settings."
    if key in NUMERIC:
        return "Type a number, then press Enter. To go through the usual values, press ← →."
    return "Press ← → to change the value."


def rows(p: Pending, schema: Schema, model_choices: Callable[[], List[str]]) -> List[SettingRow]:
    """The rows shown (the advanced ones when shown); the model row offers model_choices()."""
    adv = LLAMA_ADV if p.get("adv") == "shown" else []
    base = [r._replace(choices=list(model_choices())) if r.key == "model" else r for r in schema.llama]
    return base + [ADV_ROW] + adv


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


def shown_value(key: str, v: object) -> str:
    """A row value on screen: contexts as 96K."""
    return ctx_label(v) if key == "ctx" else str(v)


def parse_typed(key: str, text: str) -> Tuple[Optional[Value], str]:
    """A value typed for a numeric row ("96k", "0.6", "4096"): (value, "") if valid, else (None, why)."""
    v = text.strip().lower()
    try:
        num = float(v[:-1]) * 1024 if v.endswith("k") else float(v)
        whole = int(num)
    except (ValueError, OverflowError):
        return None, f"not a number: {v!r}"
    if num < 0 or (key == "ctx" and not 4096 <= num <= 262144) or (key in ("top_p", "min_p") and num > 1):
        return None, f"{v} is out of range for {key}"
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
def _fits(ok: bool) -> str:
    return f"{GRN if ok else RED}{'fits' if ok else 'does not fit'}{R}"


def llama_need(weights: int, shape: Shape, kv: str, ctx: int, n: int, swa_full: bool = True) -> float:
    """GPU bytes for a llama.cpp model with n slots of ctx tokens each (carl_core.domain.fit)."""
    return need_bytes(shape, weights, ctx, n, kv, swa_full)


def llama_fit(name: str, weights: int, shape: Shape, kv: str, ctx: int, slots: str, limit: int,
              swa: str = "auto") -> FitResult:
    """Does the model fit with these settings? Slots and, for a model with sliding-window layers, the
    full or the window cache as the launcher decides (fit.swa_plan with cache.swa); the same check the
    launcher refuses a start with (carl_core.domain.fit.check_start)."""
    n, full = swa_plan(swa, shape, weights, ctx, slots, kv, limit)
    chk = check_start(shape, weights, ctx, n, kv, limit, full is not False)
    text = f"{_fits(chk.fits)}: {name} needs {size(chk.need)} for {n} × {ctx_label(ctx)} ({kv}) of {size(limit)} GPU memory"
    if full is not None:
        text += (" · sliding-window layers: full cache (CARL can restore saved prompts)" if full
                 else f" · sliding-window layers: {YEL}window only{R} (CARL cannot restore saved prompts: Settings > "
                      f"Caching)")
    if not chk.fits:
        text += (f" · largest window: {ctx_label(chk.largest)}" if chk.largest else " · the weights alone do not fit")
    return chk.fits, text


def max_ctx_per_slot(weights: int, shape: Shape, limit: int, swa_full: bool = True) -> int:
    """Largest window (q4_0, 1 slot, in steps of 4K) that fits limit, at most the trained context."""
    return max_ctx(shape, weights, limit, 1, "q4_0", swa_full)


class SettingsService:
    """Settings logic that needs the models and config.json (through the ModelStore port)."""

    def __init__(self, models: ModelList, schema: Schema, vm_addr: str, gpu_limit: Callable[[], int]) -> None:
        self.models = models
        self.store = models.store
        self.schema = schema
        self.vm_addr = vm_addr
        self.gpu_limit = gpu_limit          # bytes; the collector's cached value when it has one
        self._fit_key: Optional[Tuple[Tuple[str, str], ...]] = None
        self._fit: FitResult = (False, "")
        self._max: Dict[str, Tuple[float, Optional[int]]] = {}     # model -> (list time, largest window)
        self._slots: Dict[Tuple[str, str, str, str], int] = {}      # (model, ctx, kv, swa) -> most slots that fit

    def rows(self, p: Pending) -> List[SettingRow]:
        """The rows shown for p (the model row offers every model; the slots row 3 and 4 only when
        they fit this Mac with the pending model, window and KV cache)."""
        out = rows(p, self.schema, self.models.choices)
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
            shape, w = self.store.shape_of(m["path"]), self.store.file_size(m["path"])
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
        p: Pending = {"adv": "hidden"}
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
        """Every row at its default, advanced / model kept, the model rows at its tune."""
        q = self.schema.defaults()
        q.update(adv=p.get("adv", "hidden"), model=p.get("model", "auto"), goal=p.get("goal", "everyday"),
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
            if "draft-mtp" in spec and not info.get("mtp"):
                return RED                                      # no MTP head in this file
            if ("draft-mtp" in spec and str(p.get("specn")) != "1"
                    and str(info.get("quant", "")).upper().replace("UD-", "").startswith("IQ")):
                return RED                                      # MTP n>1 loses ~15% on IQ quants (measured 2026-10-03)
        if key in MODEL_ROW_KEYS:
            rec = self.recommended(name)[0][MODEL_ROW_KEYS[key]]
            return GRN if str(fmt_val(key, rec)) == str(p[key]) else YEL
        return ""

    def fit_line(self, p: Pending) -> FitResult:
        """(ok, text): does the pending setup fit the GPU limit, and is the model downloaded?"""
        name = self.resolved_model(p)
        m = self.models.by_name(name)
        if not m:
            return False, f"{RED}unknown model {name}{R}"
        if m["status"] != "downloaded":
            return False, (f"{RED}{name} is not downloaded{R}. To download it, press ] for the Models panel, then d "
                           f"(or run ./carl.sh download {name}).")
        try:
            return llama_fit(name, self.store.file_size(m["path"]), self.store.shape_of(m["path"]), str(p["kv"]),
                             int(p["ctx"]), str(p["slots"]), self.gpu_limit(), self.swa_mode())
        except Exception as e:      # a GGUF that can't be read, a value that is not a number, ...: say so
            return False, f"{RED}fit check failed: {e}{R}"

    def fit_cached(self, p: Pending) -> FitResult:
        """fit_line, recomputed only when a pending value changes (it reads the GGUF header)."""
        key = tuple(sorted((k, str(v)) for k, v in p.items())) + (("swa", self.swa_mode()),)
        if self._fit_key != key:
            self._fit_key, self._fit = key, self.fit_line(p)
        return self._fit

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
            if m["status"] == "downloaded":
                return max_ctx_per_slot(self.store.file_size(m["path"]), self.store.shape_of(m["path"]), self.gpu_limit(),
                                        self.swa_mode() == "full")
            shape = self.store.model_shape(m)
            return None if shape is None else max_ctx_per_slot(int(m.get("bytes", 0)), shape, self.gpu_limit(),
                                                               self.swa_mode() == "full")
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

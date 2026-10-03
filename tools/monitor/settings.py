"""The Settings tab's model: its rows, how values are shown and coloured, whether a setup
fits the GPU, and how the chosen values map to ~/.config/llm-deploy/config.json.

The Settings tab chooses the llama.cpp server's model and settings, saves them to
config.json (tools/carl.py; the launchers read it: flags > environment > config.json >
Auto-tune > catalogue) and restarts the server. Pure, except SettingsService, which reads
models, tunes and config.json through the ModelStore port."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from .fmt import GRN, R, RED, YEL, ctx_label, size
from .gguf import OVERHEAD, kv_bytes_per_token
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


@dataclass(frozen=True)
class Schema:
    """The Server panel's rows. The network row offers auto, local, vm and this Mac's
    own addresses (an address = llama.host in config.json)."""
    net_choices: Tuple[str, ...]

    @property
    def llama(self) -> List[SettingRow]:
        """The llama.cpp rows (without the advanced ones)."""
        return [
            SettingRow("model", "model", None, "llama:model", "auto"),      # choices: the model list
            SettingRow("kv", "KV cache", ["q4_0", "q8_0"], "m:kv", "q4_0"),
            SettingRow("ctx", "context/slot", [32768, 49152, 65536, 98304, 131072, 163840, 196608, 262144], "m:ctx", 98304),
            SettingRow("slots", "slots", ["auto", "1", "2"], "m:slots", "auto"),
            SettingRow("spec", "speculation", ["none", "ngram-mod", "draft-mtp", "draft-mtp,ngram-mod"], "m:spec",
                       "draft-mtp,ngram-mod"),
            SettingRow("specn", "draft tokens", ["1", "2", "3"], "m:spec_n", "1"),
            SettingRow("cache", "RAM cache", ["auto", 1024, 2560, 4096, 6144, 8192], "llama:cache_ram", "auto"),
            SettingRow("net", "network", list(self.net_choices), "llama:net", "auto"),
            SettingRow("temp", "temperature", ["1.0", "0.6"], "m:temp", "1.0"),
            SettingRow("presence", "presence", ["0", "1.5"], "m:presence", "0"),
        ]

    def saved_rows(self) -> List[SettingRow]:
        """The rows saved to config.json (the advanced ones included)."""
        return self.llama + LLAMA_ADV

    def defaults(self) -> Pending:
        """Every row at its default."""
        return {r.key: r.default for r in self.saved_rows()}


def net_choices(addrs: Sequence[str]) -> Tuple[str, ...]:
    """The network row's choices: auto, local, vm, then this Mac's addresses."""
    return ("auto", "local", "vm", *addrs)


# row key -> key of the model's profile (config.json models.<name>, Auto-tune, catalogue)
MODEL_ROW_KEYS = {r.key: r.loc[2:] for r in Schema(()).llama + LLAMA_ADV if r.loc and r.loc.startswith("m:")}
NUMERIC = {"ctx", "temp", "presence", "top_k", "top_p", "min_p", "repeat", "specn", "ub", "ckpt", "ckstep",
           "cache"}       # Enter types a value
INT_KEYS = {"ctx", "cache", "top_k", "specn", "ub", "ckpt", "ckstep"}
FROM_RUNNING = {"kv", "ctx", "temp", "presence", "spec", "specn", "top_k", "top_p", "min_p", "repeat", "ckpt", "ckstep",
                "ub", "slots"}
UNMARKED = {"slots", "cache", "net", "adv", "model"}   # no * when they differ
REINSTALL = {"ctx", "slots"}         # clients need install.sh again when these change

ADV_WARN = ("CAUTION: these values are tuned and measured (REFERENCE.md).",
            "         A change can make the model slower, or its answers worse. Defaults (x) sets them back.")
_NET_HELP = ("auto = the VM address if VMware's network is up, else this Mac only · an address = that interface "
             "(LAN: other computers can reach it)")
SET_HELP = {
    "model": "Enter: pick from every model (catalogue, models folder, Hugging Face downloads) · auto = this Mac's default · "
             "any other model: ] Models panel → Add from Hugging Face (h)",
    "kv": "q4_0: less memory, the tested default · q8_0: more exact long-range recall, about 2x the KV memory",
    "ctx": "tokens per slot; green = fast cold reads on this Mac, yellow = slow, red = very slow (see the context zones)",
    "slots": "auto = 2 when two full windows fit (main session + coder subagent), else 1",
    "spec": "speculative decoding: n-gram copies repeated text, MTP drafts with the model's own head; Auto-tune measures which wins",
    "specn": "draft tokens per speculation step; more is not faster on Metal for dense models",
    "cache": "RAM prompt cache in MiB: keeps evicted prompts so a session comes back without a full re-read",
    "net": _NET_HELP,
    "temp": "1.0 = Qwen's thinking-mode value (default) · 0.6 = more precise coding (35B card)",
    "presence": "0 = default · 1.5 = fewer repetition loops (35B card, general use)",
    "adv": "more server settings: sampling, batch and checkpoints",
    "top_k": "sample from the k most likely tokens; Qwen: 20 · 0 = off",
    "top_p": "nucleus sampling; Qwen: 0.95 (thinking), 0.8 (no thinking: the client sends it)",
    "min_p": "drop tokens below min_p × the top probability; Qwen: 0",
    "repeat": "repetition penalty; Qwen: 1.0 (off) · use presence instead",
    "ub": "-ub physical batch; 512 measured best on Metal (90.5 vs 88.6 / 86.1 tok/s for 1024 / 2048)",
    "ckpt": "context checkpoints per slot (each ~63 MiB on the 35B, ~150 MiB on the 27B); more did not help (Phase 6)",
    "ckstep": "minimum tokens between checkpoints; 1024 vs 4096 made no difference in the Phase 6 test",
}


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


def llama_need(weights: int, shape: Shape, kv: str, ctx: int, n: int) -> float:
    """GPU bytes for a llama.cpp model with n slots of ctx tokens each."""
    return weights + kv_bytes_per_token(shape, kv, kv) * ctx * n + shape["rs_bytes"] * n + OVERHEAD


def llama_fit(name: str, weights: int, shape: Shape, kv: str, ctx: int, slots: str, limit: int) -> FitResult:
    """Does the model fit with these settings? slots "auto" = 2 when two fit, else 1."""
    if slots == "auto":
        n = 2 if llama_need(weights, shape, kv, ctx, 2) <= limit else 1
    else:
        n = int(slots)
    need = llama_need(weights, shape, kv, ctx, n)
    ok = need <= limit
    return ok, f"{_fits(ok)}: {name} needs {size(need)} for {n} × {ctx_label(ctx)} ({kv}) of {size(limit)} GPU memory"


def max_ctx_per_slot(weights: int, shape: Shape, limit: int) -> int:
    """Largest window (q4_0, 1 slot, in steps of 4K) that fits limit, at most the trained context."""
    room = limit - weights - shape["rs_bytes"] - OVERHEAD
    return 0 if room <= 0 else min(int(room // kv_bytes_per_token(shape, "q4_0")) // 4096 * 4096, shape.get("ctx_train") or 262144)


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

    def rows(self, p: Pending) -> List[SettingRow]:
        """The rows shown for p (the model row offers every model)."""
        return rows(p, self.schema, self.models.choices)

    def resolved_model(self, p: Pending) -> str:
        """The model a start with these settings loads ("auto" resolved for this Mac)."""
        name = str(p.get("model", "auto"))
        return name if name != "auto" else self.models.auto_model()

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
        q.update(adv=p.get("adv", "hidden"), model=p.get("model", "auto"))
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
            return False, f"{RED}{name} is not downloaded{R}: Models panel (]) or ./carl.sh download {name}"
        try:
            return llama_fit(name, self.store.file_size(m["path"]), self.store.shape_of(m["path"]), str(p["kv"]),
                             int(p["ctx"]), str(p["slots"]), self.gpu_limit())
        except Exception as e:      # a GGUF that can't be read, a value that is not a number, ...: say so
            return False, f"{RED}fit check failed: {e}{R}"

    def fit_cached(self, p: Pending) -> FitResult:
        """fit_line, recomputed only when a pending value changes (it reads the GGUF header)."""
        key = tuple(sorted((k, str(v)) for k, v in p.items()))
        if self._fit_key != key:
            self._fit_key, self._fit = key, self.fit_line(p)
        return self._fit

    def max_ctx(self, m: ModelInfo) -> Optional[int]:
        """Largest window per slot (q4_0, 1 slot) that fits this Mac, or None if unknown."""
        if m["status"] != "downloaded":
            return None
        try:
            return max_ctx_per_slot(self.store.file_size(m["path"]), self.store.shape_of(m["path"]), self.gpu_limit())
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

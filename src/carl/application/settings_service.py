"""The Settings tab's model: its rows, how values are shown and coloured, whether a setup
fits the GPU, and how the chosen values map to ~/.config/carl/config.json.

The Settings tab chooses the llama.cpp server's model and settings, saves them to
config.json (`carl config` writes the same file; a start reads it: flags > environment > config.json >
Auto-tune > catalogue) and restarts the server. The rows come from the settings schema
(domain/config/server_rows.py), the running server's values from its command line (domain/server/cmdline.py).
Pure, except SettingsService, which reads models, tunes and config.json through the ModelStore port; every change of
config.json the dashboard makes is one of its services (a read, the change, a save; contract C-126)."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from ..domain.catalog import thinking
from ..domain.catalog.drafts import FILE_SPECS, TUNE_NS
from ..domain.catalog.inventory import group_of
from ..domain.catalog.layout import FALLBACK_QUANT
from ..domain.catalog.quants import ModelGroup, group_models, mtp_kind, quant_of
from ..domain.config import server_rows
from ..domain.config.settings import CACHE_KEYS, DELEGATION_KEYS, GPU_KEYS, LLAMA_KEYS, coerce
from ..domain.config.server_rows import (INT_KEYS, MODEL_ROW_KEYS, NUMERIC, Pending, SettingRow,  # noqa: F401
                                         Value, number_text, parse_typed, step_number, stepped, typed_at)
from ..domain.memory.autofit import AutoFit, Candidate, model_plan, setup_text, window_cap
from ..domain.memory import loaded, offload
from ..domain.memory.fit import (DEFAULT_CTX, UBATCH, Spec, max_ctx, mtp_spec, need_bytes, need_parts, ub_choice)
from ..domain.server import ub_order as uo
from ..domain.memory.split import (Slot, calib_of, fits as split_fits, load_of, max_ctx as split_max_ctx, one_limit,
                                    plan_split)
from ..domain.server.cmdline import CLEAN_ENV, env_from_cmd  # noqa: F401  (their names here too)
from ..domain.server.cmdline import running_settings as _running
from ..domain.server.launch_plan import own_load
from ..domain.server.snapshot import (JSONDict, ModelInfo, ServerData, Shape, draft_bytes, drafter_missing, jdict,
                                      spec_draft_bytes, vision_bytes)
from ..domain.shared.ansi import GRN, RED, YEL
from ..domain.shared.units import GIB, MIB, memory, tokens
from . import fit as budget_builder
from .model_list import ModelList

SPEC_COMBOS = ["none|1", "ngram-mod|1", "draft-mtp|1", "draft-mtp|2", "draft-mtp|3", "draft-mtp|4",
               "draft-mtp,ngram-mod|1", "draft-mtp,ngram-mod|2", "draft-mtp,ngram-mod|3",
               "draft-mtp,ngram-mod|4",          # the Speculation row: a mode and the drafter's guesses together (n-gram has none)
               *(f"{s}|{n}" for s in FILE_SPECS for n in TUNE_NS[s])]     # then the drafter files of other kinds (WCATX)
LLAMA_ADV = server_rows.adv_rows()      # the full detail level's rows (More settings)


# Auto fit's goal and scope: chosen in the Auto fit panel (saved at once), not Server-panel rows.
AUTO_ROWS = server_rows.auto_rows()


@dataclass(frozen=True)
class Schema:
    """The Server panel's rows, built from the settings schema (domain/config/server_rows.py) for this computer's OS
    (os_name; "": every OS). The network row offers local, vm and this computer's own addresses (an address =
    llama.host in config.json)."""
    net_choices: Tuple[str, ...]
    os_name: str = ""

    @property
    def llama(self) -> List[SettingRow]:
        """The llama.cpp rows (without the More settings rows of the full detail level)."""
        return server_rows.llama_rows(self.net_choices, self.os_name)

    @property
    def adv(self) -> List[SettingRow]:
        """The More settings rows of the full detail level."""
        return server_rows.adv_rows(self.os_name)

    def saved_rows(self) -> List[SettingRow]:
        """The rows saved to config.json (the More settings rows and the Auto fit panel's included; not a view such as
        Quantization, which is the Model row's entry)."""
        return [r for r in self.llama + self.adv + AUTO_ROWS if r.loc]

    def defaults(self) -> Pending:
        """Every row at its default."""
        return {r.key: r.default for r in self.saved_rows()}


def net_choices(addrs: Sequence[str]) -> Tuple[str, ...]:
    """The network row's choices: local, vm, then this Mac's addresses."""
    return ("local", "vm", *addrs)


FROM_RUNNING = {"kv", "ctx", "temp", "presence", "spec", "specn", "top_k", "top_p", "min_p", "repeat", "ckpt", "ckstep",
                "slots"}            # not ub: the row is the model's -ub (A51), not what runs (a start may have lowered it)
# Phase 23.4.4: the clients' thinking of each role, per model: the Agents panel (saved at once, no restart)
THINKING_ROWS = ("thinking_main", "thinking_coder")
THINKING_ROLE = {"thinking_main": "main", "thinking_coder": "coder"}
THINKING_LABELS = {"thinking_main": "Main thinking", "thinking_coder": "Coder thinking"}
NOT_RUNNING = {"goal", "scope", "keep", "backend", "shared"}    # rows without a "running now" value
FULL_ONLY = {"presence"} | {r.key for r in LLAMA_ADV}   # rows of the full detail level (More settings)
REINSTALL = {"ctx", "slots"}         # clients need the setup again when these change

ADV_WARN = ("Caution: Auto-tune and tests measured these values (reference/performance.md). A change can make the model "
            "slower or its answers worse. Press x to use the recommended settings again.")
SWA_NAMES = {"auto": "auto", "full": "full cache", "window": "window cache"}
def coder_off(v: Mapping[str, object]) -> bool:
    """The coder thinks off: Coder thinking off, or same as main with Main thinking off."""
    return v.get("thinking_coder") == "off" or (v.get("thinking_coder") == thinking.MAIN and v.get("thinking_main") == "off")


class AgentsInfo(NamedTuple):
    """The Agents panel's model: its thinking kind ("effort" or "on-off"), and for thinking_main and thinking_coder:
    your choice, the recommended value, where that comes from, the values it takes."""
    name: str
    kind: str
    mine: Dict[str, str]
    rec: Dict[str, str]
    source: Dict[str, str]
    choices: Dict[str, Tuple[str, ...]]


CODER_OFF_NOTE = ("Coder thinking is off. Use it only with a full spec (spec-kit or a similar tool): a coder without "
                  "thinking does well only when every requirement is written down.")


def row_instruction(key: str) -> str:
    """How to change a Settings row, for someone new to the dashboard."""
    if key == "model":
        return "Press Enter to choose a model from the list. Press A to see Auto fit's choice and why."
    if key == "quant":
        return "Press ← → for another quantization of this model. ● downloaded, ○ not downloaded."
    if key == "cpu_moe":
        return ("Press ← → for the presets; ← from 0 is auto. At the full level (L), ← → change it by one layer, and "
                "you can type a number, then press Enter.")
    if key in NUMERIC and stepped(key):
        return ("Press ← → to step through the presets. At the full level (L), ← → take fine steps, and you can "
                "type a number, then press Enter.")
    if key in NUMERIC:
        return "Press ← → for auto. Or type a number, then press Enter."
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
    return out + (schema.adv if full else [])


def _is_number(v: object) -> bool:
    return str(v).replace(".", "", 1).isdigit()


def fmt_val(key: str, v: object) -> Value:
    """A config / tune value as the Settings rows show it."""
    if key in ("ctx", "cache"):
        return int(str(v)) if str(v).isdigit() else (v if isinstance(v, (str, int)) else str(v))
    if key in ("temp", "repeat") and _is_number(v):
        return number_text(key, float(str(v)))          # 1.0, 0.65 (a step of 0.05 is kept)
    if key in ("presence", "min_p", "top_p") and _is_number(v):
        return f"{float(str(v)):g}"
    return str(v)


def spec_value(p: Pending) -> str:
    """The Speculation row's value: the mode and MTP's guesses ("draft-mtp|1"); n-gram alone and none: "|1" (n-gram has
    no guesses to set: Phase 23.5.1.8)."""
    spec = str(p.get("spec", "none"))
    return f"{spec}|{p.get('specn', '1') if 'draft-' in spec else '1'}"


def step_choice(choices: Sequence[Value], cur: Value, step: int) -> Value:
    """The choice step places after cur (cyclic); from the first one if cur is not a choice."""
    j = next((n for n, c in enumerate(choices) if str(c) == str(cur)), 0)
    return choices[(j + step) % len(choices)]


def running_settings(d: ServerData, vm_addr: str, find: Callable[[str], Optional[ModelInfo]]) -> Pending:
    """The values the running llama.cpp server uses, from its command line ({} when none runs; domain/server/cmdline.py,
    contract C-134)."""
    return _running(d, vm_addr, find)  # type: ignore[return-value]


def settings_to_config(p: Pending, cfg: JSONDict, schema: Schema, model: Optional[str],
                       tuned: Optional[JSONDict], ub_auto: Optional[str] = None, ub_stamp: str = "",
                       ub_loaded: Optional[str] = None) -> JSONDict:
    """config.json with the pending settings: server-wide values that differ from the defaults,
    and the model's profile = the values that differ from its tune (Auto-tune / catalogue).
    model / tuned: the model a start loads and its tune. ub_auto: unused (the Batch size row holds "auto" itself since
    W1.6). The Batch size row is saved as that model's choice (models.NAME.ub, a size stamped with the tune it was made
    against: ub_stamp) only when the person changed the row from ub_loaded, the value it was loaded with (the -ub
    order's, so a typed -ub or llama.ub is not a choice made here, A53); a row left alone keeps whatever the model has
    saved. auto (W1.6): the order picks the -ub; the model saves nothing, or "auto" when a llama.ub would else apply.
    ub_loaded None: any row is a choice, as before A53. There is no global -ub (A51). A choice that stands keeps its
    stamp. The Experts in RAM row (W1.6, B2) is the model's own models.NAME.n_cpu_moe,
    saved only when it differs from llama.n_cpu_moe (else auto), which it never changes. Returns a new dict."""
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
        prof = {pk: p[key] for key, pk in MODEL_ROW_KEYS.items()
                if key != "ub" and str(fmt_val(key, tuned[pk])) != str(p[key])}
        cfg.setdefault("models", {})
        old = jdict(cfg["models"].get(model))
        prof.update({k: old[k] for k in THINKING_ROWS if k in old})    # the Agents panel's: saved at once, kept
        if "ub" in p and ub_loaded is not None and str(p["ub"]) == str(ub_loaded):
            for k in ("ub", "ub_tune"):                                 # the row was left alone (C-126): what is
                if k in old:                                            # saved stays, nothing is added
                    prof[k] = old[k]
        elif "ub" in p and str(p["ub"]) == "auto":          # the order picks (W1.6): auto over a llama.ub only
            if isinstance(jdict(cfg.get("llama")).get("ub"), int):
                prof["ub"] = "auto"
        elif "ub" in p:
            prof["ub"] = int(str(p["ub"]))                              # this model's choice (A51)
            same = str(old.get("ub")) == str(p["ub"])
            prof["ub_tune"] = str(old.get("ub_tune") or "") if same else ub_stamp
        if "cpu_moe" in p:      # Experts in RAM, the model's own (B2): saved when it is not what llama.n_cpu_moe gives
            fallback = jdict(cfg.get("llama")).get("n_cpu_moe", "auto")
            if str(p["cpu_moe"]) != str(fallback):
                prof["n_cpu_moe"] = int(str(p["cpu_moe"])) if str(p["cpu_moe"]).isdigit() else "auto"
        elif "n_cpu_moe" in old:                                        # a computer without the row: kept
            prof["n_cpu_moe"] = old["n_cpu_moe"]
        if prof:
            cfg["models"][model] = prof
        else:
            cfg["models"].pop(model, None)
    return cfg


# ---------------------------------------------------------------- fit maths
@dataclass(frozen=True)
class FitInfo:
    """Does a setup fit the GPU memory limit? The parts of the memory it needs (bytes), the slots a start
    uses (auto resolved), the sliding-window cache (full: True, window: False, None: no such layers),
    the largest context per slot that fits with these slots. ok = known, downloaded and fits. Over several GPUs
    (gpus) need is the split's (each GPU with a compute buffer of its own), limit the rooms added up, and split_ok
    whether the layers fit the GPUs one by one (domain/memory/split.py), as the launcher's check decides."""
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
    spec: str = ""                  # the speculation the start runs (MTP dropped when it does not fit)
    dropped: bool = False           # MTP does not fit with these slots and this context: n-gram only
    gpus: int = 0                   # the GPUs the setup was checked over as a split (0: one limit)
    split_ok: bool = True           # over several GPUs: the layers fit them (the rooms added up are not enough)
    cpu_moe: int = 0                # a MoE model's layers whose experts stay in the RAM (--n-cpu-moe; W6.8)
    moe_layers: int = 0             # the model's layers with experts: the Experts in RAM row's top (0: dense, unknown)
    experts: float = 0              # their bytes in the RAM
    ram_ok: bool = True             # the RAM side fits the RAM less the reserve
    ram_need: float = 0             # the RAM side: the experts, the other weights there, the CPU's compute buffer (W1.6)
    ram_room: float = 0             # the RAM less the part kept for the system and apps (0: not known)
    ram_other: float = 0            # of the RAM side, the weights besides the experts (token_embd, Gemma's per-layer)
    ub: int = 0                     # the -ub the fit is at: the one the start uses (start_fit; 0: not known)

    @property
    def fits(self) -> bool:
        return (self.known and self.downloaded and not self.error and self.need <= self.limit and self.split_ok
                and self.ram_ok)

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
    """'2 slots × 96K tokens' (the package's words, as the CLI says it)."""
    return setup_text(slots, ctx)


def llama_fit(name: str, weights: int, shape: Shape, kv: str, ctx: int, slots: str, limit: int,
              swa: str = "auto", drafter: int = 0, spec: str = "none", spec_n: int = 1, ub: int = UBATCH,
              devices: Sequence[Slot] = (), ram: float = 0.0, cpu_moe: Optional[int] = None,
              kept: bool = False, vision: int = 0) -> FitInfo:
    """Does the model fit with these settings? Slots and, for a model with sliding-window layers, the
    full or the window cache as the launcher decides (fit.swa_plan with cache.swa); the same check the
    launcher refuses a start with (carl.domain.memory.fit.check_start). weights include the drafter; spec,
    spec_n and ub: the start's speculation (MTP counted, and dropped before a slot as the launcher does) and -ub.
    devices: this PC's GPUs (FitService.slots); over several, the plan and the check are the launcher's over the split
    (domain/memory/split.py: need = the split's, limit = the rooms added up, largest = the split's), not the rooms
    added up. The plan: carl.application.fit, the one StartPlan builder. A MoE model on a PC's GPUs that does not fit
    them whole keeps the experts of some layers in the RAM, as the start plans it (start_offload, W6.7): need is then the
    GPU side, cpu_moe the layers (cpu_moe: the setting llama.n_cpu_moe; None: auto); ram: this computer's RAM. kept: the
    speculation is a person's or Auto-tune's choice, so MTP stays with experts in the RAM (A51). vision: the size of the
    vision projector a start with vision on loads (WCATX)."""
    file = weights - drafter
    if devices and offload.offloads(devices, shape):
        found = offload_fit(name, shape, file, drafter, kv, ctx, slots, limit, swa, mtp_spec(spec, spec_n, shape, drafter, kept, vision),
                            ub, devices, ram, cpu_moe)
        if found is not None:
            return found
    limit = int(one_limit(devices, limit))       # one device: its own room, not every GPU's added up
    plan = budget_builder.start_plan(swa, shape, file, ctx, slots, kv, limit,
                                     mtp_spec(spec, spec_n, shape, drafter, kept, vision), ub, devices)
    sw = plan.swa_full is not False
    calib = calib_of(devices)                    # the backend's constants (CUDA); the Mac's with no device
    parts = need_parts(shape, file, ctx, plan.slots, kv, sw, plan.spec, ub, calib)
    if len(devices) > 1:
        split = plan_split(devices, load_of(shape, file, ctx, plan.slots, kv, sw, plan.spec, ub, calib))
        return FitInfo(name=name, need=split.need, limit=split.room, ctx=ctx, slots=plan.slots, kv=kv,
                       full=plan.swa_full,
                       largest=split_max_ctx(shape, file, devices, plan.slots, kv, sw, plan.spec, ub, calib),
                       weights=parts.weights,
                       drafter=int(parts.drafter), context=parts.context, state=parts.state,
                       buffers=max(split.need - parts.weights - parts.drafter - parts.context - parts.state, 0.0),
                       spec=plan.spec.kind, dropped=plan.dropped, gpus=len(devices), split_ok=split.fits)
    return FitInfo(name=name, need=parts.total, limit=limit, ctx=ctx, slots=plan.slots, kv=kv, full=plan.swa_full,
                   largest=max_ctx(shape, file, limit, plan.slots, kv, sw, plan.spec, ub, calib), weights=parts.weights,
                   drafter=int(parts.drafter), context=parts.context, state=parts.state, buffers=parts.buffers,
                   spec=plan.spec.kind, dropped=plan.dropped)


def offload_fit(name: str, shape: Shape, file: int, drafter: int, kv: str, ctx: int, slots: str, limit: int, swa: str,
                spec: Spec, ub: int, devices: Sequence[Slot], ram: float, cpu_moe: Optional[int]) -> Optional[FitInfo]:
    """llama_fit with the experts of some layers in the RAM, as the start plans it; None when the start keeps every
    expert on the GPUs."""
    sp, off = budget_builder.check_offload(swa, shape, file, ctx, slots, kv, limit, spec, ub, devices, ram, cpu_moe)
    if off is None:
        return None
    sw = sp.swa_full is not False
    parts = need_parts(shape, file, ctx, sp.slots, kv, sw, sp.spec, ub, calib_of(devices))
    on_gpu = max(parts.weights - off.experts, 0.0)          # the weights less the experts in the RAM
    rest = off.split.need - on_gpu - parts.drafter - parts.context - parts.state
    calib = calib_of(devices)
    other = (float(shape.get("embd_bytes", 0) or 0) if calib.gpu_weights else 0.0) + calib.ram_bytes(shape)
    return FitInfo(name=name, need=off.split.need, limit=off.split.room, ctx=ctx, slots=sp.slots, kv=kv,
                   full=sp.swa_full, largest=ctx if off.fits else 0, weights=on_gpu, drafter=int(parts.drafter),
                   context=parts.context, state=parts.state, buffers=max(rest, 0.0), spec=sp.spec.kind,
                   dropped=sp.dropped, gpus=len(devices), split_ok=off.gpu_fits, cpu_moe=off.n, experts=off.experts,
                   ram_ok=off.ram_fits, ram_need=off.ram, ram_room=off.room, ram_other=min(other, off.ram))


def ram_resolved(asked: str, ran: str, cpu_moe: object) -> bool:
    """The speculation a start ran (ran) is the one asked for (asked) as a start with experts in the RAM resolves it:
    MTP off, its n-gram part on (offload.ram_spec, A51), with some layers' experts in the RAM (W1.6: not a change)."""
    n = int(str(cpu_moe)) if str(cpu_moe).isdigit() else 0
    want = Spec(asked or "none")
    return n > 0 and want.mtp and asked != ran and want.without_mtp().kind == ran


def cpu_moe_of(p: Pending) -> Optional[int]:
    """The Experts in RAM row as llama_fit takes it: None for auto (or no such row), else the number of layers."""
    v = str(p.get("cpu_moe", "auto"))
    return int(v) if v.isdigit() else None


def pending_spec(p: Pending) -> Tuple[str, int, int]:
    """(speculation, guesses, -ub) of pending settings, for llama_fit."""
    def num(v: object, default: int) -> int:
        try:
            return int(str(v))
        except ValueError:
            return default
    return str(p.get("spec") or "none"), num(p.get("specn"), 1), num(p.get("ub"), UBATCH)


UB_AUTO = "auto"                # the Batch size row's auto (W1.6): the -ub order picks the size


def start_fit(at: Callable[[int], FitInfo], ub: int, auto: bool, pick: bool = False) -> FitInfo:
    """The fit at the -ub the start uses: ub as it is (a person's: typed, the model's saved choice, llama.ub), or (auto:
    the model's own, A45) the -ub that keeps the setup -ub 512 gets (fit.ub_choice: the slots, the cache, the
    speculation and the fit), as the start's plan lowers it; pick (no tune record, W1.6): of the sizes up to ub the
    largest that keeps that setup (ub_order.ub_pick). at(ub): the fit at that -ub."""
    if not auto:
        return replace(at(ub), ub=ub)
    seen: Dict[int, FitInfo] = {}

    def setup(u: int) -> Tuple[object, str]:
        f = seen[u] = seen.get(u) or at(u)
        return (f.slots, f.full, f.spec, f.fits), setup_text(f.slots, f.ctx)
    if pick and ub > UBATCH:
        ub = uo.ub_pick(ub, lambda u: (setup(u)[0], seen[u].cpu_moe))
    chosen = ub_choice(ub, setup).ub
    return replace(seen.get(chosen) or at(chosen), ub=chosen)


DEVICES_TTL = 5.0       # seconds the GPUs of a PC stand (the adapter asks the driver what the servers hold)


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
        self._gpus: Tuple[float, List[Slot]] = (-DEVICES_TTL, [])    # (when they were read, the PC's GPUs)
        self._ub_loaded: Optional[str] = None   # the row as it was loaded (ub_row): Apply saves only a change (A53)

    def devices(self) -> List[Slot]:
        """The GPUs the check works on, as the launcher's plan does (FitService.slots through the store): over several
        of them a setup fits when its split does. [] on a Mac, with no GPU and when they cannot be read. Read again
        after DEVICES_TTL."""
        now = time.monotonic()
        if now - self._gpus[0] >= DEVICES_TTL:
            try:
                found = list(self.store.gpu_slots())
            except Exception:       # a driver that does not answer: the limit alone
                found = []
            self._gpus = (now, found)
        return self._gpus[1]

    def rows(self, p: Pending) -> List[SettingRow]:
        """The rows shown for p (the model row offers every model; the slots row 3 and 4 only when
        they fit this Mac with the pending model, window and KV cache)."""
        out = [r._replace(choices=self.model_choices(p)) if r.key == "model" else
               r._replace(choices=self.quant_choices(p)) if r.key == "quant" else r
               for r in rows(p, self.schema, self.models.choices, self.full)]
        if self.fit_cached(p).full is None:                 # no sliding-window layers: no such row
            out = [r for r in out if r.key != "swa"]
        most = self.max_slots(p)
        out = [r._replace(choices=None) if r.key == "cpu_moe" and self.dense(p) else r     # nothing to choose
               for r in out]
        return [r._replace(choices=[c for c in (r.choices or []) if not str(c).isdigit() or int(str(c)) <= max(most, 2)])
                if r.key == "slots" else r for r in out]

    # ------------------------------------------------------------ the model and its quantization (W1.6, B1)
    def group(self, name: str) -> Optional[ModelGroup]:
        """The model an entry is a quantization of (None: not a known entry)."""
        return group_of(self.models.get(), name) if name and name != "auto" else None

    def model_name(self, name: str) -> str:
        """The name of the model an entry is of (the Model row shows it): qwen3.6-35b-a3b for its IQ3's entry."""
        g = self.group(name)
        return g.name if g is not None else name

    def quant_name(self, name: str) -> str:
        """The quantization of an entry (UD-IQ3_XXS): its file name's, else the catalogue's word for it; "" for a name
        that is not an entry or a file that names none."""
        m = self.models.by_name(name)
        if not m:
            return ""
        q = quant_of(m)
        return q if q != FALLBACK_QUANT else str(m.get("quant") or "")

    def model_choices(self, p: Pending) -> List[str]:
        """The Model row's values: auto, then each model once (its entry: the pending one for the pending model, else
        the model's default quantization), in the order of the model list."""
        cur = str(p.get("model", "auto"))
        defaults = self.models.defaults()
        of = {str(e.get("name", "")): g for g in group_models(self.models.get()) for e in g.entries}
        out: List[str] = []
        seen = set()
        for name in self.models.choices():
            g = of.get(name)
            if g is None or not g.several:
                out.append(name)
            elif g.name not in seen:
                seen.add(g.name)
                names = [e.get("name", "") for e in g.entries]
                out.append(cur if cur in names else defaults.get(g.name) or names[0])
        return out

    def quant_choices(self, p: Pending) -> Optional[List[str]]:
        """The Quantization row's values: the entries of the pending model, the largest first (None: Model auto, or a
        model with one quantization: nothing to choose there)."""
        g = self.group(str(p.get("model", "auto")))
        return [e.get("name", "") for e in g.entries] if g is not None and g.several else None

    # ------------------------------------------------------------ the Agents panel (Phase 23.4.4)
    def agents(self, name: str) -> "AgentsInfo":
        """A model's thinking per role: its kind, your choice (config.json, else recommended), the recommended value
        and where it comes from. Not a known model: the defaults of an on / off model."""
        m = self.models.by_name(name)
        kind = thinking.kind_of(m.get("thinking") if m else None)
        rec, src = self.recommended(name)
        mine = self.recommended(name, with_config=True)[0]
        own = jdict(m.get("tune")) if m else {}
        source: Dict[str, str] = {}
        for key in THINKING_ROWS:
            s = src.get(key, "default")
            # the coder's default comes from CARL, not from the catalogue entry (unless the entry has its own)
            carl_default = key == "thinking_coder" and key not in own and s in ("catalogue", "header")
            names = {"catalogue": "catalogue", "header": "the file", "default": "CARL's default", "auto-tune": "Auto-tune"}
            source[key] = "CARL's default" if carl_default else names.get(s, s)
        values = {k: thinking.normalize(kind, THINKING_ROLE[k], mine.get(k, thinking.default(kind, THINKING_ROLE[k])))
                  for k in THINKING_ROWS}
        recs = {k: thinking.normalize(kind, THINKING_ROLE[k], rec.get(k, thinking.default(kind, THINKING_ROLE[k])))
                for k in THINKING_ROWS}
        return AgentsInfo(name, kind, values, recs, source,
                          {k: tuple(thinking.choices(kind, THINKING_ROLE[k])) for k in THINKING_ROWS})

    def thinking_saved(self, name: str) -> Dict[str, Optional[str]]:
        """The model's thinking values in config.json (None: not set there)."""
        prof = jdict(jdict(self.store.load_config().get("models")).get(name))
        return {k: (str(prof[k]) if k in prof else None) for k in THINKING_ROWS}

    def save_thinking(self, name: str, key: str, value: Optional[str]) -> None:
        """Main thinking or Coder thinking of a model: into its settings in config.json at once (the clients' setting:
        no restart); the recommended value (or None) is left out. ValueError for a value the model does not take."""
        if key not in THINKING_ROWS:
            raise ValueError(f"{key}: not a thinking setting")
        info = self.agents(name)
        if value is not None and value not in info.choices[key]:
            raise ValueError(f"{key}: {value!r} is not a choice for this model")
        cfg = self.store.load_config()
        models = cfg.setdefault("models", {})
        prof = models.setdefault(name, {})
        if value is None or value == info.rec[key]:
            prof.pop(key, None)
        else:
            prof[key] = value
        if not prof:
            models.pop(name)
        if not models:
            cfg.pop("models")
        self.store.save_config(cfg)

    def max_slots(self, p: Pending) -> int:
        """The most slots (up to 4) whose windows fit the GPU limit with these settings (2 when unknown);
        cached per model, window and KV type (it reads the GGUF header)."""
        key = (self.resolved_model(p), str(p.get("ctx")), str(p.get("kv")), self.swa_mode(p))
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
            full = self.swa_mode(p) == "full"           # auto and window: the window when it has to
            devices = self.devices()
            calib = calib_of(devices)
            if len(devices) > 1:                        # several GPUs: the split decides, as the launcher's check does
                return max([n for n in (1, 2, 3, 4)
                            if split_fits(devices, load_of(shape, w, ctx, n, kv, full, calib=calib))] or [1])
            limit = one_limit(devices, limit)           # one GPU of the limit: its own room
            return max([n for n in (1, 2, 3, 4) if need_bytes(shape, w, ctx, n, kv, full, calib=calib) <= limit]
                       or [1])
        except Exception:           # an unreadable header, a value that is not a number: no extra slots offered
            return 2

    def swa_mode(self, p: Optional[Pending] = None) -> str:
        """The sliding-window cache: the pending value of the Server panel's row, else cache.swa (Settings >
        Caching): auto, full or window; auto when config.json can't be read."""
        if p and p.get("swa") in SWA_NAMES:
            return str(p["swa"])
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
        """Model rows <- that model's profile (config.json > Auto-tune > catalogue). A Batch size row that still holds
        the last model's own -ub takes this model's (A45)."""
        resolved = self.resolved_model({"model": name})
        vals, _ = self.recommended(resolved, with_config=True)
        for key, pk in MODEL_ROW_KEYS.items():
            if key != "ub":
                p[key] = fmt_val(key, vals[pk])
        row = self.ub_row(resolved)
        if "ub" not in p or str(p.get("ub")) in (UB_AUTO, self._ub_loaded):        # not a person's change: follow
            p["ub"] = row
        self._ub_loaded = row
        if self.has_row("cpu_moe"):                         # the model's own count (B2), else llama.n_cpu_moe
            p["cpu_moe"] = self.cpu_moe_saved(resolved)

    def has_row(self, key: str) -> bool:
        """This OS has the row (the GPU rows: off the Mac)."""
        return any(r.key == key for r in self.schema.llama)

    def tune_stale(self, m: ModelInfo) -> str:
        """How this computer's llama.cpp build and backend differ from those of the model's Auto-tune record (the Auto-tune
        card says the result is an estimate then; "" when they do not, A72). A store that cannot tell gives ""."""
        try:
            return self.store.tune_stale(m)
        except Exception:               # the build cannot be asked (no llama-server): no claim
            return ""

    def model_ub(self, name: str) -> int:
        """The model's own -ub (A45: its Auto-tune record's, else this OS's default; FitService.model_ub through the
        store); UBATCH for a model that is not known."""
        m = self.models.by_name(name)
        if not m:
            return UBATCH
        try:
            return int(self.store.model_ub(m))
        except Exception:           # a store that cannot tell: the smallest, as before A45
            return UBATCH

    def order(self, name: str) -> Optional[uo.UbOrder]:
        """The -ub of a start of this model and where it comes from (FitService.ub_order through the store: the one
        order, A51); None for a model that is not known or a store that cannot tell."""
        m = self.models.by_name(name)
        if not m:
            return None
        try:
            return self.store.ub_order(m)
        except Exception:           # a store that cannot tell: the model's own -ub
            return None

    def ub_row(self, name: str) -> str:
        """The Batch size row of a model as it loads (W1.6): auto when the order picks the -ub (the tune record's or
        this computer's, which a start may lower), else the person's size (typed, the model's saved choice, llama.ub)."""
        o = self.order(name)
        return str(o.ub) if o is not None and o.fixed else UB_AUTO

    def ub_auto(self, name: str, ub: object) -> bool:
        """The pending -ub is auto (W1.6): the start picks it from the model's own and may lower it to keep the setup
        (fit.ub_choice). name: unused (the row holds auto itself)."""
        return str(ub) == UB_AUTO

    def ub_pick(self, name: str) -> bool:
        """The model has no tune record: its start picks the largest -ub that keeps the setup (W1.6)."""
        o = self.order(name)
        return bool(o) and not o.tuned  # type: ignore[union-attr]

    def ub_question(self, p: Pending) -> Optional[uo.UbAsk]:
        """The -ub question Apply asks (A51), None when there is none. A Batch size row changed from the model's tuned
        -ub is a new choice for this model: it is asked about, once, against the tuned one. A row left alone asks what
        the order asks (a llama.ub, or an earlier choice an older tune has outdated, that differs from the tuned -ub).
        Nothing is asked for a model without a tune record: there is nothing to tell the row from."""
        o = self.order(self.resolved_model(p))
        if o is None or not o.tuned:
            return None
        if str(p.get("ub")) == UB_AUTO:             # auto as loaded asks what the order asks; a new auto: nothing
            return o.ask if self._ub_loaded == UB_AUTO else None
        try:
            row = int(str(p.get("ub")))
        except ValueError:
            return None
        if row == o.tuned:
            return o.ask
        if o.source == uo.TYPED:
            return None                             # a -ub typed in the server options wins: nothing to ask
        if o.ask is None and row == o.ub and o.source == uo.KEPT:
            return None                             # the model's earlier choice, made against this tune
        t, mine = uo.reads_of(o.reads, row, o.tuned)
        return uo.UbAsk(uo.CHANGED, row, o.tuned, o.stamp, t, mine)

    def answer_ub(self, name: str, p: Pending, ask: uo.UbAsk, choice: str) -> None:
        """The answer to ub_question: the row follows it (Use tuned: the tuned -ub; Keep: the choice), and config.json
        too (ub_order.answer_config), so the question is not asked again until the model's next tune."""
        self.store.answer_ub(name, ask, choice)
        p["ub"] = self._ub_loaded = str(ask.mine) if choice == uo.KEEP else UB_AUTO

    # ------------------------------------------------------------ the number rows (W1.6, B6)
    def top(self, key: str, p: Pending) -> Optional[float]:
        """The top of a number row that depends on the model or this computer (None: the schema's): Experts in RAM the
        model's layers with experts, the RAM cache this computer's RAM (MiB), GPU memory kept the smallest GPU, Shared
        GPU limit the RAM (GiB)."""
        if key == "cpu_moe":
            return float(self.fit_cached(p).moe_layers) or None
        if key == "cache" and self.ram:
            return float(self.ram // MIB)
        if key == "keep":
            gpus = [s.gpu.total for s in self.devices() if s.gpu.total]
            return round(min(gpus) / GIB, 1) if gpus else None
        if key == "shared" and self.ram:
            return float(self.ram // GIB)
        return None

    def step(self, key: str, p: Pending, step: int) -> Value:
        """The value ← (step -1) or → (1) gives on a number row: its presets at the simple level, its ladder at the full
        level (self.full), from the value shown, to the row's top (server_rows.step_number). From auto, Experts in RAM
        steps from auto's count now, Batch size from auto's -ub."""
        auto_at = (float(self.fit_cached(p).cpu_moe) if key == "cpu_moe" else
                   float(self.fit_cached(p).ub or self.model_ub(self.resolved_model(p))) if key == "ub" else None)
        return step_number(key, p[key], step, self.top(key, p), auto_at, self.full)

    def dense(self, p: Pending) -> bool:
        """The pending model is known to have no experts: the Experts in RAM row has nothing to choose."""
        f = self.fit_cached(p)
        return f.known and f.downloaded and not f.error and not f.moe_layers

    def cpu_moe_saved(self, name: str, cfg: Optional[JSONDict] = None) -> Value:
        """The Experts in RAM row of a model: its own models.NAME.n_cpu_moe (W1.6, B2), else llama.n_cpu_moe, else
        auto."""
        try:
            cfg = self.store.load_config() if cfg is None else cfg
        except Exception:           # a config.json that cannot be read: auto
            return "auto"
        own = jdict(jdict(cfg.get("models")).get(name)).get("n_cpu_moe")
        v = own if own is not None else jdict(cfg.get("llama")).get("n_cpu_moe")
        return "auto" if v in (None, "", "auto") else str(v)

    def running(self, d: ServerData) -> Pending:
        """The settings the running server uses."""
        return running_settings(d, self.vm_addr, self.models.by_name)

    def pending_init(self, d: ServerData, cfg: JSONDict) -> Pending:
        """Start from config.json and the running server (so Apply without changes restarts the same setup). The Batch
        size row is the model's -ub (FitService.ub_order: its saved choice, else its tuned -ub), never the running one (A45:
        the start may have lowered it at its window), so Apply does not save it as the model's choice."""
        run = self.running(d)
        p: Pending = {}
        self._ub_loaded = None
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
        p.pop("ub", None)
        self.load_profile(p, str(p["model"]))
        if run:
            asked = (str(p.get("spec") or ""), p.get("specn"))
            for key in FROM_RUNNING:
                if key in run and run[key] not in (None, "N/A"):
                    p[key] = fmt_val(key, run[key])
            if ram_resolved(asked[0], str(run.get("spec") or ""), run.get("cpu_moe")):
                p["spec"], p["specn"] = asked   # n-gram ran for MTP because experts are in the RAM (A51): not a change
            if "net" not in llama_cfg and not llama_cfg.get("host"):
                p["net"] = run.get("net", p["net"]) if run.get("net") in self.schema.net_choices else p["net"]
        return p

    def defaults_for(self, p: Pending) -> Pending:
        """Every row at its default, the model (and Auto fit's goal and candidates) kept, the model rows at its
        recommended values."""
        q = self.schema.defaults()
        q.update(model=p.get("model", "auto"), goal=p.get("goal", "everyday"),
                 scope=p.get("scope", "catalogue"))
        name = self.resolved_model(q)
        vals = self.recommended(name)[0]                            # the model's tune, without my overrides
        for key, pk in MODEL_ROW_KEYS.items():
            if key != "ub":
                q[key] = fmt_val(key, vals[pk])
        q["ub"] = UB_AUTO                                           # the order picks the -ub (W1.6), not a choice
        self._ub_loaded = self.ub_row(name)                         # so Apply clears a saved choice the row left
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
                    else {"mtp": mtp_kind(m) == "head" or bool(m.get("draft")), "quant": m.get("quant", "")})
            spec = str(p["spec"])
            drafter = bool(m.get("draft")) and not drafter_missing(m)       # a separate drafter, downloaded
            if "draft-mtp" in spec and not info.get("mtp") and not drafter:
                return RED                                      # no MTP head in this file, no drafter for it
            if any(s in FILE_SPECS and not self.drafter_of(m, s) for s in spec.split(",")):
                return RED                                      # a DFlash, DSpark or EAGLE-3 file that is not there
            if ("draft-mtp" in spec and str(p.get("specn")) != "1"
                    and str(info.get("quant", "")).upper().replace("UD-", "").startswith("IQ")):
                return RED                                      # MTP n>1 loses ~15% on IQ quants (measured 2026-10-03)
        if key == "ub":
            return ""                                           # the Batch size row has no colour, as before A51
        if key in MODEL_ROW_KEYS:
            rec = self.recommended(name)[0][MODEL_ROW_KEYS[key]]
            return GRN if str(fmt_val(key, rec)) == str(p[key]) else YEL
        return ""

    def value_note(self, key: str, p: Pending, machine: str) -> str:
        """Why a value is red (value_color), in one sentence ('' when it is not). machine: this computer in a
        sentence (the platform's words: this Mac)."""
        if self.value_color(key, p) != RED:
            return ""
        if key == "ctx":
            m = self.models.by_name(self.resolved_model(p))
            slow = self.store.ctx_zones(m)[1] if m else 0
            return (f"Context: {machine} reads more than {tokens(slow)} tokens again very slowly (the context zones of "
                    f"this model).")
        if key == "spec" and str(p.get("specn")) != "1" and "draft-mtp" in str(p.get("spec")):
            m = self.models.by_name(self.resolved_model(p))
            info = self.store.header_info(m["path"]) if m and m["status"] == "downloaded" else {}
            if info.get("mtp") or (m and m.get("draft") and not drafter_missing(m)):
                return "Speculation: MTP with more than 1 guess is about 15% slower on IQ quantizations (measured)."
        if key == "spec":
            m = self.models.by_name(self.resolved_model(p))
            if m and any(s in FILE_SPECS and not self.drafter_of(m, s) for s in str(p.get("spec")).split(",")):
                return ("Speculation: this model has no downloaded drafter of this kind. The server leaves it out "
                        "(n-gram stays).")
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
            spec, spec_n, ub = pending_spec(p)
            rec, src = self.recommended(name, with_config=True)
            kept = src.get("spec") in ("config", "auto-tune") or spec != str(rec.get("spec"))    # A51
            more = self.keep_change(p)                  # a pending GPU memory kept row (A46): before it is applied
            drafter = spec_draft_bytes(m, spec)               # the drafter file this speculation loads
            shape, weights = self.store.shape_of(m["path"]), self.weights(m) - draft_bytes(m) + drafter
            devices = [replace(s, room=max(s.room + more, 0)) for s in self.devices()]
            limit = max(self.gpu_limit() + more * len(devices), 0) if devices else self.gpu_limit()
            ctx = self.order_ctx(p, rec, src, shape, weights, mtp_spec(spec, spec_n, shape, drafter, kept,
                                                                      self.vision_of(m)), devices, limit)

            def at(u: int) -> FitInfo:
                return llama_fit(name, weights, shape, str(p["kv"]), ctx, str(p["slots"]), limit,
                                 self.swa_mode(p), drafter, spec, spec_n, u, devices, ram=self.ram,
                                 cpu_moe=cpu_moe_of(p), kept=kept, vision=self.vision_of(m))
            auto = self.ub_auto(name, p.get("ub"))
            found = start_fit(at, self.model_ub(name) if auto else ub, auto, self.ub_pick(name))
            # the layers that load (A52: without MTP the head's are not there): the row's top, "40 of 40" (W1.6)
            mtp = any(x == "draft-mtp" for x in found.spec.split(","))
            return replace(found, moe_layers=offload.layers_of(loaded.trimmed(shape, mtp, calib_of(devices))))
        except Exception as e:      # a GGUF that can't be read, a value that is not a number, ...: say so
            return FitInfo(name, error=str(e))

    def order_ctx(self, p: Pending, rec: JSONDict, src: Dict[str, str], shape: Shape, weights: float, spec: Spec,
                  devices: Sequence[Slot], limit: float) -> int:
        """The window a start of the pending setup takes (W1.6): the pending one when it is the user's (config.json,
        Auto-tune, or the row changed here), else the first in Auto fit's order that fits (autofit.model_plan with
        window_cap: on an NVIDIA card from 128K, never above the model's own; the start's apply_order), so the plan the
        MEMORY section shows is the one a start runs (a PC: 38 layers at 96K here, 40 at the start's 128K)."""
        ctx = int(p["ctx"])
        if src.get("ctx") in ("config", "auto-tune") or str(fmt_val("ctx", rec.get("ctx"))) != str(p["ctx"]):
            return ctx
        want = str(p.get("slots", "auto"))
        budget = budget_builder.settings_budget(limit, self.ram, devices, self.swa_mode(p))
        c = Candidate("", "moe" if offload.is_moe(shape) else "dense", 1, False, True, int(weights), shape,
                      kv=str(p.get("kv") or "q4_0"), spec=spec, calib=calib_of(devices))
        _, plan = model_plan(c, budget.allowed, budget.swa_full, window_cap(ctx, budget.tiers),
                             int(want) if want.isdigit() else None, UBATCH, budget)
        return plan.ctx if plan else ctx

    @staticmethod
    def drafter_of(m: ModelInfo, spec_type: str) -> bool:
        """The model has its drafter file of the kind that this --spec-type value names, and it is downloaded."""
        return any(d.get("spec") == spec_type and d.get("status") == "downloaded" for d in m.get("drafts", []) or [])

    def vision_of(self, m: ModelInfo) -> int:
        """The size of the model's vision projector when llama.vision is on and it is downloaded, else 0: a start passes
        it with --mmproj and the memory plan counts it (WCATX)."""
        try:
            on = jdict(self.store.load_config().get("llama")).get("vision") is True
        except Exception:           # a config.json that cannot be read: vision is off
            return 0
        return vision_bytes(m) if on else 0

    def keep_change(self, p: Pending) -> int:
        """Bytes each GPU's room gains from the pending GPU memory kept row (gpu.vram_keep) against config.json's (else
        its default): the row's change counts before Apply (A46). 0 with no such row (a Mac) or a value that is not a
        number."""
        if "keep" not in p:
            return 0
        try:
            saved = jdict(self.store.load_config().get("gpu")).get("vram_keep")
            now = float(saved) if isinstance(saved, (int, float)) and not isinstance(saved, bool) else float(
                str(GPU_KEYS["vram_keep"].default))
            return int((now - float(str(p["keep"]))) * 1024 * MIB)
        except Exception:           # a config.json that cannot be read, a value that is not a number
            return 0

    def fit_cached(self, p: Pending) -> FitInfo:
        """fit_line, recomputed only when a pending value changes (it reads the GGUF header)."""
        key = tuple(sorted((k, str(v)) for k, v in p.items())) + (("swa", self.swa_mode(p)),)
        if self._fit_key != key:
            self._fit_key, self._fit = key, self.fit_line(p)
        return self._fit

    def swa_needs(self, p: Pending) -> Tuple[float, float]:
        """The memory a start needs with the full cache and with the window cache (bytes; 0 when unknown)."""
        out = []
        for mode in ("full", "window"):
            f = self.fit_line(dict(p, swa=mode))
            out.append(f.need if f.known and f.downloaded and not f.error else 0)
        return out[0], out[1]

    def auto_cache_mib(self, p: Pending) -> Optional[int]:
        """The RAM cache that auto gives a start with these settings (MiB), as the launcher sizes it; None
        when RAM or the memory needed is unknown."""
        f = self.fit_cached(p)
        if not self.ram or not f.fits:
            return None
        return budget_builder.ram_cache_mib(self.ram, f.need)

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
            vals, src = self.recommended(m["name"])
            ctx = int(vals.get("ctx") or DEFAULT_CTX)
            order, devices = self.order(m["name"]), self.devices()
            kept = src.get("spec") == "auto-tune"           # Auto-tune's speculation stays with experts in the RAM (A51)

            spec = str(vals.get("spec") or "none")
            drafter = spec_draft_bytes(m, spec)

            def at(u: int) -> FitInfo:
                return llama_fit(m["name"], weights + drafter, shape, str(vals.get("kv") or "q4_0"), ctx,
                                 str(vals.get("slots") or "auto"), self.gpu_limit(), self.swa_mode(), drafter,
                                 spec, int(str(vals.get("spec_n") or 1)), u, devices,
                                 kept=kept, vision=self.vision_of(m))
            fixed = bool(order and order.fixed)
            return start_fit(at, order.ub if order else self.model_ub(m["name"]), not fixed,
                             bool(order and order.source == uo.DEFAULT))      # at the -ub the start uses (A51)
        except Exception:           # an unreadable header, a value that is not a number: unknown
            return None

    def own_load(self) -> str:
        """The user's own load option in llama.extra_args (--load-mode X, --mmap or --no-mmap; the last one), "" when
        there is none or config.json cannot be read: the MODEL card's load mode then gives carl start's reason."""
        try:
            extra = jdict(self.store.load_config().get("llama")).get("extra_args") or []
        except Exception:           # a config.json that cannot be read: none
            return ""
        return own_load([str(a) for a in extra] if isinstance(extra, list) else str(extra).split())

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
                shape, weights = self.store.shape_of(m["path"]), self.weights(m)
            else:
                shape, weights = self.store.model_shape(m), int(m.get("bytes", 0)) + draft_bytes(m)
            if shape is None:
                return None
            devices = self.devices()
            calib = calib_of(devices)
            # at the -ub a person chose (typed, saved for the model, llama.ub); else at -ub 512: the start takes a
            # smaller -ub before a smaller context (A45)
            order = self.order(m["name"])
            ub = order.ub if order and order.fixed else UBATCH
            if len(devices) > 1:                        # several GPUs: the largest window of the split
                return split_max_ctx(shape, weights, devices, 1, "q4_0", full, ub=ub, calib=calib)
            return max_ctx(shape, weights, one_limit(devices, self.gpu_limit()), 1, "q4_0", full, ub=ub, calib=calib)
        except Exception:           # unreadable GGUF header or file: unknown
            return None

    # ------------------------------------------------------------ the other panels' settings (saved at once)
    def clear_model_settings(self, name: str) -> None:
        """A model's own settings in config.json removed (Auto-tune's x): the recommended ones apply again."""
        cfg = self.store.load_config()
        (cfg.get("models") or {}).pop(name, None)
        self.store.save_config(cfg)

    def set_mode(self, mode: str) -> None:
        """llama.mode: single (the default, left out) or router. ValueError for another value."""
        coerce(LLAMA_KEYS["mode"], mode, "llama.mode")
        cfg = self.store.load_config()
        llama = cfg.setdefault("llama", {})
        if mode == LLAMA_KEYS["mode"].default:
            llama.pop("mode", None)
        else:
            llama["mode"] = mode
        self.store.save_config(cfg)

    def set_cache(self, key: str, value: object) -> object:
        """One of the Caching panel's settings (config.json "cache"), checked against the schema; the value saved.
        ValueError (ConfigError) for a value the schema refuses."""
        if key not in CACHE_KEYS:
            raise ValueError(f"cache.{key}: not a setting")
        v = coerce(CACHE_KEYS[key], value, f"cache.{key}")
        cfg = self.store.load_config()
        cfg.setdefault("cache", {})[key] = v
        self.store.save_config(cfg)
        return v

    def step_gate(self, steps: Sequence[int]) -> int:
        """The new-file gate of carl-delegation (delegation.gate) at the next of steps after the saved one (0: off,
        left out of config.json); the new value."""
        cfg = self.store.load_config()
        cur = int(jdict(cfg.get("delegation")).get("gate") or 0)
        nxt = next((g for g in steps if g > cur), 0)
        sec = cfg.setdefault("delegation", {})
        if nxt:
            sec["gate"] = coerce(DELEGATION_KEYS["gate"], nxt, "delegation.gate")
        else:
            sec.pop("gate", None)
            if not sec:
                cfg.pop("delegation")
        self.store.save_config(cfg)
        return nxt

    def config_with(self, p: Pending) -> JSONDict:
        """config.json as it would be saved with these settings."""
        cfg = self.store.load_config()
        name = self.resolved_model(p)
        o = self.order(name)
        return settings_to_config(p, cfg, self.schema, name, self.recommended(name)[0], str(self.model_ub(name)),
                                  o.stamp if o else "", self._ub_loaded)

    def save(self, p: Pending) -> None:
        """Write the settings to config.json."""
        self.store.save_config(self.config_with(p))

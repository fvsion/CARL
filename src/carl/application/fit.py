"""What fits this computer: THE one builder of the Budget (what a model may use) and of the StartPlan (how a start
runs: slots, the sliding-window cache, the speculation, the RAM cache). Auto fit, the model a start loads and the
launcher's settings (launch-env) are here too, because they stand on the budget.

Every budget consumer calls this module: the facade (`carl fit`, models, download default, launch-env, router
presets), carl fit (--plan and --check: cli/commands/fit.py), carl tune, the dashboard's Settings tab
(tools/monitor/settings.py) and its GPU limit (tools/monitor/gguf.py).
An architecture test checks that nothing else builds a Budget or a StartPlan.
"""
from __future__ import annotations

import functools
import threading
from dataclasses import replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar, cast

from ..domain.catalog import inventory as dm
from ..domain.catalog.hf import model_name
from ..domain.catalog.quants import cpu_over, cpu_quant_note, default_quant, group_models, model_key, split_spec
from ..domain.config.settings import LLAMA_KEYS, Config, default_ub
from ..domain.memory import budget as memory_budget
from ..domain.memory.budget import GpuSettings, gpu_settings, preview_limit, preview_slots
from ..domain.memory.calibration import METAL, Calibration
from ..domain.memory.autofit import (AutoFit, Budget, Candidate, Goal, Plan, Scope, as_goal, as_scope, auto_fit,
                                     best_downloaded, candidate, model_plan, order_note, setup_text, window_cap)
from ..domain.memory.fit import (NO_SPEC, UBATCH, LoadMode, Spec, StartPlan, UbChoice, estimated_limit, mtp_spec,
                                 need_bytes, offline_default, prompt_cache_mib, reserve_bytes,
                                 start_plan as _start_plan, ub_choice)
from ..domain.memory.gguf import GIB, ModelShape
from ..domain.memory.shape import has_head
from ..domain.memory import cpu_kv, offload
from ..domain.memory.offload import Offload
from ..domain.memory.split import Slot, calib_of, fits as split_fits, load_of, ok_for, one_limit, only, trim
from ..domain.server import ub_order as uo
from ..domain.server.launch_plan import UB_FLAGS, extra_value, launch_env as build_launch_env
from ..domain.shared.contracts import ensure
from ..domain.shared.errors import ConfigError
from ..domain.shared.platform_words import PlatformWords
from ..domain.shared.types import ModelInfo, SettingSource, SettingValue, Settings
from ..domain.hardware.cpu_topology import ThreadPin, checked, pin_for
from ..domain.memory import server_fit as sf
from ..domain.memory.calibration import bound as b_calibration
from ..domain.server.argv import fit_argv
from ..domain.tune import provenance
from ..ports import CpuLayout, GpuLimit, HostMemory, ServerFit
from .config import ConfigService
from .inventory import Inventory

_F = TypeVar("_F", bound=Callable[..., Any])


class _Look(threading.local):
    """The plans running in one thread: how deep, and what they read once (a class, so a new thread starts at 0)."""
    depth = 0
    memo: Optional[Dict[str, bool]] = None


def one_plan(method: _F) -> _F:
    """A method of FitService that makes a plan (a budget, a start's plan, a launch-env): inside it, what the computer
    says about the CPU alone (FitService.cpu_only: the settings, the installed build, the GPU listing) is read once, not
    on every use of `unified` and `dedicated` (about 8 times a plan). It is kept for this call and this thread only: the
    next plan reads it again, so a changed setting or build is seen at once."""
    @functools.wraps(method)
    def planned(self: "FitService", *args: Any, **kwargs: Any) -> Any:
        look = self._look
        depth = look.depth
        if depth == 0:
            look.memo = {}
        look.depth = depth + 1
        try:
            return method(self, *args, **kwargs)
        finally:
            look.depth = depth
            if depth == 0:
                look.memo = None
    return cast(_F, planned)


FIT_ROUNDS = 3              # Auto fit chooses again when its pick's fit needs more than the room (a start's ROUNDS, A67)


def estimated(ram_gb: float) -> Tuple[int, float]:
    """The GPU limit of a Mac with ram_gb of RAM (estimated: VM not counted), and its fraction of the RAM."""
    return estimated_limit(ram_gb * GIB)


def start_plan(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, allowed: float,
               spec: Spec = NO_SPEC, ub: int = UBATCH, devices: Sequence[Slot] = ()) -> StartPlan:
    """How a start runs at its window within `allowed` bytes (domain/memory/fit.py start_plan): the slots (auto = 2
    when two windows fit), the sliding-window cache (cache.swa mode), the speculation (MTP goes before a slot). The
    launcher's plan (FitService.plan_start) and the dashboard's memory check both take it from here. devices: the
    GPUs of a PC; over several, a setup fits when its split does (domain/memory/split.py), not when its need is
    within `allowed`; over one, when its need is within that device's room (`allowed` counts every GPU)."""
    calib = calib_of(devices)              # CUDA: the backend's constants; a Mac or no GPU: the Mac's formula
    ok = ok_for(shape, weights, ctx, kv, ub, devices, calib) if len(devices) > 1 else None
    return _start_plan(mode, shape, weights, ctx, want_slots, kv, one_limit(devices, allowed), spec, ub, ok, calib)


def whole_fits(sp: StartPlan, shape: ModelShape, weights: float, ctx: int, kv: str, allowed: float, ub: int,
               devices: Sequence[Slot]) -> bool:
    """Does the StartPlan fit the GPUs whole (no experts in the RAM): over several devices as a split, else within
    one device's room (or `allowed` with no device)?"""
    calib, full = calib_of(devices), sp.swa_full is not False
    if len(devices) > 1:
        return split_fits(devices, load_of(shape, weights, ctx, sp.slots, kv, full, sp.spec, ub, calib))
    return need_bytes(shape, weights, ctx, sp.slots, kv, full, sp.spec, ub, calib) <= one_limit(devices, allowed)


def start_offload(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, allowed: float,
                  spec: Spec = NO_SPEC, ub: int = UBATCH, devices: Sequence[Slot] = (), ram: float = 0.0,
                  reserve: float = 0.0, cpu_moe: Optional[int] = None) -> Tuple[StartPlan, Optional[Offload]]:
    """start_plan, then the MoE experts in the RAM when the start does not fit the GPUs whole (W6.7,
    domain/memory/offload.py): (the StartPlan, its Offload; None for a start that keeps every expert on the GPUs).
    Auto fit's order holds: the whole-GPU setups first, then the same setups with experts in the RAM (slots auto = 2
    when two windows fit that way; MTP goes before a slot). cpu_moe: the setting llama.n_cpu_moe (None: auto; 0: never
    in the RAM; N: these N layers, checked the same way). A dense model, a Mac and a GPU that shares the RAM never keep
    experts in the RAM. ram, reserve: the RAM and the part kept for the system and apps."""
    whole = start_plan(mode, shape, weights, ctx, want_slots, kv, allowed, spec, ub, devices)
    if cpu_moe == 0 or not offload.offloads(devices, shape):
        return whole, None
    if cpu_moe is None and whole_fits(whole, shape, weights, ctx, kv, allowed, ub, devices):
        return whole, None
    calib = calib_of(devices)
    lean = offload.ram_spec(spec)           # any expert layer in the RAM: MTP off unless a person or Auto-tune chose it
    ok = offload.ok_for(shape, weights, ctx, kv, ub, devices, ram, reserve, calib, cpu_moe)
    sp = _start_plan(mode, shape, weights, ctx, want_slots, kv, allowed, lean, ub, ok, calib)
    off = offload.plan_of(shape, weights, ctx, sp.slots, kv, sp.swa_full is not False, sp.spec, ub, devices, ram,
                          reserve, calib, cpu_moe)
    return (replace(sp, ram_off=True) if spec.mtp and not lean.mtp else sp), off


def check_offload(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, allowed: float,
                  spec: Spec, ub: int, devices: Sequence[Slot], ram: float,
                  cpu_moe: Optional[int]) -> Tuple[StartPlan, Optional[Offload]]:
    """start_offload with the usual reserve (VM not counted): the dashboard's memory check (W6.8), as the start
    plans it."""
    return start_offload(mode, shape, weights, ctx, want_slots, kv, allowed, spec, ub, devices, ram,
                         reserve_bytes(None, False), cpu_moe)


def ram_cache_mib(ram: int, need: float) -> int:
    """The RAM prompt cache a setup leaves room for, with the usual reserve (VM not counted): the dashboard's
    auto size of llama.cache_ram."""
    return prompt_cache_mib(ram, need, reserve_bytes(None, False))


class FitService:
    """The budget of this computer, Auto fit over the model list, and the plan of a start. Contracts C-103 (the
    Budget), C-104 (the StartPlan), C-105 (Auto fit and the start's model), C-106 (launch-env)."""

    def __init__(self, config: ConfigService, inventory: Inventory, gpu: GpuLimit, host: HostMemory,
                 os_name: str = "macos", gpu_env: Optional[Mapping[str, str]] = None,
                 cpu: Optional[CpuLayout] = None, server_fit: Optional[Callable[[], ServerFit]] = None,
                 build_backend: Optional[Callable[[], str]] = None,
                 build_version: Optional[Callable[[], str]] = None) -> None:
        self.config = config
        self.inventory = inventory
        self.gpu = gpu
        self.host = host
        self.os_name = os_name              # the profile's OS: which settings launch-env gives (their platforms)
        self.gpu_env = dict(gpu_env or {})  # the GPU settings' variables, as the composition read them
        self.cpu = cpu                      # the CPU's cores (a PC), for the pinned threads of experts in the RAM (A54)
        self.server_fit = server_fit        # llama-server's own fit, built when Auto fit's pick is measured (A67); None:
                                            # the pick is the estimate's, unmeasured
        self.build_backend = build_backend  # the backend the installed llama.cpp build says it is (its engine record:
                                            # cpu, cuda-13, ...); "" or None: not known
        self.build_version = build_version  # the installed llama-server's --version text, for a tune record's check (A72);
                                            # "" or None: not known
        self._look = _Look()      # the answers a plan in this thread reads once (one_plan)
        self._port: Optional[ServerFit] = None      # the server fit, built at the first ask (_fit_port)
        self._head: Tuple[str, bytes] = ("", b"")   # the last header read for a stub (_head_of)

    @property
    def words(self) -> PlatformWords:
        return self.config.words

    # ------------------------------------------------------------ the budget
    @property
    def unified(self) -> bool:
        """The GPU and the CPU share the RAM (a Mac, a GPU that shares it, a start on the CPU alone); False on a
        discrete GPU."""
        return self.gpu.unified or self.cpu_only()

    @property
    def dedicated(self) -> int:
        """Of the GPU limit, the bytes that are the GPUs' own memory (the RAM does not cap them); 0 on a Mac and on the
        CPU alone."""
        return 0 if self.cpu_only() else int(self.gpu.dedicated)

    def cpu_only(self) -> bool:
        """A start here runs on the CPU alone (A71): not a Mac, and either the setting gpu.backend is cpu, or the
        installed llama.cpp build is a CPU build (its engine record; a CPU build on a GPU host, WWSL), or the inventory
        finds no GPU a model uses. A GPU that shares the RAM is a GPU. The model is then in the RAM, which caps it. A GPU
        whose device listing failed or timed out is still a GPU: no name for it is not the build's word. Read once
        inside a plan (one_plan), else on every call."""
        memo = self._look.memo
        if memo is None:
            return self._read_cpu_only()
        if "cpu_only" not in memo:
            memo["cpu_only"] = self._read_cpu_only()
        return memo["cpu_only"]

    def _read_cpu_only(self) -> bool:
        if self.os_name == "macos":
            return False
        if self.gpu_settings().backend == "cpu":
            return True
        if self.build_backend is not None and self.build_backend() == "cpu":
            return True
        if self.gpu.shared_gpus:
            return False
        return self.gpu.unified

    def start_backend(self) -> str:
        """The backend a start here runs on, in the words of the engine record and of TuneRecord.backend: metal on a Mac,
        cpu on the CPU alone, else the installed build's (cuda-13, vulkan, ...; "" when it is not known)."""
        if self.os_name == "macos":
            return "metal"
        if self.cpu_only():
            return provenance.CPU
        return (self.build_backend() or "") if self.build_backend is not None else ""

    def effective_tune(self, m: ModelInfo, cfg: Config) -> Tuple[Settings, Dict[str, SettingSource]]:
        """The model's settings as a start here takes them (inventory.effective_tune), with one rule on the Auto-tune
        record's context memory type (A72, provenance.kv_applies): a record that measured the types holds for a start on
        the CPU alone only (a start on a GPU takes the type below it), and a record that did not measure them (q4_0 as a
        constant) is not a choice on the CPU alone (the start picks: A71)."""
        vals, src = self.inventory.effective_tune(m, cfg)
        if src.get("kv") == "auto-tune":
            tune = (m.get("local") or {}).get("tune") or {}
            if not provenance.kv_applies(tune, self.cpu_only()):
                below, below_src = self.inventory.effective_tune(dm.without_kv(m), cfg)
                vals["kv"], src["kv"] = below["kv"], below_src["kv"]
        return vals, src

    def tune_stale(self, m: ModelInfo) -> str:
        """The start line for a model whose Auto-tune record was measured on another llama.cpp build or backend than this
        start's (provenance.stale; "" when it is the same, or the model has no record). A72."""
        if not (((m.get("local") or {}).get("tune") or {}).get("settings")):
            return ""
        return provenance.stale(m, self._build_text(), self.start_backend(), self.words)

    def tune_stale_parts(self, m: ModelInfo) -> str:
        """How the build and backend differ from the model's record, for the Auto-tune card (provenance.differences;
        "" when they do not)."""
        if not (((m.get("local") or {}).get("tune") or {}).get("settings")):
            return ""
        return provenance.differences(m, self._build_text(), self.start_backend())

    def _build_text(self) -> str:
        return (self.build_version() if self.build_version is not None else "") or ""

    @property
    def shared_gpus(self) -> Tuple[str, ...]:
        """The names of the GPUs that share the RAM (an APU); () when none."""
        return tuple(self.gpu.shared_gpus)

    def port_limit(self) -> Tuple[int, str]:
        """(bytes the GPU may use, how it was found) as the adapter says it: a GPU that shares the RAM gives its own
        memory and the whole shared part, not yet capped by the RAM less the reserve (the Budget does that)."""
        got = self.gpu.limit()
        ensure(isinstance(got[0], (int, float)) and got[0] >= 0 and isinstance(got[1], str), "C-003",
               "the GPU limit is a byte count of 0 or more and its source in words")
        ram = self.host.ram_bytes()
        if ram > 0 and not self.gpu.unified and self.cpu_only():       # a GPU is there, but no model runs on it (A71)
            return ram, "the RAM: no GPU is used here, so the model runs on the CPU"
        return got

    @one_plan
    def gpu_limit(self) -> Tuple[int, str]:
        """(bytes the GPU may use, how it was found); measured once per process by the adapter. With a GPU that
        shares the RAM it is capped by the RAM less the usual reserve (memory_budget.cap), as the Budget caps it."""
        got = self.port_limit()
        ram = self.host.ram_bytes()
        if not (self.unified and self.dedicated and ram > 0):
            return got
        reserve = reserve_bytes(None, self.host.vm_network_up())
        capped = int(memory_budget.cap(got[0], self.dedicated, ram, reserve))
        if capped >= got[0]:
            return got
        return capped, (f"{got[1]}, capped by the RAM less {reserve / GIB:.1f} GiB kept free for "
                        f"{self.words.os_and_apps}")

    def gpu_settings(self) -> GpuSettings:
        """The GPU settings in force (gpu.vram_keep, gpu.backend, gpu.devices): the variables, then config.json."""
        try:
            raw = self.config.stores.config.load()
        except ConfigError:                 # a broken config.json: its own error shows where it is read
            raw = None
        return gpu_settings(self.gpu_env, raw.get("gpu") if isinstance(raw, dict) else None)

    def calibration(self) -> Calibration:
        """The constants of the backend this computer's GPUs run (domain/memory/calibration.py): CUDA's; the
        Mac's formula on a Mac and with no GPU."""
        try:
            return calib_of(self.slots())
        except ConfigError:
            return METAL

    def preview_limit(self, vram: Sequence[float]) -> Tuple[int, str]:
        """(limit, how) of a preview of discrete GPUs with these sizes in GiB (carl fit --vram), each less the keep."""
        return preview_limit(vram, self.gpu_settings().keep)

    def slots(self, vram: Optional[Sequence[float]] = None, names: Sequence[str] = ()) -> List[Slot]:
        """The devices a start plans its split over (domain/memory/split.py), in the order of -dev: each GPU a model
        uses with llama.cpp's name for it and its room (a GPU that shares the RAM within the RAM less the reserve:
        gpu_limit). [] on a Mac, with no GPU and when no GPU can be named. vram: a preview of discrete GPUs
        (carl fit --vram); names: only these devices (the start's LLAMA_DEVICES). Raises ConfigError for a name that
        is no device's."""
        found = preview_slots(vram, self.gpu_settings().keep) if vram else self.gpu.slots()
        if not vram and any(s.shared for s in found):         # a GPU that shares the RAM: the RAM less the reserve caps it
            found = trim(found, self.gpu_limit()[0])
        try:
            return only(found, names)
        except KeyError as e:
            have = ", ".join(s.name for s in found) or "none"
            raise ConfigError(f"no GPU is named {e.args[0]} (llama.cpp's names for this computer: {have})") from None

    @one_plan
    def budget(self, ram_gb: Optional[float] = None, reserve_gb: Optional[float] = None,
               swa: Optional[str] = None, vram: Optional[Sequence[float]] = None) -> Budget:
        """What a model may use: on this Mac (GPU limit, RAM, the reserve for macOS + apps,
        more with the VM up), or estimated for a Mac with ram_gb of RAM (VM not counted).
        swa: cache.swa when the caller has the config already (else config.json is read).
        vram: a preview of discrete GPUs (carl fit --vram), with this computer's RAM or ram_gb. On discrete GPUs the
        budget names them (Budget.devices: Auto fit's passes with MoE experts in the RAM, W6.7)."""
        swa = self.config.swa_mode() if swa is None else swa
        if vram:
            ram = ram_gb * GIB if ram_gb else self.host.ram_bytes()
            return Budget(self.preview_limit(vram)[0], ram, reserve_bytes(reserve_gb, False), swa=swa,
                          unified=False, words=self.words,
                          devices=tuple(preview_slots(vram, self.gpu_settings().keep)))
        if ram_gb:
            return Budget(estimated(ram_gb)[0], ram_gb * GIB, reserve_bytes(reserve_gb, False), swa=swa,
                          words=self.words)
        vm = self.host.vm_network_up()
        return Budget(self.port_limit()[0], self.host.ram_bytes(), reserve_bytes(reserve_gb, vm), vm, swa,
                      self.unified, self.words, self.dedicated, self.discrete_slots())

    @one_plan
    def discrete_slots(self) -> Tuple[Slot, ...]:
        """The discrete GPUs a start splits over (slots), for experts in the RAM; () on unified memory (a Mac, a GPU
        that shares the RAM) and when the GPUs cannot be named."""
        if self.unified:
            return ()
        try:
            return tuple(self.slots())
        except ConfigError:
            return ()

    def shared_note(self, reserve_gb: Optional[float] = None) -> str:
        """The start's line for a GPU that shares the RAM (an APU): its name and what a model can use on it."""
        names = " and ".join(self.shared_gpus)
        return f"GPU: {names} shares the RAM with {self.words.machine}. {self.budget(reserve_gb=reserve_gb).explain()}"

    def plan_start(self, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, swa: str,
                   spec: Spec = NO_SPEC, ub: int = UBATCH, reserve_gb: Optional[float] = None,
                   limit: Optional[float] = None, dedicated: Optional[float] = None,
                   devices: Sequence[Slot] = ()) -> Tuple[StartPlan, int]:
        """The launcher's plan of a start (carl fit --plan): the StartPlan within what a model may use
        (the GPU limit, capped by the RAM less the reserve on unified memory), and the RAM prompt cache in MiB
        (what the RAM allows after the model and the reserve). limit: another GPU limit (fit --ram's estimate);
        the RAM is this computer's either way. dedicated: the part of limit that is the GPUs' own memory (default:
        this computer's when limit is this computer's, else 0). devices: the GPUs the start plans its split over
        (FitService.slots); over several, a setup fits when its split does. Every expert stays on the GPUs (the plan
        with experts in the RAM: plan_offload)."""
        sp, cache, _ = self.plan_offload(shape, weights, ctx, want_slots, kv, swa, spec, ub, reserve_gb, limit,
                                         dedicated, devices, 0)
        return sp, cache

    @one_plan
    def plan_offload(self, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, swa: str,
                     spec: Spec = NO_SPEC, ub: int = UBATCH, reserve_gb: Optional[float] = None,
                     limit: Optional[float] = None, dedicated: Optional[float] = None,
                     devices: Sequence[Slot] = (), cpu_moe: Optional[int] = None
                     ) -> Tuple[StartPlan, int, Optional[Offload]]:
        """plan_start with the MoE experts in the RAM when the start does not fit the GPUs whole (start_offload; W6.7):
        (the StartPlan, the RAM prompt cache in MiB, the Offload or None). cpu_moe: the setting llama.n_cpu_moe (None:
        auto). With experts in the RAM the RAM cache is what the RAM leaves after them (the RAM side)."""
        b = self._plan_budget(reserve_gb, limit, dedicated)
        ram = int(b.ram)
        sp, off = start_offload(swa, shape, weights, ctx, want_slots, kv, b.allowed, spec, ub, devices, ram, b.reserve,
                                cpu_moe)
        if off is not None:
            return sp, prompt_cache_mib(ram, off.ram, b.reserve), off
        need = need_bytes(shape, weights, ctx, sp.slots, kv, sp.swa_full is not False, sp.spec, ub, calib_of(devices))
        # a discrete GPU (Windows, Linux): the model is in the VRAM, so the RAM neither caps it nor holds it; a GPU that
        # shares the RAM holds the model in its own memory first and the RAM for the rest
        return sp, prompt_cache_mib(ram, max(need - b.dedicated, 0) if b.unified else 0, b.reserve), None

    def _plan_budget(self, reserve_gb: Optional[float] = None, limit: Optional[float] = None,
                     dedicated: Optional[float] = None) -> Budget:
        """The Budget of the launcher's plan (plan_offload): this computer's GPU limit or `limit`, its RAM and the
        reserve."""
        vm = self.host.vm_network_up()
        return Budget(self.port_limit()[0] if limit is None else limit, self.host.ram_bytes(),
                      reserve_bytes(reserve_gb, vm), vm, unified=self.unified, words=self.words,
                      dedicated=(self.dedicated if limit is None else 0) if dedicated is None else dedicated)

    @one_plan
    def plan_fits(self, sp: StartPlan, off: Optional[Offload], shape: ModelShape, weights: float, ctx: int, kv: str,
                  ub: int, reserve_gb: Optional[float] = None, limit: Optional[float] = None,
                  dedicated: Optional[float] = None, devices: Sequence[Slot] = ()) -> bool:
        """Does a plan of plan_offload fit: its experts in the RAM on both sides, else the whole start (over several
        devices as a split, else within one device's room or what a model may use)?"""
        if off is not None:
            return off.fits
        return whole_fits(sp, shape, weights, ctx, kv, self._plan_budget(reserve_gb, limit, dedicated).allowed, ub,
                          devices)

    @one_plan
    def plan_ub(self, want: int, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, swa: str,
                spec: Spec = NO_SPEC, reserve_gb: Optional[float] = None, limit: Optional[float] = None,
                dedicated: Optional[float] = None, devices: Sequence[Slot] = (),
                cpu_moe: Optional[int] = None, pick: bool = False) -> UbChoice:
        """The -ub of a start whose -ub is the model's (A45, fit.ub_choice): `want` when the plan at it keeps the
        setup that -ub 512 gets (the slots, the cache, the speculation, the experts in the RAM or not, and that it
        fits), else 1024 or 512. pick (a model without a tune record, A52; ub_order.ub_pick): of the sizes up to `want`
        the one that keeps the most expert layers on the GPUs at this window, a larger one only when it keeps as many."""
        plans: Dict[int, Tuple[StartPlan, Optional[Offload], bool]] = {}

        def plan_at(ub: int) -> Tuple[StartPlan, Optional[Offload], bool]:
            if ub not in plans:
                sp, _, off = self.plan_offload(shape, weights, ctx, want_slots, kv, swa, spec, ub, reserve_gb, limit,
                                               dedicated, devices, cpu_moe)
                plans[ub] = (sp, off, self.plan_fits(sp, off, shape, weights, ctx, kv, ub, reserve_gb, limit,
                                                     dedicated, devices))
            return plans[ub]

        def key(ub: int) -> object:
            sp, off, ok = plan_at(ub)
            return sp.slots, sp.swa_full, sp.spec.kind, off is not None, ok

        def setup(ub: int) -> Tuple[object, str]:
            sp, off, ok = plan_at(ub)
            cache = "" if sp.swa_full is None else " with the full cache" if sp.swa_full else " with the window cache"
            words = setup_text(sp.slots, ctx) + cache + (" and MTP" if sp.spec.mtp else "") + \
                (f" with {off.words()}" if off is not None else "") + ("" if ok else ", which does not fit")
            return key(ub), words
        if pick and want > UBATCH:
            best = uo.ub_pick(want, lambda ub: (key(ub), plan_at(ub)[1].n if plan_at(ub)[1] else 0))
            if best != want:
                a, b = plan_at(want)[1], plan_at(best)[1]
                more = (a.n - b.n) if a and b else 0
                if more <= 0 or key(want) != key(best):     # the larger size loses the setup: the context comes first
                    return UbChoice(best, want, f"Batch size -ub {best}, not {want}: with -ub {want} the setup would be "
                                                f"{setup(want)[1]}, not {setup(best)[1]}.")
                return UbChoice(best, want, f"Batch size -ub {best}, not {want}: it keeps the experts of {more} more "
                                            f"layer{'s' if more != 1 else ''} on the GPU, which writes faster.")
            want = best
        return ub_choice(want, setup)

    def tune_room(self, shape: ModelShape, weights: float,
                  kv: str = "q4_0") -> Optional[Callable[[int, int, int, Spec], bool]]:
        """Auto-tune's test of a setup on a PC with discrete GPUs (TunePlan.room): does a start of ctx tokens x slots
        at -ub and that speculation fit, as the start's own plan finds it (the split, the experts in the RAM)? None on
        a Mac and with no GPU that CARL can name: the tune then sizes against the GPU limit, as it always did."""
        devices = self.discrete_slots()
        if not devices:
            return None

        def room(ctx: int, slots: int, ub: int, spec: Spec) -> bool:
            spec = replace(spec, kept=True)         # the tune measures the speculation it names (A51)
            sp, _, off = self.plan_offload(shape, weights, ctx, str(slots), kv, "auto", spec, ub, devices=devices)
            return sp.slots == slots and self.plan_fits(sp, off, shape, weights, ctx, kv, ub, devices=devices)
        return room

    def thread_pin(self) -> Optional[ThreadPin]:
        """The threads a start with MoE experts in the RAM pins (A54): one per physical core, from the CPU's topology
        (cpu_topology.pin_for). None on a Mac, on a CPU whose topology is not known and with fewer than two cores."""
        return pin_for(checked(self.cpu.topology())) if self.cpu is not None else None

    def tune_ram(self, shape: ModelShape, weights: float,
                 kv: str = "q4_0") -> Optional[Callable[[int, int, int, Spec], int]]:
        """Auto-tune's count of the expert layers a start keeps in the RAM (TunePlan.ram_layers): for ctx tokens x slots at
        -ub and that speculation, as the start's own plan finds it (0: every expert on the GPUs, or the slots do not
        fit). None on a Mac and with no GPU that CARL can name: no experts in the RAM there."""
        devices = self.discrete_slots()
        if not devices:
            return None

        def layers(ctx: int, slots: int, ub: int, spec: Spec) -> int:
            spec = replace(spec, kept=True)
            sp, _, off = self.plan_offload(shape, weights, ctx, str(slots), kv, "auto", spec, ub, devices=devices)
            return off.n if off is not None and sp.slots == slots else 0
        return layers

    def load_reserve(self) -> float:
        """The load reserve in GiB: llama.load_reserve_gib in config.json, else its default (8)."""
        try:
            v = self.config.load_config().llama.get("load_reserve_gib")
        except ConfigError:
            v = None
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(
            LLAMA_KEYS["load_reserve_gib"].default)                                   # type: ignore[arg-type]

    @one_plan
    def load_need(self, sp: StartPlan, off: Optional[Offload], shape: ModelShape, weights: float, ctx: int, kv: str,
                  ub: int, devices: Sequence[Slot] = ()) -> float:
        """What the server holds in the RAM under load mode none: the RAM side with experts there; on discrete GPUs
        the weights that stay in the RAM (token_embd, Gemma's per-layer embeddings); on a Mac, the CPU and a GPU that
        shares the RAM the whole start (the GPU's memory is the RAM)."""
        if off is not None:
            return off.ram
        if not devices or self.unified:
            return self.ram_need(need_bytes(shape, weights, ctx, sp.slots, kv, sp.swa_full is not False, sp.spec, ub,
                                            calib_of(devices)), shape, devices)
        return self.ram_need(0.0, shape, devices)

    @one_plan
    def ram_need(self, need: float, shape: ModelShape, devices: Sequence[Slot] = ()) -> float:
        """load_need of a start that keeps every expert on the GPUs and needs `need` there: on a Mac, the CPU and a GPU
        that shares the RAM that need (the GPU's memory is the RAM); on discrete GPUs the weights that stay in the RAM
        (token_embd, Gemma's per-layer embeddings)."""
        if not devices or self.unified:
            return need
        embd = shape.get("embd_bytes", 0) or 0
        return float(embd) + calib_of(devices).ram_bytes(shape)

    def load_mode(self, need: float, reserve_gib: Optional[float] = None) -> LoadMode:
        """Load mode none or the mapped load for a start whose server holds `need` bytes in the RAM under none (A44,
        A45): the free RAM now against that and the load reserve (reserve_gib, else load_reserve)."""
        reserve = self.load_reserve() if reserve_gib is None else reserve_gib
        return LoadMode(need, float(self.host.free_bytes()), reserve * GIB)

    def cpu_free(self, reserve_gb: Optional[float] = None) -> Tuple[float, str]:
        """(the RAM a start on the CPU alone may fill, in words): the free RAM now (the load mode's test), else when the
        OS does not say the RAM less the reserve for the system and apps."""
        free = float(self.host.free_bytes())
        if free > 0:
            return free, "free RAM"
        ram, reserve = self.ram_room(reserve_gb)
        return max(ram - reserve, 0.0), "RAM less the reserve"

    @one_plan
    def cpu_kv(self, sp: StartPlan, shape: ModelShape, weights: float, ctx: int, ub: int,
               load_reserve_gib: Optional[float] = None, reserve_gb: Optional[float] = None) -> cpu_kv.KvChoice:
        """The context memory type of a start on the CPU alone (A71; domain/memory/cpu_kv.py): f16 when the free RAM covers
        the start's need with f16 and the load reserve, else q8_0, else q4_0. The start is the StartPlan's (its slots, its
        speculation and its cache): only the type changes. load_reserve_gib: None is llama.load_reserve_gib."""
        free, basis = self.cpu_free(reserve_gb)
        reserve = (self.load_reserve() if load_reserve_gib is None else load_reserve_gib) * GIB
        return cpu_kv.choose(lambda kv: self.load_need(sp, None, shape, weights, ctx, kv, ub, ()), free, reserve, basis,
                             self._plan_budget(reserve_gb).allowed)

    def cpu_kv_for(self, shape: ModelShape, weights: float, ctx: int, slots: int, kv: str, swa: str, spec: Spec,
                   ub: int) -> cpu_kv.KvChoice:
        """cpu_kv of a setup that is not planned here yet (router mode's preset of a model, A72): the start's plan for
        this window and these slots with the type it has, and the type the CPU's rule takes for it."""
        sp, _, _ = self.plan_offload(shape, weights, ctx, str(slots), kv, swa, spec, ub)
        return self.cpu_kv(sp, shape, weights, ctx, ub)

    @one_plan
    def cpu_kv_open(self, m: ModelInfo, cfg: Config) -> bool:
        """A start of this model leaves its context memory type to the CPU's rule (A71): it runs on the CPU alone, and
        nobody chose the type (not config.json, not an Auto-tune record that measured the types). The environment and the
        start's own flag are the start's to see."""
        if not self.cpu_only():
            return False
        return self.effective_tune(m, cfg)[1].get("kv") in ("default", "catalogue", "header")

    @one_plan
    def tune_kv(self, shape: ModelShape, weights: float) -> Optional[Callable[[str, int, int, int, Spec], bool]]:
        """Auto-tune's test of a context memory type on the CPU alone (TunePlan.kv_room): does a start of ctx tokens x slots
        at -ub and that speculation fit the RAM with this type (the free RAM and the load reserve: cpu_kv.covers)? None
        where a start does not run on the CPU alone: the tune keeps q4_0 there."""
        if not self.cpu_only():
            return None
        free, _ = self.cpu_free()
        reserve = self.load_reserve() * GIB

        def room(kv: str, ctx: int, slots: int, ub: int, spec: Spec) -> bool:
            return cpu_kv.covers(need_bytes(shape, weights, ctx, slots, kv, True, spec, ub), free, reserve)
        return room

    def ram_room(self, reserve_gb: Optional[float] = None) -> Tuple[float, float]:
        """(the RAM, the part kept free for the system and apps) of this computer: what experts in the RAM may use."""
        return float(self.host.ram_bytes()), reserve_bytes(reserve_gb, self.host.vm_network_up())

    # ------------------------------------------------------------ auto fit
    def candidates(self, models: List[ModelInfo]) -> List[Candidate]:
        """Every model as auto fit sees it; headers are read only for the eligible ones
        (ranked stock models)."""
        out = []
        calib = self.calibration()
        for m in models:
            c = candidate(m, None)
            out.append(replace(candidate(m, self.inventory.shape_of(m)), calib=calib) if c.eligible else c)
        return self.cpu_ladder(models, out) if self.cpu_only() else out

    @staticmethod
    def cpu_ladder(models: List[ModelInfo], cands: List[Candidate]) -> List[Candidate]:
        """The candidates of a start on the CPU alone (A72): an IQ4_NL or Q4_0 candidate takes the quality rank of the
        best K-quant of its model and bit class, when that ranks better, and goes before it on a tie
        (quants.cpu_ladder); its Candidate.cpu_over says why. The rank in the catalogue is not changed."""
        by_name = {c.name: c for c in cands}
        groups: Dict[str, List[ModelInfo]] = {}
        for m in models:
            groups.setdefault(model_key(m), []).append(m)
        out = dict(by_name)
        for entries in groups.values():
            for m in entries:
                c = by_name.get(m.get("name", ""))
                over = [by_name[e.get("name", "")] for e in cpu_over(m, entries) if e.get("name", "") in by_name]
                if c is None or c.rank is None or not over:
                    continue
                best = min([c.rank] + [x.rank for x in over if x.rank is not None])
                out[c.name] = replace(c, rank=best, cpu_over=cpu_quant_note(m, entries))
        return [out[c.name] for c in cands]

    @staticmethod
    def auto_settings(cfg: Config) -> Tuple[Goal, Scope]:
        """The goal and the candidates auto fit uses (config.json llama.auto_goal / auto_fit)."""
        return as_goal(cfg.llama.get("auto_goal")), as_scope(cfg.llama.get("auto_fit"))

    def auto_fit(self, models: List[ModelInfo], goal: Goal = "everyday", scope: Scope = "catalogue",
                 budget: Optional[Budget] = None) -> AutoFit:
        """The best ranked stock model for the goal that fits (domain/autofit.py)."""
        return auto_fit(self.candidates(models), budget or self.budget(), goal, scope)

    def measured_fit(self, models: List[ModelInfo], goal: Goal = "everyday", scope: Scope = "catalogue",
                     budget: Optional[Budget] = None) -> Tuple[AutoFit, Optional[sf.PickNeed]]:
        """auto_fit with its pick measured by llama-server's own fit (A67 (2), pick_need): when the need is more than
        the room, Auto fit chooses again with that much less room (the gap to CARL's estimate), at most FIT_ROUNDS
        times, as a start makes its plan again. The need is None when nothing was measured (no pick, several GPUs,
        no server fit port)."""
        return self._measured(self.candidates(models), models, budget or self.budget(), goal, scope)

    def _measured(self, cands: List[Candidate], models: List[ModelInfo], budget: Budget, goal: Goal,
                  scope: Scope) -> Tuple[AutoFit, Optional[sf.PickNeed]]:
        fit, less = auto_fit(cands, budget, goal, scope), 0.0
        for round_ in range(FIT_ROUNDS + 1):
            m = self.inventory.find(fit.pick.name, models) if fit.pick else None
            need = self.pick_need(m, fit.pick, fit.plan, fit.budget) if m and fit.pick and fit.plan else None
            if need is None:
                return fit, None
            need = replace(need, less=less)
            if need.fits or round_ == FIT_ROUNDS:
                return fit, need
            less = max(less, need.need - need.estimate)
            fit = auto_fit(cands, budget.less_room(less), goal, scope)
        return fit, None                     # not reached: the last round returns

    def pick_need(self, m: ModelInfo, c: Candidate, plan: Plan, budget: Budget) -> Optional[sf.PickNeed]:
        """Auto fit's pick measured (A67 (2)): llama-server's own fit of its plan (fit_argv, at -ub 512 as Auto fit
        plans) on its file (E), or before a download on a stub of its header (stub E); a drafter in its own file adds
        its file and its bound (draft_bound). B, CARL's upper bound, where the fit cannot run (no llama-server, no
        header). None without a server fit port, over several GPUs (a start there takes the estimate) or without a
        header shape."""
        devices = budget.devices
        if c.shape is None or len(devices) > 1:
            return None
        room = float(devices[0].room) + budget.less if devices else budget.allowed_whole
        return self.setup_need(m, c.shape, c.weights, c.calib, plan.spec, plan.ctx, plan.slots, plan.kv, UBATCH,
                               budget.swa_full, devices, plan.cpu_moe, budget.ram, budget.reserve, room, c.downloaded,
                               plan.need)

    def setup_need(self, m: ModelInfo, shape: ModelShape, weights: float, calib: Calibration, spec: Spec, ctx: int,
                   slots: int, kv: str, ub: int, swa_full: bool, devices: Sequence[Slot], cpu_moe: int, ram: float,
                   reserve: float, room: float, downloaded: bool, estimate: Optional[float] = None,
                   ask: bool = True) -> Optional[sf.PickNeed]:
        """One setup of a model measured (A67 (2), A72): llama-server's own fit on its file (E), or when it is not
        downloaded on a stub of its header (stub E), with a drafter in its own file adding its file and its bound. ask
        False: only what an earlier fit kept (no server is started and no header is read): None when nothing was kept.
        ask True: B, CARL's upper bound, where the fit cannot run. estimate: CARL's need of the same setup (computed
        when not given); room: what a model can use on the device. None without a server fit port or with several GPUs."""
        if self.server_fit is None or len(devices) > 1:
            return None
        window = bool(shape.get("swa_window"))
        head = spec.mtp and has_head(shape)                        # an MTP head in the file: the fit measures it
        drafter = spec.mtp and not head and spec.draft_bytes > 0     # a drafter in its own file: bounded
        hf = m.get("hf") or {}
        argv = fit_argv(m.get("path") or hf.get("file", "") or m.get("name", ""), ctx, slots, kv, ub,
                        swa_full and window, spec.n if head else 0, devices[0].name if devices else "", cpu_moe)
        fit = self._fit_port()
        name = sf.stub_name(hf.get("repo", ""), hf.get("revision", ""), hf.get("file", ""))
        size = int(m.get("bytes") or 0)
        if not ask:
            log = fit.kept(argv) if downloaded else fit.kept_stub(name, size, argv)
            if sf.parse(log or "") is None:
                return None
        elif downloaded:
            log = fit.measure(argv)
        else:
            log = fit.measure_stub(name, size, lambda: self._head_of(m), argv)
        if estimate is None:
            estimate = self.need_of(shape, weights, ctx, slots, kv, swa_full, spec, ub, devices, cpu_moe, calib, ram,
                                    reserve)
        got = sf.parse(log or "")
        if got is None:
            need = self.need_of(shape, weights, ctx, slots, kv, swa_full, spec, ub, devices, cpu_moe,
                                b_calibration(calib), ram, reserve)
            return sf.PickNeed(need, sf.B, estimate, room)
        need, source = float(got.device_bytes()) + calib.context, sf.E if downloaded else sf.STUB_E
        if drafter:
            need += spec.draft_bytes + sf.draft_bound_of(shape, ctx, slots, ub, swa_full,
                                                         "cuda" if devices else "metal", spec.draft_bytes)
            source = sf.E_BOUND if downloaded else sf.STUB_E_BOUND
        return sf.PickNeed(need, source, estimate, room)

    def measure_row(self, m: ModelInfo, shape: ModelShape, weights: float, calib: Calibration, spec: Spec, slots: int,
                    kv: str, ub: int, swa_full: bool, devices: Sequence[Slot], ram: float, reserve: float,
                    room: float, plan_at: Callable[[float], Optional[Tuple[int, int, float]]],
                    ask: bool) -> Optional[sf.RowFit]:
        """A row of carl fit's table measured (A72): the largest window that fits by llama-server's own fit. plan_at(less)
        is CARL's estimate with `less` bytes less room: (the window, the layers whose experts stay in the RAM, the
        estimate's need there), None when not even the smallest window fits. The window is measured; when the fit needs
        more than the room, the estimate is made again with the gap less room, at most FIT_ROUNDS times (as Auto fit's
        pick and a start do). ask: run the fit, or (False) read only the fits that were kept: a table fills its other
        rows from them. None when the row stays an estimate: no server fit port, several GPUs, nothing kept, no header or
        no fit (B is not a measure), or a window that never fitted."""
        less = 0.0
        for _ in range(FIT_ROUNDS + 1):
            at = plan_at(less)
            if at is None:
                return None
            ctx, cpu_moe, est = at
            need = self.setup_need(m, shape, weights, calib, spec, ctx, slots, kv, ub, swa_full, devices, cpu_moe, ram,
                                   reserve, room, m.get("status") == "downloaded", est, ask)
            if need is None or need.source == sf.B:
                return None
            if need.fits:
                return sf.RowFit(ctx, cpu_moe, replace(need, less=less))
            less = max(less, need.need - need.estimate)
        return None

    def _fit_port(self) -> ServerFit:
        """llama-server's own fit, built once for this service (a command's table makes many asks)."""
        assert self.server_fit is not None
        if self._port is None:
            self._port = self.server_fit()
        return self._port

    def _head_of(self, m: ModelInfo) -> bytes:
        """The header of a model not on disk, read once for the rows that follow (a stub of each window)."""
        name = str(m.get("name", ""))
        if self._head[0] != name:
            self._head = (name, self.inventory.remote_head(m))
        return self._head[1]

    def need_of(self, shape: ModelShape, weights: float, ctx: int, slots: int, kv: str, full: bool, spec: Spec,
                ub: int, devices: Sequence[Slot], cpu_moe: Optional[int], calib: Calibration, ram: float,
                reserve: float) -> float:
        """CARL's need of a start on its one device (a Mac: its GPU): need_bytes, or with the experts of cpu_moe layers
        in the RAM the GPU side of that start. calib: the estimate's constants, or B's (calibration.bound, A67); ram,
        reserve: the RAM and the part kept free for the system and apps."""
        need = need_bytes(shape, weights, ctx, slots, kv, full, spec, ub, calib)
        if cpu_moe and offload.offloads(devices, shape):
            off = offload.plan_of(shape, weights, ctx, slots, kv, full, spec, ub, devices, ram, reserve, calib, cpu_moe)
            need = off.split.need if off is not None else need
        return need

    def pick_default(self, models: List[ModelInfo], budget: Optional[Budget] = None, goal: Goal = "everyday") -> str:
        """This Mac's default (the download offer, `download default`): auto fit's pick from
        the whole catalogue, measured by llama-server's own fit on a stub of its header (measured_fit, A67). When the
        headers can't be read (offline) and so nothing could be sized, the catalogue default (default_small when its
        weights alone don't fit)."""
        b = budget or self.budget()
        stock = [m for m in models if not m.get("custom")]                  # what can be downloaded
        cands = self.candidates(stock)
        fit, _ = self._measured(cands, stock, b, goal, "catalogue")
        if fit.pick:
            return fit.pick.name
        if any(c.eligible and c.shape is None for c in cands):
            cat = self.inventory.load_catalog()
            default = cat.get("default", "")
            m = self.inventory.find(default, models)
            return offline_default(default, cat.get("default_small"), m.get("bytes") if m else None, b.allowed)
        raise ConfigError(f"Auto fit: {fit.because()}. {self.words.command} fit shows every model.")

    def auto_launch(self, models: List[ModelInfo], cfg: Config) -> Tuple[ModelInfo, Optional[str], Plan]:
        """The model llama.model = auto starts: auto fit's pick when it is downloaded, else
        the best downloaded stock model that fits (with a note naming the pick to download)."""
        goal, scope = self.auto_settings(cfg)
        cmd, here = self.words.command, self.words.machine
        cands = self.candidates(models)
        fit = auto_fit(cands, self.budget(), goal, scope)
        start = best_downloaded(fit, cands)
        if not any(m.get("status") == "downloaded" for m in models):
            raise ConfigError(f"no model is downloaded. To download the model that Auto fit chooses for {here}: "
                              f"{cmd} download default")
        if start.pick is None or start.plan is None:
            want = f"Download {fit.name} ({cmd} download {fit.name}), or c" if fit.name else "C"
            raise ConfigError(f"Auto fit: {start.because()}. "
                              f"{want}hoose a model by name: {cmd} config set llama.model NAME")
        m = self.inventory.find(start.pick.name, models)
        if m is None:                          # the candidates came from these models
            raise ConfigError(f"Auto fit: {start.pick.name} is not in the model list")
        note = None
        if start is not fit and fit.name:
            note = (f"Auto fit chooses {fit.name} for {here}, but it is not downloaded ({cmd} download {fit.name}). "
                    f"CARL starts {start.pick.name}, the best downloaded model that fits.")
        return m, note, start.plan

    def resolve_launch(self, name: Optional[str], cfg: Config) -> Tuple[ModelInfo, List[ModelInfo], Optional[str]]:
        """The model a llama.cpp start uses: name, else config llama.model, else auto fit's
        pick (the best downloaded stock model that fits when the pick isn't downloaded)."""
        models = self.inventory.all_models(cfg)
        explicit = name or dm.configured_model(cfg)
        if explicit:
            last = dm.configured_model(cfg)             # the quantization a person chose last (A68)
            m = dm.select_named(models, explicit, self.config.expand(explicit), self.words,
                                chosen=[last] if last else [], fits=self.fits_ladder, cpu=self.cpu_only())
            return m, models, self.cpu_quant_line(models, m, explicit, [last] if last else [])
        m, note, _ = self.auto_launch(models, cfg)
        return m, models, note

    def cpu_quant_line(self, models: List[ModelInfo], m: ModelInfo, named: str,
                       chosen: Sequence[str] = ()) -> Optional[str]:
        """The start line when a start on the CPU alone took an IQ4_NL or Q4_0 before a K-quant of its model, because the
        name did not say which quantization (A72); None when it did, when the person's last choice (chosen: the entry
        names of llama.model) or the latest Auto-tune record picked the entry, or the order changed nothing."""
        base, quant = split_spec(named)
        group = dm.group_of(models, base)
        if not self.cpu_only() or quant or group is None or base != group.name or not group.several:
            return None
        if default_quant(group, chosen, self.fits_ladder, False).get("name") == m.get("name"):
            return None                                   # without the ladder the start takes it too: not the ladder's doing
        return cpu_quant_note(m, [e for e in group.entries if e.get("status") == "downloaded"]) or None

    def default_quants(self, models: List[ModelInfo], cfg: Config) -> Dict[str, str]:
        """For each model with several quantizations (by the model's name) and one of them downloaded, the entry a start
        takes when none is named (decision A68; quants.default_quant): the one chosen last (llama.model), else the
        latest Auto-tune record, else the largest that fits."""
        last = dm.configured_model(cfg)
        cpu = self.cpu_only()
        return {g.name: default_quant(g, [last] if last else [], self.fits_ladder, cpu).get("name", "")
                for g in group_models(models) if g.several and any(m.get("status") == "downloaded" for m in g.entries)}

    def fits_ladder(self, m: ModelInfo) -> bool:
        """Does a start of this model fit this computer on some step of the context ladder (96K, 64K, 48K ...)? The fit
        check of Auto fit for this one model; False when its header cannot be read."""
        shape = self.inventory.shape_of(m)
        if shape is None:
            return False
        b = self.budget()
        c = replace(candidate(m, shape), calib=self.calibration())
        return model_plan(c, b.allowed, b.swa_full, None, None, UBATCH, b)[1] is not None

    # ------------------------------------------------------------ the launcher's settings
    @one_plan
    def launch_env(self, name: Optional[str], use_config: bool) -> Tuple[Dict[str, SettingValue], Optional[str]]:
        """Settings for serve-llama.sh (everything below flags and environment) and a note."""
        cfg = self.config.load_config() if use_config else Config()
        path = self.inventory.model_file(name)
        note: Optional[str] = None
        if path:
            m = self.inventory.find(path, self.inventory.all_models(self.config.load_config())) or dm.custom_entry(
                model_name(path), path, {"source": "file"}, self.inventory.files)
        elif name or dm.configured_model(cfg):
            m, _, note = self.resolve_launch(name, cfg)
        else:
            m, note, _ = self.auto_launch(self.inventory.all_models(cfg), cfg)
        vals, src = self.effective_tune(m, cfg)          # A71, A72: the record's type holds where it was measured
        advice = dm.tune_advice(m, vals, src, self.words)
        stale = self.tune_stale(m)                       # a record from another build or backend: said, kept (A72)
        shape = self.inventory.shape_of(m)
        source = dm.mtp_source(m, shape) if shape is not None else None
        fallback = None
        if source is not None:                # header unreadable: llama-server reports the file itself
            vals["spec"], fallback = dm.mtp_fallback(m, str(vals["spec"]), source, self.words)
        ub = self.ub_order(m, cfg)
        order = self.apply_order(m, vals, src, cfg, shape, source, ub) if shape is not None else ""
        note = "\n".join(n for n in (note, advice, stale, fallback, order) if n) or None
        return build_launch_env(m, vals, src, cfg, swa=bool(shape and shape.get("swa_window")), mtp=source,
                                os_name=self.os_name, ub=ub, stamp=dm.tune_stamp(m),
                                pin=dm.tuned_pin(m)), note

    def answer_ub(self, model: str, ask: uo.UbAsk, choice: str) -> None:
        """Save the answer to a -ub question in config.json (ub_order.answer_config): Use tuned removes the config
        value; Keep makes it this model's choice, until the model's next tune."""
        doc = uo.answer_config(self.config.load_config().to_json(), model, ask, choice)
        self.config.save_config(self.config.validate(doc)[0])

    def model_ub(self, m: ModelInfo) -> int:
        """The model's own -ub (A45): its Auto-tune record's, else this OS's default (2048 on a PC, 512 on a Mac). The
        -ub a start uses is ub_order's."""
        return dm.tuned_ub(m) or default_ub(self.os_name)

    def ub_order(self, m: ModelInfo, cfg: Optional[Config] = None) -> uo.UbOrder:
        """The -ub of a start of this model and where it comes from (A51; domain/server/ub_order.py, the one order
        every reader follows): a -ub typed in the server options, else the model's saved choice, else its Auto-tune
        record's, else (a model without a record) llama.ub, else this OS's default. A config value that differs from the
        record is the order's ask (UbOrder.ask); the caller asks or says it ignored it. cfg: config.json (None: read)."""
        if cfg is None:
            try:
                cfg = self.config.load_config()
            except ConfigError:
                cfg = Config()
        prof = cfg.profile(m.get("name", ""))
        extra = [str(a) for a in (cfg.llama.get("extra_args") or [])] if isinstance(cfg.llama.get("extra_args"), list) \
            else []
        typed = extra_value(extra, UB_FLAGS)

        def size(v: object) -> int:
            return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
        return uo.ub_order(int(typed) if typed else 0, size(prof.get("ub")), str(prof.get("ub_tune") or ""),
                           size(cfg.llama.get("ub")), dm.tuned_ub(m) or 0, dm.tune_stamp(m),
                           default_ub(self.os_name), dm.tuned_by_ub(m))

    def apply_order(self, m: ModelInfo, vals: Settings, src: Dict[str, SettingSource], cfg: Config,
                    shape: ModelShape, source: Optional[dm.MtpSource], ub: Optional[uo.UbOrder] = None) -> str:
        """Auto fit's order for the model a start runs (domain/autofit.py, Phase 23.4.4), when the window
        is not the user's (config.json or Auto-tune): 2 x 96K, 2 x 64K, 2 x 48K, 1 x 96K, 1 x 64K, 1 x 48K (on NVIDIA
        starts the windows begin at 128K: A53, Budget.tiers), never above the model's own window; MTP goes (n-gram
        stays) before a slot or the window. Sets ctx and spec in vals (source auto-fit; the launcher's carl fit --plan
        then takes the same slots) and returns the start line ("" when nothing changed or nothing fits: the launcher's
        check refuses).
        ub: the -ub of the start (ub_order): a person's (typed, kept, llama.ub) is used as it is; the model's own (the
        tune record's, else the OS's) takes its rung at the -ub that keeps the rung -ub 512 gets (1024, then 512: A45,
        the context first), and the start's plan lowers it to the same at that window."""
        ctx, want = vals.get("ctx"), str(vals.get("slots", "auto"))
        if src.get("ctx") in ("config", "auto-tune") or not isinstance(ctx, int) or not (want == "auto" or want.isdigit()):
            return ""
        files = self.inventory.files
        path = m.get("path", "")
        weights = files.size(path) if files.exists(path) else int(m.get("bytes", 0))
        dpath = m.get("draft_path", "")
        drafter = files.size(dpath) if source == "drafter" and dpath and files.exists(dpath) else 0
        other = dm.spec_drafter(m, str(vals.get("spec") or "none"))      # a DFlash, DSpark or EAGLE-3 file (WCATX)
        if other is not None and files.exists(str(other.get("path", ""))):
            drafter = files.size(str(other["path"]))
        vpath = m.get("vision_path", "")
        vision = files.size(vpath) if cfg.llama.get("vision") and dm.vision_ready(m) and files.exists(vpath) else 0
        n = vals.get("spec_n", 1)
        spec = mtp_spec(str(vals.get("spec") or "none"), n if isinstance(n, int) else 1, shape, drafter,
                        src.get("spec") in ("config", "auto-tune"), vision)     # a person's or Auto-tune's: kept (A51)
        c = replace(candidate(m, shape), weights=weights, kv=str(vals.get("kv") or "q4_0"), spec=spec,
                    calib=self.calibration())
        if "config" in (src.get("spec"), src.get("spec_n")):      # the user's own speculation: no other MTP mode
            c = replace(c, mtp_modes=())
        budget = self.budget()
        slots = None if want == "auto" else int(want)
        top = window_cap(ctx, budget.tiers)         # the default window is the top of the machine's ladder (A53)

        def setup(ub: int) -> Tuple[object, str]:
            t, p = model_plan(c, budget.allowed, budget.swa_full, top, slots, ub, budget)
            return ((t, p.ctx, p.slots, p.spec.kind, p.cpu_moe > 0) if p else None), (p.describe() if p else "nothing")
        fixed = ub is not None and ub.fixed
        choice = ub_choice(ub.ub, setup) if ub is not None and not fixed else None
        at_ub = choice.ub if choice else ub.ub if ub is not None else UBATCH
        tier, plan = model_plan(c, budget.allowed, budget.swa_full, top, slots, at_ub, budget)
        if plan is None:
            return ""
        if plan.ctx != ctx:
            vals["ctx"], src["ctx"] = plan.ctx, "auto-fit"
        if plan.dropped or plan.spec != c.spec:       # MTP went, or another measured MTP mode fits (Phase 23.5.1.8)
            vals["spec"], src["spec"] = plan.spec.kind, "auto-fit"
            if plan.spec.mtp:
                vals["spec_n"], src["spec_n"] = plan.spec.n, "auto-fit"
        # the start's own plan, at the window the start really runs (the environment may set another), lowers the
        # model's -ub the same way and says why: UB_AUTO stays the model's
        return order_note(m.get("name", ""), tier, plan, top, self.words, budget.tiers)

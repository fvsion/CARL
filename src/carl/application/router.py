"""Router mode's presets: every downloaded model with the settings a single-model start of it would use, within the
budget (application/fit.py), the ones that don't fit left out, and the model a start loads first (the rules:
domain/server/router_presets.py). Each model's -ub and load mode are a single start's (A46, A51): the order of
domain/server/ub_order.py (a -ub typed in the server options, the model's saved choice, its Auto-tune record's, else
llama.ub for a model without one, else this OS's default, with the size that keeps the most experts on the GPUs for a
model without a record) lowered as the start lowers it, and load mode none when the free RAM holds the model under it
and the load reserve. Router mode cannot ask: a config -ub that differs from a model's tuned one is ignored, and the
model's line says so. MTP is off for a model with experts in the RAM unless its speculation is a person's or Auto-tune's.
"""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Dict, Optional, Sequence, Tuple

from ..domain.catalog import inventory as dm
from ..domain.catalog.drafts import without_file_kinds
from ..domain.config.settings import LLAMA_KEYS, Config
from ..domain.hardware.cpu_topology import typed_options
from ..domain.memory.fit import UBATCH, Spec, mtp_spec, ub_choice
from ..domain.memory.gguf import ModelShape
from ..domain.memory.split import Slot
from ..domain.server.launch_plan import PIN_ON, PINNED, own_load, template_file
from ..domain.server.router_presets import Common, ModelPlan, Preset, plan_model
from ..domain.server.ub_order import DEFAULT, ub_pick
from ..domain.shared.errors import ConfigError
from ..domain.shared.types import Settings
from .config import ConfigService
from .fit import FitService
from .inventory import Inventory


class RouterService:
    """The router presets of the downloaded models. Contract C-107."""

    def __init__(self, config: ConfigService, inventory: Inventory, fit: FitService) -> None:
        self.config = config
        self.inventory = inventory
        self.fit = fit

    def router_preset(self, cfg: Config, templates_dir: str, thread_pin: str = "") -> Tuple[Preset, Common]:
        """Router mode's presets (domain/router.py): every downloaded model with the settings a
        single-model start of it would use, the ones that don't fit left out, and the model a
        start loads first. templates_dir: where the thinking-toggle chat templates are. thread_pin: the start's
        THREAD_PIN from the environment ("on", "off" or ""): a model pins when it says on, or when it is not set and
        the model's tune record says pinned, as a single start's (A57)."""
        ll = {k: cfg.llama.get(k, s.default) for k, s in LLAMA_KEYS.items()}
        cache = ll["cache_ram"]
        swa = str(cfg.cache.get("swa") or "auto")
        common = Common(batch=int(str(ll["batch"])), ubatch=UBATCH, ckpt=int(str(ll["ckpt"])),
                        ckpt_step=int(str(ll["ckpt_step"])), cache_ram=cache if isinstance(cache, int) else None,
                        # the settings folder's slots, as a single start (serve-llama.sh SLOT_DIR): CARL_CONF_DIR too
                        slot_dir=os.path.join(os.path.dirname(self.config.stores.local_path), "slots"),
                        swa_mode=swa)
        budget = self.fit.budget(swa=swa)
        devices = self.fit.slots()              # a PC: the GPUs each model is planned over (none on a Mac)
        n = ll["n_cpu_moe"]                     # llama.n_cpu_moe: a number of layers, else auto (the plan finds N)
        cpu_moe = n if isinstance(n, int) and not isinstance(n, bool) else None
        # the -ub and the load mode of each model as a single start's (A46, A51): the order's -ub (the server options'
        # own wins, planned at its size and passed on to the server as it is), lowered for the model's setup when it is the model's own; the
        # server options' own load option wins
        extra = [str(a) for a in ll["extra_args"]] if isinstance(ll["extra_args"], list) else []
        loads = not own_load(extra)
        typed_threads = typed_options(extra)            # the user's own thread options win (A54)
        pin = self.fit.thread_pin()                     # one thread per physical core, pinned, with experts in the RAM
        files = self.inventory.files
        preset = Preset()
        for m in self.inventory.all_models(cfg):
            if m.get("status") != "downloaded":
                continue
            name, path = m.get("name", ""), m.get("path", "")
            shape = self.inventory.local_shape(path)
            if shape is None:
                preset.skipped.append((name, "its GGUF header can't be read"))
                continue
            tmpl = template_file(path, templates_dir, self.config.models_dir(cfg))
            vals, src = self.fit.effective_tune(m, cfg)
            source = dm.mtp_source(m, shape)
            # the router's presets know MTP and n-gram only: a drafter file of another kind (DFlash, DSpark, EAGLE-3) and the
            # vision projector are for the single-model start (WCATX)
            vals["spec"] = dm.mtp_fallback(m, without_file_kinds(str(vals["spec"]))[0], source)[0]
            draft = m.get("draft_path") if source == "drafter" and "draft-mtp" in str(vals["spec"]) else None
            weights = files.size(path)
            seen: Dict[int, Tuple[Optional[ModelPlan], str]] = {}

            def at(ub: int) -> Tuple[Optional[ModelPlan], str]:
                if ub not in seen:
                    seen[ub] = plan_model(name, path, vals, shape, weights, budget.gpu_limit, int(budget.ram),
                                          budget.reserve, common,
                                          tmpl if ll["think_toggle"] and files.exists(tmpl) else None, draft,
                                          files.size(draft) if draft else 0, devices, cpu_moe, ub,
                                          src.get("spec") in ("config", "auto-tune"))
                return seen[ub]

            def setup(ub: int) -> Tuple[object, str]:
                p = at(ub)[0]
                return ((p.slots, p.ctx, p.spec, p.swa, p.window, p.cpu_moe > 0) if p else None), (
                    p.describe() if p else "nothing")
            order = self.fit.ub_order(m, cfg)
            if order.fixed:                                         # a typed -ub too, planned at its own size (A53)
                plan, why = at(order.ub)
            else:
                start = order.ub
                if order.source == DEFAULT and start > UBATCH:      # no tune record: the size that keeps most experts
                    start = ub_pick(start, lambda u: (setup(u)[0], at(u)[0].cpu_moe if at(u)[0] else 0))
                plan, why = at(ub_choice(start, setup).ub)
            if plan and order.ask:                                  # nobody to ask here: the tuned -ub, said (A51)
                plan = replace(plan, ub_note=order.ask.ignored(plan.ub or order.ub))
            if plan and self.fit.cpu_kv_open(m, cfg):               # the CPU alone: the type for speed, as a start's (A72)
                plan = self.cpu_kv(plan, vals, shape, weights, draft, files.size(draft) if draft else 0, common, tmpl,
                                   devices, cpu_moe, src.get("spec") in ("config", "auto-tune"),
                                   bool(ll["think_toggle"])) or plan
            parts = self.fit.tune_stale_parts(m) if plan else ""     # a record from another build or backend: said, kept (A72)
            if plan and parts:
                plan = replace(plan, stale_note=f"{parts}; to measure again: {self.fit.words.command} tune {name}")
            pinned = thread_pin == PIN_ON or (not thread_pin and dm.tuned_pin(m) == PINNED)
            if plan and plan.cpu_moe and pin is not None and not typed_threads and pinned:
                plan = replace(plan, threads=pin.threads, cpu_mask=pin.mask_text())      # A57: as a single start's
            if plan and loads:
                plan = replace(plan, load=self.fit.load_mode(self.ram_need(plan, shape, devices)).mode)
            if plan:
                preset.models.append(plan)
            else:
                preset.skipped.append((name, why))
        try:
            first: Optional[str] = self.fit.resolve_launch(None, cfg)[0].get("name")
        except ConfigError:
            first = None
        names = [p.name for p in preset.models]
        preset.start = first if first in names else (names[0] if names else None)
        return preset, common

    def cpu_kv(self, plan: ModelPlan, vals: Settings, shape: ModelShape, weights: int, draft: Optional[str],
               draft_bytes: int, common: Common, tmpl: str, devices: Sequence[Slot], cpu_moe: Optional[int],
               kept: bool, think: bool) -> Optional[ModelPlan]:
        """The model's section with the context memory type of a start on the CPU alone (A71, A72: the same rule as a
        single start, FitService.cpu_kv): f16 when the free RAM covers the setup, else q8_0, else q4_0. The setup stays
        (the window and the slots); None when the section with that type does not fit, then the section stands."""
        spec = Spec(plan.spec, plan.spec_n, draft_bytes, kept) if draft else mtp_spec(plan.spec, plan.spec_n, shape, 0,
                                                                                    kept)
        choice = self.fit.cpu_kv_for(shape, weights, plan.ctx, plan.slots, plan.kv, common.swa_mode, spec,
                                     plan.ub or common.ubatch)
        if choice.kv == plan.kv:
            return replace(plan, kv_note=choice.short())
        budget = self.fit.budget(swa=common.swa_mode)
        files = self.inventory.files
        chosen: Settings = {**vals, "kv": choice.kv, "slots": str(plan.slots), "ctx": plan.ctx}
        got, _ = plan_model(plan.name, plan.path, chosen, shape, weights, budget.gpu_limit, int(budget.ram),
                            budget.reserve, common, tmpl if think and files.exists(tmpl) else None, draft,
                            draft_bytes, devices, cpu_moe, plan.ub, kept)
        return replace(got, kv_note=choice.short(), ub_note=plan.ub_note) if got is not None else None

    def ram_need(self, plan: ModelPlan, shape: ModelShape, devices: Sequence[Slot]) -> float:
        """What a model's server holds in the RAM under load mode none, as a single start counts it
        (FitService.load_need): the RAM side with experts in the RAM; on a Mac or a GPU that shares the RAM the whole
        start; on discrete GPUs the weights that stay in the RAM."""
        if plan.cpu_moe:
            return plan.ram
        return self.fit.ram_need(plan.need, shape, devices)

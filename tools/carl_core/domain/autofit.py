"""Auto fit: the best stock model for this Mac and a goal, with the reasons every
better-ranked model was passed over.

Quality is the catalogue rank (1 = best: published benchmarks and CARL's code test first, then the quantization).
CARL prioritises speed, so the goal decides the family first:
  everyday   the fast builds: MoE (35B-A3B: fast, usually sufficient) and the small dense
             builds the catalogue marks fast (Gemma 4 E4B); another build only when no fast
             build fits (said so in the result)
  hard-code  the dense builds (27B: better at code and hard tasks, slower); MoE as the fallback
Only ranked stock models are candidates: abliterated models are picked by hand. A custom
model takes part only when the user's card opts it in (auto_fit: a rank, an arch, stock):
its rank is the user's word, not measured, so it never joins by default.

Within the goal's family the passes are, in order, on every Mac (user, 2026-10-09: "every Mac with
48K floor"; the first pass any candidate meets wins, the best rank within it):
  2 x 96K, 2 x 64K, 2 x 48K (the main session + a coder subagent), then 1 x 96K, 1 x 64K, 1 x 48K
Nothing below 48K. In each pass a model with MTP that fits only without it drops MTP (n-gram stays)
before Auto fit drops a slot or the window, and says so.
The memory allowed is the smaller of the GPU limit and RAM minus the reserve for macOS and
apps (more with the VM up), so a pick leaves the Mac usable. Pure: shapes, sizes and the
limits come in as values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from .fit import NO_SPEC, UBATCH, Spec, mtp_spec, need_bytes, window_label
from .gguf import GIB, ModelShape, ctx_train
from .models import draft_bytes
from .types import ModelInfo

Goal = Literal["everyday", "hard-code"]
Scope = Literal["catalogue", "downloaded"]
GOALS: Tuple[Goal, ...] = ("everyday", "hard-code")
SCOPES: Tuple[Scope, ...] = ("catalogue", "downloaded")
# The words every screen uses (reference/glossary.md): the goal "hard code" (config: hard-code),
# the candidates, memory in GiB, the context in K = 1024 tokens.
GOAL_TEXT: Dict[Goal, str] = {"everyday": "everyday (fast models first)",
                               "hard-code": "hard code (dense models first: better code, slower)"}
GOAL_NAME: Dict[Goal, str] = {"everyday": "everyday", "hard-code": "hard code"}
SCOPE_TEXT: Dict[Scope, str] = {"catalogue": "all catalogue models", "downloaded": "downloaded models only"}
ARCH_TEXT: Dict[str, str] = {"moe": "MoE", "dense": "dense"}
FAMILY_TEXT: Dict[Goal, str] = {"everyday": "fast (MoE or small dense)", "hard-code": "dense"}
KV_TEXT: Dict[str, str] = {"q4_0": "q4", "q8_0": "q8"}
MIN_WINDOW = 49152                  # the floor: below 48K a window is not worth starting (user, 2026-10-09)


def as_goal(v: object) -> Goal:
    """A goal from config or the command line (anything else: everyday)."""
    return "hard-code" if v == "hard-code" else "everyday"


def as_scope(v: object) -> Scope:
    """A scope from config or the command line (anything else: catalogue)."""
    return "downloaded" if v == "downloaded" else "catalogue"


def gib(n: float) -> str:
    """Memory in GiB, one decimal (glossary: GiB for memory)."""
    return f"{n / GIB:.1f} GiB"


def setup_text(slots: int, ctx: Optional[int], kv: Optional[str] = None) -> str:
    """Slots and context in words: 2 slots × 96K tokens (q4)."""
    head = f"{slots} slot{'s' if slots != 1 else ''} × {window_label(ctx or 0)} tokens"
    return f"{head} ({KV_TEXT.get(kv, kv)})" if kv else head


@dataclass(frozen=True)
class Candidate:
    """A model as auto fit sees it. shape None: its GGUF header could not be read. weights: the model
    file; spec: its speculation (an MTP drafter's weights are in it)."""
    name: str
    arch: str
    rank: Optional[int]
    abliterated: bool
    downloaded: bool
    weights: int
    shape: Optional[ModelShape]
    kv: str = "q4_0"
    opted_in: bool = True           # a custom model: its card's auto_fit (catalogue models: always)
    fast: bool = False              # a small dense build the everyday goal takes with the MoE builds
    spec: Spec = NO_SPEC            # the catalogue's speculation (MTP only with a head or a drafter)

    def kind(self) -> str:
        """MoE, small dense or dense: what the result calls the build."""
        return "small dense" if self.fast and self.arch != "moe" else ARCH_TEXT.get(self.arch, self.arch or "?")

    @property
    def eligible(self) -> bool:
        """A ranked stock model. Abliterated models are only ever picked by hand; a custom
        model only when its card opts it in."""
        return self.rank is not None and not self.abliterated and self.opted_in


def candidate(m: ModelInfo, shape: Optional[ModelShape]) -> Candidate:
    """A model record (catalogue or custom) and its header shape as a candidate: its speculation from
    the catalogue (tune.spec, tune.spec_n), MTP with its head or its drafter (the drafter's weights count
    only while MTP runs)."""
    rank = m.get("rank")
    tune = m.get("tune") or {}
    kv = tune.get("kv", "q4_0")
    n = tune.get("spec_n", 1)
    spec = mtp_spec(str(tune.get("spec") or "none"), n if isinstance(n, int) else 1, shape, draft_bytes(m))
    return Candidate(name=m.get("name", ""), arch=str(m.get("arch") or "").lower(),
                     rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                     abliterated=bool(m.get("abliterated")), downloaded=m.get("status") == "downloaded",
                     weights=int(m.get("bytes", 0)), shape=shape, kv=kv if isinstance(kv, str) else "q4_0",
                     opted_in=not m.get("custom") or m.get("auto_fit") is True, fast=m.get("fast") is True,
                     spec=spec)


@dataclass(frozen=True)
class Budget:
    """What a model may use on this Mac: the GPU limit, and RAM less the reserve for macOS
    and apps (ram 0 = unknown: the GPU limit alone)."""
    gpu_limit: float
    ram: float
    reserve: float
    vm_up: bool = False             # VMware's network is up (the reserve is larger)
    swa: str = "auto"               # cache.swa: sliding-window layers at full length only with "full"

    @property
    def swa_full(self) -> bool:
        """Plan a sliding-window model's cache at full length (cache.swa full); auto and window plan
        the window (auto takes the full cache at the start only when it fits: fit.swa_plan)."""
        return self.swa == "full"

    @property
    def allowed(self) -> float:
        if self.ram <= 0:
            return self.gpu_limit
        return max(0.0, min(self.gpu_limit, self.ram - self.reserve))

    def describe(self) -> str:
        """What a model may use, and the reason, as a short phrase: 25.0 GiB (the GPU memory limit)."""
        if self.ram > 0 and self.ram - self.reserve < self.gpu_limit:
            return f"{gib(self.allowed)} (the RAM, {gib(self.ram)}, minus {gib(self.reserve)} kept free for macOS and apps)"
        return f"{gib(self.allowed)} (the GPU memory limit)"

    def explain(self) -> str:
        """What a model may use on this Mac, in full sentences (the CLI's ./carl.sh fit)."""
        if self.ram <= 0:
            return f"A model can use {gib(self.allowed)}: the GPU memory limit."
        free = self.ram - self.reserve
        kept = f"the RAM ({gib(self.ram)}) minus the memory kept free for macOS and apps ({gib(self.reserve)})"
        if free < self.gpu_limit:
            return (f"A model can use {gib(self.allowed)}: {kept}. "
                    f"This is less than the GPU memory limit ({gib(self.gpu_limit)}).")
        return (f"A model can use {gib(self.allowed)}: the GPU memory limit. "
                f"This is less than {kept}, which is {gib(free)}.")


@dataclass(frozen=True)
class Plan:
    """How a model runs: slots of ctx tokens each at a KV cache type, its speculation, and the memory
    it needs. dropped: the model's MTP does not fit with these slots and window, so n-gram only."""
    ctx: int
    slots: int
    kv: str
    need: float
    spec: Spec = NO_SPEC
    dropped: bool = False

    def label(self) -> str:
        """The plan in short: 2 × 96K tokens, q4."""
        return f"{self.slots} × {window_label(self.ctx)} tokens, {KV_TEXT.get(self.kv, self.kv)}"

    def describe(self) -> str:
        """The plan in words: 2 slots × 96K tokens (q4)."""
        return setup_text(self.slots, self.ctx, self.kv)

    def spec_note(self) -> str:
        """Why the speculation is n-gram only, one sentence without the last full stop ("" when MTP fits)."""
        if not self.dropped:
            return ""
        return f"MTP does not fit with {setup_text(self.slots, self.ctx)} on this Mac, so the speculation is n-gram only"


@dataclass(frozen=True)
class Tier:
    """One pass: slots × ctx tokens per slot."""
    slots: int
    ctx: int

    def label(self) -> str:
        """2 slots × 64K tokens."""
        return setup_text(self.slots, self.ctx)

    def words(self) -> str:
        """two slots of 64K tokens."""
        return f"{'two slots' if self.slots == 2 else 'one slot' if self.slots == 1 else f'{self.slots} slots'} of " \
               f"{window_label(self.ctx)} tokens"


# Auto fit's order on every Mac (user, 2026-10-09): two slots first, the window from 96K down to the 48K floor.
TIERS: Tuple[Tier, ...] = tuple(Tier(n, k * 1024) for n in (2, 1) for k in (96, 64, 48))


def setups(c: Candidate) -> Tuple[Tuple[Spec, bool], ...]:
    """The speculation a candidate tries in each pass: its own, then without MTP (n-gram stays)."""
    return ((c.spec, False), (c.spec.without_mtp(), True)) if c.spec.mtp else ((c.spec, False),)


def plan_for(c: Candidate, tier: Tier, allowed: float, swa_full: bool = True, ub: int = UBATCH) -> Optional[Plan]:
    """The plan of a candidate in one pass, None when it doesn't fit (or its shape is unknown). MTP
    goes before the pass does: a model that fits only without it runs n-gram (Plan.dropped)."""
    if c.shape is None or tier.ctx > ctx_train(c.shape):
        return None
    for spec, dropped in setups(c):
        need = need_bytes(c.shape, c.weights, tier.ctx, tier.slots, c.kv, swa_full, spec, ub)
        if need <= allowed:
            return Plan(tier.ctx, tier.slots, c.kv, need, spec, dropped)
    return None


def model_plan(c: Candidate, allowed: float, swa_full: bool = True, cap: Optional[int] = None,
               slots: Optional[int] = None, ub: int = UBATCH) -> Tuple[int, Optional[Plan]]:
    """Auto fit's order for one model (a start whose window is not the user's): (the pass, its plan), or
    (the last pass, None) when no pass fits. cap: the model's own window (no pass above it); slots: a
    number the user set (only its passes)."""
    for t, tier in enumerate(TIERS):
        if (cap is not None and tier.ctx > cap) or (slots is not None and tier.slots != slots):
            continue
        plan = plan_for(c, tier, allowed, swa_full, ub)
        if plan:
            return t, plan
    return len(TIERS) - 1, None


def order_note(name: str, tier: int, plan: Plan, cap: int) -> str:
    """The start line for a start that Auto fit's order changed (a smaller window, or n-gram for MTP), one
    whole sentence; "" when it changed nothing."""
    parts = []
    if plan.ctx < cap:
        prev = TIERS[tier - 1] if tier else None
        why = f": {prev.label()} {'does' if prev.slots == 1 else 'do'} not fit this Mac" if prev else ""
        parts.append(f"{name} starts with {plan.describe()}, the first setup in Auto fit's order that fits{why}.")
    note = plan.spec_note()
    if note:
        parts.append(f"{note}.")
    return " ".join(parts)


def why_not(c: Candidate, tier: Tier, allowed: float, swa_full: bool = True) -> str:
    """Why a candidate fails a pass, with the numbers (the memory without MTP: the least it needs)."""
    if c.shape is None:
        return "has an unknown size: CARL cannot read its GGUF header (possibly no network)"
    spec = c.spec.without_mtp()
    alone = need_bytes(c.shape, c.weights, 0, 1, c.kv, swa_full, spec)
    if alone > allowed:
        return f"does not fit: the weights and buffers alone need {gib(alone)}, and a model can use {gib(allowed)}"
    if ctx_train(c.shape) < MIN_WINDOW:
        return (f"does not fit: its trained context is {window_label(ctx_train(c.shape))} tokens, "
                f"less than {window_label(MIN_WINDOW)}")
    need = need_bytes(c.shape, c.weights, tier.ctx, tier.slots, c.kv, swa_full, spec)
    return f"does not fit: {tier.label()} need {gib(need)}, and a model can use {gib(allowed)}"


@dataclass(frozen=True)
class Rejection:
    """A better-ranked candidate auto fit passed over, and why."""
    name: str
    rank: int
    reason: str

    def line(self) -> str:
        """One sentence without the last full stop: qwen3.8-27b (quality rank 1) is dense: ..."""
        return f"{self.name} (quality rank {self.rank}) {self.reason}"


@dataclass(frozen=True)
class AutoFit:
    """Auto fit's answer: the pick (None when nothing fits), how it runs, and why every
    better-ranked candidate was passed over. fallback: no model of the goal's family fits,
    the pick is of the other family. tier: the pass the pick met (an index of TIERS)."""
    goal: Goal
    scope: Scope
    budget: Budget
    pick: Optional[Candidate]
    plan: Optional[Plan]
    tier: int
    fallback: bool
    rejected: Tuple[Rejection, ...]

    @property
    def name(self) -> Optional[str]:
        return self.pick.name if self.pick else None

    def because(self) -> str:
        """Why the pick (or that nothing fits), in short sentences without the last full stop."""
        if not self.pick or not self.plan:
            return (f"No stock model with a quality rank fits this Mac ({SCOPE_TEXT[self.scope]}). "
                    f"A model can use {self.budget.describe()}")
        arch = self.pick.kind()
        here = "downloaded " if self.scope == "downloaded" else ""
        lead = (f"No {here}{FAMILY_TEXT[self.goal]} model fits, so this is the best {arch} model that fits. It holds "
                if self.fallback else
                f"Of the stock {arch} models that hold ")
        tail = "" if self.fallback else ", this model has the best quality rank"
        note = self.plan.spec_note()
        return (f"{lead}{self.holds()}{tail}. It needs {gib(self.plan.need)}, and a model can use "
                f"{self.budget.describe()}" + (f". {note}" if note else ""))

    def holds(self) -> str:
        """The setup of the pick and why Auto fit took it, as a phrase: two slots of 64K tokens (the main
        session and a subagent; two slots of 96K tokens do not fit)."""
        t = TIERS[self.tier]
        if self.tier == 0:
            return f"{t.words()} (the main session and a subagent)"
        prev = TIERS[self.tier - 1]
        both = "the main session and a subagent; " if t.slots == 2 else ""
        return f"{t.words()} ({both}{prev.words()} {'does' if prev.slots == 1 else 'do'} not fit)"

    def order_text(self) -> str:
        """Which setup Auto fit chose and why, in whole sentences without the last full stop (the
        Server panel, ./carl.sh fit)."""
        if not self.pick or not self.plan:
            return ""
        t = TIERS[self.tier]
        prev = TIERS[self.tier - 1] if self.tier else None
        text = f"Auto fit takes the first setup in its order that fits: {t.label()}" + (
            f", because {prev.label()} {'does' if prev.slots == 1 else 'do'} not fit" if prev else "")
        note = self.plan.spec_note()
        return text + (f". {note}" if note else "")

    def summary(self) -> str:
        """The pick in one line: its name and plan (the goal and the candidates are shown elsewhere)."""
        return f"{self.pick.name}, {self.plan.label()}" if self.pick and self.plan else "nothing fits"


def in_family(c: Candidate, goal: Goal) -> bool:
    """Is c in the goal's own family (module docstring)?"""
    if goal == "everyday":
        return c.arch == "moe" or (c.fast and c.arch == "dense")
    return c.arch == "dense"


def _order(cands: Sequence[Candidate], keep: bool, goal: Goal) -> List[Candidate]:
    return sorted((c for c in cands if in_family(c, goal) == keep), key=lambda c: (c.rank or 0, c.name))


def auto_fit(candidates: Sequence[Candidate], budget: Budget, goal: Goal = "everyday",
             scope: Scope = "catalogue") -> AutoFit:
    """The best ranked stock candidate for the goal that fits the budget (module docstring)."""
    allowed = budget.allowed
    eligible = [c for c in candidates if c.eligible]
    in_scope = [c for c in eligible if scope == "catalogue" or c.downloaded]
    groups = [_order(in_scope, True, goal), _order(in_scope, False, goal)]
    for g, group in enumerate(groups):
        for t, tier in enumerate(TIERS):
            for c in group:
                plan = plan_for(c, tier, allowed, budget.swa_full)
                if plan:
                    return AutoFit(goal, scope, budget, c, plan, t, g == 1,
                                   tuple(_rejections(eligible, c, t, g == 1, scope, goal, allowed, budget.swa_full)))
    return AutoFit(goal, scope, budget, None, None, len(TIERS) - 1, False,
                   tuple(_rejections(eligible, None, len(TIERS) - 1, False, scope, goal, allowed, budget.swa_full)))


def _rejections(eligible: Sequence[Candidate], pick: Optional[Candidate], tier: int, fallback: bool, scope: Scope,
                goal: Goal, allowed: float, swa_full: bool = True) -> List[Rejection]:
    """Every eligible candidate ranked above the pick (and, after a fallback, the whole goal
    family), best rank first, with the reason it lost."""
    def better(c: Candidate) -> bool:
        if pick is None or (fallback and in_family(c, goal)):
            return True
        return (c.rank or 0, c.name) < (pick.rank or 0, pick.name)

    out: List[Rejection] = []
    for c in sorted(eligible, key=lambda c: (c.rank or 0, c.name)):
        if (pick is not None and c.name == pick.name) or not better(c):
            continue
        if scope == "downloaded" and not c.downloaded:
            reason = "is not downloaded"
        elif pick is not None and not fallback and not in_family(c, goal) and _fits_somehow(c, allowed, swa_full):
            other: Goal = "everyday" if goal == "hard-code" else "hard-code"
            reason = f"is {c.kind()}: Auto fit keeps it for the {GOAL_NAME[other]} goal"
        elif pick is not None and not fallback and not in_family(c, goal):
            reason = why_not(c, TIERS[-1], allowed, swa_full)       # it fits no pass: say that, not the family
        else:
            failed = tier if pick is not None and not (fallback and in_family(c, goal)) else len(TIERS) - 1
            reason = why_not(c, TIERS[failed], allowed, swa_full)
        out.append(Rejection(c.name, c.rank or 0, reason))
    return out


def _fits_somehow(c: Candidate, allowed: float, swa_full: bool) -> bool:
    """Does the candidate fit any pass (its family aside)?"""
    return any(plan_for(c, t, allowed, swa_full) for t in TIERS)


def best_downloaded(fit: AutoFit, candidates: Sequence[Candidate]) -> AutoFit:
    """What a start can use when the pick is not downloaded: the same goal, downloaded only."""
    if fit.pick is None or fit.pick.downloaded:
        return fit
    return auto_fit(candidates, fit.budget, fit.goal, "downloaded")

"""Auto fit: the best stock model for this Mac and a goal, with the reasons every
better-ranked model was passed over.

Quality is the catalogue rank (1 = best: parameters and density first, then quantization).
CARL prioritises speed, so the goal decides the family first:
  everyday   the MoE builds (35B-A3B: fast, usually sufficient); a dense build only when
             no MoE build fits (said so in the result)
  hard-code  the dense builds (27B: better at code and hard tasks, slower); MoE as the fallback
Only ranked stock models are candidates: abliterated models are picked by hand. A custom
model takes part only when the user's card opts it in (auto_fit: a rank, an arch, stock):
its rank is the user's word, not measured, so it never joins by default.

Within the goal's family the passes are, in order (the first pass any candidate meets wins,
the best rank within it):
  1. two 96K windows (main session + a coder subagent)
  2. one 96K window
  3. one window, the largest that fits, at least 32K
The memory allowed is the smaller of the GPU limit and RAM minus the reserve for macOS and
apps (more with the VM up), so a pick leaves the Mac usable. Pure: shapes, sizes and the
limits come in as values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from .fit import max_ctx, need_bytes, window_label
from .gguf import GIB, OVERHEAD, ModelShape, ctx_train
from .models import CTX_FLOOR
from .types import ModelInfo

Goal = Literal["everyday", "hard-code"]
Scope = Literal["catalogue", "downloaded"]
GOALS: Tuple[Goal, ...] = ("everyday", "hard-code")
SCOPES: Tuple[Scope, ...] = ("catalogue", "downloaded")
GOAL_ARCH: Dict[Goal, str] = {"everyday": "moe", "hard-code": "dense"}
GOAL_TEXT: Dict[Goal, str] = {"everyday": "everyday (MoE first: fast)",
                               "hard-code": "hard code (dense first: better, slower)"}
SCOPE_TEXT: Dict[Scope, str] = {"catalogue": "all catalogue models", "downloaded": "downloaded models only"}
ARCH_TEXT: Dict[str, str] = {"moe": "MoE", "dense": "dense"}
MIN_WINDOW = 32768                  # below this a window is not worth starting


def as_goal(v: object) -> Goal:
    """A goal from config or the command line (anything else: everyday)."""
    return "hard-code" if v == "hard-code" else "everyday"


def as_scope(v: object) -> Scope:
    """A scope from config or the command line (anything else: catalogue)."""
    return "downloaded" if v == "downloaded" else "catalogue"


def gib(n: float) -> str:
    return f"{n / GIB:.1f} GiB"


@dataclass(frozen=True)
class Candidate:
    """A model as auto fit sees it. shape None: its GGUF header could not be read."""
    name: str
    arch: str
    rank: Optional[int]
    abliterated: bool
    downloaded: bool
    weights: int
    shape: Optional[ModelShape]
    kv: str = "q4_0"
    opted_in: bool = True           # a custom model: its card's auto_fit (catalogue models: always)

    @property
    def eligible(self) -> bool:
        """A ranked stock model. Abliterated models are only ever picked by hand; a custom
        model only when its card opts it in."""
        return self.rank is not None and not self.abliterated and self.opted_in


def candidate(m: ModelInfo, shape: Optional[ModelShape]) -> Candidate:
    """A model record (catalogue or custom) and its header shape as a candidate."""
    rank = m.get("rank")
    kv = (m.get("tune") or {}).get("kv", "q4_0")
    return Candidate(name=m.get("name", ""), arch=str(m.get("arch") or "").lower(),
                     rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                     abliterated=bool(m.get("abliterated")), downloaded=m.get("status") == "downloaded",
                     weights=int(m.get("bytes", 0)), shape=shape, kv=kv if isinstance(kv, str) else "q4_0",
                     opted_in=not m.get("custom") or m.get("auto_fit") is True)


@dataclass(frozen=True)
class Budget:
    """What a model may use on this Mac: the GPU limit, and RAM less the reserve for macOS
    and apps (ram 0 = unknown: the GPU limit alone)."""
    gpu_limit: float
    ram: float
    reserve: float
    vm_up: bool = False             # VMware's network is up (the reserve is larger)

    @property
    def allowed(self) -> float:
        if self.ram <= 0:
            return self.gpu_limit
        return max(0.0, min(self.gpu_limit, self.ram - self.reserve))

    def describe(self) -> str:
        if self.ram > 0 and self.ram - self.reserve < self.gpu_limit:
            return (f"{gib(self.allowed)} ({gib(self.ram)} RAM less {gib(self.reserve)} kept for macOS and apps; "
                    f"GPU limit {gib(self.gpu_limit)})")
        return f"{gib(self.allowed)} (the GPU limit)"


@dataclass(frozen=True)
class Plan:
    """How a model runs: slots of ctx tokens each at a KV cache type, and the memory it needs."""
    ctx: int
    slots: int
    kv: str
    need: float

    def label(self) -> str:
        return f"{self.slots} × {window_label(self.ctx)} {self.kv}"


@dataclass(frozen=True)
class Tier:
    """One pass: slots × ctx per slot; ctx None = the largest window that fits (>= MIN_WINDOW)."""
    slots: int
    ctx: Optional[int]

    def label(self) -> str:
        return f"{self.slots} × {window_label(self.ctx)}" if self.ctx else f"1 × {window_label(MIN_WINDOW)}"


TIERS: Tuple[Tier, ...] = (Tier(2, CTX_FLOOR), Tier(1, CTX_FLOOR), Tier(1, None))


def plan_for(c: Candidate, tier: Tier, allowed: float) -> Optional[Plan]:
    """The plan of a candidate in one pass, None when it doesn't fit (or its shape is unknown)."""
    if c.shape is None:
        return None
    if tier.ctx is None:
        ctx = min(max_ctx(c.shape, c.weights, allowed, tier.slots, c.kv), CTX_FLOOR)
        if ctx < MIN_WINDOW:
            return None
    else:
        ctx = tier.ctx
        if ctx > ctx_train(c.shape):
            return None
    need = need_bytes(c.shape, c.weights, ctx, tier.slots, c.kv)
    return Plan(ctx, tier.slots, c.kv, need) if need <= allowed else None


def why_not(c: Candidate, tier: Tier, allowed: float) -> str:
    """Why a candidate fails a pass, with the numbers."""
    if c.shape is None:
        return "size unknown: its GGUF header could not be read (offline?)"
    alone = c.weights + c.shape["rs_bytes"] + OVERHEAD
    if alone > allowed:
        return f"the weights and buffers alone need {gib(alone)}, this Mac allows {gib(allowed)}"
    if tier.ctx is None:
        mx = max_ctx(c.shape, c.weights, allowed, 1, c.kv)
        return f"the largest window that fits is {window_label(mx)} (less than {window_label(MIN_WINDOW)})"
    need = need_bytes(c.shape, c.weights, tier.ctx, tier.slots, c.kv)
    return f"needs {gib(need)} for {tier.label()}, this Mac allows {gib(allowed)}"


@dataclass(frozen=True)
class Rejection:
    """A better-ranked candidate auto fit passed over, and why."""
    name: str
    rank: int
    reason: str

    def line(self) -> str:
        return f"{self.name} (rank {self.rank}): {self.reason}"


@dataclass(frozen=True)
class AutoFit:
    """Auto fit's answer: the pick (None when nothing fits), how it runs, and why every
    better-ranked candidate was passed over. fallback: no model of the goal's family fits,
    the pick is of the other family. tier: the pass the pick met (0-2)."""
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
        """One sentence: why the pick (or that nothing fits)."""
        if not self.pick or not self.plan:
            return (f"no ranked stock model fits this Mac ({SCOPE_TEXT[self.scope]}); "
                    f"it allows {self.budget.describe()}")
        arch = ARCH_TEXT.get(self.pick.arch, self.pick.arch or "?")
        holds = ("two 96K windows (main session + a subagent)" if self.tier == 0 else
                 "one 96K window (two don't fit)" if self.tier == 1 else
                 f"a {window_label(self.plan.ctx)} window, the largest that fits (no build holds 96K)")
        here = "downloaded " if self.scope == "downloaded" else ""
        lead = (f"no {here}{ARCH_TEXT[GOAL_ARCH[self.goal]]} build fits, so the best {arch} build that does: "
                if self.fallback else f"the best-ranked stock {arch} build that holds ")
        tail = f"it holds {holds}" if self.fallback else holds
        return (f"{lead}{tail}; needs {gib(self.plan.need)} of {self.budget.describe()}")

    def summary(self) -> str:
        """The pick in one line: name, plan, goal and scope."""
        head = f"{self.pick.name}, {self.plan.label()}" if self.pick and self.plan else "nothing fits"
        return f"{head}  [{GOAL_TEXT[self.goal]} · {SCOPE_TEXT[self.scope]}]"


def _order(cands: Sequence[Candidate], arch: str) -> List[Candidate]:
    return sorted((c for c in cands if c.arch == arch), key=lambda c: (c.rank or 0, c.name))


def auto_fit(candidates: Sequence[Candidate], budget: Budget, goal: Goal = "everyday",
             scope: Scope = "catalogue") -> AutoFit:
    """The best ranked stock candidate for the goal that fits the budget (module docstring)."""
    allowed = budget.allowed
    eligible = [c for c in candidates if c.eligible]
    in_scope = [c for c in eligible if scope == "catalogue" or c.downloaded]
    first = GOAL_ARCH[goal]
    other = next(a for a in GOAL_ARCH.values() if a != first)
    groups = [_order(in_scope, first), _order(in_scope, other)]
    for g, group in enumerate(groups):
        for t, tier in enumerate(TIERS):
            for c in group:
                plan = plan_for(c, tier, allowed)
                if plan:
                    return AutoFit(goal, scope, budget, c, plan, t, g == 1,
                                   tuple(_rejections(eligible, c, t, g == 1, scope, first, allowed)))
    return AutoFit(goal, scope, budget, None, None, len(TIERS) - 1, False,
                   tuple(_rejections(eligible, None, len(TIERS) - 1, False, scope, first, allowed)))


def _rejections(eligible: Sequence[Candidate], pick: Optional[Candidate], tier: int, fallback: bool, scope: Scope,
                first: str, allowed: float) -> List[Rejection]:
    """Every eligible candidate ranked above the pick (and, after a fallback, the whole goal
    family), best rank first, with the reason it lost."""
    def better(c: Candidate) -> bool:
        if pick is None or (fallback and c.arch == first):
            return True
        return (c.rank or 0, c.name) < (pick.rank or 0, pick.name)

    out: List[Rejection] = []
    for c in sorted(eligible, key=lambda c: (c.rank or 0, c.name)):
        if (pick is not None and c.name == pick.name) or not better(c):
            continue
        if scope == "downloaded" and not c.downloaded:
            reason = "not downloaded"
        elif pick is not None and not fallback and c.arch != pick.arch:
            reason = (f"{ARCH_TEXT.get(c.arch, c.arch or '?')}: for the "
                      f"{'everyday goal (faster)' if c.arch == GOAL_ARCH['everyday'] else 'hard-code goal (slower)'}")
        else:
            failed = tier if pick is not None and not (fallback and c.arch == first) else len(TIERS) - 1
            reason = why_not(c, TIERS[failed], allowed)
        out.append(Rejection(c.name, c.rank or 0, reason))
    return out


def best_downloaded(fit: AutoFit, candidates: Sequence[Candidate]) -> AutoFit:
    """What a start can use when the pick is not downloaded: the same goal, downloaded only."""
    if fit.pick is None or fit.pick.downloaded:
        return fit
    return auto_fit(candidates, fit.budget, fit.goal, "downloaded")

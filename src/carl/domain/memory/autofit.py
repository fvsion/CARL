"""Auto fit: the best stock model for this Mac and a goal, with the reasons every
better-ranked model was passed over.

Quality is the catalogue rank (1 = best: published benchmarks and CARL's code test first, then the quantization).
CARL prioritises speed, so the goal decides the family first:
  everyday   the fast builds: MoE (35B-A3B: fast, usually sufficient); another build only when
             no MoE build fits (said so in the result: on a 16 GB Mac the Gemma 4 12B). The small
             dense builds the catalogue marks fast (Gemma 4 E4B) are the last resort (W1.6, below)
  hard-code  the dense builds (27B: better at code and hard tasks, slower); MoE as the fallback, also when the only
             dense models that fit rank below the best MoE model that fits (W1.6: a PC, the A3B Q4 with its experts in
             the RAM, not the Qwen 9B at 1 x 64K)
Only ranked stock models are candidates: abliterated models are picked by hand. A custom
model takes part only when the user's card opts it in (auto_fit: a rank, an arch, stock):
its rank is the user's word, not measured, so it never joins by default.

Within the goal's family the passes are, in order, on every Mac (user, 2026-10-09: "every Mac with
48K floor"; the first pass any candidate meets wins, the best rank within it):
  2 x 96K, 2 x 64K, 2 x 48K (the main session + a coder subagent), then 1 x 96K, 1 x 64K, 1 x 48K
Nothing below 48K. On NVIDIA (CUDA) starts the windows begin at 128K (A53, the user, 2026-10-29: "Agreed on 128k
proposal"; WCTX measured it on the 8 GB RTX 2070 SUPER): 2 x 128K, 2 x 96K, 2 x 64K, 2 x 48K, then 1 x 128K, 1 x 96K,
1 x 64K, 1 x 48K. Each pass is checked against the free memory as it is, so a busy desktop costs the pass and nothing
else. A Mac keeps 96K at the top (128K is left to carl tune to find on a Mac with a large RAM: A50). The ladder is the
machine's (Budget.tiers), the one list every reader takes.
A model whose catalogue window is smaller (Saluki: 48K, sized for 16 GB Macs) runs each pass
at its own window: its full window counts as the first pass. The hard code pick on a 16 GB Mac is the Gemma 4 12B
(user, 2026-10-13: "12b is clearly the better pick for default"; Saluki's rank is below it). In each pass a model with MTP that fits only without it drops MTP (n-gram stays)
before Auto fit drops a slot or the window, and says so.
On discrete GPUs (Windows, Linux) a MoE model that does not fit a pass whole tries it with some of its experts in the
RAM (W6.7, offload.py: the smallest --n-cpu-moe N whose GPU side splits over the devices and whose RAM side fits the RAM
less the reserve). The pass comes first, then the rank (W1.6, the user, 2026-11-13: "We want ngram still so batch 1024
if we must. E4B should be consciously chosen"): in each pass each model of the family by rank, whole on the GPUs or
else with experts in the RAM, so the better model at the larger window wins. An experts-in-RAM setup is taken only
when it writes at least RAM_WRITE_FLOOR and reads at least RAM_READ_FLOOR tok/s there (RamSpeed: this computer's
Auto-tune record, else the catalogue's ram_speed); n-gram stays on. A dense model is never offloaded, and a Mac never
offloads.
The fast small dense builds (the catalogue's fast mark: the Gemma 4 E4B) are the last resort (W1.6, the user,
2026-11-14: "Install E4B, you over-specified what i meant"): Auto fit takes the best one that fits only when no other model
of its quality rank or better fits any pass (a tie goes to the other model), and then it is the pick like any other. Otherwise the result names it as the fast option
(AutoFit.fast), and a person may choose it.
The memory allowed is the smaller of the GPU limit and RAM minus the reserve for macOS and
apps (more with the VM up), so a pick leaves the Mac usable. Pure: shapes, sizes and the
limits come in as values.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from .budget import cap
from .calibration import METAL, Calibration
from .fit import NO_SPEC, UBATCH, Spec, mtp_spec, need_bytes, ub_choice, window_label
from .gguf import GIB, ModelShape, ctx_train
from .offload import is_moe, plan_of as offload_plan, ram_spec
from .split import Slot, calib_of
from ..catalog.inventory import draft_bytes, spec_draft_bytes
from ..server.ub_order import ub_pick
from ..shared.constants import DEFAULT_CTX
from ..shared.platform_words import MACOS, PlatformWords
from ..shared.types import ModelInfo

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
TOP_WINDOW = DEFAULT_CTX            # the first pass's window: a catalogue window below it is the model's own


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


RAM_WRITE_FLOOR = 15.0      # tok/s an experts-in-RAM pick writes at least (W1.6, the user, 2026-11-13)
RAM_READ_FLOOR = 300.0      # tok/s it reads a prompt at least (a PC measured 21 to 26 and 530 to 860)


@dataclass(frozen=True)
class RamSpeed:
    """A MoE model's speed with experts in the RAM: write and read in tok/s, and where they come from ("Auto-tune":
    this computer's record; "catalogue": the catalogue's ram_speed, measured on another PC; "": not known, 0, 0)."""
    write: float = 0.0
    read: float = 0.0
    source: str = ""

    @property
    def known(self) -> bool:
        return self.write > 0 and self.read > 0

    @property
    def fast(self) -> bool:
        """Fast enough for Auto fit to take it with experts in the RAM (the floor)."""
        return self.known and self.write >= RAM_WRITE_FLOOR and self.read >= RAM_READ_FLOOR

    def words(self) -> str:
        """writes about 21 tok/s and reads about 530 (the catalogue's figure)"""
        if not self.known:
            return "has no speed figure with experts in the RAM (Auto-tune measures it)"
        whose = "this computer's Auto-tune record" if self.source == "Auto-tune" else "the catalogue's figure"
        return f"writes about {self.write:.0f} tok/s and reads about {self.read:.0f} ({whose})"


def ram_speed_of(m: ModelInfo) -> RamSpeed:
    """The speed with experts in the RAM (RamSpeed): this computer's Auto-tune record (the best write score of its
    speculation, with experts in the RAM when it measured that, and its first prompt read), else the catalogue's
    ram_speed ({"write": tok/s, "read": tok/s}), else not known."""
    local = ((m.get("local") or {}).get("tune") or {})
    res = local.get("results") or {}
    spec = res.get("speculation_ram") or res.get("speculation") or {}
    writes = [float((r or {}).get("score") or 0) for r in spec.values()] if isinstance(spec, dict) else []
    reads = res.get("prompt_read") or []
    read = float(reads[0][1]) if reads and isinstance(reads[0], (list, tuple)) and len(reads[0]) > 1 else 0.0
    if writes and max(writes) > 0 and read > 0:
        return RamSpeed(max(writes), read, "Auto-tune")
    cat = m.get("ram_speed") or {}
    w, r = cat.get("write"), cat.get("read")
    if isinstance(w, (int, float)) and isinstance(r, (int, float)) and not isinstance(w, bool) and w > 0 and r > 0:
        return RamSpeed(float(w), float(r), "catalogue")
    return RamSpeed()


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
    fast: bool = False              # a small fast dense build (the E4B): the last resort, else the fast option (W1.6)
    spec: Spec = NO_SPEC            # its speculation: this Mac's Auto-tune result, else the catalogue's (MTP only with
                                    # a head or a drafter)
    window: int = 0                 # the catalogue's window when it is below the passes' (0: the passes')
    mtp_modes: Tuple[Spec, ...] = ()   # the other MTP modes measured, best score first: tried before MTP goes
    calib: Calibration = METAL      # the constants of the backend it runs on (calibration.py; a Mac: its own formula)
    cpu_over: str = ""              # a start on the CPU alone: why this IQ4_NL or Q4_0 comes before the K-quant of its
                                    # model and size (quants.cpu_quant_note; A72); "" for any other candidate
    ram_speed: "RamSpeed" = field(default_factory=lambda: RamSpeed())   # its speed with experts in the RAM (W1.6)

    @property
    def order(self) -> Tuple[int, bool, str]:
        """Auto fit's order: the quality rank, then (the same rank) the quantization the CPU prefers, then the name."""
        return self.rank or 0, not self.cpu_over, self.name

    def kind(self) -> str:
        """MoE, small dense or dense: what the result calls the build."""
        return "small dense" if self.fast and self.arch != "moe" else ARCH_TEXT.get(self.arch, self.arch or "?")

    @property
    def eligible(self) -> bool:
        """A ranked stock model. Abliterated models are only ever picked by hand; a custom
        model only when its card opts it in."""
        return self.rank is not None and not self.abliterated and self.opted_in


def measured_mtp(m: ModelInfo, shape: Optional[ModelShape]) -> Tuple[Spec, ...]:
    """The MTP modes measured for a model, best score first (Phase 23.5.1.8): this Mac's Auto-tune result
    (models.json models.NAME.tune.results.speculation, "spec:n" keys), else the catalogue's table (tune.speeds)."""
    rows: List[Tuple[float, str, int]] = []
    local = (((m.get("local") or {}).get("tune") or {}).get("results") or {}).get("speculation") or {}
    for key, r in local.items():
        spec, _, n = str(key).partition(":")
        rows.append((float((r or {}).get("score") or 0), spec, int(n) if n.isdigit() else 1))
    if not rows:
        for r in (m.get("tune") or {}).get("speeds") or []:
            n = r.get("n", 1)
            rows.append((float(r.get("score") or 0), str(r.get("spec") or "none"),
                         n if isinstance(n, int) and not isinstance(n, bool) else 1))
    out: List[Spec] = []
    for _, spec, n in sorted(rows, key=lambda x: -x[0]):
        s = mtp_spec(spec, n, shape, draft_bytes(m))
        if s.mtp and s not in out:
            out.append(s)
    return tuple(out)


def model_spec(m: ModelInfo, shape: Optional[ModelShape]) -> Spec:
    """The speculation a start of this model runs, for the estimate: this computer's Auto-tune result first
    (its spec and spec_n; kept, A51), else the catalogue's (tune.spec, tune.spec_n; not kept). MTP only with its
    head or its drafter (the drafter's weights count only while MTP runs). The one place the carl fit table and
    Auto fit take it from."""
    tune = m.get("tune") or {}
    own = ((m.get("local") or {}).get("tune") or {}).get("settings") or {}
    if own.get("spec"):                  # this Mac's Auto-tune result first (user, 2026-10-12)
        tune = {**tune, "spec": own.get("spec"), "spec_n": own.get("spec_n", 1)}
    n = tune.get("spec_n", 1)
    spec = str(tune.get("spec") or "none")
    return mtp_spec(spec, n if isinstance(n, int) else 1, shape, spec_draft_bytes(m, spec),
                    bool(own.get("spec")))              # this computer's Auto-tune result: kept (A51)


def candidate(m: ModelInfo, shape: Optional[ModelShape]) -> Candidate:
    """A model record (catalogue or custom) and its header shape as a candidate: its speculation from
    model_spec, MTP with its head or its drafter."""
    rank = m.get("rank")
    tune = m.get("tune") or {}
    kv = tune.get("kv", "q4_0")
    spec = model_spec(m, shape)
    ctx = tune.get("ctx")
    window = ctx if isinstance(ctx, int) and not isinstance(ctx, bool) and MIN_WINDOW <= ctx < TOP_WINDOW else 0
    return Candidate(name=m.get("name", ""), arch=str(m.get("arch") or "").lower(),
                     rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                     abliterated=bool(m.get("abliterated")), downloaded=m.get("status") == "downloaded",
                     weights=int(m.get("bytes", 0)), shape=shape, kv=kv if isinstance(kv, str) else "q4_0",
                     opted_in=not m.get("custom") or m.get("auto_fit") is True, fast=m.get("fast") is True,
                     spec=spec, window=window, mtp_modes=measured_mtp(m, shape), ram_speed=ram_speed_of(m))


@dataclass(frozen=True)
class Budget:
    """What a model may use on this Mac: the GPU limit, and RAM less the reserve for macOS
    and apps (ram 0 = unknown: the GPU limit alone). unified False: a discrete GPU (Windows, Linux), whose
    VRAM is apart from the RAM: gpu_limit is its free VRAM (domain/gpu.py) and the RAM does not cap it. A PC whose
    GPU shares the RAM (an APU, WAPU) is unified with dedicated > 0: dedicated is the part of gpu_limit that is
    the GPUs' own memory (the carve-out, a discrete GPU's VRAM), which the RAM does not cap; the rest is the RAM's
    and the smaller of it and the RAM less the reserve counts. devices: the discrete GPUs a start splits over
    (split.py), for the passes with MoE experts in the RAM (W6.7); () on a Mac."""
    gpu_limit: float
    ram: float
    reserve: float
    vm_up: bool = False             # VMware's network is up (the reserve is larger)
    swa: str = "auto"               # cache.swa: sliding-window layers at full length only with "full"
    unified: bool = True            # the GPU and the CPU share the RAM (a Mac)
    words: PlatformWords = MACOS    # the platform words of its texts (the composition's)
    dedicated: float = 0.0          # the part of gpu_limit that is not the RAM's (0 on a Mac)
    devices: Tuple[Slot, ...] = ()  # the discrete GPUs (llama.cpp's names and rooms), for experts in the RAM
    less: float = 0.0               # the room taken off: llama-server's own fit needs that much more (A67, less_room)

    def less_room(self, extra: float) -> "Budget":
        """The budget with `extra` bytes less on every device and in what a model may use: Auto fit chooses again when
        llama-server's own fit of its pick needs more than CARL's estimate (A67; the start's --gpu-extra)."""
        return replace(self, less=self.less + extra,
                       devices=tuple(replace(d, room=int(d.room - extra)) for d in self.devices))

    @property
    def offloads(self) -> bool:
        """Can a MoE model keep some of its experts in the RAM (discrete GPUs that CARL can name)?"""
        return not self.unified and bool(self.devices)

    @property
    def tiers(self) -> Tuple["Tier", ...]:
        """Auto fit's order on this machine (A53): the CUDA ladder when the discrete GPUs it names are NVIDIA's, else
        A45's (a Mac, the CPU)."""
        return tiers_for(calib_of(self.devices))

    @property
    def swa_full(self) -> bool:
        """Plan a sliding-window model's cache at full length (cache.swa full); auto and window plan
        the window (auto takes the full cache at the start only when it fits: fit.swa_plan)."""
        return self.swa == "full"

    @property
    def allowed(self) -> float:
        return max(0.0, self._allowed() - self.less)

    @property
    def allowed_whole(self) -> float:
        """What a model may use before the room taken off (less): the room a measured pick is judged against."""
        return self._allowed()

    def _allowed(self) -> float:
        if self.ram <= 0 or not self.unified:
            return self.gpu_limit
        if self.dedicated:
            return cap(self.gpu_limit, self.dedicated, self.ram, self.reserve)
        return max(0.0, min(self.gpu_limit, self.ram - self.reserve))

    def describe(self) -> str:
        """What a model may use, and the reason, as a short phrase: 25.0 GiB (the GPU memory limit)."""
        if not self.unified:
            return f"{gib(self.allowed)} (the free GPU memory)"
        if self.dedicated:
            return self._describe_shared()
        if self.ram > 0 and self.ram - self.reserve < self.gpu_limit:
            return (f"{gib(self.allowed)} (the RAM, {gib(self.ram)}, minus {gib(self.reserve)} kept free for "
                    f"{self.words.os_and_apps})")
        return f"{gib(self.allowed)} (the GPU memory limit)"

    def explain(self) -> str:
        """What a model may use on this Mac, in full sentences (the CLI's carl fit)."""
        if not self.unified:
            return (f"A model can use {gib(self.allowed)}: the free memory of the GPU, less a part that CARL keeps "
                    f"free. The GPU has its own memory, apart from the RAM.")
        if self.ram <= 0:
            return f"A model can use {gib(self.allowed)}: the GPU memory limit."
        if self.dedicated:
            return f"A model can use {gib(self.allowed)}: {self._shared_how()}."
        free = self.ram - self.reserve
        kept = f"the RAM ({gib(self.ram)}) minus the memory kept free for {self.words.os_and_apps} ({gib(self.reserve)})"
        if free < self.gpu_limit:
            return (f"A model can use {gib(self.allowed)}: {kept}. "
                    f"This is less than the GPU memory limit ({gib(self.gpu_limit)}).")
        return (f"A model can use {gib(self.allowed)}: the GPU memory limit. "
                f"This is less than {kept}, which is {gib(free)}.")

    def _shared_how(self) -> str:
        """What a GPU that shares the RAM gives a model: its own memory, and the smaller of the shared part and the
        RAM less the reserve."""
        shared, free = self.gpu_limit - self.dedicated, max(self.ram - self.reserve, 0.0)
        left = (f"the RAM ({gib(self.ram)}) less {gib(self.reserve)} kept free for {self.words.os_and_apps} leaves "
                f"{gib(free)}")
        if free < shared:
            return (f"{gib(self.dedicated)} of the GPU's own memory and {gib(free)} of the RAM. The GPU may map "
                    f"{gib(shared)}, but {left}")
        return (f"{gib(self.dedicated)} of the GPU's own memory and the {gib(shared)} of the RAM that the GPU may map. "
                f"With {left}, that fits")

    def _describe_shared(self) -> str:
        shared, free = self.gpu_limit - self.dedicated, self.ram - self.reserve
        if free < shared:
            return (f"{gib(self.allowed)} (the GPU's own memory, {gib(self.dedicated)}, and the RAM, {gib(self.ram)}, "
                    f"minus {gib(self.reserve)} kept free for {self.words.os_and_apps})")
        return f"{gib(self.allowed)} (the GPU's own memory and the RAM it may map)"


# Why a start with experts in the RAM runs n-gram (A51): one sentence without the last full stop
RAM_MTP_NOTE = ("MTP is off while experts are in the RAM: it did not pay there, and the draft context takes GPU "
                "memory. Auto-tune measures it for this model")


@dataclass(frozen=True)
class Plan:
    """How a model runs: slots of ctx tokens each at a KV cache type, its speculation, and the memory
    it needs. dropped: the model's MTP does not fit with these slots and window, so n-gram only. cpu_moe: the layers
    whose MoE experts stay in the RAM (--n-cpu-moe; 0: every expert on the GPUs; need is then the GPUs' side).
    ram_off: MTP is off by default because experts are in the RAM."""
    ctx: int
    slots: int
    kv: str
    need: float
    spec: Spec = NO_SPEC
    dropped: bool = False
    cpu_moe: int = 0
    ram_off: bool = False       # MTP is off because experts are in the RAM (offload.ram_spec, A51), not for lack of room
    ub: int = UBATCH            # the -ub it is planned at (at_ub: a pick with experts in the RAM at its start's, W1.6)

    def ram_text(self) -> str:
        """", the experts of 12 layers in the RAM" ("" with every expert on the GPUs)."""
        return f", the experts of {self.cpu_moe} layer{'s' if self.cpu_moe != 1 else ''} in the RAM" if self.cpu_moe \
            else ""

    def label(self) -> str:
        """The plan in short: 2 × 96K tokens, q4."""
        return f"{self.slots} × {window_label(self.ctx)} tokens, {KV_TEXT.get(self.kv, self.kv)}{self.ram_text()}"

    def describe(self) -> str:
        """The plan in words: 2 slots × 96K tokens (q4)."""
        return setup_text(self.slots, self.ctx, self.kv) + self.ram_text()

    def spec_note(self, words: PlatformWords = MACOS) -> str:
        """Why the speculation is n-gram only, one sentence without the last full stop ("" when MTP fits)."""
        if self.ram_off:
            return RAM_MTP_NOTE
        if not self.dropped:
            return ""
        return (f"MTP does not fit with {setup_text(self.slots, self.ctx)} on {words.machine}, so the speculation is "
                f"n-gram only")


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


# Auto fit's windows, largest first, in K = 1024 tokens: two slots first, from the top down to the 48K floor
# (user, 2026-10-09). LADDER_K is every backend's (A45); LADDER_K_BY_BACKEND the ones that start higher: CUDA at 128K
# (A53, WCTX table 7a). The one list of rungs (R14).
LADDER_K: Tuple[int, ...] = (96, 64, 48)
LADDER_K_BY_BACKEND: Dict[str, Tuple[int, ...]] = {"cuda": (128, 96, 64, 48)}


def tiers_of(windows: Sequence[int]) -> Tuple[Tier, ...]:
    """The passes for these windows (K): two slots at each window, then one slot at each."""
    return tuple(Tier(n, k * 1024) for n in (2, 1) for k in windows)


TIERS: Tuple[Tier, ...] = tiers_of(LADDER_K)                  # every backend but CUDA (A45)
TIERS_CUDA: Tuple[Tier, ...] = tiers_of(LADDER_K_BY_BACKEND["cuda"])


def tiers_for(calib: Calibration) -> Tuple[Tier, ...]:
    """Auto fit's passes for a backend (its calibration): CUDA's start at 128K, the others' at 96K."""
    return tiers_of(LADDER_K_BY_BACKEND.get(calib.backend, LADDER_K))


def window_cap(ctx: int, tiers: Sequence[Tier]) -> int:
    """The window no pass goes above for a model whose window is ctx: the model's own when it is below the default
    window (a catalogue window sized for smaller Macs), else the top of this ladder (96K, the default window, is the
    top of every ladder but the CUDA one, which starts at 128K)."""
    return ctx if ctx < TOP_WINDOW else max(ctx, tiers[0].ctx)


def ladder_words(tiers: Sequence[Tier], who: bool = True) -> str:
    """The order in words, for the texts that tell it: 2 slots × 96K, 64K or 48K tokens (the main session and a
    subagent), then 1 slot × 96K, 64K or 48K tokens (who False: without the bracket)."""
    def one(slots: int) -> str:
        ks = [window_label(t.ctx) for t in tiers if t.slots == slots]
        return f"{slots} slot{'s' if slots != 1 else ''} × {', '.join(ks[:-1])} or {ks[-1]} tokens"
    return f"{one(2)}{' (the main session and a subagent)' if who else ''}, then {one(1)}"


def own_tier(c: Candidate, tier: Tier) -> Tier:
    """The pass as the candidate runs it: at its own window when the catalogue's is smaller."""
    return Tier(tier.slots, c.window) if c.window and c.window < tier.ctx else tier


def setups(c: Candidate) -> Tuple[Tuple[Spec, bool], ...]:
    """The speculation a candidate tries in each pass (the agreed order, 2026-10-12: MTP, then n-gram, then the window,
    then one slot): its own; the other MTP modes measured, best first (another guess count needs other memory); then
    without MTP (n-gram stays)."""
    if not c.spec.mtp:
        return ((c.spec, False),)
    others = tuple((s, False) for s in c.mtp_modes if s != c.spec)
    return ((c.spec, False),) + others + ((c.spec.without_mtp(), True),)


def plan_for(c: Candidate, tier: Tier, allowed: float, swa_full: bool = True, ub: int = UBATCH) -> Optional[Plan]:
    """The plan of a candidate in one pass, None when it doesn't fit (or its shape is unknown). MTP
    goes before the pass does: a model that fits only without it runs n-gram (Plan.dropped)."""
    tier = own_tier(c, tier)
    if c.shape is None or tier.ctx > ctx_train(c.shape):
        return None
    for spec, dropped in setups(c):
        need = need_bytes(c.shape, c.weights, tier.ctx, tier.slots, c.kv, swa_full, spec, ub, c.calib)
        if need <= allowed:
            return Plan(tier.ctx, tier.slots, c.kv, need, spec, dropped, ub=ub)
    return None


def ram_plan_for(c: Candidate, tier: Tier, budget: Budget, ub: int = UBATCH) -> Optional[Plan]:
    """The plan of a MoE candidate in one pass with some of its experts in the RAM (offload.py), None when it does not
    fit that way either, when the budget has no discrete GPUs, or for a dense model. need: the GPUs' side."""
    tier = own_tier(c, tier)
    if not budget.offloads or c.shape is None or tier.ctx > ctx_train(c.shape):
        return None
    lean = ram_spec(c.spec)                 # any expert layer in the RAM: MTP off unless a person or Auto-tune chose it
    off_by_default = lean.mtp != c.spec.mtp
    for spec, dropped in (((lean, False),) if off_by_default else setups(c)):
        off = offload_plan(c.shape, c.weights, tier.ctx, tier.slots, c.kv, budget.swa_full, spec, ub, budget.devices,
                           budget.ram, budget.reserve, c.calib)
        if off is None:                     # a dense model: never in the RAM
            return None
        if off.fits:
            return Plan(tier.ctx, tier.slots, c.kv, off.split.need, spec, dropped, off.n, off_by_default, ub)
    return None


def setup_of(c: Candidate, tier: Tier, budget: Optional[Budget], floor: bool = True, ub: int = UBATCH,
             allowed: Optional[float] = None, swa_full: Optional[bool] = None) -> Optional[Plan]:
    """The plan of a candidate in one pass: whole on the GPUs, else (discrete GPUs, a MoE model) with some of its experts
    in the RAM; None when neither fits. floor: Auto fit's own pick takes the experts-in-RAM setup only when its speed
    there meets the floor (RamSpeed.fast); a model a person named (model_plan) does not. allowed, swa_full: the
    budget's unless given (no budget: both must be given)."""
    room = allowed if allowed is not None else budget.allowed if budget is not None else 0.0
    full = swa_full if swa_full is not None else budget.swa_full if budget is not None else True
    plan = plan_for(c, tier, room, full, ub)
    if plan or budget is None or not budget.offloads or (floor and not c.ram_speed.fast):
        return plan
    return ram_plan_for(c, tier, budget, ub)


def model_plan(c: Candidate, allowed: float, swa_full: bool = True, cap: Optional[int] = None,
               slots: Optional[int] = None, ub: int = UBATCH, budget: Optional[Budget] = None) -> Tuple[int, Optional[Plan]]:
    """Auto fit's order for one model (a start whose window is not the user's): (the pass, its plan), or
    (the last pass, None) when no pass fits. cap: the model's own window (no pass above it); slots: a
    number the user set (only its passes). budget: with discrete GPUs, a pass the model does not fit whole is tried with
    experts in the RAM before the next pass (no speed floor: the model was named), and its ladder is the order
    (budget.tiers; no budget: A45's)."""
    tiers = budget.tiers if budget is not None else TIERS
    for t, tier in enumerate(tiers):
        if (cap is not None and tier.ctx > cap) or (slots is not None and tier.slots != slots):
            continue
        plan = setup_of(c, tier, budget, False, ub, allowed, swa_full)
        if plan:
            return t, plan
    return len(tiers) - 1, None


def order_note(name: str, tier: int, plan: Plan, cap: int, words: PlatformWords = MACOS,
               tiers: Sequence[Tier] = TIERS) -> str:
    """The start line for a start that Auto fit's order changed (a smaller window, or n-gram for MTP), one
    whole sentence; "" when it changed nothing. tiers: the machine's order (Budget.tiers)."""
    parts = []
    if plan.ctx < cap:
        prev = tiers[tier - 1] if tier else None
        why = f": {prev.label()} {'does' if prev.slots == 1 else 'do'} not fit {words.machine}" if prev else ""
        parts.append(f"{name} starts with {plan.describe()}, the first setup in Auto fit's order that fits{why}.")
    note = plan.spec_note(words)
    if note:
        parts.append(f"{note}.")
    return " ".join(parts)


def why_not(c: Candidate, tier: Tier, allowed: float, swa_full: bool = True, budget: Optional[Budget] = None) -> str:
    """Why a candidate fails a pass, with the numbers (the memory without MTP: the least it needs). budget: with discrete
    GPUs a MoE model's reason is its experts-in-RAM setup (_why_not_ram), not the whole-GPU need."""
    if c.shape is None:
        return "has an unknown size: CARL cannot read its GGUF header (possibly no network)"
    short = (f"does not fit: its trained context is {window_label(ctx_train(c.shape))} tokens, "
             f"less than {window_label(MIN_WINDOW)}")
    if budget is not None and budget.offloads and is_moe(c.shape):
        return short if ctx_train(c.shape) < MIN_WINDOW else _why_not_ram(c, tier, budget)
    spec = c.spec.without_mtp()
    alone = need_bytes(c.shape, c.weights, 0, 1, c.kv, swa_full, spec, calib=c.calib)
    if alone > allowed:
        return f"does not fit: the weights and buffers alone need {gib(alone)}, and a model can use {gib(allowed)}"
    tier = own_tier(c, tier)
    if ctx_train(c.shape) < MIN_WINDOW:
        return short
    need = need_bytes(c.shape, c.weights, tier.ctx, tier.slots, c.kv, swa_full, spec, calib=c.calib)
    return f"does not fit: {tier.label()} need {gib(need)}, and a model can use {gib(allowed)}"


def _why_not_ram(c: Candidate, tier: Tier, budget: Budget) -> str:
    """Why a MoE candidate on discrete GPUs fails a pass: it fits only with experts in the RAM and is below the speed
    floor (or its speed there is not known), or the GPU side or the RAM side does not fit even so."""
    assert c.shape is not None
    tier = own_tier(c, tier)
    if tier.ctx > ctx_train(c.shape):
        return f"does not fit: {tier.label()} are more than its trained context"
    off = offload_plan(c.shape, c.weights, tier.ctx, tier.slots, c.kv, budget.swa_full, ram_spec(c.spec), UBATCH,
                       budget.devices, budget.ram, budget.reserve, c.calib)
    if off is None:
        return f"does not fit: {tier.label()} need more than a model can use, {gib(budget.allowed)}"
    if off.fits:
        if not c.ram_speed.known:
            return (f"fits {tier.label()} only with {off.words()}, and its speed there is not known (Auto-tune "
                    f"measures it)")
        return (f"fits {tier.label()} only with {off.words()}, where it {c.ram_speed.words()}: below Auto fit's "
                f"floor of {RAM_WRITE_FLOOR:.0f} tok/s writing and {RAM_READ_FLOOR:.0f} reading")
    if not off.gpu_fits:
        return (f"does not fit: with {off.words()}, {tier.label()} need {gib(off.split.need)} on the GPU, and a model "
                f"can use {gib(off.split.room)}")
    return (f"does not fit: with {off.words()}, the RAM side needs {gib(off.ram)}, and the RAM has {gib(off.room)} for "
            f"a model")


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
    better-ranked candidate was passed over. fallback: no model of the goal's family fits, or (outranked, the goal
    hard code) the only ones that fit rank below the pick, which is of the other family. tier: the pass the pick met
    (an index of budget.tiers). fast: the fast option (a fast small dense build that fits, not the pick: another model of
    its rank or better fits) and its plan, None when none fits or it is the pick."""
    goal: Goal
    scope: Scope
    budget: Budget
    pick: Optional[Candidate]
    plan: Optional[Plan]
    tier: int
    fallback: bool
    rejected: Tuple[Rejection, ...]
    fast: Optional[Tuple[Candidate, Plan]] = None
    outranked: bool = False

    @property
    def name(self) -> Optional[str]:
        return self.pick.name if self.pick else None

    def because(self, need: Optional[float] = None) -> str:
        """Why the pick (or that nothing fits), in short sentences without the last full stop. need: the pick's need as
        llama-server's own fit measured it (carl fit's goal line, W1.6 wave 3: one figure in both), else the plan's."""
        if not self.pick or not self.plan:
            return (f"No stock model with a quality rank fits {self.budget.words.machine} ({SCOPE_TEXT[self.scope]}). "
                    f"A model can use {self.budget.describe()}")
        arch = self.pick.kind()
        here = "downloaded " if self.scope == "downloaded" else ""
        if self.pick.fast:                                      # the last resort (W1.6)
            lead, tail = f"No other {here}model of the fast option's rank or better fits, so Auto fit takes it. It holds ", ""
        elif self.outranked:
            lead, tail = (f"The only {here}{FAMILY_TEXT[self.goal]} models that fit rank below this one, so this is "
                          f"the best {arch} model that fits. It holds "), ""
        elif self.fallback:
            lead, tail = (f"No {here}{FAMILY_TEXT[self.goal]} model fits, so this is the best {arch} model that fits. "
                          f"It holds "), ""
        else:
            lead, tail = f"Of the stock {arch} models that hold ", ", this model has the best quality rank"
        note = self.plan.spec_note(self.budget.words)
        if self.pick.cpu_over:                                  # a start on the CPU alone chose the faster quantization
            note = f"{self.pick.cpu_over}" + (f". {note}" if note else "")
        t = Tier(self.plan.slots, self.plan.ctx)
        n = self.plan.need if need is None else need
        needs = (f"It needs {gib(n)}" if not self.plan.cpu_moe else
                 f"{t.label()} {'does' if t.slots == 1 else 'do'} not fit the GPUs whole, so the experts of "
                 f"{self.plan.cpu_moe} layers stay in the RAM, where it {self.pick.ram_speed.words()}. On the GPUs it "
                 f"needs {gib(n)}")
        return (f"{lead}{self.holds()}{tail}. {needs}, and a model can use "
                f"{self.budget.describe()}" + (f". {note}" if note else "") +
                (f". {self.fast_text()}" if self.fast else ""))

    def fast_text(self) -> str:
        """The fast option in one sentence without the last full stop ("" when there is none): For speed:
        gemma-4-e4b (small dense, quality rank 9) fits with 2 slots × 64K tokens (q4). Auto fit takes it only when no
        other model of its rank or better fits: choose it by name (carl config set llama.model gemma-4-e4b)."""
        if not self.fast:
            return ""
        c, plan = self.fast
        return (f"For speed: {c.name} ({c.kind()}, quality rank {c.rank}) fits with {plan.describe()}. Auto fit takes "
                f"it only when no other model of its rank or better fits: choose it by name "
                f"({self.budget.words.command} config set llama.model {c.name})")

    def holds(self) -> str:
        """The setup of the pick and why Auto fit took it, as a phrase: two slots of 64K tokens (the main
        session and a subagent; two slots of 96K tokens do not fit)."""
        tiers = self.budget.tiers
        t = Tier(self.plan.slots, self.plan.ctx) if self.plan else tiers[self.tier]
        if self.tier == 0:
            own = "; the window its catalogue entry is sized for" if t.ctx < tiers[0].ctx else ""
            return f"{t.words()} (the main session and a subagent{own})"
        prev = own_tier(self.pick, tiers[self.tier - 1]) if self.pick else tiers[self.tier - 1]
        both = "the main session and a subagent; " if t.slots == 2 else ""
        return f"{t.words()} ({both}{prev.words()} {'does' if prev.slots == 1 else 'do'} not fit)"

    def order_text(self) -> str:
        """Which setup Auto fit chose and why, in whole sentences without the last full stop (the
        Server panel, carl fit)."""
        if not self.pick or not self.plan:
            return ""
        t = Tier(self.plan.slots, self.plan.ctx)
        prev = own_tier(self.pick, self.budget.tiers[self.tier - 1]) if self.tier else None
        text = f"Auto fit takes the first setup in its order that fits: {t.label()}{self.plan.ram_text()}" + (
            f", because {prev.label()} {'does' if prev.slots == 1 else 'do'} not fit" if prev else "")
        note = self.plan.spec_note(self.budget.words)
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
    return sorted((c for c in cands if in_family(c, goal) == keep), key=lambda c: c.order)


def fast_option(cands: Sequence[Candidate], budget: Budget) -> Optional[Tuple[Candidate, Plan]]:
    """The best-ranked fast small dense build that fits a pass whole (the first pass it fits), None when none does."""
    for c in sorted((c for c in cands if c.fast), key=lambda c: c.order):
        for tier in budget.tiers:
            plan = plan_for(c, tier, budget.allowed, budget.swa_full)
            if plan:
                return c, plan
    return None


def auto_fit(candidates: Sequence[Candidate], budget: Budget, goal: Goal = "everyday",
             scope: Scope = "catalogue") -> AutoFit:
    """The best ranked stock candidate for the goal that fits the budget (module docstring): the goal's family first,
    then the other (for hard code also when the only dense fits rank below the other family's pick); in each, pass by
    pass, each candidate by rank, whole or (setup_of) with experts in the RAM at the speed floor. The fast option is a
    candidate only when no other model of its rank or better fits any pass (the last resort); else AutoFit.fast names it."""
    allowed = budget.allowed
    tiers = budget.tiers
    eligible = [c for c in candidates if c.eligible]
    in_scope = [c for c in eligible if scope == "catalogue" or c.downloaded]
    pickable = [c for c in in_scope if not c.fast]
    fast = fast_option(in_scope, budget)
    if fast and not any((c.rank or 0) <= (fast[0].rank or 0) and _fits_somehow(c, allowed, budget.swa_full, tiers, budget)
                        for c in pickable):
        pickable.append(fast[0])            # the last resort: no other model of its rank or better fits (W1.6)

    def first(group: Sequence[Candidate]) -> Optional[Tuple[Candidate, Plan, int]]:
        for t, tier in enumerate(tiers):
            for c in group:
                plan = setup_of(c, tier, budget)
                if plan:
                    return c, plan, t
        return None
    own = first(_order(pickable, True, goal))
    other = first(_order(pickable, False, goal)) if own is None or goal == "hard-code" else None
    outranked = (own is not None and other is not None and other[0].order < own[0].order and
                 not any(c.order < other[0].order and _fits_somehow(c, allowed, budget.swa_full, tiers, budget)
                         for c in pickable if in_family(c, goal)))
    got, fallback = (own, False) if own is not None and not outranked else (other, other is not None)
    if got is None:
        return AutoFit(goal, scope, budget, None, None, len(tiers) - 1, False,
                       tuple(_rejections(eligible, None, len(tiers) - 1, False, scope, goal, allowed, budget.swa_full,
                                         tiers, budget)), fast)
    c, plan, t = got
    return AutoFit(goal, scope, budget, c, plan, t, fallback,
                   tuple(_rejections(eligible, c, t, fallback, scope, goal, allowed, budget.swa_full, tiers, budget,
                                     outranked)), None if fast and fast[0].name == c.name else fast, outranked)


def at_ub(fit: AutoFit, ub: int, fixed: bool = False, pick: bool = False) -> AutoFit:
    """Auto fit's answer with a pick whose experts stay in the RAM planned at the -ub its start runs (W1.6): the count
    depends on the -ub (a PC, the A3B Q4 at 2 x 128K: 38 layers at -ub 512, 40 at the start's 2048), so the reason,
    Settings and carl fit name the start's count. ub: the start's -ub as its order gives it (ub_order); fixed (a
    person's): as it is; pick (no tune record, this computer's default): the largest size up to ub that keeps the setup
    -ub 512 gets (ub_order.ub_pick); then, as the start lowers it, ub when its setup is -ub 512's, else 1024 or 512
    (fit.ub_choice). The pass, the rank and the reasons stay -ub 512's: the setup is the same. fit as it is for a pick
    whole on the GPUs, at -ub 512, or one that does not fit at a fixed ub (the start's own check refuses it)."""
    c, plan = fit.pick, fit.plan
    if c is None or plan is None or not plan.cpu_moe or ub == UBATCH:
        return fit
    tier = Tier(plan.slots, plan.ctx)
    plans: Dict[int, Optional[Plan]] = {UBATCH: plan}

    def at(u: int) -> Optional[Plan]:
        if u not in plans:
            plans[u] = setup_of(c, tier, fit.budget, False, u)
        return plans[u]

    def setup(u: int) -> Tuple[object, str]:
        p = at(u)
        return ((p.slots, p.ctx, p.spec.kind, p.cpu_moe > 0) if p is not None else None), ""
    if not fixed:
        if pick and ub > UBATCH:
            ub = ub_pick(ub, lambda u: (setup(u)[0], at(u).cpu_moe if at(u) is not None else 0))  # type: ignore
        ub = ub_choice(ub, setup).ub
    got = at(ub)
    return replace(fit, plan=got) if got is not None else fit


def _rejections(eligible: Sequence[Candidate], pick: Optional[Candidate], tier: int, fallback: bool, scope: Scope,
                goal: Goal, allowed: float, swa_full: bool = True,
                tiers: Sequence[Tier] = TIERS, budget: Optional[Budget] = None,
                outranked: bool = False) -> List[Rejection]:
    """Every eligible candidate ranked above the pick (and, after a fallback where none of the goal family fits, the
    whole goal family), best rank first, with the reason it lost. budget: the experts-in-RAM reasons on discrete GPUs.
    outranked: the goal family's fits rank below the pick (hard code's MoE fallback): only those ranked above it."""
    def better(c: Candidate) -> bool:
        if pick is None or (fallback and not outranked and in_family(c, goal)):
            return True
        return c.order < pick.order

    out: List[Rejection] = []
    for c in sorted(eligible, key=lambda c: c.order):
        if (pick is not None and c.name == pick.name) or not better(c):
            continue
        if scope == "downloaded" and not c.downloaded:
            reason = "is not downloaded"
        elif c.fast and _fits_somehow(c, allowed, swa_full, tiers):
            reason = "is the fast option: Auto fit takes it only when no other model of its rank or better fits"
        elif pick is not None and not fallback and not in_family(c, goal) and _fits_somehow(c, allowed, swa_full,
                                                                                            tiers, budget):
            other: Goal = "everyday" if goal == "hard-code" else "hard-code"
            reason = f"is {c.kind()}: Auto fit keeps it for the {GOAL_NAME[other]} goal"
        elif pick is not None and not fallback and not in_family(c, goal):
            reason = why_not(c, tiers[-1], allowed, swa_full, budget)    # it fits no pass: say that, not the family
        else:
            failed = tier if pick is not None and not (fallback and in_family(c, goal)) else len(tiers) - 1
            reason = why_not(c, tiers[failed], allowed, swa_full, budget)
        out.append(Rejection(c.name, c.rank or 0, reason))
    return out


def _fits_somehow(c: Candidate, allowed: float, swa_full: bool, tiers: Sequence[Tier] = TIERS,
                  budget: Optional[Budget] = None) -> bool:
    """Does the candidate fit any pass (its family aside; with a budget, with experts in the RAM at the floor too)?"""
    return any(setup_of(c, t, budget, True, UBATCH, allowed, swa_full) for t in tiers)


def best_downloaded(fit: AutoFit, candidates: Sequence[Candidate]) -> AutoFit:
    """What a start can use when the pick is not downloaded: the same goal, downloaded only."""
    if fit.pick is None or fit.pick.downloaded:
        return fit
    return auto_fit(candidates, fit.budget, fit.goal, "downloaded")

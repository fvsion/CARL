"""The constants of the memory estimate for each backend (W6.5, plan 5.7; rebuilt by A57, WEST; A58, WESTb; A60, WESTc). Pure.

The estimate in domain/memory/fit.py is the Mac's formula, measured on Metal (llama.cpp 0.6.0; reference/memory.md). A
backend holds more or less than that, so the estimate takes a Calibration, one per backend. The weights, the KV cache
and the recurrent state are exact (the header's sums reproduce llama.cpp's buffers to 0.1 MiB); what is not exact is
the compute buffer, so it is what a Calibration corrects. A57 and A58 kept rows of it by -ub for each kind of model;
A60 (the user: "analyze the model and generically build the buffer") replaced the rows with one model per backend that
is built from the model's header alone: no row for a model, a quant or a pool, no step, no allowance.

THE COMPUTE BUFFER is the allocator's peak: the graph of one batch (-ub tokens) runs layer by layer, a layer's big
intermediates are freed when it is done, and what stays live through the whole graph is the attention mask. So

  compute buffer = masks + the largest of three working sets (ComputeFit.peak)

  masks    ComputeParts.mask: the f16 mask of the attention, 2 bytes per -ub row and token of the pool; a second one for the
           sliding-window layers (their window and one batch with a window cache, the whole pool with --swa-full). Exact
           (the 26B-A4B's full cache holds the window cache's buffer plus the second mask to the byte).
  attention  the K and V view in f16 of the widest layer kind, the pool times 2 bytes x the layer's K and V widths
           (ComputeParts.view, exact: the A3B's buffer grows by 2 MiB per 1K tokens of pool beyond the mask, the 0.6B by 4),
           and the layer's activations: the attention's and the recurrent layers' matrices' outputs x -ub (attn_width) and
           the per-layer inputs of Gemma (extra_width), and a part of the experts' copy
  feed-forward  the experts' copy (stage_of: with the experts in the RAM llama.cpp copies them to the GPU for a large
           batch and the buffer holds the largest down matrix and the median one of the blocks below N, or with one
           gate_up matrix the largest layer; once: weight 1, so the Q4_K_M's Q6_K layers count from N 35), the gate, the up
           and the activation of the experts' choice or the dense width (ff_width: 3 times -ub x ff_width x 4 bytes), the
           per-layer inputs, and a small fixed part
  the big working set  the matrices' outputs of the widest layer (layer_width x -ub x 4 bytes), of the attention and of the
           inputs outside the layers (extra_width), which is what the buffer is at a large -ub, and a part of the experts'
           copy; one more feed-forward tensor with the full cache of a model with sliding-window layers
  and, left out because the data do not show them: the output (n_vocab x the outputs kept: 1 to 2 MiB) and the CUDA
  dequantization scratch (the pool of the CUDA backend, which the compute buffer does not hold; the context, which does,
  does not move with -ub or with the quant: WCTX)

The widths are elements one token gets out of the matrices of the model, read from the tensor table of the header
(gguf.width_sums), the same for every backend; the coefficients are the backend's (ComputeFit): the same graph is
allocated by every backend (Metal's and CUDA's E4B are the same 429.4 MiB beyond the masks at -ub 2048 and 857.4 at 4096),
and CUDA adds its staged copy and a few buffers of its own. No term is a kind of model (A60; the review of the first
version found a constant for the dense model, one for the hybrid and one for the MoE, each of which picked out the one
model of its kind in the data: removed). A term is a width times -ub, the experts' copy or one fixed part, and its
coefficient is the same for every model. They were fitted by a linear program to the measured starts (the least total
over, never under any of them; the measured starts: WCTX's 91 starts on CUDA, WEST's, WESTb's, WESTc's, the
M3 Pro's 24 on Metal; the 0.6B too) with the view, the staged copy and the feed-forward's three tensors fixed at their
physical values, and each coefficient rounded up (by a development tool, which also fits with each model held
out in turn). Within the data the whole start is within 7.0% over (five starts are over 5%), the compute buffer 2.4% over at the
median, 8.7% at the 90th percentile and 18% at most (CUDA; 46 of 126 starts are over 5%: the allocator's layout moves by quant,
family and -ub in steps that one function of the header does not follow, the A3B IQ3 holds 382 MiB in the attention's set at
-ub 2048 and the Q4_K_M 304 on the same graph), and the Mac's 24 starts are within 0.9%. A60's target, within about 5% of the
compute buffer on every start and never under a model that was held out, is not met by this data; test_measured_starts and
test_compute_fit state the target and what is now. The held-out check shows what the data can and cannot give: the 26B-A4B,
held out, is 0.1% under at worst, the 0.6B is never under, the A3B IQ3 held out is 6.5% under, the Q4_K_M 17%, and the E4B, the
only dense model with per-layer inputs, 11% to 27%; on Metal, with two models, the IQ3 held out is 16% under. A new
architecture is the estimate's weakest case; the dry run (domain/memory/dry_run.py) is the check for a model whose file is
local.

  draft_compute  the MTP draft context's compute buffers, in main compute buffers: 2 on Metal (measured there:
             two buffers of the main one's size); on CUDA one buffer of about half the main one's (A48, measured on
             an RTX 2070 SUPER, the A3B IQ3 with its experts in the RAM: 352 against 670 MiB at 1 x 48K and -ub 2048, 272 against 558 at
             1 x 96K and -ub 1024), so 0.55. Vulkan is not measured: Metal's 2
  idle_head  the MTP head's blocks are not loaded when MTP does not run (A52, measured on CUDA, llama.cpp b11738; A57:
             on Metal too, the M3 Pro's A3B IQ3 model buffer is the file less the head; loaded.py)
  context    the bytes a backend's context holds beyond llama.cpp's own buffers, once on each device it runs. CUDA: 177 MiB,
             the largest of WCTX's starts (the process less its buffers: the A3B 106 to 170 MiB, median 163, 66 starts; the
             E4B, A4B and 0.6B 113 to 177 MiB, their process read as nvidia-smi's delta, which the desktop moves by up to
             45 MiB); it was the median, 163 MiB (A56), and 256 MiB before that. Vulkan: 9 to 19 MiB measured, 20 MiB
  gpu_weights  the weights on the GPU are the file less token_embd (llama.cpp keeps it in the RAM) and less Gemma's
             per-layer embeddings (they stay in the RAM too), plus the output layer: the file's output.weight, or for a
             model that ties it to token_embd (output_bytes 0) a copy of token_embd. Metal maps the whole file.

A shape without the widths (a header cache entry from before A60, or a header read that did not reach the tensor table, such
as a remote prefix of a model with a large table) gets them, on a PC backend, as upper bounds (bounded): multiples of act_width, the widest
activation of one token, which the header's scalars give, and for a MoE model its experts take the model's file in equal
shares of the blocks. The same model then builds the buffer, never under a measured start (the guard test strips the table
of every measured start and checks it), and about 54% over at the median on CUDA. The Mac's formula was under the CUDA
buffers (A57), so it is not the fallback there. On the Mac it is: it is what the Mac measured (Saluki and the E4B to within 10
MiB at -ub 512), a header without its table is rare there (the file is local), and it is under the buffers of the A3B IQ3 and
the E4B at -ub 2048 and above, which a header with its table does not do.

ROCm runs llama.cpp's CUDA code through HIP and has not been measured: it takes CUDA's numbers. Vulkan has no measured
compute buffers either, and its context is small: it takes CUDA's fit too, the larger of the two measured backends.

These numbers make the plan and carl fit's table. The source of a start's GPU need is llama-server's own fit (E, A67:
domain/memory/server_fit.py): the start measures its plan, and a need over this estimate makes the plan again with that
much less room. Where no llama-server with --fit is present the need is B (bound below: the same terms refitted over the
measured starts and the E points as an upper bound). llama.cpp's own no-alloc dry run (llama-fit-params --fit-print on,
domain/memory/dry_run.py) was tried as the source first (A57: it counts the logits; A64's D with the output off the GPU
is under with experts in the RAM): it is a check of E on a start whole on the GPU. A buffer measured after a start is not
added to the estimate (A58: it is left for the engine, whose slot pool takes that room).

The backend of a start comes from its devices: llama.cpp names them by backend (CUDA0, Vulkan1, ROCm0), and the memory
plan puts a PC's start on named devices. A start on no device (a Mac, the CPU) is METAL.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

GIB = 2 ** 30
MIB = 2 ** 20


@dataclass(frozen=True)
class ComputeParts:
    """The compute buffer of one context in its physical parts, bytes (fit.compute_parts): act grows with -ub alone, mask
    with -ub x pool (all the attention masks), kv with the pool alone (the Mac's formula adds the three). view: the K and V
    view in f16 of the widest layer kind (the sliding-window layers keep a window of the pool, not all of it unless the
    full cache); windowed: the sliding-window layers keep a window cache."""
    act: float
    mask: float
    kv: float
    windowed: bool = False
    view: float = -1.0

    @property
    def total(self) -> float:
        """The Mac's formula: the three parts added."""
        return self.act + self.mask + self.kv

    @property
    def seen(self) -> float:
        """The view (the kv part for a parts built without one)."""
        return self.kv if self.view < 0 else self.view


def _experts(shape: Mapping[str, object], key: str, n_ram: Optional[int]) -> List[int]:
    """The experts' bytes of the blocks below n_ram (None or 0: all) under a shape key (expert_bytes: each layer's;
    expert_down_bytes: its down matrices), only for a MoE model; [] when the shape has none."""
    experts = shape.get(key) or []
    if not is_moe(shape) or not isinstance(experts, list):
        return []
    return [int(e) for e in (experts[:n_ram] if n_ram else experts)]


def _median(values: List[int]) -> float:
    return float(sorted(values)[len(values) // 2]) if values else 0.0


def stage_of(shape: Mapping[str, object], n_ram: Optional[int] = None) -> float:
    """The experts' copy the compute buffer holds, bytes (A60). With the experts in the RAM llama.cpp copies a layer's
    experts to the GPU for a large batch, and the buffer holds the copies of the blocks below n_ram (None or 0: all). Measured
    (WCTX, WESTc: the A3B IQ3 and Q4_K_M, 13 starts at -ub 512 and 1024, to the MiB): with separate gate and up matrices it is
    the largest down matrix and the typical (median) one, 220 MiB for the IQ3 with N 34 and 246 with N 35 or more, 352 for the
    Q4_K_M with N 30 and 386 with N 41; with one gate_up matrix (the 26B-A4B) it is the largest layer, 408 MiB. A shape
    without the down matrices (a cache entry from before A60) takes its largest layer. 0 for a dense model."""
    layers, down = _experts(shape, "expert_bytes", n_ram), _experts(shape, "expert_down_bytes", n_ram)
    if not down or shape.get("expert_fused") or len(down) != len(layers):
        return float(max(layers, default=0))
    return float(max(down)) + _median(down)


def terms(parts: ComputeParts, shape: Mapping[str, object], ub: int, n_ram: Optional[int] = None) -> Dict[str, float]:
    """The terms a working set is made of (ComputeFit), MiB. Each is a size the header gives (a width times -ub, the experts'
    copy) or a fixed part: none is a kind of model, so no coefficient can belong to one model (A60).
      view    the K and V view of the widest layer kind (exact)
      attn    -ub x attn_width x 4 bytes: the attention's and the recurrent layers' matrices' outputs
      ff      -ub x ff_width x 4 bytes: one tensor of the feed-forward's gate or up
      layer   -ub x layer_width x 4 bytes: every matrix's output of the widest layer
      extra   -ub x extra_width x 4 bytes: the matrices outside the layers (Gemma's per-layer inputs)
      stage   the experts' copy of the blocks below n_ram (stage_of); 0 for a dense model
      one     1 (a fixed part)
      full    ff when the model has sliding-window layers and keeps the full cache, else 0 (the full cache holds one
              more feed-forward tensor, measured on the E4B)"""
    u = ub * 4.0 / MIB
    ff = u * _bytes(shape, "ff_width")
    return {"view": parts.seen / MIB, "attn": u * _bytes(shape, "attn_width"), "ff": ff,
            "layer": u * _bytes(shape, "layer_width"), "extra": u * _bytes(shape, "extra_width"),
            "stage": stage_of(shape, n_ram) / MIB, "one": 1.0,
            "full": ff if (not parts.windowed and shape.get("swa_window", 0)
                           and shape.get("kv_elems_per_token_swa", 0)) else 0.0}


Coefficients = Tuple[Tuple[str, float], ...]


@dataclass(frozen=True)
class ComputeFit:
    """The coefficients of the compute buffer of one backend (the module docstring): for each of the three working
    sets, the coefficient of each of its terms (terms). The sets add up their terms; the buffer is the masks and the
    largest set. The view, the staged copy and the feed-forward's three tensors have their physical values (1, 1, 3)."""
    attention: Coefficients
    feed: Coefficients
    big: Coefficients
    margin: float = 0.0            # A67: the sets times 1 + margin, so a model held out of the fit is never under

    def peak(self, parts: ComputeParts, shape: Mapping[str, object], ub: int, n_ram: Optional[int] = None) -> float:
        """The compute buffer, bytes: the masks and the largest of the three working sets. Never below the masks."""
        t = terms(parts, shape, ub, n_ram)
        sets = [sum(c * t[name] for name, c in cs) for cs in (self.attention, self.feed, self.big)]
        return parts.mask + max(max(sets), 0.0) * (1 + self.margin) * MIB


@dataclass(frozen=True)
class Calibration:
    """The constants of one backend (module docstring). backend: its name (metal, cuda, vulkan, rocm); context: bytes a
    device's context holds beyond llama.cpp's buffers; gpu_weights: token_embd stays in the RAM and the output layer is
    on the GPU; compute: the coefficients of the compute buffer (None: the Mac's formula); draft_compute: the MTP draft
    context's compute buffers, in main ones; idle_head: the MTP head's blocks are not loaded when MTP does not run;
    bound_widths: a shape without widths gets them as upper bounds (bounded) rather than the Mac's formula."""
    backend: str
    context: float = 0.0
    gpu_weights: bool = False
    compute: Optional[ComputeFit] = None
    draft_compute: float = 2.0
    idle_head: bool = False
    bound_widths: bool = False

    @property
    def is_metal(self) -> bool:
        """The Mac's formula for the weights and the context (the compute buffer has its own model: ComputeFit)."""
        return self.context == 0.0 and not self.gpu_weights

    def compute_buffer(self, parts: ComputeParts, shape: Mapping[str, object], ub: int = 0,
                       n_ram: Optional[int] = None, file: float = 0.0) -> float:
        """The compute buffer of a start of this backend from the Mac's parts (fit.compute_parts): the masks and the
        largest working set of the model's widths (ComputeFit.peak). It only grows with -ub and with the pool, and is never
        under a measured start (the guard test). A shape with no widths (a header cache entry from before A60, a header
        read that did not reach the tensor table) gets them, on a PC backend (bound_widths), as upper bounds from the scalars
        of the header and the model's file size (bounded: file, in bytes), so it stays above the measured buffers too: the
        Mac's formula was under the CUDA buffers (A57). On the Mac and for a backend with no coefficients it keeps the Mac's
        formula, which the Mac measured to within 10 MiB at the default -ub (Saluki, the E4B; a unit test holds it)."""
        if self.compute is None:
            return parts.total
        if not _bytes(shape, "layer_width"):
            if not self.bound_widths:
                return parts.total
            shape = bounded(shape, file)
        return self.compute.peak(parts, shape, ub, n_ram)

    def weights(self, shape: Mapping[str, object], file: float) -> float:
        """The weights on the GPU for a model file of `file` bytes (the drafter's, if it is counted in, too). Metal:
        the whole file. Else the file less token_embd, and a copy of it as the output layer when the model ties the
        two (output_bytes is 0 then). A header without the tensor sums gives the file."""
        embd, out = _bytes(shape, "embd_bytes"), _bytes(shape, "output_bytes")
        if not self.gpu_weights or embd <= 0:
            return file
        return max(file - embd - self.ram_bytes(shape) + (0.0 if out > 0 else embd), 0.0)

    def ram_bytes(self, shape: Mapping[str, object]) -> float:
        """The weights that stay in the RAM besides token_embd (Gemma's per-layer embeddings) on a GPU backend; 0 on
        Metal and for a model without them."""
        return _bytes(shape, "per_layer_bytes") if self.gpu_weights else 0.0

    def head(self, embd: float) -> float:
        """What token_embd costs on the first device: the whole of it on Metal, nothing where it stays in the RAM."""
        return 0.0 if self.gpu_weights else embd

    def output(self, embd: float, out: float) -> float:
        """What the output layer costs on the last device: its tensor, or on the GPU a copy of token_embd when the
        model ties them."""
        return (out if out > 0 else embd) if self.gpu_weights else out


def is_moe(shape: Mapping[str, object]) -> bool:
    """The model has MoE experts (a header with the tensor table; a dense model or a header without it: no)."""
    experts = shape.get("expert_bytes") or []
    return isinstance(experts, list) and any(isinstance(b, (int, float)) and b > 0 for b in experts)


def _bytes(shape: Mapping[str, object], key: str) -> float:
    v = shape.get(key, 0)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


ACT_DEFAULT = 17408                  # a header without act_width: the widest feed-forward of the catalogue's dense 27B
# What the widths of the measured models are at most, in act_width (the widest activation of one token, from the header's
# scalars): layer 4.0 (0.6B; the others 2.4 to 3.4), attn 2.0 (0.6B 1.7), ff 1.0, extra 2.1 (E4B's per-layer inputs).
BOUND = (("layer_width", 4.0), ("attn_width", 2.0), ("ff_width", 1.0), ("extra_width", 2.5))


def bounded(shape: Mapping[str, object], file: float = 0.0) -> Dict[str, object]:
    """The shape with widths that are upper bounds, for a header whose tensor table was not read: the widths are multiples of
    act_width (BOUND), and a MoE model's experts take the model's file in equal shares of its blocks (the largest layer of
    the measured models is a third to two thirds of that share). A shape that has widths is returned as it is."""
    out: Dict[str, object] = dict(shape)
    if _bytes(shape, "layer_width"):
        return out
    act = _bytes(shape, "act_width") or float(ACT_DEFAULT)
    out.update({key: act * k for key, k in BOUND})
    blocks = int(_bytes(shape, "blocks"))
    if _bytes(shape, "experts") and not is_moe(shape) and file > 0 and blocks > 0:
        out["expert_bytes"] = [int(file / blocks)] * blocks
    return out


# The compute buffer's coefficients (module docstring; fitted by a development tool to
# the measured starts, each rounded up). CUDA: WCTX, an RTX 2070 SUPER, llama.cpp b11738, 91 starts (the A3B IQ3 and
# Q4_K_M, the 26B-A4B, the E4B and the 0.6B), WEST's 4, WESTb's 15, WESTc's 16. Metal: the M3 Pro, 0.6.0 build 11429, 24
# starts of the A3B IQ3 and the E4B; the experts are in the same memory there: no staged copy.
CUDA_COMPUTE = ComputeFit(
    attention=(("view", 1.0), ("attn", 2.638), ("extra", 0.75), ("stage", 0.347)),
    feed=(("stage", 1.0), ("ff", 3.0), ("extra", 1.126), ("one", 0.3)),
    big=(("layer", 0.918), ("extra", 0.446), ("attn", 1.573), ("stage", 0.337), ("full", 1.0)))
METAL_COMPUTE = ComputeFit(
    attention=(("view", 1.0), ("attn", 2.001), ("extra", 0.529), ("one", 16.6)),
    feed=(("ff", 4.339), ("one", 82.3)),
    big=(("layer", 1.328), ("extra", 0.423)))
# The CUDA context beyond llama.cpp's buffers: the largest of WCTX's starts (process less buffers), A57 (module docstring).
CUDA_CONTEXT = 177 * MIB
METAL = Calibration("metal", compute=METAL_COMPUTE, idle_head=True)
CUDA = Calibration("cuda", context=CUDA_CONTEXT, gpu_weights=True, compute=CUDA_COMPUTE, draft_compute=0.55, idle_head=True,
                   bound_widths=True)
VULKAN = Calibration("vulkan", context=20 * MIB, gpu_weights=True, compute=CUDA_COMPUTE, idle_head=True, bound_widths=True)
# ROCm runs llama.cpp's CUDA code through HIP and has not been measured: it takes CUDA's numbers.
ROCM = Calibration("rocm", context=CUDA_CONTEXT, gpu_weights=True, compute=CUDA_COMPUTE, draft_compute=0.55, idle_head=True,
                   bound_widths=True)
BY_BACKEND = {c.backend: c for c in (METAL, CUDA, VULKAN, ROCM)}
# B (A67, WESTd): the upper bound of a start where no llama-server with --fit is present (llama-server's own fit, E, is the
# source of a start's need: domain/memory/server_fit.py). The same terms, fitted by a development tool bound to
# the measured starts and the E points (every catalogue model in the model library and
# gpt-oss-20b, llama-server's fit on stubs, over -ub 512 to 4096, 1 x 16K to 1 x 128K and 2 x 96K, N 0, half and all, both
# caches; CUDA b11738 on an RTX 2070 SUPER, Metal 0.6.0 on the M3 Pro), the least total over and none under, each rounded up; the margin
# is the most that one model, held out of the fit, needs to be never under (the fit's margin). An upper bound, so it
# is far over on some starts; the estimate above stays what CARL plans and shows with.
CUDA_BOUND = ComputeFit(
    attention=(("view", 1.0), ("attn", 4.092), ("extra", 0.213), ("stage", 0.692)),
    feed=(("stage", 1.0), ("ff", 3.0), ("extra", 0.0), ("one", 434.0)),
    big=(("layer", 0.74), ("extra", 0.996), ("attn", 2.795), ("stage", 0.136), ("full", 0.0)),
    margin=0.0)              # every model, held out of the fit, needs +0.0%
METAL_BOUND = ComputeFit(
    attention=(("view", 1.0), ("attn", 5.245), ("extra", 4.456), ("one", 18.0)),
    feed=(("ff", 4.16), ("one", 306.5)),
    big=(("layer", 1.853), ("extra", 0.0)),
    margin=0.227)            # gpt-oss-20b, held out of the fit, needs +22.7%; every other model +0.0%
BOUNDS = {"metal": METAL_BOUND, "cuda": CUDA_BOUND, "vulkan": CUDA_BOUND, "rocm": CUDA_BOUND}


# B's MTP draft context in main compute buffers, where the estimate's is under an E point: on CUDA the A3B IQ3 at
# 2 x 64K, -ub 4096 needs 0.572 (the estimate's 0.55 is 3.3% under), and held out of the other models' largest (0.551)
# +3.9%: 0.6. Metal's 2 is over every E point (0.72 at the most). The MTP E points of the measured starts.
B_DRAFT_COMPUTE = {"cuda": 0.6, "vulkan": 0.6, "rocm": 0.6}


def bound(calib: Calibration) -> Calibration:
    """The backend's constants with B's compute buffer and MTP draft context (A67): the start's need where
    llama-server's fit cannot run."""
    return replace(calib, compute=BOUNDS.get(calib.backend, CUDA_BOUND),
                   draft_compute=max(calib.draft_compute, B_DRAFT_COMPUTE.get(calib.backend, 0.0)))
KINDS = {"cuda": "cuda", "vulkan": "vulkan", "rocm": "rocm", "hip": "rocm", "mtl": "metal", "metal": "metal"}


def for_backend(name: str) -> Calibration:
    """The calibration of a backend by its name (cuda, vulkan, rocm, metal); anything else (cpu, "") is METAL, the
    formula that counts the whole file."""
    return BY_BACKEND.get(KINDS.get(name.strip().lower(), ""), METAL)


def backend_of(device: str) -> str:
    """The backend in llama.cpp's name for a device: CUDA0 -> cuda, Vulkan1 -> vulkan, ROCm0 -> rocm, MTL0 -> metal;
    "" for a name CARL does not know."""
    word = device.strip().rstrip("0123456789").lower()
    return KINDS.get(word, "")


def for_device(device: str) -> Calibration:
    """The calibration of a device by llama.cpp's name for it (CUDA0)."""
    return for_backend(backend_of(device))


def for_devices(names: Sequence[str]) -> Calibration:
    """The calibration of a start on these devices (llama.cpp's names): the first device's backend decides, as
    llama.cpp's own build does (one backend runs a start: mixed vendors use Vulkan for all). No device: METAL."""
    return for_device(names[0]) if names else METAL

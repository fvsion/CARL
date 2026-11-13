"""THE llama-server argv builder: a single-model start, a router start, and llama-batched-bench (Auto-tune's
parallel measure). Pure: the arguments come from a resolved LaunchPlan (launch_plan.py); the program's name or path
is the caller's (it goes first, before these). The order is host/serve-llama.sh's, option for option.

--fit off on every start: CARL sizes the start itself (llama-fit.py --check); llama.cpp's own fitting only probes
here, and the probe logged a false error for Gemma 4's MTP drafter. On a PC the devices the memory plan chose follow
-ngl: -dev, and over several GPUs -ts and --split-mode layer (W6.6, domain/memory/split.py). A MoE model that does not
fit the GPUs whole keeps the experts of its first N layers in the RAM: --n-cpu-moe N right after -ngl 999, before the
devices (W6.7, domain/memory/offload.py), then the threads, one per physical core and pinned (--threads N --cpu-mask M;
A54, domain/hardware/cpu_topology.py). --load-mode none when the memory plan found the free RAM enough for it (A44,
A45; the weights read into the RAM, not mapped), unless the user's own options set the load. Contract C-119.
"""
from __future__ import annotations

from typing import List, Sequence

from ..catalog.drafts import FILE_SPECS
from ..config.settings import LLAMA_KEYS
from ..hardware.cpu_topology import pin_options
from ..shared.constants import UBATCH
from .launch_plan import (ALIAS, API_KEY_FILE, BATCH, CACHE_RAM, CKPT, CKPT_STEP, CTX, HOST, KV_K, KV_V, LOG_FILE,
                          MIN_P, MODEL, NGRAM_MATCH, NGRAM_MAX, NGRAM_MIN, PORT, PRESENCE, REPEAT, SLOTS, SPEC,
                          SPEC_MTP, SPEC_N, SPEC_NGRAM, SPEC_NONE, TEMP, TOP_K, TOP_P, UB, LaunchPlan, logs)

ALL_LAYERS = "999"                                   # -ngl: every layer on the GPU
THINKING_KWARGS = '{"preserve_thinking":true}'       # the clients' thinking survives the chat template
LOG_LINES = ["--log-timestamps", "--log-prefix"]     # colours kept; the log tool strips them
VERBOSE = "4"                                        # -lv 4 for the RAM cache's lines (the log filter keeps the log small)
SPLIT_LAYER = "layer"                                # --split-mode: the layers of the model, device by device
IDLE_SLOT_SIMILARITY = "0.5"                         # -sps: a subagent with the same system prompt keeps off the main slot


def server_argv(plan: LaunchPlan) -> List[str]:
    """llama-server's arguments for a single model: the model, the address and the key, the sampling, the context
    memory (one unified pool of slots x ctx with more than one slot), the batches (-b at least -ub: llama.cpp would
    cut -ub to -b), the checkpoints, the RAM cache,
    the slot folder, -lv 4 with a log (its output goes through CARL's log filter), the thinking toggle's template,
    the speculation, the drafter, the full sliding-window cache, then the extra options."""
    p = plan.get
    slots, ctx = int(p(SLOTS)), int(p(CTX))
    out = ["-m", p(MODEL), "--alias", p(ALIAS),
           "--host", p(HOST), "--port", p(PORT), "--api-key-file", p(API_KEY_FILE),
           "--jinja", "--reasoning-format", "deepseek",
           "--temp", p(TEMP), "--top-p", p(TOP_P), "--top-k", p(TOP_K), "--min-p", p(MIN_P),
           "--presence-penalty", p(PRESENCE), "--repeat-penalty", p(REPEAT),
           "--chat-template-kwargs", THINKING_KWARGS,
           "-ngl", ALL_LAYERS, *cpu_moe_argv(plan), *pin_argv(plan), *device_argv(plan),
           "-fa", "on", "--fit", "off", "-ctk", p(KV_K), "-ctv", p(KV_V), "-c", str(slots * ctx),
           "-b", str(max(int(p(BATCH)), int(p(UB)))), "-ub", p(UB), "--parallel", str(slots)]
    if slots > 1:
        out += ["--kv-unified", "--kv-unified-per-slot", str(ctx), "--no-cache-idle-slots", "-sps", IDLE_SLOT_SIMILARITY]
    out += [*load_argv(plan), *vision_argv(plan), "--ctx-checkpoints", p(CKPT), "--checkpoint-min-step", p(CKPT_STEP),
            "--cache-ram", p(CACHE_RAM), "--metrics", "--slot-save-path", plan.slot_dir]
    if logs(plan):
        out += ["-lv", VERBOSE] + LOG_LINES
    if plan.template:
        out += ["--chat-template-file", plan.template]
    out += spec_argv(plan)
    if plan.draft:
        out += ["-md", plan.draft]
    if plan.swa == "full":
        out += ["--swa-full"]
    return out + list(plan.extra)


def fit_argv(model: str, ctx: int, slots: int, kv: str, ub: int, swa_full: bool, mtp_n: int, device: str,
             cpu_moe: int) -> List[str]:
    """The arguments that set a start's memory, as server_argv gives them, for a start that has no LaunchPlan yet: Auto
    fit's pick, which llama-server's own fit measures before it is started or downloaded (A67 (2),
    domain/memory/server_fit.py). mtp_n: the guesses of an MTP head in the model file (0: no MTP; a drafter in its own
    file is bounded from its header, not measured); device: the one GPU of a PC ("" on a Mac); cpu_moe: the layers whose
    experts stay in the RAM."""
    out = ["-m", model, "-ngl", ALL_LAYERS] + (["--n-cpu-moe", str(cpu_moe)] if cpu_moe > 0 else []) + \
        (["-dev", device] if device else [])
    out += ["-fa", "on", "--fit", "off", "-ctk", kv, "-ctv", kv, "-c", str(slots * ctx),
            "-b", str(max(int(LLAMA_KEYS["batch"].default), ub)), "-ub", str(ub), "--parallel", str(slots)]
    if slots > 1:
        out += ["--kv-unified", "--kv-unified-per-slot", str(ctx), "--no-cache-idle-slots"]
    out += ["--no-mmproj"]
    if mtp_n > 0:
        out += ["--spec-type", SPEC_MTP, "--spec-draft-n-max", str(mtp_n)]
    return out + (["--swa-full"] if swa_full else [])


def vision_argv(plan: LaunchPlan) -> List[str]:
    """--mmproj FILE when vision is on and the model's projector is downloaded (WCATX), else --no-mmproj: the start is
    text only, as llama-server's own default (it loads a projector it finds) would not be."""
    return ["--mmproj", plan.mmproj] if plan.mmproj else ["--no-mmproj"]


def load_argv(plan: LaunchPlan) -> List[str]:
    """--load-mode none when the memory plan chose it (the user's own --load-mode, --mmap or --no-mmap in the extra
    options: the plan leaves load_mode empty); none for the mapped load, llama.cpp's default."""
    return ["--load-mode", "none"] if plan.load_mode == "none" else []


def device_argv(plan: LaunchPlan) -> List[str]:
    """The devices of a PC (W6.6): -dev names the devices the memory plan put the model on; with several, -ts gives the
    layers of each and --split-mode layer says how llama.cpp reads it. llama.cpp's own split reads the wrong free VRAM
    on Windows, so the plan's is passed explicitly. A Mac, and a start with no plan, pass none. One device: -dev only."""
    if not plan.devices:
        return []
    return ["-dev", plan.devices] + (["-ts", plan.tensor_split, "--split-mode", SPLIT_LAYER] if plan.tensor_split else [])


def cpu_moe_argv(plan: LaunchPlan) -> List[str]:
    """--n-cpu-moe N: the MoE experts of the first N layers stay in the RAM (W6.7); none when every expert is on the
    GPUs (a Mac, a dense model, a model that fits whole)."""
    return ["--n-cpu-moe", str(plan.n_cpu_moe)] if plan.n_cpu_moe > 0 else []


def pin_argv(plan: LaunchPlan) -> List[str]:
    """--threads N --cpu-mask 0x...: one thread on the first logical processor of each physical core (A54); none when the
    plan has no pin (every expert on the GPUs, a Mac, a CPU that is not known, the user's own thread options)."""
    return pin_options(plan.pin) if plan.pin is not None else []


def spec_argv(plan: LaunchPlan) -> List[str]:
    """The speculation: --spec-draft-n-max is read by the drafters (MTP, DFlash, DSpark, EAGLE-3: the drafter file goes
    with -md, below); n-gram has its own lookup and draft lengths."""
    spec = plan.get(SPEC)
    if spec == SPEC_NONE:
        return []
    out = ["--spec-type", spec]
    kinds = spec.split(",")
    if SPEC_MTP in kinds or any(k in FILE_SPECS for k in kinds):
        out += ["--spec-draft-n-max", plan.get(SPEC_N)]
    if SPEC_NGRAM in kinds:
        out += ["--spec-ngram-mod-n-match", plan.get(NGRAM_MATCH), "--spec-ngram-mod-n-min", plan.get(NGRAM_MIN),
                "--spec-ngram-mod-n-max", plan.get(NGRAM_MAX)]
    return out


def router_argv(plan: LaunchPlan, preset: str) -> List[str]:
    """llama-server's arguments in router mode: the presets file (every downloaded model that fits, each with its
    own settings), at most one model in memory, the default log level with a log file, then the extra options."""
    p = plan.get
    out = ["--host", p(HOST), "--port", p(PORT), "--api-key-file", p(API_KEY_FILE),
           "--models-preset", preset, "--models-max", "1"]
    if logs(plan):
        out += ["--log-file", p(LOG_FILE)] + LOG_LINES
    return out + list(plan.extra)


def batched_bench_argv(model: str, counts: Sequence[int], prompt: int, gen: int, kv: str) -> List[str]:
    """llama-batched-bench's arguments for Auto-tune's parallel measure: `counts` requests at once, each reading
    `prompt` tokens and writing `gen`, with the context memory type of the start (K and V the same)."""
    return ["-m", model, "-c", str(max(counts) * (prompt + gen) + 1024),
            "-b", str(LLAMA_KEYS["batch"].default), "-ub", str(UBATCH), "-npp", str(prompt), "-ntg", str(gen),
            "-npl", ",".join(str(n) for n in counts), "-fa", "on", "-ctk", kv, "-ctv", kv, "-ngl", ALL_LAYERS,
            "--fit", "off"]

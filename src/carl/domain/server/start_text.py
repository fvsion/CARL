"""The start lines: one plain sentence per setting, with its unit and where it comes from (a flag, the environment,
the settings, Auto-tune, the catalogue, the model file, Auto fit or CARL's default), the sliding-window line, and the
note when K and V differ. On a PC a line for each GPU after the address (the memory plan's: which layers go where,
W6.6), and for a MoE model with experts in the RAM a line that names N (--n-cpu-moe, W6.7). The router start's lines
too. Pure: the words of this computer come from PlatformWords.
The text is host/serve-llama.sh's of 1.20.10, byte for byte, held by the CLI goldens. Contract C-119.
"""
from __future__ import annotations

from typing import List

from ..shared.paths import policy_for
from ..shared.platform_words import PlatformWords
from .launch_plan import (ALIAS, CACHE_RAM, CARL_SOURCES, CTX, HOST, KV, KV_K, KV_V, KV_WORDS, LOG_FILE, MODEL,
                          MODEL_NAME, N_CPU_MOE, PORT, SLOTS, SPEC, SPEC_MTP, SPEC_N, SPEC_NONE, SWA_MODE, UB, AUTO,
                          LaunchError, LaunchPlan, backend, logs)

READING = "CARL reads the model and checks the memory..."     # first, in a terminal: a slow first start shows progress
SOURCE_WORDS = {"flag": "from your option", "env": "from the environment", "config": "from your settings",
                "auto-tune": "from Auto-tune", "catalogue": "from the catalogue", "header": "from the model file",
                "auto-fit": "from Auto fit: the first setup in its order that fits"}
DEFAULT_WORD = "CARL's default"
SPEC_WORDS = {"none": "none", "ngram-mod": "n-gram", "draft-mtp": "MTP", "draft-mtp,ngram-mod": "MTP + n-gram"}
# The first start of a new llama.cpp build costs once (measured on a Windows PC): CUDA warms up and the first request is
# slow; Vulkan compiles its shaders at the first request. Off the Mac only: a start on no device has no backend.
FIRST_START = {
    "cuda": "First start with a new llama.cpp build: CUDA warms up for about 30 s, and the first request is slow. "
            "Later starts are quick.",
    "vulkan": "First request with a new llama.cpp build: Vulkan compiles its shaders, so the first prompt is slow. "
              "Later requests are quick."}
ROUTER_NOTES = (
    "OpenCode and Pi can switch the model. The server loads one model at a time. A switch takes 30 s to 2 min.",
    "After a switch, the new model has none of the sessions in memory. A saved session belongs to the model it",
    "ran on: OpenCode and Pi restore it from the disk cache when you switch back to that model (with saved sessions",
    "on, and for a Gemma model the full cache). A session that continues on the new model is read again.")


def gpu_blind_note(words: PlatformWords) -> str:
    """The line when this computer has a GPU that the llama-server found sees none (a build for another GPU)."""
    return (f"warning: this computer has a GPU, but this llama-server sees none: the model runs on the CPU, which is "
            f"slow. {words.command} gpu names the llama.cpp build for this GPU.")


def tilde(path: str, home: str) -> str:
    """A path under the home folder as ~/... (the home's text replaced at the start, as bash's ${p/#$HOME/~})."""
    return "~" + path[len(home):] if path.startswith(home) else path


def tokens_k(n: int) -> str:
    """96K, or 95.5K when not a whole K (K = 1024 tokens)."""
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n / 1024:.1f}K"


def mib_text(n: int) -> str:
    """4.0 GiB from 1024 MiB on, else 512 MiB."""
    return f"{n / 1024:.1f} GiB" if n >= 1024 else f"{n} MiB"


def source_of(plan: LaunchPlan, key: str, flag: str, var: str) -> str:
    """Where one setting comes from, in words: the flag (flag is its value), the environment (var was set there),
    else launch-env's CARL_SOURCES (key:source), else the default."""
    if flag:
        return SOURCE_WORDS["flag"]
    if var in plan.env_set:
        return SOURCE_WORDS["env"]
    s = f" {plan.get(CARL_SOURCES)} "
    at = s.find(f" {key}:")
    if at >= 0:
        return SOURCE_WORDS.get(s[at + len(key) + 2:].split(" ", 1)[0], DEFAULT_WORD)
    return DEFAULT_WORD


def cpu_moe_line(plan: LaunchPlan) -> str:
    """The line that names N for a start with MoE experts in the RAM (--n-cpu-moe N), and where N comes from."""
    n, setting = plan.n_cpu_moe, plan.get(N_CPU_MOE)
    why = ("from your setting llama.n_cpu_moe" if setting and setting != AUTO else
           "the model does not fit the GPUs whole, so CARL keeps the fewest experts in the RAM that let it fit")
    return f"Experts in the RAM: the MoE experts of the first {n} layer{'s' if n != 1 else ''} (--n-cpu-moe {n}; {why})."


def threads_line(plan: LaunchPlan) -> str:
    """The line for the threads of a start with MoE experts in the RAM (A54, A57), "" when the start says nothing: one
    per physical core, pinned (when Auto-tune measured it faster on this computer; the CPU runs the experts, and it was
    12 to 14% faster on a quiet one); the user's own options; or llama.cpp's own."""
    if plan.pin is not None:
        pin = plan.pin
        where = f" on the {pin.threads} faster of {pin.cores} cores" if pin.hybrid else ""
        return (f"Threads: {pin.words()}{where} (--threads {pin.threads} --cpu-mask {pin.mask_text()}). "
                "The CPU runs the experts in the RAM faster this way.")
    return f"Threads: {plan.pin_note}." if plan.pin_note else ""


def start_lines(plan: LaunchPlan, words: PlatformWords, home: str, llama_note: str = "") -> List[str]:
    """The lines a single-model start shows (stdout) just before llama-server runs. llama_note: the installed
    llama.cpp against the tested one ("" when it is not older)."""
    p, o = plan.get, plan.options
    model = p(MODEL)
    base = policy_for(model).basename
    out = [f"CARL starts the server: {p(MODEL_NAME) or p(ALIAS)} ({base(model)})."]
    if llama_note:
        out.append(llama_note)
    out.append(f"Address: http://{p(HOST)}:{p(PORT)}. {plan.net_note}")
    out += plan.gpu_note.split("\n") if plan.gpu_note else []      # a PC: a line for each GPU (the memory plan's)
    if plan.n_cpu_moe:                                              # a MoE model with experts in the RAM
        out.append(cpu_moe_line(plan))
        threads = threads_line(plan)
        out += [threads] if threads else []
    if plan.need_note:                                              # where the GPU need came from (A67)
        out.append(plan.need_note)
    if plan.dry_note:                                               # D's check against the fit: a changed build (A67)
        out.append(plan.dry_note)
    if backend(plan) in FIRST_START:                                # a PC: what the first start of a new build costs
        out.append(FIRST_START[backend(plan)])
    slots_src = plan.slots_note or source_of(plan, "slots", o.slots, SLOTS)
    out.append(f"Slots: {p(SLOTS)} ({slots_src}). Context: {tokens_k(int(p(CTX)))} tokens per slot "
               f"({source_of(plan, 'ctx', o.ctx, CTX)}).")
    ram = mib_text(int(p(CACHE_RAM)))
    if p(KV_K) == p(KV_V):
        why = plan.kv_note or source_of(plan, "kv", o.kv, KV)           # a start on the CPU alone says why (A71)
        out.append(f"Context memory type: {KV_WORDS.get(p(KV_K), p(KV_K))} ({why}). RAM cache: {ram}.")
    else:
        out.append(f"Context memory type: K {KV_WORDS.get(p(KV_K), p(KV_K))}, V {KV_WORDS.get(p(KV_V), p(KV_V))}. "
                   f"Different types read prompts about 5 times more slowly. RAM cache: {ram}.")
    spec = p(SPEC)
    if spec == SPEC_NONE:
        out.append(f"Speculation: none ({source_of(plan, 'spec', '', SPEC)}).")
    else:
        guesses = ""                                 # MTP's guesses; n-gram has none of its own to set
        if SPEC_MTP in spec.split(","):
            guesses = f", {p(SPEC_N)} guess{'' if p(SPEC_N) == '1' else 'es'}"
        drafter = f" The MTP drafter is {base(plan.draft)}." if plan.draft else ""
        out.append(f"Speculation: {SPEC_WORDS.get(spec, spec)}{guesses} "
                   f"({plan.spec_note or source_of(plan, 'spec', '', SPEC)}).{drafter}")
    swa_mode = p(SWA_MODE) or AUTO
    if plan.swa == "full":
        out.append(f"Sliding window: full cache. CARL can restore saved sessions and prompts (cache.swa = {swa_mode}).")
    elif plan.swa == "window":
        out.append("Sliding window: window cache. It uses less memory, but CARL cannot restore saved sessions and "
                   f"prompts (cache.swa = {swa_mode}).")
    log_text = tilde(p(LOG_FILE), home) if logs(plan) else "none"
    batch = f"-ub {p(UB)}"
    if plan.ub_ask is not None:                     # nobody to ask (a script, router mode, a client): the tuned -ub, said
        batch = plan.ub_ask.ignored(int(p(UB)))
    elif plan.ub_kept:
        batch += " (kept for this model)"
    out.append(f"Log file: {log_text}. Batch: {batch}. Your settings: {words.command} config show.")
    return out


def router_lines(plan: LaunchPlan, presets: str, preset_file: str, words: PlatformWords, home: str,
                 llama_note: str = "") -> List[str]:
    """The lines a router start shows. presets: the router-preset command's answer ("model NAME TEXT" for each
    model in the presets file, "skip TEXT" for one left out, "start NAME" for the one loaded first). Raises
    LaunchError when no model fits."""
    lines = presets.split("\n")
    if not any(line.startswith("model ") for line in lines):
        raise LaunchError(f"error: router mode cannot start: no downloaded model fits {words.machine}. "
                          f"{words.command} fit shows what fits.")
    first = "\n".join(line[len("start "):] for line in lines if line.startswith("start "))
    out = [f"CARL starts the server in router mode at http://{plan.get(HOST)}:{plan.get(PORT)}. {plan.net_note}"]
    if llama_note:
        out.append(llama_note)
    out += list(ROUTER_NOTES)
    out.append(f"The models (their settings: {tilde(preset_file, home)}):")
    for line in lines:
        if line.startswith("model "):
            name = line[len("model "):].split(" ", 1)[0]
            head = f"model {name} "
            text = line[len(head):] if line.startswith(head) else line
            out.append(f"  {name}: {text}{' (loads first)' if name == first else ''}")
        elif line.startswith("skip "):
            out.append(f"  {line[len('skip '):]} This model is left out.")
    return out

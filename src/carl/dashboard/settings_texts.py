"""The Settings tab's texts that name the computer: the help of each setting, a row value in the glossary's words, and
the Memory status sentences. They ask the platform's words (ui/words.py) when a text is drawn, never at import, so they
belong to the dashboard and not to the application service that holds the rows (rule R5)."""
from __future__ import annotations

import re
from typing import Iterator, Mapping, Optional

from ..application.settings_service import SWA_NAMES, FitInfo, gib_pair, plan_words
from ..domain.catalog import thinking
from ..domain.config import server_rows
from ..domain.config.settings import GPU_KEYS
from ..domain.shared.ansi import GRN, R, RED
from ..domain.shared.constants import VM_ADDR
from ..domain.shared.units import MIB, ctx_label, duration, memory, tokens
from ..ui.words import computer, computer_cap, kv_name, net_name, plural, spec_name, vm_software

_VM_NET_HELP = f" {{This}} and the VM: a {{vm}} Fusion VM can connect too ({VM_ADDR})."     # only where --vm exists
_NET_HELP = ("Who can connect. {This} only: the default.{vm_net} An address: only that network interface. "
             "Other computers on that network can connect.")
SET_HELP = {
    "swa": "A Gemma model keeps a window of recent tokens per layer. full cache: CARL can restore saved sessions and "
           "prompts; it needs more memory. window cache: less memory; saved sessions cannot be restored. auto: the "
           "full cache when it fits. One setting for every Gemma model (also in Settings > Caching).",
    "model": "The list has every model: the catalogue, the models folder and your Hugging Face downloads. Auto starts "
             "Auto fit's choice for {this}. The Auto fit panel (A) tells why. To add a model, use the Models panel: "
             "it downloads any GGUF file from Hugging Face.",
    "quant": "A quantization is the model's file at a number of bits per weight. Fewer bits: a smaller file that loads "
             "faster and leaves more memory for the context, and answers that are a little less exact. The Model row "
             "chooses the model; this row chooses its file. Model auto chooses both. A start needs the file: to "
             "download one, use the Models panel (]), then d.",
    "goal": "The goal of Auto fit. everyday: the fast models first (MoE models). They are usually good enough. hard "
            "code: the dense models first. They write better code, but they are slower. A MoE model when the only "
            "dense models that fit rank below it. Auto fit takes a small fast dense model only when no other model of "
            "its rank or better fits. Otherwise it names it as the fast option.",
    "scope": "The models that Auto fit looks at. catalogue: every catalogue model. Auto fit then offers the download. "
             "Until the download is complete, a start uses the best downloaded model. downloaded only: only the models "
             "on {this}.",
    "kv": "The memory that holds the context of all slots. q4 is smaller and is the tested default. q8 recalls text far "
          "back more exactly, but it uses about 2 × the memory.",
    "ctx": "How many tokens one slot can hold. The colour tells how fast {this} reads a full context again: green is "
           "fast, yellow is slow, red is very slow.",
    "slots": "A slot is a place in the server for one session. auto: 2 slots when two fit (the main session and a "
             "subagent at the same time), else 1. 3 or 4: more subagents at the same time. The list offers 3 and 4 only "
             "when they fit {this}. More slots write more tokens in total, but each slot is slower.",
    "spec": "The server guesses tokens ahead and checks them: more speed, the same answer. n-gram copies text that is "
            "in the context already, a long run at a time (fast for edits). MTP: a small predictor guesses new text. "
            "Guesses: the tokens MTP guesses for each step (n-gram has none to set). Auto-tune measures the best choice.",
    "specn": "The tokens MTP guesses for each step. On Metal, more guesses do not make dense models faster.",
    "cache": "The RAM cache: the server keeps sessions that leave a slot in RAM while it runs. When a session comes "
             "back, the server does not read it all again. auto: CARL sizes it from the free memory at each start.",
    "net": _NET_HELP,
    "api": "The port of the dashboard API: OpenCode and Pi on other computers sync and share the disk cache through it. "
           "auto: the server's port + 1, else the next free port. The Connect tab and the sync tell the clients.",
    "temp": "How random the answers are. The recommended value comes from the model card (Qwen in thinking mode and "
            "Gemma 4: 1.0). 0.6 gives more exact code on some models.",
    "presence": "0 is the default. 1.5 gives fewer repeat loops (the Qwen card, for general use).",
    "top_k": "The model picks from the k most likely tokens. Qwen: 20. Gemma 4: 64. 0 = off.",
    "top_p": "The model picks from the most likely tokens up to this total probability. 0.95 for most models.",
    "min_p": "The model ignores the tokens below min p × the top probability. Qwen: 0.",
    "repeat": "The repeat penalty. Qwen: 1.0 (off). Use presence instead.",
    "ub": "The batch that the GPU works on in one step (-ub). auto: the model's own, which Auto-tune measures (512 to "
          "4096) and saves, else 2048 on a PC and 512 on a Mac. A PC reads a prompt about 2.5 times faster at 2048 "
          "than at 512; Metal reads about the same at each. A larger -ub needs more GPU memory, so with auto a start "
          "lowers it before it takes a smaller context. A size here is for this model only, and a start keeps it; it "
          "is asked about once when it differs from the tuned one. A -ub typed in the server options wins over this "
          "row.",
    "ckpt": "Saved points of the recurrent state, per slot. Each one uses about 63 MiB on the 35B and 150 MiB on the "
            "27B. More checkpoints did not help in the Phase 6 test.",
    "ckstep": "The smallest number of tokens between two checkpoints. In the Phase 6 test, 1024 and 4096 gave the same "
              "result.",
    "keep": "The GPU memory CARL keeps free on each GPU, for the display and other apps (GiB). A start fits the model "
            f"in the rest. {GPU_KEYS['vram_keep'].default:.1f} is the default.",   # the schema's default
    "backend": "The llama.cpp build CARL starts: auto chooses (CUDA on NVIDIA); the choice cpu "
               "uses no GPU.",
    "cpu_moe": "For a MoE model: the number of layers whose experts stay in the RAM. A model larger than the GPU memory "
               "runs that way, slower. auto: CARL finds the number at each start. 0: every expert on the GPU.",
    "shared": "For a GPU that shares the RAM (an APU): the GiB a model may use on it. 0: CARL works it out from the "
              "driver's figures.",
}


class HelpTexts(Mapping[str, str]):
    """SET_HELP: the help of each setting, a capital at each sentence. The computer and the VM software are the
    platform's words ({this}, {This}, {vm}): they are filled in when a text is asked for, not at import. Where
    there is no VM software ({vm_net}), the sentence about the VM is left out."""

    def __init__(self, raw: Mapping[str, str]) -> None:
        self._raw = dict(raw)

    def __getitem__(self, key: str) -> str:
        vm = vm_software()
        text = self._raw[key].replace("{vm_net}", _VM_NET_HELP if vm else "")
        text = text.replace("{this}", computer()).replace("{This}", computer_cap()).replace("{vm}", vm)
        return re.sub(r"(^|[.!?] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)    # sentences: a capital

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)


SET_HELP = HelpTexts(SET_HELP)


def shown_value(key: str, v: object, long: bool = False) -> str:
    """A row value on screen in the glossary's words: contexts as 96K (long: '96K tokens per slot'), the
    context memory type as q4 (small), the RAM cache in GiB, the network as who can connect."""
    if key == "ctx":
        return ctx_label(v) + (" tokens per slot" if long and str(v).isdigit() else "")
    if key == "kv":
        return kv_name(v, short=not long)
    if key == "cache":
        return memory(int(str(v)) * MIB) if str(v).isdigit() and int(str(v)) else str(v)
    if key == "net":
        return net_name(v)
    if key == "spec":
        return spec_name(v)
    if key == "swa":
        return SWA_NAMES.get(str(v), str(v))
    if key == "thinking_coder" and v == thinking.MAIN:
        return "same as main"
    return str(v)


def fit_sentence(f: FitInfo) -> str:
    """The Memory status as plain sentences (coloured ✓ / ✗)."""
    if not f.known:
        return f"{RED}✗{R} {f.name} is not a known model."
    if not f.downloaded:
        return f"{RED}✗{R} {f.name} is not downloaded. To download it: the Models panel (]), then d."
    if f.error:
        return f"{RED}✗{R} CARL cannot check the memory: {f.error}."
    need, limit = gib_pair(f.need, f.limit)
    plan = plan_words(f.slots, f.ctx)
    if f.gpus > 1:
        gpus = f"The {f.gpus} GPUs give {limit} together."
        if f.fits:
            return f"{GRN}✓{R} It fits. This model with {plan} needs {need}, split over the {f.gpus} GPUs. {gpus}"
        more = (f" With {plural(f.slots, 'slot')}, at most {tokens(f.largest)} tokens per slot fit." if f.largest
                else " The weights alone do not fit.")
        return (f"{RED}✗{R} It does not fit. This model with {plan} needs {need} as a split over the {f.gpus} GPUs "
                f"(each has a compute buffer of its own). {gpus}{more}")
    if f.fits:
        return f"{GRN}✓{R} It fits. This model with {plan} needs {need}. {computer_cap()} gives the GPU {limit}."
    more = (f" With {plural(f.slots, 'slot')}, at most {tokens(f.largest)} tokens per slot fit." if f.largest
            else " The weights alone do not fit.")
    return (f"{RED}✗{R} It does not fit. This model with {plan} needs {need}. {computer_cap()} gives the GPU "
            f"{limit}.{more}")


def number_words(key: str, v: float) -> str:
    """One value of a number row in its unit: 96K, 2.0 GiB (the RAM cache's MiB), 0.5 GiB, 1 min 30 s, 10 GB."""
    if key == "ctx":
        return tokens(int(v))
    if key == "cache":
        return memory(int(v) * MIB) if v else "0 (off)"
    if key in ("keep", "shared"):
        return f"{server_rows.number_text(key, v)} GiB"
    if key == "auto_s":
        return duration(v, secs=True)
    if key == "disk_gb":
        return f"{v:g} GB"
    return server_rows.number_text(key, v)


def range_ends(key: str, top: Optional[float] = None, full: bool = True) -> str:
    """A number row's least and largest value at this level (B6): "10 s to 60 min" (full), "15 s to 3 min" (the simple
    level's presets)."""
    vals = server_rows.ladder(key, top, full)
    if not vals:
        return ""
    unit = {"keep": " GiB", "shared": " GiB", "disk_gb": " GB"}.get(key, "")
    hi = "60 min" if key == "auto_s" and vals[-1] == 3600 else number_words(key, vals[-1])
    return f"{number_words(key, vals[0])[:-len(unit) or None]} to {hi}"


def range_words(key: str, top: Optional[float] = None, full: bool = True) -> str:
    """A number row's range for ABOUT (B6): at the full level its least and largest value (the top of the model or the
    computer, when it has one) and the steps ← → take: "0 to 2, by 0.05"; "10 s to 60 min: by 10 s up to 2 min, by 30 s
    up to 10 min, then by 1 min". At the simple level the presets: "15 s, 30 s, 45 s, 1 min, ..."."""
    vals = server_rows.ladder(key, top, full)
    if not vals:
        return ""
    ends = range_ends(key, top, full)
    if key == "ctx":
        return f"{ends}: {', '.join(tokens(int(v)) for v in vals)}. Type any size from 4K"     # at both levels
    if not full and key in server_rows.PRESETS:
        return ", ".join(number_words(key, v) for v in vals)
    if key in ("ub", "ckstep"):
        return f"{ends}, the powers of two"
    if key == "top_k":
        return f"{ends}: 10, 20, 40, 64, 100, then by 50 (0 is off)"
    runs = [(end, step) for end, step in server_rows.STEPS[key] if end > vals[0]]
    if len(runs) == 1 or vals[-1] <= runs[0][0]:
        return f"{ends}, by {number_words(key, runs[0][1]) if key != 'cache' else memory(int(runs[0][1]) * MIB)}"
    parts = [f"by {number_words(key, step)} up to {number_words(key, end)}" for end, step in runs[:-1]
             if end < vals[-1]]
    return f"{ends}: {', '.join(parts)}, then by {number_words(key, runs[len(parts)][1])}"

"""What each setting does, and its name in the dashboard: the text of `carl config show` (cli/text/config.py). The
settings themselves (their values, ranges and defaults) are domain/config/settings.py, the one source."""
from __future__ import annotations

from typing import Dict

from ..domain.config.settings import GPU_KEYS, SettingSpec
from ..domain.shared.constants import DEFAULT_CTX
from ..domain.shared.platform_words import PlatformWords

# What each setting does, and its name in the dashboard (carl config show). {machine}: the platform's word for the
# computer (key_help fills it in).
KEY_HELP: Dict[str, str] = {
    "llama.model": "The model that the server starts (Settings > Server). auto: the model that Auto fit chooses.",
    "llama.auto_goal": "The goal of Auto fit: everyday (fast models first) or hard-code (dense models first: "
                       "better code, slower).",
    "llama.auto_fit": "The candidates of Auto fit: catalogue (all catalogue models) or downloaded (downloaded "
                      "models only).",
    "llama.mode": "single (single model: you change it in the dashboard) or router (router mode: OpenCode and Pi "
                  "switch the model). Settings > Router.",
    "llama.net": "The network: local (only {machine} can use the server) or vm (the VM network too).",
    "llama.host": "One address of {machine} to listen on. It wins over llama.net. Empty: llama.net decides.",
    "llama.cache_ram": "The size of the RAM cache in MiB. auto: the free memory after the model, from 1 GiB to "
                       "8 GiB.",
    "llama.ub": "The old setting for the physical batch size (-ub) of every model. Each model has its own -ub now: "
                "the Batch size row, or models.NAME.ub. auto (the default): the start picks it. A model with an "
                "Auto-tune result uses that result: a start in a terminal asks about a size here once, a script or "
                "router mode ignores it and says so. A model without one uses this size, else 2048 on a PC and 512 on "
                "a Mac. A -ub in llama.extra_args wins.",
    "llama.load_reserve_gib": "The free memory (GiB) that load mode none leaves: a start reads the weights into the "
                              "memory when the free memory holds what the server needs and this much more; else the "
                              "weights stay mapped from the file.",
    "llama.batch": "The logical batch size of llama-server (-b).",
    "llama.ckpt": "The number of context checkpoints for each slot (--ctx-checkpoints). Models with a recurrent "
                  "state (Qwen3.6, Qwen3.8) use them.",
    "llama.ckpt_step": "The smallest number of tokens between two checkpoints.",
    "llama.think_toggle": "A chat template that lets OpenCode turn thinking off. false: the template of the model "
                          "file.",
    "llama.vision": "Pass the vision projector of the model to llama-server (--mmproj) when it is downloaded "
                    "({command} download NAME --vision). false: a text start. The projector costs about 1 GB of "
                    "memory.",
    "llama.extra_args": "More flags for llama-server, as a list of words.",
    "llama.path": "The folder of the llama.cpp build that CARL runs. CARL_LLAMA_DIR wins over it. {command} "
                  "engine install sets it. Empty: CARL_LLAMA_DIR, else the newest build that it installed, else PATH.",
    "llama.n_cpu_moe": "For a MoE model: the number of layers whose experts stay in the RAM (--n-cpu-moe). auto: "
                       "CARL plans it.",
    "paths.models_dir": "The models folder. MODELS_DIR wins over it.",
    "cache.disk_gb": "The largest size of the disk cache, in GB (Settings > Caching).",
    "cache.prefix": "Save the prompt of each agent (saved prompts).",
    "cache.sessions": "Save the sessions (saved sessions).",
    "cache.save": "When CARL saves a session: auto (when it would take cache.auto_s seconds to read again), turn "
                  "(after each turn), switch or stop (CARL writes only a record).",
    "cache.auto_s": "For cache.save auto: the seconds of reading that make CARL save a session.",
    "cache.share": "Store a saved session as the changes to its saved prompt (less disk).",
    "cache.keep_slots": "Save the slots before a restart that CARL makes and put them back after it (clients without "
                        "CARL's plugins).",
    "cache.swa": "Sliding-window models (Gemma): full (full cache), window (window cache) or auto (the full cache "
                 "when it fits).",
    "cache.move": "Models whose chat template CARL does not know (not Qwen, not Gemma 4): off (the default) leaves "
                  "the parts of the prompt that change per project (the folder, the date, AGENTS.md) in the system "
                  "prompt; auto moves them to the start of your first message, so one saved prompt serves every "
                  "project (Settings > Caching > Other templates).",
    "delegation.gate": "Advanced, not recommended. N: OpenCode and Pi stop the main agent's write that makes its Nth "
                       "new file in a turn and tell it to use the coder; 0 (the default) is off. The dashboard sets "
                       "it (Connect > Setup, g).",
    "gpu.vram_keep": "The GPU memory (GiB) that CARL keeps free on each GPU, for the desktop's growth while the server "
                     f"runs ({GPU_KEYS['vram_keep'].default:g}; the backend's own part is in the estimate). "
                     "CARL_VRAM_KEEP_GIB wins over it.",
    "gpu.backend": "The llama.cpp build: auto (CARL chooses: CUDA for NVIDIA), cuda "
                   "or cpu.",
    "gpu.devices": "The GPUs that a model uses, by their numbers in {command} gpu (for example 0 1). Empty: every "
                   "GPU.",
    "gpu.shared_limit_gib": "The memory (GiB) that a model may use on a GPU that shares the RAM (an APU), its own "
                            "memory included. 0 (the default): CARL works it out. CARL_SHARED_LIMIT_GIB wins over "
                            "it.",
    "engine.name": "The engine that serves the models: auto (the engine on the model's card, else llama.cpp) or the name "
                   "of an engine. CARL_ENGINE wins over it.",
    "models.NAME.kv": "The context memory type: q4_0 (q4), q8_0 (q8) or f16.",
    "models.NAME.ctx": f"The context of each slot in tokens (96k = {DEFAULT_CTX}).",
    "models.NAME.slots": "The number of slots, or auto (2 slots when they fit).",
    "models.NAME.spec": "The speculation: none, ngram-mod (n-gram), draft-mtp (MTP), draft-mtp,ngram-mod "
                        "(MTP + n-gram), or a drafter file of another kind: draft-dflash, draft-dspark, draft-eagle3 "
                        "(alone or with ,ngram-mod). A kind needs its file in the model's folder.",
    "models.NAME.spec_n": "The number of guesses of the drafter for each step (n-gram does not use it).",
    "models.NAME.temp": "The sampling temperature.",
    "models.NAME.top_p": "The sampling top_p.",
    "models.NAME.top_k": "The sampling top_k.",
    "models.NAME.min_p": "The sampling min_p.",
    "models.NAME.presence": "The presence penalty.",
    "models.NAME.repeat": "The repetition penalty (1.0 = off).",
    "models.NAME.alias": "The model name that the server shows to the clients. Empty: the model name.",
    "models.NAME.ub": "The batch size (-ub) you chose for this model (Settings > Server, Batch size), which a start "
                      "keeps. auto: the start picks it, also over llama.ub: the model's Auto-tune result, else 2048 on "
                      "a PC and 512 on a Mac, made smaller before the context is. Unset: llama.ub, else as auto. A -ub "
                      "in llama.extra_args wins.",
    "models.NAME.n_cpu_moe": "For a MoE model: the number of this model's layers whose experts stay in the RAM "
                             "(--n-cpu-moe; Settings > Server, Experts in RAM). auto: CARL plans it. Unset: "
                             "llama.n_cpu_moe.",
    "models.NAME.thinking_main": "How the main session of OpenCode and Pi thinks with this model (Settings > Server, "
                                 "Main thinking): off, on, or a level for a model with effort levels (low, medium, "
                                 "xhigh). Default: on; low for a model with effort levels. The clients use it after "
                                 "the next update.",
    "models.NAME.thinking_coder": "How the coder thinks with this model, also in its test session (Settings > Server, "
                                  "Coder thinking): main (the default: as the main session), off, on, or a level "
                                  "(low, medium, xhigh). On is medium for a model with effort levels. Off: use it only "
                                  "with a full spec.",
}



def key_help(key: str, words: PlatformWords) -> str:
    """What a setting does (KEY_HELP), with the platform's words."""
    return KEY_HELP.get(key, "").replace("{machine}", words.machine).replace("{command}", words.command)


def value_text(spec: SettingSpec) -> str:
    """The values a key takes and its default, in words."""
    kinds = {"int": "a whole number", "float": "a number", "intauto": "a whole number or auto", "bool": "true or false",
             "list": "a list", "str": "text", "choice": " | ".join(spec.choices)}
    text = kinds.get(spec.kind, spec.kind)
    if spec.min is not None and spec.max is not None and spec.kind in ("int", "float", "intauto"):
        text += f" from {spec.min} to {spec.max}"
    default = spec.default
    shown = ("empty" if default in ("", [], None) else "true" if default is True else "false" if default is False
             else str(default))
    return f"{text}. Default: {shown}."

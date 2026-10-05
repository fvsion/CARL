#!/usr/bin/env python3
"""The help of ./carl.sh and its commands, and the text layout of the CLI.

    python3 tools/carl_help.py [TOPIC]      # what ./carl.sh -h and ./carl.sh help TOPIC print

Every topic is written in ASD-STE100 simplified technical English with the names of
docs/phase21/glossary.md. The text wraps to the terminal: COLUMNS when it is set, else the
terminal's width, else 80 columns (at most MAX_WIDTH, at least MIN_WIDTH). tools/carl.py
and tools/llama-fit.py use width() and the wrap helpers for their output too.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from typing import Dict, List, Optional, Sequence, Tuple, Union

MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH = 40, 120, 80
HERE = os.path.dirname(os.path.abspath(__file__))


def width() -> int:
    """The width of the output: COLUMNS, else the terminal's width, else 80; in MIN_WIDTH..MAX_WIDTH."""
    raw = os.environ.get("COLUMNS", "")
    if raw.isdigit() and int(raw) > 0:
        w = int(raw)
    elif sys.stdout.isatty():
        w = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    else:
        w = DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, w))


NBSP = "\u00a0"
_UNIT = re.compile(r"(\d) (s|min|h|ms|GB|GiB|MB|MiB|KiB|kB|K|tokens|tok/s|%|slots?|guess(?:es)?)(?=[\s.,;:)]|$)")


def wrap(text: str, w: int, indent: str = "", hang: Optional[str] = None) -> List[str]:
    """A paragraph as lines of at most w characters: the first line starts with indent, the
    others with hang (default: indent). Words are never cut, and a number stays on the line of
    its unit ("2 min"); a word longer than a line stays whole on its own line."""
    hang = indent if hang is None else hang
    lines = textwrap.wrap(_UNIT.sub(lambda m: m.group(1) + NBSP + m.group(2), text), w, initial_indent=indent,
                          subsequent_indent=hang, break_long_words=False, break_on_hyphens=False)
    return [line.replace(NBSP, " ") for line in lines] or [indent.rstrip()]


def columns(rows: Sequence[Tuple[str, str]], w: int, indent: str = "  ", max_term: int = 24, gap: int = 2,
            term_width: Optional[int] = None) -> List[str]:
    """Term and description pairs: the description in a column beside the term, wrapped; a term
    longer than the column puts its description on the next line. A narrow screen puts every
    description under its term. term_width: the column of the terms (default: the longest
    term, at most max_term)."""
    tw = term_width if term_width is not None else min(max((len(t) for t, _ in rows), default=0), max_term)
    col = len(indent) + tw + gap
    out: List[str] = []
    if w - col < 30:                                # too narrow for two columns
        for term, desc in rows:
            out.append(indent + term)
            if desc:
                out += wrap(desc, w, indent + "    ")
        return out
    for term, desc in rows:
        if not desc:
            out.append(indent + term)
        elif len(term) > tw:
            out.append(indent + term)
            out += wrap(desc, w, " " * col)
        else:
            out += wrap(desc, w, f"{indent}{term:<{tw}}{' ' * gap}", " " * col)
    return out


# ---------------------------------------------------------------- the topics
# A topic is a list of blocks: ("h", heading), ("p", paragraph), ("d", [(term, description)]),
# ("c", [command lines, not wrapped: keep them shorter than 78 characters]).
Block = Tuple[str, Union[str, List[Tuple[str, str]], List[str]]]


def _llama_default(var: str, fallback: str) -> str:
    """The default of VAR in host/serve-llama.sh (VAR="${VAR:-default}")."""
    try:
        with open(os.path.join(HERE, "..", "host", "serve-llama.sh"), encoding="utf-8") as f:
            m = re.search(rf'^{var}="\${{{var}:-([^}}]*)}}"', f.read(), re.M)
        return m.group(1) if m else fallback
    except OSError:
        return fallback


def topics(cmd: str) -> Dict[str, List[Block]]:
    """Every help topic; cmd is how the user starts CARL (./carl.sh)."""
    c = cmd
    network: List[Block] = [
        ("h", f"NETWORK (for llama, and for {c} without a command)"),
        ("d", [("--local", "The server listens on 127.0.0.1. Only OpenCode and Pi on this Mac can use it. "
                           "This is the default."),
               ("--vm", "The server listens on 192.168.42.1, the network of VMware Fusion. A VM can use it. "
                        "VMware Fusion must run."),
               ("--host ADDR", "The server listens on one address of this Mac, for example its LAN address. "
                               "Every computer that can reach this address can use the server. Only the API key "
                               "protects it. CARL refuses 0.0.0.0."),
               ("(no option)", "CARL uses your setting llama.net (Settings > Server in the dashboard), "
                               "else --local.")]),
    ]
    mode: List[Block] = [
        ("h", f"MODE (for llama, and for {c} without a command)"),
        ("d", [("--single", "Single model: one model runs. You change it in the dashboard. This is the default."),
               ("--router", "Router mode: OpenCode and Pi can switch the model. The server loads one model at a "
                            "time. Each switch loads the model again (30 s to 2 min). The RAM cache is empty after "
                            "a switch. OpenCode and Pi restore their sessions from the disk cache. Your setting "
                            "llama.mode keeps the choice (Settings > Router).")]),
    ]
    return {
        "main": [
            ("p", "CARL (Can't Afford Remote LLMs) runs a language model on this Mac for OpenCode and Pi. "
                  "OpenCode and Pi can run on this Mac, in a VM or on other computers. One server runs at a time."),
            ("h", "USAGE"),
            ("d", [(c, "Open the dashboard. If no server runs, CARL starts the server with your saved settings."),
                   (f"{c} COMMAND [OPTIONS]", "Run a command."),
                   (f"{c} help COMMAND", f"Show the help for a command. {c} COMMAND --help does the same.")]),
            ("h", "THE SERVER AND THE DASHBOARD"),
            ("d", [("llama [OPTIONS]", "Start the server (llama.cpp) on port 8080. The dashboard opens in this "
                                       "terminal. More: help llama."),
                   ("dashboard", "Open the dashboard and do not start a server. --no-start does the same."),
                   ("monitor", "Open the dashboard for a server that runs.")]),
            ("h", "MODELS"),
            ("d", [("models", "List the models, their size and their download status."),
                   ("fit", "Show the model that Auto fit chooses for this Mac, and why. Show the largest context "
                           "of each model."),
                   ("download NAME", "Download a model and check it. NAME can also be default (the model of Auto "
                                     "fit), all, or a Hugging Face file (hf:OWNER/REPO/FILE.gguf)."),
                   ("verify [NAME...]", "Check the size and the SHA-256 of downloaded models."),
                   ("delete NAME", "Delete a downloaded model."),
                   ("card NAME", "Show the card of a model: what the model is good for. You can edit the card of a "
                                 "custom model."),
                   ("tune NAME", "Auto-tune: CARL measures the model on this Mac and finds its best settings. "
                                 "It takes 5 to 10 min.")]),
            ("h", "CLIENTS AND SETTINGS"),
            ("d", [("install", "Install OpenCode and Pi, and connect them to this server."),
                   ("config", "Show or change your settings (config.json)."),
                   ("cache", "Show the disk cache, make it smaller, or clear it."),
                   ("push", "Send the client config to other computers.")]),
            *network,
            *mode,
            ("h", "MORE HELP"),
            ("d", [(f"{c} help env", "The environment variables of the server."),
                   (f"{c} help tuning", "Where to find the speed and memory figures for this Mac.")]),
            ("p", f"A first option that starts with \"-\" means llama: {c} --vm is {c} llama --vm. "
                  "The documents: README.md (overview), USERGUIDE.md (how to), reference/README.md (details)."),
        ],
        "llama": [
            ("p", "Start the server (llama.cpp) on port 8080. In a terminal, the server runs in the background "
                  "and the dashboard opens in this terminal. When you quit the dashboard, it asks if the server "
                  "must stop."),
            ("h", "USAGE"),
            ("c", [f"{c} llama [OPTIONS] [LLAMA-SERVER FLAGS...]", f"{c} [OPTIONS]"]),
            ("h", "OPTIONS"),
            ("d", [("--model NAME|PATH", f"The model: a model name ({c} models) or the path of a .gguf file. "
                                         "Default: your setting llama.model, else the model of Auto fit."),
                   ("--ctx N|Nk", "The context of each slot in tokens, from 4K to 256K (K = 1024). Default: 96K. "
                                  "A larger context reads prompts more slowly. Run the installer again after a change "
                                  f"({c} install --config-only)."),
                   ("--slots N|auto", "The number of slots, from 1 to 4. A slot holds one session. With 2 slots, "
                                      "the main session and a subagent work at the same time. auto: 2 slots when "
                                      "they fit, else 1. Each slot has its own context, so the context memory is "
                                      "slots × context."),
                   ("--kv q4|q8", "The context memory type. q4 (the default) uses less memory. q8 uses more memory. "
                                  "--q4 and --q8 do the same."),
                   ("--local, --vm, --host", "The network (see below)."),
                   ("--single, --router", "The mode (see below)."),
                   ("--help-adv", "Show every flag of llama-server. CARL gives the other flags to llama-server."),
                   ("-h, --help", "Show this help.")]),
            *network,
            *mode,
            ("h", "WHAT CARL DOES AT THE START"),
            ("p", "CARL gets the settings in this order: flags, then environment variables, then your settings "
                  "(config.json), then the result of Auto-tune on this Mac, then the catalogue. The first lines of "
                  "the output show each setting and where it comes from."),
            ("p", "CARL refuses a start if the port is in use, or if another large process (possibly a model) is in "
                  "memory. Two models do not fit, and the second one can stop the Mac."),
            ("p", "CARL refuses a start that needs more memory than the GPU memory limit. The message gives the "
                  f"largest context that fits, and the model that Auto fit chooses. {c} fit shows what fits."),
            ("p", "CARL makes the API key ~/.config/carl/api-key at the first start. The RAM cache gets the memory "
                  "that is free after the model, from 1 GiB to 8 GiB."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} llama", f"{c} llama --model qwen3.6-35b-a3b", f"{c} llama --kv q8 --ctx 128k",
                   f"{c} llama --slots 1          # one session, less memory",
                   f"{c} llama --model ~/models/gguf/Other.gguf", "SPEC=none MONITOR=0 " + f"{c} llama"]),
            ("p", f"The environment variables: {c} help env."),
        ],
        "dashboard": [
            ("p", "The dashboard shows the server, its sessions and its settings. You can start, stop and "
                  "change the server in the dashboard."),
            ("h", "USAGE"),
            ("d", [(c, "Open the dashboard. If no server runs, CARL starts the server with your saved settings."),
                   (f"{c} dashboard", "Open the dashboard. If no server runs, it opens without a server. Settings "
                                      "(tab 5) can start one. --no-start does the same."),
                   (f"{c} monitor [--port N]", "Open the dashboard for a server that runs (default port 8080).")]),
            ("h", "OPTIONS"),
            ("d", [("--port N", "The port of the server (default 8080)."),
                   ("--once", "Show the dashboard one time as text, then stop."),
                   ("--expand", "Show every card with all its details.")]),
            ("p", "In the dashboard, press ? to see all the keys. Press q to quit. When a server runs, the dashboard "
                  "asks if the server must stop."),
        ],
        "install": [
            ("p", "Install OpenCode and Pi for your user, then write their configs for this server. "
                  "For a VM or another computer, copy the client folder there and run its install.sh."),
            ("h", "USAGE"),
            ("c", [f"{c} install [both|opencode|pi] [OPTIONS]"]),
            ("h", "OPTIONS"),
            ("d", [("both, opencode, pi", "The clients to install (default: both)."),
                   ("--clients-only", "Install the clients. Do not write the configs."),
                   ("--config-only", "Write the configs again. Do this after a change of the model list, the "
                                     "context or the slots."),
                   ("--local", "Connect the clients to the server on this Mac (the default)."),
                   ("--vm [HOST]", "Connect the clients to the server on the VM network (default 192.168.42.1)."),
                   ("--host ADDR", "Connect the clients to the server at this address."),
                   ("--port N", "The port of the server (default 8080)."),
                   ("--key-file FILE", "Read the API key from FILE."),
                   ("--key KEY", "The API key. Your shell history keeps it, so use --key-file if you can.")]),
            ("p", "The installer also reads environment variables, for example NO_CACHE=1 (no disk cache) or "
                  "CODER=0 (no coder subagent). client/install.sh --help lists them."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} install", f"{c} install pi", f"{c} install --config-only"]),
        ],
        "models": [
            ("p", "List every model that CARL knows: the catalogue (CARL's tested models), the files in the "
                  "models folder, and your Hugging Face downloads (custom models)."),
            ("h", "USAGE"),
            ("c", [f"{c} models"]),
            ("p", "The list shows for each model its size on disk (GB), its download status and where it comes "
                  "from. Download status: downloaded, not downloaded, partial (the download stopped: run it again "
                  "to continue) or bad file (the SHA-256 check failed)."),
            ("p", "The models folder is ~/models/gguf (your setting paths.models_dir, or MODELS_DIR). "
                  "Each .gguf file in it is a model. The catalogue is host/catalog.json. Custom models and the "
                  "results of Auto-tune are in ~/.config/carl/models.json."),
            ("p", f"More: {c} help download, {c} help card, {c} help fit."),
        ],
        "download": [
            ("p", "Download a model into the models folder and check its SHA-256. If a download stops, run the "
                  "command again: it continues. A file with a bad SHA-256 gets the name FILE.bad."),
            ("h", "USAGE"),
            ("d", [(f"{c} download NAME...", "Download catalogue models."),
                   (f"{c} download default", "Download the model that Auto fit chooses for this Mac."),
                   (f"{c} download all", "Download every catalogue model."),
                   (f"{c} download hf:OWNER/REPO", "List the GGUF files of a Hugging Face repository."),
                   (f"{c} download hf:OWNER/REPO/FILE.gguf", "Download one file from Hugging Face. CARL checks it "
                                                             "against the SHA-256 of Hugging Face. A huggingface.co "
                                                             "address is also correct.")]),
            ("p", "A Gemma 4 model has an MTP drafter in a separate file (mtp-*.gguf). CARL downloads, checks and "
                  "deletes it with the model. Without the MTP drafter, the speculation is n-gram only."),
            ("p", "A Gemma 4 file from another Hugging Face repository (for example a fine-tune) also gets an MTP "
                  "drafter: the drafter of the catalogue model with the same size. CARL finds the size in the file. "
                  f"For a custom Gemma 4 model that you have already, {c} download NAME downloads only its drafter. "
                  "Auto-tune measures if the drafter makes the model faster."),
            ("p", "In the dashboard: Settings > Models, then h (Add from Hugging Face)."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} download gemma-4-12b", f"{c} download hf:Qwen/Qwen3-0.6B-GGUF",
                   f"{c} download hf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf"]),
        ],
        "verify": [
            ("p", "Check the size and the SHA-256 of downloaded models, and of their MTP drafters. Without a name, "
                  "CARL checks every downloaded model. A local file without a known SHA-256 is not checked."),
            ("h", "USAGE"),
            ("c", [f"{c} verify [NAME...]"]),
        ],
        "delete": [
            ("p", "Delete the file of a downloaded model, and its MTP drafter. CARL also forgets the results of "
                  "Auto-tune for the model. The catalogue entry stays, so you can download the model again."),
            ("h", "USAGE"),
            ("c", [f"{c} delete NAME"]),
            ("p", "Stop the server first if the model runs."),
        ],
        "card": [
            ("p", "A model card tells what a model is good for, why you use it, and its quality rank. The cards of "
                  "catalogue models are in host/catalog.json: you cannot change them. You can write the card of a "
                  "custom model (~/.config/carl/models.json)."),
            ("h", "USAGE"),
            ("d", [(f"{c} card NAME", "Show the card."),
                   (f"{c} card NAME set FIELD VALUE", "Set one field of the card of a custom model. CARL checks it "
                                                      "as it checks the catalogue."),
                   (f"{c} card NAME unset FIELD", "Remove one field.")]),
            ("h", "FIELDS"),
            ("d", [("label", "The name in the lists."),
                   ("role", "A short headline, 60 characters or less."),
                   ("good_for", "Tags, with commas between them: agent coding, hard code, chat & writing, "
                                "uncensored."),
                   ("why_use, trade_offs, hardware", "Text."),
                   ("abliterated", "yes or no: the safety training is removed."),
                   ("uncensored", "Text, for an abliterated model only."),
                   ("arch", "dense or moe."),
                   ("quant", "The quantization, for example Q4_K_M."),
                   ("rank", "The quality rank: 1 is the best."),
                   ("thinking", "on-off or effort."),
                   ("auto_fit", "yes or no: Auto fit can choose the model. The model needs a rank and an arch, "
                                "and it must not be abliterated."),
                   ("pick_instead", "MODEL=WHEN (one for each model), or a JSON list.")]),
            ("p", "In the dashboard: Settings > Models, select the model, then e (Edit card)."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} card gemma-4-12b", f'{c} card my-model set good_for "agent coding,hard code"',
                   f'{c} card my-model set pick_instead qwen3.8-27b="harder code"']),
        ],
        "fit": [
            ("p", "Show the model that Auto fit chooses for this Mac for each goal, and why. Then show, for each "
                  "model, the memory it needs and the largest context that fits. Use it before you download or "
                  "start a model."),
            ("h", "USAGE"),
            ("c", [f"{c} fit [OPTIONS]"]),
            ("h", "OPTIONS"),
            ("d", [("--ram GB", "Show what fits on a Mac with this much RAM (an estimate of its GPU memory limit)."),
                   ("--ctx N|Nk", "Also show the memory each model needs for this context."),
                   ("--slots N", "Calculate the largest context for N slots (default 1)."),
                   ("--goal G", "Explain this goal: everyday (fast first) or hard-code (better code, slower). "
                                "Default: your setting llama.auto_goal."),
                   ("--scope S", "The candidates: catalogue (all catalogue models) or downloaded. Default: your "
                                 "setting llama.auto_fit."),
                   ("--reserve-gb N", "The memory kept free for macOS and apps, in GiB (default 6; 10 when the VM "
                                      "network is up).")]),
            ("h", "HOW AUTO FIT CHOOSES"),
            ("p", "Auto fit looks only at stock models with a quality rank (1 is the best). It never chooses an "
                  "abliterated model. It chooses a custom model only when the card of the model says auto_fit yes. "
                  "The goal everyday takes the fast models first (MoE and small dense models). The goal hard-code "
                  "takes the dense models first."),
            ("p", "Auto fit wants two slots of 96K tokens (the main session and a subagent). If no model fits, it "
                  "tries one slot of 96K, then the largest context of 32K or more. The model must fit in the memory "
                  "that a model can use: the GPU memory limit, or the RAM minus the memory kept free, whichever is "
                  "smaller."),
            ("p", "Memory needed = weights (with an MTP drafter) + context memory + recurrent state + about 1 GiB of "
                  "buffers. These are estimates: keep some memory free."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} fit", f"{c} fit --ram 24", f"{c} fit --goal hard-code --scope downloaded",
                   f"{c} fit --ctx 128k --slots 2"]),
        ],
        "tune": [
            ("p", "Auto-tune measures a downloaded model on this Mac and finds its best settings: the speculation, "
                  "the context and the slots. It takes 5 to 10 min. The next start of the model uses the result, "
                  "unless your settings (config.json) set a value."),
            ("h", "USAGE"),
            ("c", [f"{c} tune NAME [OPTIONS]", f"{c} tune all [OPTIONS]"]),
            ("h", "OPTIONS"),
            ("d", [("NAME", "A downloaded model. all: every downloaded model, one after the other."),
                   ("--quick", "Do fewer tests (about 4 min)."),
                   ("--long", "Also read 128K and 192K tokens, and measure the write speed at each size "
                              "(10 to 40 min more)."),
                   ("--port N", "The port of the test server (default 8093)."),
                   ("--dry-run", "Measure and show the result, but do not save it.")]),
            ("h", "WHAT AUTO-TUNE DOES"),
            ("p", "1. It calculates the largest context that fits with 1 and 2 slots."),
            ("p", "2. It measures each speculation mode: none, n-gram, and MTP and MTP + n-gram with 1 to 4 guesses "
                  "(when the model has an MTP head or an MTP drafter). Each mode writes prose, new code and an edit, "
                  "two times. A mode with more parts must be 3% faster to win."),
            ("p", "3. It measures the read speed at 8K, 32K and 64K tokens. The time to read a full context again "
                  "gives the context zones of this Mac: fast (3 min or less), slow (10 min or less), very slow."),
            ("p", "4. It saves the result in ~/.config/carl/models.json."),
            ("p", "Auto-tune needs the GPU for itself. Stop the server first. The Auto-tune panel of the dashboard "
                  "(Settings > Auto-tune) stops the server for you."),
        ],
        "config": [
            ("p", "Your settings are in ~/.config/carl/config.json. The dashboard (Settings) changes them too. "
                  "A flag or an environment variable at the start wins over a setting."),
            ("h", "USAGE"),
            ("d", [(f"{c} config show", "Show the file and every setting with its default and what it does."),
                   (f"{c} config path", "Show where the file is."),
                   (f"{c} config get KEY", "Show one setting."),
                   (f"{c} config set KEY VALUE", "Change one setting."),
                   (f"{c} config unset KEY", "Remove one setting, so that CARL uses its default.")]),
            ("p", "A KEY has the form section.name, for example llama.net or cache.disk_gb. The settings of one "
                  "model have the form models.NAME.key, for example models.gemma-4-12b.ctx."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} config set llama.auto_goal hard-code", f"{c} config set models.qwen3.8-9b.ctx 128k",
                   f"{c} config unset llama.model"]),
        ],
        "cache": [
            ("p", "The disk cache keeps the saved prompts and the saved sessions of OpenCode and Pi on the disk "
                  "(~/.config/carl/slots). They stay after a stop, a restart or a model switch. In the dashboard: "
                  "Settings > Caching."),
            ("h", "USAGE"),
            ("d", [(f"{c} cache show", "Show what the disk cache holds and its size."),
                   (f"{c} cache trim", "Remove the oldest files until the disk cache is in its limit "
                                       "(cache.disk_gb)."),
                   (f"{c} cache clear", "Remove every file of the disk cache.")]),
        ],
        "push": [
            ("p", "Send the client config (the list of installed models) to OpenCode and Pi on other computers. "
                  "The dashboard sends it while it runs. The clients apply it at their next start."),
            ("h", "USAGE"),
            ("c", [f"{c} push"]),
        ],
        "env": [
            ("p", f"Environment variables for the server: VAR=value {c} llama. A flag wins over a variable."),
            ("h", "THE SERVER"),
            ("d", [("CTX=98304", "The context of each slot in tokens."),
                   ("SLOTS", "The number of slots, as --slots."),
                   ("KV=q4_0", "The context memory type: q4_0, q8_0 or f16."),
                   ("KV_K, KV_V", "The type of each half of the context memory. Keep them the same: different "
                                  "types read prompts about 5 times more slowly."),
                   ("SPEC, SPEC_N", "The speculation mode (none, ngram-mod, draft-mtp, draft-mtp,ngram-mod) and "
                                    "the number of guesses. Default: the settings of the model."),
                   ("TEMP, TOP_P, TOP_K, MIN_P", "The sampling. Default: 1.0, 0.95, 20, 0 (the values of Qwen for "
                                                 "thinking). PRESENCE=0 is the presence penalty."),
                   ("CACHE_RAM", "The size of the RAM cache in MiB. Default: the free memory, from 1 GiB to 8 GiB."),
                   ("RESERVE_GB", "The memory (GiB) that CARL keeps free for macOS and apps. Default: 6, or 10 when "
                                  "the VM network is up."),
                   (f"UB={_llama_default('UB', '512')}", "The physical batch size of llama-server (-ub)."),
                   ("FIT_CHECK=0", "Start also when the setup needs more memory than the GPU memory limit. The "
                                   "model can fail to load, or the Mac can become very slow."),
                   ("MODEL=PATH, ALIAS", "The model file, and the model name that the server shows."),
                   ("THINK_TOGGLE=1", "A chat template that lets OpenCode turn thinking off. 0: the template of the "
                                      "model file.")]),
            ("h", "THE NETWORK"),
            ("d", [("NET=local", "local or vm, as --local and --vm."),
                   ("VM_HOST=192.168.42.1", "The address that --vm uses."),
                   ("HOST", "One address to listen on. It wins over NET. CARL refuses 0.0.0.0."),
                   ("PORT=8080", "The port of the server."),
                   ("API_KEY_FILE", "The API key file. Default: ~/.config/carl/api-key.")]),
            ("h", "THE START"),
            ("d", [("MONITOR=1", "Show the dashboard in this terminal. 0: the server runs in the foreground, with "
                                 "no dashboard."),
                   ("KEEP_AWAKE=1", "Keep the Mac awake while the server runs. 0: off."),
                   ("LOG_FILE", "The log file. Default: ~/models/logs/llama-server-DATE.log. none: no log file."),
                   ("ALLOW_SECOND_MODEL=1", "Start also when a process of more than 8 GB (BIG_GB) is in memory. "
                                            "A second model can stop the Mac."),
                   ("SETTINGS_FILE=none", "Do not use ~/.config/carl/config.json."),
                   ("MODELS_DIR", "The models folder. Default: ~/models/gguf (your setting paths.models_dir)."),
                   ("CARL_CONF_DIR", "Another settings folder (default ~/.config/carl), for example for tests.")]),
        ],
        "tuning": [
            ("p", "The speed and the memory of a model depend on the Mac. CARL measures them on your Mac:"),
            ("d", [(f"{c} fit", "What fits this Mac: the memory each model needs and its largest context."),
                   (f"{c} tune NAME", "Auto-tune: the speeds of the model on this Mac and its best settings."),
                   ("Settings > Auto-tune", "The results of Auto-tune in the dashboard.")]),
            ("p", "Some rules from the measurements: q4 context memory has the same write speed as q8 and uses less "
                  "memory. Different K and V types read prompts about 5 times more slowly. A larger context reads "
                  "a full prompt more slowly. MTP + n-gram is fastest for most models; n-gram is fast for edits."),
            ("p", "The figures: USERGUIDE.md and reference/ (memory.md and the measurements)."),
        ],
    }


ALIASES = {"monitor": "dashboard", "--no-start": "dashboard", "list": "models", "help": "main", "": "main"}


def render(topic: str, cmd: Optional[str] = None, w: Optional[int] = None) -> str:
    """One topic as text that fits w columns (KeyError: no such topic)."""
    c = cmd or os.environ.get("CARL_CMD") or "./carl.sh"
    w = w or width()
    blocks = topics(c)[ALIASES.get(topic, topic)]
    terms = [len(r[0]) for _, body in blocks if not isinstance(body, str) for r in body if isinstance(r, tuple)]
    tw = min(max(terms, default=0), 24)         # one term column for the whole topic
    out: List[str] = []
    seen_heading = False
    for kind, body in blocks:
        if isinstance(body, str):
            if kind == "h":
                if out and out[-1]:
                    out.append("")
                out.append(body)
                seen_heading = True
            else:
                out += wrap(body, w, "  " if seen_heading else "")
                out.append("")
        elif kind == "d":
            out += columns([r for r in body if isinstance(r, tuple)], w, term_width=tw)
            out.append("")
        else:
            for line in body:                      # a command line that is too long goes on, indented
                if isinstance(line, str):
                    out += [f"  {line}"] if len(line) + 2 <= w else wrap(line, w, "  ", "      ")
            out.append("")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def names() -> List[str]:
    """Every topic and alias that `help` accepts."""
    return sorted(set(topics("./carl.sh")) | set(ALIASES) - {""})


def main(argv: List[str]) -> int:
    topic = argv[0] if argv else "main"
    try:
        print(render(topic))
    except KeyError:
        cmd = os.environ.get("CARL_CMD") or "./carl.sh"
        print(f"error: there is no help for '{topic}'. Run {cmd} -h to see the commands.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

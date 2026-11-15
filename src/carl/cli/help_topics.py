"""The help of carl and its commands: every help topic, the one help source (`carl help TOPIC`, `COMMAND --help`,
and the --help of the old tools that forward to the commands). A command that this computer's help does not show
(gpu on a Mac: cli/commands/__init__.py) is left out of the main topic too.

Every topic is written in ASD-STE100 simplified technical English with the names of reference/glossary.md, and with
the platform words of this computer (domain/shared/platform_words.py; the Mac's words are the texts' own). The text
wraps to the width that ui/layout.py gives.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple, Union

from ..domain.config.settings import GPU_KEYS, LLAMA_KEYS, MODEL_KEYS
from ..domain.server.engines import ENGINES
from ..domain.shared.constants import DEFAULT_CTX as CTX, DEFAULT_PORT as PORT, UBATCH as UB, VM_ADDR as VM
from ..domain.shared.platform_words import MACOS, PlatformWords
from ..ui.layout import columns, wrap

# ---------------------------------------------------------------- the topics
# A topic is a list of blocks: ("h", heading), ("p", paragraph), ("d", [(term, description)]),
# ("c", [command lines, not wrapped: keep them shorter than 78 characters]).
Block = Tuple[str, Union[str, List[Tuple[str, str]], List[str]]]


def topics(words: PlatformWords = MACOS, shown: Sequence[str] = (), port: int = PORT,
           plugins: Sequence[Tuple[str, str]] = ()) -> Dict[str, List[Block]]:
    """Every help topic, in the platform words of this computer (words.command: how the user starts CARL). shown:
    the commands of OFF_MAC that this computer's help shows (the main topic lists them); port: the port a start uses
    now (the variable PORT, else the default; A46); plugins: the commands of the plugins that are on that the main
    topic lists, (name, what it does), in a group of their own (none: no group)."""
    c, m = words.command, words.machine
    pc = "gpu" in shown                 # a computer with discrete GPUs: its help names the GPU settings and --vram
    # The commands that only the help of a computer that is not a Mac shows (OFF_MAC).
    off_mac: List[Tuple[str, str]] = [
        ("gpu", f"Show the GPUs that CARL finds, the memory a model can use, and the llama.cpp build for {m}.")]
    network: List[Block] = [
        ("h", f"NETWORK (for start, and for {c} without a command)"),
        ("d", [("--local", f"The server listens on 127.0.0.1. Only OpenCode and Pi on {m} can use it. "
                           "This is the default."),
               ("--vm", f"The server listens on {VM}, the network of VMware Fusion. A VM can use it. "
                        "VMware Fusion must run."),
               ("--host ADDR", f"The server listens on one address of {m}, for example its LAN address. "
                               "Every computer that can reach this address can use the server. Only the API key "
                               "protects it. CARL refuses 0.0.0.0."),
               ("(no option)", "CARL uses your setting llama.net (Settings > Server in the dashboard), "
                               "else --local.")]),
    ]
    mode: List[Block] = [
        ("h", f"MODE (for start, and for {c} without a command)"),
        ("d", [("--single", "Single model: one model runs. You change it in the dashboard. This is the default."),
               ("--router", "Router mode: OpenCode and Pi can switch the model. The server loads one model at a "
                            "time. Each switch loads the model again (30 s to 2 min). The RAM cache is empty after "
                            "a switch. When you switch back to a model, OpenCode and Pi restore its sessions from the "
                            "disk cache. Your setting "
                            "llama.mode keeps the choice (Settings > Router).")]),
    ]
    return {
        "main": [
            ("p", f"CARL (Can't Afford Remote LLMs) runs a language model on {m} for OpenCode and Pi. "
                  f"OpenCode and Pi can run on {m}, in a VM or on other computers. One server runs at a time."),
            ("h", "USAGE"),
            ("d", [(c, "Open the dashboard. If no server runs, CARL starts the server with your saved settings."),
                   (f"{c} COMMAND [OPTIONS]", "Run a command."),
                   (f"{c} help COMMAND", f"Show the help for a command. {c} COMMAND --help does the same."),
                   (f"{c} --version", "Show the version of CARL.")]),
            ("h", "THE SERVER AND THE DASHBOARD"),
            ("d", [("start [OPTIONS]", f"Start the server (llama.cpp) on port {port}. The dashboard opens in this "
                                       "terminal. More: help start."),
                   ("engine COMMAND", "Show the version of llama.cpp (version) or every flag of llama-server (help)."
                                      + (" install installs llama.cpp." if pc else "") + " More: help engine."),
                   ("dashboard", "Open the dashboard and do not start a server. --no-start does the same."),
                   ("monitor", "Open the dashboard for a server that runs."),
                   ("log [-f]", "Show the server log as plain text. -f follows it."),
                   *[row for row in off_mac if row[0] in shown]]),
            ("h", "MODELS"),
            ("d", [("models", "List the models, their size and their download status."),
                   ("fit", f"Show the model that Auto fit chooses for {m}, and why. Show the largest context "
                           "of each model."),
                   ("download NAME", "Download a model and check it. NAME can also be default (the model of Auto "
                                     "fit), all, or a Hugging Face file (hf:OWNER/REPO/FILE.gguf)."),
                   ("hf token", "Show, set or clear your Hugging Face access token. A gated repo needs it. More: "
                                "help hf."),
                   ("verify [NAME...]", "Check the size and the SHA-256 of downloaded models."),
                   ("delete NAME", "Delete a downloaded model."),
                   ("card NAME", "Show the card of a model: what the model is good for. You can edit the card of a "
                                 "custom model."),
                   ("tune NAME", f"Auto-tune: CARL measures the model on {m} and finds its best settings. "
                                 "It takes 5 to 10 min.")]),
            ("h", "CLIENTS AND SETTINGS"),
            ("d", [("install", f"Install OpenCode and Pi on {m}, and connect them to this server."),
                   ("package", "Make the client package: a zip for OpenCode and Pi on another computer."),
                   ("config", "Show or change your settings (config.json)."),
                   ("cache", "Show the disk cache, make it smaller, or clear it."),
                   ("push", "Send the client config to other computers.")]),
            *([("h", "PLUGINS"), ("d", list(plugins))] if plugins else []),
            *network,
            *mode,
            ("h", "MORE HELP"),
            ("d", [(f"{c} help env", "The environment variables of the server."),
                   (f"{c} help tuning", f"Where to find the speed and memory figures for {m}.")]),
            ("p", f"A first option that starts with \"-\" means start: {c} --vm is {c} start --vm. "
                  "The documents: README.md (overview), USERGUIDE.md (how to), reference/README.md (details)."),
        ],
        "start": [
            ("p", f"Start the server (llama.cpp) on port {port}. In a terminal, the server runs in the background "
                  "and the dashboard opens in this terminal. When you quit the dashboard, it asks if the server "
                  "must stop."),
            ("h", "USAGE"),
            ("c", [f"{c} start [OPTIONS] [LLAMA-SERVER FLAGS...]", f"{c} [OPTIONS]"]),
            ("h", "OPTIONS"),
            ("d", [("--model NAME|PATH", f"The model: a model name ({c} models) or the path of a .gguf file. "
                                         "Default: your setting llama.model, else the model of Auto fit."),
                   ("--quant Q", "The quantization of a model that has several (for example Q4_K_M, or IQ3 for "
                                 "UD-IQ3_XXS). NAME:Q does the same. Default: the one you chose last, else the one "
                                 "Auto-tune ran on last, else the largest that fits."),
                   ("--ctx N|Nk", "The context of each slot in tokens, from 4K to 256K (K = 1024). Default: 96K "
                                  "(Auto fit starts at 128K on an NVIDIA GPU when 128K fits). "
                                  "A larger context reads prompts more slowly. Run the installer again after a change "
                                  f"({c} install --config-only)."),
                   ("--slots N|auto", "The number of slots, from 1 to 4. A slot holds one session. With 2 slots, "
                                      "the main session and a subagent work at the same time. auto: 2 slots when "
                                      "they fit, else 1. Each slot has its own context, so the context memory is "
                                      "slots × context."),
                   ("--kv q4|q8", "The context memory type. q4 (the default) uses less memory. q8 uses more memory. "
                                  "--q4 and --q8 do the same. On the CPU alone (no GPU, or a CPU build of "
                                  "llama.cpp) a start that nobody told takes f16, which reads prompts much faster "
                                  "there, when the free RAM covers it, else q8, else q4. The start line says which."),
                   ("--local, --vm, --host", "The network (see below)."),
                   ("--port N", f"The port of the server (default {port}; the environment's PORT does the same)."),
                   ("--single, --router", "The mode (see below)."),
                   ("LLAMA-SERVER FLAGS", f"CARL gives every other flag to llama-server. {c} engine help shows them."),
                   ("-h, --help", "Show this help.")]),
            *network,
            *mode,
            ("h", "WHAT CARL DOES AT THE START"),
            ("p", "CARL gets the settings in this order: flags, then environment variables, then your settings "
                  f"(config.json), then the result of Auto-tune on {m}, then the catalogue. The first lines of "
                  "the output show each setting and where it comes from."),
            ("p", "CARL refuses a start if the port is in use, or if another large process (possibly a model) is in "
                  f"memory. Two models do not fit, and the second one can stop {words.the_machine}."),
            ("p", "CARL refuses a start that needs more memory than the GPU memory limit. The message gives the "
                  f"largest context that fits, and the model that Auto fit chooses. {c} fit shows what fits."),
            ("p", "CARL makes the API key ~/.config/carl/api-key at the first start. The RAM cache gets the memory "
                  "that is free after the model, from 1 GiB to 8 GiB."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} start", f"{c} start --model NAME", f"{c} start --kv q8 --ctx 128k",
                   f"{c} start --slots 1          # one session, less memory",
                   f"{c} start --model NAME --quant Q4_K_M",
                   f"{c} start --model ~/models/gguf/Other/Q4_K_M/Other-Q4_K_M.gguf",
                   "SPEC=none MONITOR=0 " + f"{c} start"]),
            ("p", f"The environment variables: {c} help env."),
        ],
        "dashboard": [
            ("p", "The dashboard shows the server, its sessions and its settings. You can start, stop and "
                  "change the server in the dashboard."),
            ("h", "USAGE"),
            ("d", [(c, "Open the dashboard. If no server runs, CARL starts the server with your saved settings."),
                   (f"{c} dashboard", "Open the dashboard. If no server runs, it opens without a server. Settings "
                                      "(tab 5) can start one. --no-start does the same."),
                   (f"{c} monitor [--port N]", f"Open the dashboard for a server that runs (default port {PORT}).")]),
            ("h", "OPTIONS"),
            ("d", [("--port N", f"The port of the server (default {PORT})."),
                   ("--once", "Show the dashboard one time as text, then stop."),
                   ("--expand", "Show every card with all its details.")]),
            ("p", "In the dashboard, press ? to see all the keys. Press q to quit. When a server runs, the dashboard "
                  "asks if the server must stop."),
        ],
        "install": [
            ("p", f"Install OpenCode and Pi on {m} and connect them to this server. This runs client/setup, the "
                  "same setup as on another computer. For another computer or a VM, make the client package "
                  f"({c} package), copy it there, unzip it and run ./setup."),
            ("h", "USAGE"),
            ("c", [f"{c} install [both|opencode|pi] [OPTIONS]"]),
            ("p", "The setup asks one time which clients and which options you want: the coder subagent, the "
                  "browser tools, web search (the search queries leave this computer) and LSP. Press Enter to keep "
                  "the value in brackets. The defaults are the choices of your last setup. Then it installs OpenCode "
                  "and Pi when they are missing or older (into ~/.local, no sudo). It writes their configs, keeps "
                  "your settings and makes a backup of each file that it changes."),
            ("h", "OPTIONS"),
            ("d", [("both, opencode, pi", "The clients (default: both, or the choice of your last setup)."),
                   ("-y, --yes", "Take the defaults. Ask no questions."),
                   ("--coder auto|on|off", "The coder subagent. auto: on when the server runs 2 or more slots."),
                   ("--browser on|off", "The browser tools."),
                   ("--web-search WHICH", "exa, parallel or off."),
                   ("--lsp on|off", "LSP in OpenCode."),
                   ("--clients-only", "Install or update the clients. Do not write the configs."),
                   ("--config-only", "Write the configs again. Do this after a change of the model list, the "
                                     "context or the slots."),
                   ("--local", f"Connect the clients to the server on {m} (the default)."),
                   ("--vm [HOST]", f"Connect the clients to the server on the VM network (default {VM})."),
                   ("--host ADDR", "Connect the clients to the server at this address."),
                   ("--port N", f"The port of the server (default {PORT})."),
                   ("--key-file FILE", "Read the API key from FILE.")]),
            ("p", "The setup also reads environment variables, for example NO_CACHE=1 (no disk cache) or "
                  "WEB_SEARCH=off. client/setup --help lists them."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} install", f"{c} install --yes pi", f"{c} install --config-only"]),
        ],
        "package": [
            ("p", "Make the client package: one zip for OpenCode and Pi on another computer (macOS or Linux). It "
                  "holds the client folder, the address and the API key of the server, the list of installed "
                  "models and the setup. CARL writes it to dist/carl-client-VERSION-HOST.zip in the CARL folder "
                  "and shows the path."),
            ("h", "USAGE"),
            ("c", [f"{c} package [--anyway] [--out DIR]"]),
            ("h", "OPTIONS"),
            ("d", [("--out DIR", "Write the zip to DIR, not to dist/."),
                   ("--anyway", f"Make the package also when the server serves only {m}."),
                   ("-h, --help", "Show this help.")]),
            ("h", "THE KEY"),
            ("p", "The zip holds the API key of the server, so keep it secret. Its mode is 0600. After you copy it, "
                  "delete it (rm with the path that CARL shows). On the other computer, delete the zip after you "
                  "unzip it. The key file in the folder keeps the mode 0600."),
            ("h", "ON THE OTHER COMPUTER"),
            ("c", ["unzip carl-client-VERSION-HOST.zip", "cd carl-client && ./setup"]),
            ("p", "On a Mac, a double click on setup.command does the same. To update, make a new package, unzip it "
                  "over the old folder (unzip -o) and run ./setup again."),
            ("h", "WHEN CARL MAKES NO PACKAGE"),
            ("p", f"When the server serves only {m} (the network local, the address 127.0.0.1), other computers "
                  "cannot reach it. CARL then tells you how to change the network and changes nothing: in the "
                  f"dashboard, Settings > Server > Network, or {c} config set llama.net vm. Then start the server "
                  "again, so that client/remote.json has the new address. CARL also makes no package before the "
                  "first start of the server: the start writes client/remote.json and client/api-key."),
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
                  "A model with several quantizations is listed once, each quantization under its name; the default "
                  "one is marked. CARL reads only gguf/, in the folders of this layout and the .gguf files directly in "
                  "it: another folder in ~/models (mlx/, your own) is not a model list and is not read. "
                  "Each model has its own folder in it with a folder for each quantization "
                  "(for example Model/Q4_K_M/Model-Q4_K_M.gguf), and beside them a folder for each kind of drafter "
                  "(mtp/ for the MTP drafters, dflash/, dspark/, eagle3/), vision/ for its vision projector, "
                  "template/ for its chat template, imatrix/ for an importance matrix and, later, lora/ for its "
                  "LoRA adapters. A .gguf file directly in the models folder is a model too, and CARL moves no file by "
                  "itself: the list says how to move it. "
                  "The catalogue is built into CARL. Custom models and the results of Auto-tune are in "
                  "~/.config/carl/models.json."),
            ("p", f"More: {c} help download, {c} help card, {c} help fit."),
        ],
        "download": [
            ("p", "Download a model into the folder of its quantization in its own folder in the models folder (for "
                  "example Model/Q4_K_M/) and check its SHA-256. If a download stops, run the command again: it "
                  "continues. A file with a bad SHA-256 gets the name FILE.bad."),
            ("h", "USAGE"),
            ("d", [(f"{c} download NAME...", "Download catalogue models. NAME:Q takes a quantization (for example "
                                              "NAME:IQ3)."),
                   (f"{c} download NAME --vision", "Also download the vision projector (about 1 GB). Without the "
                                                   "flag the projector comes only when llama.vision is on."),
                   (f"{c} download default", f"Download the model that Auto fit chooses for {m}."),
                   (f"{c} download all", "Download every catalogue model."),
                   (f"{c} download hf:OWNER/REPO", "List the GGUF files of a Hugging Face repository."),
                   (f"{c} download hf:OWNER/REPO/FILE.gguf", "Download one file from Hugging Face. CARL checks it "
                                                             "against the SHA-256 of Hugging Face. A huggingface.co "
                                                             "address is also correct.")]),
            ("p", "A Gemma 4 model has an MTP drafter in a separate file (mtp-*.gguf), in the mtp folder of the model. "
                  "CARL downloads, checks and deletes it with the model. Without the MTP drafter, the speculation "
                  "is n-gram only."),
            ("p", "A catalogue model can have more drafters: DFlash (dflash/), DSpark (dspark/) and EAGLE-3 "
                  "(eagle3/), each in the folder of its kind. CARL downloads and checks them with the model. Auto-tune "
                  "tries a downloaded drafter when your llama.cpp supports its kind; if it does not, the drafter is not "
                  "used and Auto-tune says so. The vision projector is in vision/; a start passes it when llama.vision "
                  "is on."),
            ("p", "A Gemma 4 file from another Hugging Face repository (for example a fine-tune) also gets an MTP "
                  "drafter: the drafter of the catalogue model with the same size. CARL finds the size in the file. "
                  f"For a custom Gemma 4 model that you have already, {c} download NAME downloads only its drafter. "
                  "Auto-tune measures if the drafter makes the model faster."),
            ("p", "In the dashboard: Settings > Models, then h (Add from Hugging Face)."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} download NAME", f"{c} download hf:Qwen/Qwen3-0.6B-GGUF",
                   f"{c} download hf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf"]),
        ],
        "hf": [
            ("p", "Some Hugging Face repos are gated: they answer 401 until you accept their terms on their page and "
                  "give an access token. Make a token on https://huggingface.co/settings/tokens (it needs the right "
                  "to read repos). CARL keeps your token in the place where your system keeps secrets, and sends it "
                  "to huggingface.co and its download hosts only: for the file list, the download and the header "
                  "read before a download."),
            ("h", "USAGE"),
            ("d", [(f"{c} hf token", "Show if a token is set, where it comes from and its last 4 characters. CARL "
                                     "never shows the whole token."),
                   (f"{c} hf token set", "Keep a token. CARL reads it from standard input when you pipe it in, else "
                                         "it asks for it and shows nothing as you type. A token is never an "
                                         "argument: a process list shows every argument."),
                   (f"{c} hf token clear", "Remove the token that CARL keeps.")]),
            ("h", "WHERE THE TOKEN IS KEPT"),
            ("d", [("macOS", "The Keychain."),
                   ("Windows", "The Credential Manager. Else (in an ssh session, which it does not reach) a file in the settings "
                               "folder that only you, SYSTEM and the Administrators can open."),
                   ("Linux", "The Secret Service, through secret-tool, when it is installed. Else a file with the "
                             "mode 0600 in the settings folder (~/.config/carl/hf-token). CARL also uses the file "
                             "when the Keychain or the Secret Service refuses.")]),
            ("p", "The token is never in config.json, in a log or in a URL. The environment variable HF_TOKEN wins "
                  "over the token that CARL keeps. CARL reads the file of the Hugging Face tools "
                  "(~/.cache/huggingface/token) when it has no token. CARL does not change the variable or that "
                  "file. In the dashboard: Settings > Models, Hugging Face token."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} hf token", f"{c} hf token set", f"{c} hf token clear"]),
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
                  "catalogue models are built into CARL: you cannot change them. You "
                  "can write the card of a custom model (~/.config/carl/models.json)."),
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
                   ("pick_instead", "MODEL=WHEN (one for each model), or a JSON list."),
                   *([("engine", f"The engine that serves the model: {' or '.join(ENGINES)}. Default: "
                                 f"{ENGINES[0]}. The setting engine.name overrides it.")] if len(ENGINES) > 1 else [])]),
            ("p", "In the dashboard: Settings > Models, select the model, then e (Edit card)."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} card NAME", f'{c} card my-model set good_for "agent coding,hard code"',
                   f'{c} card my-model set pick_instead other-model="harder code"']),
        ],
        "fit": [
            ("p", f"Show the model that Auto fit chooses for {m} for each goal, and why. Then show, for each "
                  "model, the memory it needs and the largest context that fits. Use it before you download or "
                  "start a model."),
            ("h", "USAGE"),
            ("c", [f"{c} fit [OPTIONS] [NAME...]"]),
            ("h", "OPTIONS"),
            ("d", [("NAME", "Measure these models with llama-server's own fit, and show what it says of each. The table "
                            "shows the answer, and keeps it."),
                   ("--ram GB", f"Show what fits on {words.a_machine} with this much RAM (an estimate of its GPU "
                                "memory limit)."),
                   ("--ctx N|Nk", "Also show the memory each model needs for this context."),
                   ("--slots N", "Calculate the largest context for N slots (default 1)."),
                   ("--goal G", "Explain this goal: everyday (fast first) or hard-code (better code, slower). "
                                "Default: your setting llama.auto_goal."),
                   ("--scope S", "The candidates: catalogue (all catalogue models) or downloaded. Default: your "
                                 "setting llama.auto_fit."),
                   ("--reserve-gb N", f"The memory kept free for {words.os_and_apps}, in GiB (default 6; 10 when the "
                                      "VM network is up)."),
                   *([("--vram GIB[,GIB]", "Show what fits on GPUs with this much memory, for example 12,8 (each "
                                           "less the part that CARL keeps free, gpu.vram_keep). A model is split over "
                                           "several GPUs by layers.")] if pc else [])]),
            ("h", "HOW AUTO FIT CHOOSES"),
            ("p", "Auto fit looks only at stock models with a quality rank (1 is the best). It never chooses an "
                  "abliterated model. It chooses a custom model only when the card of the model says auto_fit yes. "
                  "The goal everyday takes the fast models first (MoE models). The goal hard-code takes the dense "
                  "models first, and a MoE model when the only dense models that fit rank below it. Auto fit takes a "
                  "small fast dense model (the Gemma 4 E4B) only when no other model of its rank or better fits. Otherwise it "
                  "names it as the fast option, and you choose it by name."),
            ("p", "Auto fit tries these setups in this order, on every Mac: 2 slots of 96K tokens, 2 slots of 64K, "
                  "2 slots of 48K (the main session and a subagent), then 1 slot of 96K, 64K or 48K. On a PC with an "
                  "NVIDIA GPU it starts at 128K: 2 slots of 128K, 96K, 64K or 48K, then 1 slot of 128K, 96K, 64K or "
                  "48K. It takes the first setup that a model fits, with the memory that is free now, so a busy "
                  "desktop costs the setup and nothing else. It never offers less than 48K. The model must fit in the memory "
                  "that a model can use: the GPU memory limit, or the RAM minus the memory kept free, whichever is "
                  "smaller."),
            ("p", "When MTP does not fit a setup, Auto fit uses n-gram speculation only, before it removes a slot or "
                  "makes the context smaller. The output says so. A start does the same."),
            ("p", "Memory needed = weights (the whole file; an MTP drafter while MTP runs) + context memory + "
                  "recurrent state + compute buffers (by the batch size, -ub, and the context) + with MTP its draft "
                  "context + 0.75%. These are estimates: keep some memory free."),
            ("p", "The figures in the table are CARL's estimate, marked ~, except for Auto fit's choices and the models "
                  "that you name: llama-server's own fit measures those, on the model file or, before a download, on a "
                  "stub of its header (the first 24 MB). The table keeps every answer, so a later table shows it without "
                  "a run. In the dashboard, m measures a model in the Auto fit panel and in the Models panel."),
            ("h", "EXAMPLES"),
            ("c", [f"{c} fit", f"{c} fit NAME", f"{c} fit --ram 24", f"{c} fit --goal hard-code --scope downloaded",
                   f"{c} fit --ctx 128k --slots 2"]),
        ],
        "tune": [
            ("p", f"Auto-tune measures a downloaded model on {m} and finds its best settings: the speculation, "
                  "the batch size, the context and the slots. It takes 10 to 20 min. The next start of the model uses the "
                  "result, unless your settings (config.json) set a value."),
            ("h", "USAGE"),
            ("c", [f"{c} tune NAME [OPTIONS]", f"{c} tune all [OPTIONS]"]),
            ("h", "OPTIONS"),
            ("d", [("NAME", "A downloaded model. all: every downloaded model, one after the other."),
                   ("--quick", "Do fewer tests (about 5-10 min)."),
                   ("--long", "Also read 128K and 192K tokens, and measure the write speed at each size "
                              "(10 to 40 min more)."),
                   ("--port N", "The port of the test server (default 8093)."),
                   ("--dry-run", "Measure and show the result, but do not save it.")]),
            ("h", "WHAT AUTO-TUNE DOES"),
            ("p", "1. It calculates the largest context that fits with 1 and 2 slots."),
            ("p", "2. It measures each speculation mode: none, n-gram, and MTP and MTP + n-gram: with 1 and 2 MTP "
                  "guesses for a model with an MTP head (quick: 1), with 1 to 4 for a model with an MTP drafter "
                  "(quick: 1 and 2). Each mode writes prose, new code and an edit, "
                  "two times. A mode with more parts must be 3% faster to win."),
            ("p", "3. It reads a prompt of 8K tokens at the batch sizes -ub 512, 1024, 2048 and 4096, the ones that "
                  "keep the slots and the context. The fastest wins when it is 5% faster than the default (2048 on a "
                  "PC, 512 on a Mac). A MoE model whose experts stay in the RAM then writes with the threads pinned "
                  "one per core and with the threads unpinned. Pinned is used only when it is 3% faster."),
            ("p", "4. It measures the read speed at 8K, 32K and 64K tokens. The time to read a full context again "
                  f"gives the context zones of {m}: fast (3 min or less), slow (10 min or less), very slow."),
            ("p", "5. It saves the result in ~/.config/carl/models.json, with the batch size and the load mode of "
                  "its starts."),
            ("p", "On the CPU alone (no GPU, or a CPU build of llama.cpp) llama.cpp reads prompts much faster with an "
                  "f16 context memory. Auto-tune then also measures the context memory types that fit the RAM (f16, "
                  "q8, q4) at the batch size of the model, and saves the fastest one: it must read or write 3% faster "
                  "than the one before it. Its read speed stops at 8K tokens, and the longer reads are an estimate "
                  "from the slope of the shorter ones."),
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
            *([("p", f"The GPU settings of {m}: gpu.vram_keep (the GPU memory that CARL keeps free on each GPU, "
                     "default 0.5 GiB), gpu.backend (the llama.cpp build), gpu.devices (the GPUs that a model "
                     "uses) and gpu.shared_limit_gib (the memory that a model may use on a GPU that shares the "
                     f"RAM, an APU). {c} gpu shows them.")] if pc else []),
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
            ("p", f"Environment variables for the server: VAR=value {c} start. A flag wins over a variable."),
            ("h", "THE SERVER"),
            ("d", [(f"CTX={CTX}", "The context of each slot in tokens."),
                   (str(MODEL_KEYS["slots"].env), "The number of slots, as --slots."),
                   ("KV=q4_0", "The context memory type: q4_0, q8_0 or f16."),
                   ("KV_K, KV_V", "The type of each half of the context memory. Keep them the same: different "
                                  "types read prompts about 5 times more slowly."),
                   ("SPEC, SPEC_N", "The speculation mode (none, ngram-mod, draft-mtp, draft-mtp,ngram-mod, "
                                    "draft-dflash, draft-dspark, draft-eagle3, or one of those three with ,ngram-mod) "
                                    "and the number of guesses. Default: the settings of the model."),
                   ("VISION=1", "Pass the vision projector of the model (--mmproj), when it is downloaded. 0: a text "
                                "start (the default)."),
                   ("TEMP, TOP_P, TOP_K, MIN_P", "The sampling. Default: the settings of the model (Qwen: 1.0, 0.95, 20, 0 for "
                                                 "thinking; Gemma 4: 1.0, 0.95, 64, 0). PRESENCE=0 is the presence penalty."),
                   (str(LLAMA_KEYS["cache_ram"].env), "The size of the RAM cache in MiB. Default: the free memory, from 1 GiB to 8 GiB."),
                   ("RESERVE_GB", f"The memory (GiB) that CARL keeps free for {words.os_and_apps}. Default: 6, or 10 "
                                  "when the VM network is up."),
                   (f"UB={UB}", "The physical batch size of llama-server (-ub). Default: the result of Auto-tune, "
                                "else 2048 on a PC and 512 on a Mac."),
                   ("LOAD_RESERVE_GIB=8", "The free memory (GiB) that load mode none leaves: the weights are read "
                                          "into the memory when the free memory holds them and this much more."),
                   ("FIT_CHECK=0", "Start also when the setup needs more memory than the GPU memory limit. The "
                                   f"model can fail to load, or {words.the_machine} can become very slow."),
                   ("MODEL=PATH, ALIAS", "The model file, and the model name that the server shows."),
                   ("THINK_TOGGLE=1", "A chat template that lets OpenCode turn thinking off. 0: the template of the "
                                      "model file.")]),
            ("h", "THE NETWORK"),
            ("d", [("NET=local", "local or vm, as --local and --vm."),
                   (f"VM_HOST={VM}", "The address that --vm uses."),
                   (str(LLAMA_KEYS["host"].env), "One address to listen on. It wins over NET. CARL refuses 0.0.0.0."),
                   (f"PORT={PORT}", "The port of the server."),
                   ("API_KEY_FILE", "The API key file. Default: ~/.config/carl/api-key.")]),
            ("h", "THE START"),
            ("d", [("MONITOR=1", "Show the dashboard in this terminal. 0: the server runs in the foreground, with "
                                 "no dashboard."),
                   ("KEEP_AWAKE=1", f"Keep {words.the_machine} awake while the server runs. 0: off."),
                   ("LOG_FILE", "The log file. Default: ~/models/logs/llama-server-DATE.log. none: no log file."),
                   ("ALLOW_SECOND_MODEL=1", "Start also when a process of more than 8 GiB (BIG_GB) is in memory. "
                                            f"A second model can stop {words.the_machine}."),
                   ("SETTINGS_FILE=none", "Do not use ~/.config/carl/config.json."),
                   ("MODELS_DIR", "The models folder. Default: ~/models/gguf (your setting paths.models_dir)."),
                   ("CARL_CONF_DIR", "Another settings folder (default ~/.config/carl), for example for tests.")]),
            *([("h", "THE GPUS"),
               ("d", [(LLAMA_KEYS["path"].env, "The folder of a llama.cpp build. It wins over llama.path."),
                      (GPU_KEYS["vram_keep"].env, "The GPU memory (GiB) that CARL keeps free on each GPU. Default: "
                                             f"{GPU_KEYS['vram_keep'].default:g}. It wins over gpu.vram_keep."),
                      (GPU_KEYS["backend"].env, "The llama.cpp build: auto, cuda or cpu. It wins over "
                                           "gpu.backend."),
                      (GPU_KEYS["devices"].env, f"The GPUs that a model uses, by their numbers in {c} gpu (0 1 or "
                                           "0,1). Default: every GPU."),
                      (GPU_KEYS["shared_limit_gib"].env, "The memory (GiB) that a model may use on a GPU that shares "
                                                         "the RAM (an APU). Default: 0, CARL works it out. It wins "
                                                         "over gpu.shared_limit_gib."),
                      (LLAMA_KEYS["n_cpu_moe"].env, "For a MoE model: the number of layers whose experts stay in the RAM. Default: "
                                    "auto."),
                      ("THREAD_PIN=on", "With experts in the RAM: on pins one thread to each physical core, off leaves "
                                         "llama.cpp's own threads. Default: what Auto-tune found for the model, else "
                                         "off. It wins over the model's record.")])] if pc else []),
        ],
        "log": [
            ("p", "Show the log of the server as plain text, without the colour codes. The server keeps its colours, "
                  "so its log file holds the codes too."),
            ("h", "USAGE"),
            ("d", [(f"{c} log", "Show the whole log of the server that runs (on PORT), else the latest log."),
                   (f"{c} log -f", "Follow the log: show its last 50 lines, then each new line (Ctrl-C stops)."),
                   (f"{c} log [-f] FILE", "Show another log file.")]),
            ("p", "The logs are in ~/models/logs. To see only the warnings and the errors: "
                  f"{c} log | grep -E \" W | E \"."),
        ],
        "gpu": [
            ("p", f"Show what CARL finds on {m}: the GPUs and their free memory, the memory a model can use (the free "
                  "GPU memory less the memory CARL keeps free), the llama.cpp build for this computer and the files "
                  "of its release, and the GPUs that llama-server sees."),
            ("h", "USAGE"),
            ("c", [f"{c} gpu [--offline]"]),
            ("d", [("--offline", "Do not ask GitHub for the files of the release.")]),
        ],
        "engine": [
            ("p", "An engine runs the models. llama.cpp (llama-server) is the engine of CARL today. "
                  f"The engine command shows its version and its flags{', and installs it' if pc else ''}."),
            ("h", "USAGE"),
            ("c", [*([f"{c} engine install [--backend BUILD] [--list]"] if pc else []), f"{c} engine version",
                   f"{c} engine help"]),
            ("d", [*([("install", f"Download the llama.cpp build that CARL is tested with, check it and install it "
                                  f"on {m}. CARL offers this when it finds no llama-server.")] if pc else []),
                   ("version", "Show the version of llama.cpp. When it is older than the version that CARL is tested "
                               f"with, CARL says so and tells you how to update it ({words.update_hint()})."),
                   ("help", f"Show every flag of llama-server. CARL gives the flags that it does not use to "
                            f"llama-server ({c} help start).")]),
            *([("h", "INSTALL"),
               ("p", "CARL checks each file against the SHA-256 in its own list and against GitHub's. The build goes "
                     "into the settings folder (llama.cpp/bNNNNN-BACKEND), and your setting llama.path points at it. "
                     "The previous build stays."),
               ("d", [("--backend BUILD", f"The build: auto (the build for the GPU of {m}, or your setting "
                                          "gpu.backend), cuda, cpu, cuda-13 or cuda-12. When the "
                                          "release has no such build for this computer, CARL takes the one it "
                                          "has, or tells you to set llama.path to a build of your own."),
                      ("--list", "Show the builds that CARL installed and your setting llama.path.")]),
               ("p", f"{c} gpu shows the build for this computer.")] if pc else []),
        ],
        "tuning": [
            ("p", "The speed and the memory of a model depend on the Mac. CARL measures them on your Mac:"),
            ("d", [(f"{c} fit", f"What fits {m}: the memory each model needs and its largest context."),
                   (f"{c} tune NAME", f"Auto-tune: the speeds of the model on {m} and its best settings."),
                   ("Settings > Auto-tune", "The results of Auto-tune in the dashboard.")]),
            ("p", "Some rules from the measurements: q4 context memory has the same write speed as q8 and uses less "
                  "memory. Different K and V types read prompts about 5 times more slowly. A larger context reads "
                  "a full prompt more slowly. MTP + n-gram is fastest for most models; n-gram is fast for edits."),
            ("p", "The figures: USERGUIDE.md and reference/ (memory.md and the measurements)."),
        ],
    }


ALIASES = {"monitor": "dashboard", "--no-start": "dashboard", "list": "models", "help": "main", "": "main"}
# The commands that only the help of a computer that is not a Mac shows (cli/commands/__init__.py declares where).
OFF_MAC = ("gpu",)


def render(topic: str, w: int, words: PlatformWords = MACOS, shown: Sequence[str] = (), port: int = PORT,
           plugins: Sequence[Tuple[str, str]] = ()) -> str:
    """One topic as text that fits w columns (KeyError: no such topic), in the platform words of this computer.
    shown: the commands of OFF_MAC that this computer's help shows; port: the port a start uses now; plugins: the
    plugins' commands the main topic lists."""
    blocks = topics(words, shown, port, plugins)[ALIASES.get(topic, topic)]
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


def is_main(topic: str) -> bool:
    """The topic is the main one (`carl -h`, `carl help`)."""
    return ALIASES.get(topic, topic) == "main"


def names(words: PlatformWords = MACOS) -> List[str]:
    """Every topic and alias that `help` accepts."""
    return sorted(set(topics(words)) | set(ALIASES) - {""})


def unknown_topic(topic: str, words: PlatformWords) -> str:
    """The error line of a topic that has no help (exit 2)."""
    return f"error: there is no help for '{topic}'. Run {words.command} -h to see the commands."

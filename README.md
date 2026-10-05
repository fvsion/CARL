<p align="center"><img src="assets/carl-face.png" alt="CARL" width="180"></p>

<h1 align="center">CARL</h1>

<h3 align="center"><b>C</b>an't <b>A</b>fford <b>R</b>emote <b>L</b>LMs</h3>

<p align="center"><i>AI Slop Coded LLM Runner, So You Can Code AI Slop Locally</i></p>

<p align="center">A local coding model runner on your Apple Silicon Mac, for <b>OpenCode</b> and <b>Pi</b>.<br>
One command starts the server and a live dashboard.</p>

---

## Install

**You need:** an Apple Silicon Mac, [Homebrew](https://brew.sh), `python3`, and 4.6–23 GB of free disk space for each model.

```bash
brew install llama.cpp aria2 ansifilter zstd
./carl.sh download default            # the best model for this Mac (resumable, checksum-verified)
./carl.sh install                     # OpenCode + Pi into ~/.local (no sudo), connected to the server
```

- If you skip the `brew install`, `./carl.sh` finds the missing tools and asks to install them.
- If you skip the download, `./carl.sh` asks to download the best model for this Mac.
- **Your own OpenCode and Pi settings stay.** The installer adds CARL next to them. It keeps a backup of each file that it changes (`FILE.before-carl`, `FILE.bak.<time>`).
- **The server serves this Mac only** (127.0.0.1). For a VM or another computer, see [Clients in the VM](USERGUIDE.md#clients-in-the-vm) and [Clients on another computer](USERGUIDE.md#clients-on-another-computer-or-another-vm-app).

## Use

```bash
./carl.sh                              # start the server and the dashboard
```

Then, in a second terminal, go to your project folder and start `opencode` or `pi` there. They work on the folder that you start them in.

```bash
cd ~/path/to/your/project
opencode
```

- **The first time,** CARL starts the model that **Auto fit** chooses for your Mac: the best stock model that holds two slots of 96K tokens. `./carl.sh fit` shows the choice and why.
- **The next time,** it starts llama.cpp with the settings that you saved.
- **If a server runs already,** the dashboard attaches to it.
- `./carl.sh -h` shows all commands. `./carl.sh help COMMAND` (or `./carl.sh COMMAND --help`) shows the help for one command.

## Models

| Model | For |
|---|---|
| `qwen3.6-35b-a3b` | The default: fast MoE, everyday agent coding (32 GB+) |
| `qwen3.8-27b` | The dense 27B: hard code, slower (32 GB+) |
| IQ3 and Q3 builds | 24 GB Macs |
| `gemma-4-e4b`, `gemma-4-12b` | 16 GB Macs: the E4B is fast, the 12B is better at code |
| `orcarouter-27b…`, `heretic-35b-a3b…` | Abliterated (uncensored) builds |
| `gemma-4-26b-a4b`, `gemma-4-31b` | Google's larger Gemma 4 models (32 GB+) |
| `qwen3.8-9b` | A small distilled Qwen, for more subagents at the same time |

- `./carl.sh models` lists the catalogue and each `.gguf` in `~/models/gguf`.
- `./carl.sh download hf:OWNER/REPO/FILE.gguf` gets any GGUF from Hugging Face.
- `./carl.sh tune NAME` measures the best settings for a model on your Mac.
- More: [Choosing a model](USERGUIDE.md#3-choosing-a-model), [Router mode](USERGUIDE.md#router-mode-switch-models-from-opencode-or-pi) (OpenCode and Pi change the model).

## The dashboard

The live state of the server: what it does now, the slots, the speed, the memory, and the requests.

![The CARL dashboard](assets/dashboard.gif)

- **Tabs:** Live, Connect (install the clients, send their config), Requests, Log, Settings.
- **Settings (tab 5):** six panels: Server, Models, Auto fit, Auto-tune, Router, Caching. Push `[` or `]` to change the panel.
- **Detail:** push `D` to change between simple and full detail. The dashboard keeps your choice.
- The footer shows the keys of the screen that you see. `?` shows all keys of that screen.
- More: [The dashboard](USERGUIDE.md#10-the-dashboard).

![The Settings tab](assets/settings.png)

## In OpenCode and Pi

- **Model:** `/models` in OpenCode, `/model` in Pi.
- **Thinking:** `/variants` (or ctrl+t) in OpenCode, the thinking level in Pi ([Thinking](USERGUIDE.md#4-thinking-on-off-and-effort)).
- **The coder:** large tasks go to a coder subagent in the background. Type `@coder TASK` in OpenCode to ask for it.
- **Tools:** web search, LSP, a browser subagent, background subagents ([Tools](USERGUIDE.md#tools-in-opencode-and-pi)). Web search sends the queries to Exa: `WEB_SEARCH=off ./carl.sh install` turns it off.

## Plugins

![OpenCode with the CARL plugins](assets/opencode-plugins.png)

`./carl.sh install` adds CARL's plugins to OpenCode and Pi:

- **Disk cache:** fast starts; sessions come back after a restart (OpenCode, Pi).
- **Coder subagent, in the background:** large tasks go to a specialist coder while the main session stays free (OpenCode, Pi).
- **Subagents panel** and **session switcher** in OpenCode's sidebar and prompt box.
- **Model check:** a warning when the model you pick is not the one the server runs (OpenCode).
- **`/carl`:** every CARL piece and its state, and the config sync (OpenCode, Pi).

What each one does and how to turn it off: [USERGUIDE.md, "CARL's plugins and extensions"](USERGUIDE.md#carls-plugins-and-extensions). How they work: [reference/plugins.md](reference/plugins.md).

## Read more

- [USERGUIDE.md](USERGUIDE.md): setup, daily use, models, thinking levels, the dashboard, troubleshooting.
- [reference/](reference/README.md): how it works, the caches, measurements, the files in this folder, and the design decisions.
- [CHANGELOG.md](CHANGELOG.md): what changed in each release.

<p align="center"><img src="assets/carl-face.png" alt="CARL" width="180"></p>

<h1 align="center">CARL</h1>

<h3 align="center"><b>C</b>an't <b>A</b>fford <b>R</b>emote <b>L</b>LMs</h3>

<p align="center"><i>AI Slop Coded LLM Runner, So You Can Code AI Slop Locally</i></p>

<p align="center">A local coding model runner on your Apple Silicon Mac, for <b>OpenCode</b> and <b>Pi</b>.<br>
One command starts the server and a live dashboard.</p>

---

## Install

**You need:** an Apple Silicon Mac, [Homebrew](https://brew.sh), `python3`, and 11–23 GB of free disk space for each model.

```bash
brew install llama.cpp aria2 ansifilter
./carl.sh download default            # the best model for this Mac (resumable, checksum-verified)
./carl.sh install                     # OpenCode + Pi into ~/.local (no sudo), connected to the server
```

If you skip the `brew install`, `./carl.sh` finds the missing tools and asks to install them. If you skip the download, `./carl.sh` asks to download the default model for this Mac.

**Your own OpenCode and Pi settings stay.** The installer adds CARL next to them and does not replace them:
- It keeps your providers, default model, agents and plugins. If a name is already in use, CARL uses its own name (`llm-deploy`).
- Before it changes a file, it keeps your original as `FILE.before-carl`, and a copy of each version as `FILE.bak.<time>`.
- A second run with the same settings changes nothing.

**Clients in a VM or on another computer:** copy the `client/` folder there, and run `./install-clients.sh && ./install.sh`. VMware Fusion works without options. For Parallels or a computer on your network, start the server with `--host ADDR` and use the same address for the clients (USERGUIDE.md, "Clients on another computer or another VM app").

## Use

```bash
./carl.sh                              # start the server and the dashboard
```

Then run `opencode` or `pi` in a second terminal. `./carl.sh -h` shows all commands.

- **The first time,** CARL starts llama.cpp with the model that fits your Mac (Qwen3.6-35B-A3B).
- **The next time,** it starts llama.cpp with the settings that you saved.
- **If a server runs already,** the dashboard attaches to it.

**Models:** `./carl.sh models` lists the built-in catalogue and each `.gguf` in `~/models/gguf`. `./carl.sh download hf:OWNER/REPO/FILE.gguf` gets any GGUF from Hugging Face. `./carl.sh tune NAME` measures the best settings for a model on your Mac.

## The dashboard

The live state of the server: what it does now, the speed, the context, the memory, and the requests.

![The CARL dashboard](assets/dashboard.gif)

**Settings (tab 5):** three panels. Push `[` or `]` to change the panel.
- **Server:** change the model, the KV cache, the context and more. Then push `a` to restart with them. Colours show tuned values (green), changed values (yellow) and very slow values (red).
- **Models:** download, verify and delete models, or add one from Hugging Face.
- **Auto-tune:** measure the best settings for a model on this Mac.

![The Settings tab](assets/settings.png)

## In OpenCode: model, thinking effort, variants

| Do this | In OpenCode 1.18 |
|---|---|
| **Switch the model** | type `/models` (or `/mo`, or press the leader key then **m**) and pick the entry that matches the server, e.g. `llamacpp/qwen3.6-35b-a3b` |
| **Set the thinking effort** | type `/variants` and pick a level, or press **ctrl+t** to step to the next one. The current variant shows next to the model name |
| **Switch the agent** | `/agents`, or **Tab** to cycle: `build` (does the work) and `plan` (read-only). The **coder** subagent that CARL adds is not picked here: the main agent hands it large coding tasks |
| **Talk to the coder directly** | type `@coder` and your task (Tab completes the name), e.g. `@coder add tests for parse_config`: the task goes straight to the coder subagent, the main agent doesn't have to decide to delegate. If you already had an agent of your own called `coder`, CARL's is `@carl-coder`. (Pi: ask in the message, "use the coder subagent to …": there it is the `subagent` tool) |
| **Show or hide the thinking** | `/thinking` (display only: it doesn't change how much the model thinks) |

The variants are CARL's thinking levels:

| Model entry | Variants (thinking effort) | Default |
|---|---|---|
| `llamacpp/qwen3.6-35b-a3b` (35B-A3B) | `none` = thinking off · `high` = on | `high` |
| `llamacpp/qwen3.8-27b`, `llamacpp/qwen3.8-27b-abliterated-llama` (27B) | `none` = off · `low` · `medium` · `xhigh` | `low` |

- **The model entry doesn't change the server's model.** The server answers with the model it has loaded; the entry only sets the label, the context limit and the thinking options. Change the server's model in the dashboard (Settings tab) or with `./carl.sh llama --model NAME`, then pick the matching entry in `/models`.
- **`low` is right for agent work** on the 27B; `xhigh` thinks a long time even on simple tasks. A change applies from the next message.
- **After `install.sh` runs again, fully restart OpenCode**: an open OpenCode keeps the old entries.

More: USERGUIDE.md, "Thinking on, off and effort".

## OpenCode plugins

![OpenCode with the CARL plugins](assets/opencode-plugins.png)

- **Subagents panel** (right): what each running subagent does now. Below them: the subagents that finished (✓ or ✗, and the duration). A specialist **coder** subagent gets the large coding tasks.
- **Session switcher** (in the prompt box): `‹ 1/3 ● title ›`. Click the arrows to go to the previous or next session. Click the title, or type `/switch`, to select a session from a list. `●` = busy, `○` = idle.

## Read more

- [USERGUIDE.md](USERGUIDE.md): setup, daily use, models, thinking levels, the dashboard, troubleshooting.
- [REFERENCE.md](REFERENCE.md): how it works, measurements, the files in this folder, and the design decisions.
- [CHANGELOG.md](CHANGELOG.md): what changed in each release.

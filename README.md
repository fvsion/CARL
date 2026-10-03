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
- It keeps your providers, default model, agents and plugins. If a name is already in use, CARL uses its own name (`carl`).
- Before it changes a file, it keeps your original as `FILE.before-carl`, and a copy of each version as `FILE.bak.<time>`.
- A second run with the same settings changes nothing.
- CARL was formerly called LLM-Deploy: `./carl.sh` and the installer move files with the old name (`~/.config/llm-deploy`, `llm-deploy.json`) to the new one once.

**The server serves this Mac only** (127.0.0.1) unless you ask for more. **Clients in a VM or on another computer:** start the server with `./carl.sh --vm` (VMware Fusion), copy the `client/` folder there, and run `./install-clients.sh && ./install.sh`. The dashboard's Connect tab shows the same steps, and installs the clients on this Mac with one key (`i`). For Parallels or a computer on your network, start the server with `--host ADDR` and use the same address for the clients (USERGUIDE.md, "Clients on another computer or another VM app").

## Use

```bash
./carl.sh                              # start the server and the dashboard
```

Then run `opencode` or `pi` in a second terminal. `./carl.sh -h` shows all commands.

- **The first time,** CARL offers the model that **auto fit** picks for your Mac: the best stock model that holds two 96K windows (the fast Qwen3.6-35B-A3B by default; the 27B dense for the "hard-code" goal). `./carl.sh fit` shows the pick and why.
- **The next time,** it starts llama.cpp with the settings that you saved.
- **If a server runs already,** the dashboard attaches to it.

**Models:** `./carl.sh models` lists the built-in catalogue and each `.gguf` in `~/models/gguf`. `./carl.sh download hf:OWNER/REPO/FILE.gguf` gets any GGUF from Hugging Face. `./carl.sh tune NAME` measures the best settings for a model on your Mac.

## The dashboard

The live state of the server: what it does now, the speed, the context, the memory, and the requests.

![The CARL dashboard](assets/dashboard.gif)

**Settings (tab 5):** five panels. Push `[` or `]` to change the panel.
- **Server:** change the model, the KV cache, the context and more. Then push `a` to restart with them. Colours show tuned values (green), changed values (yellow) and very slow values (red). A start that needs more GPU memory than the Mac has is refused (`FIT_CHECK=0` overrides).
- **Models:** download, verify and delete models, or add one from Hugging Face.
- **Auto fit** (`A` on the Server panel): the best stock model and settings for this Mac, why, and every model ranked. **Use this** sets them in one step.
- **Auto-tune:** measure the best settings for a model on this Mac.
- **Router:** who switches the model: the dashboard (the default), or OpenCode / Pi (router mode, for users who prefer to choose models on the fly). WARNING: every switch empties the prompt cache, so the next request re-reads the whole conversation.

![The Settings tab](assets/settings.png)

## In OpenCode: model, thinking effort, variants

| Do this | In OpenCode 1.18 |
|---|---|
| **Switch the model** | type `/models` (or `/mo`, or press the leader key then **m**). The list shows the models installed on the server, one entry each, under their CARL names (e.g. `llamacpp/qwen3.6-35b-a3b-iq3`). The dashboard's model mode (the default): pick the entry the server runs; router mode: the entry you pick is loaded |
| **Set the thinking effort** | type `/variants` and pick a level, or press **ctrl+t** to step to the next one. The current variant shows next to the model name |
| **Switch the agent** | `/agents`, or **Tab** to cycle: `build` (does the work) and `plan` (read-only). The **coder** subagent that CARL adds is not picked here: the main agent hands it large coding tasks |
| **Talk to the coder directly** | type `@coder` and your task (Tab completes the name), e.g. `@coder add tests for parse_config`: the task goes straight to the coder subagent, the main agent doesn't have to decide to delegate. If you already had an agent of your own called `coder`, CARL's is `@carl-coder`. (Pi: ask in the message, "use the coder subagent to …": there it is the `subagent` tool) |
| **Show or hide the thinking** | `/thinking` (display only: it doesn't change how much the model thinks) |

The variants are CARL's thinking levels:

| Model entry | Variants (thinking effort) | Default |
|---|---|---|
| the 35B-A3B builds (`qwen3.6-35b-a3b…`, `heretic-35b-a3b…`) | `none` = thinking off · `high` = on | `high` |
| the 27B builds (`qwen3.8-27b…`, `orcarouter-27b…`) | `none` = off · `low` · `medium` · `xhigh` | `low` |
| a model you added | from its card's *thinking* (on / off, or effort levels); on / off without one | |

- **By default the entry doesn't change the server's model.** The server answers with the model it has loaded; the entry sets the label, the context limit and the thinking options. Change the server's model in the dashboard (Settings tab) or with `./carl.sh llama --model NAME`, then pick its entry in `/models`. If the entry and the server differ, OpenCode shows a CARL warning. **Router mode** (Settings → Router, or `./carl.sh --router`) loads the model you pick instead.
- **After a download or a delete,** update the lists: `./carl.sh install --config-only`, or `u` in the dashboard's Connect tab (it warns when they are out of date).
- **`low` is right for agent work** on the 27B; `xhigh` thinks a long time even on simple tasks. A change applies from the next message.
- **After `install.sh` runs again, fully restart OpenCode**: an open OpenCode keeps the old entries.

More: USERGUIDE.md, "Thinking on, off and effort".

## OpenCode plugins

![OpenCode with the CARL plugins](assets/opencode-plugins.png)

- **Subagents panel** (right): what each running subagent does now. Below them: the subagents that finished (✓ or ✗, and the duration). A specialist **coder** subagent gets the large coding tasks.
- **Model check:** a CARL warning when the model you picked isn't the one the server runs, isn't installed on it, or is being loaded (router mode).
- **Session switcher** (in the prompt box): `‹ 1/3 ● title ›`. Click the arrows to go to the previous or next session. Click the title, or type `/switch`, to select a session from a list. `●` = busy, `○` = idle.

## Read more

- [USERGUIDE.md](USERGUIDE.md): setup, daily use, models, thinking levels, the dashboard, troubleshooting.
- [REFERENCE.md](REFERENCE.md): how it works, measurements, the files in this folder, and the design decisions.
- [CHANGELOG.md](CHANGELOG.md): what changed in each release.

<p align="center"><img src="assets/carl-face.png" alt="CARL" width="180"></p>

<h1 align="center">CARL</h1>

<h3 align="center"><b>C</b>an't <b>A</b>fford <b>R</b>emote <b>L</b>LMs</h3>

<p align="center"><i>AI Slop Coded LLM Runner, So You Can Code AI Slop Locally</i></p>

<p align="center">A local coding model runner on your Apple Silicon Mac, for <b>OpenCode</b> and <b>Pi</b>.<br>
One command starts the server and a live dashboard.</p>

---

## Install

**You need:** an Apple Silicon Mac, [Homebrew](https://brew.sh), `python3`, and 14–24 GB of free disk space for each model.

```bash
brew install llama.cpp aria2 ansifilter
./carl.sh download default            # the best model for this Mac (resumable, checksum-verified)
./carl.sh install                     # OpenCode + Pi into ~/.local (no sudo), connected to the server
```

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
- **The next time,** it starts the server and the settings that you used last.
- **If a server runs already,** the dashboard attaches to it.

## The dashboard

The live state of the server: what it does now, the speed, the context, the memory, and the requests.

![The CARL dashboard](assets/dashboard.gif)

**Settings (tab 5):** change the backend (llama.cpp or MTPLX), the model, the KV cache, the context and more. Then push `a` to restart with them.

![The Settings tab](assets/settings.png)

## OpenCode plugins

![OpenCode with the CARL plugins](assets/opencode-plugins.png)

- **Subagents panel** (right): what each subagent does now. A specialist **coder** subagent gets the large coding tasks.
- **Session switcher** (in the prompt box): `‹ 1/3 ● title ›`. Click the arrows to go to the previous or next session. Click the title, or type `/switch`, to select a session from a list. `●` = busy, `○` = idle.

## Read more

- [USERGUIDE.md](USERGUIDE.md): setup, daily use, models, thinking levels, the dashboard, troubleshooting.
- [REFERENCE.md](REFERENCE.md): how it works, measurements, the files in this folder, and the design decisions.
- [CHANGELOG.md](CHANGELOG.md): what changed in each release.

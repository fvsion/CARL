# CARL Reference

CARL: Can't Afford Remote LLMs.

CARL runs a local Qwen coding model on an Apple Silicon Mac, for OpenCode or Pi. This document gives the technical details of CARL: the architecture, the files, the llama.cpp server, and the measured results. It also tells how each model and client controls "thinking". For setup and daily use, see [USERGUIDE.md](USERGUIDE.md). For the short overview, see [README.md](README.md).

Each behaviour in this document was checked against the source code (pi-ai 0.99.2, OpenCode 1.18.32, the GGUF chat templates). Other behaviours were measured on this Mac. If a statement is not verified, the text says so.

## Contents

- [Architecture](#architecture)
- [Repository layout](#repository-layout)
1. [The server: llama.cpp](#1-the-server-llamacpp)
2. [llama.cpp details](#2-llamacpp-details)
3. [Model and quantization choices](#3-model-and-quantization-choices)
4. [How thinking works](#4-how-thinking-works)
5. [Thinking by model](#5-thinking-by-model)
6. [Thinking by client](#6-thinking-by-client)
7. [The full thinking matrix](#7-the-full-thinking-matrix)
8. [Sampling and output limits](#8-sampling-and-output-limits)
9. [Context limits: server vs client](#9-context-limits-server-vs-client)
10. [Verifying behaviour](#10-verifying-behaviour)
11. [OpenCode config](#11-opencode-config)
12. [Performance (measured)](#12-performance-measured)

---

## Architecture

```
 Mac (here: M3 Pro, 36 GB)                             Kali VM (VMware Fusion, 4 GB) -- optional
 ┌──────────────────────────────────────────┐          ┌──────────────────────────────────┐
 │ host/serve.sh  ──►  llama-server :8080    │  vmnet8  │ OpenCode ─┐                       │
 │   (default)         (llama.cpp, Metal)    │◄─────────┤           ├─ Bearer key from      │
 │                                           │  NAT     │ Pi ───────┘  ~/.config/llm-deploy/│
 │                                           │          │ configs from client/install.sh    │
 │ binds 192.168.42.1 (VM) or 127.0.0.1      │          │ VM address 192.168.42.130         │
 │ monitor in the same terminal              │          │                                   │
 │ OpenCode / Pi here too (--local mode)     │          │                                   │
 └──────────────────────────────────────────┘          └──────────────────────────────────┘
      ~/models/gguf/      GGUF weights (catalogue: host/catalog.json; any other .gguf here is listed too)
      ~/models/logs/      server logs
      ~/.config/llm-deploy/config.json   your settings (Settings tab, ./carl.sh config)
      ~/.config/llm-deploy/models.json   custom models and Auto-tune results (this Mac)
      ~/models/templates/ patched chat templates (thinking toggle)
      ~/.config/llm-deploy/api-key       shared API key (server and clients; created on first start)
```

- **CAUTION:** Run only one server at a time. Each model needs about 14–23 GB, and two models do not fit in 36 GB. If you run two servers, GPU out-of-memory errors occur. The running server then stays broken until you restart it. The launchers refuse to start a second model ([section 2](#2-llamacpp-details), "Launch guard").
- **The server uses the OpenAI API** (`/v1/chat/completions`, `/v1/models`) and a Bearer key.
- **Code layers on the Mac:** the shell launchers (`host/`) get their model and settings from `tools/carl.py`. `tools/carl.py` and `tools/gguf_shape.py` are thin facades over the package `tools/carl_core/`: a pure `domain/` (no I/O) and `adapters/` for the files, `sysctl` / `netstat`, Hugging Face, the downloads and `llama-server`. The monitor, `llama-fit.py` and `carl-tune.py` use the same facades ([Repository layout](#repository-layout)).
- **The client side is a self-contained bundle** (`client/`). You can run it in the VM (copy it through the Fusion shared folder) or on the Mac itself (`./client/install.sh --local`).

## Repository layout

| Path | What it is |
|---|---|
| `carl.sh` | The launcher in the project root. It runs `host/serve.sh` with the same arguments: `./carl.sh` opens the dashboard, `./carl.sh -h` shows the help. `./carl.sh --no-start` opens only the dashboard (nothing loads). `./host/serve.sh` continues to work. |
| `CHANGELOG.md` | What changed in each release. |
| `assets/` | The logo and the README screenshots. `tools/tuishot.py` makes the screenshots. |
| `host/serve.sh` | The entry point on the Mac. Server command: `llama`. Tools: `monitor`, `fit`, `models`, `download`, `verify`, `tune`, `config`, `install`. `help [command]` shows the help for one command, and `-h` prints the overview. `--help-adv` lists every `llama-server` flag. With no arguments, it opens the dashboard. It gives `llama` to `serve-llama.sh`. |
| `host/common.sh` | `serve.sh` and `serve-llama.sh` use this file. It contains the network mode (`--vm` / `--local` / auto, never 0.0.0.0), the API key (creation, and the one-time copy from the old path), `ensure_deps` (the Homebrew tools), `port_pid` / `port_host` (port lookups with `netstat`), `apply_settings` (reads the `KEY=value` lines of `tools/carl.py` with an allow-list; values are never evaluated), `require_int` and `is_port` (input checks), the launch guard and `run_server`. `run_server` runs the server in the background in its own process group, with the monitor in front. If there is no terminal, it runs a plain foreground server. |
| `host/serve-llama.sh` | The llama.cpp launcher: `--model`, `--kv q4\|q8`, `--ctx N\|Nk`, logging, keep-awake, thinking-toggle template. It gets the model and its settings from `tools/carl.py launch-env`. |
| `host/catalog.json` | The built-in model catalogue. For each model: `hf` (repo, pinned revision, file, SHA-256, size), `summary`, `description`, the model card (`role`, `good_for`, `why_use`, `trade_offs`, `pick_instead`, `hardware`, `uncensored`, `rank`), `tune` (kv, ctx, slots, spec, spec_n, sampling), `why` (the reason for each tuned value), `ctx_zones`, `measured`. `default` and `default_small` select the default models. |
| `host/models.sh` | A thin wrapper around `tools/carl.py`: `list`, `download NAME\|default\|all\|hf:OWNER/REPO/FILE.gguf`, `verify`, `delete`, `path`, `get`, `default`, `downloaded`. |
| `host/gguf-chat-template.py` | Extracts the chat template of a GGUF. Then it adds the rule "`reasoning_effort: none` → thinking off" at the start of the template. |
| `client/install-clients.sh` | Run it in the VM or on a Mac. It installs OpenCode and/or Pi from npm into `~/.local`. If necessary, it also installs a Linux or macOS Node 22. It does not use sudo. |
| `client/install.sh` | Run it in the VM (`--vm`, default on Linux) or on the Mac (`--local`, default on macOS). It stores the API key. It makes a backup, then writes the OpenCode and Pi configs. It sets the context limit to the server value. It does a smoke test of the server. |
| `client/configure.py` | The non-destructive merge that `install.sh` uses (USERGUIDE.md, "Updating the client configs"). |
| `client/opencode/opencode.json` | The OpenCode config template (the `llamacpp` provider). |
| `client/agents/coder.md`, `client/agents/delegation.md` | The **coder** subagent (OpenCode and Pi both use it): its "when to use" description and its work method. Also the delegation rule for the main agent: delegate only if the agent is stuck after two failed fixes, or if the task is large. |
| `client/pi/extensions/subagent/` | The official subagent extension for Pi (vendored, MIT). A patch makes it list the installed agents in the tool description. Thus, the model can delegate without help. |
| `client/opencode/plugins/subagents-sidebar/` | An OpenCode TUI plugin: a live list of subagents in the sidebar. Running subagents on top (agent, task, elapsed time, current tool, context); finished ones below (✓/✗, duration; the 5 newest for 5 minutes, then a `+N more` line). It resets when you go to a different parent session. Click to open. `install.sh` installs it into `tui.json`. |
| `client/opencode/plugins/session-switcher/` | An OpenCode TUI plugin: a session switcher in the prompt box (`‹ 2/3 ● title ›`), and `/switch` for a list of the recent sessions with their state. `install.sh` installs it into `tui.json`. |
| `client/pi/models.json` | The Pi config template. |
| `tools/carl_core/` | The models and settings logic, in layers. `domain/` is pure (no I/O): `types`, `settings`, `gguf`, `fit`, `models`, `hf`, `launch`, `records`, `tuning`, `ports`. `adapters/` does the I/O: `json_files`, `filesystem`, `gguf_reader`, `system` (sysctl, the GPU limit, the `netstat` parser), `huggingface`, `downloader`, `llama_server`, `console`. `app.py` holds the use cases, and `wiring.py` connects the adapters. |
| `tools/carl.py` | A thin facade over `tools/carl_core` (its CLI and the API of the monitor). One place for the models and the settings: the catalogue, the models on this Mac (catalogue, models folder, Hugging Face downloads), `config.json` (validated) and the settings order. Downloads (pinned, SHA-256 verified), `launch-env` for the launchers, `config show\|get\|set\|unset\|path`. The launchers, `llama-fit.py` and the monitor use it. |
| `tools/carl-tune.py` | Auto-tune (`./carl.sh tune NAME [--quick]`): memory fit, speculation modes, prompt reading at 8K/32K/64K, context zones. It saves the result in `~/.config/llm-deploy/models.json`. |
| `tools/llama-monitor.py` | The live monitor (the dashboard) for llama.cpp. Tabs: Overview, Connect, Requests, Log, Settings. The Settings tab has three panels: Server (the model and its settings, with the reason for each tuned value; restarts the server), Models (download, verify, delete, add from Hugging Face) and Auto-tune. On the Server panel the model card sits under the settings, full width; a click on its title cycles collapsed / normal / full. A thin launcher over the `tools/monitor/` package. |
| `tools/monitor/` | The dashboard's code: pure formatting, state, settings and views (`fmt`, `model`, `keys`, `settings`, `cards`, `views`, `settings_view`, `state`), adapters (`system` for ps/netstat/sysctl/pmset, `api`, `collector`, `logtail`, `store` over `carl.py`, `jobs` for restart, Auto-tune and downloads, `terminal`), wiring (`app`, `controller`, `cli`). |
| `tools/llama-fit.py`, `tools/gguf_shape.py`, `tools/metal-limit.swift` | `serve.sh fit` and the memory check at server start. `gguf_shape.py` (now a thin facade over `carl_core`) reads the GGUF metadata and does the KV/state maths (the monitor uses it too). `metal-limit.swift` reads the GPU memory limit of the Mac. |
| `tools/make-share-zip.sh` | Makes a clean zip of this folder that you can share. |
| `tools/tuishot.py` | Renders terminal screenshots and GIFs (the dashboard, OpenCode) for the README. |
| `tools/llama-log.sh` | Shows the server log as plain text. |
| `tools/llama-spec-sweep.sh`, `tools/llama-spec-bench.py` | The speculative-decoding sweep and its benchmark. The edit workload re-emits the (rewritten, 1.1.0) source of the bench, so its edit numbers are not directly comparable with older measurements. |
| `tools/llama-ab.sh`, `tools/llama-ab-measure.py`, `tools/llama-kv-longctx.py` | The KV-type and `-ub` A/B test, and the ~64K needle test. Since 1.2.0, the long prompts come from the source files of this folder (Python, shell, JS in `tools/`, `host/`, `client/`), so the numbers are not directly comparable with older runs. |
| `tools/llama-wait-idle.sh` | Waits until the server is idle. |
| `tools/llama-sesstest.py` | A long-session test for llama.cpp. Its prompt corpus is the same as for `llama-kv-longctx.py`. |
| `tools/req-capture-proxy.py` | A request-capture proxy. It refuses wildcard listen addresses, and it writes its log with mode 600. |
| `tools/carl_bench.py` | Shared helpers for the small benchmark tools: the API key, chat requests, the PID of the listening server (`netstat`), the server memory. |
| `tests/` | Unit tests. `python3 -m unittest discover -s tests` (the `carl_core` domain, app and adapters) `python3 -m unittest discover -s tests/scripts` (the shell helpers, `client/configure.py`, the small tools) and `python3 -m unittest discover -s tests/monitor -t tests/monitor` (the dashboard). They need no server and no model. |

For the use of each tool, see USERGUIDE.md, "Benchmarking and testing".

## 1. The server: llama.cpp

| | llama.cpp (`./carl.sh llama`) |
|---|---|
| Version | 0.4.1 (Homebrew), `llama-server` |
| Port | 8080 |
| API | OpenAI API (`/v1/chat/completions`, `/v1/models`), Bearer key `~/.config/llm-deploy/api-key` |
| Weights | GGUF (`~/models/gguf/`): the catalogue models, and any other GGUF |
| Models available | Qwen3.6-35B-A3B (default, `qwen3.6-35b-a3b`), Qwen3.8-27B stock, Qwen3.8-27B abliterated (orcarouter), and their Q3/IQ3 builds |
| KV cache | Real q4_0 (default) or q8_0, allocated one time with no bf16 copy. Set it with `--kv`. |
| Usable context | 96K per slot by default, up to 256K (`--ctx`) |
| Memory behaviour | The server allocates the full KV cache at start. Memory stays flat during a session (a 56K → 81K session was measured). |
| Prompt reuse | Context checkpoints (8, every 4K tokens) at user-message boundaries. RAM prompt cache, sized automatically to 1–8 GiB ([section 2](#ram-prompt-cache-and-checkpoints-measured-2026-10-02)). |
| Speculation | MTP head + n-gram (`draft-mtp,ngram-mod`). The draft count is set for each model. |
| Decode (27B) | 10.5–11 tok/s on new text, 27 tok/s when it re-emits text |
| Prompt read speed (27B, cold) | ~85–90 tok/s at 2–9K, ~56–65 tok/s at 66K |
| Thinking off from OpenCode | Yes: `none` variant (patched template) |
| Thinking off from Pi | Yes: `off` → `chat_template_kwargs.enable_thinking=false` |
| Effort values accepted | Depends on the template of the model ([section 5](#5-thinking-by-model)) |
| Bad effort value | The Qwen3.8 template raises an error (the request fails). |
| Concurrency | 2 slots by default (`--slots auto`): two conversations at the same time, each with its own cache. More requests wait in a queue. |
| Stop | In the monitor: `q`, then `s`. Or kill the PID that `netstat -anv -p tcp` shows for :8080 ([Port and PID lookups](#port-and-pid-lookups-no-lsof)) |
| Logs | `~/models/logs/llama-server-*.log` (`tools/llama-log.sh`) |
| Health | `/health`, `/props`, `/metrics`, `/slots` |

**Other servers considered:**
- MTPLX was evaluated (2026-09/10) and removed in 1.2.0. On 32–36 GB Macs, it could not hold long sessions: HTTP 507 from ~4–56K tokens (it depends on the free memory), no real quantized KV cache in 2.11, and a 48K cap. llama.cpp holds 2 × 96K slots. The last version with MTPLX support is commit 470c316.
- NOTE: oMLX (a different MLX server) was researched as an alternative and not used. It was not tested on this Mac.

---

## 2. llama.cpp details

`host/serve.sh` gives the `llama` command to `host/serve-llama.sh`. With no arguments, `./carl.sh` opens the dashboard. It attaches to a server on port 8080. If no server runs, it starts llama.cpp with the saved settings. If no model is downloaded, it first offers to download the default model for this Mac. `./carl.sh -h` prints the help. The flags that `serve-llama.sh` uses are in [Server flags](#server-flags).

- **Slots: 2 by default when they fit** (`--slots auto`).
  - **Reason:** OpenCode subagents are separate conversations. With one slot, a subagent evicts the main session. llama.cpp could park the main session in the RAM cache only if it fit. The states were 2.1–2.3 GiB, and the old cap was 2048 MiB (`exceeds cache size limit … skipping`). Thus, the server read the main session again after each subagent.
  - **Two slots:** `--parallel 2 --kv-unified --kv-unified-per-slot CTX -c 2×CTX --no-cache-idle-slots -sps 0.5`.
    - The last two flags are important. By default, llama.cpp parks and clears idle slots at each new task (`--cache-idle-slots`). By default, it also gives a slot to each prompt that shares ≥10% of its tokens with that slot (`-sps 0.1`). A subagent that shares the system prompt meets this condition.
  - **Measured:** A follow-up turn after a subagent took 0.6 s (35B) / 1.9 s (27B). The prompt stayed fully cached in its slot.
    - Two slots that generate at the same time: the 35B gives +39% combined (each slot at 72–77% of its speed alone). The 27B gives 8.8 vs 9.6 tok/s (time-shared).
    - MTP works in both slots.
  - **RAM prompt cache** (`--cache-ram`): The server calculates its size from the free RAM after the weights, the KV cache and a reserve. The reserve is 10 GiB with the VM network, otherwise 6 GiB. The size is clamped to 1–8 GiB. This cache holds conversations that are not in a slot.
  - **NVMe parking:** llama.cpp has no automatic disk tier. It has only a manual function (`--slot-save-path` + `/slots/{id}?action=save|restore`). On Apple Silicon, the RAM cache is the same memory as "VRAM". Also, macOS moves this memory to swap on the SSD when memory pressure is high.
- **Two models at the same time do not fit.**
  - CAUTION: Do not load a second model while a server runs. The second load causes Metal out-of-memory errors.
  - After that, the first server returns `Compute error` for each request. But `/health` continues to report ok. Only a restart repairs the server.
- **Launch guard** (`guard_other_models` in `host/common.sh`). `serve-llama.sh` and Auto-tune refuse to start in these conditions:
  - a process holds more than 8 GB of resident memory (`BIG_GB`, default 8);
  - a process has a known model-server name (`llama-server`, `ollama`, `LM Studio`, …).
  - **Reason:** two models do not fit. A name check alone misses servers that started in a different way. On 2026-10-02, a second model crashed the Mac (it restarted).
  - `ALLOW_SECOND_MODEL=1` skips the check. Use it only if the large process is not a model.
- **Sleep stops requests.** The launchers keep `caffeinate -i` active for the full life of the server. To disable this, set `KEEP_AWAKE=0`. If you close the lid on battery power, the Mac still goes to sleep.
  - **Evidence:** before this change, idle sleep (1 min on battery) froze a 74K prompt for 30+ min.
- **The server ignores the model name in a request.** The GGUF that is loaded answers all requests. Thus, the model picker in the client must match the server.
- **Template override.** The server uses `--chat-template-file ~/models/templates/<model>.thinking-toggle.jinja`. This file is generated at start ([section 4](#4-how-thinking-works)). To use the template of the GGUF, set `THINK_TOGGLE=0`.
- **Speculation per model** (catalogue `tune.spec` and `tune.spec_n`, or the Auto-tune result): The 27B dense uses MTP + n-gram with 1 draft token. The 35B MoE (Q4) uses 2. More drafts help a MoE, because with 3B active parameters it costs little to verify more tokens. The IQ3 builds use MTP + n-gram with 1 draft token: 2 drafts lose on IQ quants ([IQ3 speculation](#iq3-speculation-measured-2026-10-03)).
- **The monitor (since 2026-10-01):** `tools/llama-monitor.py`.
  - It has a header with the status and **[ Quit ]**.
  - Tabs: Overview (cards), Connect (URL, key, config copy), Requests, Log, Settings.
  - Use only the left click. The monitor does not use the right click, because terminals such as iTerm2 show their own context menu.
  - A config that you copy from the Connect tab contains the API key. On screen, the key stays masked until you reveal it.
- **Launch model (since 2026-10-01).** When you start from a terminal, `run_server` (`host/common.sh`) does these steps:
  - It starts the server under `nohup` with job control on (`set -m`). Thus, the server gets its own process group, and Ctrl-C in the monitor cannot reach it.
  - It starts `caffeinate -w <server pid>`.
  - It then uses `exec` to change into the monitor.
  - **Result:** The server is a child process of the monitor, so the monitor must reap it. If the monitor does not reap it, a stopped or crashed server stays as a zombie process. `ps` still lists this zombie. The `pid_alive()` function of the monitor calls `waitpid(WNOHANG)` and treats state `Z` as dead.
  - Without a terminal (scripts, `nohup`, `MONITOR=0`), the launcher starts the server with `exec` in the foreground, as before. The benchmark tools need this behaviour.
- **API key** (`ensure_api_key` in `host/common.sh`): the server and the clients use `~/.config/llm-deploy/api-key` (mode 600, in a folder with mode 700; the folder of `config.json`). `API_KEY_FILE` overrides the path.
  - If the file is missing, the first server start copies the key from the old path `~/.mtplx/api-key` (before 1.2.0). It is the same key, so existing clients continue to work. If there is no old key, it makes a random 40-character key.
  - The dashboard, Auto-tune, the bench tools and `tools/llama-wait-idle.sh` read the new path. While the new file does not exist, they read the old path.
- **Network modes** (`host/common.sh`):
  - `--vm` = 192.168.42.1. This is the VMware Fusion NAT network (vmnet8). The Mac is `192.168.42.1` on `bridge101`. The Kali VM is `192.168.42.130`. The VM and the Mac itself can both connect to this address. The mode fails if this address is missing.
  - `--local` = 127.0.0.1. Only the Mac can connect. Use it for OpenCode/Pi on the Mac, or on a Mac without VMware.
  - auto = VM if present, else local.
  - `--host ADDR` (or `HOST=ADDR`) has priority over the modes. ADDR must exist on an interface of this Mac: for example its LAN address, or the address on a Parallels network (often `10.211.55.2`). `VM_HOST=ADDR` changes the address of `--vm` and auto mode.
  - The settings file can hold an address (`llama.host` in `config.json`; the Settings tab writes it when you select an address in the network row). Order of priority: flag, environment, settings file.
  - The start-up banner shows the mode. For an address that is not 127.0.0.1 or the VM address, it shows a CAUTION: every computer that can reach the address can use the server, and only the API key protects it.
  - After a restart from the Settings tab, the health check also tries the selected address. Thus, a server on the LAN address is not taken as a failed start.
  - CAUTION: The script refuses 0.0.0.0 / `::`, also through `HOST=0.0.0.0`. The macOS firewall is off on this Mac, so a wildcard bind would make the model available to the LAN.
  - `client/install.sh --local` sets the clients to the address on which the server actually listens. `--host ADDR` sets any address. `--port N` sets the llama.cpp port (default 8080). The old positional form `install.sh HOST [X] [PORT]` still works: the second value (an old port) is ignored with a note, and the third is the llama.cpp port.
  - API key for `client/install.sh` (first hit wins): `--key KEY` / `--key-file FILE`, `$CARL_API_KEY`, a file `api-key` next to the script, on a Mac with `--local` the server key (`~/.config/llm-deploy/api-key`, else the old `~/.mtplx/api-key`), the key from a previous run (the same new path, else the old `~/.config/mtplx/api-key`), a hidden prompt. The installer stores it at `~/.config/llm-deploy/api-key` (mode 600), and the OpenCode and Pi configs point at that file. A new run changes old configs to the new path. It does not delete the old client file (a provider of your own can use it): delete it when nothing uses it. `--key` leaves the key in the shell history. The smoke test gives the key to `curl` through a header file (`curl -H @file`, curl 7.55 or later), so the key does not show in `ps`.
- **Memory check before load.** `serve-llama.sh` runs `tools/llama-fit.py --check`. It warns when weights + KV + buffers are more than the GPU limit ([below](#gpu-memory-limit-and-what-fits)). The warning does not block the start.
- **Port guard.** `serve-llama.sh` does not start if a process already listens on the port (`port_pid`, see below). It does this check before it touches `llama-server-latest.log` or loads anything. Before this guard, a second `serve.sh` (10:08 on 2026-10-01) failed to bind. But it had already pointed the `latest` symlink to its own 4-line failure log.
- **`/metrics` gauges reset at each read.** (`--metrics` enables this Prometheus endpoint.) `prompt_tokens_seconds` and `predicted_tokens_seconds` cover only the time since the last scrape. Thus, you must calculate averages from the `*_total` counters. The monitor does this.
- **The server does not log its KV cache size** at the default log level. Thus, the monitor calculates it ([below](#context-memory-kv-cache-and-recurrent-state)).
- **Dependency check** (`ensure_deps` in `host/common.sh`). Before a start, the launchers look for `llama-server` (llama.cpp), `aria2c` and `ansifilter`. In a terminal, they offer to run `brew install` for the missing tools. Without a terminal, they show the command. Only `llama-server` is necessary; the others are optional. `SKIP_DEPS=1` skips the check.
- **No model, or the default is not downloaded.** `./carl.sh` with no arguments offers to download the default for this Mac if no model is downloaded. If you answer no, it opens the dashboard without a server. If the default is not downloaded but another model is, `tools/carl.py` (`resolve_launch`) uses the first downloaded model, and says so.

### Port and PID lookups (no lsof)

CARL does not use `lsof`. `lsof` checks each mounted file system. On a stale network share (for example a Time Machine SMB volume that is disconnected), it hangs in the kernel, and no signal can stop it. Before version 1.1.0, this froze the monitor and `./carl.sh` on a Mac with such a volume.

The launchers (`port_pid`, `port_host` in `host/common.sh`), the monitor, `client/install.sh` and the test tools use `netstat -anv -p tcp`. Its `process:pid` column gives the PID of the process that listens on a port. To do the same by hand:

```bash
netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}'
```

`$(NF-8)` counts from the end of the line, because a process name can contain spaces.

### Settings: config.json, Auto-tune and the catalogue

`tools/carl.py` gives each llama.cpp start its model and settings (`launch-env`). Each value comes from the first source that has it:

| Order | Source | Where |
|---|---|---|
| 1 | Command-line flags | `--model`, `--ctx`, `--kv`, `--slots`, … |
| 2 | Environment variables | `CTX`, `KV`, `SPEC`, `SPEC_N`, `TEMP`, … |
| 3 | `config.json` | `~/.config/llm-deploy/config.json`: the `llama` section, and the profile `models.<name>` |
| 4 | Auto-tune result for this Mac | `~/.config/llm-deploy/models.json`: `models.<name>.tune.settings` |
| 5 | Catalogue | `host/catalog.json`: `tune` of the model. For a custom model: values from its GGUF header |
| 6 | Built-in defaults | q4_0, 96K, auto slots, MTP + n-gram n=1, temperature 1.0, … |

- **`config.json` sections:** `llama` (`model`, `net`, `host`, `cache_ram`, `ub`, `batch`, `ckpt`, `ckpt_step`, `think_toggle`, `extra_args`); `models.<name>` (`kv`, `ctx`, `slots`, `spec`, `spec_n`, `temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `alias`); `paths` (`models_dir`).
- **Validation:** each key has a type, a range or a list of choices (`./carl.sh config show` lists them). A bad value stops the start with an error. An unknown key is ignored. CARL writes the file atomically, with mode 600.
- **Migration:** before `config.json`, the dashboard wrote `llama.env`. If `config.json` is missing, CARL converts this file into it one time.
- **Files from before 1.2.0** can hold settings of features that 1.2.0 removed (CHANGELOG.md). They load: CARL ignores them (`./carl.sh config show` warns about a removed section), and they go away the next time CARL saves the file.
- `SETTINGS_FILE=none` skips `config.json`.
- **Custom models:** each `.gguf` in the models folder (`paths.models_dir`, or `MODELS_DIR`) is a model, also if it is not in the catalogue. `./carl.sh download hf:OWNER/REPO/FILE.gguf` resolves the revision, the size and the SHA-256 from the Hugging Face API, records the model in `models.json`, downloads it and verifies it.

### Auto-tune

`./carl.sh tune NAME [--quick]` (`tools/carl-tune.py`) measures one model on this Mac. It starts its own server on port 8093, one time for each speculation mode (about 5–10 min in all). It refuses to start if a server runs on 8080, or if another large process is in memory (more than `BIG_GB`, default 8 GB, or a known model server; `ALLOW_SECOND_MODEL=1` skips this check, as for the launch guard).

1. **Memory:** the largest window for each slot that fits the GPU limit, with 1 and 2 slots (the same maths as `./carl.sh fit`).
2. **Speculation:** none, `ngram-mod` (n=2), and, if the GGUF has an MTP head, `draft-mtp` and `draft-mtp,ngram-mod` at n=1 and n=2 (`--quick`: n=1 only). Each mode generates prose, new code and a code re-emit, two times. The score is a weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2). A mode with drafting must beat a simpler mode by 3% to win.
3. **Prompt reading:** a cold read at 8K and 32K tokens, and 64K without `--quick`. The time for each token grows about linearly with the depth. From this, Auto-tune calculates the time to read a full window. The context zones of this Mac: a full cold read in ≤3 min = fast, ≤10 min = slow, more = very slow. The fast zone is never smaller than 96K (`ctx_zones()` in `carl_core/domain/models.py`).
4. **Result** (`choose_ctx()` in `carl_core/domain/tuning.py`): kv q4_0, the best speculation, the context, and 2 slots if two windows fit. It is saved in `~/.config/llm-deploy/models.json`, with the speed of each mode, the read speeds and the zones.
   - If 96K fits one slot, 96K is the floor. Above it, Auto-tune keeps the catalogue window while a cold read of it is not worse than slow on this Mac and it fits. Otherwise, it uses the largest standard window (96K, 128K, 160K) in the fast zone that fits.
   - If 96K does not fit, it uses the largest standard window (32K, 48K, 64K) that fits.

NOTE: The re-emit workload copies `tools/carl_core/adapters/llama_server.py` back (since 1.1.0). Thus, re-emit scores are not directly comparable with older Auto-tune results.

The dashboard (Settings, Auto-tune panel) stops the running server, runs the tune, and starts the server again.

### Server flags

This table shows the flags that `host/serve-llama.sh` gives to `llama-server`, and the reasons:

| Setting | Value | Why |
|---|---|---|
| Bind | `--host 192.168.42.1 --port 8080 --api-key-file ~/.config/llm-deploy/api-key` | An address for the VM only; a shared key |
| Offload | `-ngl 999` | The whole model is on the GPU (Metal). |
| Flash attention | `-fa on` | Necessary for a quantized V cache. It was on for every measurement. |
| KV cache | `-ctk q4_0 -ctv q4_0` (`--kv q8` → q8_0) | q4: +16% prefill and about 2 GB less memory than q8, the same decode speed, 8/8 needle recall at 66K. Do not mix K and V types (prefill is about 5× slower). |
| Context | `-c` = slots × 96K (`--ctx` sets the context window for each slot) | 96K per slot by default, because long context windows read cold prompts and decode much more slowly ([Context length](#context-length-what-longer-windows-cost)). The model maximum is 262144. The server allocates the whole KV cache at start. |
| Batching | `-b 2048 -ub 512` | `-ub 512` gave the best measured result (90.5 tok/s vs 88.6 / 86.1 for 1024 / 2048). |
| Slots | `--slots auto` (default): 2 if two full context windows fit, else 1 → `--parallel 2 --kv-unified --kv-unified-per-slot CTX -c 2×CTX --no-cache-idle-slots -sps 0.5` | The main OpenCode session and a subagent each keep their own slot and cache (see above). |
| Prompt cache | `--ctx-checkpoints 8 --checkpoint-min-step 4096 --cache-ram N` (N comes from the free RAM, 1–8 GiB) | Checkpoints let follow-up turns use the cache again, because the recurrent layers of Qwen cannot trim it. The RAM cache holds conversations that do not fit in the slots (a third session). Each held state is 2–2.5 GiB. Size = RAM − weights − KV − reserve (10 GiB with the VM network, else 6; `RESERVE_GB`). |
| Templates | `--jinja --reasoning-format deepseek`, `preserve_thinking: true`, `--chat-template-file` (patched) | The server sends the reasoning to `reasoning_content`. `reasoning_effort: none` turns off thinking ([section 4](#4-how-thinking-works)). |
| Sampling | `--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0 --repeat-penalty 1.0` (`TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE`) | The Qwen recommendation for thinking mode ([section 8](#8-sampling-and-output-limits)). |
| Speculation | `--spec-type draft-mtp,ngram-mod --spec-draft-n-max N` (type and N from the tune of each model: N 1 for the dense 27B and the IQ3 builds, 2 for the Q4 35B) | The best of 7 configs that were measured on each model ([section 12](#12-performance-measured), [IQ3 speculation](#iq3-speculation-measured-2026-10-03)). |
| Logging | `--log-file ~/models/logs/llama-server-<ts>.log --log-timestamps --log-prefix` | `llama-server-latest.log` points to the newest log. Use `tools/llama-log.sh` to read it. |
| Keep awake | `caffeinate -i -w <server pid>` | If the Mac goes to sleep, requests stop in the middle of the prompt. |
| Monitor in the same terminal | `run_server` in `host/common.sh` | One command shows the server live. `MONITOR=0` gives a plain foreground server. |
| Network | `--vm` (192.168.42.1), `--local` (127.0.0.1), default auto | The launcher refuses 0.0.0.0. |
| Memory check | `tools/llama-fit.py --check` before the model loads | A warning if weights + KV + buffers are more than the GPU limit, with the largest context window that fits (`FIT_CHECK=0` skips it). |
| Launch guard | `guard_other_models` | No start if a second model is in memory (`ALLOW_SECOND_MODEL=1` skips it). |
| Port guard | No start if the port is in use (`netstat`, not `lsof`) | A second model would collide with the running one. Also, `llama-server-latest.log` continues to point at the running server. |
| Metrics | `--metrics` | A Prometheus endpoint at `/metrics`. |

You can override the settings with environment variables (`CTX`, `KV`, `KV_K`/`KV_V`, `UB`, `SPEC`, `SPEC_N`, `MODEL`, `ALIAS`, `HOST`, `PORT`, `LOG_FILE`, `THINK_TOGGLE`, `KEEP_AWAKE`), or in `config.json` ([Settings](#settings-configjson-auto-tune-and-the-catalogue)). Extra arguments go directly to `llama-server` (also `llama.extra_args` in `config.json`). `./carl.sh -h` lists all of them, with the current defaults.

### GPU memory limit and what fits

- **macOS sets a maximum for the memory that the GPU can use:** the Metal value `recommendedMaxWorkingSetSize`.
  - On this M3 Pro 36 GB, it is **28.1 GiB (78%)**.
  - Macs with 24–32 GB get approximately **2/3 of RAM**: ~16 GiB on a 24 GB Mac (estimate; not measured here). Macs with more than 32 GB get approximately **3/4**.
  - `sudo sysctl iogpu.wired_limit_mb=N` overrides this limit until the next reboot.
- **`./carl.sh fit`** (`tools/llama-fit.py`) calculates these values for each model (the catalogue and the models folder):
  - **Need** = weights (file size) + KV cache (window × bytes per token) + recurrent state + ~1 GiB for compute buffers and the MTP draft context.
  - The largest context window for each KV type that keeps need ≤ limit.
- **Sources of the inputs:**
  - **The limit:** a Swift probe that runs one time (`tools/metal-limit.swift`, cached in `~/models/.metal-limit`). An `iogpu.wired_limit_mb` override has priority. Without Swift, the script uses the 2/3–3/4 estimate.
  - **Model shapes:** the local GGUF. For models that are not downloaded yet, the script gets the first 24 MB with an HTTP range request (cached in `~/models/.gguf-shapes.json`).
- **Results:**

| Model | Weights | This Mac (28.1 GiB) | 24 GB Mac (~16 GiB) | 24 GB, limit raised to 18 GiB |
|---|---|---|---|---|
| `qwen3.8-27b` (Q4) | 15.3 GiB | 256K | does not fit | ~84K |
| `orcarouter-27b` (Q4) | 16.6 GiB | 256K | does not fit | ~16K |
| `qwen3.8-27b-q3` | 12.2 GiB | 256K | ~148K | 256K |
| `orcarouter-27b-q3` | 13.6 GiB | 256K | ~68K | ~184K |
| `qwen3.6-35b-a3b` | 21.1 GiB | 256K | does not fit | does not fit |
| `qwen3.6-35b-a3b-iq3` (24 GB default) | 13.1 GiB | 256K | 256K (2 × 96K slots: ~15.3 GiB) | 256K |
| `qwen3.8-27b-iq3` | 10.2 GiB | 256K | 256K (2 slots: ~128K each) | 256K |

- **On a 24 GB Mac, the Q3 27B entries get only 1 slot.** Thus, `install.sh` does not install the coder subagent there. The IQ3 35B gets 2 × 96K slots. The IQ3 27B gets 2 × 96K slots (the default window). `orcarouter-27b-q3` is the one exception to the 96K floor: its catalogue window is 64K, because 96K does not fit a 24 GB Mac (~68K, estimate) and larger Macs use the Q4 build. An untuned start prints a note to run `./carl.sh tune orcarouter-27b-q3`; Auto-tune selects 96K wherever it fits.
- **16 GB Macs (~10.7 GiB limit, estimate):** no catalogue model fits under the default limit. The IQ3 27B (10.2 GiB of weights) needs a raised limit (`sudo sysctl iogpu.wired_limit_mb=…`). `./carl.sh fit --ram 16` shows the numbers.
- **The ~1 GiB buffer allowance is a conservative estimate.** On the 35B, the monitor measured ~0.5 GiB of "other" memory.
- **The fit check does not include** the RAM that the rest of macOS and its apps need.
  - CAUTION: Keep ≥ 6 GB free. Monitor the pressure line on the SYSTEM card.

### Context length: what longer windows cost

The default is **96K tokens per slot** (both slots), for all models. **96K is a floor:** users need that much context to work. The catalogue, the custom-model defaults and Auto-tune never select less if 96K fits. The Settings tab warns (yellow, red) only about windows above 96K. You can still select a larger window. Longer context windows work. But each cold read of a long session is slower, and each new token is slower. The table shows measurements on the 35B (q4_0 KV, 2 slots, M3 Pro 36 GB, `tools/llama-kv-longctx.py`, 2026-10-01):

| Session size | Cold prompt read | Time to read it all | Decode (prose) | Decode (re-emit) | Recall (8 needles) |
|---|---|---|---|---|---|
| ~64K | 229 tok/s | ~4.8 min | 20.0 tok/s | 46.3 tok/s | 8/8 |
| ~128K | 117 tok/s | ~19 min | 13.9 tok/s | 33.1 tok/s | 8/8 |
| ~150K | 97 tok/s | ~27 min | 12.1 tok/s | 17.0 tok/s | 8/8 |

- **Cold reads are important after a restart or an eviction.** Follow-up turns use the cache again and read only the new tokens. At these depths, 434–477 new tokens took 4–11 s.
- **To use a longer context window:**
  1. Start the server with `./carl.sh llama --ctx 128k` (or `160k`).
  2. Run `client/install.sh` again, so that the client limit matches.
  - On the 35B, the additional KV is small (5.6 KiB/token at q4: 2 × 160K = 1.75 GiB).
  - On the 27B, it is 18 KiB/token (2 × 160K = 5.6 GiB).
- **Both slots always get the same context window.** A shared pool smaller than slots × window was tested and rejected. Two long sessions used more than the pool. Then **both requests failed** with HTTP 500 "Context size has been exceeded" after minutes of work (40K/64K test pool, two ~36K prompts). There is no graceful truncation.
- **Memory on 36 GB:** The 35B (21 GB weights) and a 4 GB VM leave little memory for macOS. Swap reached 3–6 GB during the 128K and 150K runs. Monitor the SYSTEM card of the monitor.

### Context memory: KV cache and recurrent state

Qwen3.8 (`qwen35`) and Qwen3.6-35B-A3B (`qwen35moe`) are **hybrid** models.
- **Only every 4th layer is a full-attention layer** with a KV cache (`full_attention_interval = 4`). The other three layers are Gated-DeltaNet layers. These layers have a recurrent state of fixed size, which does not grow with the context.
- **The MTP head is one more attention layer** (`nextn_predict_layers = 1`).

**Formulas** (from the GGUF metadata; the monitor uses the same formulas):
- **KV bytes per token** = attention layers × KV heads × (key length + value length) × bytes per element.
  - Bytes per element: f16 2, q8_0 34/32, q4_0 18/32 (32-value blocks).
- **Recurrent state** = recurrent layers × [heads × 128 × 128 × 4 B (f32 state) + 3 × (inner size + 2 × groups × 128) × 4 B (conv state)].

| | Qwen3.8-27B (`qwen35`) | Qwen3.6-35B-A3B (`qwen35moe`) |
|---|---|---|
| Layers | 65 = 16 attention + 48 recurrent + 1 MTP | 41 = 10 attention + 30 recurrent + 1 MTP |
| KV heads × (K + V) length | 4 × (256 + 256) | 2 × (256 + 256) |
| **KV per token** q4_0 / q8_0 / f16 | 18 KiB / 34 KiB / 64 KiB | 5.6 KiB / 10.6 KiB / 20 KiB |
| KV at 96K, q4_0 | 1.69 GiB | 540 MiB |
| **KV at 128K** q4_0 / q8_0 | **2.25 GiB** / 4.25 GiB | **720 MiB** / 1.33 GiB |
| KV at 256K, q4_0 | 4.5 GiB | 1.41 GiB |
| Recurrent state (per sequence) | ~150 MiB | ~63 MiB |
| Context checkpoints (up to 8) | ≤ 8 × ~150 MiB = 1.2 GiB | ≤ 8 × ~63 MiB = 0.5 GiB |
| Prompt cache (`--cache-ram`, auto at 96K × 2 slots) | q4_0 4864 MiB (~276K tokens), q8_0 1792 MiB (~54K tokens) | q4_0 2560 MiB (~466K tokens), q8_0 1792 MiB (~173K tokens) |

- **Cross-checks:**
  - The 2.25 / 4.25 GiB of the 27B at 128K agree with the values that were measured before.
  - The ~150 MiB recurrent state agrees with the ~150 MiB checkpoints that llama.cpp reported.
  - The KV of the MTP head (one more layer, ~1/16 of the KV of the 27B) is an estimate. It was not checked if llama.cpp allocates it for the full window.
- **The server allocates the full KV cache at start.** "In use" is only the part of it that contains data.
- **Result for the 35B:** The KV cache costs little memory. At 128K, `--kv q8` costs approximately 0.6 GiB more. A 256K q4 window costs approximately 0.7 GiB more than 128K. With q8 at 96K, the 35B read prompts approximately 4% faster, and the decode speed did not change (see the next section).
- **Result for the 27B:** The KV cache is the largest part of the context cost. For this reason, q4_0 was selected (−2 GiB at 128K vs q8_0).

### RAM prompt cache and checkpoints (measured 2026-10-02)

The server has 2 slots. When a third conversation starts, it takes the slot of the conversation that was used least recently. The server keeps the evicted prompt in the **RAM prompt cache** (`--cache-ram`). When that conversation continues, the server copies the prompt back from RAM. It does not read the prompt again.

- **The launcher sets the cache size automatically.** The size is the free memory after the weights, the KV cache and a reserve for macOS (10 GB when the VMware network is up, otherwise 6 GB). The limits are 1024 MiB and 8192 MiB.
- **Test:** the 35B, 2 × 96K, three conversations of ~20K tokens each, then one follow-up in each conversation. Then a turn with thinking, and the turn after it.

| Configuration | Cold read of each prompt | Follow-up after eviction | Turn after a thinking turn | Swap |
|---|---|---|---|---|
| Default: cache 2560 MiB, 8 checkpoints every ≥4096 tokens | 44–47 s (~450 tok/s) | **0.7–1.2 s** (22 tokens read) | 26 tokens read | 0.90 GB |
| `--cache-ram 0` | the same | **40–45 s** (~19K tokens read again) | 26 tokens read | 0.90 GB |
| 16 checkpoints every ≥1024 tokens | the same | 0.7–1.1 s | 26 tokens read | 0.90 GB |
| `--kv q8` (cache 1792 MiB) | 42–45 s (~470 tok/s) | 0.8–1.2 s | 26 tokens read | 1.26 GB |

**Results:**
- **The RAM prompt cache is necessary.** Without it, a conversation that comes back after an eviction is read again in full: 40–45 s for 20K tokens, and minutes for a long session.
- **A larger cache gives no gain on the 35B.** 2560 MiB holds approximately 466K tokens of q4_0 KV. This is almost five full 96K windows.
- **More checkpoints give no gain.** Each turn adds to the end of the conversation. The server keeps earlier reasoning (`preserve_thinking`). Thus, the cached prompt stays the start of the new prompt, and the server reads only the new tokens.
- **q8_0 fits at 96K on 36 GB.** On the 35B, it reads approximately 4% faster, with the same decode speed and 0.36 GB more swap. q4_0 stays the default, because it uses less memory and has the recall tests.
- NOTE: **The 27B with `--kv q8` has a small cache.** The automatic size is 1792 MiB. At 34 KiB for each token, this holds only ~54K tokens. Thus, the server reads an evicted long 27B conversation again in full. With q4_0, the 27B gets 4864 MiB (~276K tokens), which is sufficient.

## 3. Model and quantization choices

### Why these models

| Model | Why it is here |
|---|---|
| unsloth Qwen3.8-27B UD-Q4_K_M (`qwen3.8-27b`) | Stock (not abliterated) 27B. It is the smallest Q4 build of the 27B (16.5 GB), so it fits on more Macs. It has the same architecture and speed profile as the measured orcarouter build. |
| orcarouter Qwen3.8-27B Q4_K_M (bartowski, `orcarouter-27b`) | Abliteration with published figures (0–2.7% refusals, MMLU +0.4). Its imatrix was calibrated with a large quantity of tool calls. MTP head in the file. Q4_K_M is the nearest GGUF to the Q4_K_L class that was requested first. |
| unsloth Qwen3.6-35B-A3B UD-Q4_K_M (MTP build, **default**, `qwen3.6-35b-a3b`, the `default` of `host/catalog.json`) | Stock MoE with 3B active. It has approximately 4× the decode speed and 6× the prompt read speed of the 27B. Its KV cache is very small, so long sessions and 2 slots cost little. It is the default since 2026-10-01 (the dense 27B is heavy for general use). |
| unsloth Qwen3.8-27B UD-Q3_K_XL (`qwen3.8-27b-q3`, 13.1 GB) | For 24 GB Macs: ~148K window under a 16 GiB GPU limit (estimate). The unsloth "dynamic" 3-bit format keeps sensitive layers at higher precision. |
| unsloth Qwen3.6-35B-A3B UD-IQ3_XXS (`qwen3.6-35b-a3b-iq3`, 14.1 GB) | **Default on 24 GB Macs** (catalogue `default_small`, selected when the main default cannot fit). Its MoE KV is very small (5.6 KiB/token at q4), so 2 × 96K slots fit in ~15.3 GiB. Thus, subagents work on these Macs. IQ formats unpack more slowly on Metal than K-quants, but only 3B parameters are active for each token: ~45–50 tok/s on an M2 Max (measured 2026-10-03, [below](#iq3-speculation-measured-2026-10-03)). Speculation: MTP + n-gram, 1 draft. |
| unsloth Qwen3.8-27B UD-IQ3_XXS (`qwen3.8-27b-iq3`, 10.9 GB) | The smallest 27B build. For 24 GB Macs that want the dense 27B with 2 slots (2 × 96K fit), or a long window with 1 slot. It does not fit a 16 GB Mac under the default GPU limit (catalogue `min_ram_gb` 24; on 16 GB it needs a raised GPU limit). IQ3 loses more quality than Q3_K/Q4 in tool calls and edits: use it only when nothing larger fits. ~10 tok/s on new text, 31 tok/s on a re-emit (M2 Max). Speculation: MTP + n-gram, 1 draft. |
| orcarouter Qwen3.8-27B Q3_K_M (`orcarouter-27b-q3`, 14.6 GB) | Abliterated, for 24 GB Macs: ~68K window (estimate). Q3_K_L (15.3 GB) and Q3_K_XL (16.4 GB, bigger than the stock Q4) were not used. |

- **All seven entries have the MTP head in the file.** For five entries, this was checked in the GGUF headers: `nextn_predict_layers = 1` and the four `nextn` tensors in block 64. For the two IQ3 builds, `nextn_predict_layers = 1` was checked on 2026-10-03, and MTP drafting ran on the 35B IQ3. Auto-tune reads the header, and tests MTP only if the head is there.
  - unsloth also publishes the head as a separate file (`MTP/mtp-Qwen3.8-27B-Q4_0.gguf`). But the main unsloth files include the head, so you do not need that file.
- **Q3 entries use the model name of their Q4 sibling**, so client configs work without change.
- **The default model is `default` in `host/catalog.json`.** If that model cannot fit one window, the launcher uses the default for small Macs (`default_small`, `qwen3.6-35b-a3b-iq3`). If the selected default is not downloaded, the launcher uses the first downloaded model. `./carl.sh fit` shows which model this Mac gets.

**Policy:** Use only models with a known and documented uncensoring method. Do not use "uncensored" fine-tunes with an undisclosed method, even if they are popular.

### IQ quants (2026-09-24 study; IQ3 builds adopted 2026-10-03)

- **On a 36 GB Mac, IQ quants are not used.** The 2026-09-24 study (below) found the gain too small and the quality loss too large for the 27B.
- **Since 2026-10-03, two IQ3 builds are in the catalogue,** for Macs where no larger file fits: `qwen3.6-35b-a3b-iq3` (the 24 GB default) and `qwen3.8-27b-iq3` (the smallest 27B).
- **Their speculation:** MTP + n-gram with 1 draft token, as on the 27B K-quants. MTP alone helps much less than on the K-quants, and 2 drafts lose ([IQ3 speculation](#iq3-speculation-measured-2026-10-03)).

#### The 2026-09-24 study (27B, IQ4_XS and IQ3_M)

The candidates were IQ4_XS and IQ3_M from `HauhauCS/Qwen3.8-27B-uncensored-hauhaucs-aggressive-mtp-gguf`.

| Quant | File size | vs Q4_K_M (17.77 GB) | Decode ceiling | Expected real decode gain | Quality |
|---|---|---|---|---|---|
| IQ4_XS | 15.71 GB | −2.1 GB | ~+13% | ~+8–12% | usually within ~1–2% of Q4_K_M on perplexity |
| IQ3_M | 12.79 GB | −5.0 GB | ~+39% | ~+10–25% | clearly worse (several %); more errors in tool calls and edits |

The gains are smaller than the size difference suggests, for these reasons:
- **Decode speed follows the bytes read for each token** (mostly weights). This sets the ceiling in the table.
- **IQ formats cost more to unpack on Metal than K-quants.** IQ4_XS costs little. IQ3_M uses a lookup-table format that is known to be slow per byte on Metal.
- **Prompt read speed is compute-bound**, so both IQ quants would probably read prompts slightly *slower* than Q4_K_M.
- **Speculation does not change this result.** MTP drafts use the same kernels. Also, the MTP speed-up in llama.cpp PR #29110 applies only to Q4_0/Q8_0 weights.

The HauhauCS model also has differences from orcarouter that are not related to the quant:
- **Method not disclosed:** It claims "aggressive" uncensoring with 0/465 refusals. But it has no published KL, MMLU or coding figures. It does not state if it uses an imatrix. It is very popular (~2M downloads).
- **"Minimal preamble":** It can give shorter answers. This could save more time than the quant.
- **FastMTP:** Its "up to 3×" claim needs a patched llama.cpp build, and was measured on NVIDIA. Homebrew llama.cpp cannot run it. The standard MTP head works as usual.

**Decision (2026-09-24): not adopted on 36 GB.** The reasons are:
- The gain was small.
- IQ3_M loses too much quality for agent work.
- The HauhauCS method is not documented.

If you examine this again, do these steps:
1. Test **orcarouter IQ4_XS** first (bartowski, 15.57 GB). It is the same model, so the test isolates the effect of the quant.
2. Use `tools/llama-spec-sweep.sh`, the 66K recall test (`tools/llama-kv-longctx.py`) and a real OpenCode session.

IQ3_M is a good choice only where no larger file fits, for example on a 24 GB Mac. This is the role of the IQ3 builds in the catalogue now.

#### IQ3 speculation (measured 2026-10-03)

**Setup:** Apple M2 Max 32 GB, llama.cpp 0.5.0 (build 11146), 400-token runs, thinking off. The values are the decode speed in tok/s (the mean of 2 runs): prose / code / re-emit (the model writes code again). The bold row is the catalogue setting.

**`qwen3.6-35b-a3b-iq3`** (unsloth UD-IQ3_XXS, 14.1 GB):

| Speculation | Prose | Code | Re-emit |
|---|---|---|---|
| none | 49 | 45 | 46 |
| MTP, n=1 | 47 | 44 | 46 |
| MTP, n=2 | 40 | 41 | 49 |
| n-gram (`ngram-mod`, n=2) | 47 | 42 | 117 |
| **MTP + n-gram, n=1** | **49** | **50** | **93** |
| MTP + n-gram, n=2 | 42 | 43 | 83 |

**`qwen3.8-27b-iq3`** (unsloth UD-IQ3_XXS, 10.9 GB), same setup:

| Speculation | Prose | Code | Re-emit |
|---|---|---|---|
| none | 9.4 | 8.9 | 8.9 |
| MTP, n=1 | 9.7 | 9.8 | 11.0 |
| MTP, n=2 | 8.9 | 9.4 | 11.3 |
| n-gram (`ngram-mod`, n=2) | 9.8 | 9.6 | 21.5 |
| **MTP + n-gram, n=1** | **10.0** | **9.8** | **31.1** |
| MTP + n-gram, n=2 | 8.7 | 9.7 | 23.3 |

**Results:**
- **MTP alone helps little on IQ3.** On the 35B IQ3, it adds about nothing. On the 27B IQ3, it adds only ~5–10%. On the Q4 builds (M3 Pro), MTP alone added 27–50% ([section 12](#12-performance-measured)). The IQ kernels make the verification of the drafts expensive. Thus, for MTP alone, the hypothesis "IQ quants do not do well with MTP" is correct.
- **2 drafts lose on IQ3.** On the 35B IQ3, MTP n=2 is about 15% slower on new text than no speculation.
- **MTP + n-gram with 1 draft is the best on both models.** With the Auto-tune score (weighted geometric mean: prose 0.4, code 0.4, re-emit 0.2), it is about 4–5% better than n-gram alone on the 35B IQ3, and about 9% better on the 27B IQ3. n-gram alone is better only on a code re-emit on the 35B (117 vs 93 tok/s).
- **Thus, the catalogue tune for both IQ3 builds is `draft-mtp,ngram-mod` with n=1.** The earlier setting of the 35B IQ3 (MTP + n-gram, n=2, copied from the Q4) was the second slowest mode on new text. The Q4 35B keeps n=2 (measured on the M3 Pro).
- The Settings tab shows MTP with more than 1 draft on an IQ quant in red. To measure a model on your own Mac, run `./carl.sh tune NAME`.

### The coder subagent

- **One definition, two clients.** `client/agents/coder.md` contains the definition:
  - The frontmatter `description` tells when to use the coder. The main model reads this text.
  - The body contains the instructions for the coder:
    - understand
    - plan
    - small verified steps
    - reproduce before you fix
    - no placeholders
    - stop after three failed approaches
    - structured report.
  - `client/agents/delegation.md` contains the rule for the main agent. This rule is added to the system prompt.
- **When the coder is used:** The main agent decides before the first tool call. It uses the coder only in these conditions:
  - It is **stuck**: two fixes failed (its own fixes, or fixes that the user reports as failed).
  - The task is **large**: 3+ files, ~150+ lines, a new module/package/CLI, implementation plus tests, or a multi-step refactor.
  - All other work stays in the main session.
- **OpenCode:** `agent.coder` in `opencode.json`:
  - `mode: subagent`, `prompt: {file:…/.config/opencode/llm-deploy/coder.md}` (an absolute path; the rule is `~/.config/opencode/llm-deploy/delegation.md`; earlier versions used `~/.config/opencode/prompts/`, and the installer removes those files);
  - `options.reasoningEffort: medium` (the main session uses low; on the 35B, it only means thinking on);
  - `temperature: 0.6` (see "Coder sampling" below);
  - `permission.task: deny` (no nested subagents), `steps: 80`, `color: secondary`.
  - The rule is added through `instructions`. The installer adds it to the instructions that you already have.
- **Pi:**
  - Pi uses its official `subagent` example extension. A vendored copy is in `client/pi/extensions/subagent` (MIT, pi-coding-agent 1.0.0).
  - The copy has a patch: the tool description lists the installed agents and their descriptions. The upstream version lists no agents, so the model could not select one itself.
  - The coder runs as a separate `pi --mode json -p --no-session` process with the same model. It has the tools read, bash, edit, write, grep, find, ls. It does not have `subagent`, so it cannot start nested subagents.
  - The rule is a marked block in `~/.pi/agent/APPEND_SYSTEM.md`.
- **When `install.sh` installs it:** if the server has 2+ slots. `CODER=1` / `NO_CODER=1` override this.
- **Same model, different context.** The server loads only one model, so the coder does not add capability. The coder gets a new context with only the task, more reasoning and a stricter method. With 2 slots, the main session keeps its cache while the coder runs.
- **Reason for the rule:** With only the agent description, the 35B never delegated. It did 4/4 tasks itself. These tasks included a multi-file package and a bug that the user reported as stuck.


**Coder sampling (measured 2026-10-02):** the 35B (2 × 96K, q4_0) did the same task 3 times for each setting. The task was a new package with a CLI that parses real llama-server log lines, plus tests. The main agent sent it to the coder each time.

| Coder setting | Average time | Tests written | Typed functions | Packaging file builds |
|---|---|---|---|---|
| Thinking on, temperature 1.0 | 405 s | 16, 18, 12 | 10/13 | 1 of 2 |
| **Thinking on, temperature 0.6** | **299 s** | **35, 18, 4** | **20/20** | 1 of 3 |
| Thinking off (0.7 / 0.8 / presence 1.5) | 234 s | 9, 9, 7 | 10/10 | 0 of 3 |

- All 9 runs passed their own tests, and all parsed the real log correctly.
- **Selected: thinking on, temperature 0.6.** It typed every function, wrote the most tests on average, and was 26% faster than 1.0. It also agrees with the Qwen model cards, which give 0.6 for precise coding.
- Thinking off was the fastest. But it wrote about half the tests, and none of its packaging files built.
- 6 of 8 packaging files (`pyproject.toml`) did not build, with all settings. Thus, the coder prompt now has a "Builds" item in its definition of done.
- NOTE: 3 runs for each setting give a direction, not proof. The test count for one setting went from 4 to 35.
- NOTE: **The 27B coder uses the same setting, but it did not get its own test.** A full test of the 27B is a v2 item.
- NOTE: **Pi has no temperature for subagents.** Its subagent extension passes only the thinking level. Thus, the Pi coder uses the server temperature (1.0).

---

## 4. How thinking works

Qwen models write hidden reasoning between `<think>` and `</think>`, before the answer. The **chat template** is a Jinja program in the model file. It decides if that block opens. It uses variables that the server gives to it:

| Template variable | Meaning |
|---|---|
| `enable_thinking` | `false` → the template writes an empty, closed `<think></think>`, so the model answers directly. Not defined or `true` → thinking on. |
| `reasoning_effort` | Qwen3.8 only: sets how much the model thinks. Qwen3.6 does not have this variable. |
| `preserve_thinking` | `true` → reasoning from earlier turns stays in the prompt (necessary for prompt-cache reuse) |

llama.cpp gives these request fields to the template:
- top-level `reasoning_effort`
- all fields in `chat_template_kwargs`
- the server-wide `--chat-template-kwargs '{"preserve_thinking":true}'`.

`--reasoning-format deepseek` moves the thinking text into the `reasoning_content` field of the response.

**The patch.** OpenCode can send only `reasoning_effort`. The Qwen3.8 template has no effort value that means "off". For this reason, `host/serve-llama.sh` extracts the template from the GGUF (`host/gguf-chat-template.py`). It then adds one rule at the start. The rest of the template does not change:

```jinja
{%- if reasoning_effort is defined and reasoning_effort in ('none', 'minimal', 'off', 'disable', 'disabled') %}{%- set enable_thinking = false %}{%- endif %}
```

The patched copy is cached as `~/models/templates/<model>.thinking-toggle.jinja`. It is generated again when the GGUF or the script changes. Models with no `enable_thinking` in their template get their stock template.

---

## 5. Thinking by model

| | Qwen3.8-27B (abliterated orcarouter; stock unsloth) | Qwen3.6-35B-A3B (stock unsloth) |
|---|---|---|
| On/off switch | `enable_thinking` | `enable_thinking` |
| Effort levels | `low`, `medium`, `xhigh` | none: the template ignores all `reasoning_effort` values |
| Default if the request sends nothing | thinking on, effort `xhigh` | thinking on |
| Invalid effort (e.g. `high`, or `none` without the patch) | **template raises an error**, and the request fails | ignored |
| Measured (same prompt, "Is 91 prime?") | `low` 32–39 reasoning chars, `xhigh` 115–165, off 0 | low 1027, high 861, xhigh 984, off 0 |
| `preserve_thinking` | supported | supported |

- The stock Qwen3.8-27B (`qwen3.8-27b`) was not downloaded or checked. Its template is assumed to be the same as the template of the abliterated build.
- **Abliteration does not change thinking.** It removes refusals.
- **Qwen3.8 thinks too much at `xhigh`** on simple tasks. `low` is the default for agent work.

---

## 6. Thinking by client

### What each client sends

| Client → server | Config mechanism | Thinking on | Thinking off |
|---|---|---|---|
| OpenCode → llama.cpp | `@ai-sdk/openai-compatible`, `options.reasoningEffort` + variants | top-level `reasoning_effort: "<level>"` | top-level `reasoning_effort: "none"` (patched template → `enable_thinking=false`) |
| Pi → llama.cpp | `thinkingFormat: "chat-template"` + `chatTemplateKwargs` | `chat_template_kwargs {enable_thinking: true, preserve_thinking: true, reasoning_effort: "<level>"}` | `chat_template_kwargs {enable_thinking: false, preserve_thinking: true}` (no effort value) |

**OpenCode precedence:** provider options → model `options` → agent `options` → **variant**.
- OpenCode merges the variant last, so the variant has priority.
- If the variant name is unknown (old config), OpenCode uses the base options of the model. It gives no warning.
- For this reason, fully restart OpenCode after `install.sh`.

---

## 7. The full thinking matrix

What you select → what occurs.

### OpenCode (`/variants`, ctrl+t)

| Model entry | Selection | Sent | Result |
|---|---|---|---|
| `llamacpp/qwen3.8-27b-abliterated-llama` | `none` | `reasoning_effort: none` | **off** (0 reasoning) |
| | `low` (default) | `low` | short thinking |
| | `medium` | `medium` | medium |
| | `xhigh` | `xhigh` | long thinking |
| `llamacpp/qwen3.6-35b-a3b` | `none` | `none` | **off** |
| | `high` (default) | `high` | on (the template ignores the level) |

`minimal` and `high` are disabled for the Qwen3.8 entries. `minimal` would only repeat `none` ("off"). `high` is not a Qwen3.8 level (the template raises an error). For the 35B, `low`, `medium` and `xhigh` are disabled, because they have no different effect.

### Pi (thinking level)

| Model | Levels offered | `off` | Other levels |
|---|---|---|---|
| `qwen3.8-27b-abliterated-llama` | off, low, medium, xhigh | `enable_thinking: false` → off | sent as `chat_template_kwargs.reasoning_effort` |
| `qwen3.6-35b-a3b` | off, high | off | `high` = on (level ignored) |

The Pi `thinkingLevelMap` hides levels with `null`. `minimal` is hidden for all models.

### Raw API (curl, scripts)

| Goal | llama.cpp |
|---|---|
| Thinking off | `"reasoning_effort":"none"` (patched) or `"chat_template_kwargs":{"enable_thinking":false}` |
| Set a level (Qwen3.8) | `"reasoning_effort":"low"\|"medium"\|"xhigh"` |
| Avoid | `"reasoning_effort":"high"` on Qwen3.8 (template error) |

**Timing:** A change applies from the next message. A reply that is in progress keeps its mode.

---

## 8. Sampling and output limits

### Qwen's recommendations (model cards, checked 2026-10-01)

| Mode | temperature | top_p | top_k | min_p | presence | repetition |
|---|---|---|---|---|---|---|
| Qwen3.8, thinking | 1.0 | 0.95 | 20 | 0 | 0 | 1.0 |
| Qwen3.8 / Qwen3.6, non-thinking (instruct) | 0.7 | 0.80 | 20 | 0 | 1.5 | 1.0 |
| Qwen3.6, thinking, general tasks | 1.0 | 0.95 | 20 | 0 | 1.5 | 1.0 |
| Qwen3.6, thinking, precise coding (e.g. WebDev) | 0.6 | 0.95 | 20 | 0 | 0 | 1.0 |

Qwen ran its agentic coding benchmarks (SWE-bench, Terminal-Bench, Claude Code harness) at temperature 1.0, top_p 0.95 for both models. The card says that presence 0–2 decreases endless repetition. But higher values can cause language mixing.

### What actually runs

| Situation | Sampling | Source |
|---|---|---|
| Thinking on, any llama.cpp model (normal use) | 1.0 / 0.95 / 20 / 0 / presence 0 / repetition 1.0 | `serve-llama.sh` sets these values explicitly (`TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE` override them). The clients send no sampling fields. The capture proxy showed only `model`, `stream`, `store`, `reasoning_effort`. |
| OpenCode `none` variant (thinking off) | 0.7 / 0.80 / 20 / 0 / presence 1.5 | The variant sends `temperature`, `top_p`, `presence_penalty` with `reasoning_effort: none`. OpenCode forwards variant options into the request body. This is the same mechanism that carried `chat_template_kwargs` in the 2026-09-24 captures. These fields were not captured again yet. |
| Pi `off` (thinking off) | thinking-mode values | Pi changes only `chat_template_kwargs` for each level. It cannot change the sampling for each level. Small effect: direct answers are slightly more random. |

- **Reason for explicit flags:** The five catalogue GGUFs that were checked contain 1.0 / 20 / 0.95 (`general.sampling.*`). The flags also cover the two IQ3 builds and custom models, which were not checked. But a GGUF without these values would get the generic llama.cpp defaults with no warning (temperature 0.8, top_k 40, min_p 0.05).
- **35B-A3B choices:** Temperature 1.0 with presence 0 agrees with the Qwen agentic benchmark settings.
  - If the model loops or repeats text, start the server with `PRESENCE=1.5` (the general recommendation of the card).
  - For precise code work, use `TEMP=0.6`.
  - Neither setting was compared here.

| | llama.cpp |
|---|---|
| Output cap | None by default (`n_predict -1`): the model generates until it stops or the context is full |
| Client sends | OpenCode: no `max_tokens` (seen with the capture proxy). Pi: `max_tokens` = `maxTokens` (`install.sh` caps it at half the window). |

NOTE: The presence 1.5 of the non-thinking variant is the Qwen recommendation for that mode.

---

## 9. Context limits: server vs client

- **The server setting `--ctx` is the real limit.**
- **The client limit only decides when the client compacts.** The client limits are `limit.context` in OpenCode and `contextWindow` in Pi. The clients do not send these values to the server.
- **Client limit above server limit:** When the session is larger than the server context window, each request fails with `HTTP 400 exceed_context_size_error`. The client never compacts by itself.
- **Server limit above client limit:** This causes no problem. The extra memory is not used.
- **`client/install.sh` keeps the two limits the same.** The order of the sources is: `LLAMA_CTX`, then `/props` → `default_generation_settings.n_ctx` of the running server, then 96K (98304). It caps the output at half the context window. After you change `--ctx`, run it again. Then restart the client.

---

## 10. Verifying behaviour

```bash
K=$(cat ~/.config/llm-deploy/api-key)

# Is thinking off? Look at reasoning_content length (llama.cpp)
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' http://192.168.42.1:8080/v1/chat/completions \
  -d '{"model":"x","reasoning_effort":"none","messages":[{"role":"user","content":"Is 91 prime?"}]}' \
  | python3 -c 'import json,sys; m=json.load(sys.stdin)["choices"][0]["message"]; print(len(m.get("reasoning_content") or ""), "reasoning chars")'

# Is the patched template loaded?
PID=$(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')
ps -o command= -p $PID | grep -o -- '--chat-template-file [^ ]*'
head -2 ~/models/templates/*.thinking-toggle.jinja

# Server sampling defaults and context
curl -s -H "Authorization: Bearer $K" http://192.168.42.1:8080/props | python3 -c 'import json,sys; d=json.load(sys.stdin)["default_generation_settings"]; print(d["n_ctx"], {k: d["params"][k] for k in ("temperature","top_k","top_p","min_p")})'
```

**To capture what a client really sends** (thinking fields, tool count, reasoning returned): Run `tools/req-capture-proxy.py` in front of the server.

```bash
HOST=127.0.0.1 PORT=8081 ./carl.sh llama &      # the server, behind the proxy
python3 tools/req-capture-proxy.py 192.168.42.1:8080 127.0.0.1:8081 ~/models/logs/req-capture.jsonl
```

- The proxy does not log the message text.
- Each JSON line has `req` (`reasoning_effort`, `chat_template_kwargs` and the other request fields), `n_messages`, `n_tools`, `reasoning_chars` and `content_chars`.
- The proxy writes an entry when the response is complete. Thus, a long cold prompt shows nothing until it is complete. The first OpenCode prompt (~9K tokens) takes approximately 2 min on the 27B.
- If the request has only the base `reasoning_effort`, the client did not apply the variant. Run `client/install.sh` again and fully restart OpenCode. Then capture again.

---

## 11. OpenCode config

The installer writes these settings into `~/.config/opencode/opencode.json` (template: `client/opencode/opencode.json`):

| Provider / model | Context | Thinking (`/variants`) | Default |
|---|---|---|---|
| `llamacpp/qwen3.6-35b-a3b` (also its IQ3) | server `--ctx` | `none` (off), `high` (on) | `high`; **the default model** |
| `llamacpp/qwen3.8-27b` (stock; also its Q3 and IQ3) | server `--ctx` | `none` (off), `low`, `medium`, `xhigh` | `low` |
| `llamacpp/qwen3.8-27b-abliterated-llama` (orcarouter; also its Q3) | server `--ctx` | `none` (off), `low`, `medium`, `xhigh` | `low` |

- **Timeouts:** the config sets `timeout: false` and `chunkTimeout: 900000` (15 min). A long cold prompt can take many minutes before the first token.
- **Title agent:** the title agent of OpenCode stays on (earlier versions disabled it). With 2 slots, it runs at the same time as the main session. It does not wait in a queue behind the main session.
- **Plugins:** `subagents-sidebar` and `session-switcher` are TUI plugins in `~/.config/opencode/tui.json`. To update them, run `install.sh` again and restart OpenCode.
- **Coder subagent:** see [The coder subagent](#the-coder-subagent).
- **Pi** (`~/.pi/agent/models.json`, `~/.pi/agent/settings.json`): defaults (only if they are unset or still ours) are provider `llamacpp`, model `qwen3.6-35b-a3b`, thinking `low`. For the thinking format, see [section 6](#6-thinking-by-client).

---

## 12. Performance (measured)

All values come from this M3 Pro 36 GB with llama.cpp 0.4.1. The decode speed is in tok/s, for 400-token generations: prose / code / edit (the model writes a 1.9K-token file again).

| Speculation | Qwen3.8-27B (dense) | Qwen3.6-35B-A3B (MoE) |
|---|---|---|
| none | 7.4 / 7.3 / 7.3 | 33 / 34 / 33 |
| MTP, 1 draft | 11.1 / 11.4 / 11.4 | 42 / 41 / 44 |
| ngram-mod only | 6.9 / 6.9 / 17.1 | 33 / 33 / 67 |
| MTP + ngram, 1 draft | **10.5 / 10.9 / 27.3** | 44 / 44 / 70 |
| MTP + ngram, 2 drafts | 9.1 / 10.3 / 17.4 | **42 / 45 / 117** |

The bold rows are the catalogue settings (`tune.spec`, `tune.spec_n`): MTP + ngram is the best of 7 configs on both models. The 27B uses 1 draft. The 35B uses 2. The IQ3 builds use MTP + n-gram with 1 draft ([IQ3 speculation](#iq3-speculation-measured-2026-10-03)). Auto-tune (`./carl.sh tune NAME`) repeats this measurement on your Mac.

| | Qwen3.8-27B | Qwen3.6-35B-A3B |
|---|---|---|
| Prompt read, cold | ~85–90 tok/s at 2–9K; ~56–65 tok/s at 66K (56K ≈ 16 min) | ~490–580 tok/s at 2–9K |
| Append to a long session | ~35–45 tok/s (6K tokens ≈ 2.5–3 min); small follow-ups use the cache again (~5 s) | not measured |
| Decode far into a session | 6.5–8 tok/s at 60–80K | not measured |
| Memory (RSS, 128K, q4_0 KV) | ~20 GB (q8_0: ~22) | ~22.8 GB |
| KV cache at 128K, q4_0 / q8_0 (calculated) | 2.25 / 4.25 GiB (18 KiB/token at q4) | 0.70 / 1.33 GiB (5.6 KiB/token at q4) |
| Long-context recall | 8/8 needles at 66K, q4 and q8 | not measured |

For the 35B at 64K–150K, see [Context length](#context-length-what-longer-windows-cost).

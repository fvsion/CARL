# CARL Reference: Architecture and repository layout

[Index](../REFERENCE.md) · how the parts fit and every file in the repository.

## Architecture

```
 Mac (here: M3 Pro, 36 GB)                             Kali VM (VMware Fusion, 4 GB) -- optional
 ┌──────────────────────────────────────────┐          ┌──────────────────────────────────┐
 │ host/serve.sh  ──►  llama-server :8080    │  vmnet8  │ OpenCode ─┐                       │
 │   (default)         (llama.cpp, Metal)    │◄─────────┤           ├─ Bearer key from      │
 │                                           │  NAT     │ Pi ───────┘  ~/.config/carl/      │
 │                                           │          │ configs from client/install.sh    │
 │ binds 192.168.42.1 (VM) or 127.0.0.1      │          │ VM address 192.168.42.130         │
 │ monitor in the same terminal              │          │                                   │
 │ OpenCode / Pi here too (--local mode)     │          │                                   │
 └──────────────────────────────────────────┘          └──────────────────────────────────┘
      ~/models/gguf/      GGUF weights (catalogue: host/catalog.json; any other .gguf here is listed too)
      ~/models/logs/      server logs
      ~/.config/carl/config.json   your settings (Settings tab, ./carl.sh config)
      ~/.config/carl/models.json   custom models, their cards and Auto-tune results (this Mac)
      ~/models/templates/          patched chat templates (thinking toggle)
      ~/.config/carl/api-key       shared API key (server and clients; created on first start)
```

- **CAUTION:** Run only one server at a time. Each model needs about 14–23 GB, and two models do not fit in 36 GB. If you run two servers, GPU out-of-memory errors occur. The running server then stays broken until you restart it. The launchers refuse to start a second model ([section 2](server.md#llamacpp-details), "Launch guard").
- **The server uses the OpenAI API** (`/v1/chat/completions`, `/v1/models`) and a Bearer key.
- **Code layers on the Mac:** the shell launchers (`host/`) get their model and settings from `tools/carl.py`. `tools/carl.py` and `tools/gguf_shape.py` are thin facades over the package `tools/carl_core/`: a pure `domain/` (no I/O) and `adapters/` for the files, `sysctl` / `netstat`, Hugging Face, the downloads and `llama-server`. The monitor, `llama-fit.py` and `carl-tune.py` use the same facades ([Repository layout](#repository-layout)).
- **The client side is a self-contained bundle** (`client/`). You can run it in the VM (copy it through the Fusion shared folder) or on the Mac itself (`./carl.sh install`, or the dashboard's Connect tab: `i`).

## Repository layout

| Path | What it is |
|---|---|
| `carl.sh` | The launcher in the project root. It runs `host/serve.sh` with the same arguments: `./carl.sh` opens the dashboard, `./carl.sh -h` shows the help. `./carl.sh --no-start` opens only the dashboard (nothing loads). `./host/serve.sh` continues to work. |
| `CHANGELOG.md` | What changed in each release. |
| `assets/` | The logo and the README screenshots. `tools/tuishot.py` makes the screenshots. |
| `host/serve.sh` | The entry point on the Mac. Server command: `llama`. Tools: `monitor`, `fit`, `models`, `card`, `download`, `verify`, `tune`, `config`, `install`. `help [command]` shows the help for one command, and `-h` prints the overview. `--help-adv` lists every `llama-server` flag. With no arguments, it opens the dashboard. It gives `llama` to `serve-llama.sh`. |
| `host/common.sh` | `serve.sh` and `serve-llama.sh` use this file. It contains the network mode (`--local` by default, `--vm`, never 0.0.0.0), the API key (creation, and the one-time copy from the old path), `ensure_deps` (the Homebrew tools), `port_pid` / `port_host` (port lookups with `netstat`), `apply_settings` (reads the `KEY=value` lines of `tools/carl.py` with an allow-list; values are never evaluated), `require_int` and `is_port` (input checks), the launch guard and `run_server`. `run_server` runs the server in the background in its own process group, with the monitor in front. If there is no terminal, it runs a plain foreground server. |
| `host/serve-llama.sh` | The llama.cpp launcher: `--model`, `--kv q4\|q8`, `--ctx N\|Nk`, logging, keep-awake, thinking-toggle template. It gets the model and its settings from `tools/carl.py launch-env`. |
| `host/catalog.json` | The built-in model catalogue. For each model: `hf` (repo, pinned revision, file, SHA-256, size), `summary`, `description`, the model card (`role`, `good_for`, `why_use`, `trade_offs`, `pick_instead`, `hardware`, `uncensored`, `rank`), `tune` (kv, ctx, slots, spec, spec_n, sampling), `why` (the reason for each tuned value), `ctx_zones`, `measured`. `default` and `default_small` are the offline fallback of auto fit (when no GGUF header can be read). |
| `host/models.sh` | A thin wrapper around `tools/carl.py`: `list`, `download NAME\|default\|all\|hf:OWNER/REPO/FILE.gguf`, `verify`, `delete`, `path`, `get`, `default` (auto fit's pick), `downloaded`. |
| `host/gguf-chat-template.py` | Extracts the chat template of a GGUF. Then it adds the rule "`reasoning_effort: none` → thinking off" at the start of the template. |
| `client/install-clients.sh` | Run it in the VM or on a Mac. It installs OpenCode and/or Pi from npm into `~/.local`. If necessary, it also installs a Linux or macOS Node 22. It does not use sudo. |
| `client/install.sh` | Run it in the VM (`--vm`, default on Linux) or on the Mac (`--local`, default on macOS). It stores the API key. It makes a backup, then writes the OpenCode and Pi configs. It sets the context limit to the server value. It does a smoke test of the server. |
| `client/configure.py` | The non-destructive merge that `install.sh` uses (USERGUIDE.md, "Updating the client configs"). |
| `client/carl_models.py` | The OpenCode and Pi model entries, one per installed model (stdlib only: it runs in the VM, from `configure.py`, and in the dashboard's copy buttons). Its input is `client/installed-models.json` (`tools/carl.py client-models`, written by `install.sh` on the server Mac; gitignored), or the server's `/v1/models` ids. |
| `client/opencode/plugins/carl-model-check/` | OpenCode server plugin (`chat.params` hook): before each request to CARL's provider it reads the server's `/v1/models` (cached 5 s, 1.5 s timeout, errors ignored) and shows a TUI toast (and a line in OpenCode's log, `service=carl-model-check`), once per situation: the server runs another model (single mode), the model isn't installed (router: HTTP 400), or it is loading (router). OpenCode 1.18 loads a server plugin through its package's `exports["./server"]` (without it, the module loads and its hooks are never called); the entry in `opencode.json` is `["file:DIR", {"provider": ID}]`. Hooks run in the TUI and in `opencode run` (1.18.34). Verified 2026-10-03 (the 9B picked while the IQ3 ran: warning shown). |
| `client/opencode/plugins/carl-cache/`, `client/pi/extensions/carl-cache/`, `client/shared/carl-cache.js` | CARL's prompt cache ([Disk prompt cache](cache.md#disk-prompt-cache-per-agent-and-per-session-measured-2026-10-03)): the OpenCode plugin (wraps `fetch`; `chat.headers` marks the session and agent) and the Pi extension (`before_provider_request`, `message_end`) each carry a copy of the shared core, which `configure.py` copies next to them. Tests: `tests/js` (`node --test`, run by `tests/scripts/test_js.py`). |
| `client/opencode/plugins/carl-panel/`, `client/pi/extensions/carl-panel/`, `client/shared/carl-panel.js` | The `/carl` panel in OpenCode (a TUI plugin) and Pi (an extension): every CARL piece on this computer with its state, and the config sync's auto-apply switch ([Clients on other computers](client-sync.md)). |
| `client/carl-sync.py` | The client config sync: `watch` (the sync service: launchd / systemd --user, one outgoing connection to the dashboard's API), `once` (OpenCode and Pi without the service), `apply`, `auto on/off`, `status`. It runs `install.sh` again with the last switches. |
| `client/remote.json`, `client/api-key` | Written at every server start (git-ignored, 0600): the server's address and port, the dashboard's API, the key. A copy of the client folder needs nothing else. |
| `reference/` | This reference, one page per topic (`REFERENCE.md` is the index). |
| `client/opencode/opencode.json` | The OpenCode config template (the `llamacpp` provider). |
| `client/agents/coder.md`, `client/agents/delegation.md` | The **coder** subagent (OpenCode and Pi both use it): its "when to use" description and its work method. Also the delegation rule for the main agent: delegate only if the agent is stuck after two failed fixes, or if the task is large. |
| `client/pi/extensions/subagent/` | The official subagent extension for Pi (vendored, MIT). A patch makes it list the installed agents in the tool description. Thus, the model can delegate without help. |
| `client/opencode/plugins/subagents-sidebar/` | An OpenCode TUI plugin: a live list of subagents in the sidebar. Running subagents on top (agent, task, elapsed time, current tool, context); finished ones below (✓/✗, duration; the 5 newest for 5 minutes, then a `+N more` line). It resets when you go to a different parent session. Click to open. `install.sh` installs it into `tui.json`. |
| `client/opencode/plugins/session-switcher/` | An OpenCode TUI plugin: a session switcher in the prompt box (`‹ 2/3 ● title ›`), and `/switch` for a list of the recent sessions with their state. `install.sh` installs it into `tui.json`. |
| `client/pi/models.json` | The Pi config template. |
| `tools/carl_core/` | The models and settings logic, in layers. `domain/` is pure (no I/O): `types`, `settings`, `gguf`, `fit`, `autofit`, `models`, `cards` (the card fields, command-line values, joining a user's card into the model record), `hf`, `launch`, `records` (validation, including a user's card), `tuning`, `ports`. `adapters/` does the I/O: `json_files`, `filesystem`, `gguf_reader`, `system` (sysctl, the GPU limit, the `netstat` parser), `huggingface`, `downloader`, `llama_server`, `console`. `app.py` holds the use cases, and `wiring.py` connects the adapters. |
| `tools/carl.py` | A thin facade over `tools/carl_core` (its CLI and the API of the monitor). One place for the models and the settings: the catalogue, the models on this Mac (catalogue, models folder, Hugging Face downloads), `config.json` (validated) and the settings order. Downloads (pinned, SHA-256 verified), `launch-env` for the launchers, `config show\|get\|set\|unset\|path`, `card NAME [set FIELD VALUE\|unset FIELD]`. The launchers, `llama-fit.py` and the monitor use it. |
| `tools/carl-tune.py` | Auto-tune (`./carl.sh tune NAME [--quick]`): memory fit, speculation modes, prompt reading at 8K/32K/64K, context zones. It saves the result in `~/.config/carl/models.json`. |
| `tools/llama-monitor.py` | The live monitor (the dashboard) for llama.cpp. Tabs: Overview, Connect, Requests, Log, Settings. The Settings tab has six panels: Server (the model and its settings, with the reason for each tuned value; restarts the server), Models (download, verify, delete, add from Hugging Face, edit a custom model's card), Auto fit (the pick for this Mac, the goal and scope, the budget, the reasons and the ranking; Use this), Auto-tune, Router and Caching (the disk cache: limit, on / off, Clear). It keeps the disk cache within its limit (OpenCode and Pi fill it: `client/shared/carl-cache.js`). The Connect tab runs `host/serve.sh install --local --port N` in the background (`i`, or `u` for `--config-only`), after a question, with its output in the tab. On the Server panel the model card sits under the settings, full width; a click on its title cycles collapsed / normal / full. A thin launcher over the `tools/monitor/` package. |
| `tools/monitor/` | The dashboard's code: pure formatting, state, settings and views (`fmt`, `model`, `keys`, `settings`, `cards`, `views`, `settings_view`, `state`, `card_form` and `card_view` for the card edit mode), adapters (`system` for ps/netstat/sysctl/pmset, `api`, `collector`, `logtail`, `store` over `carl.py`, `jobs` for restart, Auto-tune, downloads and the disk limit, `terminal`; `diskcache` and `slotpack` for the disk cache and its shared pieces, `cacheapi` for the dashboard's API, `clientsync` for the pushed client config), wiring (`app`, `controller`, `cli`). |
| `tools/llama-fit.py`, `tools/gguf_shape.py`, `tools/metal-limit.swift` | `serve.sh fit` (auto fit's picks and reasons, every model's largest window) and the memory check that refuses a start over the GPU limit. `gguf_shape.py` (now a thin facade over `carl_core`) reads the GGUF metadata and does the KV/state maths (the monitor uses it too). `metal-limit.swift` reads the GPU memory limit of the Mac. |
| `tools/make-share-zip.sh` | Makes a clean zip of this folder that you can share. |
| `tools/tuishot.py` | Renders terminal screenshots and GIFs (the dashboard, OpenCode) for the README. |
| `tools/llama-log.sh` | Shows the server log as plain text. |
| `tools/llama-spec-sweep.sh`, `tools/llama-spec-bench.py` | The speculative-decoding sweep and its benchmark. The edit workload re-emits the (rewritten, 1.1.0) source of the bench, so its edit numbers are not directly comparable with older measurements. |
| `tools/llama-ab.sh`, `tools/llama-ab-measure.py`, `tools/llama-kv-longctx.py` | The KV-type and `-ub` A/B test, and the ~64K needle test. Since 1.2.0, the long prompts come from the source files of this folder (Python, shell, JS in `tools/`, `host/`, `client/`), so the numbers are not directly comparable with older runs. |
| `tools/llama-wait-idle.sh` | Waits until the server is idle. |
| `tools/llama-sesstest.py` | A long-session test for llama.cpp. Its prompt corpus is the same as for `llama-kv-longctx.py`. |
| `tools/req-capture-proxy.py` | A request-capture proxy. It refuses wildcard listen addresses, and it writes its log with mode 600. |
| `tools/carl_bench.py` | Shared helpers for the small benchmark tools: the API key, chat requests, the PID of the listening server (`netstat`), the server memory. |
| `tests/` | Unit tests. `python3 -m unittest discover -s tests` (the `carl_core` domain, app and adapters) `python3 -m unittest discover -s tests/scripts` (the shell helpers, `client/configure.py`, the small tools, and `tests/js`: the prompt cache's JavaScript, with node) and `python3 -m unittest discover -s tests/monitor -t tests/monitor` (the dashboard). They need no server and no model. |

For the use of each tool, see USERGUIDE.md, "Benchmarking and testing".

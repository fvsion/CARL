# CARL Reference: Architecture and repository layout

[Index](README.md) · how the parts fit and every file in the repository.

## Architecture

```
 Mac (M3 Pro, 36 GB, in the measurements)              VM (VMware Fusion, Kali, 4 GB) -- optional
 ┌──────────────────────────────────────────┐          ┌──────────────────────────────────┐
 │ host/serve.sh  ──►  llama-server :8080    │  vmnet8  │ OpenCode ─┐                       │
 │   (default)         (llama.cpp, Metal)    │◄─────────┤           ├─ Bearer key from      │
 │                                           │  NAT     │ Pi ───────┘  ~/.config/carl/      │
 │ dashboard in the same terminal            │          │ configs from carl-client/setup    │
 │ dashboard API on :8081                    │          │ VM address 192.168.42.130         │
 │ binds 127.0.0.1 (default) or 192.168.42.1 │          │                                   │
 │ OpenCode / Pi here too (--local)          │          │                                   │
 └──────────────────────────────────────────┘          └──────────────────────────────────┘
      ~/models/gguf/                  GGUF weights (catalogue: host/catalog.json; any other .gguf here is a model too)
      ~/models/logs/                  server logs
      ~/models/templates/             patched chat templates (thinking toggle)
      ~/.config/carl/config.json      your settings (Settings tab, ./carl.sh config)
      ~/.config/carl/models.json      custom models, their cards, Auto-tune results (this Mac)
      ~/.config/carl/api-key          the shared API key (server and clients; made at the first start)
      ~/.config/carl/slots/           the disk cache (--slot-save-path)
      ~/.config/carl/router-presets.ini   the router presets (router mode only)
      ~/.config/carl/dashboard.json   the dashboard's levels (each section: collapsed / simple / full)
```

`CARL_CONF_DIR` moves the files of `~/.config/carl` (not the API key) to a different folder. `tools/carl.py`, the launchers and the dashboard use it.

| Fact | Detail |
|---|---|
| One server at a time | A model file is 4.6–23 GB. Two large models do not fit in 36 GB. |
| API | The OpenAI API (`/v1/chat/completions`, `/v1/models`) with a Bearer key |
| Code layers on the Mac | The shell launchers (`host/`) get the model and the settings from `tools/carl.py`. |
| Facades | `tools/carl.py` and `tools/gguf_shape.py` are thin facades over the package `tools/carl_core/`. |
| `carl_core` | A pure `domain/` (no I/O) and `adapters/` (files, `sysctl`, `netstat`, Hugging Face, downloads, `llama-server`) |
| Users of the facades | The dashboard, `llama-fit.py` and `carl-tune.py` |
| Client side | A self-contained folder (`client/`). `./carl.sh package` zips it with the server's address and key (the client package) for a VM or another computer. On the Mac itself, `./carl.sh install` runs its setup. |

- **CAUTION:** Run only one server at a time. If you run two servers, GPU out-of-memory errors occur. The first server then stays broken until you restart it. The launchers refuse to start a second model ([llama.cpp details](server.md#llamacpp-details), "Launch guard").
- To install the clients on the Mac, run `./carl.sh install` (`client/setup`). You can also push `i` in the dashboard's Connect tab.
- To use the clients on another computer, make the client package (`./carl.sh package`, or `z` in the Connect tab), unzip it there and run `./setup`. The page [Clients on other computers](client-sync.md) gives the details.

## Repository layout

### Project root and `host/`

| Path | What it is |
|---|---|
| `carl.sh` | The launcher in the project root. It runs `host/serve.sh` with the same arguments. `./carl.sh` opens the dashboard. `./carl.sh -h` shows the help. `./carl.sh --no-start` opens only the dashboard (no model loads). |
| `CHANGELOG.md` | What changed in each release. Its newest `## X.Y.Z` heading is the CARL version (`remote.json`, the client package). |
| `dist/` | The client packages (`./carl.sh package`; git-ignored) |
| `assets/` | The logo and the README screenshots. `tools/tuishot.py` makes the screenshots. |
| `host/serve.sh` | The entry point on the Mac. With no arguments, it opens the dashboard. It gives the `llama` command to `serve-llama.sh`. |
| `host/common.sh` | The shared shell functions of `serve.sh` and `serve-llama.sh` (see the next table) |
| `host/serve-llama.sh` | The llama.cpp launcher: `--model`, `--kv q4\|q8`, `--ctx N\|Nk`, `--slots 1-4\|auto`, `--router`, logs, keep-awake, the thinking-toggle template. It gets the model and its settings from `tools/carl.py launch-env`. |
| `host/catalog.json` | The built-in model catalogue (see below) |
| `host/models.sh` | A thin wrapper around `tools/carl.py`: `list`, `download NAME\|default\|all\|hf:OWNER/REPO/FILE.gguf`, `verify`, `delete`, `path`, `get`, `default` (Auto fit's choice), `downloaded` |
| `host/gguf-chat-template.py` | Extracts the chat template of a GGUF. It adds the rule "`reasoning_effort: none` → thinking off" at the start. It adds `preserve_thinking` to a Qwen template that does not have it (the 9B). |

`host/serve.sh` commands:

| Command | What it does |
|---|---|
| `llama` | Starts the llama.cpp server. A first argument that starts with `-` also means `llama`. |
| `dashboard`, `--no-start` | Opens the dashboard without a server start |
| `monitor` | Attaches the dashboard to a running server |
| `install [opencode\|pi]` | Runs `client/setup`: installs the clients and writes their configs (`--yes`, `--clients-only`, `--config-only`) |
| `package [--anyway] [--out DIR]` | Makes the client package for another computer (`tools/carl.py package`) |
| `fit`, `models`, `download`, `verify`, `delete`, `card` | The models and what fits this Mac |
| `tune NAME` | Auto-tune ([Auto-tune](server.md#auto-tune)) |
| `config`, `cache`, `push` | The settings file, the disk cache, the client config push |
| `help COMMAND`, `COMMAND --help`, `COMMAND -h`, `-h` | The help of each command, from `tools/carl_help.py`. `--help-adv` lists every `llama-server` flag. An unknown command gives one `error:` line (exit 2). |

`host/common.sh` functions:

| Function | What it does |
|---|---|
| `resolve_host` | The network mode: `--local` (default), `--vm`, `--host ADDR`. It refuses 0.0.0.0. |
| `ensure_api_key` | Makes the API key, or copies it one time from an old path |
| `migrate_conf_dir` | Moves `~/.config/llm-deploy` to `~/.config/carl` one time |
| `write_client_package` | Writes `client/remote.json` (with the CARL version) and `client/api-key` at each start |
| `carl_version` | The CARL version: the newest `## X.Y.Z` heading of `CHANGELOG.md`, else `git describe --tags` |
| `ensure_deps` | Finds the Homebrew tools (`llama-server`, `aria2c`, `ansifilter`, `zstd`) |
| `port_pid`, `port_host` | Port lookups with `netstat` |
| `apply_settings` | Reads the `KEY=value` lines of `tools/carl.py` with an allow-list. It never evaluates a value. |
| `require_int`, `is_port` | Input checks |
| `guard_other_models` | The launch guard |
| `run_server` | Starts the server in its own process group, with the dashboard in front. Without a terminal, it runs a plain foreground server. |

`host/catalog.json` holds, for each model:

| Field | Contents |
|---|---|
| `hf` | The repository, the pinned revision, the file, the SHA-256, the size |
| `summary`, `description`, `label` | The text in the model lists |
| Card fields | `role`, `good_for`, `why_use`, `trade_offs`, `pick_instead`, `hardware`, `uncensored`, `rank` |
| Model facts | `arch`, `quant`, `params`, `family`, `mtp`, `min_ram_gb`, `thinking`, `abliterated` |
| `tune` | `kv`, `ctx`, `slots`, `spec`, `spec_n`, the sampling values |
| `why` | The reason for each tuned value |
| `ctx_zones`, `measured` | The context zones and the reference measurements |

The top-level keys `default` and `default_small` are the offline fallback of Auto fit. CARL uses them only when it cannot read the GGUF headers.

### `client/`

| Path | What it is |
|---|---|
| `client/setup` | The one entry point on every computer (macOS and Linux, no sudo). It asks one time which clients and options (`--yes`: the defaults, which are the choices of the last setup), runs `install-clients.sh` when OpenCode or Pi is missing or older (`npm view`), then `install.sh`. It ends with what it did, the smoke test and the next step. `./carl.sh install` runs it on the server Mac. |
| `client/setup.command` | `setup` for a double click in the macOS Finder (it opens Terminal) |
| `client/install-clients.sh` | An internal part of the setup: installs OpenCode and Pi from npm into `~/.local`. If necessary, it also installs a private Node 22 (Linux or macOS). It does not use sudo. `--help` shows its help page. |
| `client/install.sh` | An internal part of the setup (the sync service runs it too): writes the OpenCode and Pi configs. `--help` shows its help page (the options, the key sources, the switches). It reads `remote.json` first. Without it, the default is `--local` on macOS and `--vm` on Linux. It stores the API key, makes backups, sets the context limits, records the choices and does a smoke test. |
| `client/configure.py` | The non-destructive merge that `install.sh` uses (USERGUIDE.md, "What the setup changes"). `--clients` writes the configs of one client only. |
| `client/carl_models.py` | The OpenCode and Pi model entries, one for each installed model. It uses only the standard library. Its input is `client/installed-models.json` (git-ignored) or the server's `/v1/models` ids. |
| `client/carl-sync.py` | The client config sync: `watch`, `once`, `apply`, `auto on\|off`, `status`, `register on\|off`. It runs `install.sh` again with the last switches. |
| `client/remote.json`, `client/api-key` | Written at each server start (git-ignored, mode 0600): the address, the port, the dashboard API, the CARL version, the key |
| `client/VERSION` | Only in a client package: its CARL version (the `/carl` panel shows it) |
| `client/opencode/opencode.json` | The OpenCode config template (the `llamacpp` provider) |
| `client/pi/models.json` | The Pi config template |
| `client/agents/coder.md`, `client/agents/delegation.md` | The **coder** subagent and the delegation rule for the main agent ([The coder subagent](models.md#the-coder-subagent)) |
| `client/agents/browser.md` | The **browser** subagent of OpenCode (the Playwright MCP tools) |
| `client/shared/carl-cache.js` | The disk cache core ([Caching](caching.md)). `configure.py` copies it next to the plugin and the extension. |
| `client/shared/carl-panel.js` | The `/carl` panel core: for each section, its lines (plain sentences), its details and its actions |
| `client/shared/carl-delegation.js` | The hand-off rules: the delegation rule for main agents only, the reminder, the new-file gate ([the hand-off to the coder](delegation.md)). `configure.py` copies it next to the plugin and the extension. |
| `client/opencode/plugins/carl-delegation/`, `client/pi/extensions/carl-delegation/` | The hand-off in OpenCode (it takes the rule out of subagents' prompts, adds the reminder, runs the gate) and in Pi (the reminder, the gate). With the coder. |
| `client/opencode/commands/code.md`, `client/pi/prompts/code.md` | `/code`: a task straight to the coder. With the coder. |
| `client/opencode/plugins/carl-cache/`, `client/pi/extensions/carl-cache/` | The disk cache in OpenCode (it wraps `fetch`; `chat.headers` marks the session and the agent) and in Pi (`before_provider_request`, `message_end`) |
| `client/opencode/plugins/carl-panel/`, `client/pi/extensions/carl-panel/` | The `/carl` panel: each CARL piece on this computer with its state, and the actions of the config sync ([Clients on other computers](client-sync.md)) |
| `client/opencode/plugins/carl-model-check/` | An OpenCode server plugin (see below) |
| `client/opencode/plugins/subagents-sidebar/` | An OpenCode TUI plugin: a live list of the subagents in the sidebar. The setup adds it to `tui.json`. |
| `client/opencode/plugins/session-switcher/` | An OpenCode TUI plugin: a session switcher in the prompt box (`‹ 2/3 ● title ›`), and `/switch` for a list of the recent sessions. The setup adds it to `tui.json`. |
| `client/pi/extensions/subagent/` | The official subagent extension of Pi (vendored, MIT). A patch adds the installed agents to the tool description. Thus, the model can delegate without help. `result.js` formats the result of a background subagent for the screen (`✓ Coder finished (42 s)`, the first 3 lines). |

**`carl-model-check`** (the `chat.params` hook):
- Before each request to CARL's provider, it reads the server's `/v1/models`. The cache time is 5 s and the timeout is 1.5 s. It ignores errors.
- It shows a TUI toast one time for each situation. It also writes a line in OpenCode's log (`service=carl-model-check`).
- The situations: the server runs a different model (single model), the model is not installed (router: HTTP 400), the model loads (router).
- OpenCode 1.18 loads a server plugin through the `exports["./server"]` of its package. Without it, the module loads but OpenCode never calls its hooks.
- The entry in `opencode.json` is `["file:DIR", {"provider": ID}]`. The hooks run in the TUI and in `opencode run` (1.18.34).
- Verified 2026-10-03: with the 9B selected while the IQ3 ran, the warning showed.

**`subagents-sidebar`:**
- Running subagents are at the top: the agent, the task, the time, the current tool, the context.
- Finished subagents are below: ✓ or ✗ and the duration. The 5 newest stay for 5 minutes, then a `+N more` line shows.
- The list resets when you go to a different parent session. Click a row to open it.

### `tools/`

| Path | What it is |
|---|---|
| `tools/carl_core/domain/` | Pure logic (no I/O): `types`, `errors`, `settings`, `gguf`, `fit`, `autofit`, `models`, `cards`, `records`, `hf`, `launch`, `router`, `tuning`, `clientlist`, `apikey`, `confdir`, `ports`, `units`, `package` (the client package: the version, the name of the zip, its files and modes, the refusals), `llamacpp` (the tested llama.cpp version and the check of the installed one) |
| `tools/carl_core/domain/units.py` | The units of every screen: GB and MB for files, GiB and MiB for memory, K = 1024 tokens, tok/s, the durations. It replaces `fit.human_gb` and the `fmt.size`, `knum` and `dur` of the dashboard. |
| `tools/carl_core/domain/llamacpp.py` | The llama.cpp version that CARL is tested with (`TESTED`, one place), the parse of `llama-server --version`, the comparison (by the build number, else by the dotted version), the start line and the rows of the HEALTH card ([The llama.cpp version](server.md#the-llamacpp-version)). |
| `tools/carl_core/domain/cards.py` | The model cards. `card_rows()` gives the rows of `./carl.sh card` (the labels of the dashboard; for a custom model, every field with the key to set it). |
| `tools/carl_core/adapters/` | The I/O: `json_files`, `filesystem`, `gguf_reader`, `system` (sysctl, the GPU memory limit, `netstat`), `huggingface`, `downloader`, `llama_server`, `api_key`, `console`, `client_package` (`git ls-files client/`, the zip with mode 0600; `./carl.sh package` and the dashboard use it) |
| `tools/carl_core/app.py`, `wiring.py` | The use cases, and the connection of the adapters |
| `tools/carl.py` | A thin facade over `tools/carl_core`: its CLI and the API of the dashboard (see below) |
| `tools/carl_help.py` | The help of `./carl.sh` and of each command (`help COMMAND`, `COMMAND --help`, `COMMAND -h`), and the text layout of the CLI: it wraps to `COLUMNS`, else to the width of the terminal, else to 80 columns (40 to 120). `tools/carl.py`, `tools/llama-fit.py` and `tools/carl-tune.py` use it. |
| `tools/carl-tune.py` | Auto-tune (`./carl.sh tune NAME\|all [--quick\|--long]`). It saves the result in `~/.config/carl/models.json`. |
| `tools/llama-monitor.py` | The dashboard. A thin launcher over the `tools/monitor/` package. |
| `tools/monitor/` | The dashboard's code (see below) |
| `tools/llama-fit.py` | `./carl.sh fit`: Auto fit's choices and why, and the largest context of each model. Also the memory check that refuses a start over the GPU memory limit (one `error:` sentence first). |
| `tools/gguf_shape.py` | A thin facade over `carl_core`: the GGUF metadata and the calculation of the context memory and the recurrent state |
| `tools/metal-limit.swift` | Reads the GPU memory limit of the Mac |
| `tools/prompt-size.py` | Counts the tokens of the system prompt and of each tool in a captured request |
| `tools/req-capture-proxy.py` | A request-capture proxy. It refuses wildcard listen addresses. It writes its log with mode 600. |
| `tools/make-share-zip.sh` | Makes a clean zip of this project that you can share: the files that git tracks (`git ls-files`), never a key or the server's files. `--with-docs` adds `docs/` (not in git) from the disk. |
| `tools/tuishot.py` | Makes terminal screenshots and GIFs (the dashboard, OpenCode) for the README |
| `tools/llama-log.sh` | Shows the server log as plain text |
| `tools/llama-wait-idle.sh` | Waits until the server is idle |
| `tools/llama-spec-sweep.sh`, `tools/llama-spec-bench.py` | The speculative-decoding sweep and its benchmark |
| `tools/llama-ab.sh`, `tools/llama-ab-measure.py`, `tools/llama-kv-longctx.py` | The A/B test of the context memory type and `-ub`, and the ~64K needle test |
| `tools/llama-sesstest.py` | A long-session test. It uses the same prompt corpus as `llama-kv-longctx.py`. |
| `tools/carl_bench.py` | Shared helpers for the small benchmark tools: the API key, chat requests, the server PID (`netstat`), the server memory |
| `tools/agent-bench/` | The agent bench: does the main agent of OpenCode and Pi give coding tasks to the coder? Fixture projects, 17 requests (+2 extra), the setup's variants, a harness HOME, the server for each model, fetch and drop of model copies, a resumable results file and its report (`bench.py`; its README). Tests: `python3 -m unittest discover -s tests/agent_bench -t tests/agent_bench`. |

- `tools/carl.py` is the one place for the models and the settings: the catalogue, the models on this Mac, `config.json` (validated) and the settings order.
- Its commands: `list`, `download`, `hf-files`, `verify`, `delete`, `path`, `get`, `default`, `downloaded`, `launch-env`, `router-preset`, `client-models`, `config`, `card`, `cache`, `push`, `package`.
- Downloads are pinned and SHA-256 verified.
- The launchers, `llama-fit.py` and the dashboard use `tools/carl.py`.

The edit workload of `llama-spec-bench.py` writes the source of the bench again. The source was rewritten in 1.1.0. Thus, its edit values are not directly comparable with older measurements. Since 1.2.0, the long prompts of the A/B and needle tests come from the source files of this folder. Thus, their values are not directly comparable with older runs.

The `tools/monitor/` package:

| Layer | Modules |
|---|---|
| Pure: formats, state, settings, views | `fmt`, `words` (the glossary's names for the values of the server and `config.json`), `model`, `keys`, `state`, `settings`, `cards`, `arrange`, `logbook`, `clients`, `views`, `views_connect` (the Connect tab), `settings_view`, `settings_panels` (one module for each Settings panel; `page`: the sections, the side column and the scroll), `card_form`, `card_view` |
| Adapters | `system` (ps, netstat, sysctl, pmset), `api`, `collector`, `logtail`, `store` (over `carl.py`), `gguf`, `fsio`, `jobs` (restart, Auto-tune, downloads, the disk limit), `terminal`, `uiprefs` (the levels in `dashboard.json`) |
| The disk cache | `diskcache` (the limit), `slotpack` (shared storage: saved sessions stored as changes to their saved prompt) |
| Other computers | `cacheapi` (the dashboard's API), `clientsync` (the pushed client config) |
| Wiring | `app`, `controller`, `cli` |

### The dashboard

| Tab | What it does |
|---|---|
| Live | The live state of the server and the Mac: SLOTS, SPEED, MEMORY, THIS MAC, CONNECT, HEALTH, MODEL, RECENT REQUESTS, LOG (no server: SERVER, THIS MAC, LOG) |
| Connect | The address, the key and the client configs to copy. Sub-tabs: **Setup** and **Clients**. `i` installs the clients on this Mac (`u`: the model lists only). `z` makes the client package, `f` shows it in the Finder. `P` sends the client config. |
| Requests | Each finished request with its speeds, and a mean row |
| Log | The server log |
| Settings | Seven panels: Server, Models, Agents, Auto fit, Auto-tune, Router, Caching |

| Settings panel | What it does |
|---|---|
| Server | The model and its settings, with the reason for each tuned value. A change restarts the server. |
| Models | Download, verify, delete, add from Hugging Face, edit the card of a custom model |
| Auto fit | The choice for this Mac, the goal and the candidates, the memory that a model can use, the reasons and the ranking. **Use this** applies the choice. |
| Auto-tune | Stops the server, measures a model, and starts the server again |
| Router | Router mode, and Load / Unload of a model |
| Caching | The disk cache: the limit, the switches, **Clear** |

- Each screen is made of sections. Each section has its own level: collapsed, simple or full (two levels, collapsed and open, where full adds nothing). Tab / Shift-Tab select a section, `L` changes its level, and `D` sets every section to simple or full. `uiprefs` saves the levels in `dashboard.json` (never in `config.json`). `--expand` shows every section at full for one run.
- Every tab scrolls as one page. The Live cards are in one column below 100 columns, two from 100, three from 190. The Settings panels and the Connect tab put their help in a side column from 151 columns.
- The dashboard keeps the disk cache in its limit. OpenCode and Pi fill the cache.
- The Connect tab runs `host/serve.sh install --local --port N` (the setup, without questions) in the background, after a question. Its output shows in the tab.
- `z` runs the code of `./carl.sh package` in a thread (`jobs.make_package`). The card **CLIENT PACKAGE** shows the zip and the key warning, or why CARL made no package.

### `reference/` and `tests/`

| Path | What it is |
|---|---|
| `reference/` | This reference, one page for each topic. `reference/README.md` is the index. |
| `tests/` | Unit tests. They need no server and no model. |

| Command | What it tests |
|---|---|
| `python3 -m unittest discover -s tests` | The `carl_core` domain, the app and the adapters (`tests/test_units.py`: the units) |
| `python3 -m unittest discover -s tests/scripts` | The shell helpers, `client/configure.py`, the client package and `client/setup` (`test_client_package.py`: the files and modes of the zip, a fresh install and an update in a temporary home with a fake server and fake npm, the refusals, the help pages), the small tools, the text of the CLI (`tests/scripts/test_cli_text.py`: each help page, the width, the glossary's names, the units, the start lines), and `tests/js` (the JavaScript of the clients, with node; `pi-subagent.test.mjs`: the result of a background subagent in Pi) |
| `python3 -m unittest discover -s tests/monitor -t tests/monitor` | The dashboard (`tests/monitor/test_screens.py`: every screen as text at 100, 140 and 200 columns, in simple and full detail) |
| `CARL_DOCKER_TESTS=1 python3 -m unittest -v tests/integration/test_sync_docker.py` | The sync service under systemd, and an install from the client package, in Docker containers (opt-in) |

For the use of each tool, see USERGUIDE.md, "Benchmarking and testing".

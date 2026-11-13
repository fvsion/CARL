# CARL Reference: Architecture and repository layout

[Index](README.md) · how the parts fit and every module of CARL.

## Architecture

```
 Mac (M3 Pro, 36 GB, in the measurements)              VM (VMware Fusion, Kali, 4 GB) -- optional
 ┌──────────────────────────────────────────┐          ┌──────────────────────────────────┐
 │ carl start     ──►  llama-server :9275    │  vmnet8  │ OpenCode ─┐                       │
 │   (default)         (llama.cpp, Metal)    │◄─────────┤           ├─ Bearer key from      │
 │                                           │  NAT     │ Pi ───────┘  ~/.config/carl/      │
 │ dashboard in the same terminal            │          │ configs from carl-client/setup    │
 │ dashboard API on :9276                    │          │ VM address 192.168.42.130         │
 │ binds 127.0.0.1 (default) or 192.168.42.1 │          │                                   │
 │ OpenCode / Pi here too (--local)          │          │                                   │
 └──────────────────────────────────────────┘          └──────────────────────────────────┘
      ~/models/gguf/MODEL/QUANT/      GGUF weights, a folder for each model and quantization (catalogue: src/carl/data/catalog.json; any other .gguf here is a model too)
      ~/models/mlx/                   MLX models (named, not run in this version)
      ~/models/logs/                  server logs
      ~/models/templates/             patched chat templates (thinking toggle) of models without a folder of their own
      ~/.config/carl/config.json      your settings (Settings tab, carl config)
      ~/.config/carl/models.json      custom models, their cards, Auto-tune results (this computer)
      ~/.config/carl/api-key          the shared API key (server and clients; made at the first start)
      ~/.config/carl/slots/           the disk cache (--slot-save-path)
      ~/.config/carl/sessions/        a folder for each client session, kept when its cache expires (a plugin's files for it)
      ~/.config/carl/router-presets.ini   the router presets (router mode only)
      ~/.config/carl/dashboard.json   the dashboard's levels (each section: collapsed / simple / full)
      ~/.config/carl/llama.cpp/       the llama.cpp builds that carl engine install made (Windows and Linux)
```

On a Windows or Linux PC the same picture holds with the GPU's own memory instead of unified memory, and CUDA instead of Metal ([Windows and Linux](windows-linux.md)).

`CARL_CONF_DIR` moves the files of `~/.config/carl`, the API key too, to a different folder. CARL's commands, the start and the dashboard use it.

| Fact | Detail |
|---|---|
| One server at a time | A model file is 4.6–23 GB. Two large models do not fit in 36 GB. |
| API | The OpenAI API (`/v1/chat/completions`, `/v1/models`) with a Bearer key |
| Ports and adapters | The code in `src/carl` has a pure `domain/` (no I/O), `ports/` (the interfaces to the computer), `adapters/` (one folder per OS and the shared ones: files, `sysctl`, the TCP table, Hugging Face, downloads, `llama-server`), `application/` (the use cases) and `composition/` (the one place that picks the adapters for a computer). |
| One entry | `carl.py` in the clone and the installed command `carl` run one bootstrap, `src/carl/_boot.py` |
| Users of the typed API | The commands and the dashboard use `src/carl/application/api.py` or the `carl` command, never the adapters |
| Client side | A self-contained folder (`client/`). `carl package` zips it with the server's address and key (the client package) for a VM or another computer. On the Mac itself, `carl install` runs its setup. |

- **CAUTION:** Run only one server at a time. If you run two servers, GPU out-of-memory errors occur. The first server then stays broken until you restart it. CARL refuses to start a second model ([llama.cpp details](server.md#llamacpp-details), "Launch guard").
- To install the clients on the Mac, run `carl install` (`client/setup`). You can also push `i` in the dashboard's Connect tab.
- To use the clients on another computer, make the client package (`carl package`, or `z` in the Connect tab), unzip it there and run `./setup`. The page [Clients on other computers](client-sync.md) gives the details.

## Repository layout

### Project root

| Path | What it is |
|---|---|
| `carl.py` | The entry in the clone: `python3 carl.py` (`py carl.py` on Windows) does what the command `carl` does |
| `pyproject.toml` | Makes the command `carl` (`uv tool install --editable <the CARL folder>`). CARL has no runtime dependencies. |
| `src/carl/` | The product (below) |
| `client/` | The client package: OpenCode and Pi setup, the plugins and the sync service |
| `reference/` | This reference, one page for each topic |
| `CHANGELOG.md` | What changed in each release. Its newest `## X.Y.Z` heading is the CARL version (`remote.json`, the client package). |
| `dist/` | The client packages (`carl package`; git-ignored) |
| `assets/` | The logo and the README screenshots |
| `.gitattributes` | The line ends in a checkout on Windows: LF for the Python and shell scripts and `client/setup`, CRLF for `.cmd` files |
| `src/carl/data/catalog.json` | The built-in model catalogue (see below) |

`carl` commands (the dispatch is `src/carl/cli/main.py`; before 1.21.0 a bash launcher ran them):

| Command | What it does |
|---|---|
| `start` | Starts the llama.cpp server. A first argument that starts with `-` also means `start`. |
| `dashboard`, `--no-start` | Opens the dashboard without a server start |
| `monitor` | Attaches the dashboard to a running server |
| `engine version`, `engine help`, `engine install` | The engine (llama.cpp): its version, every `llama-server` flag, and (Windows and Linux) the install of the tested build |
| `gpu` | The GPUs, what a model can use on each, the llama.cpp build for this computer (Windows and Linux; a Mac shows its one GPU) |
| `log [-f]` | Shows the server log as plain text |
| `install [opencode\|pi]` | Runs `client/setup`: installs the clients and writes their configs (`--yes`, `--clients-only`, `--config-only`) |
| `package [--anyway] [--out DIR]` | Makes the client package for another computer |
| `fit`, `models`, `download`, `verify`, `delete`, `card` | The models and what fits this computer |
| `tune NAME` | Auto-tune ([Auto-tune](server.md#auto-tune)) |
| `config`, `cache`, `push` | The settings file, the disk cache, the client config push |
| `help COMMAND`, `COMMAND --help`, `COMMAND -h`, `-h` | The help of each command, from `src/carl/cli/help_topics.py`. `engine help` lists every `llama-server` flag. An unknown command gives one `error:` line (exit 2). The command words that 1.21.0 removed (the [CHANGELOG](../CHANGELOG.md) lists them), and `grant` and `pocket` before them, each give one `error:` line that names the new command (exit 2). |

What the bash helpers did before 1.21.0, and where it is now:

| The helper | Now |
|---|---|
| `resolve_host` (the network mode) | `src/carl/domain/server/network.py` |
| `ensure_api_key`, `migrate_conf_dir` | `src/carl/adapters/common/key_store.py`, `src/carl/adapters/macos/llm_deploy_migration.py` |
| `write_client_package` (`client/remote.json`, `client/api-key`) | `src/carl/adapters/common/runtime_files.py` |
| `mkdir -p` and `chmod 700` of CARL's private folders | `src/carl/adapters/common/private_dirs.py` (no mode in the call, chmod after; on Windows a folder an elevated run made is repaired or reported, and an elevated start gives the user's SID each folder) |
| `carl_version` | `ChangelogVersion` (`src/carl/adapters/common/version.py`) |
| `ensure_deps` | `src/carl/adapters/macos/homebrew_deps.py` (a Mac), `src/carl/adapters/llama/release_offer.py` (Windows and Linux) |
| `port_pid`, `port_host` | `src/carl/adapters/macos/bsd_netstat.py` (Linux: `src/carl/adapters/linux/ss_proc.py`; Windows: `src/carl/adapters/windows/tcp_table.py`) |
| `apply_settings`, `require_int`, `is_port` | `src/carl/domain/server/launch_plan.py` |
| `guard_other_models` | `src/carl/domain/hardware/processes.py` |
| the thinking-toggle template (`gguf-chat-template.py`) | `src/carl/domain/server/thinking_template.py`, `carl chat-template` |
| `llama-log.sh` | `carl log` (`src/carl/cli/commands/log.py`) |
| `llama-log-filter.py` | `src/carl/adapters/common/log_filter.py`, `carl log-filter` |

`src/carl/data/catalog.json` holds, for each model:

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
| `client/setup` | The one entry point on every computer (macOS and Linux, no sudo). It starts `client/carl_setup.py`. The program asks one time which clients and options (`--yes`: the defaults, which are the choices of the last setup), installs OpenCode and Pi when one is missing or older (`npm view`), writes their configs, and ends with what it did, the smoke test and the next step. `carl install` runs it on the server Mac. |
| `client/setup.command` | `setup` for a double click in the macOS Finder (it opens Terminal) |
| `client/setup.cmd` | `setup` for Windows (a double click, or `setup.cmd` in a terminal) |
| `client/carl_setup.py`, `client/_carl_boot.py` | The setup as one Python program (`carl_client.app.setup`) and the bootstrap of the client's Python entries (the Python floor, the path of the package) |
| `client/carl_client/` | The client package, standard library only, shipped alone in the zip (it never imports the server's code, rule R8): `domain/` (the merge rules of OpenCode and Pi, the switches, the model entries, thinking for each role, what a CARL piece was called before), `ports.py`, `adapters/` (this computer's files, the package data, the dashboard API over HTTP, the lock, node and npm, the service managers: launchd, systemd, Task Scheduler, the user's environment: the shell profile or HKCU), `app/` (the setup, the config step `install.py`, `configure.py`, the sync, the model list), `data/` (the thinking values, the words, the earlier names) |
| `client/carl_client/domain/` | `merge.py` (the rules both clients share), `merge_opencode.py`, `merge_pi.py`, `switches.py` (the one vocabulary of the setup's switches), `remote.py` (the server as a client folder knows it), `legacy.py` (what earlier versions left), `models.py`, `thinking.py`, `words.py` |
| `client/carl_client/adapters/` | `files_local.py`, `package_data.py`, `sync_files.py`, `dashboard_http.py`, `interpreter.py`, `local_addrs.py`, `npm.py`, `node_dist.py`, `install_sh.py` (the config step run again by the sync), `lock_fcntl.py` and `lock_msvcrt.py` (one process at a time), `launchd.py`, `systemd.py` and `task_scheduler.py` (the sync service), `userenv_profile.py` and `userenv_hkcu.py` (OpenCode's tool switches in the shell profile or in HKCU) |
| `client/carl_client/app/` | `setup.py`, `install.py`, `configure.py`, `sync.py` and `model_list.py` |
| `client/carl-sync.py` | The client config sync: `watch`, `once`, `apply`, `auto on\|off`, `status`, `register on\|off`. It runs the config step again with the last switches. |
| `client/remote.json`, `client/api-key` | Written at each server start (git-ignored, mode 0600): the address, the port, the dashboard API, CARL's ID, the CARL version, the key |
| `client/VERSION` | Only in a client package: its CARL version (the `/carl` panel shows it) |
| `client/opencode/opencode.json` | The OpenCode config template (the `llamacpp` provider) |
| `client/pi/models.json` | The Pi config template |
| `client/agents/coder.md`, `client/agents/delegation.md` | The **coder** subagent and the delegation rule for the main agent ([The coder subagent](models.md#the-coder-subagent)) |
| `client/agents/browser.md` | The **browser** subagent of OpenCode (the Playwright MCP tools) |
| `client/shared/carl-cache.js` | The disk cache core ([Caching](caching.md)). The config step copies it next to the plugin and the extension. |
| `client/shared/carl-panel.js` | The `/carl` panel core: for each section, its lines (plain sentences), its details and its actions |
| `client/shared/carl-api.js` | The check before a client sends its key to the dashboard API: the API must answer `GET /carl/ping` with CARL's ID ([Clients on other computers](client-sync.md#the-dashboards-api)). The config step copies it next to `carl-cache` and `carl-delegation`. |
| `client/shared/carl-model-check.js` | The model check core: OpenCode's `carl-model-check` plugin and Pi's `carl-model-check` extension use it |
| `client/shared/carl-delegation.js` | The hand-off rules: the delegation rule for main agents only, the reminder, the new-file gate ([the hand-off to the coder](delegation.md)). The config step copies it next to the plugin and the extension. |
| `client/opencode/plugins/carl-delegation/`, `client/pi/extensions/carl-delegation/` | The hand-off in OpenCode (it takes the rule out of subagents' prompts, adds the reminder, runs the gate) and in Pi (the reminder, the gate). With the coder. |
| `client/opencode/commands/code.md`, `client/pi/prompts/code.md` | `/code`: a task straight to the coder. With the coder. |
| `client/opencode/plugins/carl-cache/`, `client/pi/extensions/carl-cache/` | The disk cache in OpenCode (it wraps `fetch`; `chat.headers` marks the session and the agent) and in Pi (`before_provider_request`, `message_end`) |
| `client/opencode/plugins/carl-panel/`, `client/pi/extensions/carl-panel/` | The `/carl` panel: each CARL piece on this computer with its state, and the actions of the config sync ([Clients on other computers](client-sync.md)) |
| `client/opencode/plugins/carl-model-check/` | An OpenCode server plugin (see below) |
| `client/opencode/plugins/subagents-sidebar/` | An OpenCode TUI plugin: a live list of the subagents in the sidebar. The setup adds it to `tui.json`. |
| `client/opencode/plugins/session-switcher/` | An OpenCode TUI plugin: a session switcher in the prompt box (`‹ 2/3 ● title ›`), and `/switch` for a list of the recent sessions. The setup adds it to `tui.json`. |
| `client/pi/extensions/subagent/` | The official subagent extension of Pi (vendored, MIT). A patch adds the installed agents to the tool description. Thus, the model can delegate without help. `result.js` formats the result of a background subagent for the screen (`✓ Coder finished (42 s)`, the first 3 lines). |

**`carl-model-check`** (the `chat.params` hook; Pi: its own `carl-model-check` extension, `before_provider_request`, one notice for each situation):
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

### `src/carl/`

The product. Every module belongs to one layer, and each layer imports only the layers below it. The domain does no I/O. Only `composition/` picks adapters.

| Folder | What it holds |
|---|---|
| `domain/` | The rules (no I/O): the settings, the models, memory and what fits, the launch plan, Auto-tune's scoring, the GPUs |
| `ports/` | The interfaces to the computer, one module for each context, and a fake for each in the tests |
| `adapters/` | The ports' implementations: `common/` (every OS), `macos/`, `posix/`, `linux/`, `windows/`, `gpu/`, `llama/`, `http/` |
| `application/` | The use cases over the domain and the ports: the start, the stop, fit, the models, the settings, the jobs |
| `composition/` | The one place that picks the adapters for this computer (the platform profiles) and builds each entry |
| `cli/` | The commands of `carl`, their help and their texts |
| `dashboard/` | The terminal dashboard: tabs, cards, the Settings panels, keys and clicks |
| `ui/` | Text helpers shared by the command line and the dashboard |
| `plugins/` | The plugin registry ([The plugins](plugins.md#carls-own-plugins-the-plugin-seam)) |
| `data/` | Package data: the catalogue (`catalog.json`), the pinned llama.cpp release (`llamacpp-release.json`), Auto-tune's edit workload (`tune-edit-workload.txt`) |

**The entry.**

| Module | What it does |
|---|---|
| `src/carl/__init__.py` | The package |
| `src/carl/_boot.py` | The bootstrap that `carl.py` and the command `carl` share: it checks the Python floor, sets UTF-8 output and calls the dispatch |

**`domain/`: the rules.** The packages are `src/carl/domain/shared/`, `src/carl/domain/config/`, `src/carl/domain/catalog/`, `src/carl/domain/clients/`, `src/carl/domain/memory/`, `src/carl/domain/hardware/`, `src/carl/domain/llama/`, `src/carl/domain/server/` and `src/carl/domain/tune/`.

| Module | What it does |
|---|---|
| `types.py`, `errors.py`, `paths.py`, `units.py`, `constants.py`, `contracts.py`, `platform_words.py`, `ansi.py` (`src/carl/domain/shared/`) | The typed shapes of the data; the one error type that users see; paths as text on every OS; the units of every screen; the ANSI stripper and colours; every default value once; the checks at the boundaries of the ports (design by contract); the words for the computer CARL runs on (the command word, "this Mac" or "this computer") |
| `settings.py`, `key_paths.py`, `paths.py`, `server_rows.py` (`src/carl/domain/config/`) | The settings schema (the one source of every key, range and default), where the API key lives, where the settings folder is, the Settings tab's Server rows built from the schema |
| `inventory.py`, `layout.py`, `quants.py`, `cards.py`, `records.py`, `hf.py`, `hf_token.py`, `drafters.py`, `drafts.py`, `thinking.py` (`src/carl/domain/catalog/`) | The models CARL knows and the settings each starts with; where a model's files are in the models folder (a folder for each model and quantization, or an older place); the entries of one model and its default quantization; the model cards; the checks of the catalogue and `models.json`; Hugging Face references, and the access token's form, the hosts that get it and the text of a gated repo; the drafters of every kind (MTP, DFlash, DSpark, EAGLE-3) and the vision projector of a catalogue model (`drafts.py`); the MTP drafter of a custom Gemma 4 model; thinking for each role |
| `model_list.py`, `package.py` (`src/carl/domain/clients/`) | The installed models as the client configs list them; the client package (its version, name, files and modes, and its refusals) |
| `gguf.py`, `shape.py`, `fit.py`, `autofit.py`, `budget.py`, `loaded.py`, `split.py`, `offload.py`, `calibration.py`, `dry_run.py`, `server_fit.py`, `cpu_kv.py` (`src/carl/domain/memory/`) | The GGUF header and tensor table; the context and weight maths of a model; what fits and the largest window; Auto fit; the memory a model may use on discrete GPUs; what a start loads of the file; the split over several GPUs; MoE experts in the RAM; the estimate's constants for each backend and B, its upper bound; llama.cpp's no-alloc dry run of a start, D, the check of the fit (its output parsed, the flags it takes); llama-server's own fit of a start, E, the source of its need (its breakdown, the flags of a fit run, the drafter's bound and record, the stub's size, the start's lines); the context memory type of a start on the CPU alone, chosen for speed (A71) |
| `devices.py`, `gpu.py`, `cpu_topology.py`, `processes.py` (`src/carl/domain/hardware/`) | A discrete GPU and the VRAM kept free; the GPU inventory's merge and choice; the CPU's logical processors and physical cores, and the threads pinned one per core for a start that keeps MoE experts in the RAM (`pin_for`: the thread count and the `--cpu-mask`; decisions A54 and A57, [Threads with experts in the RAM](server.md)); the rule that finds a process that probably holds a model |
| `version.py`, `build.py`, `release.py`, `spec_support.py` (`src/carl/domain/llama/`) | The llama.cpp version CARL is tested with and the check of the installed one; the build for a GPU; the pinned release and its archive names; the speculative kinds a build supports (read from its source) |
| `launch_plan.py`, `argv.py`, `network.py`, `cmdline.py`, `ub_order.py`, `engines.py`, `start_text.py`, `dispatch_text.py` (`src/carl/domain/server/`) | The launch plan and the order of the sources; `llama-server`'s arguments; where a start listens; a running server's command line read back into settings; the one order of the `-ub`; the engines that can serve a model; the start lines; the dispatch's own texts |
| `router_presets.py`, `runtime_doc.py`, `slot_files.py`, `session_folders.py`, `serverlog.py`, `logbook.py`, `known_warnings.py`, `snapshot.py`, `thinking_template.py` (`src/carl/domain/server/`) | Router mode's presets; the server file `server-PORT.json`; the disk cache's saved states as data; the sessions' own folders (their names, the ones kept); which lines of the server log CARL keeps; what the log says, request by request; the warnings llama.cpp writes in normal work, with their reasons; the data the dashboard collects; the chat template with the thinking toggle |
| `scoring.py`, `progress.py`, `provenance.py` (`src/carl/domain/tune/`) | Auto-tune's measures and choice; the lines it prints as events; where a record was measured and whether a start is on that build and backend (A72) |

**`ports/`: the interfaces.**

| Module | What it does |
|---|---|
| `src/carl/ports/__init__.py` | Every Protocol, imported again for the callers that name one |
| `src/carl/ports/clock.py`, `desktop.py`, `http.py`, `hub.py`, `llama.py`, `network.py`, `process.py`, `secrets.py`, `storage.py`, `system_stats.py`, `ui.py` | The date; the clipboard and the file manager; the `llama-server` HTTP API; Hugging Face and the downloads; the user's access token in the system's secret store; llama.cpp's programs, its install and the first-run check; the listening ports and the computer's addresses; processes, the server's runner and keep-awake; CARL's own files, the key, the templates and the package; the dashboard's view of the computer; text for the person at the terminal |
| `src/carl/ports/cache.py`, `engine.py`, `hardware.py`, `models.py`, `plugin.py`, `terminal.py`, `tune.py` | The disk cache and the client config; an engine that serves a model; the GPUs, the RAM and the CPU's layout; the models folder and the header shapes; a plugin and the plugin host; the terminal the dashboard draws on; the server Auto-tune measures |

**`adapters/`: the implementations.**

| Folder | Modules |
|---|---|
| `common/` (every OS) | `json_files.py`, `key_store.py`, `private_dirs.py`, `private_files.py`, `runtime_files.py`, `version.py`, `console.py` (files, the key, CARL's private folders, the copies of the key left to their owner on Windows, the server file, the version, terminal output); `model_folder.py`, `gguf_reader.py`, `hf_http.py`, `hf_auth.py`, `downloader.py` (the models folder, GGUF headers, Hugging Face, the token on its calls, resumable downloads); `token_file.py`, `secret_chain.py` (the access token in a private file, and the stores one after the other); `template_files.py`, `zip_writer.py`, `git_files.py`, `socket_addresses.py` (the chat templates, the client package's zip and file list, the computer's addresses); `llama_http.py`, `log_tail.py`, `log_filter.py`, `installed_clients.py`, `ui_prefs.py`, `session_names.py` (HTTP to llama-server, the log, CARL's log filter, small file reads, the dashboard's saved levels, the names of the clients' sessions); `slot_folder.py`, `slot_claims.py`, `cache_events.py`, `zstd_patch.py` (the disk cache's folder, claims, counters, and sessions stored as patches); `session_folders.py` (a folder for each client session, kept when its cache expires); `client_config_store.py`, `client_registry.py` (the client config that other computers sync, and the computers that sync); `program.py` (a program run to its end: its exit code and output) |
| `client_bundle.py` | The one bridge from CARL to its client package (`client/carl_client`) |
| `macos/` | `sysctl_host.py`, `bsd_netstat.py`, `ps.py`, `ifaces.py`, `vm_stat.py`, `ioreg.py`, `pmset.py`, `libproc.py`, `system_stats.py` (the host and the dashboard's reads); `caffeinate.py`, `desktop.py`, `homebrew_deps.py`, `llm_deploy_migration.py`, `mtplx_migration.py` (keep-awake, the clipboard and Finder, Homebrew's tools, the migrations of earlier versions' settings and key); `keychain.py` (the access token in the Keychain, through `security`) |
| `posix/` | `process.py`, `server_runner.py`, `start_tools.py`, `terminal.py` (processes and the server's runner on macOS and Linux, the terminal's lines and prompts, cbreak mode and the mouse) |
| `linux/` | `host.py`, `cpu_layout.py`, `ss_proc.py`, `system_stats.py`, `inhibit.py`, `desktop.py`, `secret_tool.py` (the RAM and the processor, the CPU's cores from `/sys/devices/system/cpu`, ports and processes from `ss` and `/proc`, the dashboard's reads, `systemd-inhibit`, the clipboard and the file manager, the access token in the Secret Service through `secret-tool`) |
| `windows/` | `sparse_file.py` (a stub extended as a sparse file), `host.py`, `process.py`, `processes.py`, `tcp_table.py`, `server_runner.py`, `stop_helper.py`, `keep_awake.py`, `secrets.py`, `credentials.py`, `console.py`, `desktop.py`, `system_stats.py`, `gpu_counters.py`, `cpu_layout.py` (the Win32 host, processes, the TCP table, the runner and the console stop, keep-awake, the key's access list, the access token in the Credential Manager, the VT console, the clipboard and Explorer, the dashboard's reads, the GPU counters, the CPU's cores) |
| `gpu/` | `inventory.py`, `tool.py`, `parsers.py`, `nvidia_smi.py`, `amdgpu_sysfs.py`, `windows_counters.py`, `llama_devices.py`, `host_info.py` (the sources of a PC's GPUs and the merge, running a GPU tool, reading its output, the PC's record for Auto-tune) |
| `llama/` | `homebrew.py`, `locator.py`, `build_class.py` (a build of your own classed from what it offers, A72), `release_installer.py`, `release_offer.py`, `version_probe.py`, `engine.py`, `tune_control.py`, `dry_run.py`, `server_fit.py`, `gguf_stub.py` (where llama.cpp is on a Mac and on a PC, the pinned build's install and the first-run offer, the version, the llama.cpp engine, the server Auto-tune measures, the dry run of a start, llama-server's own fit of a start and its answers, the sparse stub of a header) |
| `http/` | `dashboard_api_server.py` (the dashboard API's HTTP server) |

**`application/`: the use cases.** The jobs are the package `src/carl/application/server_jobs/`.

| Module | What it does |
|---|---|
| `start_server.py`, `stop_server.py`, `first_run.py`, `templates.py`, `version_check.py`, `llama_install.py` | The start of `llama-server`; the stop (the sessions are saved first); what `carl` does with no command; the thinking templates of a start; the installed llama.cpp against the tested one; the install of the pinned build |
| `app.py`, `api.py`, `config.py`, `inventory.py`, `cards.py`, `fit.py`, `router.py`, `client_list.py`, `downloads.py`, `hub_token.py`, `model_list.py` | The facade over the services and the typed API; the settings; the models CARL knows; the cards; what fits (the one builder of the budget and the start plan); router mode's presets; the installed models for the client configs; downloads and checks; the Hugging Face access token (which one counts, set and clear); the Settings tab's model store |
| `tune.py`, `settings_service.py`, `server_file.py`, `disk_cache.py` | Auto-tune's run; the Settings tab's model; the running server's file; the disk cache's use cases |
| `package.py`, `client_status.py`, `client_sync.py`, `cache_api.py`, `dashboard_api_host.py` | The client package; what the dashboard tells about the clients here; the client config for the sync; the dashboard API's routes; where the dashboard API runs |
| `collector.py`, `logbook.py` (the log follower), `server_jobs/__init__.py`, `server_jobs/host.py`, `server_jobs/notifier.py`, `server_jobs/records.py` | A snapshot of the server and the computer; what the log says; the dashboard's background jobs (restart, Auto-tune, downloads, the installer, the package), their side of the computer, their reports to the dashboard, their records |

**`cli/` and `composition/`.**

| Module | What it does |
|---|---|
| `src/carl/cli/main.py`, `help_topics.py`, `settings_help.py` | The dispatch; every help topic; what each setting does |
| `src/carl/cli/commands/` | One module for each command: `start` (in `application/start_server.py`), `engine.py`, `llama_install.py` (`engine install`), `gpu.py`, `log.py`, `fit.py`, `models.py`, `download.py`, `hf.py` (`hf token`), `verify.py`, `delete.py`, `card.py`, `tune.py`, `config.py`, `cache.py`, `package.py`, `push.py`; `__init__.py` is the table; `base.py` what every command runs with; the hidden ones: `launch_env.py`, `client_models.py`, `router_preset.py`, `server_file.py`, `chat_template.py`, `carl_id.py`, `paths.py` |
| `src/carl/cli/text/` | The texts of `carl models`, `config show`, `card` and `cache` as lines that fit a width |
| `src/carl/composition/wiring.py`, `profiles.py`, `entries.py`, `dispatch.py`, `cli.py`, `start.py`, `dashboard.py`, `gpu.py`, `llama.py`, `engines.py`, `package.py`, `cache_api.py`, `hub.py`, `computer.py` | Where CARL's files are and the one platform profile of this computer (macos, windows, linux, shared_memory_pc, cpu_only); one builder for each entry; and the wiring of each part (`hub.py`: the access token's store for each OS and the calls that carry it; `computer.py`: what CARL tells a plugin of this computer, the GPU, the llama.cpp programs and the models folder) |

**`dashboard/`: the terminal dashboard.** The packages are `src/carl/dashboard/actions/`, `src/carl/dashboard/actions/settings/`, `src/carl/dashboard/forms/`, `src/carl/dashboard/render/`, `src/carl/dashboard/render/cards/`, `src/carl/dashboard/render/tabs/` and `src/carl/dashboard/render/settings/`.

| Module | What it does |
|---|---|
| `__main__.py`, `app.py`, `loop.py`, `controller.py`, `state.py`, `keys.py`, `options.py`, `act.py`, `sections.py`, `shown.py`, `arrange.py`, `clients.py`, `gguf.py`, `settings_texts.py` | The dashboard's own script (python3 runs it in place of the command); the application and its main loop; keys and clicks into actions; the UI state; terminal input; the arguments; the actions of buttons; the sections that have a level; what a screen tells the dashboard; sorting and filtering of model lists; the client configs for the running server; the GPU limit; the Settings tab's texts that name the computer (the help of each setting, a row value, the Memory sentences) |
| `actions/connect.py`, `card.py`, `notifier.py`, `actions/settings/` (`__init__.py`, `context.py`, `dialogs.py`, `server.py`, `models.py`, `agents.py`, `autofit.py`, `tune.py`, `router.py`, `caching.py`, `plugin.py`), `card_form.py` (`src/carl/dashboard/forms/`) | What a key or a click does on the Connect tab, the card edit mode, the jobs' notifier and each Settings panel (one module each); the card edit form |
| `render/cards/` (`__init__.py`, `view.py`, `slots.py`, `speed.py`, `memory.py`, `thismac.py`, `connect.py`, `health.py`, `model.py`, `requests.py`, `column.py`, `plugin.py`), `card_view.py` | The Live tab's cards, one module each, the View they draw from and the column that stacks them |
| `render/tabs/` (`__init__.py`, `live.py`, `connect.py`), `render/__init__.py` | The Live, Requests, Log and Connect tabs, the `?` card and the dialogs |
| `render/settings/` (`__init__.py`, `page.py`, `common.py`, `pickers.py`, `model_card.py`, `model_lines.py`, `server.py`, `models.py`, `agents.py`, `autofit.py`, `tune.py`, `router.py`, `caching.py`) | The Settings tab: one panel for each, the page they share, the drop-downs and the model lists |

**`ui/` and `plugins/`.**

| Module | What it does |
|---|---|
| `src/carl/ui/__init__.py`, `fmt.py`, `words.py`, `layout.py`, `screen.py` | Terminal text: bars and bordered cards (the colours and the ANSI stripper are in the domain); the glossary's names for values; the width of the command line and its paragraphs; the logo and the placing of a frame's lines |
| `src/carl/plugins/__init__.py` | The plugin registry: it finds the plugins installed in the settings folder's `plugins` folder ([Plugins](plugins.md)) |

Before 1.21.0 the tools folder also held the product's modules under their old names (`carl_core`, the dashboard's `monitor` modules such as `sessionnames.py`, the command scripts such as `llama-log-filter.py`, and aliases such as `runtime.py`, now `runtime_doc.py`); they were forwarders and are removed, and the folder is gone (the [CHANGELOG](../CHANGELOG.md) lists each move).

**The old names.** The modules had other names before 1.21.0. A module of today that has one is listed here; the [CHANGELOG](../CHANGELOG.md) has the full table of the moves.

| Old name | Now |
|---|---|
| `carl.py` of the tools folder, `carl_help.py`, `llama-fit.py`, `carl-tune.py`, `llama-monitor.py` | `src/carl/application/api.py`, `src/carl/cli/help_topics.py`, `src/carl/cli/commands/fit.py`, `src/carl/cli/commands/tune.py`, `src/carl/dashboard/` |
| `carl_core`: `app.py`, `wiring.py`, and in `domain/`: `types.py`, `settings.py`, `apikey.py`, `confdir.py`, `launch.py`, `clientlist.py`, `tuning.py` | The application, the composition and the domain packages above (`apikey.py` and `confdir.py` are `key_paths.py` and `paths.py`; `launch.py` is `launch_plan.py`; `clientlist.py` is `model_list.py`; `tuning.py` is `scoring.py`) |
| `carl_core`: in `adapters/`: `api_key.py`, `client_package.py`, `filesystem.py`, `gpu_probe.py`, `huggingface.py`, `llama_server.py`, `system.py` | `key_store.py`, `package.py` of the application, `model_folder.py`, `inventory.py` of the GPU adapters, `hf_http.py`, `tune_control.py`, `sysctl_host.py` |
| The dashboard's `monitor`: `cacheapi.py`, `clientsync.py`, `diskcache.py`, `fsio.py`, `slotpack.py` | `dashboard_api_server.py`, `client_config_store.py`, `slot_folder.py`, `installed_clients.py`, `zstd_patch.py` |
| client/carl_models.py, client/configure.py, client/install.sh, client/install-clients.sh | `client/carl_client/domain/models.py`, `client/carl_client/app/configure.py`, `client/carl_client/app/install.py` and `client/carl_client/app/setup.py` |

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
| Agents | The thinking of the main session and of the coder, for each model. A change is saved at once, with no restart. |
| Auto fit | The choice for this Mac, the goal and the candidates, the memory that a model can use, the reasons and the ranking. **Use this** applies the choice. |
| Auto-tune | Stops the server, measures a model, and starts the server again |
| Router | Router mode, and Load / Unload of a model |
| Caching | The disk cache: the limit, the switches, **Clear** |

- Each screen is made of sections. Each section has its own level: collapsed, simple or full (two levels, collapsed and open, where full adds nothing). Tab / Shift-Tab select a section, `L` changes its level, and `D` sets every section to simple or full. `uiprefs` saves the levels in `dashboard.json` (never in `config.json`). `--expand` shows every section at full for one run.
- Every tab scrolls as one page. The Live cards are in one column below 100 columns, two from 100, three from 190. The Settings panels and the Connect tab put their help in a side column from 151 columns.
- The dashboard keeps the disk cache in its limit. OpenCode and Pi fill the cache.
- The Connect tab runs `carl install --local --port N` (the setup, without questions) in the background, after a question. Its output shows in the tab.
- `z` runs the code of `carl package` in a thread (`jobs.make_package`). The card **CLIENT PACKAGE** shows the zip and the key warning, or why CARL made no package.

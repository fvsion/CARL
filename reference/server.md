# CARL Reference: The llama.cpp server

[Index](README.md) · the server, its settings, Auto-tune and its flags.

## The server: llama.cpp

| | llama.cpp (`./carl.sh llama`) |
|---|---|
| Program | `llama-server` from Homebrew `llama.cpp`. Measured with 0.4.1 (M3 Pro) and 0.5.0, build 11146 (M2 Max). |
| Port | 8080 |
| API | The OpenAI API (`/v1/chat/completions`, `/v1/models`) and the Bearer key `~/.config/carl/api-key` |
| Weights | GGUF files in `~/models/gguf/`: the catalogue models and any other GGUF |
| Catalogue | Qwen3.6-35B-A3B (Q4 and IQ3), Qwen3.8-27B stock (Q4, Q3, IQ3), Qwen3.8-27B abliterated (orcarouter: Q4, Q3, IQ3), Heretic 35B-A3B abliterated (Q4, IQ3), Qwen3.8-9B Distill, Gemma 4 E4B, 12B, 26B-A4B and 31B ([Models](models.md)) |
| Default model | Auto fit's choice for this Mac (`llama.model = auto`) |
| Context memory (KV cache) | Real q4 (q4_0, the default) or q8 (q8_0), allocated one time with no bf16 copy. `--kv` sets it. |
| Usable context | 96K for each slot by default, from 4K to 256K (`--ctx`) |
| Memory | The server allocates the full context memory at start. The memory stays flat during a session (measured from 56K to 81K tokens). |
| Prompt reuse | Context checkpoints (8, at least 4K tokens apart) at user-message boundaries. A RAM cache of 1–8 GiB ([RAM cache](caching.md#the-ram-cache-and-checkpoints-measured-2026-10-02)). A disk cache for OpenCode and Pi ([Caching](caching.md)). |
| Speculation | MTP + n-gram (`draft-mtp,ngram-mod`): the MTP head in the model file (Qwen) or a separate MTP drafter (Gemma 4). The number of guesses is set for each model. |
| Write speed (27B, M3 Pro, llama.cpp 0.4.1) | 10.5–11 tok/s on new text, 27 tok/s on an edit (the model writes text again) |
| Read speed (27B, cold, M3 Pro, llama.cpp 0.4.1) | ~85–90 tok/s at 2–9K, ~56–65 tok/s at 66K |
| Thinking off from OpenCode | Yes: the `none` variant (patched template) |
| Thinking off from Pi | Yes: `off` → `chat_template_kwargs.enable_thinking=false` |
| Effort values | They depend on the template of the model ([Thinking by model](thinking.md#thinking-by-model)). |
| Bad effort value | The Qwen3.8 27B template raises an error, and the request fails. |
| Concurrency | `--slots auto` (default): 2 slots when two full contexts fit, else 1. `--slots` accepts 1–4. More requests wait in a queue. |
| Stop | In the dashboard: `q`, then `s`. Or stop the PID that `netstat -anv -p tcp` shows for :8080 ([Port and PID lookups](#port-and-pid-lookups-no-lsof)). |
| Logs | `~/models/logs/llama-server-*.log` (`tools/llama-log.sh`) |
| Health | `/health`, `/props`, `/metrics`, `/slots` |

**Other servers that were examined:**
- MTPLX was examined in 2026-09 and 2026-10, and removed in 1.2.0. On 32–36 GB Macs, it could not hold long sessions. It gave HTTP 507 from about 4K–56K tokens (this depends on the free memory). Version 2.11 had no real quantized KV cache and a 48K limit. llama.cpp holds 2 × 96K slots. The last commit with MTPLX support is 470c316.
- NOTE: oMLX (a different MLX server) was examined on paper only. It was not tested on this Mac.

---

## llama.cpp details

### The start

- `host/serve.sh` gives the `llama` command to `host/serve-llama.sh`.
- `./carl.sh` with no arguments opens the dashboard. If a server runs on port 8080, the dashboard attaches to it.
- If no server runs, `./carl.sh` starts llama.cpp with the saved settings.
- If no model is downloaded, `./carl.sh` first offers to download Auto fit's choice for this Mac (`llama-fit.py --pick-default`: the whole catalogue, the everyday goal).
- If you answer no, or no model fits, the dashboard opens without a server.
- `./carl.sh -h` shows the help. `./carl.sh help COMMAND`, `./carl.sh COMMAND --help` and `./carl.sh COMMAND -h` show the help of one command (`tools/carl_help.py`; it wraps to the width of the terminal). [Server flags](#server-flags) lists the flags of `serve-llama.sh`.

**The start lines.** Before the model loads, the launcher writes one sentence for each setting, with its unit and its source:

| Line | Example |
|---|---|
| The model | `CARL starts the server: NAME (FILE).` |
| The address | `Address: http://127.0.0.1:8080. Only this Mac can use the server.` |
| Slots and context | `Slots: 2 (from Auto-tune). Context: 96K tokens per slot (from Auto-tune).` With `--slots auto`: `Slots: 2 (auto: two slots fit).` |
| Context memory and RAM cache | `Context memory type: q4 (from Auto-tune). RAM cache: 8.0 GiB.` |
| Speculation | `Speculation: MTP + n-gram, 2 guesses (from Auto-tune).` With a drafter: `The MTP drafter is FILE.` |
| Sliding window (Gemma) | `Sliding window: full cache. CARL can restore saved sessions and prompts (cache.swa = auto).` or `Sliding window: window cache. It uses less memory, but CARL cannot restore saved sessions and prompts (cache.swa = auto).` |
| The log | `Log file: PATH. Batch: -ub 512. Your settings: ./carl.sh config show.` |

- The sources (`CARL_SOURCES`) are: `from your option` (a flag), `from the environment`, `from your settings` (`config.json`), `from Auto-tune`, `from the catalogue`, `from the model file`, `from Auto fit: the largest context that fits`, and `CARL's default`.
- Each refusal starts with `error:`. The dashboard shows these lines when a start fails.

### Slots

**Slots: 2 by default when they fit** (`--slots auto`).
- **Reason:** OpenCode subagents are separate sessions. With one slot, a subagent removes the main session from the slot. llama.cpp can keep the main session in the RAM cache only if it fits there. The states were 2.1–2.3 GiB, and the old limit was 2048 MiB (`exceeds cache size limit … skipping`). Thus, the server read the main session again after each subagent.
- **Flags for 2 or more slots:** `--parallel N --kv-unified --kv-unified-per-slot CTX -c N×CTX --no-cache-idle-slots -sps 0.5`.
- The last two flags are important:
  - By default, llama.cpp parks and clears idle slots at each new task (`--cache-idle-slots`).
  - By default, llama.cpp also gives a slot to each prompt that shares ≥10% of its tokens with that slot (`-sps 0.1`). A subagent that shares the system prompt meets this condition.
- **Measured (2026-10-01, M3 Pro 36 GB):** a follow-up turn after a subagent took 0.6 s (35B) and 1.9 s (27B). The prompt stayed fully cached in its slot.
- Two slots that write at the same time: the 35B gives +39% in total (each slot at 72–77% of its speed alone). The 27B gives 8.8 tok/s vs 9.6 alone (it shares the time). MTP works in both slots.
- `--slots 3` and `--slots 4` are for more subagents at the same time. The start check refuses them when they do not fit. `auto` never selects more than 2.

### The caches

The slots, the RAM cache (`--cache-ram`, 1–8 GiB from the free RAM) and the disk cache of OpenCode and Pi (`--slot-save-path`) are on the page [Caching](caching.md).

### Two models at the same time do not fit

- CAUTION: Do not load a second model while a server runs. The second load causes Metal out-of-memory errors.
- After that, the first server returns `Compute error` for each request. But `/health` continues to report ok. Only a restart repairs the server.

**Launch guard** (`guard_other_models` in `host/common.sh`). `serve-llama.sh` and Auto-tune refuse to start when:
- a process holds more than 8 GiB of resident memory (`BIG_GB`, default 8), or
- a process has a known model-server name (`llama-server`, `mtplx`, `ollama`, `LM Studio`).

- **Reason:** two models do not fit. A name check alone does not find servers that started in a different way. On 2026-10-02, a second model crashed the Mac (it restarted).
- `ALLOW_SECOND_MODEL=1` skips the check. Use it only when the large process is not a model.

### Sleep, model names, templates

- **Sleep stops requests.** The launchers keep `caffeinate -i` active while the server runs. `KEEP_AWAKE=0` turns this off. If you close the lid on battery power, the Mac still goes to sleep.
  - **Evidence:** before this change, idle sleep (1 min on battery) stopped a 74K prompt for 30+ min.
- **The server ignores the model name in a request** (single model). The GGUF that is loaded answers all requests. Thus, the model in the client must match the server. The OpenCode plugin `carl-model-check` warns when they are different.
- **Served names (since 1.3.0):** each model is served under its CARL name (`--alias` = the catalogue or custom name). Before 1.3.0, builds shared a family alias such as `qwen3.8-27b`, and OpenCode could show the wrong build.
- **The client lists (since 1.3.0):** the client configs list only the installed models, one entry each (`tools/carl.py client-models`). Each entry has the id, the label and the context for each slot (from the effective settings). It also has the thinking type from the card: `on-off` or `effort`. The default model of the clients is the model that the server runs now (a single model), else the model that a start loads ([OpenCode and Pi configs](client-configs.md#the-provider-and-the-models)).
- The dashboard compares the models in the configs on this Mac (`opencode.json`, `models.json`, CARL's provider) with the downloaded models. The Connect tab shows a warning when they are different.
- **Template override.** The server uses `--chat-template-file ~/models/templates/<model>.thinking-toggle.jinja`. The launcher makes this file at start ([How thinking works](thinking.md#how-thinking-works)). `THINK_TOGGLE=0` uses the template of the GGUF.

### Speculation for each model

The catalogue (`tune.spec`, `tune.spec_n`) or the Auto-tune result sets the speculation:

| Model | Speculation | Guesses |
|---|---|---|
| 27B dense (Q4, Q3) | MTP + n-gram | 1 |
| 35B MoE (Q4, stock and Heretic) | MTP + n-gram | 2 |
| Stock IQ3 builds (35B, 27B) | MTP + n-gram | 1 |
| 9B Distill | MTP + n-gram | 1 |
| Heretic 35B IQ3 (no MTP head) | n-gram only | 1 |
| orcarouter 27B IQ3 | n-gram only | 2 (a tie with MTP + n-gram) |
| Gemma 4 E4B, 12B, 26B-A4B, 31B (MTP drafter) | MTP + n-gram | 2 (measured: [Gemma 4](models.md#gemma-4)) |

- More guesses help a MoE: with 3B active parameters, it costs little to verify more tokens.
- 2 guesses lose on IQ quants ([IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)).
- A custom model gets MTP + n-gram with 1 guess when its GGUF has an MTP head, MTP + n-gram with 2 guesses when it has an MTP drafter (Gemma 4), else n-gram with 2 guesses.
- **A separate MTP drafter (Gemma 4).** The model file has no MTP head. The catalogue entry names a drafter file (`draft`). `tools/carl.py launch-env` gives `MTP_SOURCE=drafter` and `DRAFT=<the drafter's path>` when the drafter is downloaded. The launcher adds `-md "$DRAFT"` when `SPEC` contains `draft-mtp`, also when `SPEC` comes from the environment. The drafter uses the context memory of the model, so only its weights add memory: `llama-fit.py --plan` and `--check` get `--draft "$DRAFT"` and count them.
- **No MTP available.** `MTP_SOURCE=none` means that the file has no MTP head and that no drafter is downloaded. If the speculation uses MTP, the start uses `ngram-mod` instead and tells you one time (`launch-env` for a configured `SPEC`, the launcher for a `SPEC` from the environment).
- `DRAFT` and `MTP_SOURCE` always come from `tools/carl.py`. The launcher ignores them in the environment.

### The dashboard and the launch model

- The dashboard is `tools/llama-monitor.py` (since 2026-10-01). It has the tabs Live, Connect, Requests, Log and Settings ([The dashboard](architecture.md#the-dashboard)). Each section of a screen has its own level: collapsed, simple or full (Tab selects a section, `L` changes its level, `D` sets every section).
- Use only the left click. The dashboard does not use the right click, because terminals such as iTerm2 show their own context menu.
- A config that you copy from the Connect tab contains the API key. On the screen, the key stays masked until you show it.

When you start from a terminal, `run_server` (`host/common.sh`) does these steps:
1. It starts the server under `nohup` with job control on (`set -m`). Thus, the server gets its own process group, and Ctrl-C in the dashboard cannot reach it.
2. It starts `caffeinate -i -w <server pid>`.
3. It uses `exec` to change into the dashboard.

- **Result:** the server is a child process of the dashboard, so the dashboard must reap it. Otherwise, a stopped or crashed server stays as a zombie process, and `ps` still lists it. The `pid_alive()` function of the dashboard calls `waitpid(WNOHANG)` and treats state `Z` as dead.
- Without a terminal (scripts, `nohup`, `MONITOR=0`), the launcher starts the server with `exec` in the foreground. The benchmark tools need this.

### The API key and the settings folder

**API key** (`ensure_api_key` in `host/common.sh`):
- The server and the clients use `~/.config/carl/api-key` (mode 600, in a folder with mode 700). `API_KEY_FILE` sets a different path.
- If the file is missing, the first start copies the key from an earlier path: `~/.config/llm-deploy/api-key`, then `~/.mtplx/api-key` (before 1.2.0). The key stays the same, so the clients continue to work.
- If there is no earlier key, the first start makes a random 40-character key.
- The dashboard, Auto-tune, the bench tools and `tools/llama-wait-idle.sh` read the new path. While the new file does not exist, they read the old paths.

**Settings folder, formerly `~/.config/llm-deploy`** (CARL was called LLM-Deploy; `migrate_conf_dir` in `host/common.sh`):
- The launch commands of `./carl.sh` move the folder one time to `~/.config/carl` (folder 0700, files 0600). Help and unknown commands do not move it.
- A symlink `~/.config/llm-deploy -> carl` stays. Client configs from before the rename read the key there.
- If both folders exist, CARL uses `~/.config/carl` and does not change the old folder.
- Until the move, the readers use the old folder. The key order is `API_KEY_FILE` > `~/.config/carl/api-key` > `~/.config/llm-deploy/api-key` > `~/.mtplx/api-key`.
- `CARL_CONF_DIR` turns the move off.
- **`CARL_CONF_DIR`** sets a different settings folder. `tools/carl.py` (`domain/confdir.py`) and the launchers (`host/common.sh`) both use it: `config.json`, `models.json`, the disk cache (`slots/`) and `router-presets.ini` go there. The API key stays in `~/.config/carl`. Before this change, the launchers always used `~/.config/carl`, so a test run with a different folder wrote the real one.
- On the client side, the setup (`client/install.sh`) moves the same folder. `configure.py` (`OLD_NAMES`) renames CARL's own pieces:

| Before | Now |
|---|---|
| `llm-deploy.json` (state file) | `carl.json` |
| `~/.config/opencode/llm-deploy/` | `~/.config/opencode/carl/` |
| Provider `llm-deploy` | `carl` |
| The `llm-deploy:delegation` block in `APPEND_SYSTEM.md` | `carl:delegation` |
| The `LLM-Deploy:` marker of the Pi extension | `CARL:` |

- A provider block that you paste from the dashboard uses the id `carl`. The plugin packages are `carl-subagents-sidebar` and `carl-session-switcher` (command `carl.session.switch`).

### Router mode

Router mode is opt-in: `llama.mode = router`, `LLAMA_MODE=router` or `--router` (`carl_core/domain/router.py`, the router branch of `serve-llama.sh`).
- The router of llama.cpp 0.5.0 (`llama-server --models-preset FILE --models-max 1`, no `-m`) starts one child `llama-server` for the loaded model on a free port. It sends each request to the child by the `model` field.
- The presets file is `~/.config/carl/router-presets.ini`. The launcher writes it again at each router start.
- The start lines list the models that the router offers, each with its setup (for example `2 slots × 96K tokens, q4, window cache`), the models that it leaves out (with the reason), and the model that it loads first.

Measured on 2026-10-03 (M2 Max):
- `--models-max 1`: a request for a different model stops the loaded model ("evicting idle LRU"). The router waits until it **exits**, then starts the new model. Two models are never in memory. A busy model completes its request first, and the new request waits.
- An unknown model gives HTTP 400 `model 'X' not found`. Nothing loads, and the router does not log it.
- A stop of the router (SIGTERM) stops its child.
- `/slots` and `/metrics` need `?model=ID`. They **load the model if it is not loaded**, unless you add `&autoload=false`. The dashboard asks only about the loaded model, always with `autoload=false`.
- `/props` without a model answers `role: router`. `/models` lists each model with `status.value` (unloaded, loading, loaded, sleeping) and the arguments of the child.
- `/models/load` returns at once. The load takes 10 s to 2 min.

The presets INI:

| Section | Contents |
|---|---|
| `[*]` | CARL's shared flags: jinja, the reasoning format, `preserve_thinking`, `-ngl 999`, `-fa on`, `fit = off`, batch, ubatch, checkpoints, metrics, no mmproj, `slot-save-path` |
| One section for each downloaded model | Its effective settings: `ctx-size` (slots × context), `parallel`, the context memory types, `cache-ram`, sampling, `spec-type` and `spec-draft-n-max`, the thinking-toggle `chat-template-file` |
| A model with a downloaded MTP drafter, speculation with MTP | `spec-draft-model = <the drafter's path>` (the `-md` of a single start) |
| With 2 or more slots | `kv-unified`, `kv-unified-per-slot`, `cache-idle-slots = false`, `slot-prompt-similarity = 0.5` |
| A sliding-window model with a full cache | `swa-full = true` |
| The start model | `load-on-startup = true` |

- The keys are the long option names. `cache-idle-slots = false` becomes `--no-cache-idle-slots`.
- A model that fails the start check is left out. A comment in the file and the banner tell why.
- The command-line arguments of the router override every preset. Thus, the router gets only the host, the port, the key file and the log flags (and `llama.extra_args`).
- The log of the router holds the lines of its children as `[PORT] M.SS.mmm.uuu L ...`, with the clock of the child. The dashboard removes the prefix and moves the time by the "spawning server instance ... on port PORT" line. It hides the "proxying request" lines of the router (one for each dashboard poll).
- **Disadvantage:** each switch empties the RAM cache. The new child starts cold. A saved session belongs to the model it ran on (the model name is in the file name): when you switch back to that model, OpenCode and Pi restore the session from the disk cache (with Saved sessions on, a session of 4,096 tokens or more, and for a Gemma model the full cache). A session that continues on the new model is read again in full; only the agent's saved prompt (the system prompt and the tools) comes from the disk cache.

### Network modes

The network modes are in `host/common.sh`:

| Mode | Address | Who can connect |
|---|---|---|
| `--local` (default since 1.3.0) | 127.0.0.1 | Only this Mac |
| `--vm` | 192.168.42.1 (`VM_HOST`) | The VMware Fusion VM and this Mac |
| `--host ADDR` or `HOST=ADDR` | ADDR | Every computer that can reach ADDR |

- `--local` is the default also with no flag, `NET` or `llama.net`. When the VMware network is up, the banner tells you that `--vm` serves a VM client.
- `--vm` uses the VMware Fusion NAT network (vmnet8). The Mac is `192.168.42.1` on `bridge101`. The Kali VM is `192.168.42.130`. The mode fails if no interface has this address.
- Before 1.3.0, the default was auto: the VM address when vmnet8 was up. Thus, a VMware user exposed the server to the VM network without a question. `NET=auto` now means local (the banner tells you). `migrate_config()` (`carl_core/domain/settings.py`) removes a saved `llama.net = auto` one time, with a note on stderr.
- `--host ADDR` has priority over the modes. ADDR must be on an interface of this Mac: for example its LAN address, or the address on a Parallels network (often `10.211.55.2`).
- The settings file can hold an address: `llama.host` in `config.json`. The Settings tab writes it when you select an address in the network row. The order of priority is: flag, environment, settings file.
- The banner shows the mode. For an address that is not 127.0.0.1 or the VM address, it shows a CAUTION. Every computer that can reach the address can use the server. Only the API key protects it.
- After a restart from the Settings tab, the health check also tries the selected address. Thus, a server on the LAN address does not show as a failed start.
- CAUTION: The launcher refuses 0.0.0.0 and `::`, also through `HOST=0.0.0.0`. The macOS firewall is off on this Mac, so a wildcard address would make the model available to the LAN.

**The address and the key of the setup** (`client/setup`, which runs `client/install.sh`; `./carl.sh install` on the server Mac, `./setup` in the client package on other computers):
- With no address argument, the setup reads `remote.json` in the client folder. The client package has it ([The client package](client-sync.md#the-client-package-carries-the-connection)). Without it, the default is `--local` on macOS and `--vm` on Linux.
- `--local` sets the clients to the address on which the server actually listens. `--host ADDR` sets any address. `--port N` sets the llama.cpp port (default 8080).
- The old form `install.sh HOST [X] [PORT]` still works. The installer ignores the second value (an old MTPLX port) with a note. The third value is the llama.cpp port.

The setup takes the API key from the first of these sources:
1. `--key KEY` or `--key-file FILE`
2. `$CARL_API_KEY`
3. The file `api-key` in the client folder (the client package has it)
4. `~/.config/carl/api-key` (the server key on the Mac, or the key from a previous run)
5. `~/.config/llm-deploy/api-key`
6. With `--local`: `~/.mtplx/api-key`
7. `~/.config/mtplx/api-key`
8. A hidden prompt

- The setup stores the key at `~/.config/carl/api-key` (mode 600). The OpenCode and Pi configs point at that file.
- If the stored key is different, the setup keeps a backup (`api-key.bak.<time>`).
- A new run changes old configs to the new path. It does not delete the old client file, because a provider of your own can use it. Delete it when nothing uses it.
- `--key` leaves the key in the shell history.
- The smoke test gives the key to `curl` from a file descriptor (`curl -H @<(…)`). Thus, the key does not show in `ps`.

### Checks before a start

| Check | What it does |
|---|---|
| Dependency check (`ensure_deps`) | Finds `llama-server`, `aria2c`, `ansifilter` and `zstd`. In a terminal, it offers `brew install` for the missing tools. Without a terminal, it shows the command. Only `llama-server` is necessary. `SKIP_DEPS=1` skips the check. |
| Port guard | No start if a process listens on the port (`port_pid`). The check runs before the launcher changes `llama-server-latest.log` or loads a model. |
| Launch guard | No start if a second model is in memory (see above) |
| Memory check | `tools/llama-fit.py --check` (see below) |

- **Port guard history:** before this guard, a second `serve.sh` (10:08 on 2026-10-01) failed to bind. But it had already pointed the `latest` symlink to its own 4-line failure log.
- **Memory check:** `check_start()` in `carl_core/domain/fit.py` compares weights + context memory + buffers with the GPU memory limit ([GPU memory limit](memory.md#gpu-memory-limit-and-what-fits)). The dashboard uses the same function.
  - Over the limit, the start is **refused** (exit 3, then the launcher exits 1). Such a start does not load, or it makes the Mac swap and become very slow.
  - The first line is one `error:` sentence with the memory needed and the limit: `error: NAME does not fit this Mac with 4 slots × 256K tokens (q8): it needs 24.97 GiB, and the GPU memory limit is 24.96 GiB. …`. Two decimals show when the two values round to the same number.
  - It gives the largest context that fits, with these slots and with 1 slot.
  - It gives Auto fit's choice: the best downloaded model, and a better catalogue model to download.
  - `FIT_CHECK=0` is the expert override. A check that cannot run (an unreadable header) only shows a warning.
- **`/metrics` gauges reset at each read** (`--metrics` turns on this Prometheus endpoint). `prompt_tokens_seconds` and `predicted_tokens_seconds` cover only the time since the last read. Thus, calculate averages from the `*_total` counters. The dashboard does this.
- **The server does not log its context memory (KV cache) size** at the default log level. Thus, the dashboard calculates it ([Context memory](memory.md#context-memory-kv-cache-and-recurrent-state)).

### Auto fit and the start model

- With `llama.model = auto`, `tools/carl.py` (`resolve_launch`) starts Auto fit's choice (`llama.auto_goal`, `llama.auto_fit`).
- If the choice is not downloaded, it starts the best downloaded stock model that fits, and tells you. It never selects an abliterated model.

**Auto fit** (`carl_core/domain/autofit.py`, pure, with unit tests):
- The candidates are the ranked stock models: a rank, and not abliterated. A custom model is a candidate only when its card says `auto_fit: true`.
- The goal selects the family first: `everyday` = the fast builds (`arch: moe`, and a dense build with `fast: true`, the Gemma 4 E4B), `hard-code` = `arch: dense`. The other builds are the fallback.
- The passes, in this order: 2 × 96K, then 1 × 96K, then 1 × the largest context ≥ 32K. The first pass that a candidate of the family meets wins, best rank first.
- The memory that a model can use is min(GPU memory limit, RAM − the memory kept free). The memory kept free is as for the RAM cache: 6 GiB, 10 GiB with the VM network, or `RESERVE_GB`.
- The result gives each candidate with a better quality rank that was not selected, and why (`Rejection.line()`). Examples: `qwen3.8-27b (quality rank 1) is dense: Auto fit keeps it for the hard code goal`, and for a model that fits no pass, a reason that starts with `does not fit:`.
- `Plan.label()` gives the plan in short (`2 × 96K tokens, q4`), `Plan.describe()` in words (`2 slots × 96K tokens (q4)`).
- If the context of the choice is less than 96K, an `auto` start uses that context, unless `config.json` sets one. `CARL_SOURCES` then shows `ctx:auto-fit`.
- Offline (no header can be read), the download offer and `./carl.sh download default` use the catalogue `default`. If the weights of the default alone do not fit, they use `default_small`.

### Port and PID lookups (no lsof)

CARL does not use `lsof`. `lsof` examines each mounted file system. On a stale network share (for example a disconnected Time Machine SMB volume), it stops in the kernel, and no signal can stop it. Before 1.1.0, this stopped the dashboard and `./carl.sh` on a Mac with such a volume.

The launchers (`port_pid`, `port_host` in `host/common.sh`), the dashboard, `client/install.sh` and the test tools use `netstat -anv -p tcp`. Its `process:pid` column gives the PID of the process that listens on a port. To do the same by hand:

```bash
netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}'
```

`$(NF-8)` counts from the end of the line, because a process name can contain spaces.

### Settings: config.json, Auto-tune and the catalogue

`tools/carl.py launch-env` gives each llama.cpp start its model and settings. Each value comes from the first source that has it:

| Order | Source | Where |
|---|---|---|
| 1 | Command-line flags | `--model`, `--ctx`, `--kv`, `--slots`, … |
| 2 | Environment variables | `CTX`, `KV`, `SPEC`, `SPEC_N`, `TEMP`, … |
| 3 | `config.json` | `~/.config/carl/config.json`: the `llama` section, and the profile `models.<name>` |
| 4 | Auto-tune result for this Mac | `~/.config/carl/models.json`: `models.<name>.tune.settings` |
| 5 | Catalogue | `host/catalog.json`: the `tune` of the model. For a custom model: values from its GGUF header. |
| 6 | Built-in defaults | q4, 96K, auto slots, MTP + n-gram with 1 guess, temperature 1.0, … |

**`config.json` sections:**

| Section | Keys |
|---|---|
| `llama` | `model`, `auto_goal`, `auto_fit`, `mode`, `net`, `host`, `cache_ram`, `ub`, `batch`, `ckpt`, `ckpt_step`, `think_toggle`, `extra_args` |
| `models.<name>` | `kv`, `ctx`, `slots`, `spec`, `spec_n`, `temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `alias` |
| `paths` | `models_dir` |
| `cache` | `disk_gb`, `prefix`, `sessions`, `save`, `auto_s`, `share`, `swa` ([Caching](caching.md#the-cache-settings)) |

- **Validation:** each key has a type, a range or a list of choices. `./carl.sh config show` lists them. A bad value stops the start with an error. An unknown key gets a warning and has no effect.
- CARL writes the file atomically, with mode 600.
- **Migration:** before `config.json`, the dashboard wrote `llama.env`. If `config.json` is missing, CARL converts this file one time.
- **Files from before 1.2.0** can hold settings of removed features (CHANGELOG.md). The file loads. CARL ignores these settings, and `./carl.sh config show` warns about a removed section. They go away the next time that CARL saves the file.
- `SETTINGS_FILE=none` skips `config.json`.
- **Custom models:** each `.gguf` in the models folder (`paths.models_dir`, or `MODELS_DIR`) is a model, also when it is not in the catalogue.
- `./carl.sh download hf:OWNER/REPO/FILE.gguf` gets the revision, the size and the SHA-256 from the Hugging Face API. It records the model in `models.json`, downloads it and verifies it.

**Custom model cards** (`models.json` `models.<name>.card`):
- A card is for a custom model only. Catalogue models are read-only: CARL refuses to write a card for them, and ignores a card written by hand under a catalogue name.
- `./carl.sh card NAME set FIELD VALUE` and `unset FIELD` write the card. The dashboard's edit mode does the same (Models panel, `e`).
- When CARL saves a card, it also records the `path` and the `source` of the model, as Auto-tune does.

Example:

```json
"my-coder-7b.q4_k_m": {
  "path": "/Users/me/models/gguf/My-Coder-7B.Q4_K_M.gguf",
  "source": "file",
  "card": {
    "role": "Fast local coder", "good_for": ["agent coding", "hard code"], "arch": "moe", "quant": "Q4_K_M",
    "rank": 4, "thinking": "on-off", "auto_fit": false,
    "pick_instead": [{"model": "qwen3.8-27b", "when": "harder code"}]
  }
}
```

| Field | Value |
|---|---|
| `label`, `role` | Text, at most 60 characters |
| `good_for` | The catalogue's tags: `agent coding`, `hard code`, `chat & writing`, `uncensored` |
| `why_use`, `trade_offs`, `hardware`, `uncensored` | Text, at most 1000 characters each |
| `abliterated`, `auto_fit` | true or false |
| `arch` | `dense` or `moe` |
| `quant` | Text, at most 40 characters |
| `rank` | An integer ≥ 1 |
| `thinking` | `on-off` or `effort` |
| `pick_instead` | `[{model, when}]` |

`parse_custom_card()` (`carl_core/domain/records.py`) uses the rules of the catalogue (`_card()`):
- only the catalogue's tags, and the `uncensored` tag only when abliterated
- the role length
- rank ≥ 1
- `pick_instead` models that exist

It adds these rules:
- only known fields
- one line of printable text (no control characters, because the terminal shows the text)
- the `uncensored` text only when abliterated
- `auto_fit` only with `rank` and `arch`, and not abliterated

More about cards:
- A bad card in the file stops the model list with an error that names the model and the field.
- When you save a card, each `pick_instead` model must exist. When a card loads, CARL leaves out an entry whose model is gone (deleted or renamed).
- **Where CARL reads a card:** `build_models()` (`carl_core/domain/models.py`) joins the card into the record of the custom model. Thus, the model lists, the sort by quality (the rank), the filters and auto fit use the same fields as for a catalogue model.
- **Auto fit** uses a custom model only when its card says `auto_fit: true`. The rank of a user is not measured, so this is opt-in. `./carl.sh download default` and the download offer use only catalogue models.

### Auto-tune

`./carl.sh tune NAME [--quick | --long]` (`tools/carl-tune.py`) measures one model on this Mac. `./carl.sh tune all` measures each downloaded model in turn. `--dry-run` measures but does not save.

- Auto-tune starts its own server on port 8093, one time for each speculation mode. The default run takes about 5–10 min, `--quick` about 4 min, and `--long` 10–40 min more.
- It refuses to start if a server runs on 8080 or 8093. It also refuses if a different large process is in memory (more than `BIG_GB`, default 8 GiB, or a known model server). `ALLOW_SECOND_MODEL=1` skips this check, as for the launch guard.

The steps:

1. **Memory:** the largest context for each slot that fits the GPU memory limit, with 1 and 2 slots (the same calculation as `./carl.sh fit`). If not even a 16K context fits, Auto-tune stops.
2. **Speculation:** the modes in the next table. Each mode writes prose, new code and a code edit, two times. The score is a weighted geometric mean (prose 0.4, code 0.4, edit 0.2). A mode with more guesses or more parts must be 3% better than a simpler mode to win.
3. **Prompt reading:** cold reads at 8K, 32K and 64K tokens. `--quick` reads at 8K and 32K. `--long` also reads at 128K and 192K, and measures the write speed at each depth. The time for each token increases about linearly with the depth. From this, Auto-tune calculates the time to read a full context.
4. **Parallel requests** (not with `--quick`): 1, 2, 3 and 4 requests at the same time (`llama-batched-bench`).
5. **Result** (`choose_ctx()` in `carl_core/domain/tuning.py`): q4 context memory, the best speculation, the context, and 2 slots if two contexts fit. Auto-tune saves it in `~/.config/carl/models.json`, with the speed of each mode, the read speeds and the zones.

| Speculation mode | When |
|---|---|
| none, `ngram-mod` with 2 guesses | Always |
| `ngram-mod` with 1 guess | When the GGUF has no MTP head and the model has no downloaded MTP drafter |
| `draft-mtp` and `draft-mtp,ngram-mod` with 1 guess | When the GGUF has an MTP head, or the model has a downloaded MTP drafter |
| `draft-mtp` and `draft-mtp,ngram-mod` with 2 guesses | With an MTP head, not with `--quick`. With an MTP drafter, always |
| `draft-mtp` and `draft-mtp,ngram-mod` with 3 and 4 guesses | With an MTP drafter, not with `--quick` |

| Context zone | A full cold read takes |
|---|---|
| Fast | ≤ 3 min |
| Slow | ≤ 10 min |
| Very slow | more |

- The fast zone is never smaller than 96K (`ctx_zones()` in `carl_core/domain/models.py`).
- If 96K fits one slot, 96K is the minimum. Above it, Auto-tune keeps the catalogue context while a cold read of it is not worse than slow on this Mac and it fits. Else, it uses the largest standard context (96K, 128K, 160K) in the fast zone that fits.
- If 96K does not fit, Auto-tune uses the largest standard context (32K, 48K, 64K) that fits.

NOTE: The edit workload copies `tools/carl_core/adapters/llama_server.py` back (since 1.1.0). Thus, edit scores are not directly comparable with older Auto-tune results.

The Auto-tune panel of the dashboard stops the running server, runs the tune, and starts the server again.

### Server flags

This table shows the flags that `host/serve-llama.sh` gives to `llama-server` (single model), and the reasons:

| Setting | Value | Why |
|---|---|---|
| Bind | `--host 127.0.0.1 --port 8080 --api-key-file ~/.config/carl/api-key` | This Mac only by default (`--vm`: 192.168.42.1). A shared key. |
| Model name | `--alias NAME` | The CARL name of the model |
| Offload | `-ngl 999` | The whole model is on the GPU (Metal). |
| Flash attention | `-fa on` | Necessary for a quantized V cache. It was on for each measurement. |
| Memory fitting | `--fit off` (router presets: `fit = off`) | CARL sets `-ngl` and `-c`, and checks the memory itself (`llama-fit.py --check`). llama.cpp's own memory fitting only probes here. Its probe fails for the Gemma 4 MTP drafter ("Gemma4Assistant requires ctx_other"), and that showed as 1 error on every Gemma start. |
| Context memory (KV cache) | `-ctk q4_0 -ctv q4_0` (`--kv q8` → q8_0) | q4 against q8 (27B, M3 Pro, 2026-09-24): +16% read speed, about 2 GiB less memory, the same write speed, 8/8 needle recall at 66K. Do not mix K and V types (the read speed is about 5× slower). |
| Context | `-c` = slots × 96K (`--ctx` sets the context for each slot) | A large context reads cold prompts and writes much more slowly ([Context length](memory.md#context-length-what-a-larger-context-costs)). The model maximum is 262144. The server allocates the whole context memory at start. |
| Batching | `-b 2048 -ub 512` | `-ub 512` gave the best measured result (90.5 tok/s vs 88.6 and 86.1 for 1024 and 2048). |
| Slots | `--parallel N`; with 2 or more: `--kv-unified --kv-unified-per-slot CTX --no-cache-idle-slots -sps 0.5` | The main OpenCode session and a subagent each keep their own slot and cache. |
| RAM cache and checkpoints | `--ctx-checkpoints 8 --checkpoint-min-step 4096 --cache-ram N` | Checkpoints let follow-up turns use the cache again, because the recurrent layers of Qwen cannot trim it. The RAM cache holds sessions that are not in a slot (each held state is 2–2.5 GiB). N = RAM − the memory needed by the model − the memory kept free, 1–8 GiB. If the size calculation fails, N is 4096. |
| Disk cache | `--slot-save-path ~/.config/carl/slots` (`CARL_CONF_DIR/slots` when it is set) | OpenCode and Pi save and restore prompts and sessions here ([Caching](caching.md)). |
| Sliding window | `--swa-full` (only for a sliding-window model, when `cache.swa` gives full) | A restored state needs every layer at full length ([Sliding-window models](caching.md#sliding-window-models)). |
| Projector | `--no-mmproj` | CARL uses text only. |
| Templates | `--jinja --reasoning-format deepseek`, `--chat-template-kwargs '{"preserve_thinking":true}'`, `--chat-template-file` (patched) | The server sends the reasoning to `reasoning_content`. `reasoning_effort: none` turns off thinking ([How thinking works](thinking.md#how-thinking-works)). |
| Sampling | `--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0 --repeat-penalty 1.0` (Gemma 4: `--top-k 64`) | The values of the catalogue `tune` of the model: the Qwen values for thinking mode, Google's values for Gemma 4 ([Sampling](sampling.md#sampling-and-output-limits)). `TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE`, `REPEAT` override them. |
| Speculation | `--spec-type TYPE --spec-draft-n-max N` (from the tune of each model; none with `SPEC=none`) | The best of the modes that were measured on each model ([Performance](performance.md#performance-measured), [IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)). |
| MTP drafter | `-md DRAFT` (only a model with a separate drafter, Gemma 4, when `--spec-type` contains `draft-mtp`) | The drafter gives MTP speculation to a model file without an MTP head. On the Gemma 4 E4B (new code, M2 Max, llama.cpp 0.5.0, 2026-10-04): 76.0 tok/s with MTP and 2 guesses, 61.1 without speculation. |
| Logging | `--log-file ~/models/logs/llama-server-<ts>.log --log-timestamps --log-prefix` | `llama-server-latest.log` points to the newest log. Use `tools/llama-log.sh` to read it. `LOG_FILE=none` turns the file off. |
| Metrics | `--metrics` | A Prometheus endpoint at `/metrics` |
| Keep awake | `caffeinate -i -w <server pid>` | If the Mac goes to sleep, requests stop in the middle of the prompt. |
| Dashboard in the same terminal | `run_server` in `host/common.sh` | One command shows the server live. `MONITOR=0` gives a plain foreground server. |
| Network | `--local` (127.0.0.1, the default), `--vm` (192.168.42.1) | The launcher refuses 0.0.0.0. |
| Memory check | `tools/llama-fit.py --check` before the model loads | No start over the GPU memory limit. `FIT_CHECK=0` skips it (the expert override). |
| Launch guard | `guard_other_models` | No start if a second model is in memory. `ALLOW_SECOND_MODEL=1` skips it. |
| Port guard | No start if the port is in use (`netstat`, not `lsof`) | A second model would collide with the running one. Also, `llama-server-latest.log` continues to point at the running server. |

- These environment variables override the settings: `CTX`, `KV`, `KV_K`, `KV_V`, `SLOTS`, `UB`, `BATCH`, `CKPT`, `CKPT_STEP`, `CACHE_RAM`, `RESERVE_GB`, `SPEC`, `SPEC_N`, the sampling variables, `MODEL`, `ALIAS`, `HOST`, `PORT`, `NET`, `LLAMA_MODE`, `SWA_MODE`, `API_KEY_FILE`, `LOG_FILE`, `THINK_TOGGLE`, `KEEP_AWAKE`, `MONITOR`, `FIT_CHECK`.
- `config.json` can hold the same settings ([Settings](#settings-configjson-auto-tune-and-the-catalogue)).
- Extra arguments go directly to `llama-server`. `llama.extra_args` in `config.json` does the same.
- `./carl.sh -h` lists the commands. `./carl.sh help llama` lists the options. `./carl.sh help env` lists the environment variables.

# CARL Reference: The llama.cpp server

[Index](../REFERENCE.md) · the server, its settings, Auto-tune and its flags.

## The server: llama.cpp

| | llama.cpp (`./carl.sh llama`) |
|---|---|
| Version | 0.4.1 (Homebrew), `llama-server` |
| Port | 8080 |
| API | OpenAI API (`/v1/chat/completions`, `/v1/models`), Bearer key `~/.config/carl/api-key` |
| Weights | GGUF (`~/models/gguf/`): the catalogue models, and any other GGUF |
| Models available | Qwen3.6-35B-A3B (default, `qwen3.6-35b-a3b`), Qwen3.8-27B stock, Qwen3.8-27B abliterated (orcarouter), and their Q3/IQ3 builds |
| KV cache | Real q4_0 (default) or q8_0, allocated one time with no bf16 copy. Set it with `--kv`. |
| Usable context | 96K per slot by default, up to 256K (`--ctx`) |
| Memory behaviour | The server allocates the full KV cache at start. Memory stays flat during a session (a 56K → 81K session was measured). |
| Prompt reuse | Context checkpoints (8, every 4K tokens) at user-message boundaries. RAM prompt cache, sized automatically to 1–8 GiB ([section 2](memory.md#ram-prompt-cache-and-checkpoints-measured-2026-10-02)). |
| Speculation | MTP head + n-gram (`draft-mtp,ngram-mod`). The draft count is set for each model. |
| Decode (27B) | 10.5–11 tok/s on new text, 27 tok/s when it re-emits text |
| Prompt read speed (27B, cold) | ~85–90 tok/s at 2–9K, ~56–65 tok/s at 66K |
| Thinking off from OpenCode | Yes: `none` variant (patched template) |
| Thinking off from Pi | Yes: `off` → `chat_template_kwargs.enable_thinking=false` |
| Effort values accepted | Depends on the template of the model ([section 5](thinking.md#thinking-by-model)) |
| Bad effort value | The Qwen3.8 template raises an error (the request fails). |
| Concurrency | 2 slots by default (`--slots auto`): two conversations at the same time, each with its own cache. More requests wait in a queue. |
| Stop | In the monitor: `q`, then `s`. Or kill the PID that `netstat -anv -p tcp` shows for :8080 ([Port and PID lookups](#port-and-pid-lookups-no-lsof)) |
| Logs | `~/models/logs/llama-server-*.log` (`tools/llama-log.sh`) |
| Health | `/health`, `/props`, `/metrics`, `/slots` |

**Other servers considered:**
- MTPLX was evaluated (2026-09/10) and removed in 1.2.0. On 32–36 GB Macs, it could not hold long sessions: HTTP 507 from ~4–56K tokens (it depends on the free memory), no real quantized KV cache in 2.11, and a 48K cap. llama.cpp holds 2 × 96K slots. The last version with MTPLX support is commit 470c316.
- NOTE: oMLX (a different MLX server) was researched as an alternative and not used. It was not tested on this Mac.

---

## llama.cpp details

`host/serve.sh` gives the `llama` command to `host/serve-llama.sh`. With no arguments, `./carl.sh` opens the dashboard. It attaches to a server on port 8080. If no server runs, it starts llama.cpp with the saved settings. If no model is downloaded, it first offers to download auto fit's pick for this Mac. `./carl.sh -h` prints the help. The flags that `serve-llama.sh` uses are in [Server flags](#server-flags).

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
- **Template override.** The server uses `--chat-template-file ~/models/templates/<model>.thinking-toggle.jinja`. This file is generated at start ([section 4](thinking.md#how-thinking-works)). To use the template of the GGUF, set `THINK_TOGGLE=0`.
- **Speculation per model** (catalogue `tune.spec` and `tune.spec_n`, or the Auto-tune result): The 27B dense uses MTP + n-gram with 1 draft token. The 35B MoE (Q4) uses 2. More drafts help a MoE, because with 3B active parameters it costs little to verify more tokens. The stock IQ3 builds use MTP + n-gram with 1 draft token: 2 drafts lose on IQ quants. The abliterated IQ3 builds use n-gram only (Heretic 35B: no MTP head, n=1; orcarouter 27B IQ3: n=2, a tie with MTP + n-gram) ([IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)).
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
- **API key** (`ensure_api_key` in `host/common.sh`): the server and the clients use `~/.config/carl/api-key` (mode 600, in a folder with mode 700; the folder of `config.json`). `API_KEY_FILE` overrides the path.
  - If the file is missing, the first server start copies the key from an earlier path (`carl_core/domain/apikey.py`; `~/.mtplx/api-key` before 1.2.0). It is the same key, so existing clients continue to work. If there is no old key, it makes a random 40-character key.
  - The dashboard, Auto-tune, the bench tools and `tools/llama-wait-idle.sh` read the new path. While the new file does not exist, they read the old paths.
- **Settings folder, formerly `~/.config/llm-deploy`** (CARL was called LLM-Deploy; `migrate_conf_dir` in `host/common.sh`, `carl_core/domain/confdir.py`). Every `./carl.sh` command except help moves it once to `~/.config/carl` (folder 0700, files 0600) and leaves the symlink `~/.config/llm-deploy -> carl`: client configs from before the rename read the key there. If both folders exist, CARL uses `~/.config/carl` and does not change the old one. Until the move, the readers use the old folder, and the key chain is `API_KEY_FILE` > `~/.config/carl/api-key` > `~/.config/llm-deploy/api-key` > `~/.mtplx/api-key`. `CARL_CONF_DIR` turns the move off. On the client side, `client/install.sh` moves the same folder, and `configure.py` (`OLD_NAMES`) changes CARL's own `llm-deploy.json`, `~/.config/opencode/llm-deploy/`, provider `llm-deploy`, the `llm-deploy:delegation` block in `APPEND_SYSTEM.md` and the `LLM-Deploy:` marker of the Pi extension to `carl.json`, `~/.config/opencode/carl/`, `carl`, `carl:delegation` and `CARL:`. The dashboard's pasted provider id and the plugin packages are `carl` (`carl-subagents-sidebar`, `carl-session-switcher`, command `carl.session.switch`).
- **Served names and the client lists (1.3.0):** every model is served under its CARL name (`--alias` = the catalogue or custom name; before 1.3.0 builds shared a family alias such as `qwen3.8-27b`, so OpenCode's label could name the wrong build). The client configs list only the installed models (`tools/carl.py client-models`: id, label, window per slot from the effective settings, thinking `on-off` | `effort` from the catalogue's or the user's card), one entry each; the default model is the one a start loads. The dashboard compares what the configs on this Mac list (`opencode.json` / `models.json`, CARL's provider) with the downloaded models and warns in the Connect tab.
- **Router mode** (opt-in: `llama.mode = router`, `LLAMA_MODE`, `--router`; `carl_core/domain/router.py`, the router branch of `serve-llama.sh`). llama.cpp 0.5.0's router (`llama-server --models-preset FILE --models-max 1`, no `-m`) starts one child `llama-server` per loaded model on a free port and proxies requests by the `model` field. Measured 2026-10-03 on this Mac:
  - `--models-max 1`: a request for another model evicts the loaded one ("evicting idle LRU"), waits for it to **exit**, then spawns the new one: never two models in memory. A busy model finishes its request first (the new request queues).
  - An unknown model: HTTP 400 `model 'X' not found` (nothing loads; the router doesn't log it). Stopping the router (SIGTERM) stops its child.
  - `/slots`, `/metrics` need `?model=ID`, and **load the model if it isn't loaded** unless `&autoload=false` is added: the dashboard asks only about the loaded model, always with `autoload=false`. `/props` without a model answers `role: router`; `/models` lists every model with `status.value` (unloaded / loading / loaded / sleeping) and the child's arguments. `/models/load` returns at once (the load takes 10 s to 2 min).
  - The presets INI: `[*]` = CARL's shared flags (jinja, reasoning format, `preserve_thinking`, `-ngl 999`, `-fa on`, batch, ubatch, checkpoints, metrics, no mmproj), one section per downloaded model with its effective settings (`ctx-size` = slots × window, `parallel`, `kv-unified*` and `cache-idle-slots = false` / `slot-prompt-similarity` with 2+ slots, KV types, `cache-ram`, sampling, `spec-type` / `spec-draft-n-max`, the thinking-toggle `chat-template-file`); keys are long option names, `cache-idle-slots = false` becomes `--no-cache-idle-slots`. A model whose setup fails the start check is left out (a comment and the banner say why). `load-on-startup` marks the start model. Command-line arguments of the router override every preset, so the router gets only host, port, key file and log flags (plus `llama.extra_args`).
  - The router's log file carries its children's lines as `[PORT] M.SS.mmm.uuu L ...` with the child's own clock: the dashboard strips the prefix and moves the time by the router's "spawning server instance ... on port PORT" line; the router's per-request "proxying request" lines (one per dashboard poll) are hidden.
  - **Downside:** every switch evicts the prompt cache (the new child starts cold): the next request re-reads the whole conversation, and so does switching back.
- **Network modes** (`host/common.sh`):
  - `--vm` = 192.168.42.1. This is the VMware Fusion NAT network (vmnet8). The Mac is `192.168.42.1` on `bridge101`. The Kali VM is `192.168.42.130`. The VM and the Mac itself can both connect to this address. The mode fails if this address is missing.
  - `--local` = 127.0.0.1. Only the Mac can connect. **The default since 1.3.0** (also without any flag, `NET` or `llama.net`). When the VMware network is up, the banner says that `--vm` serves a VM client.
  - Before 1.3.0 the default was auto: the VM address whenever vmnet8 existed, so a VMware user exposed the server to the VM network without asking. Removed: `NET=auto` means local (the banner says so), and a saved `llama.net = auto` is dropped once by `migrate_config()` (`carl_core/domain/settings.py`), with a note on stderr, so it means local.
  - `--host ADDR` (or `HOST=ADDR`) has priority over the modes. ADDR must exist on an interface of this Mac: for example its LAN address, or the address on a Parallels network (often `10.211.55.2`). `VM_HOST=ADDR` changes the address of `--vm`.
  - The settings file can hold an address (`llama.host` in `config.json`; the Settings tab writes it when you select an address in the network row). Order of priority: flag, environment, settings file.
  - The start-up banner shows the mode. For an address that is not 127.0.0.1 or the VM address, it shows a CAUTION: every computer that can reach the address can use the server, and only the API key protects it.
  - After a restart from the Settings tab, the health check also tries the selected address. Thus, a server on the LAN address is not taken as a failed start.
  - CAUTION: The script refuses 0.0.0.0 / `::`, also through `HOST=0.0.0.0`. The macOS firewall is off on this Mac, so a wildcard bind would make the model available to the LAN.
  - `client/install.sh --local` sets the clients to the address on which the server actually listens. `--host ADDR` sets any address. `--port N` sets the llama.cpp port (default 8080). The old positional form `install.sh HOST [X] [PORT]` still works: the second value (an old port) is ignored with a note, and the third is the llama.cpp port.
  - API key for `client/install.sh` (first hit wins): `--key KEY` / `--key-file FILE`, `$CARL_API_KEY`, a file `api-key` next to the script, on a Mac with `--local` the server key (`~/.config/carl/api-key`, else the old `~/.mtplx/api-key`), the key from a previous run (the same new path, else the old `~/.config/mtplx/api-key`), a hidden prompt. The installer stores it at `~/.config/carl/api-key` (mode 600), and the OpenCode and Pi configs point at that file. A new run changes old configs to the new path. It does not delete the old client file (a provider of your own can use it): delete it when nothing uses it. `--key` leaves the key in the shell history. The smoke test gives the key to `curl` through a header file (`curl -H @file`, curl 7.55 or later), so the key does not show in `ps`.
- **Memory check before load.** `serve-llama.sh` runs `tools/llama-fit.py --check` (`check_start()` in `carl_core/domain/fit.py`; the dashboard's fit line uses the same function). When weights + KV + buffers are more than the GPU limit ([below](memory.md#gpu-memory-limit-and-what-fits)), the start is **refused** (exit 3 → the launcher exits 1): such a start fails to load or swaps the Mac to a crawl. The message names the need and the limit, the largest window that fits (with these slots and with 1), and auto fit's alternative (the best downloaded model, and the catalogue pick to download when it is better). `FIT_CHECK=0` is the expert override. A check that cannot run (an unreadable header) only warns.
- **Port guard.** `serve-llama.sh` does not start if a process already listens on the port (`port_pid`, see below). It does this check before it touches `llama-server-latest.log` or loads anything. Before this guard, a second `serve.sh` (10:08 on 2026-10-01) failed to bind. But it had already pointed the `latest` symlink to its own 4-line failure log.
- **`/metrics` gauges reset at each read.** (`--metrics` enables this Prometheus endpoint.) `prompt_tokens_seconds` and `predicted_tokens_seconds` cover only the time since the last scrape. Thus, you must calculate averages from the `*_total` counters. The monitor does this.
- **The server does not log its KV cache size** at the default log level. Thus, the monitor calculates it ([below](memory.md#context-memory-kv-cache-and-recurrent-state)).
- **Dependency check** (`ensure_deps` in `host/common.sh`). Before a start, the launchers look for `llama-server` (llama.cpp), `aria2c` and `ansifilter`. In a terminal, they offer to run `brew install` for the missing tools. Without a terminal, they show the command. Only `llama-server` is necessary; the others are optional. `SKIP_DEPS=1` skips the check.
- **No model, or auto fit's pick is not downloaded.** `./carl.sh` with no arguments offers to download auto fit's pick (`llama-fit.py --pick-default`: the whole catalogue, everyday goal) if no model is downloaded. If you answer no, or nothing fits, it opens the dashboard without a server. With `llama.model = auto`, `tools/carl.py` (`resolve_launch`) starts auto fit's pick (`llama.auto_goal`, `llama.auto_fit`); if the pick is not downloaded, it starts the best downloaded stock model that fits, and says so. It never falls back to an abliterated model.
- **Auto fit** (`carl_core/domain/autofit.py`, pure, unit-tested): candidates are ranked stock models (a rank and not abliterated; custom models have no rank yet). Order: the goal's family (`everyday` = `arch: moe`, `hard-code` = `arch: dense`) by rank, then the other family as a fallback. Passes: 2 × 96K, then 1 × 96K, then 1 × the largest window ≥ 32K; the first pass that any candidate of the family meets wins, best rank first. Memory allowed: min(GPU limit, RAM − reserve), the reserve as for the RAM cache (6 GiB, 10 with the VM network, `RESERVE_GB`). The result names every better-ranked candidate that was passed over and why (needs X GiB for 2 × 96K, this Mac allows Y; not downloaded; the other goal's family; header unreadable). If the pick's window is below 96K, an `auto` start uses that window unless `config.json` sets one (`CARL_SOURCES` shows `ctx:auto-fit`). Offline (no header readable): the catalogue `default`, or `default_small` when the default's weights alone don't fit.

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
| 3 | `config.json` | `~/.config/carl/config.json`: the `llama` section, and the profile `models.<name>` |
| 4 | Auto-tune result for this Mac | `~/.config/carl/models.json`: `models.<name>.tune.settings` |
| 5 | Catalogue | `host/catalog.json`: `tune` of the model. For a custom model: values from its GGUF header |
| 6 | Built-in defaults | q4_0, 96K, auto slots, MTP + n-gram n=1, temperature 1.0, … |

- **`config.json` sections:** `llama` (`model`, `net`, `host`, `cache_ram`, `ub`, `batch`, `ckpt`, `ckpt_step`, `think_toggle`, `extra_args`); `models.<name>` (`kv`, `ctx`, `slots`, `spec`, `spec_n`, `temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `alias`); `paths` (`models_dir`).
- **Validation:** each key has a type, a range or a list of choices (`./carl.sh config show` lists them). A bad value stops the start with an error. An unknown key is ignored. CARL writes the file atomically, with mode 600.
- **Migration:** before `config.json`, the dashboard wrote `llama.env`. If `config.json` is missing, CARL converts this file into it one time.
- **Files from before 1.2.0** can hold settings of features that 1.2.0 removed (CHANGELOG.md). They load: CARL ignores them (`./carl.sh config show` warns about a removed section), and they go away the next time CARL saves the file.
- `SETTINGS_FILE=none` skips `config.json`.
- **Custom models:** each `.gguf` in the models folder (`paths.models_dir`, or `MODELS_DIR`) is a model, also if it is not in the catalogue. `./carl.sh download hf:OWNER/REPO/FILE.gguf` resolves the revision, the size and the SHA-256 from the Hugging Face API, records the model in `models.json`, downloads it and verifies it.
- **Custom model cards (`models.json` `models.<name>.card`):** the user's card for a custom model (catalogue models are read-only: CARL refuses to write a card for them, and a card written by hand under a catalogue model's name is ignored). Written by `./carl.sh card NAME set FIELD VALUE` / `unset FIELD` and the dashboard's edit mode (Models panel, `e`); saving one also records the model's `path` and `source`, as Auto-tune does. Example:

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

  Fields: `label` (≤ 60 characters), `role` (≤ 60), `good_for` (the catalogue's tags), `why_use`, `trade_offs`, `hardware`, `uncensored` (text, ≤ 1000 characters each), `abliterated` and `auto_fit` (true / false), `arch` (`dense` \| `moe`), `quant` (≤ 40), `rank` (integer ≥ 1), `thinking` (`on-off` \| `effort`), `pick_instead` (`[{model, when}]`). The rules (`parse_custom_card()` in `carl_core/domain/records.py`) are the catalogue's (`_card()`: the tag set, `uncensored` tag only when abliterated, role length, rank ≥ 1, `pick_instead` models exist) plus: known fields only; one line of printable text (no control characters: the text is drawn in the terminal); the `uncensored` text only when abliterated; `auto_fit` only with `rank` and `arch` and not abliterated. A bad card in the file stops the model list with an error that names the model and the field. When the card is saved, a `pick_instead` model must exist; when it loads, an entry whose model is gone (deleted, renamed) is left out instead.
- **Where a card is read:** `all_models()` joins the card into the custom model's record (`build_models()` in `carl_core/domain/models.py`), so the MODEL card, the role and tags columns, sort by quality (the rank), the filters (tags, stock, dense / MoE) and auto fit see the same fields as a catalogue model's. **Auto fit** takes a custom model only when its card says `auto_fit: true` (a user's rank is not measured: opt-in, never by default); `./carl.sh download default` and the download offer only consider catalogue models.

### Auto-tune

`./carl.sh tune NAME [--quick]` (`tools/carl-tune.py`) measures one model on this Mac. It starts its own server on port 8093, one time for each speculation mode (about 5–10 min in all). It refuses to start if a server runs on 8080, or if another large process is in memory (more than `BIG_GB`, default 8 GB, or a known model server; `ALLOW_SECOND_MODEL=1` skips this check, as for the launch guard).

1. **Memory:** the largest window for each slot that fits the GPU limit, with 1 and 2 slots (the same maths as `./carl.sh fit`).
2. **Speculation:** none, `ngram-mod` (n=2), and, if the GGUF has an MTP head, `draft-mtp` and `draft-mtp,ngram-mod` at n=1 and n=2 (`--quick` skips the MTP modes at n=2; n-gram alone n=2, and n=1 too when the file has no MTP head). Each mode generates prose, new code and a code re-emit, two times. The score is a weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2). A mode with drafting must beat a simpler mode by 3% to win.
3. **Prompt reading:** a cold read at 8K and 32K tokens, and 64K without `--quick`. The time for each token grows about linearly with the depth. From this, Auto-tune calculates the time to read a full window. The context zones of this Mac: a full cold read in ≤3 min = fast, ≤10 min = slow, more = very slow. The fast zone is never smaller than 96K (`ctx_zones()` in `carl_core/domain/models.py`).
4. **Result** (`choose_ctx()` in `carl_core/domain/tuning.py`): kv q4_0, the best speculation, the context, and 2 slots if two windows fit. It is saved in `~/.config/carl/models.json`, with the speed of each mode, the read speeds and the zones.
   - If 96K fits one slot, 96K is the floor. Above it, Auto-tune keeps the catalogue window while a cold read of it is not worse than slow on this Mac and it fits. Otherwise, it uses the largest standard window (96K, 128K, 160K) in the fast zone that fits.
   - If 96K does not fit, it uses the largest standard window (32K, 48K, 64K) that fits.

NOTE: The re-emit workload copies `tools/carl_core/adapters/llama_server.py` back (since 1.1.0). Thus, re-emit scores are not directly comparable with older Auto-tune results.

The dashboard (Settings, Auto-tune panel) stops the running server, runs the tune, and starts the server again.

### Server flags

This table shows the flags that `host/serve-llama.sh` gives to `llama-server`, and the reasons:

| Setting | Value | Why |
|---|---|---|
| Bind | `--host 192.168.42.1 --port 8080 --api-key-file ~/.config/carl/api-key` | An address for the VM only; a shared key |
| Offload | `-ngl 999` | The whole model is on the GPU (Metal). |
| Flash attention | `-fa on` | Necessary for a quantized V cache. It was on for every measurement. |
| KV cache | `-ctk q4_0 -ctv q4_0` (`--kv q8` → q8_0) | q4: +16% prefill and about 2 GB less memory than q8, the same decode speed, 8/8 needle recall at 66K. Do not mix K and V types (prefill is about 5× slower). |
| Context | `-c` = slots × 96K (`--ctx` sets the context window for each slot) | 96K per slot by default, because long context windows read cold prompts and decode much more slowly ([Context length](memory.md#context-length-what-longer-windows-cost)). The model maximum is 262144. The server allocates the whole KV cache at start. |
| Batching | `-b 2048 -ub 512` | `-ub 512` gave the best measured result (90.5 tok/s vs 88.6 / 86.1 for 1024 / 2048). |
| Slots | `--slots auto` (default): 2 if two full context windows fit, else 1 → `--parallel 2 --kv-unified --kv-unified-per-slot CTX -c 2×CTX --no-cache-idle-slots -sps 0.5` | The main OpenCode session and a subagent each keep their own slot and cache (see above). |
| Prompt cache | `--ctx-checkpoints 8 --checkpoint-min-step 4096 --cache-ram N` (N comes from the free RAM, 1–8 GiB) | Checkpoints let follow-up turns use the cache again, because the recurrent layers of Qwen cannot trim it. The RAM cache holds conversations that do not fit in the slots (a third session). Each held state is 2–2.5 GiB. Size = RAM − weights − KV − reserve (10 GiB with the VM network, else 6; `RESERVE_GB`). |
| Templates | `--jinja --reasoning-format deepseek`, `preserve_thinking: true`, `--chat-template-file` (patched) | The server sends the reasoning to `reasoning_content`. `reasoning_effort: none` turns off thinking ([section 4](thinking.md#how-thinking-works)). |
| Sampling | `--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0 --repeat-penalty 1.0` (`TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE`) | The Qwen recommendation for thinking mode ([section 8](sampling.md#sampling-and-output-limits)). |
| Speculation | `--spec-type draft-mtp,ngram-mod --spec-draft-n-max N` (type and N from the tune of each model: N 1 for the dense 27B and the IQ3 builds, 2 for the Q4 35B) | The best of 7 configs that were measured on each model ([section 12](performance.md#performance-measured), [IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)). |
| Logging | `--log-file ~/models/logs/llama-server-<ts>.log --log-timestamps --log-prefix` | `llama-server-latest.log` points to the newest log. Use `tools/llama-log.sh` to read it. |
| Keep awake | `caffeinate -i -w <server pid>` | If the Mac goes to sleep, requests stop in the middle of the prompt. |
| Monitor in the same terminal | `run_server` in `host/common.sh` | One command shows the server live. `MONITOR=0` gives a plain foreground server. |
| Network | `--local` (127.0.0.1, the default), `--vm` (192.168.42.1) | The launcher refuses 0.0.0.0. |
| Memory check | `tools/llama-fit.py --check` before the model loads | The start is refused if weights + KV + buffers are more than the GPU limit; the message shows the largest context window that fits and auto fit's alternative (`FIT_CHECK=0` skips it: the expert override). |
| Launch guard | `guard_other_models` | No start if a second model is in memory (`ALLOW_SECOND_MODEL=1` skips it). |
| Port guard | No start if the port is in use (`netstat`, not `lsof`) | A second model would collide with the running one. Also, `llama-server-latest.log` continues to point at the running server. |
| Metrics | `--metrics` | A Prometheus endpoint at `/metrics`. |

You can override the settings with environment variables (`CTX`, `KV`, `KV_K`/`KV_V`, `UB`, `SPEC`, `SPEC_N`, `MODEL`, `ALIAS`, `HOST`, `PORT`, `LOG_FILE`, `THINK_TOGGLE`, `KEEP_AWAKE`), or in `config.json` ([Settings](#settings-configjson-auto-tune-and-the-catalogue)). Extra arguments go directly to `llama-server` (also `llama.extra_args` in `config.json`). `./carl.sh -h` lists all of them, with the current defaults.

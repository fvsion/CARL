# CARL Reference: The llama.cpp server

[Index](README.md) · the server, its settings, Auto-tune and its flags.

## The server: llama.cpp

| | llama.cpp (`carl start`) |
|---|---|
| Program | `llama-server`: on a Mac from Homebrew `llama.cpp`; on a Windows or Linux PC the build that `carl engine install` fetches ([per OS](#the-server-on-each-os)). CARL is tested with 0.6.0, build 11429 on a Mac (both Macs since 2026-10-09), and 0.7.0, build 11738 on a PC. An older version gives a warning ([The llama.cpp version](#the-llamacpp-version)). The older measurements used 0.4.1 (M3 Pro) and 0.5.0, build 11146 (M2 Max). |
| Port | 9275 (`PORT=N` sets another). CARL's earlier default was 8080: OpenCode and Pi on this Mac that were set up for 8080 show as out of date in the dashboard's Connect tab, and `u` updates them. On other computers, the sync finds the new port by itself (`client/carl-sync.py`: `find_api`, `server_moved`). |
| API | The OpenAI API (`/v1/chat/completions`, `/v1/models`) and the Bearer key `~/.config/carl/api-key` |
| Weights | GGUF files in `~/models/gguf/`, a folder for each model and quantization (`MODEL/QUANT/FILE.gguf`, with `mtp/`, `dflash/`, `dspark/`, `eagle3/`, `vision/`, `template/`, `imatrix/` and `lora/` beside the quantizations): the catalogue models and any other GGUF |
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
| Stop | In the dashboard: `q`, then `s`. Or stop the PID that `netstat -anv -p tcp` shows for :9275 ([Port and PID lookups](#port-and-pid-lookups-no-lsof)). |
| Logs | `~/models/logs/llama-server-*.log` (`carl log`) |
| Health | `/health`, `/props`, `/metrics`, `/slots` |

**Other servers that were examined:**
- MTPLX was examined in 2026-09 and 2026-10, and removed in 1.2.0. On 32–36 GB Macs, it could not hold long sessions. It gave HTTP 507 from about 4K–56K tokens (this depends on the free memory). Version 2.11 had no real quantized KV cache and a 48K limit. llama.cpp holds 2 × 96K slots. The last commit with MTPLX support is 470c316.
- NOTE: oMLX (a different MLX server) was examined on paper only. It was not tested on this Mac.

---

## llama.cpp details

### The start

- `carl start` runs the start in Python (`src/carl/application/start_server.py`). Before 1.21.0, a bash launcher ran it.
- `carl` with no arguments opens the dashboard. If a llama.cpp server runs on port 9275, the dashboard attaches to it. Another program on the port is not a server: the start goes on (below).
- If no server runs, `carl` starts llama.cpp with the saved settings.
- If no model is downloaded, `carl` first offers to download Auto fit's choice for this Mac (`carl fit`: the whole catalogue, the everyday goal).
- If you answer no, or no model fits, the dashboard opens without a server.
- `carl -h` shows the help. `carl help COMMAND`, `carl COMMAND --help` and `carl COMMAND -h` show the help of one command (`src/carl/cli/help_topics.py`; it wraps to the width of the terminal). [Server flags](#server-flags) lists the flags of the start.

**The start lines.** Before the model loads, the launcher writes one sentence for each setting, with its unit and its source:

| Line | Example |
|---|---|
| The model | `CARL starts the server: NAME (FILE).` |
| An older llama.cpp (only then) | `llama.cpp 0.4.1 (build 9001) is older than the version that CARL is tested with (0.6.0, build 11429): update it with brew upgrade llama.cpp.` |
| The address | `Address: http://127.0.0.1:9275. Only this Mac can use the server.` |
| Slots and context | `Slots: 2 (from Auto-tune). Context: 96K tokens per slot (from Auto-tune).` With `--slots auto`: `Slots: 2 (auto: two slots fit).` |
| Context memory and RAM cache | `Context memory type: q4 (from Auto-tune). RAM cache: 8.0 GiB.` |
| Speculation | `Speculation: MTP + n-gram, 2 guesses (from Auto-tune).` With a drafter: `The MTP drafter is FILE.` |
| Sliding window (Gemma) | `Sliding window: full cache. CARL can restore saved sessions and prompts (cache.swa = auto).` or `Sliding window: window cache. It uses less memory, but CARL cannot restore saved sessions and prompts (cache.swa = auto).` |
| The log | `Log file: PATH. Batch: -ub 512. Your settings: carl config show.` |

- The sources (`CARL_SOURCES`) are: `from your option` (a flag), `from the environment`, `from your settings` (`config.json`), `from Auto-tune`, `from the catalogue`, `from the model file`, `from Auto fit: the largest context that fits`, and `CARL's default`.
- Each refusal starts with `error:`. The dashboard shows these lines when a start fails.

**The port at start** (`src/carl/application/start_server.py`; the dashboard: `src/carl/dashboard/actions/settings/`, `src/carl/dashboard/render/settings/server.py`). When another program holds the server's port, the start offers the next free port. It never changes the port without a question: other computers reach the server by its port.

| Where | What occurs |
|---|---|
| A terminal | `Port 9275 is in use by another program (ssh, process 4242). Start the server on port 9276? [Y/n]`. `n` stops the start. |
| No terminal | An `error:` line that names the free port: `PORT=9276 carl start starts the server there.` |
| The dashboard (Start, Apply) | `Port 9275 is in use by another program (ssh, pid 4242). This starts the server on port 9277.` and **[ Start on port 9277 (y) ]** |

- A llama-server on the port is still a server that runs already: `error: a server runs on port 9275 already (process PID).` One model at a time.
- The next free port is the first port after the server's port that nothing listens on (at most 100 ports after it).

**The server file** (`src/carl/domain/server/runtime_doc.py`). At each start, the launcher writes `~/.config/carl/server-PORT.json` (mode 0600). It says each model's cache: `swa` (`full`, `window` or `none`), `kv`, `ctx` and `slots`, and the mode (`single` or `router`; router mode writes it from its presets). The dashboard adds the dashboard API's real port (`api_port`). `carl-cache` reads it ([Caching](caching.md#sliding-window-models)). A client on another computer gets the same facts from the dashboard API (`GET /carl/cache/settings?port=N`).

### The server on each OS

The start, the settings and the commands are the same on every OS. What differs is below. [Windows and Linux](windows-linux.md) gives the setup of a PC.

| | macOS | Windows | Linux |
|---|---|---|---|
| llama.cpp | Homebrew (`brew upgrade llama.cpp` updates it) | The pinned build: `carl engine install` downloads it, checks the SHA-256, unpacks it into `~/.config/carl/llama.cpp` and sets `llama.path`. `CARL_LLAMA_DIR` or `llama.path` name a build of your own. | The same as Windows |
| Backend | Metal | CUDA 13 or 12 (NVIDIA), CPU | The same as Windows |
| The memory a model may use | The GPU memory limit, or the RAM less the memory kept free | The free VRAM of each GPU less the VRAM kept free, added up; a GPU that shares the RAM: its carve-out and the shared memory | The same as Windows |
| Several GPUs | One | The layers are split over them (`-dev`, `-ts`); `gpu.devices` chooses | The same as Windows |
| MoE experts | On the GPU | In the RAM when the model does not fit (`--n-cpu-moe`, `llama.n_cpu_moe`) | The same as Windows |
| Default `-ub` | 512 | 2048, then Auto-tune's | 2048, then Auto-tune's |
| Load mode | `--load-mode none` when the free RAM allows it | The same | The same |
| Keep awake | `caffeinate -i` (`KEEP_AWAKE=0` turns it off) | `SetThreadExecutionState`, held while the server runs | `systemd-inhibit`, else a note |
| Port and PID lookup | `netstat -anv -p tcp` | The TCP table of Windows (`GetExtendedTcpTable`) | `ss`, else `/proc/net/tcp` |
| Stop | Process group; `q`, `s` in the dashboard | The console stop (Ctrl-C reaches the server); `q`, `s` in the dashboard | Process group; `q`, `s` in the dashboard |
| The VM network (`--vm`) | VMware Fusion | Not used | Not used |
| The words | "this Mac", "macOS and apps" | "this computer", "Windows and apps" | "this computer", "the system and apps" |
| Settings and models | `~/.config/carl`, `~/models/gguf` | `%USERPROFILE%\.config\carl`, `%USERPROFILE%\models\gguf` | `~/.config/carl`, `~/models/gguf` |
| Tested on | The M3 Pro and the M2 Max | An RTX 2070 SUPER (8 GB) | The same card in WSL (Ubuntu 24.04) |

### Engines

An **engine** is the program that runs the models. llama.cpp is the only engine today. The card of a model has the field `engine` (default `llama.cpp`), and the setting `engine.name` (`auto`, or an engine name; `CARL_ENGINE` wins) overrides it. A second engine will be a new adapter, with no new command word. `carl engine version` shows the engine's version, and `carl engine help` every flag of `llama-server`. `carl engine install` (Windows and Linux) installs the llama.cpp build. The `llama.*` settings configure the llama.cpp engine.

### The llama.cpp version

CARL keeps two tested versions of llama.cpp in `src/carl/domain/llama/version.py`: `TESTED`, the Homebrew build of a Mac (now 0.6.0, build 11429), and `PINNED`, the release that `carl engine install` installs on Windows and Linux (now 0.7.0, build 11738; its files are in `src/carl/data/llamacpp-release.json`). A computer's llama.cpp is checked against the version of its OS. Reason: the M3 Pro ran 0.4.1 for weeks, and nothing showed it.
- **How CARL reads it:** `llama-server --version` (`src/carl/adapters/llama/version_probe.py`). The launcher reads it at each start (`carl engine version`). The dashboard reads it one time when it opens.
- **The format:** `version: 0.6.0 (build 11429, commit d81235049)`. Builds before 0.x write `version: 6789 (bc091a4d)`: there, the number is the build number.
- **The comparison:** by the build number when both versions have one, else by the dotted version (0.6 = 0.6.0; 0.10 is newer than 0.9).
- **Older than the tested version:** one start line (the table above), and on the HEALTH card of the dashboard a warning row (**Version**) and a **To update** row (`Run brew upgrade llama.cpp, then restart the server.`; off the Mac: `carl engine install`), at each detail level. The collapsed HEALTH card shows `old llama.cpp` when there is no other problem. The server starts.
- **The same or newer:** no start line. At full detail, the HEALTH card shows the version in a quiet row (for example `llama.cpp 0.7.0 (build 12001) is newer than the tested version, 0.6.0 (build 11429).`).
- **Not known** (no `llama-server` on the `PATH`, or a format that CARL cannot read): nothing.

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

**Launch guard** (`src/carl/domain/hardware/processes.py`). The start and Auto-tune refuse to start when:
- a process holds more than 8 GiB of resident memory (`BIG_GB`, default 8), or
- a process has a known model-server name (`llama-server`, `mtplx`, `ollama`, `LM Studio`).

- **Reason:** two models do not fit. A name check alone does not find servers that started in a different way. On 2026-10-02, a second model crashed the Mac (it restarted).
- `ALLOW_SECOND_MODEL=1` skips the check. Use it only when the large process is not a model.

### Sleep, model names, templates

- **Sleep stops requests.** The launchers keep `caffeinate -i` active while the server runs. `KEEP_AWAKE=0` turns this off. If you close the lid on battery power, the Mac still goes to sleep.
  - **Evidence:** before this change, idle sleep (1 min on battery) stopped a 74K prompt for 30+ min.
- **The server ignores the model name in a request** (single model). The GGUF that is loaded answers all requests. Thus, the model in the client must match the server. The `carl-model-check` plugin of OpenCode and extension of Pi warn when they are different.
- **Served names (since 1.3.0):** each model is served under its CARL name (`--alias` = the catalogue or custom name). Before 1.3.0, builds shared a family alias such as `qwen3.8-27b`, and OpenCode could show the wrong build.
- **The client lists (since 1.3.0):** the client configs list only the installed models, one entry each (`carl client-models`). Since Phase 23.4.4, single-model mode lists only the model that the server runs, and router mode all the installed models ([the model list](client-configs.md#the-model-list-single-model-mode-and-router-mode-phase-2344)). Each entry has the id, the label and the context for each slot (from the effective settings). It also has the thinking type from the card: `on-off` or `effort`. The default model of the clients is the model that the server runs now (a single model), else the model that a start loads ([OpenCode and Pi configs](client-configs.md#the-provider-and-the-models)).
- The dashboard compares the models in the configs on this Mac (`opencode.json`, `models.json`, CARL's provider) with the downloaded models. The Connect tab shows a warning when they are different.
- **Template override.** The server uses `--chat-template-file <model>.thinking-toggle.jinja` from the `template/` folder of the model's own folder (`~/models/templates/` for a model without one). The launcher makes this file at start ([How thinking works](thinking.md#how-thinking-works)). `THINK_TOGGLE=0` uses the template of the GGUF.

### Speculation for each model

The catalogue (`tune.spec`, `tune.spec_n`) or the Auto-tune result sets the speculation:

| Model | Speculation | Guesses |
|---|---|---|
| 27B dense (Q4, Q3) | MTP + n-gram | 1 |
| 35B MoE (Q4, stock and Heretic) | MTP + n-gram | 2 |
| Stock IQ3 builds (35B, 27B) | MTP + n-gram | 1 |
| 9B Distill | MTP + n-gram | 1 |
| Heretic 35B IQ3 (no MTP head) | n-gram only | – |
| orcarouter 27B IQ3 | n-gram only (a tie with MTP + n-gram) | – |
| Gemma 4 E4B, 12B, 26B-A4B, 31B (MTP drafter) | MTP + n-gram | 2 (measured: [Gemma 4](models.md#gemma-4)) |

- More guesses help a MoE: with 3B active parameters, it costs little to verify more tokens.
- 2 guesses lose on IQ quants ([IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)).
- A custom model gets MTP + n-gram with 1 guess when its GGUF has an MTP head, MTP + n-gram with 2 guesses when it has an MTP drafter (Gemma 4), else n-gram.
- **The guesses are MTP's.** llama.cpp's n-gram (`ngram-mod`) does not read `--spec-draft-n-max`: it drafts a run that it has seen in the context. CARL passes `--spec-draft-n-max` only with MTP (Phase 23.5.1.8; before 1.20.5 an n-gram's "guesses" changed nothing).
- **n-gram's own settings** (`--spec-ngram-mod-n-match 32 --spec-ngram-mod-n-min 16 --spec-ngram-mod-n-max 96`, every model; `NGRAM_MATCH`, `NGRAM_MIN`, `NGRAM_MAX` in the environment): it drafts when the last 32 tokens were seen before, a run of 16 to 96 tokens. llama.cpp's own are 24, 48 and 64. Measured 2026-10-12 (M3 Pro, llama.cpp 0.6.0; prose, new code, re-emitting a file and an agent-style edit, at a short prompt and a 25K-token one):

  | Model | n-gram setting | Re-emit (× no speculation) | Agent-style edit | Prose, new code |
  |---|---|---|---|---|
  | Qwen3.6-35B-A3B IQ3 | llama.cpp's 24 / 48 / 64 | 4.1 / 3.9 | 2.3 / 2.3 | 0.99–1.00 |
  | | **CARL's 32 / 16 / 96** | **5.4 / 5.4** | 2.9 / 2.6 | 0.99–1.00 |
  | Qwen3.8-27B IQ3 | llama.cpp's 24 / 48 / 64 | 4.1 / 3.9 | 2.4 / 2.7 | 0.98–0.99 |
  | | **CARL's 32 / 16 / 96** | **5.1 / 5.3** | 2.7 / 2.7 | 0.98–0.99 |

  The shortest draft matters most (16 instead of 48: n-gram drafts more often); a 32-token match makes fewer wrong drafts; 96 tokens accepts longer runs (128 adds nothing). n-gram does not change the speed of new text.
- **A separate MTP drafter (Gemma 4).** The model file has no MTP head. The catalogue entry names a drafter file (`drafts`, kind `mtp-file`). `carl launch-env` gives `MTP_SOURCE=drafter` and `DRAFT=<the drafter's path>` when the drafter is downloaded. The launcher adds `-md "$DRAFT"` when `SPEC` contains `draft-mtp`, also when `SPEC` comes from the environment. The drafter uses the context memory of the model, so only its weights add memory: `carl fit --plan` and `--check` get `--draft "$DRAFT"` and count them.
- **MTP with experts in the RAM (decision A51).** When any expert layer of a MoE model is in the RAM (`--n-cpu-moe`), a start runs n-gram and leaves MTP off, and the Speculation line says why: MTP did not pay on a card that keeps experts in the RAM (the same write speed within the noise, prompts 5% to 6% slower), and its draft context takes GPU memory. A speculation that you set (`config.json`, the environment) or that Auto-tune chose for the model stays: `carl tune` measures the best MTP mode against n-gram at the setup it chooses and keeps MTP only if it wins by 3%. A model whole on the GPUs keeps Auto fit's order (MTP goes before a slot). Without MTP, llama.cpp does not load the MTP layer, and the estimate does not count it (decision A52; Windows and Linux).
- **Threads with experts in the RAM (decision A54).** The CPU runs the experts that are in the RAM, and one thread on each physical core, pinned, runs them faster: on a Ryzen 9 3900X (12 cores, 2 threads each) `--threads 12 --cpu-mask 0x555555` wrote 30.3 tok/s against 26.5 for the A3B IQ3 (+14%) and 8.5 against 7.0 for Flash-Next (+12%), and lost nothing for the A3B Q4_K_M and Gemma 4 26B-A4B Q4_0. The speed is flat from 8 to 16 threads and falls at 24 (two threads on a core); `--cpu-strict`, the thread priority and polling gained nothing, so the start passes none of them. When the memory plan keeps any expert layer in the RAM (`--n-cpu-moe N`), the start passes `--threads N --cpu-mask M` after it: N is the number of physical cores and M has the first logical processor of each core (the 3900X on Windows, and in WSL on it, numbers a core's two threads side by side, so `0x555555`; a Linux host that numbers the second threads after the first ones gets `0xfff` for the same CPU). The CPU's cores come from the `CpuLayout` port (Windows: GetLogicalProcessorInformationEx; Linux: `/sys/devices/system/cpu/*/topology`, only the processors the process may run on). On a CPU with fast and slow cores (an Intel hybrid) only the fast cores carry threads; that is not measured yet. A thread or CPU-mask option in the server options (`-t`, `--threads`, `-C`, `--cpu-mask`, `-tb`, `-Cr`, `-Cb`) wins as typed, and the start passes none of its own. A start with every expert on the GPUs, a Mac and a dense model whole on the card are unchanged. A start pins only when it was measured (decision A57, 2026-11-01): `carl tune` measures pinned against unpinned at the setup it chooses and the record keeps pinned only when it writes 3% faster; a model with no record, or a record that says unpinned, starts with llama.cpp's own threads (the start line says so). A record that says pinned gives the start `THREAD_PIN=on`; the environment's `THREAD_PIN=on` or `off` wins over the record. Pinned is not always faster: it needs the cores it pins to be quiet. Measured again for this decision (WTHR, 2026-10-29, the Windows test PC, with other work on the machine: about 11% busy, 40% to 60% on four logical processors), `llama-bench` on the A3B IQ3 wrote 24.2 tok/s with 12 threads pinned on the cores' first logical processors against 25.5 unpinned, and 27.5 with 9 threads pinned on the nine cores that were quiet (against 23.9 for 9 unpinned); the real start through Auto-tune's measure wrote 34.0 pinned against 33.9 unpinned (weighted tok/s). One busy pinned core holds the other threads back at each layer. This is why `carl tune` measures it and the start follows the record: the +14% needs quiet cores, and on this busy computer pinning was neutral to 7% slower. The start line says `Threads: N, one per core (pinned)` for a pinned start and `Threads: llama.cpp's own, not pinned (...)` with the reason otherwise; the MODEL card shows `threads: N, one per core (pinned)` for a pinned start only. Router mode follows the same rule for each model with experts in the RAM (`threads` and `cpu-mask` in its section, for a model whose record says pinned or when `THREAD_PIN=on`).
- **No MTP available.** `MTP_SOURCE=none` means that the file has no MTP head and that no drafter is downloaded. If the speculation uses MTP, the start uses `ngram-mod` instead and tells you one time (`launch-env` for a configured `SPEC`, the launcher for a `SPEC` from the environment).
- `DRAFT` and `MTP_SOURCE` always come from `carl`. The launcher ignores them in the environment.

### The dashboard and the launch model

- The dashboard is `carl dashboard` (`src/carl/dashboard/`, since 2026-10-01). It has the tabs Live, Connect, Requests, Log and Settings ([The dashboard](architecture.md#the-dashboard)). Each section of a screen has its own level: collapsed, simple or full (Tab selects a section, `L` changes its level, `D` sets every section).
- Use only the left click. The dashboard does not use the right click, because terminals such as iTerm2 show their own context menu.
- A config that you copy from the Connect tab contains the API key. On the screen, the key stays masked until you show it.

When you start from a terminal, the start (`PosixServerRunner.start` in `src/carl/adapters/posix/server_runner.py`) does these steps:
1. It starts the server in the background, deaf to a closed terminal (SIGHUP ignored, like `nohup`) and in a process group of its own. Thus Ctrl-C in the dashboard cannot reach it.
2. It starts `caffeinate -i -w <server pid>`.
3. It uses `exec` to change into the dashboard.

- **Result:** the server is a child process of the dashboard, so the dashboard must reap it. Otherwise, a stopped or crashed server stays as a zombie process, and `ps` still lists it. The `pid_alive()` function of the dashboard calls `waitpid(WNOHANG)` and treats state `Z` as dead.
- Without a terminal (scripts, `nohup`, `MONITOR=0`), the launcher starts the server with `exec` in the foreground. The benchmark tools need this.

### The API key and the settings folder

**API key** (`KeyFile.ensure`, `src/carl/adapters/common/key_store.py`):
- The server and the clients use `~/.config/carl/api-key` (mode 600, in a folder with mode 700). `API_KEY_FILE` sets a different path.
- If the file is missing, the first start copies the key from an earlier path: `~/.config/llm-deploy/api-key`, then `~/.mtplx/api-key` (before 1.2.0). The key stays the same, so the clients continue to work.
- If there is no earlier key, the first start makes a random 40-character key.
- The dashboard, Auto-tune and the bench tools read the new path. While the new file does not exist, they read the old paths.

**Settings folder, formerly `~/.config/llm-deploy`** (CARL was called LLM-Deploy; `FolderMigration`, `src/carl/adapters/macos/llm_deploy_migration.py`):
- The launch commands of `carl` move the folder one time to `~/.config/carl` (folder 0700, files 0600). Help and unknown commands do not move it.
- A symlink `~/.config/llm-deploy -> carl` stays. Client configs from before the rename read the key there.
- If both folders exist, CARL uses `~/.config/carl` and does not change the old folder.
- Until the move, the readers use the old folder. The key order is `API_KEY_FILE` > `~/.config/carl/api-key` > `~/.config/llm-deploy/api-key` > `~/.mtplx/api-key`.
- `CARL_CONF_DIR` turns the move off.
- **`CARL_CONF_DIR`** sets a different settings folder. CARL's commands (`src/carl/domain/config/paths.py`) and the start both use it: `config.json`, `models.json`, the disk cache (`slots/`) and `router-presets.ini` go there. The API key (`api-key`) goes there too, and CARL does not copy a key of an earlier place into it (`API_KEY_FILE` still wins). Before this change, the launchers always used `~/.config/carl`, so a test run with a different folder wrote the real one.
- On the client side, the setup moves the same folder. `configure.py` (`OLD_NAMES`) renames CARL's own pieces:

| Before | Now |
|---|---|
| `llm-deploy.json` (state file) | `carl.json` |
| `~/.config/opencode/llm-deploy/` | `~/.config/opencode/carl/` |
| Provider `llm-deploy` | `carl` |
| The `llm-deploy:delegation` block in `APPEND_SYSTEM.md` | `carl:delegation` |
| The `LLM-Deploy:` marker of the Pi extension | `CARL:` |

- A provider block that you paste from the dashboard uses the id `carl`. The plugin packages are `carl-subagents-sidebar` and `carl-session-switcher` (command `carl.session.switch`).

### Router mode

Router mode is opt-in: `llama.mode = router`, `LLAMA_MODE=router` or `--router` (`src/carl/domain/server/router_presets.py`, the router branch of `StartServer` in `src/carl/application/start_server.py`).
- The router of llama.cpp 0.5.0 (`llama-server --models-preset FILE --models-max 1`, no `-m`) starts one child `llama-server` for the loaded model on a free port. It sends each request to the child by the `model` field.
- The router gets `--port`. Each child gets the keys of the presets file: `fit = off` and CARL's other shared flags in `[*]`, and the settings of its model in its own section. The section has the model's own `-ub` (`ubatch-size`: the same order as a single start, below; a `-ub` in the server options is passed on to the server; router mode cannot ask about a config `-ub` that differs from the tuned one, so it uses the tuned one and says so in the model's line) and, when the free memory holds the model and the load reserve, `load-mode = none`, as a single start of it would have.
- The presets file is `~/.config/carl/router-presets.ini`. The launcher writes it again at each router start.
- **Paths in the file** (measured on Windows, 2026-10-27): llama.cpp's parser takes a value as it stands, so backslashes (`C:\Users\...`), spaces and letters outside ASCII work. A `;` or `#` starts a comment and cuts the path there, and quotes are part of the value. On Windows CARL gives a path with a `;` or `#` its 8.3 short name; a model whose path cannot be written is left out with the reason.
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

The network modes are in `src/carl/domain/server/network.py`:

| Mode | Address | Who can connect |
|---|---|---|
| `--local` (default since 1.3.0) | 127.0.0.1 | Only this Mac |
| `--vm` | 192.168.42.1 (`VM_HOST`) | The VMware Fusion VM and this Mac |
| `--host ADDR` or `HOST=ADDR` | ADDR | Every computer that can reach ADDR |

- `--local` is the default also with no flag, `NET` or `llama.net`. When the VMware network is up, the banner tells you that `--vm` serves a VM client.
- `--vm` uses the VMware Fusion NAT network (vmnet8). The Mac is `192.168.42.1` on `bridge101`. The Kali VM is `192.168.42.130`. The mode fails if no interface has this address.
- Before 1.3.0, the default was auto: the VM address when vmnet8 was up. Thus, a VMware user exposed the server to the VM network without a question. `NET=auto` now means local (the banner tells you). `migrate_config()` (`src/carl/domain/config/settings.py`) removes a saved `llama.net = auto` one time, with a note on stderr.
- `--host ADDR` has priority over the modes. ADDR must be on an interface of this Mac: for example its LAN address, or the address on a Parallels network (often `10.211.55.2`).
- The settings file can hold an address: `llama.host` in `config.json`. The Settings tab writes it when you select an address in the network row. The order of priority is: flag, environment, settings file.
- The banner shows the mode. For an address that is not 127.0.0.1 or the VM address, it shows a CAUTION. Every computer that can reach the address can use the server. Only the API key protects it.
- After a restart from the Settings tab, the health check also tries the selected address. Thus, a server on the LAN address does not show as a failed start.
- CAUTION: The launcher refuses 0.0.0.0 and `::`, also through `HOST=0.0.0.0`. The macOS firewall is off on this Mac, so a wildcard address would make the model available to the LAN.

**The address and the key of the setup** (`client/setup`, which runs `client/carl_setup.py`; `carl install` on the server Mac, `./setup` in the client package on other computers):
- With no address argument, the setup reads `remote.json` in the client folder. The client package has it ([The client package](client-sync.md#the-client-package-carries-the-connection)). Without it, the default is `--local` on macOS and `--vm` on Linux.
- `--local` sets the clients to the address on which the server actually listens. `--host ADDR` sets any address. `--port N` sets the llama.cpp port (default 9275).

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
- If the stored key is different, the setup keeps a backup of the old key (`api-key.bak`, replaced at the next change).
- A new run changes old configs to the new path. It does not delete the old client file, because a provider of your own can use it. Delete it when nothing uses it.
- `--key` leaves the key in the shell history.
- The smoke test gives the key to `curl` from a file descriptor (`curl -H @<(…)`). Thus, the key does not show in `ps`.

### Checks before a start

| Check | What it does |
|---|---|
| Dependency check (`ensure_deps`) | Finds `llama-server`, `aria2c`, `ansifilter` and `zstd`. In a terminal, it offers `brew install` for the missing tools. Without a terminal, it shows the command. Only `llama-server` is necessary. `SKIP_DEPS=1` skips the check. |
| Port guard | No start if a process listens on the port (`port_pid`). The check runs before the launcher changes `llama-server-latest.log` or loads a model. |
| Launch guard | No start if a second model is in memory (see above) |
| Memory check | `carl fit --check` (see below) |

- **Port guard history:** before this guard, a second bash `serve.sh` (10:08 on 2026-10-01) failed to bind. But it had already pointed the `latest` symlink to its own 4-line failure log.
- **Memory check:** `check_start()` in `src/carl/domain/memory/fit.py` compares weights + context memory + buffers with the GPU memory limit ([GPU memory limit](memory.md#gpu-memory-limit-and-what-fits)). The dashboard uses the same function.
  - Over the limit, the start is **refused** (exit 3, then the launcher exits 1). Such a start does not load, or it makes the Mac swap and become very slow.
  - The first line is one `error:` sentence with the memory needed and the limit: `error: NAME does not fit this Mac with 4 slots × 256K tokens (q8): it needs 24.97 GiB, and the GPU memory limit is 24.96 GiB. …`. Two decimals show when the two values round to the same number.
  - It gives the largest context that fits, with these slots and with 1 slot.
  - It gives Auto fit's choice: the best downloaded model, and a better catalogue model to download.
  - `FIT_CHECK=0` is the expert override. A check that cannot run (an unreadable header) only shows a warning.
- **`/metrics` gauges reset at each read** (`--metrics` turns on this Prometheus endpoint). `prompt_tokens_seconds` and `predicted_tokens_seconds` cover only the time since the last read. Thus, calculate averages from the `*_total` counters. The dashboard does this.
- **The server does not log its context memory (KV cache) size** at the default log level. Thus, the dashboard calculates it ([Context memory](memory.md#context-memory-kv-cache-and-recurrent-state)).

### Auto fit and the start model

- With `llama.model = auto`, the launch plan (`resolve_launch`) starts Auto fit's choice (`llama.auto_goal`, `llama.auto_fit`).
- If the choice is not downloaded, it starts the best downloaded stock model that fits, and tells you. It never selects an abliterated model.

**Auto fit** (`src/carl/domain/memory/autofit.py`, pure, with unit tests):
- The candidates are the ranked stock models: a rank, and not abliterated. A custom model is a candidate only when its card says `auto_fit: true`.
- The goal selects the family first: `everyday` = the fast builds (`arch: moe`), `hard-code` = `arch: dense`. The other builds are the fallback. `hard-code` also falls back to the MoE builds when the only dense builds that fit rank below the best MoE build that fits (`AutoFit.outranked`; W1.6: on the Windows test PC the A3B Q4 with its experts in the RAM, not the Qwen 9B at 1 × 64K).
- A dense build with `fast: true` (the Gemma 4 E4B) is the last resort (W1.6, the user: "Install E4B, you over-specified what i meant"). The best one that fits is a candidate only when no other model of its rank or better fits any pass; then it is the pick like any other (the first run offers its download, a start runs it). Otherwise it is the fast option (`AutoFit.fast`): the reason names it with its setup and the command to choose it.
- The passes, in this order, on every Mac: 2 × 96K, 2 × 64K, 2 × 48K, then 1 × 96K, 1 × 64K, 1 × 48K. On a PC with an NVIDIA GPU they start at 128K: 2 × 128K, 2 × 96K, 2 × 64K, 2 × 48K, then 1 × 128K, 1 × 96K, 1 × 64K, 1 × 48K (`Budget.tiers`, decision A53). Nothing below 48K. A model with a smaller catalogue window (`saluki-27b`, 48K) tries each pass at its own window. The first pass that a candidate of the family meets wins, best rank first ([Auto fit's order](memory.md#auto-fits-order)).
- On a PC with discrete GPUs, each candidate of a pass is tried whole on the GPUs and else with some of its experts in the RAM (`autofit.setup_of`), so the pass comes before the rank (W1.6). An experts-in-RAM setup counts only when the model writes at least 15 tok/s and reads at least 300 tok/s there (`RamSpeed`: this computer's Auto-tune record, else the catalogue's `ram_speed`; the Windows test PC measured the A3B Q4 at 21 and 530). n-gram stays on. A model below the floor, or with no figure, says so in its reason. A named model (`model_plan`) has no floor.
- In each pass, a model with MTP that fits only without it runs n-gram only (`Plan.dropped`, `Plan.spec_note()`), before Auto fit takes the next pass. The memory counts MTP's draft context ([The estimate](memory.md#the-estimate-what-is-counted-and-the-measured-check)).
- The memory that a model can use is min(GPU memory limit, RAM − the memory kept free). The memory kept free is as for the RAM cache: 6 GiB, 10 GiB with the VM network, or `RESERVE_GB`.
- The result gives each candidate with a better quality rank that was not selected, and why (`Rejection.line()`). Examples: `qwen3.8-27b (quality rank 1) is dense: Auto fit keeps it for the hard code goal`, and for a model that fits no pass, a reason that starts with `does not fit:`.
- `Plan.label()` gives the plan in short (`2 × 96K tokens, q4`), `Plan.describe()` in words (`2 slots × 96K tokens (q4)`).
- Every start whose context is not set in `config.json` or by Auto-tune (`app.apply_order`) takes the same order for its model: never above the model's own context, MTP dropped before a slot or the context. A smaller context shows `ctx:auto-fit` in `CARL_SOURCES`, n-gram for MTP `spec:auto-fit`, and a start line says why. The launcher's `carl fit --plan` (`SLOTS CACHE_MIB SWA SPEC`) then takes 2 slots when they fit the same memory, and drops MTP before the second slot also for a context from your option or settings.
- Offline (no header can be read), the download offer and `carl download default` use the catalogue `default`. If the weights of the default alone do not fit, they use `default_small`.

### Port and PID lookups (no lsof)

CARL does not use `lsof` to find the server. `lsof` examines each mounted file system. On a stale network share (for example a disconnected Time Machine SMB volume), it stops in the kernel, and no signal can stop it. Before 1.1.0, this stopped the dashboard and `carl` on a Mac with such a volume. The one exception: on a Mac where `netstat` prints nothing at all (seen under a Homebrew Python on macOS 27), CARL reads the listening sockets from `lsof -nP -b -w -iTCP`. The `-b` flag keeps `lsof` from touching the file systems, and every `lsof` call has a time limit of 5 seconds. CARL does not wait for an `lsof` that ignores the kill. When it does not answer, the sockets are unreadable: the lookup says it cannot tell (no listeners, no owner), never that the port is free, and a start still binds the port to check it.

The start (`src/carl/adapters/macos/bsd_netstat.py`), the dashboard, the client setup and the test tools use `netstat -anv -p tcp`. Its `process:pid` column gives the PID of the process that listens on a port. To do the same by hand:

```bash
netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]9275$/ {n=split($(NF-8),a,":"); print a[n]; exit}'
```

`$(NF-8)` counts from the end of the line, because a process name can contain spaces.

### Settings: config.json, Auto-tune and the catalogue

`carl launch-env` gives each llama.cpp start its model and settings. Each value comes from the first source that has it:

| Order | Source | Where |
|---|---|---|
| 1 | Command-line flags | `--model`, `--ctx`, `--kv`, `--slots`, … |
| 2 | Environment variables | `CTX`, `KV`, `SPEC`, `SPEC_N`, `TEMP`, … |
| 3 | `config.json` | `~/.config/carl/config.json`: the `llama` section, and the profile `models.<name>` |
| 4 | Auto-tune result for this Mac | `~/.config/carl/models.json`: `models.<name>.tune.settings` |
| 5 | Catalogue | `src/carl/data/catalog.json`: the `tune` of the model. For a custom model: values from its GGUF header. |
| 6 | Built-in defaults | q4, 96K, auto slots, MTP + n-gram with 1 guess, temperature 1.0, … |

**`config.json` sections:**

| Section | Keys |
|---|---|
| `llama` | `model`, `auto_goal`, `auto_fit`, `mode`, `net`, `host`, `cache_ram`, `api_port`, `ub`, `batch`, `ckpt`, `ckpt_step`, `think_toggle`, `extra_args` |
| `models.<name>` | `kv`, `ctx`, `slots`, `spec`, `spec_n`, `temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `alias`, `thinking_main`, `thinking_coder` |
| `paths` | `models_dir` |
| `cache` | `disk_gb`, `prefix`, `sessions`, `save`, `auto_s`, `share`, `swa`, `move` ([Caching](caching.md#the-cache-settings)) |
| `delegation` | `gate` ([The new-file gate](delegation.md#the-new-file-gate-advanced)) |

- **Validation:** each key has a type, a range or a list of choices. `carl config show` lists them. A bad value stops the start with an error. An unknown key gets a warning and has no effect.
- CARL writes the file atomically, with mode 600.
- **Migration:** before `config.json`, the dashboard wrote `llama.env`. If `config.json` is missing, CARL converts this file one time.
- **Files from before 1.2.0** can hold settings of removed features (CHANGELOG.md). The file loads. CARL ignores these settings, and `carl config show` warns about a removed section. They go away the next time that CARL saves the file.
- `SETTINGS_FILE=none` skips `config.json`.
- **One entry per model (decision A68, `domain/catalog/quants.py`).** The entries of the list are one file each (a download, a tune and a start act on one), and the entries that share a model folder are one model, ordered the largest first; its name is the name of its first entry. The lists a person reads show the model once with a quantization selector (`carl models`; the dashboard's Models panel, drop-down, Server list and MODEL card, which show the entry of the shown quantization with the model's name in `group` and every quantization in `quants`). `carl start --model NAME [--quant Q]` (`NAME:Q` inside) resolves the quantization in `select_named`: a quantization of the model by its name, with or without `UD-`/`i1-`, or a unique start of one; without it the default (`default_quant`): the entry named in `llama.model`, else the one with the latest Auto-tune record, else the largest downloaded one that fits the context ladder (`FitService.fits_ladder`), else the largest. The name of an entry names that entry. `carl download` and `find` take NAME:Q too.
- **Custom models:** each `.gguf` in the models folder (`paths.models_dir`, or `MODELS_DIR`) is a model, also when it is not in the catalogue.
- **The layout of the models folder (decisions A65, A66).** The models root (`~/models`, on Windows `%USERPROFILE%\models`) holds `gguf/` (the models folder, default) and `mlx/` (only named: this version runs no MLX model, so CARL makes nothing there). Each model has a folder of its own in the models folder and a folder for each quantization in it: `gguf/MODEL/QUANT/FILE.gguf`. MODEL and QUANT come from the file name without `.gguf` (a split file: without the part number), split at its quantization: `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf` is `Qwen3.6-35B-A3B/UD-IQ3_XXS/`, `gpt-oss-20b-MXFP4.gguf` is `gpt-oss-20b/MXFP4/`; a name that ends in no quantization is its own model with the quantization `default`. Beside the quantizations, the model has a folder for each kind of drafter (A76: `mtp/` for the MTP drafters only, `mtp-*.gguf`; `dflash/`; `dspark/` for DeepSeek's DSpark; `eagle3/`), `vision/` (the vision projector), `imatrix/` (an importance matrix), `template/` (the thinking toggle's chat template; a model not in a folder of its own keeps it in `~/models/templates`) and `lora/` (adapters, later). `src/carl/domain/catalog/layout.py` is the one place that knows this: a download goes into the quantization folder (`folder_path`), the drafter into `mtp/` (`locate_beside`); the scan, the catalogue match, `carl start --model`, the router presets and the client lists find a model there or, as before, directly in its model's folder or directly in the models folder (`locate`, `relocate`, `same_file`); a model recorded in `models.json` with an older path is found after a move. The scan (`domain/catalog/inventory.py scan_models`, decision A68) reads the `.gguf` files directly in the models folder and the model folders of the layout: the quantization folders and the model's own folder, where the file has the folder's name for its model (`split_file`, `names_model`: a maker's prefix on the file name is fine) or, in a quantization folder, that folder's name for its quantization. It does not read `mtp/`, `vision/`, `template/` or `lora/`, a hidden folder or any other folder (`r4/`, `bench-a3b/`, a user's own): `~/models` is a storage area that the user's other tools share, and CARL reads only `gguf/` and `mlx/` (named). A file name shows once; a GGUF whose architecture is not a language model's (`bert` in its name and the like: it embeds or classifies text; `memory/shape.py not_language_model_arch`) is left out of the list (`Inventory.not_language_model`). `carl models` names the files that are not in the layout and says how to move them. CARL moves no file by itself, and `carl delete` removes the folders it empties (the quantization folder, `mtp/`, the model's folder) and no folder with anything else in it.
- `carl download hf:OWNER/REPO/FILE.gguf` gets the revision, the size and the SHA-256 from the Hugging Face API. It records the model in `models.json`, downloads it and verifies it.
- **The Hugging Face access token** (decision A75, `carl hf token`). A gated repo answers 401 without it, and its file list holds other SHA-256s without it than with it. CARL keeps the token in the system's secret store (`SecretStore`, C-065: the Keychain on a Mac, the Credential Manager on Windows (else, in an ssh session, a file of the same name left to the user, SYSTEM and the Administrators group), the Secret Service through `secret-tool` on Linux, else a file `hf-token` of mode 0600 in the settings folder; also the file when the Keychain or the Secret Service refuses). `HF_TOKEN` in the environment wins; `~/.cache/huggingface/token` (or `HF_HOME/token`) is read when CARL has none. The token is sent to huggingface.co and the hosts below it (and hf.co) over https only, in the Authorization header, on every Hugging Face call: the file list, the revision, the 24 MB header read and the download. A redirect to another host drops it (the signed address of a download host needs none). aria2c and curl never see it: with a token, CARL asks Hugging Face for the file itself and gives them the signed address. A 401 or 403 is one `error:` text that says the repo is gated and how to set the token. A file whose SHA-256 is not the recorded one is checked against the list read again with the token before it is called bad. The token is never in `config.json`, a log, an error text, a URL or an argument of a program.

**Custom model cards** (`models.json` `models.<name>.card`):
- A card is for a custom model only. Catalogue models are read-only: CARL refuses to write a card for them, and ignores a card written by hand under a catalogue name.
- `carl card NAME set FIELD VALUE` and `unset FIELD` write the card. The dashboard's edit mode does the same (Models panel, `e`).
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

`parse_custom_card()` (`src/carl/domain/catalog/records.py`) uses the rules of the catalogue (`_card()`):
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
- **Where CARL reads a card:** `build_models()` (`src/carl/domain/catalog/inventory.py`) joins the card into the record of the custom model. Thus, the model lists, the sort by quality (the rank), the filters and auto fit use the same fields as for a catalogue model.
- **Auto fit** uses a custom model only when its card says `auto_fit: true`. The rank of a user is not measured, so this is opt-in. `carl download default` and the download offer use only catalogue models.

### Auto-tune

`carl tune NAME [--quick | --long]`  measures one model on this Mac. `carl tune all` measures each downloaded model in turn. `--dry-run` measures but does not save.

- Auto-tune starts its own server (`carl start` of the model) on port 8093, one time for each speculation mode. On Windows it runs the same way (the server has a hidden console of its own, and the console stop ends it). The default run takes about 5–10 min, `--quick` about 4 min, and `--long` 10–40 min more.
- The record's machine is the processor and the RAM on a Mac ("Apple M2 Max 32 GB"), and on a PC the processor and each GPU with its VRAM ("AMD Ryzen 9 3900X, NVIDIA GeForce RTX 2070 SUPER 8 GiB").
- It refuses to start if a server runs on 9275 (or 8080, CARL's earlier port) or 8093. It also refuses if a different large process is in memory (more than `BIG_GB`, default 8 GiB, or a known model server). `ALLOW_SECOND_MODEL=1` skips this check, as for the launch guard.

The steps:

1. **Memory:** the largest context for each slot that fits the GPU memory limit, with 1 and 2 slots (the same calculation as `carl fit`). If not even a 16K context fits, Auto-tune stops.
2. **Speculation:** the modes in the next table. Each mode writes prose, new code and a code edit, two times. The score is a weighted geometric mean (prose 0.4, code 0.4, edit 0.2). A mode with more guesses or more parts must be 3% better than a simpler mode to win.
3. **Batch size** (every OS, also with `--quick`; decision A45): a cold read of 8K tokens at `-ub` 512, 1024, 2048 and 4096, each in a start of its own, for the sizes that keep the setup `-ub` 512 gets (Auto fit's order: 2 × 96K, 2 × 64K, 2 × 48K, 1 × 96K, 1 × 64K, 1 × 48K; nothing below 48K for a larger `-ub`). The fastest wins when it reads 5% faster than the default (2048 on a PC, 512 on a Mac), else the default. Auto-tune saves it in the model's record (`ub`, and `ub_note` says why); every start of the model uses it. A `-ub` typed in the server options wins. A `llama.ub` or a choice you saved for the model does not win silently over the record: a start in a terminal asks once (see Batching below). Measured on the RTX 2070 SUPER with the A3B's experts in the RAM: 386, 635, 978 and 1267 tok/s at 512, 1024, 2048 and 4096; on the M3 Pro (Metal) 2048 read 1% faster than 512, so a Mac keeps 512.
   - **Threads with the experts in the RAM** (a computer whose CPU can be read, when the chosen setup keeps any expert layer in the RAM; decision A54): the chosen speculation and `-ub` run at that setup with the threads pinned one per physical core and unpinned (prose, code and an edit; two runs each, alternating, averaged). The record keeps pinned only when it writes 3% faster (`pin`, `pin_note`, `results.threads_ram`); a tie or a smaller gain is unpinned, and the start pins only for a record that says pinned and whose two results show pinned 3% faster (A57, A59): a record that holds a tie starts unpinned without a new tune.
4. **Prompt reading:** cold reads at 8K, 32K and 64K tokens (on the CPU alone: 2K, 4K and 8K, with the longer reads estimated from their slope, decision A71). `--quick` reads at 8K and 32K. `--long` also reads at 128K and 192K, and measures the write speed at each depth. The time for each token increases about linearly with the depth. From this, Auto-tune calculates the time to read a full context.
5. **Parallel requests** (not with `--quick`): 1, 2, 3 and 4 requests at the same time (`llama-batched-bench`, the one in the folder of `llama-server`).
   - **Context memory type on the CPU alone** (decision A71; step 2, right after the memory step): llama.cpp reads prompts 2.6 to 3.3 times faster on the CPU with f16 than with q8_0 or q4_0, so each type that fits the RAM (`cpu_kv.covers`: the free RAM covers the start and the load reserve) starts the model at the model's `-ub` with no speculation, reads 8K tokens cold and writes 128. `best_kv` keeps f16 unless a later type reads or writes faster by 3% without losing more than 3% on the other. The record keeps the speeds (`results.kv`), the type (`settings.kv`) and a line why (`kv_note`); every later step and every later start on the CPU alone use the type (a start on a GPU backend or a Mac does not: the type is the CPU's, decision A72). The memory (the largest context, the slots) is judged with it, and with the RAM less the reserve as the limit.
6. **Result** (`choose_ctx()` in `src/carl/domain/tune/scoring.py`): q4 context memory (on the CPU alone the type step 2 chose), the best speculation, the context, and 2 slots if two contexts fit. Auto-tune saves it in `~/.config/carl/models.json`, with the llama.cpp version and the backend it measured on (`llama_cpp`, `backend`; every start compares them with the installed build, and says the result is an estimate when they differ, decision A72), the speed of each mode, the read speeds, the zones, the `-ub` and the load mode its starts used (each test start takes load mode none when the free memory allows it, as `carl start` does).

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

- The fast zone is never smaller than 96K (`ctx_zones()` in `src/carl/domain/catalog/inventory.py`).
- If 96K fits one slot, 96K is the minimum. Above it, Auto-tune keeps the catalogue context while a cold read of it is not worse than slow on this Mac and it fits. Else, it uses the largest standard context (96K, 128K, 160K) in the fast zone that fits.
- If 96K does not fit, Auto-tune uses the largest standard context (32K, 48K, 64K) that fits.

NOTE: The edit workload copies `llama_server.py` (as it was at one commit) back (since 1.1.0). Thus, edit scores are not directly comparable with older Auto-tune results.

The Auto-tune panel of the dashboard stops the running server, runs the tune, and starts the server again.

### Server flags

This table shows the flags that the start gives to `llama-server` (single model), and the reasons:

| Setting | Value | Why |
|---|---|---|
| Bind | `--host 127.0.0.1 --port 9275 --api-key-file ~/.config/carl/api-key` | This Mac only by default (`--vm`: 192.168.42.1). A shared key. llama.cpp's own default port changes to 9931 in a later release: CARL always gives `--port` (the single start, the router, Auto-tune and agent-bench, which start the server through the launcher). |
| Model name | `--alias NAME` | The CARL name of the model |
| Offload | `-ngl 999` | The whole model is on the GPU (Metal). |
| Flash attention | `-fa on` | Necessary for a quantized V cache. It was on for each measurement. |
| Memory fitting | `--fit off` (router presets: `fit = off` in `[*]`, for each model that the router starts; Auto-tune: through the launcher, and also on `llama-batched-bench`) | llama.cpp fits by default (`--fit on`). CARL sets `-ngl` and `-c`, and checks the memory itself (`carl fit --check`). llama.cpp's own memory fitting only probes here. Its probe fails for the Gemma 4 MTP drafter ("Gemma4Assistant requires ctx_other"), and that showed as 1 error on every Gemma start. |
| Context memory (KV cache) | `-ctk q4_0 -ctv q4_0` (`--kv q8` → q8_0). On the CPU alone (no GPU found, a CPU build of llama.cpp installed, or `gpu.backend` cpu; A71): f16 when the free RAM covers the start and the load reserve, else q8_0, else q4_0, unless a flag, `KV`, `config.json` or a tune record that measured the types on the CPU says otherwise (`cpu_kv.py`; the plan's `kv` line; router mode's presets take the same rule, A72) | q4 against q8 (27B, M3 Pro, 2026-09-24): +16% read speed, about 2 GiB less memory, the same write speed, 8/8 needle recall at 66K. On the CPU (llama-bench b11738, Ryzen 9 3900X, Qwen3 0.6B Q8_0, 4K tokens): f16 267 tok/s, q8_0 103, q4_0 81; write 49.1, 49.4, 46.7. Do not mix K and V types (the read speed is about 5× slower). |
| Context | `-c` = slots × 96K (`--ctx` sets the context for each slot) | A large context reads cold prompts and writes much more slowly ([Context length](memory.md#context-length-what-a-larger-context-costs)). The model maximum is 262144. The server allocates the whole context memory at start. |
| Batching | `-b 2048 -ub N`, in this order: a `-ub` you typed (in the server options, on `carl start`'s command line, or the environment's `UB`); the size you saved for this model (Settings > Batch size, or the answer Keep; auto, the default, saves none and passes over a `llama.ub`); the model's Auto-tune result; for a model without a result `llama.ub`, else 512 on a Mac and 2048 on a PC (a PC start without a result picks the largest size that keeps the setup: 2048 where it fits beside n-gram, else 1024, else 512). A `llama.ub`, or a saved choice that a later tune outdates, that differs from the tuned `-ub` is asked about once in a terminal (Use tuned, or Keep it for this model); a script, router mode or a client uses the tuned `-ub` and the start's Batch line says "-ub 2048 (tuned); config's 512 ignored" | On Metal `-ub 512` gave the best measured result (90.5 tok/s vs 88.6 and 86.1 for 1024 and 2048). On a CUDA card a MoE model with its experts in the RAM reads prompts 2.5 times faster at 2048. A `-ub` that would cost a slot or context (2 × 96K to 2 × 64K, or 2 slots to 1) gives way to 1024, then 512: the start line says so. |
| Load mode | `--load-mode none` when the free memory holds what the server keeps in the RAM and the load reserve (`llama.load_reserve_gib`, 8 GiB); otherwise no option, so the weights stay mapped from the file (llama.cpp's default) | The weights read into memory: on the Windows test PC (A3B, experts in the RAM) 1084 against 979 tok/s of prompt reading, and 10.8 against 13.6 GB of server memory. On a Mac (M3 Pro) the same speed either way. The mapped load lets a larger model start. Your own `--load-mode`, `--mmap` or `--no-mmap` in the server options wins. The start line `Load:` says which and why. |
| Slots | `--parallel N`; with 2 or more: `--kv-unified --kv-unified-per-slot CTX --no-cache-idle-slots -sps 0.5` | The main OpenCode session and a subagent each keep their own slot and cache. |
| RAM cache and checkpoints | `--ctx-checkpoints 8 --checkpoint-min-step 4096 --cache-ram N` | Checkpoints let follow-up turns use the cache again, because the recurrent layers of Qwen cannot trim it. The RAM cache holds sessions that are not in a slot (each held state is 2–2.5 GiB). N = RAM − the memory needed by the model − the memory kept free, 1–8 GiB. If the size calculation fails, N is 4096. |
| Disk cache | `--slot-save-path ~/.config/carl/slots` (`CARL_CONF_DIR/slots` when it is set) | OpenCode and Pi save and restore prompts and sessions here ([Caching](caching.md)). |
| Sliding window | `--swa-full` (only for a sliding-window model, when `cache.swa` gives full) | A restored state needs every layer at full length ([Sliding-window models](caching.md#sliding-window-models)). |
| Projector | `--no-mmproj` | CARL uses text only. |
| Templates | `--jinja --reasoning-format deepseek`, `--chat-template-kwargs '{"preserve_thinking":true}'`, `--chat-template-file` (patched) | The server sends the reasoning to `reasoning_content`. `reasoning_effort: none` turns off thinking ([How thinking works](thinking.md#how-thinking-works)). |
| Sampling | `--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0 --repeat-penalty 1.0` (Gemma 4: `--top-k 64`) | The values of the catalogue `tune` of the model: the Qwen values for thinking mode, Google's values for Gemma 4 ([Sampling](sampling.md#sampling-and-output-limits)). `TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE`, `REPEAT` override them. |
| Speculation | `--spec-type TYPE --spec-draft-n-max N` (from the tune of each model; none with `SPEC=none`) | The best of the modes that were measured on each model ([Performance](performance.md#performance-measured), [IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)). |
| Vision | `--no-mmproj` (a start is text only), or `--mmproj FILE` when `llama.vision` is on and the model's projector is downloaded | The projector costs about 1 GB; the memory plan counts it. Without the file, the start says so and stays text only. |
| Other drafters | `-md DRAFT` with `--spec-type draft-dflash`, `draft-dspark` or `draft-eagle3` (alone or with `ngram-mod`) and `--spec-draft-n-max N` | The file of the kind comes from the model's `dflash/`, `dspark/` or `eagle3/` folder (`DRAFT_DFLASH`, `DRAFT_DSPARK`, `DRAFT_EAGLE3`). Without it the kind is left out and n-gram stays. Auto-tune chooses the kind. |
| MTP drafter | `-md DRAFT` (only a model with a separate drafter, Gemma 4, when `--spec-type` contains `draft-mtp`) | The drafter gives MTP speculation to a model file without an MTP head. On the Gemma 4 E4B (new code, M2 Max, llama.cpp 0.5.0, 2026-10-04): 76.0 tok/s with MTP and 2 guesses, 61.1 without speculation. |
| Logging | One model: `-lv 4 --log-timestamps --log-prefix`, the output through `src/carl/adapters/common/log_filter.py` into `~/models/logs/llama-server-<ts>.log`. Router mode: `--log-file ~/models/logs/llama-server-<ts>.log --log-timestamps --log-prefix` | `llama-server-latest.log` points to the newest log. Use `carl log` to read it. `LOG_FILE=none` turns the file off (and `-lv 4`). Why `-lv 4` (Phase 23.4.4): llama.cpp reports its RAM cache (`cache state: N prompts, X MiB (limits: …)`) only at that level, and no endpoint has it (`/metrics`, `/slots`); the dashboard's SLOTS card shows its sessions and memory from these lines. At `-lv 4` the log is about 10 times longer, so the server writes to CARL's filter, not to a `--log-file`: the filter (`src/carl/domain/server/serverlog.py`) drops the per-request detail that only `-lv 4` writes and keeps everything else (the default lines, every warning and error, the RAM cache's lines, the start's detail with the buffer sizes, and any line it does not know). Per request the log keeps about 1.4 times the lines of the default level (measured: 41 against 29; 148 unfiltered). llama.cpp writes its `-lv 4` lines with the same `I` prefix as its default ones, so the filter knows them by their kind (`LV4_ONLY`), checked against llama.cpp 0.6.0; a kind it does not know stays in the log. The filter writes `~/models/logs/.log-PORT.json` (the log, its pid): the dashboard finds the log there, and its HEALTH card warns when the filter stopped while the server runs. It ends when the server's output ends (Ctrl-C, a closed terminal and SIGTERM do not stop it). A proposal for llama.cpp to report the RAM cache in `/metrics` would make the filter unnecessary. Router mode keeps the default level (no count). |
| Metrics | `--metrics` | A Prometheus endpoint at `/metrics` |
| Keep awake | `caffeinate -i -w <server pid>` | If the Mac goes to sleep, requests stop in the middle of the prompt. |
| Dashboard in the same terminal | `PosixServerRunner` in `src/carl/adapters/posix/server_runner.py` | One command shows the server live. `MONITOR=0` gives a plain foreground server. |
| Network | `--local` (127.0.0.1, the default), `--vm` (192.168.42.1) | The launcher refuses 0.0.0.0. |
| Memory check | `carl fit --check` before the model loads | No start over the GPU memory limit. `FIT_CHECK=0` skips it (the expert override). |
| Launch guard | `guard_other_models` | No start if a second model is in memory. `ALLOW_SECOND_MODEL=1` skips it. |
| Port guard | No start if the port is in use (`netstat`, not `lsof`) | A second model would collide with the running one. Also, `llama-server-latest.log` continues to point at the running server. |

- These environment variables override the settings: `CTX`, `KV`, `KV_K`, `KV_V`, `SLOTS`, `UB`, `BATCH`, `CKPT`, `CKPT_STEP`, `CACHE_RAM`, `RESERVE_GB`, `SPEC`, `SPEC_N`, the sampling variables, `MODEL`, `ALIAS`, `HOST`, `PORT`, `NET`, `LLAMA_MODE`, `SWA_MODE`, `API_KEY_FILE`, `LOG_FILE`, `THINK_TOGGLE`, `KEEP_AWAKE`, `MONITOR`, `FIT_CHECK`.
- `config.json` can hold the same settings ([Settings](#settings-configjson-auto-tune-and-the-catalogue)).
- Extra arguments go directly to `llama-server`. `llama.extra_args` in `config.json` does the same.
- `carl -h` lists the commands. `carl help llama` lists the options. `carl help env` lists the environment variables.

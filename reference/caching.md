# CARL Reference: Caching

[Index](README.md) · every cache that CARL uses: llama.cpp's slots and RAM cache, the disk cache of OpenCode and Pi, how a saved state is built, and how saved sessions share their prompt on the disk.

## The caches at a glance

A request reads its whole prompt unless a cache already holds the start of it. The server then reads only the new part, and the answer starts sooner.

| Cache | Where | What it holds | Lost when | Set by |
|---|---|---|---|---|
| A slot | GPU memory | The session that the slot runs now (its context memory, the KV cache, and on Qwen the recurrent state) | Another session takes the slot | `--parallel` (slots), the context per slot |
| Context checkpoints | GPU memory | Points in a session that the server can go back to (Qwen) | The session leaves its slot | `--ctx-checkpoints` (8, at least 4K tokens apart) |
| The RAM cache | RAM | Sessions that left their slot | The server stops, a router switch, or the cache is full | `--cache-ram` (the launcher: 1–8 GiB) |
| The disk cache | `~/.config/carl/slots` | Each agent's prompt (a saved prompt) and each session (a saved session) of OpenCode and Pi | The disk limit removes the oldest files | The `carl-cache` plugin and extension; Settings > Caching |

## llama.cpp's own caches

- **Slots:** the server has 2 slots by default (up to 4 where they fit). Each slot keeps one session.
- **RAM cache** (`--cache-ram`): the launcher calculates its size from the free RAM after the model and the memory kept free for macOS and apps: 10 GiB with the VM network, else 6 GiB (`RESERVE_GB`). The size is 1–8 GiB, in 256 MiB steps. This cache holds sessions that are not in a slot.
- On Apple Silicon, the RAM cache uses the same memory as "VRAM". macOS moves this memory to swap on the SSD when the memory pressure is high.
- **Disk:** llama.cpp has no automatic disk tier. It has only a manual function (`--slot-save-path` and `/slots/{id}?action=save|restore`). CARL's clients use this function ([the disk cache](#the-disk-cache-of-opencode-and-pi-measured-2026-10-03-and-2026-10-04)).

### The RAM cache and checkpoints (measured 2026-10-02)

- The server has 2 slots by default. When a third session starts, it takes the slot of the session that was used least recently.
- The server keeps the removed prompt in the **RAM cache** (`--cache-ram`).
- When that session continues, the server copies the prompt back from RAM. It does not read the prompt again.
- **The launcher sets the cache size.** The size is the free memory after the model and the memory kept free for macOS (10 GiB when the VMware network is up, else 6 GiB). The limits are 1024 MiB and 8192 MiB.
- **Test:** the 35B, 2 × 96K, M3 Pro 36 GB, llama.cpp 0.4.1. Three sessions of ~20K tokens each, then one follow-up in each session. Then a turn with thinking, and the turn after it.

| Configuration | Cold read of each prompt | Follow-up after eviction: the wait | Turn after a thinking turn | Swap |
|---|---|---|---|---|
| Default: cache 2560 MiB, 8 checkpoints at least 4096 tokens apart | 44–47 s (~450 tok/s) | **0.7–1.2 s** | only the new text read | 0.90 GiB |
| `--cache-ram 0` | the same | **40–45 s** (the session read again) | only the new text read | 0.90 GiB |
| 16 checkpoints at least 1024 tokens apart | the same | 0.7–1.1 s | only the new text read | 0.90 GiB |
| `--kv q8` (cache 1792 MiB) | 42–45 s (~470 tok/s) | 0.8–1.2 s | only the new text read | 1.26 GiB |

**Results:**
- **The RAM cache is necessary.** Without it, the server reads a removed session again in full: 40–45 s for 20K tokens, and minutes for a long session.
- **A larger cache gives no gain on the 35B.** 2560 MiB holds approximately 466K tokens of q4 context memory. This is almost five full 96K contexts.
- **More checkpoints give no gain.** Each turn adds to the end of the session. The server keeps earlier reasoning (`preserve_thinking`). Thus, the cached prompt stays the start of the new prompt, and the server reads only the new tokens.
- **q8 fits at 96K on 36 GB.** On the 35B, it reads approximately 4% faster, with the same write speed and 0.36 GiB more swap. q4 stays the default, because it uses less memory and has the recall tests.
- NOTE: **The 27B with `--kv q8` has a small cache.** The automatic size is 1792 MiB. At 34 KiB for each token, this holds only ~54K tokens. Thus, the server reads a removed long 27B session again in full. With q4, the 27B gets 4864 MiB (~276K tokens), which is sufficient.
- The RAM cache is lost when the server stops or when router mode changes the model. The disk cache of OpenCode and Pi covers these cases ([below](#the-disk-cache-of-opencode-and-pi-measured-2026-10-03-and-2026-10-04)).

## The disk cache of OpenCode and Pi (measured 2026-10-03 and 2026-10-04)

The RAM cache is lost when the server stops or when router mode changes the model. It also removes the oldest prompts when it is full. CARL's disk cache keeps saved prompts and saved sessions on disk with the slot API of llama.cpp. The dashboard and `/carl` call it **disk cache**.

| Part | What it does |
|---|---|
| `--slot-save-path ~/.config/carl/slots` | The folder of the saved states. The launcher and the router presets set it. |
| `POST /slots/N?action=save\|restore` with `{"filename"}` | Saves or restores the state of slot N. In router mode, the body also holds `"model"`. A `?model=` query gives HTTP 400. |
| `client/shared/carl-cache.js` | The client code. It uses only the server's API, so it also works from a VM or another computer. |
| OpenCode plugin `carl-cache` | Wraps `fetch` (the AI SDK calls the global one). `chat.headers` marks the requests of CARL's provider with the session, the agent, and whether it is a subagent. |
| Pi extension `carl-cache` | Uses `before_provider_request` and `message_end` |
| The dashboard (`tools/monitor/diskcache.py`), the launcher, `./carl.sh cache` | Keep the disk limit |

### Before each request

1. **The parts that change go after the shared part.** Some blocks of the system message change between projects and days:
   - OpenCode: the `<env>` block (folder, git, date), and the `Instructions from:` blocks of files in the working folder (the project's AGENTS.md).
   - Pi: the `<project_context>` and `<cwd>` blocks.

   The client takes them out of the shared system text, so that the system text and the tools are the same in each project and on each day, and one saved prompt serves every project. Where they go depends on the model's chat template (the client checks it once per model with `/apply-template`):
   - **A second system message** (Qwen 3.6 and 3.8): the template puts the tools first, then the system text, and merges a second system message into it. The blocks stay system text, after the shared part. The saved prompt ends exactly where they start.
   - **Left where they are** (Gemma 4): CARL saves no prompt for Gemma 4 (its sliding-window cache cannot restore one), so nothing moves.
   - **Any other template:** left where they are by default (`cache.move off`): they stay system text, but each new project reads the whole prompt again. Settings > Caching > Other templates > "move to your message" (`cache.move auto`; `CARL_CACHE_MOVE=auto` for the clients on another computer) puts them at the start of the first user message, so one saved prompt serves every project. Do not use it with a template that also uses a sliding-window cache. Qwen and Gemma 4 do not change with it.

   Two saved prompts cannot be joined: each token's state depends on all the tokens before it. Thus, the parts that change must come after the shared part, not between.
2. **The slot.** The client selects the session's own slot when it is idle and no other session of this process used it since. A process that is new to the session finds the slot through the slot records (see below). Else, the client selects an idle slot (an empty one first).
   - The client pins the request to that slot (`id_slot`). A response does not tell which slot served it.
   - The client claims the slot until the server has the request. In the process, this is a counter. Across processes on this Mac, it is a claim file (`.claim+MODEL+SLOT` in the slots folder). The client makes the file only if it does not exist, and takes it over after 2 min.
   - A client on another computer claims through the dashboard's API ([Clients on other computers](client-sync.md)).
   - Without a free slot, the request is not pinned, and llama.cpp decides.
   - Before the claim files, two processes could read the slot list at the same time and pin the same slot. The second request then waited in the queue of llama.cpp for the whole first request.
3. **The state.** The client puts a state into the slot in this order:
   - The session's file (`carl-session+MODEL+KEY+SESSION.bin`), when the session is new to this process or server. Also when a different session took its slot after its last save.
   - Else, the prompt file of the agent (`carl-prefix+MODEL+AGENT+HASH.bin`).
   - Else, the client reads the agent's prompt one time (`/completion` with exactly its tokens, `n_predict 0`) and saves it. A prompt of less than 1,024 tokens gets no file.
   - A slot that holds a different session first goes to the RAM cache. The client sends a 1-token task: llama.cpp saves the old prompt when a new task arrives.
   - The agent's prompt is the common token prefix of the system text and the tools. The client finds it with two different user messages (`/apply-template`, `/tokenize` with `add_special`).
4. **Router mode:** the client loads a model that is not loaded (`POST /models/load`). It asks again while the router is busy with a different switch. Thus, the state can go in before the request. A title or summary request for a model that is not loaded goes to the loaded model. A switch for it would unload the session's model, and then load it again.
5. **Title and summary requests** run with thinking off (`reasoning_effort: none`) and are not pinned. With thinking on, an OpenCode title on the 35B held a slot for 35 s, and `opencode run` waited for it.
6. **A slot that a different session of the same process needs:** the client first sets the session in it back to the end of its last prompt and saves it. The session comes back from its file when it returns.
   - Measured: OpenCode gave a task to its coder subagent while the other slot ran the title request.
   - Without this step, the next request of the main session read all 8,567 tokens. llama.cpp kept the coder's state (it shares the tool list of OpenCode) and not the main session's RAM copy. The hybrid model could not go back to the shared point.
   - With this step: 232 tokens. The whole run: 59 s before, 12 s after (with quick titles).

### After a turn

The client saves after a reply that is not a tool call. It saves before the client sees the end, so `opencode run` and `pi -p` do not exit first. It saves only main sessions (not subagents, titles, summaries or compactions) of 4,096 tokens or more. `cache.save` sets when:

| `cache.save` | What occurs |
|---|---|
| `auto` (default) | Saves when the tokens not in a file yet would take `cache.auto_s` seconds (default 120) to read at this model's speed. Else, as `switch`. |
| `turn` | Saves after each turn |
| `switch` | Does not save. Sets the slot back to a point that the template does not render again, then writes a **record**. |
| `stop` | As `switch` |

- "The tokens not in a file yet" = the tokens of the slot less the larger of two values: the last save, and the state that went in.
- The model's read speed is measured when the client reads an agent's prompt. Until then, the client uses 500 tokens/s.
- A record (`.resident+MODEL+SLOT.json`) tells which session the slot holds: the session's file, the slot's task id, and how much a file already holds.
- Records are saved before the state leaves the server:
  - The dashboard saves them before Stop, Apply, Auto-tune and the Router panel's Load and Unload (`jobs.save_before_stop`).
  - A client saves them before it makes the router load a different model.
  - Each save occurs only while the slot's task id is unchanged.
- With `auto`, `turn` and `switch`, a slot that a different session of the same process needs is saved first.
- KEY is a hash of the model file and the llama.cpp build (`/props`).

### Whether a state fits

The hybrid Qwen models cannot roll back their recurrent state, and a restored state has no checkpoints. Thus, a restored state helps only when the next prompt starts with all of its tokens. Otherwise, llama.cpp reads the whole prompt. A restored state that does not fit also prevents the use of the RAM cache.

A state saved directly after a reply fits when the template renders the reply again exactly as the model wrote it:

| Model | Does it fit? |
|---|---|
| Qwen 35B and 27B templates, with `preserve_thinking` (CARL sends it) | Yes, with thinking on and off |
| Qwen 9B | Its template has no `preserve_thinking`, and drops earlier reasoning. `host/gguf-chat-template.py` adds it, as in the newer templates. |
| Gemma 4 | It drops earlier reasoning. The client checks this one time for each model and thinking mode (with a probe session). Where the state does not fit, the client first sets the slot back to the end of the last prompt (`/completion`). Then it saves. |

### Sliding-window models

- Gemma 4 E4B has sliding-window layers (`attention.sliding_window` 512).
- With the window cache, a restored state was not used: the next request read the whole prompt again (5,100 tokens, ~9 s at ~580 tok/s). With the full cache (`--swa-full`), the answer started in under a second (100 new tokens read).
- `--swa-full` keeps each layer at full length. llama.cpp calculated 4,766 MiB without it and 7,099 MiB with it (2 × 96K, q4).
- `cache.swa` (`SWA_MODE` for the launcher) sets the mode:

| `cache.swa` | Effect |
|---|---|
| `auto` (default) | `--swa-full` when it fits with the slots that `llama-fit.py --plan` gives. A second slot has priority. |
| `full` | Always `--swa-full` (the full cache). Restores work. More memory. |
| `window` | Only the window (the window cache). Less memory. Restores do not work. |

- The router presets do the same for each model (`swa-full = true`).
- The memory estimate reads these GGUF keys:
  - `attention.sliding_window_pattern`: 35 of the 42 layers of Gemma 4 E4B
  - `shared_kv_layers`: the last 18 layers use the KV of earlier layers
  - the sliding-window key and value lengths
- Gemma 4 E4B has 24 layers with their own context memory: 4 full-attention layers (8,192 elements for each token) and 20 sliding-window layers (20,480 elements). The sliding-window layers count at full length with `--swa-full`, else as the window + 512 tokens for each slot.
- Before this change, the estimate counted each layer at full length with the global head size. This gave 86,016 elements for each token, about 3× too much.

### Measurements

The table gives the wait before the answer starts: with the cache, and without it (the server reads the prompt again). All rows: an M2 Max 32 GB, llama.cpp 0.5.0 (build 11146), 2026-10-03 and 2026-10-04. "Without" comes from the measured read speeds on this Mac (35B IQ3 ~505 tok/s, 9B ~293, Gemma 4 E4B ~580, at 8K), except where the table says "measured". The last column gives the tokens that the server read, only for a check against the server's log.

| Measured (q4, 2 × 96K) | Wait with the cache | Wait without it | Tokens read (with / without) |
|---|---|---|---|
| 35B IQ3, OpenCode: a new session (the build prompt read one time: 12.9 s, measured) | under 1 s | ~13 s | 207 / ~8.4K |
| 35B IQ3, OpenCode: the session after a router switch away and back (after the load) | under 1 s | ~17 s | 23 / ~8.5K |
| 9B, Pi, thinking off: the 2nd turn of the session in a new process | under 1 s | ~17 s | 23 / ~5K |
| Gemma 4 E4B, OpenCode: the session after a server restart | under 1 s | ~13 s | 21 / ~7.8K |
| Gemma 4 E4B, Pi: the session after a server restart | under 1 s | ~8 s | 20 / ~4.5K |
| 35B IQ3, OpenCode, a real config: an 11.5K-token session after a server restart | under 1 s | ~23 s | 22 / 11.5K |
| 35B IQ3, Pi: a 7.4K-token session after a restart (both measured) | 0.2 s | 10.9 s | 24 / 7,443 |
| Router: sessions after the switches 35B → 9B → Gemma → 9B → 35B → Gemma (after each load) | under 1 s | 12–25 s | 22–63 / 6.8–11.6K |
| `auto` (35B): a new process found the session in its slot (record); a dashboard Stop saved it; the turn after the restart | under 1 s | ~17 s | 20, then 18 / 8.5K |
| Restore of a 118 MB file (35B) / 29 MB (Gemma, 5K tokens) | 15–30 ms / 5 ms | | |

### The disk limit

- `cache.disk_gb` (default 10 GB) is the limit.
- After each save on this Mac, the client removes the oldest saved sessions first, then the oldest saved prompts. The file that it saved last stays, unless it alone is over the limit.
- The older prompt files of the same agent go at once.
- The dashboard checks the limit each minute. The launcher checks it at each start (for files written from a VM).
- The client does not save when the disk has less than 10 GB free.
- The dashboard removes the files of Phase 11 (its pre-read and per-slot saves, with dashes in the names).

### The cache settings

The `cache` section of `config.json` (the Caching panel of the dashboard: **Disk limit**, **Saved prompts**, **Saved sessions**, **When to save**, **Save after**, **Shared storage**, **Gemma models**; the **Sliding window** row of the Server panel is the same `cache.swa`). The clients on this Mac read it. A client on another computer reads it through the dashboard's API.

| Key | Default | Effect |
|---|---|---|
| `disk_gb` | 10 | The disk limit in GB (1–1000) |
| `prefix` | true | Save and restore the prompt of each agent |
| `sessions` | true | Save and restore the sessions |
| `save` | `auto` | When to save: `auto`, `turn`, `switch`, `stop` (see above) |
| `auto_s` | 120 | `auto`: the seconds of unsaved reading before a save (10–3600) |
| `share` | true | Store a saved session as the changes to its saved prompt (see below) |
| `swa` | `auto` | Sliding-window models (Caching panel: **Gemma models**): `auto`, `full` (the full cache), `window` (the window cache) |

- With `prefix` and `sessions` both false, the cache does nothing.
- In the environment of OpenCode or Pi, `CARL_CACHE=0` turns the cache off, and `CARL_CACHE_SAVE=MODE` overrides `save`.
- `NO_CACHE=1 ./carl.sh install --config-only` (`NO_CACHE=1 ./setup` on other computers) leaves the OpenCode plugin and the Pi extension out.
- `./carl.sh cache show` lists the saved prompts and the saved sessions (sizes in GB and MB; `*` marks a session stored as the changes to its saved prompt). `./carl.sh cache trim` applies the limit. `./carl.sh cache clear` removes each file.

### When an agent's prompt changes

- The client checks the prompt at each request. The check is a hash of the system text, the tools and the template fields, in memory. It sends no request to the server.
- The prompt changes when you add or remove a tool or an MCP server, change a global instruction file, or update OpenCode or Pi.
- The date, the folder and the AGENTS.md of the project do not change it. They come after the shared part (step 1 above).
- After a change, the next new session reads the new prompt one time and saves it as a new file. The hash gives a new name. This read takes the same time as a session without the cache.
- On this Mac, the client then removes the older prompt files of the same agent and model. The dashboard's limit removes them on the server for clients on other computers.
- A saved session that started with the old prompt cannot continue from its file, because its first tokens are different now. llama.cpp reads the session again one time. The tidy step removes its patch, because the base is gone.

## How a saved state is built

### One sequence, in order

llama.cpp keeps a session as one sequence of tokens. A saved state is that sequence and the model's memory of it. The sequence always has the same order:

| Order | Part | What it holds | Who makes it |
|---|---|---|---|
| 1 | The agent's prompt | The system text and the tool definitions | OpenCode or Pi, for each agent |
| 2 | The project part, then the first user message | The environment block and the project's instructions (a second system message with Qwen; the start of the user message with other templates), then your first message | OpenCode or Pi; CARL puts the project part here |
| 3 | The rest of the session | The replies, the tool calls, the tool results, your next messages | The model and the client |

- The chat template sets the order inside part 1. Qwen puts the tools first and the system text after them. Gemma 4 puts the system text first and the tools after it. Both orders are in part 1.
- A tool call is text that the model writes. The template writes it again, from the structured call, when the client sends the session back.
- A tool result is a message from the client.

### Saved states are prefixes

The three parts are nested prefixes. They cannot go together in a different order.

- The agent's prompt file holds part 1.
- A session file holds parts 1, 2 and 3, up to the end of the last turn that was saved.
- A new request continues a saved state only when the request starts with all the tokens of that state. One different token stops the reuse at that point. llama.cpp then reads the rest again.
- llama.cpp cannot put part 3 of one file after part 1 of another file. It cannot remove a part from the middle.

Thus, CARL keeps these rules:

1. The parts that change between projects and days go into part 2, not part 1. Then part 1 is the same for an agent in every project, and one prompt file serves every session.
2. A session file is used only by its own session. Its part 3 belongs to that session only.
3. A prompt file is used by every new session of its agent and model.

### What a saved state holds

For each token, the state holds the context memory (the KV cache) of the attention layers. A hybrid model (Qwen 3.6 and 3.8) also holds a recurrent state for the whole sequence.

| Part | Size (35B IQ3, q4_0) | Shared between files |
|---|---|---|
| Context memory of the attention layers | ~5.8 KB per token | Yes: the same tokens at the same places give the same bytes |
| Recurrent state | ~59 MB, for any length | No: it is a summary of the whole sequence |

- A session of 8.6K tokens is 115 MB. About 45 MB of it is the prompt's context memory. About 59 MB is the recurrent state.
- A session of 74K tokens is about 0.5 GB.
- A model without recurrent layers (Gemma 4) has only the context memory. A larger part of its file is shared.

### What a change does

| Change | Effect |
|---|---|
| A new tool, MCP server or global instruction | Part 1 changes. A new prompt file is made at the next new session. Old session files no longer match. |
| A new day, a new folder, a changed AGENTS.md | Part 2 changes for new sessions. Part 1 and the prompt file stay the same. |
| An edited or removed earlier message | Part 3 changes at that message. The reuse stops there. |
| Another model file or llama.cpp build | No saved file matches. The file names carry a hash of both. |

## Shared storage: saved sessions stored as changes (zstd patches)

- A saved session is stored as a zstd patch against the saved prompt of the agent that it starts with (`tools/monitor/slotpack.py`, `cache.share`, default on). The Caching panel calls this **Shared storage**. The prompt files stay whole: they are the bases.
- The dashboard makes the patches in its minute loop. `./carl.sh cache trim` also makes them. A new file waits 30 s. CARL keeps a patch only when it is at most 80% of the file.
- Before a restore, the file is made whole again. A client on this Mac uses zstd. A client on another computer uses the dashboard's API (`POST /carl/cache/unpack`). The copy gets the time of the patch, so the tidy step removes it after one minute.
- When the limit removes a prompt file, it also removes the patches that need it.
- zstd is a dependency (`brew install zstd`; `ensure_deps` offers it). Without zstd, the files stay whole.

| Measured (35B IQ3, 2026-10-04) | Size |
|---|---|
| A build session, 8.6K tokens, whole | 115.2 MB |
| The same session as a patch against the build prompt | 64.1 MB (0.3 s to make, 0.1 s to undo, the same bytes) |
| The 7 saved sessions on disk, whole / as patches | 1.5 GB / 1.2 GB |
| A restore from a patch after a server restart: the wait before the answer starts | under 1 s (the patch is undone in 0.1 s) |

The patches save disk space. They do not decrease the drive writes, because llama.cpp writes the whole file first. A decrease of the writes needs incremental saves (v2 in the plan).

- The prompt's context memory is then stored one time, in the prompt file.
- Each session stores its recurrent state and its own tokens.
- The saving is about 40% for a short session and about 10% for a long session (35B).

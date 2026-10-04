# CARL Reference: The disk prompt cache

[Index](../REFERENCE.md) · saving and restoring prompt states for OpenCode and Pi.

## How it works (measured 2026-10-03 and 2026-10-04)

The RAM prompt cache is lost when the server stops or router mode switches the model, and it evicts the oldest prompts. CARL's prompt cache keeps prompt states on disk with llama.cpp's slot API: `--slot-save-path ~/.config/carl/slots` (the launcher and the router presets set it) and `POST /slots/N?action=save|restore` with `{"filename"}` (router mode: also `"model"`, in the body; a `?model=` query is refused with HTTP 400). The clients do the work, through the server's API only, so it works from a VM too: `client/shared/carl-cache.js`, carried by the OpenCode plugin `carl-cache` (it wraps `fetch`: the AI SDK calls the global one; `chat.headers` marks our provider's requests with the session, the agent and whether it is a subagent) and the Pi extension `carl-cache` (`before_provider_request`, `message_end`). The dashboard (`tools/monitor/diskcache.py`), the launcher and `./carl.sh cache` keep the disk limit.

**Before each request:**
1. **The volatile parts move.** OpenCode's `<env>` block and the `Instructions from:` blocks of files in the working folder, and Pi's `<project_context>` and `<cwd>` blocks, move from the system message to the start of the first user message. The system text and the tools are then the same in every project and on every day. This matters most for templates that render the system text first and the tools after it (Gemma 4): otherwise the shared prefix would end at the environment block, before the tools.
2. **The slot.** The session's own slot when it is idle and no other session of this process ran there since (a process new to the session finds it through the slot records below); else an idle slot (an empty one first). The request is pinned there (`id_slot`; responses don't say which slot served them). A slot is claimed until the server has the request: within the process, and across processes on this Mac with a claim file (`.claim+MODEL+SLOT` in the slots folder, created only if absent, taken over after 2 min). Without a free, unclaimed slot the request isn't pinned and llama.cpp decides. Before the claim files, two processes reading the slot list at the same moment could pin the same slot; the second request then waited in llama.cpp's queue for the whole first request.
3. **The state.** For a session new to this process or server, or one whose slot another session took since its last save: its file goes in (`carl-session+MODEL+KEY+SESSION.bin`); else the agent's prompt file (`carl-prefix+MODEL+AGENT+HASH.bin`); else the agent's prompt is read once (`/completion` with exactly its tokens, `n_predict 0`) and saved. A slot that holds another conversation is first moved to the RAM cache (a 1-token task: llama.cpp saves the old prompt when a new task arrives). The agent's prompt is the common token prefix of the system text and tools rendered with two different user messages (`/apply-template`, `/tokenize` with `add_special`).
4. **Router mode:** a model that isn't loaded is loaded first (`POST /models/load`, asked again while the router is busy with another switch), so the state can go in before the request. A title or summary request for a model that isn't loaded goes to the loaded one (a switch for it would unload the session's model, and back).
5. **Title and summary requests** run with thinking off (`reasoning_effort: none`) and unpinned: with thinking on, an OpenCode title on the 35B held a slot for 35 s, and `opencode run` waited for it.
6. **A slot another session of the same process needs:** the session in it is first set back to the end of its last prompt and saved, and put back from its file when it returns. Measured: OpenCode delegating to its coder subagent while the other slot ran the title request; without this, the main session's next request read all 8,567 tokens (llama.cpp kept the coder's state, which shares OpenCode's tool list, over the main session's RAM copy, and the hybrid model could not roll back to the shared point); with it, 232 tokens. The whole run: 59 s before, 12 s after (with quick titles).

**After a turn** (a reply that is not a tool call; before the client sees the end, so `opencode run` and `pi -p` don't exit first), main sessions only (not subagents, titles, summaries or compactions) of at least 4,096 tokens, by `cache.save`:
- `turn`: saved. `auto` (default): saved when the tokens not in a file yet (the slot's tokens less the larger of the last save and the state that went in) would take `cache.auto_s` seconds (default 120) to read at this model's speed (measured when an agent's prompt is read; 500 tokens/s until then); else as `switch`. `switch` and `stop`: not saved; the slot is first set back where the template won't re-render the reply, then a **record** (`.resident+MODEL+SLOT.json`: the session's file, the slot's task id, how much a file already holds) says which session it holds.
- Records are saved from before the state leaves the server: the dashboard saves them before Stop, Apply, Auto-tune and the Router panel's Load / Unload (`jobs.save_before_stop`), a client before it makes the router load another model, each only while the slot's task id is unchanged. A slot another session of the same process needs is saved first in `auto`, `turn` and `switch`. KEY is a hash of the model file and the llama.cpp build (`/props`).

**Whether a state fits.** The hybrid Qwen models can't roll back their recurrent state, and a restored state carries no checkpoints: it helps only when the next prompt starts with all of its tokens; otherwise llama.cpp reads the whole prompt (and a restored state that doesn't fit also keeps the RAM cache from being used). A state saved right after a reply fits when the template re-renders the reply as the model generated it:
- Qwen with `preserve_thinking` (the 35B and 27B templates; CARL passes it): yes, thinking on and off.
- The 9B's template has no `preserve_thinking` (earlier reasoning dropped): `host/gguf-chat-template.py` adds it, as the newer templates have it.
- Gemma 4 drops earlier reasoning: the cache checks this once per model and thinking mode (rendering a probe conversation) and, where it doesn't fit, first sets the slot back to the end of the last prompt (`/completion` with that prompt's tokens), then saves.

**Sliding-window models.** Gemma 4 E4B (`attention.sliding_window` 512): a restored state was not used (the next request read all 5,100 tokens) until the server ran with `--swa-full`; then 100 of 5,100. `--swa-full` keeps every layer at full length: llama.cpp projected 4,766 MiB without it and 7,099 MiB with it (2 × 96K, q4_0). The memory estimate reads the GGUF's `attention.sliding_window_pattern` (35 of Gemma 4 E4B's 42 layers), `shared_kv_layers` (the last 18 reuse earlier layers' KV) and the sliding-window key / value lengths: the 24 layers with their own KV are 4 full-attention (8,192 elements per token) and 20 sliding-window (20,480), full length with `--swa-full`, else window + 512 per slot. Before, every layer counted at full length with the global head size (86,016 per token, about 3× too much). `cache.swa` (`SWA_MODE` for the launcher): `auto` = `--swa-full` when it fits with the slots `llama-fit.py --plan` gives (a second slot first), `full`, `window`; the router presets the same per model (`swa-full = true`).

| Measured (llama.cpp b11146, q4_0, 2 × 96K) | Read | Instead of |
|---|---|---|
| 35B IQ3, OpenCode: a new session, the build prompt read once (8,227 tokens, 12.9 s) | 207 tokens | ~8.4K |
| 35B IQ3, OpenCode: the session after a router switch away and back | 23 tokens | ~8.5K |
| 9B, Pi, thinking off: the session's 2nd turn in a new process | 23 tokens | ~5K |
| Gemma 4 E4B, OpenCode: the session after a server restart | 21 tokens | ~7.8K |
| Gemma 4 E4B, Pi: the session after a server restart | 20 tokens | ~4.5K |
| 35B IQ3, OpenCode, a real config (E2E): an 11.5K-token session after a server restart | 22 tokens | 11.5K |
| 35B IQ3, Pi: a 7.4K-token session after a restart, cache on / off | 24 tokens, 0.2 s | 7,443 tokens, 10.9 s |
| Router (E2E): sessions after switches 35B → 9B → Gemma → 9B → 35B → Gemma | 22-63 tokens | 6.8-11.6K |
| `auto` (E2E, 35B): turns of ~270-360 tokens not saved; a new process found the session in its slot (record), a dashboard Stop saved it, the turn after the restart | 20, then 18 tokens | 8.5K |
| Restore of a 118 MB file (35B) / 29 MB (Gemma, 5K tokens) | 15-30 ms / 5 ms | |

**Disk.** `cache.disk_gb` (default 10): after each save on this Mac the client removes the oldest conversations first, then the oldest prompts (the file just saved stays unless it alone is over the limit); the same agent's older prompt files go at once. The dashboard checks the limit every minute and the launcher at every start (files written from a VM). No saves below 10 GB free. Phase 11's files (the dashboard's pre-read and per-slot saves, named with dashes) are removed.

## When an agent's prompt changes

- The client checks the prompt at each request. The check is a hash of the system text, the tools and the template fields, in memory. It costs no request to the server.
- The prompt changes when you add or remove a tool or an MCP server, change a global instruction file, or update OpenCode or Pi. The date, the folder and the project's AGENTS.md do not change it: they move to the first message (step 1 above).
- After a change, the next new session reads the new prompt one time and saves it as a new file. A new name follows from the hash. This read costs the same time as a session without the cache.
- On this Mac, the client then removes the older prompt files of the same agent and model. The dashboard's budget removes them on the server for clients on other computers.
- A saved conversation that started with the old prompt cannot continue from its file. Its first tokens are different now. llama.cpp reads the conversation again one time. The tidy step removes its patch, because the base is gone.

## Shared pieces (dedup)

The page [How the pieces fit](pieces.md) tells how a saved state is built and which parts are shared.

- A conversation is stored as a zstd patch against the agent's prompt file that it starts with (`tools/monitor/slotpack.py`, `cache.share`, default on). The prompt files stay whole: they are the bases.
- The dashboard makes the patches in its minute loop, and `./carl.sh cache trim` makes them too. A new file waits 30 s. A patch is kept only when it is at most 80% of the file.
- Before a restore, the file is made whole again: by the client on this Mac (zstd), or by the dashboard's API for a client on another computer (`POST /carl/cache/unpack`). The copy gets the patch time as its time, so the tidy step removes it after one minute.
- When the budget removes a prompt file, it removes the patches that need it.
- zstd is a dependency now (`brew install zstd`; `ensure_deps` offers it). Without zstd, the files stay whole.

| Measured (35B IQ3, 2026-10-04) | Size |
|---|---|
| A build session, 8.6K tokens, whole | 115.2 MB |
| The same session as a patch against the build prompt | 64.1 MB (0.3 s to make, 0.1 s to undo, the same bytes) |
| The 7 conversations on disk, whole / as patches | 1.5 GB / 1.2 GB |
| A restore from a patch after a server restart (E2E) | 100 tokens read |

The patches save disk space. They do not save drive writes: llama.cpp writes the whole file first. The write saving needs incremental saves (v2 in the plan).


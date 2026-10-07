# CARL Reference: Glossary

[Index](README.md) · the words and units CARL uses: one name for each thing, on every screen (the dashboard, the CLI, `/carl`, the OpenCode and Pi messages) and in the docs.

- Config keys, flags and file names do not change: they are an interface. The screens use the names below, and show a key or a flag only in the full detail level (`D` in the dashboard switches between simple and full).
- A word names one thing only. The "Not this" column gives the names that CARL does not use for that thing.
- Each number has its unit. In simple detail, a short sentence tells what the number means.
- `auto` is never shown alone: CARL always shows what it chose, for example `slots: auto (2)`.
- The code follows this page: `tools/carl_core/domain/units.py` (the units), `tools/monitor/words.py` (the dashboard's words), and the tests that check the screens (`tests/monitor/test_screens.py`, `tests/scripts/test_cli_text.py`).

## 1. The server and its memory

| Name | Meaning (simple detail) | Not this | Full detail adds |
|---|---|---|---|
| **server** | The llama.cpp program that runs the model. | llama-server (except in commands), "the model" | `llama-server`, its PID and flags |
| **slot** | A place in the server for one session. Each slot has its own context. 2 slots = the main session and a subagent work at the same time. | parallel, conversation slot | `--parallel` |
| **context** | How many tokens one slot can hold, for example `context: 96K tokens per slot`. | window (for this), ctx, n_ctx, context size, tokens per slot, max ctx, fits (column) | `ctx`, `-c` = slots × context |
| **context memory** | The memory that holds the context of all slots. | KV cache, KV RAM, KV memory, KV quant, "context (KV + state)" | "KV cache", its type `q4_0` / `q8_0` |
| **context memory type** | `q4` (smaller, the default) or `q8` (larger). | KV type, cache type, KV quant, q4_0 | `-ctk q4_0 -ctv q4_0` |
| **weights** | The model file in memory. | size (for memory), bytes | the file name |
| **memory needed** | weights + drafter + context memory + buffers. | need, needs X of Y | each part |
| **recurrent state** | (full detail only) Memory that hybrid models (Qwen3.6, Qwen3.8) keep for each slot. | state (alone) | checkpoints, `--ctx-checkpoints` |

## 2. Gemma's sliding window

| Name | Meaning | Not this | Full detail adds |
|---|---|---|---|
| **sliding window** | Most Gemma layers look only at the last 1,024 tokens (the E4B: 512). | window (alone), SWA | `SWA`, the window size |
| **full cache** | Those layers keep every token. Uses much more memory and reads prompts more slowly, but CARL can restore saved sessions and prompts. | full-length, `--swa-full` | `--swa-full`, `cache.swa = full` |
| **window cache** | Those layers keep only the last window. Small and fast, but CARL cannot restore saved sessions and prompts. | window only, window-only | `cache.swa = window` |

"Window" is used only for the sliding window. The context of a slot is never called a window.

## 3. What CARL saves (the cache words)

| Name | Meaning | Not this | Full detail adds |
|---|---|---|---|
| **RAM cache** | The server keeps sessions that leave a slot in RAM while it runs. | prompt cache, RAM prompt cache, parked | `--cache-ram`, its size in MB |
| **disk cache** | CARL saves prompts and sessions on the disk. They stay after a stop, a restart or a model switch. | prompt cache (in /carl), state files, slot-save-path | the folder, `--slot-save-path` |
| **saved prompt** | An agent's system prompt and tools, read one time and saved. A new session starts from it. | prefix, prompt state, pre-read prompt | `cache.prefix`, the file name |
| **saved session** | One session at the end of a turn. CARL restores it when the session continues. | conversation, prompt state, saved state, patch | stored as changes to its prompt ("shared"), `cache.sessions` |
| **reused tokens** | Tokens of a request that the server did not have to read again. | cached (Live tab) | the count and the %. |
| **Caching** | The Settings panel for the RAM cache, the disk cache and the sliding-window cache. | — | — |
| **dashboard API** | The port (server port + 1) that clients on other computers use for the disk cache and the config. | cache API, API (alone) | the address |

"Prompt cache" and "cache" alone are not used. "Prompt" alone means the text of a request (the Live tab).

## 4. Speculation

| Name | Meaning | Not this | Full detail adds |
|---|---|---|---|
| **speculation** | The server guesses tokens ahead and checks them. More speed, the same answer. | spec, speculative decoding (simple) | `--spec-type` |
| Modes: **none**, **n-gram**, **MTP**, **MTP + n-gram** | n-gram: copies text that is already in the context (fast for edits). MTP: a small predictor guesses new text. | ngram-mod, draft-mtp, draft-mtp,ngram-mod, MTP+n-gram | the llama.cpp names |
| **guesses** | Tokens the speculation guesses for each step: `MTP + n-gram, 2 guesses`. | draft tokens, n=2, `:2`, spec_n | `--spec-draft-n-max 2` |
| **guesses accepted** | The % of guesses that were correct. | drafts accepted, draft acceptance | per draft |
| **MTP head** | The MTP predictor inside the model file (Qwen). | nextn | — |
| **MTP drafter** | The MTP predictor in a separate small file (Gemma 4). | draft model, drafter (alone in simple), mtp-*.gguf | the file, `-md` |

## 5. Choosing and tuning the model

| Name | Meaning | Not this | Full detail adds |
|---|---|---|---|
| **Auto fit** | CARL's choice: the best catalogue model that fits this Mac, with its slots and context. | auto-fit, autofit, the pick, plan | the passes, `llama.auto_fit` |
| **goal** | `everyday` (fast first) or `hard code` (better code, slower). | hard-code (in the UI) | `llama.auto_goal` |
| **candidates** | `catalogue` or `downloaded only`: the models Auto fit looks at. | scope, From, auto from | `llama.auto_fit` |
| **fits / does not fit** | Only for memory: the model, its slots and its context are inside the GPU memory limit. | fit (as a noun), fits (column) | the numbers |
| **quality rank** | CARL's quality order, 1 = best. | rank (alone), quality (alone), best-ranked | where the rank comes from |
| **Auto-tune** | CARL measures a model on this Mac and finds its best settings. | auto-tune, tune (in the UI) | `./carl.sh tune` |
| **recommended settings** | The settings for a model: from Auto-tune on this Mac, else from the catalogue. Shown with their source. | Tuned values, Use these values, Recommended for this model | per setting: its source |
| **speed** | tok/s. A model's speed in lists = the mean of its prose, code and edit speeds (Auto-tune's weights). | score, t/s | the three speeds |
| **edit** | Writing a file again with small changes (n-gram is fast here). | re-emit | — |
| **read speed / write speed** | How fast the server reads a prompt / writes the answer (tok/s). | prefill, prompt processing, pp, decode, generate, tg, gen/s | the llama.cpp names |
| **context zones** | `fast` / `slow` / `very slow`: how long a full context takes to read again on this Mac. | zones | the limits in tokens |

## 6. Clients at work

| Name | Meaning | Not this | Full detail adds |
|---|---|---|---|
| **client** | OpenCode or Pi (the programs). | — (not a computer, not a connection) | — |
| **other computer** | A computer that uses this Mac's server (Connect > Clients). | client (for this), remote | its address |
| **connection** | One open network connection to the server. | client (for this) | — |
| **session** | One conversation in OpenCode or Pi. | conversation, resident | the session ID |
| **agent / subagent / coder** | The main agent of a session / an agent it starts / CARL's coder subagent. | — | the agent file |
| **turn** | One task of an agent: from your message to its answer, with all its tool calls. | request (for this), reply | — |
| **request** | One call from a client to the server (a turn has many). | — | — |
| **wait for the turn** | CARL stops or switches the model only after the turn ends, so the session is saved whole. | drain | — |
| **thinking** | The model thinks before it answers. On / off, or levels. | reasoning (in the UI), effort (alone) | `--reasoning-format` |

## 7. Modes, setup and sync

| Name | Meaning | Not this | Full detail adds |
|---|---|---|---|
| **single model** | One model runs. You change it in the dashboard. | single mode, dashboard only, model switching: single | `llama.mode = single` |
| **router mode** | OpenCode and Pi can switch the model. Each switch loads the model again. | model switching, multi-model | `llama.mode = router`, `--router` |
| **dashboard** | CARL's console (`./carl.sh`). | monitor, console, TUI | — |
| **CARL provider** | The entry in the OpenCode / Pi config that points at this server. | provider llamacpp / provider block | its ID |
| **install / update** | Install: the first time. Update: the configs again. | — | the command |
| **send config / apply** | The dashboard sends the client config to other computers; they apply it. | push, pushed, auto-apply | the version hash |
| **GPU memory limit** | The memory macOS lets the GPU use. | GPU limit, GPU memory, recommendedMaxWorkingSetSize | the sysctl |
| **kept free** | Memory CARL keeps for macOS and apps (6 GiB; 10 GiB when the VM network is up). | reserve, allowed, budget | `RESERVE_GB` |

## 8. Models

| Name | Meaning | Not this |
|---|---|---|
| **model name** | The ID (`gemma-4-12b`). Used in commands and lists. | alias (except the server's own) |
| **label** | The readable name (`Gemma 4 12B · Q4_0 QAT`). Card titles. | — |
| **quantization** | `Q4_0`, `IQ3_XXS`… | quant, file type |
| **catalogue** | CARL's list of tested models. | catalog (except the file name) |
| **custom model** | A model that is not in the catalogue (Hugging Face or the models folder). | file (as a source) |
| **downloaded / not downloaded / partial / bad file** | The download status. | missing, not here, ○ not, here: no, .bad |
| **stock / abliterated** | Unchanged / safety training removed. "uncensored" only as the "good for" tag. | — |
| **MoE / dense** | MoE: a few experts work on each token (fast). Dense: all weights work. | MOE, moe (in the UI) |

## 9. Units

| Thing | Unit | Example | Not this |
|---|---|---|---|
| File and download sizes, disk | **GB** (1000³ bytes), as Hugging Face and Finder show them | `14.6 GB` | G, a 1024³ value called GB |
| Memory (RAM, GPU, context memory, memory needed, kept free) | **GiB** (1024³ bytes), one decimal | `13.6 GiB` | G, GB for memory |
| Small sizes | **MB** for files, **MiB** for memory | `280 MB`, `512 MiB` | |
| Tokens | **K = 1024** | `96K` | 98.3K |
| Speed | **tok/s** | `56 tok/s` | t/s |
| Time | `1.2 s`, `3 min`, `17:30` | | |

The first time a screen shows GiB in simple detail, the line says "GiB (memory: 1 GiB = 1.07 GB)".

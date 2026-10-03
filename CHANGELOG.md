# Changelog

All notable changes to CARL. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Dates are local dates on the development Mac (M3 Pro, 36 GB).

## 1.1.0 - 2026-10-03

### Added
- **Model catalogue: `host/catalog.json`** (replaces `host/models.conf`). For each model: the Hugging Face source (pinned revision, SHA-256, size), a summary and a description, the tuned settings (`tune`: KV, context, slots, speculation, draft tokens, sampling), the reason for each tuned value (`why`), context zones (`ctx_zones`) and measurements (`measured`). `default` and `default_small` select the default models.
- **`tools/carl.py`**: one place for the catalogue, the models on this Mac and the settings. `host/models.sh` is now a thin wrapper around it.
- **Custom models.**
  - Each `.gguf` in `~/models/gguf` is listed as a model, also if it is not in the catalogue. It gets its first settings from its GGUF header (MTP + n-gram n=1 with an MTP head, else n-gram n=2).
  - `./carl.sh download hf:OWNER/REPO/FILE.gguf` downloads any GGUF from Hugging Face. It gets the revision, the size and the SHA-256 from the Hugging Face API, and verifies the file. `./carl.sh download hf:OWNER/REPO` lists the GGUF files of a repo.
  - Custom models and the Auto-tune results are in `~/.config/llm-deploy/models.json`.
- **Settings file: `~/.config/llm-deploy/config.json`** (replaces `llama.env` and `mtplx.env`; CARL copies their values into it one time).
  - Sections: `backend`, `llama` (model, net, host, cache_ram, ub, batch, ckpt, ckpt_step, think_toggle, extra_args), `models.<name>` (a profile for each model: kv, ctx, slots, spec, spec_n, temp, top_p, top_k, min_p, presence, repeat, alias), `mtplx`, `paths` (models_dir).
  - CARL validates each value. `./carl.sh config show|get|set|unset|path`.
  - Order of priority: flags > environment > config.json > Auto-tune (this Mac) > catalogue > built-in defaults. `SETTINGS_FILE=none` / `SETTINGS_FILE_MTPLX=none` ignore the file.
- **Auto-tune: `./carl.sh tune NAME [--quick]`** (`tools/carl-tune.py`). It measures the memory fit, the speculation modes (prose, code, re-emit) and the prompt reading at 8K/32K/64K (gives the context zones of this Mac). It saves the result in `models.json`, and each later start of the model uses it. It needs the GPU for itself: it refuses to run while a server or another large process is in memory (`ALLOW_SECOND_MODEL=1` skips the memory check).
- **Dashboard: the Settings tab has three panels** (`[` and `]` change the panel).
  - **Server:** a model list (Enter) with every model. The values have colours: green = tuned / fast, yellow = changed / slower, red = very slow / no MTP head / MTP with n>1 on an IQ quant. The right side explains the model and why each value is tuned.
  - **Models:** the catalogue and the models folder. Download (with progress), verify, delete, add from Hugging Face.
  - **Auto-tune:** run it, and see the last results. It stops the running server, and starts it again after the tune.
- **New catalogue model: `qwen3.8-27b-iq3`** (unsloth UD-IQ3_XXS, 10.9 GB), the smallest 27B. On a 24 GB Mac, 2 × 96K slots fit.
- **IQ3 speculation measured** (M2 Max 32 GB, llama.cpp 0.5.0, both IQ3 builds): MTP alone helps little on IQ3 (about nothing on the 35B, ~5–10% on the 27B), and n=2 costs ~15% on new text. MTP + n-gram with n=1 is the best on both models (about +5% over n-gram alone on the 35B, +9% on the 27B). Both IQ3 builds now use `draft-mtp,ngram-mod`, n=1; before, the 35B IQ3 used n=2 (copied from the Q4). The Q4 35B keeps n=2 (REFERENCE.md, "IQ3 speculation").
- **`./carl.sh delete NAME`** deletes a model file. `./carl.sh verify` with no names verifies every downloaded model.
- **Unit tests:** `python3 -m unittest discover -s tests` (domain, app, adapters) and `python3 -m unittest discover -s tests/scripts` (shell helpers, `client/configure.py`, the small tools).
- **Dependency check.** `./carl.sh` looks for `llama-server`, `aria2` and `ansifilter`, and offers to `brew install` the missing tools. `SKIP_DEPS=1` skips the check.
- **No model downloaded:** `./carl.sh` offers to download the default for this Mac, or opens the dashboard without a server.

### Changed
- **OpenCode subagents panel:** the finished subagents are below the running ones (✓/✗ and the duration). It shows the 5 newest for 5 minutes, then a `+N more` line. It resets when you go to a different parent session. Run `client/install.sh` again to install it.
- **A default model that is not downloaded** falls back to the first downloaded model.
- The REFERENCE.md IQ section: the IQ3 builds are now in the catalogue (for Macs where nothing larger fits). IQ quants are still not used on 36 GB.

- **96K context for each slot is the floor of the default window** (users need that much context to work). The catalogue now gives 96K to `qwen3.8-27b-q3` and `qwen3.8-27b-iq3` (before: 64K), and the dense context zones start at 96K (96K / 128K / 160K). Custom models start at 96K (less only if the model was trained for less). The Settings tab never shows 96K or less in yellow or red; it warns only about larger windows. Auto-tune never selects less than 96K if 96K fits one slot. Above 96K, it keeps the catalogue window while a cold read of it is no worse than "slow" on this Mac and it fits. One exception: `orcarouter-27b-q3` keeps 64K. It exists only for 24 GB Macs, where 96K does not fit (~68K, estimate); larger Macs use the Q4 build. A start of a model whose catalogue window is below 96K, with no Auto-tune result and no window you set, prints a note to run `./carl.sh tune NAME`, and the fit warning (model too big for the window) also suggests a tune.
- **Code layout:** the logic of `tools/carl.py` and `tools/gguf_shape.py` moved to the package `tools/carl_core/` (a pure `domain/`, `adapters/` for the I/O, `app.py`, `wiring.py`). The two files are now thin facades. `tools/carl_bench.py` holds the helpers that the small benchmark tools share. `host/common.sh` has `apply_settings` (an allow-listed `KEY=value` reader), `require_int` and `is_port`.
- **Benchmarks:** the Auto-tune re-emit workload now copies `tools/carl_core/adapters/llama_server.py`, and the edit workload of `llama-spec-bench.py` re-emits its own rewritten source. Their re-emit / edit numbers are not directly comparable with older results.
- **The API key stays out of the process list:** `client/install.sh` gives it to `curl` through a header file (`curl -H @file`, curl 7.55 or later). `tools/sesstest.py` takes KEY `-` to read the key file. `tools/req-capture-proxy.py` refuses wildcard listen addresses, and writes its log with mode 600.
- **OpenCode plugin `mtplx-session-headers` has a second file, `mtplx.js`.** A manual copy of the plugin must include it (`install.sh` does this).

### Removed
- `host/models.conf` (replaced by `host/catalog.json`).

### Fixed
- The monitor crashed on its first refresh when `pmset -g assertions` printed a byte that is not UTF-8 (a Bluetooth device name such as a curly apostrophe in "Name’s Magic Keyboard"); command output is now decoded with replacement.
- **The monitor and `./carl.sh` froze, and Ctrl-C did not stop them.** `lsof` hangs (and cannot be stopped) on a stale network share, for example a disconnected Time Machine SMB volume. CARL no longer uses `lsof`: port and PID lookups use `netstat -anv`.
- The shell and Python scripts are executable in the repository (a fresh clone could not run `./carl.sh`).
- Monitor: a mouse report or key sequence split across two reads no longer shows up as key presses. Mouse reporting is always turned off on exit.
- The first start on a Mac without `~/.mtplx/api-key` no longer ends silently (`pipefail` and SIGPIPE in `ensure_api_key`).
- OpenCode `mtplx-session-headers`: it read the provider id from the wrong field (`input.provider.id`, now `provider.info.id`), so its fallback never matched. Its `server.js` also had a `setup` function for a session hook that OpenCode 1.18.34 does not have, and it threw an error; it is removed (the V1 hooks already send the headers). Header values are validated (no CR/LF).
- Pi: the MTPLX models offered thinking level `high`, which MTPLX turns into `xhigh` (Qwen3.8 has no `high`); it is hidden now, as in OpenCode.
- OpenCode session switcher: it now redraws on question events.
- Pi subagent extension: it no longer crashes on a JSON `null` line.
- `tools/llama-sesstest.py` and `tools/llama-kv-longctx.py` no longer crash on the first turn (an int PID was passed to `subprocess`).

## 1.0.0 - 2026-10-02

The first release under the name CARL (Can't Afford Remote LLMs). Before this release, the project was called LLM-Deploy (and Qwen-Llama-Deploy before that).

### Added
- **`./carl.sh`**, a launcher in the project root. It does the same as `./host/serve.sh`, which continues to work.
  - `./carl.sh` with no arguments opens the dashboard. If a server runs, the dashboard attaches to it. If not, CARL starts the backend that you used last, with your saved settings. The first time, it starts llama.cpp with the defaults.
  - `./carl.sh -h` shows the help.
  - `./carl.sh --no-start` (or `./carl.sh dashboard`) opens only the dashboard. If no server runs, nothing loads; the Settings tab can start a server.
  - `./carl.sh install [both|opencode|pi]` installs OpenCode and/or Pi and writes their configs in one step. Options: `--local`, `--vm [HOST]`, `--host ADDR`, `--clients-only`, `--config-only`.
- **Launch guard.** The launchers do not start a server when another large process (more than 8 GB, `BIG_GB`) or a known model server is in memory. Two models do not fit in the GPU memory, and a second model can stop the Mac. `ALLOW_SECOND_MODEL=1` skips the check.
- **Dashboard: MTPLX support.** All cards show MTPLX data from `/v1/mtplx/snapshot` and `/v1/mtplx/flight`: request progress and ETA, the KV cache, the session bank (RAM and SSD), MLX memory, profile, MTP depth, draft acceptance by depth, and the finished requests.
- **Dashboard: Settings tab (tab 5).**
  - Select the backend (llama.cpp or MTPLX), the model, the KV cache, the context, the slots, the RAM prompt cache, the network and the sampling. Push `a` to restart the server with the new settings.
  - A fit check shows if the setup fits in the GPU memory. If the new server does not start, the old server starts again.
  - The settings are saved in `~/.config/llm-deploy/llama.env` and `mtplx.env`. The launchers read them (flags > environment > file > defaults).
  - An **advanced** section: top_k, top_p, min_p, repeat penalty, draft tokens, batch size and context checkpoints (llama.cpp); scheduler, batching, prefill chunk and SSD session cache (MTPLX). You can type exact values. A warning tells you that the defaults are tuned.
- **Dashboard: logo and layout.**
  - The CARL logo in the header (iTerm2, Ghostty, WezTerm, kitty); other terminals show 😎. `CARL_LOGO=0` turns it off.
  - The cards keep a fixed height. A value that is not known shows `0`, `N/A` or `none`.
  - Dots after each card title show its detail level (`○○`, `●○`, `●●`).
- **OpenCode session switcher** (TUI plugin). `‹ 2/3 ● title ›` in the prompt box: the arrows go to the previous or next session, and the title or `/switch` opens a list with the state of each session. OpenCode 1.18.34 does not show sessions as tabs.
- **The coder subagent on MTPLX.** `install.sh` now also installs the coder when the server is MTPLX. MTPLX keeps each session in its session bank, so the main session comes back after a subagent in 3.8 s (RAM) to 14.8 s (SSD), not a full re-read.
- **Other computers and VM apps.** `--host ADDR` serves on one address of this Mac (its LAN address, or a Parallels network). The dashboard's network row lists the addresses of this Mac. `install.sh --key-file FILE` (or `--key KEY`) gives the API key to a client on another computer.
- **Coder subagent tuning.** The OpenCode coder uses temperature 0.6 with thinking on: the best of 9 measured runs (all functions typed, more tests, 26% faster than 1.0). The 27B uses the same value without its own test. The coder now checks that the packaging files it writes actually build.
- **Installer backups.** Before the installer changes an existing config file, it keeps your original as `FILE.before-carl` (never overwritten) and a copy of each version as `FILE.bak.<time>`. `APPEND_SYSTEM.md` of Pi is now included.
- `tools/tuishot.py`: makes the README screenshots and GIFs from the real terminal programs.
- README screenshots: the dashboard (GIF), the Settings tab, OpenCode with the plugins.

### Changed
- The README is short: what CARL is, install, use, pictures. All its other content moved to USERGUIDE.md and REFERENCE.md (new sections: Architecture, Repository layout, Server flags, OpenCode config, Performance).
- The share zip is `CARL-YYYYMMDD.zip` with one folder, `CARL/`.
- The help, the dashboard and the installer messages use `./carl.sh`.
- The context cache tests found no gain from a larger RAM prompt cache or more checkpoints. The defaults stay (REFERENCE.md, "RAM prompt cache and checkpoints").

### Fixed
- The dashboard hid the llama.cpp log and request list, because the llama-server command line contains `~/.mtplx/api-key`.
- With MTPLX, the dashboard showed an old llama.cpp log. It now shows the MTPLX console output, or "no log file".
- Test servers on other ports wrote the "last used backend" file.

### Known issues
- MTPLX: 56K for two sessions does not fit on a 36 GB Mac (the allocator went to 106%, and the main session was read again). Use 48K.
- OpenCode 1.18.34 has `/variants` (and `ctrl+t`) instead of `/effort` for the thinking level.
- When the server runs the 27B, a client that sends `reasoning_effort: "high"` (the 35B setting, for example the OpenCode title agent) gets HTTP 500 from the 27B chat template.

## Earlier work (as LLM-Deploy)

### 2026-10-01
- **Subagents.** 2 server slots by default (when they fit), so the main OpenCode session and a subagent each keep their cache. A specialist **coder** subagent for OpenCode and Pi, and a rule that tells the main agent when to use it. The coder is installed only when the server has 2 slots.
- **Subagents panel** for OpenCode (TUI plugin).
- **Installer that does not overwrite your configs** (`client/configure.py`). Your providers, default model, agents and plugins stay. CARL items are tracked in `llm-deploy.json`.
- **Qwen3.6-35B-A3B is the default model** on every Mac. The IQ3 build is the default on 24 GB Macs.
- **96K context for each slot** by default. 128K–160K are possible with `--ctx` (measured costs in REFERENCE.md).
- The user documents are in ASD-STE100 Simplified Technical English.

### 2026-09-25
- Qwen3.6-35B-A3B (MoE) added and measured: about 4× faster decode and 6× faster prompt read than the 27B.
- "Thinking off" works from OpenCode (a patched chat template that maps `reasoning_effort: none` to no thinking).
- The installer replaces the providers that it manages, so old model options do not stay in the configs.
- Flash attention checked and kept on (necessary for a quantized V cache).

### 2026-09-24
- llama.cpp backend (`serve-llama.sh`): q4_0 KV cache by default (+16% prompt read, −2 GB, the same decode speed as q8_0), MTP speculative decoding.

### 2026-09-23
- MTPLX presets `grant` and `pocket` (abliterated Qwen3.8-27B) at 48K, served to OpenCode and Pi in a VMware Fusion VM.
- Research: the MTPLX quantized KV cache does not hold long sessions. Research on llama.cpp and oMLX.


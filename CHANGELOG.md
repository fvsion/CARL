# Changelog

All notable changes to CARL. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Dates are local dates on the development Mac (M3 Pro, 36 GB).

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


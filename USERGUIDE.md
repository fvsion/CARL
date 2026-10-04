# CARL User Guide

CARL: Can't Afford Remote LLMs.

CARL runs a local coding model (Qwen or Gemma 4) on an Apple Silicon Mac. You use the model from OpenCode or Pi. This guide tells you how to set up, run and use CARL.

- The clients can run on the Mac, in a VMware Fusion VM on the Mac, or on another computer.
- For the technical details (the architecture, the files, the server, thinking, the models, the measurements), see the [reference](reference/README.md).
- For the short overview, see [README.md](README.md).

Commands with the label **Mac** run in the CARL folder on the Mac. Commands with the label **VM** run in the VM. Without a VM, all commands run on the Mac.

## Contents

1. [First-time setup](#1-first-time-setup)
2. [Daily use](#2-daily-use)
3. [Choosing a model](#3-choosing-a-model)
4. [Thinking on, off and effort](#4-thinking-on-off-and-effort)
5. [Context window](#5-context-window)
6. [Working in OpenCode and Pi](#6-working-in-opencode-and-pi)
7. [Fast starts: the disk cache](#7-fast-starts-the-disk-cache)
8. [KV cache: q4 or q8](#8-kv-cache-q4-or-q8)
9. [Downloading and adding models](#9-downloading-and-adding-models)
10. [The dashboard](#10-the-dashboard)
11. [Updating the client configs](#11-updating-the-client-configs)
12. [Troubleshooting](#12-troubleshooting)
13. [Benchmarking and testing](#13-benchmarking-and-testing)
14. [Rules of thumb](#14-rules-of-thumb)
15. [Command reference](#15-command-reference)

---

## 1. First-time setup

### Requirements

| Item | Requirement |
|---|---|
| Mac | An **Apple Silicon** Mac. A 36 GB Mac runs all Qwen models of the catalogue. A 24 GB Mac runs the IQ3 and Q3 builds ([Sharing with a friend](#sharing-with-a-friend)). A 16 GB Mac runs `qwen3.8-9b` and `gemma-4-e4b`. |
| Homebrew tools | [Homebrew](https://brew.sh), for `llama.cpp` (tested with 0.4.1 and 0.5.0), `aria2`, `ansifilter` and `zstd` |
| Python | `python3`. The first time that you use it, macOS offers to install it with the command-line developer tools. |
| Swift | The `fit` command uses `swift` to read the exact GPU limit. It comes with the command-line developer tools (`xcode-select --install`). |
| Disk space | 4.6–23 GB for each model, and up to 10 GB for the disk cache |
| VM (optional) | VMware Fusion with NAT (vmnet8), for VM clients |

### Set up the Mac

1. Install the tools:
   ```bash
   brew install llama.cpp aria2 ansifilter zstd
   ```
   | Tool | Use |
   |---|---|
   | `llama.cpp` | Gives `llama-server`. CARL cannot run without it. |
   | `aria2` | Makes downloads faster |
   | `ansifilter` | Makes the logs easy to read |
   | `zstd` | Stores the conversations of the disk cache as patches ([the disk cache](#7-fast-starts-the-disk-cache)) |

   - If you forget this step, `./carl.sh` finds the missing tools. It asks to install them with Homebrew.
   - `SKIP_DEPS=1` skips this check.
   - CARL does not install Homebrew itself.
2. Look at the model that **auto fit** picks for this Mac:
   ```bash
   ./carl.sh fit
   ```
   The command shows the pick and why. It also shows the largest context window of each model that fits in the GPU memory.
3. Download the model:
   ```bash
   ./carl.sh download default
   ```
   - `default` is auto fit's pick from the whole catalogue for the everyday goal ([Auto fit](#auto-fit-the-best-model-for-this-mac)).
   - On a Mac with 32 GB or more, the pick is `qwen3.6-35b-a3b`. On a 24 GB Mac, it is `qwen3.6-35b-a3b-iq3`. On a 16 GB Mac, it is `qwen3.8-9b`.
   - If you forget this step, `./carl.sh` tells you that no model is downloaded. It shows auto fit's pick and its size, and asks to download it. If you answer no, the dashboard opens without a server.
   - If the pick is not downloaded but a different model is, the server starts with the best downloaded stock model that fits. The start-up output tells you this, and how to download the pick.
4. Start the server:
   ```bash
   ./carl.sh llama            # this Mac only (127.0.0.1): the default
   ./carl.sh llama --vm       # for a VMware Fusion VM client too (fails if Fusion's network is down)
   ```
   - The server starts in the background. Then the **dashboard uses this terminal** ([The dashboard](#10-the-dashboard)).
   - The status shows "loading model…" for 10–60 s, then "idle".
   - The start-up banner shows the network mode and the values that the server selected. For example: `slots: 2 (auto) x 98304 tokens, KV q4_0/q4_0, RAM prompt cache 2560 MiB`.
   - `./carl.sh` with no arguments also starts the server, and opens the dashboard ([Daily use](#2-daily-use)).
   - `./carl.sh -h` shows the help. `./carl.sh help llama` shows the options of one command.
5. Install the clients ([Clients on the same Mac](#clients-on-the-same-mac-no-vm)).

**The API key.** The first server start makes the API key, if it is missing.

| Item | Value |
|---|---|
| Key file | `~/.config/carl/api-key`, mode 600, in a folder with mode 700 (the same folder as `config.json`) |
| Configs from `install.sh` | They refer to the key file. They do not contain the key. |
| Key from before 1.2.0 | The first start copies your old key (`~/.mtplx/api-key`) to the new path. It is the same key, so your clients continue to work. |

**The network.** The server serves this Mac only (127.0.0.1) by default.

- For clients in a VMware Fusion VM, start Fusion first. Then start the server with `--vm`. The server then listens on the Fusion NAT address `192.168.42.1`. This address exists only while the Fusion network is up.
- To make the VM the default, set **network** to `vm` in the Settings tab. Or run `./carl.sh config set llama.net vm`.
- Before 1.3.0, the default was "auto": the VM address when the Fusion network was up. The server was then open to the VM network without a question, so CARL removed this mode. A saved `llama.net = auto` changes to local one time, with a note at the start. `NET=auto` means local.

**The client folder.** At each server start, the launcher writes two files into the `client/` folder: `remote.json` (the server's address) and `api-key`. Both have mode 0600, and git ignores them. A copy of the folder then has all that the installer needs ([Clients on other computers](reference/client-sync.md)).

### Clients on the same Mac (no VM)

Use this procedure when the server and the clients run on the same Mac, for example the Mac of a friend.

1. Do the steps in [Set up the Mac](#set-up-the-mac).
2. Run the installer:
   ```bash
   ./carl.sh install
   ```
   It installs OpenCode and Pi into `~/.local` (no sudo). Then it connects them to the server on this Mac.
3. Open a new terminal, so that `~/.local/bin` is on the `PATH`.
4. Go to the folder of your project:
   ```bash
   cd ~/path/to/your/project
   ```
5. Start a client there:
   ```bash
   opencode
   ```
   Or run `pi`. The client works on the folder that you start it in.

**Installer options:**

| Command | Does |
|---|---|
| `./carl.sh install opencode` (or `pi`) | Installs only one client, then writes the configs |
| `./carl.sh install --config-only` | Writes only the configs |
| `./carl.sh install --clients-only` | Installs only the clients |
| `./carl.sh install --vm` or `--host ADDR` | Connects the clients to a different address |
| `./carl.sh install --port N` | Connects the clients to a different llama.cpp port |

**From the dashboard.** The Connect tab (tab 2) does the same:
1. Push `2` for the Connect tab.
2. Push `i` (**[ Install on this Mac ]**). Or push `u` (**[ Update configs only ]**) to write only the configs.
3. Push `y` to confirm.

The installer's output shows in the tab.

**The two steps by hand.** `./carl.sh install` runs these two commands from the CARL folder:
```bash
./client/install-clients.sh       # OpenCode + Pi into ~/.local (gets Node 22 for macOS if necessary)
./client/install.sh --local       # configs for the server on this Mac, with its own key file
```
- On a Mac, `install.sh` uses `--local` by default. It sets the clients to the address on which the server listens now: 127.0.0.1, or 192.168.42.1 for a VM server. It reads the key from `~/.config/carl/api-key`.
- `install.sh` needs `python3`. If macOS asks you to install the command-line developer tools, accept. Or run `xcode-select --install`.

**Without the installer.** In the dashboard, push `o` (**[ OpenCode config ]**) or `p` (**[ Pi config ]**). The dashboard copies a config for the server to the clipboard, with the URL and the key. Merge it into `~/.config/opencode/opencode.json` or `~/.pi/agent/models.json`.

CAUTION: A config that the dashboard copies contains the API key itself, so that you can paste it on a different computer. Protect this config as you protect the key.

### Clients in the VM

The VM needs Python 3 and curl. On the Mac, the staging folder for the client bundle is `~/Documents/carl-vm-client`. Before 1.2.0, its name was `mtplx-vm-client`: rename it, or use the old one. The VM sees this folder through the Fusion shared folder `/mnt/data/Documents/carl-vm-client`.

1. **Mac:** start the server for the VM:
   ```bash
   ./carl.sh --vm
   ```
2. **Mac:** copy the client folder to the staging folder:
   ```bash
   rsync -a --delete client/ ~/Documents/carl-vm-client/
   ```
3. **VM:** copy the bundle in:
   ```bash
   cp -r /mnt/data/Documents/carl-vm-client/. ~/carl-vm-client/ && cd ~/carl-vm-client
   ```
4. **VM:** install OpenCode and Pi (first time only):
   ```bash
   ./install-clients.sh          # or: ./install-clients.sh opencode | pi
   ```
   - The installer gets both clients from npm (`opencode-ai`, `@earendil-works/pi-coding-agent`), without sudo, into `~/.local`.
   - It needs Node 22.19 or newer. If Node is missing or older, it downloads Node 22 LTS (Linux or macOS build) from nodejs.org, checks its SHA-256, and puts it in `~/.local/lib/nodejs`.
   - It adds a `PATH` line (marked `# carl-vm-client`) to your `~/.zshrc` or `~/.bashrc`, if the file exists. Make sure that `~/.local/bin` is on your `PATH`.
5. **VM:** install the configs:
   ```bash
   ./install.sh
   ```
6. **VM:** go to the folder of your project, and start `opencode` or `pi` there.

What `install.sh` does in the VM:
- It reads the server's address and key from `remote.json` and `api-key` in the bundle. With both files, it asks nothing.
- Without these files, it asks for the API key. Paste the contents of `~/.config/carl/api-key` from the Mac. Or put the key in a file `api-key` next to `install.sh`, or set `CARL_API_KEY`.
- It keeps the key at `~/.config/carl/api-key`, and uses it again on later runs.
- It adds the client sync service. The service applies the config that you push from the dashboard ([Clients on other computers](reference/client-sync.md)). `NO_SYNC_SERVICE=1` leaves it out.
- On Linux, the service runs only while you are logged in. To keep it running (a VM that you reach by SSH), run `loginctl enable-linger $USER` one time. The installer tells you when this is necessary.
- At the end, it does a smoke test. The line `OK: llama.cpp reachable at http://192.168.42.1:8080/v1 -> "id":"qwen3.6-35b-a3b"` tells you that the connection works. The id is the loaded model.

The client configs are in these files:

| Client | Config files |
|---|---|
| OpenCode | `~/.config/opencode/opencode.json` |
| Pi | `~/.pi/agent/models.json`, `~/.pi/agent/settings.json` |

### Clients on another computer or another VM app

Use this procedure for clients that are not on the server Mac and not in the VMware Fusion VM. Examples: a Parallels VM, or a laptop on your network.

1. Find an address of the Mac that the client can reach:
   - The LAN address: run `ipconfig getifaddr en0`.
   - Parallels: the address of the Mac on the Parallels network, often `10.211.55.2`. `ifconfig` shows it.
2. Start the server on that address:
   ```bash
   ./carl.sh llama --host ADDR
   ```
   Or, in the dashboard, open the Settings tab (tab 5) and select the address in the **network** row. The row shows each address of this Mac.
3. Copy the `client/` folder to the client computer. The folder holds the address and the key (`remote.json`, `api-key`).
4. On the client computer, run the installers in that folder:
   ```bash
   ./install-clients.sh && ./install.sh
   ```

- The address must exist on this Mac. The launcher always refuses `0.0.0.0`.
- To use a Parallels address for `--vm`, set `VM_HOST=10.211.55.2`.
- CAUTION: **With the LAN address, every computer on your network can connect to the server.** Only the API key protects it. Do not use the LAN address on a public or shared network.

**To give the address and the key by hand** (a client folder without `remote.json`):
1. Get the key. In the dashboard, open the Connect tab and push `k` to show the key. Or run `cat ~/.config/carl/api-key` on the Mac.
2. Put the key in a file, for example `key.txt`.
3. Run the installer:
   ```bash
   ./install.sh --host ADDR --key-file key.txt
   ```
4. Delete `key.txt`. The installer keeps its own copy (`~/.config/carl/api-key`, mode 600).

- If you give no key, the installer asks for it. The key does not show when you type it.
- `--key KEY` also works, but the key then stays in your shell history. Use the file or the prompt.

### Sharing with a friend

1. Make the zip:
   ```bash
   tools/make-share-zip.sh [--with-docs] [OUT]
   ```
   The command writes `../CARL-YYYYMMDD.zip`, or the file `OUT`. The zip holds one folder, `CARL/`.
   - The zip does not include caches, `.DS_Store` files, `api-key` files, `*.bak.*` files or the development notes (`docs/`). `--with-docs` (the first argument) keeps the notes.
   - The models, the logs and the API key are outside the folder (`~/models`, `~/.config/carl`).
   - NOTE: The zip includes `client/remote.json` if it exists. This file holds the address of your server, not the key. The first server start on the other Mac writes a new one.
2. On the Mac of your friend, unzip the file.
3. Do the steps in [Set up the Mac](#set-up-the-mac) and [Clients on the same Mac](#clients-on-the-same-mac-no-vm). In short:
   ```bash
   brew install llama.cpp aria2 ansifilter zstd
   ./carl.sh fit
   ./carl.sh download default       # on 24 GB: qwen3.6-35b-a3b-iq3
   ./carl.sh llama
   ./carl.sh install
   ```
   The first server start on that Mac makes a key on that Mac.

**The memory decides the model.** On the other Mac, `./carl.sh fit` gives the real numbers. To see the numbers of a 24 GB Mac from here, run `./carl.sh fit --ram 24`. These are the estimates for a **24 GB** Mac (GPU limit about 16 GiB):

| Model | Largest context window (q4 KV) |
|---|---|
| **`qwen3.6-35b-a3b-iq3`** (the default on 24 GB: stock fast MoE, 14.1 GB) | 2 × 96K slots (the default) fit in ~15.3 GiB, so subagents work. 1 slot can go to 256K. |
| `qwen3.8-27b-iq3` (stock, the smallest 27B, 10.9 GB) | 2 × 96K slots fit, so subagents work. 2 slots can go to 128K each. 1 slot can go to 256K. |
| `qwen3.8-27b-q3` (stock, 13.1 GB) | ~148K with 1 slot. The default 96K fits. |
| `heretic-35b-a3b-iq3` (abliterated fast MoE, 13.6 GB) | 2 × 96K slots fit, so subagents work. It has no MTP head: n-gram speculation. |
| `orcarouter-27b-iq3` (abliterated, the smallest abliterated 27B, 12.6 GB) | 2 × 96K slots fit, so subagents work. |
| `orcarouter-27b-q3` (abliterated, 14.6 GB) | ~68K with 1 slot. Its default window is 64K. |
| `qwen3.8-9b` (5.8 GB) and `gemma-4-e4b` (4.6 GB) | 2 × 96K slots fit |
| `qwen3.8-27b`, `orcarouter-27b` (Q4) | They do not fit under the default limit. |
| `qwen3.6-35b-a3b`, `heretic-35b-a3b` (Q4), `gemma-4-31b` | They do not fit. |

- `orcarouter-27b-q3` is the one exception to the 96K floor, because this build is for 24 GB Macs. The start tells you this. It suggests `./carl.sh tune orcarouter-27b-q3`, which selects the largest window that fits.
- **To increase the GPU limit,** run `sudo sysctl iogpu.wired_limit_mb=18432`. Then the stock Q4 `qwen3.8-27b` can run with ~84K. The setting resets at reboot, and it keeps ~6 GB for macOS. The abliterated Q4 gets only ~16K: use its Q3.
- **Watch the SYSTEM card in the dashboard.** If the memory pressure changes to WARNING or CRITICAL, use a smaller `--ctx`.
- **The launcher refuses a model and a window that do not fit.** It shows what they need against the limit. It also shows the largest window that fits, and auto fit's alternative. `FIT_CHECK=0` is the expert override.
- With 1 slot, `install.sh` does not install the coder subagent ([The coder subagent](#the-coder-subagent-opencode-and-pi)).
- NOTE: These estimates did not get a test on a 24 GB Mac.

---

## 2. Daily use

1. **Mac:** for VM clients, start Fusion.
2. **Mac:** start CARL:
   ```bash
   ./carl.sh
   ```
   | Situation | What `./carl.sh` does |
   |---|---|
   | A server runs on port 8080 | The dashboard attaches to it. |
   | No server runs | It starts llama.cpp with your saved settings, then shows the dashboard. |
   | No model is downloaded | It first offers to download auto fit's pick for this Mac. |
3. **VM or Mac:** go to the folder of your project, then run `opencode` or `pi` there. The client works on the folder that you start it in.
4. In the client, select the model that the server runs: `/models` in OpenCode, `/model` in Pi.
   - The lists show the models that are installed on the server, under their own names.
   - This step is necessary only if the server's model is not the clients' default.
   - If you pick a different model, OpenCode shows a CARL warning. In [router mode](#router-mode-switch-models-from-opencode-or-pi), the pick loads that model.

**Other ways to start:**

| Command | Does |
|---|---|
| `./carl.sh --no-start` (or `./carl.sh dashboard`) | Opens only the dashboard. It attaches to a server that runs. If no server runs, it starts nothing: select a model in the Settings tab, and push `a` to start it. |
| `./carl.sh llama --model qwen3.8-27b` | Starts a specific model ([Choosing a model](#3-choosing-a-model)) |
| `./carl.sh monitor` | Attaches the dashboard to a server that runs |

CAUTION: **The server does not start if another model is in memory.** Two models do not fit. The launcher looks for each process larger than 8 GB, and shows it.

### Stop the server, or keep it running

1. In the dashboard, push `q` (or Ctrl-C, or click **[ Quit ]**).
2. Select one option:

   | Key | Option |
   |---|---|
   | `s` | Stop the server and quit. |
   | `d` | Quit the dashboard, and keep the server running. The server continues to run after you close the terminal. |
   | Esc | Cancel |

- To attach the dashboard again, run `./carl.sh monitor`.
- If an agent's turn is running, Stop asks first: wait for the end of the turn, or stop now ([A turn is running](#a-turn-is-running)).
- To stop the server without the dashboard, run `./carl.sh monitor`, then push `q` and `s`. Or run this command. It finds the process that listens on port 8080:
  ```bash
  kill $(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')
  ```

### What to expect

| Situation | What occurs |
|---|---|
| The first message of the first session | The server reads the system prompt and the tools of OpenCode, about 9K tokens: about 13–20 s on the 35B, about 2 min on the 27B. The [disk cache](#7-fast-starts-the-disk-cache) then saves this prompt. |
| A new session later | The disk cache puts the saved prompt back. The first answer starts in under a second, not after 13–20 s (35B) or about 2 min (27B). |
| A long session after a server restart | The disk cache puts the session back. The next answer starts in about a second. Without the cache, the server reads the whole session again: a 74K-token session takes about 4 min on the 35B and about 13 min on the 27B. |
| Follow-up turns | They are fast. The server uses its cache again, and reads only the new tokens. |
| The Mac on battery power with the lid closed | The Mac sleeps, and all requests in progress pause. The Mac stays awake while the server runs (`caffeinate`), but not with the lid closed on battery. Connect the power supply for long work. |

---

## 3. Choosing a model

Each model has its own name on the server, and the clients list it under the same name. (Before 1.3.0, the builds of one family shared a name, so the label could be wrong.)

| You want | Run on the Mac | Select in the client |
|---|---|---|
| **The default:** Qwen3.6-35B-A3B (MoE, ~4× faster decode and ~6× faster prompt read than the 27B) | `./carl.sh llama` | `qwen3.6-35b-a3b` |
| The dense 27B (stock), for hard code | `./carl.sh llama --model qwen3.8-27b` | `qwen3.8-27b` |
| The uncensored (abliterated) 27B, with the best measured quality in long sessions | `./carl.sh llama --model orcarouter-27b` | `orcarouter-27b` |
| Uncensored and fast: the abliterated A3B (Heretic, Q4 with the MTP head; 32 GB+) | `./carl.sh llama --model heretic-35b-a3b` | `heretic-35b-a3b` |
| A 24 GB Mac (the default on that Mac) | `./carl.sh llama` (it selects `qwen3.6-35b-a3b-iq3`) | `qwen3.6-35b-a3b-iq3` |
| A 24 GB Mac, stock 27B | `./carl.sh llama --model qwen3.8-27b-q3` | `qwen3.8-27b-q3` |
| A 24 GB Mac, stock, the smallest 27B (10.9 GB) | `./carl.sh llama --model qwen3.8-27b-iq3` | `qwen3.8-27b-iq3` |
| A 24 GB Mac (or any Mac), uncensored and fast | `./carl.sh llama --model heretic-35b-a3b-iq3` | `heretic-35b-a3b-iq3` |
| A 24 GB Mac, uncensored 27B with subagents (2 × 96K) | `./carl.sh llama --model orcarouter-27b-iq3` | `orcarouter-27b-iq3` |
| A 24 GB Mac, uncensored 27B, better quality (1 slot) | `./carl.sh llama --model orcarouter-27b-q3` | `orcarouter-27b-q3` |
| **A 16 GB Mac** (auto fit's pick there), or 3–4 subagents at the same time on a small model | `./carl.sh llama --model qwen3.8-9b` | `qwen3.8-9b` |
| Google's Gemma 4 ([Gemma 4](#gemma-4)) | `./carl.sh llama --model gemma-4-e4b` (or `gemma-4-26b-a4b`, `gemma-4-31b`) | the same name |

**Key points:**
- CAUTION: **Only one model can run at a time.** Stop the server before you start a different model. Two models do not fit in 36 GB, and the second model breaks the model that runs.
- **By default, the client does not change the server's model.** llama-server answers with the loaded model, for each model name that the client sends. If the selection and the server do not agree, you get the loaded model with the wrong label and the wrong thinking options. OpenCode then shows a CARL warning. Router mode (below) is the other way round.
- **The clients list only the installed models** (downloaded, or in the models folder), one entry each. After a download or a delete, update the lists: run `./carl.sh install --config-only`, or push `u` in the dashboard's Connect tab. The tab warns when the lists are out of date.
- **Stock or abliterated:** the stock models (`qwen3.8-27b`, the 35B, the 9B, Gemma 4) keep their refusals. The orcarouter and Heretic builds have no refusals. Only `orcarouter-27b` has full measurements in CARL. The stock 27B has the same architecture and speed profile.
- **Q3 or Q4:** Q3 is 2.5–3.5 GB smaller, but its quality is lower (more slips in long agent sessions). Use Q3 if Q4 does not fit.
- **IQ3:** the IQ3 builds are smaller again, and their quality is lower again. The IQ formats unpack more slowly on Metal. MTP helps them less, and 2 drafts make them slower. Thus, their tuned speculation is MTP + n-gram with 1 draft ([IQ3 speculation](reference/models.md#iq3-speculation-measured-2026-10-03)). Use IQ3 only if nothing larger fits.
- **The 9B** (`qwen3.8-9b`, 5.8 GB) is empero-ai's community distillation of Qwen3.8 into a 9B. It is not an official Qwen release: Qwen publishes no 9B in the 3.8 line.
  - It is the one Qwen model that fits a 16 GB Mac with 2 × 96K.
  - It decodes at ~25 tok/s on an M2 Max. The Qwen3.6/3.8 hybrid layers are slow on Metal (llama-bench without CARL's flags agrees).
  - Where the 35B-A3B IQ3 fits, the IQ3 is faster and better, also for several subagents at the same time.
  - Thinking is on or off only.
- **More slots (3–4)** run more subagents at the same time.
  - The Server panel offers 3 and 4 only when they fit this Mac with the selected model, window and KV cache.
  - CARL stops at 4, because each request becomes slower as more requests run at the same time. The 9B: 25 tok/s alone, 41 tok/s in total with 4 (~10 each). 8 would fit, but at ~6 tok/s each.
  - The parallel step of Auto-tune measures this for each model.
- **Other models:** each `.gguf` in `~/models/gguf` is a model too ([Downloading and adding models](#9-downloading-and-adding-models)).

### Gemma 4

The catalogue has three Gemma 4 models from Google. Each one is Google's QAT build in Q4_0 (ggml-org).

| Model | Size | For |
|---|---|---|
| `gemma-4-e4b` | 4.6 GB | Small and fast, for any Mac from 16 GB. 2 × 96K with the full cache needs ~8.2 GiB. Weaker on hard code. |
| `gemma-4-26b-a4b` | 14.6 GB | The MoE (3.8B active), for fast everyday coding. 2 × 96K with the full cache fits a 36 GB Mac. On a 32 GB Mac it gets 2 slots with the window cache only. |
| `gemma-4-31b` | 18.0 GB | The dense 31B, for hard code when you can wait. It runs with the window cache on 32 and 36 GB Macs. |

- **They have no rank.** Auto fit does not pick them. Select them by name.
- **Sliding-window layers.** Most Gemma layers keep only a window of tokens. A saved prompt state goes back only when every layer keeps the full context. The setting `cache.swa` decides this ([Fast starts: the disk cache](#7-fast-starts-the-disk-cache)).
- **Speculation:** n-gram only. Gemma's MTP drafter is a separate file, and CARL does not use it yet.
- **Sampling:** Google's values: temperature 1.0, top_p 0.95, top_k 64.
- **Thinking:** on or off only.
- **Images:** the models can read images, but CARL starts them text-only.
- More: [Gemma 4](reference/models.md#gemma-4).

### Router mode: switch models from OpenCode or Pi

Router mode is for users who prefer to change models during the work. llama.cpp's router then offers every downloaded model that fits this Mac. It loads the model that OpenCode (`/models`) or Pi (`/model`) asks for.

- One model is in memory at a time. The loaded model stops first.
- A switch takes 30 s to 2 min.
- The default is the dashboard's own mode (one model): auto fit and the Settings tab pick the model.

WARNING: Every switch empties the prompt cache. The model that loads starts cold. OpenCode and Pi put a session back from the [disk cache](#7-fast-starts-the-disk-cache) about a second after the load. Any other client reads the whole conversation again (minutes for a long session). Keep this in mind before you switch.

**To turn router mode on:**
1. In the dashboard, push `5` for the Settings tab.
2. Push `]` until the **Router** panel shows.
3. Click **OpenCode / Pi switch models (router)**. The Router panel works with the mouse only.
4. Push `y` to confirm ("MODEL SWITCHING?").

The dashboard saves `llama.mode = router` in `config.json`, and restarts a server that runs. Then it updates the OpenCode and Pi configs on this Mac (when CARL set them up here). In a VM, run `./install.sh` again there.

Other ways:

| Command | Does |
|---|---|
| `./carl.sh --router` | Router mode for this start only |
| `./carl.sh config set llama.mode router` | Router mode for each start |
| The panel's **Dashboard only**, or `./carl.sh --single` | Back to one model |

**How the router works:**
- **Each model gets the settings that a single start of it uses:** your `config.json` profile, else its Auto-tune result, else the catalogue. This includes the window, the slots, the KV cache, the speculation, the sampling and the RAM cache.
- A model whose setup does not fit the GPU limit is left out. The start tells you why.
- `--model`, `--ctx`, `--kv` and `--slots` do not apply to a router start.
- The launcher writes the presets to `~/.config/carl/router-presets.ini` at each start. Do not edit this file.
- **The model that loads first** is the model that a single start loads (`llama.model`, or auto fit's pick).
- **The Router panel** shows the state of each model (loaded, loading, unloaded) with **Load** and **Unload** buttons (router mode only). It also shows the last 5 switches, and if the OpenCode and Pi lists agree with the installed models. **Update the OpenCode / Pi configs** opens the Connect tab and asks there.
- **A model that is not installed** gets an error (HTTP 400 "not found"), and nothing loads. OpenCode shows a CARL warning.
- **Custom models:** set the *thinking* field of the card (Models panel, `e`), so that OpenCode offers the correct levels.
- The dashboard follows the loaded model (memory, context, requests). The header shows `llama.cpp router (N models)`.

---

## 4. Thinking on, off and effort

The models "think" (hidden reasoning) before they answer. More thinking makes the model slower, but the answers to difficult problems are better.

### OpenCode: `/variants` (or ctrl+t)

In OpenCode 1.18, the thinking levels are model **variants**. Type `/variants` and select one, or push **ctrl+t** for the next one. The current variant shows next to the model name. `/models` changes the model.

| Model | Variants | Default |
|---|---|---|
| The 27B builds (`qwen3.8-27b…`, `orcarouter-27b…`) | `none` = off, `low`, `medium`, `xhigh` | `low` |
| The 35B-A3B builds (`qwen3.6-35b-a3b…`, `heretic-35b-a3b…`), the 9B, Gemma 4 | `none` = off, `high` = on | `high` |
| A model that you added | From the *thinking* field of its card ([Cards for custom models](#cards-for-custom-models)): on / off (`none`, `high`), or effort levels (as the 27B). Without a card value, CARL reads it from the GGUF header. | |

### Pi: thinking level

| Model | Levels |
|---|---|
| The 27B builds | off, low, medium, xhigh |
| The 35B-A3B builds, the 9B, Gemma 4 | off, high |

`install.sh` sets the Pi defaults: provider `llamacpp`, the model that a server start loads, and thinking `low`. It sets them only if they are unset, or if they still have the values that it set before.

**Notes:**
- **A change applies from the next message.** A reply in progress keeps its mode.
- **After `install.sh` runs again, fully restart OpenCode.** An open OpenCode keeps its old options. If OpenCode does not know an option, it uses the default without a warning.
- **Use `xhigh` only when necessary.** With `xhigh`, Qwen3.8 thinks too much on simple tasks. `low` is the correct default for agent work on the 27B.
- **The 35B has no effort levels,** only on and off. Its template ignores low, medium and xhigh. Thus, only `none` and `high` are available.
- **The sampling settings follow the mode:**

  | Mode | Sampling |
  |---|---|
  | Thinking on (Qwen) | temperature 1.0, top_p 0.95, top_k 20 |
  | The OpenCode `none` variant | Qwen's non-thinking values: temperature 0.7, top_p 0.8, presence penalty 1.5 |
  | The Pi `off` level | The thinking values stay. |

  To change the server defaults, run `TEMP=0.6 ./carl.sh llama`. If the 35B repeats itself, use `PRESENCE=1.5`. More: [Sampling](reference/sampling.md).
- **How "off" works:** the server loads the chat template of the model with one added rule. With this rule, `reasoning_effort: none` means `enable_thinking: false`. To use the template without the rule, run `THINK_TOGGLE=0 ./carl.sh llama`. More: [Thinking](reference/thinking.md).

---

## 5. Context window

The context window is the quantity of conversation that the model can hold.

- The default is 96K tokens for each slot.
- 96K is a floor: CARL does not select less if 96K fits. Agent work needs that much context.
- The one exception is `orcarouter-27b-q3`, a build for 24 GB Macs. It starts at 64K and suggests a tune.
- The Settings tab warns only about larger windows.

Longer windows work (recall stayed 8/8 up to ~150K), but they need more time. On the 35B, a cold prompt reads in these times:

| Window | Cold read | Decode speed |
|---|---|---|
| 64K | ~5 min | 20 tok/s |
| 128K | ~19 min | 14 tok/s |
| 150K | ~27 min | 12 tok/s |

Use `--ctx 128k` or `--ctx 160k` when you need it. More: [Context length](reference/memory.md#context-length-what-longer-windows-cost).

```bash
./carl.sh --ctx 192k      # larger: N or Nk, from 4k to 256k
./carl.sh --ctx 96k       # smaller: less memory
```

**After you change `--ctx`, update the clients:**
1. **VM:** run the installer again. It reads the window of the server that runs:
   ```bash
   cd ~/carl-vm-client && ./install.sh
   ```
   On the Mac, run `./carl.sh install --config-only`.
2. Restart OpenCode or Pi.

**Why this is important:**
- **The client limit decides only when the client compacts** (summarises) the conversation. The client does not send this limit to the server.
- **If the client has 128K but the server has 96K,** all requests fail when the session is larger than 96K. The error is `HTTP 400 ... exceeds the available context size`. The client never compacts, because it did not get to its own limit.
- **A server window that is larger than the client limit causes no problem.** It only uses memory that the client does not use.
- **`install.sh` sets the client limit** from the server that runs, or from `LLAMA_CTX=128k ./install.sh`. If the server is down, it uses 96K.

**Memory:**
- The server allocates all of the KV cache at the start. The 27B at 128K: about 2.25 GiB with q4_0, 4.25 GiB with q8_0. The 35B: 720 MiB and 1.33 GiB.
- 192K with q4 fits on a 36 GB Mac. If you use a larger window, keep the memory of the VM small.

---

## 6. Working in OpenCode and Pi

### Subagents

OpenCode and Pi run a subagent as a separate conversation (a child session).

- **By default, the server keeps two slots** (if they fit). The main session and a subagent each keep their own cache.
  - When the subagent is complete, the main session continues in about a second. It does not read its full context again. With one slot, the main session read everything again: this took minutes on the 27B at 60K tokens.
  - The banner shows `slots: 2 (auto) x 98304 tokens, KV q4_0/q4_0, RAM prompt cache … MiB`.
  - `--slots 1` sets one slot (less memory). `./carl.sh fit --slots 2` shows what fits.
- **Two at the same time:** a subagent can run while the main session keeps its place. OpenCode can also run two subagents in parallel.
  - On the 35B, two requests at the same time give ~39% more total throughput.
  - On the 27B, the two requests share the GPU. Each one runs at about half speed.
- **The title agent of OpenCode stays on.** With 2 slots, it runs at the same time as the main session. It does not wait behind the main session.
- **The dashboard** shows one context bar for each slot. When both slots work, the header shows **BUSY ×2**.
- **More than two conversations at the same time** (for example, two subagents and the main session): the server puts the extra conversation in the RAM prompt cache. The size of that cache comes from the free memory, so the conversation possibly does not fit. Then the server reads it again.

### The coder in the background

The coder subagent runs in the background, in OpenCode and in Pi.

1. The main agent starts the coder and tells you what it does.
2. The main session is then free. You can ask it other things while the coder works in the other slot.
3. When the coder ends, its result comes back to the main session as a message.
4. The main agent then checks the result.

Local models often ignore an instruction to use the background. Thus, CARL starts its coder in the background, unless the model asks for the foreground:
- OpenCode: the `carl-background` plugin.
- Pi: the `subagent` tool. In Pi, `/subagents` lists the subagents that run and stops one. The footer shows how many run.

`NO_BACKGROUND_SUBAGENTS=1 ./install.sh` turns the background off.

### The Subagents panel (OpenCode)

In OpenCode, the sidebar shows a Subagents panel.

| Part | What it shows |
|---|---|
| The running subagents, at the top, oldest first | A spinner, the agent and the task, the time, and the tool that runs now |
| The finished subagents, below, newest first | ✓ (complete) or ✗ (error), the agent, the task and the duration, one line each. The panel shows the 5 newest, for 5 minutes after they end. |
| `+N more` | The other finished subagents. Click it to see all of them. |

- When you go to a different parent session, the panel shows the subagents of that session.
- Click a subagent to see its model and its context size. Click it again to open its conversation.
- Click the panel header to collapse the panel.
- To get a new version of the panel, run `install.sh` again. Then restart OpenCode.
- The plugin is `client/opencode/plugins/subagents-sidebar/`. `install.sh` registers it in `~/.config/opencode/tui.json`. `NO_SIDEBAR=1 ./install.sh` installs without it.

### Switching sessions (OpenCode)

OpenCode 1.18.34 does not show the open sessions as tabs. Thus, `install.sh` adds a session switcher to the right side of the prompt box:

```
‹ 2/3 ● fix the log parser ›
```

| Part | Meaning |
|---|---|
| `2/3` | The place of this session in the list. The newest session is first. |
| `●` `!` `○` | The state of the session: busy; waits for a permission or a question; idle |
| `‹` `›` | Click to go to the previous or the next session. |
| The title | Click it to open a list of the sessions. Select one, and push Enter. `/switch` opens the same list. |
| `+1●` after the title | Another session is busy. |

- The list contains the top-level sessions of this project that changed in the last 72 hours (at most 9). It does not contain subagent sessions. For older sessions, use `/sessions`.
- The switcher does not show when there is only one session.
- The plugin is `client/opencode/plugins/session-switcher/`. `install.sh` registers it in `~/.config/opencode/tui.json`. `NO_SWITCHER=1 ./install.sh` installs without it.

### Tools in OpenCode and Pi

`./carl.sh install` gives OpenCode and Pi as many tools as fit a small prompt. As installed, OpenCode's system prompt and tool definitions are **9,870 tokens** (Qwen3.6 35B-A3B, OpenCode 1.18.34). The target is less than 10.5K, because each new session reads them first.

| Tool | OpenCode | Pi | Switch |
|---|---|---|---|
| read, edit, write, bash, grep, glob / find / ls, task, todowrite, question, skill, webfetch | Built in | read, edit, write, bash, **grep, find, ls** (Pi has the last three off; CARL turns them on) | |
| **Web search** | `websearch` (Exa) | `web_search_exa`, `web_fetch_exa` (MCP) | `WEB_SEARCH=exa\|parallel\|off` |
| **LSP** (go to definition, references, diagnostics) | `lsp`, with `"lsp": true`. OpenCode downloads and runs the language servers that it needs. | | `NO_LSP=1` |
| **Browser** (a real Chrome: open pages, click, type, fill forms, screenshots, console and network) | The **browser** subagent | The `carl-browser` MCP server, loaded when necessary | `NO_BROWSER=1`, `BROWSER_HEADED=1` (show the window) |
| **Background subagents** | The task tool can run a subagent in the background. CARL's coder always does (`carl-background`). | The `background` option of the `subagent` tool. CARL's coder always uses it. `/subagents` lists and stops them. | `NO_BACKGROUND_SUBAGENTS=1` |
| **Parallel tool calls** | Several tool calls in one turn. llama.cpp needs the request to ask for them: CARL's OpenCode model entries do. | | |

Put a switch in front of the installer, for example `WEB_SEARCH=off ./carl.sh install --config-only`.

- CAUTION: **Web search sends data out of this computer.** The search queries go to Exa (`mcp.exa.ai`, no account necessary) or to Parallel (`search.parallel.ai`). Everything else stays local. `WEB_SEARCH=off` turns web search off.
- **The browser runs in a temporary profile** (`--isolated`). It is not logged in to a site, and nothing stays after it closes.
  - On a Mac with Google Chrome, it uses that Chrome. On other computers, it uses Playwright's Chromium. Install it one time with `npx playwright install chromium` (about 150 MB).
  - It runs without a window, unless `BROWSER_HEADED=1`.
  - The package is pinned (`@playwright/mcp@0.0.83`). npx gets it the first time.
- **Why a browser subagent:** the 26 browser tools are ~4.8K tokens.
  - In the main agents, they would make each first request read 14.5K tokens.
  - In a subagent, they cost the main prompt one short description.
  - The main agent sends browser work to the subagent when a task needs a live page. Examples: a check of a web app that it changed, or a page that webfetch cannot read.
  - You can also write `@browser ...`.
  - In Pi, `tool_search` loads the browser tools when necessary.
- **How OpenCode gets the switches:** OpenCode reads them only from environment variables. Its config file has no keys for them.
  - The installer writes them to `~/.config/carl/opencode.env`.
  - It adds a marked 3-line pointer to your `~/.zshrc` or `~/.bashrc` that loads this file. It makes a backup first, and changes nothing else in the file.
  - It never makes a new profile file. Without one, it shows the line to add.
  - Before it writes, it tells you which file it changes. For a symlinked profile, it changes the target of the link.
  - `NO_PROFILE=1` keeps your profile as it is, and shows the line to add.
  - Open a new terminal after the installer. An OpenCode that you start in a different way (not from a shell) does not see the switches.
- **Background subagents** and **LSP** are experimental features of OpenCode (`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1`, `OPENCODE_EXPERIMENTAL_LSP_TOOL=1`). Web search uses `OPENCODE_ENABLE_EXA` (or `OPENCODE_ENABLE_PARALLEL`) and `OPENCODE_WEBSEARCH_PROVIDER`. The installer removes the pointer when all these switches are off.

### The coder subagent (OpenCode and Pi)

`install.sh` adds a specialist **coder** subagent, and a rule that tells the main agent when to use it.

- It does this **only when the server has 2 or more slots.** With one slot (for example a 24 GB Mac with a 27B), each delegation removes the main session from its slot. The server then reads it all again. Thus, `install.sh` does not install the coder, or removes it.
- `install.sh` checks the server that runs. Run `install.sh` again after you change the model or the slots.
- `CODER=1` installs the coder in all conditions.

**When the main agent uses the coder.** The main agent delegates **on its own**, and only in these conditions:
1. **It is stuck:** a fix for the same code failed two times. These are its own attempts, or attempts that you tell it failed.
2. **The task is large:** 3 or more files, or ~150 or more lines. Examples: a new module, package or CLI, an implementation with tests, a multi-step feature or refactor.

The main agent keeps the questions, the explanations, the code searches and the small edits.

**How the coder works:**
- It starts with a new context, and works one step at a time.
- Before it fixes a failure, it reproduces the failure.
- It runs the tests or the build.
- It does not leave placeholders.
- After three failed approaches, it stops and reports.
- Its report gives the result, the changed files, the verification, the root cause and the open issues. The main agent checks the report before it answers you.

**Tested (35B, 2 slots, 2026-10-01):**

| Client | Large tasks delegated | Stuck tasks delegated | Questions and small edits |
|---|---|---|---|
| OpenCode | 3 of 3 | 2 of 2 | Done by the main agent |
| Pi | 2 of 3 | Yes | Done by the main agent |

Delegation depends on the judgment of the model. Thus, it occurs "usually", not "always". When you ask for it, it always occurs.

**How it codes.** Its prompt puts five engineering standards in order of priority:
1. **Secure:** check inputs at the boundaries; no shell commands, SQL or paths built from strings; no secrets.
2. **Typed:** full annotations, domain types; run the type checker.
3. **Hexagonal:** domain logic behind ports, input and output in adapters, dependencies that point inward; test the domain with fakes.
4. **Clean.**
5. **Object-oriented where it fits.**

- New modules follow the standards fully.
- For changes to code that exists, the coder applies the standards inside the change. If a structure blocks a clean change, the coder reports it. It does not refactor it without a word.
- A definition-of-done checklist runs before each report: tests green, typed, ports and adapters, secure, real input samples, no placeholders, builds. The report has a "Standards" section.

**Its tools:**

| Client | Tools of the coder |
|---|---|
| OpenCode | Every tool except `task` (also LSP and web search) |
| Pi | Every tool except `subagent` and `tool_search` (also web search, when it is installed) |

**No browser, on purpose.** The coder keeps its context for code.
1. Some changes need a check in a live page: an app loads, a form works, no console errors. For these, the coder's report has a **Needs a browser check** part: how to start the app, the URL, and what to look at.
2. The main agent starts the app (in the background).
3. OpenCode sends the URL and the checks to the browser subagent. Pi uses its own browser tools.
4. The main agent then stops the app.

- The browser subagent checks only the live page. If the page does not load, it says so. It does not read the code instead.
- Without the browser (`NO_BROWSER=1`), the main agent gives the list of checks to you.

**Same model.** The coder uses the loaded model. Thus, the gain is a new, focused context and more reasoning (OpenCode: effort medium), not a stronger model. With 2 slots, the main session keeps its cache while the coder works.

**To ask for the coder directly:**
- **OpenCode:** type `@coder` and your task in the prompt, for example `@coder add tests for parse_config`. The subagents are in the `@` list, and Tab completes the name. The task goes directly to the coder: the main agent does not decide. If you already have an agent of your own with the name `coder`, CARL's coder is `@carl-coder`. "Use the coder agent to …" also works.
- **Pi:** there is no command. The coder is the `subagent` tool. Ask for it in the message: "use the coder subagent to …".

**To turn it off,** run `NO_CODER=1 ./install.sh`.

**Where it is:**

| Item | Place |
|---|---|
| Source | `client/agents/coder.md` (the frontmatter description tells when to use it; the body holds its instructions) and `client/agents/delegation.md` (the rule for the main agent). To change the coder, edit these files and run `install.sh` again. |
| OpenCode | `agent.coder` in `opencode.json` (mode subagent, reasoning medium, temperature 0.6, no nested subagents, at most 80 steps), and `instructions`. Its prompt is in `~/.config/opencode/carl/coder.md`. |
| Pi | `~/.pi/agent/agents/coder.md`, the `subagent` extension, and a marked block in `~/.pi/agent/APPEND_SYSTEM.md` |

`install.sh` never replaces your own `coder` agent, Pi `agents/coder.md` or `extensions/subagent`. CARL's agent then gets the name `carl-coder`. If you also have your own `carl-coder`, the installer skips it and shows a note.

### CARL's plugins and extensions

`./carl.sh install` adds these to OpenCode and Pi. Each one has a switch for the installer. Type `/carl` in OpenCode or Pi to see which are on. [The plugins and extensions](reference/plugins.md) tells how each one works.

| Plugin | In | What you get | Off |
|---|---|---|---|
| **Prompt cache** (`carl-cache`) | OpenCode, Pi | Fast starts: each agent's prompt and each session saved on the server's disk ([Fast starts](#7-fast-starts-the-disk-cache)) | `NO_CACHE=1` |
| **Model check** (`carl-model-check`) | OpenCode | A warning when the model you pick is not the model that the server runs, is not installed, or loads now | `NO_MODEL_CHECK=1` |
| **Coder in the background** (`carl-background`) | OpenCode | The coder subagent runs in the background, so the main session stays free | `NO_BACKGROUND_SUBAGENTS=1` |
| **Subagent tool** (`subagent`) | Pi | The `subagent` tool: the coder and other agents, one, several at the same time, a chain, or in the background; `/subagents` | `NO_CODER=1` |
| **Subagents panel** (`subagents-sidebar`) | OpenCode | The running and finished subagents in the sidebar ([The Subagents panel](#the-subagents-panel-opencode)) | `NO_SIDEBAR=1` |
| **Session switcher** (`session-switcher`) | OpenCode | `‹ 2/3 ● title ›` in the prompt box, `/switch` ([Switching sessions](#switching-sessions-opencode)) | `NO_SWITCHER=1` |
| **The /carl panel** (`carl-panel`) | OpenCode, Pi | Every CARL piece on this computer with its state, and the config sync (auto-apply, apply now, check) | (always on) |

- Put a switch in front of the installer, for example `NO_SIDEBAR=1 ./carl.sh install --config-only`. The installer then removes the plugin.
- Restart OpenCode or Pi after an install. They load their plugins when they start.
- Your own plugins and extensions stay. If one of yours has the same name, CARL does not install its own.

**The /carl panel.** Type `/carl` in OpenCode or Pi. The panel shows one section for each CARL piece, with its state:

| Section | In | It shows |
|---|---|---|
| Config sync | OpenCode, Pi | The server, the sync service, the applied config, a config that waits, auto-apply. Actions: auto-apply on or off, **Apply now** (only when a config waits), **Check the server now**. |
| Prompt cache | OpenCode, Pi | On or off, and the switch |
| Model check, Session switcher, Subagents sidebar | OpenCode | On or off, and the switch |
| Coder subagent | OpenCode, Pi | On or off, its tools, background on or off |
| Subagent tool | Pi | On or off |
| Browser, Web search | OpenCode, Pi | On or off. Web search tells you that its queries go out of this computer. |
| LSP | OpenCode | On or off |

- A pushed config is applied at once. To keep it waiting, turn auto-apply off: select **Config sync**, then **Turn auto-apply off**.
- OpenCode and Pi read their configs when they start. After a config is applied, OpenCode shows a message and Pi shows a notice: restart it to use the new config.
- More: [Clients on other computers](reference/client-sync.md).

---

## 7. Fast starts: the disk cache

The server keeps the conversations that it read in memory: in its slots, and in its RAM cache. That memory is lost in these conditions:
- The server restarts.
- Router mode changes the model.
- Many other sessions push a conversation out.

The next request then reads the whole prompt again. This is OpenCode's system prompt and tools (~8–10K tokens: ~13–17 s on the 35B, ~2 min on the 27B). In a session that continues, it is also the whole conversation (minutes for a long one).

OpenCode and Pi prevent this with CARL's **prompt cache**: the OpenCode plugin and the Pi extension `carl-cache`, which `install.sh` adds. They save prompt states on the server's disk, through the server.

| What is saved | Effect |
|---|---|
| **Each agent's prompt:** the system prompt and the tools of each agent (OpenCode's build, plan and coder; Pi, and each Pi subagent) | The server reads the prompt one time and saves it. A new session then starts at once: **the first answer starts in under a second instead of after about 13 s** (35B IQ3; about 2 min on the 27B). |
| **Each session** (only main sessions of 4,096 tokens or more) | When the session continues and the server no longer holds it, its file goes back in first: **the next answer starts in about a second instead of after the whole conversation is read again** (a 10K-token session: about 20 s on the 35B; a 74K-token one: about 4 min). This works after a restart, after a router switch, and after many other sessions. It works for each session, so a session that you open again after several others also comes back. |

### When a session is saved

The **Save** setting (Caching panel; `cache.save`) decides when:

| Save | When | Trade-off |
|---|---|---|
| **auto** (default) | When the part that is not saved would take 2 minutes to read again (at this model's measured read speed; Caching panel: **Auto after**, `cache.auto_s`). Also before the session leaves the server: another session needs its slot, a router switch, a stop or a restart from the dashboard. | The fewest writes for normal use. A crash loses at most ~2 minutes of reading for each session. |
| **every turn** | After each reply | Nothing is lost. The most writes: up to ~0.5 GB for each turn of a long session on the 35B. |
| **on a switch** | Before the session leaves the server (as above) | A crash, or a stop outside the dashboard (Ctrl-C), loses what was not saved. |
| **before a stop** | Only before a stop or a restart from the dashboard, or a router switch | The fewest writes. Sessions that went to the RAM cache are lost at the stop. |

For the stop saves, OpenCode and Pi leave a small record of the session that each slot holds (`.resident+…` in the slots folder). The dashboard saves those slots before Stop, Apply, Auto-tune and router loads. A new OpenCode or Pi process also uses the record: it finds a session that is still in its slot, and does not read it again.

### How the cache works

- **What changes in the prompt:** some parts of the system prompt change between projects and days. These parts move to the first message of each session.
  - OpenCode: the environment block (folder, git, date) and the project's instructions (the AGENTS.md files in the working folder). Instructions from outside the folder stay in the system prompt.
  - Pi: the project context and the folder.
  - The model gets the same information. The agent's prompt is then the same in each project, and its saved file fits each session.
- **Router mode:** before a request for a different model, the plugin loads that model and puts the session back. A switch then costs the load (30 s to 2 min) and about a second. OpenCode's title requests go to the loaded model, so a new session does not cause two switches.
- **Any model:** the Qwen hybrid models and normal transformer models (tested: Gemma 4 E4B, whose template puts the tools after the system prompt).
- **Slots:** each request goes to a free slot: the session's own slot when it is free, else a different one. The session's state then goes in from the disk or from the RAM cache.
  - Two requests never take the same slot at the same time. OpenCode and Pi on this Mac claim a slot with a small file (`.claim+…`) until the server has the request.
  - If every slot is taken, llama.cpp decides, as without the cache.
  - When a subagent needs a slot that holds a different session, that session is saved first. It comes back from its file.
  - OpenCode's title requests run without thinking (they are short), so they do not hold a slot for a long time.
- **A saved state is used only when it fits exactly.** It needs the same model file, the same llama.cpp build, and a prompt that starts with the saved one. If the request is different (you added a tool, or edited an earlier message), the server reads the whole prompt, as without the cache. The new state is then saved for the next time.
- **When the prompt changes** (a new tool or MCP server, an OpenCode or Pi update): the next new session reads the new prompt one time and saves it. The old prompt files are removed. A saved session from before the change is read again one time.
- NOTE: Subagent sessions and title requests are not saved.

### Models with sliding-window layers

Gemma 4 models have sliding-window layers. llama.cpp can put their saved states back only when every layer keeps the full context (`--swa-full`). This costs memory: +2.3 GB for Gemma 4 E4B at 2 × 96K.

| SWA models (`cache.swa`) | Effect |
|---|---|
| **auto** (default) | The full cache when it fits this Mac with the slots that the model gets (a second slot has priority), else only the window |
| **full** | Always the full cache |
| **window** | Never. The least memory. Their sessions are read again after a restart. |

- The start tells you which: `sliding-window cache: full` or `window only`.
- The memory check knows the window (from the layer pattern in the GGUF). Thus, such a model can fit a larger window than before.

### The files and the disk limit

- The files are in `~/.config/carl/slots` (the server's folder).
- They stay inside a disk limit: **10 GB** by default. The oldest conversations go first, then the oldest prompts.
- The clients on the server's Mac apply the limit after each save. The dashboard applies it each minute, and the launcher at each start (for files that clients on other computers wrote).
- An agent's prompt is ~25–120 MB. On the 35B, a conversation is ~59 MB plus ~5.8 KB for each token (a 74K-token session: ~0.5 GB).
- When the disk has less than 10 GiB free, the clients on the server's Mac save nothing.
- **Conversations stored as patches:** a conversation is stored as a patch against the agent's prompt file that it starts with. The prompt part is then on the disk only one time ([Caching](reference/caching.md#how-a-saved-state-is-built)).
  - On the 35B, an 8.6K-token session goes from 115 MB to 64 MB. A long session is ~10% smaller.
  - Caching panel: **Shared** (`cache.share`).
  - It needs zstd on the server's Mac (`brew install zstd`). Without zstd, the files stay whole. Clients on other computers get a whole file through the dashboard's API.

### Settings and switches

- **Settings tab → Caching panel** (EXPERIMENTAL): the disk limit, prompts on or off, sessions on or off, when to save, auto after, conversations stored as patches, SWA models, what is on disk, and **Clear** ([Caching panel](#caching-panel)).
- `./carl.sh cache` shows the files. `./carl.sh cache trim` applies the limit (and stores new conversations as patches). `./carl.sh cache clear` removes all files.

| Switch | Effect |
|---|---|
| `CARL_CACHE_SAVE=turn\|auto\|switch\|stop` | Sets the save rule for one client |
| `CARL_CACHE=0` | Turns the cache off for one run |
| `CARL_CACHE_LOG=FILE` | Writes what the cache does to a file |
| `NO_CACHE=1 ./install.sh` | Installs without the cache |

**Clients on another computer** (a VM) use the dashboard's API for the Caching settings, the slot claims and the records. This works while the dashboard runs ([Clients on other computers](reference/client-sync.md)).

More: [Caching](reference/caching.md).

---

## 8. KV cache: q4 or q8

```bash
./carl.sh llama        # q4_0 (default)
./carl.sh --kv q8      # q8_0
```

| | q4_0 (default) | q8_0 |
|---|---|---|
| Decode at 66K | 7.3 tok/s | 7.5 tok/s |
| Cold prompt read at 66K | 64.8 tok/s | 55.7 tok/s |
| Memory (27B, 128K) | ~20 GB | ~22 GB |
| Needle recall at 66K | 8/8 | 8/8 |

Use q8 when subtle long-range detail is the most important.

CAUTION: Do not mix KV types (`KV_K=q8_0 KV_V=q4_0`). With mixed types, the server reads prompts about 5× slower.

---

## 9. Downloading and adding models

```bash
./carl.sh models                    # the catalogue + the models folder + your downloads, with status and free space
./carl.sh fit                       # what fits this Mac (fit --ram 24 / --ctx 64k / --slots 2)
./carl.sh download qwen3.8-27b      # one catalogue model (aria2c, 16 connections, resumable)
./carl.sh download default          # auto fit's pick for this Mac
./carl.sh download all              # every catalogue model
./carl.sh download hf:OWNER/REPO/FILE.gguf   # any GGUF from Hugging Face (verified too)
./carl.sh download hf:OWNER/REPO    # list the GGUF files of that repo
./carl.sh verify [NAME...]          # check the size and SHA-256 again (no names: every downloaded model)
./carl.sh delete NAME               # delete a model file (and its partial download)
./carl.sh card NAME                 # a model's card; custom models: card NAME set FIELD VALUE (see below)
```

- The files go to `~/models/gguf/`. To use a different folder, set `MODELS_DIR`, or `paths.models_dir` in the settings file ([The settings file](#the-settings-file)).
- If a download stops, run the command again. The download continues from where it stopped.
- If a file fails its checksum, its name changes to `*.bad`.
- The dashboard can do the same: Settings tab, **Models** panel ([Models panel](#models-panel)).

### The catalogue

The catalogue is [host/catalog.json](host/catalog.json). It holds the built-in models: 11 Qwen models and 3 Gemma 4 models. For each model, it has these fields:

| Field | Contents |
|---|---|
| `hf` | The download source: the Hugging Face repo, a pinned revision, the file, its SHA-256 and its size |
| `summary`, `description` | What the model is (technical) |
| The model card | `role` (a short headline), `good_for` (tags: `agent coding`, `hard code`, `chat & writing`, `uncensored`), `why_use`, `trade_offs`, `pick_instead` (another catalogue model, and when it is the better pick), `hardware`, `uncensored` (what it means; abliterated models only) and `rank` |
| `rank` | The quality order, 1 = best: parameters and density first, then quantization. Speed is the reverse. The Qwen models have ranks 1–6. The Gemma 4 models have no rank. |
| `tune` | The recommended server settings: KV cache, context, slots, speculation, draft tokens, sampling |
| `why` | The reason for each tuned value |
| `ctx_zones` | The context windows that read fast, slow and very slow |
| `measured` | The reference measurements |

- CARL checks the catalogue when it loads it. These are errors: unknown tags, `uncensored` on a stock model, and a `pick_instead` model that is not in the catalogue.
- The Settings tab shows the description and the reasons next to the settings.
- The fields `default` and `default_small` at the top of the file are only the offline fallback, when CARL cannot read the GGUF headers. Normally auto fit picks the default model.

### Custom models (not in the catalogue)

- **Each `.gguf` in the models folder is a model.** `./carl.sh models` lists it with the source `file`. Its name is the file name in lower case, without `.gguf`. Vision projectors (`mmproj…`) and the second and later parts of a split GGUF are not listed.
- **`./carl.sh download hf:OWNER/REPO/FILE.gguf` downloads any GGUF from Hugging Face.** It gets the revision, the size and the SHA-256 from the Hugging Face API. Then it downloads the file and verifies it. A `huggingface.co` URL to the file also works. `hf:OWNER/REPO` (no file) lists the GGUF files of the repo.
- CARL records these models in `~/.config/carl/models.json`. This file also holds the Auto-tune results for each model on this Mac.
- **A custom model gets its first settings from its GGUF header:**
  - q4_0 KV.
  - 96K for each slot (less only if the model was trained for less).
  - MTP + n-gram (1 draft) if the file has an MTP head. Without an MTP head: n-gram only (2 drafts).

  These settings are a guess. Run [Auto-tune](#auto-tune) to measure better values.
- To start it, run `./carl.sh llama --model NAME`. A path to a `.gguf` also works.
- **Give it a card** (what it is good for): see [Cards for custom models](#cards-for-custom-models).
- Before you use it from the clients, examine the chat template of the model. The thinking options are different between model families. The server answers with the loaded model for all model names, but the client sends the thinking options of the entry that you select.

**To add a model to the catalogue** (for each user of this folder):
1. Copy an entry in `models` in `host/catalog.json`.
2. Change `name`, `label`, `summary` and `description`. The `name` is the name that the server and the clients use.
3. Write its model card: `role`, `good_for`, `why_use`, `trade_offs`, `pick_instead`, `hardware`, `rank`, and `thinking` (`on-off` or `effort`). For an abliterated model, set `abliterated` and `uncensored`.
4. Fill in `hf`: `repo`, `revision` (the commit SHA), `file`, `sha256` and `bytes`. The easy way: run `./carl.sh download hf:OWNER/REPO/FILE.gguf` first. Then copy the `hf` block that it writes to `~/.config/carl/models.json`.
5. Set `tune` and `why`:
   - For `spec`, use `draft-mtp,ngram-mod` if the GGUF has an MTP head. For a file without an MTP head, use `ngram-mod`.
   - Use `spec_n` 1. For a MoE K-quant, 2 can be better (the Q4 35B). Do not use 2 on an IQ quant.
   - Run `./carl.sh tune NAME` to measure.
6. Set `arch`, `mtp`, `min_ram_gb` and `ctx_zones`.
7. Examine the chat template of the model before you add it to the clients. The thinking options are different between model families.
8. Run `./carl.sh install --config-only`. The clients then list the model when it is downloaded.
9. In a VM, copy the bundle to the shared folder, and run `install.sh` again there.

### Cards for custom models

CARL cannot tell what a model is for from its file name. Thus, a custom model starts without a card:
- The lists show only "Custom model …".
- It has no "good for" tags, so the use-case filters hide it.
- It has no rank, so sort by quality puts it last.
- It has no dense or MoE mark.

You write its card, with the same fields and the same rules as the catalogue's cards:

| Field | Value |
|---|---|
| `label` | The name in the lists and on the MODEL card (default: the file name) |
| `role` | A headline, at most 60 characters |
| `good_for` | Tags: `agent coding`, `hard code`, `chat & writing`, `uncensored` (`uncensored` only with `abliterated`) |
| `why_use`, `trade_offs`, `hardware` | Text: why to select it, when to select something else, which Macs it is for |
| `abliterated` | yes / no: refusals removed. The **stock** filter hides it, and auto fit never picks it. |
| `uncensored` | Text: what uncensored means for this model (abliterated models only) |
| `arch` | `dense` or `moe`. The dense and MoE filters and auto fit's goals use it. |
| `quant` | The quantization label, for example `Q4_K_M` |
| `rank` | The quality order, 1 = best (the catalogue's Qwen models have ranks 1–6). Sort by quality uses it. |
| `thinking` | `on-off` (thinking on or off only) or `effort` (effort levels) |
| `auto_fit` | yes / no (default no): auto fit can pick this model. It needs `rank` and `arch`, and a stock model. Your rank is not measured, so a custom model takes part in auto fit only when you switch this on. |
| `pick_instead` | Other models, and when they are the better pick |

**In the dashboard:**
1. Push `5` for the Settings tab.
2. Push `]` until the **Models** panel shows.
3. Select the model.
4. Push `e` (or click **[ Edit card (e) ]**). The form has one row for each field.
5. Edit the fields:

   | Key | Does |
   |---|---|
   | ↑ ↓ | Select a field |
   | Enter | Edit the field. Type the text, then push Enter to keep it or Esc to drop it. Paste works. |
   | ← → or space | Change a choice, or tick a tag |
   | `x` | Clear the field |
   | **+ add a model** (under *pick instead*) | Opens a list of models. Then type when it is the better pick. |

6. Push `s` (or click **[ Save ]**). CARL checks the card and saves it. An error shows in the form, and nothing is saved until the card is valid. Esc cancels.

The footer does not show the keys of the form. `[` and `]` do not change the panel while the form is open.

When the card has no `arch` or `quant`, the form fills them in from the GGUF header.

**On the command line:**
```bash
./carl.sh card NAME                                   # show a model's card (catalogue or yours)
./carl.sh card NAME set role "Fast local coder"
./carl.sh card NAME set good_for "agent coding,hard code"
./carl.sh card NAME set rank 4
./carl.sh card NAME set pick_instead qwen3.8-27b="harder code" qwen3.6-35b-a3b="long chats"
./carl.sh card NAME unset rank
```

- `set` checks the whole card. A bad value shows `error: …`, and the command exits with 1.
- Yes / no fields take `yes`, `no`, `true`, `false`, `on`, `off`.
- `pick_instead` also takes a JSON list: `'[{"model": "qwen3.8-27b", "when": "harder code"}]'`.

**Notes:**
- The card is saved in `~/.config/carl/models.json`, under the model (`card`). A delete of the model removes its card. A `pick_instead` entry whose model is gone later is left out.
- **Catalogue models are read-only.** `e` and `card NAME set` tell you this. Their cards come from `host/catalog.json`.
- **When the catalogue adds a model that you added yourself** (the same file in the models folder), the catalogue entry takes it over. Its Auto-tune result and your `config.json` profile move to the catalogue name. Your card for it no longer applies. CARL tells you this one time.
- **thinking:** without a value in the card, CARL reads it from the GGUF header. The chat template has effort levels, or thinking on and off only. The edit form starts with that value.
- After you save, the MODEL card, the role and the tags in each model list, sort by quality and the filters use the card at once.

### Auto-tune

Auto-tune measures the best settings for one model on this Mac. It takes about 5–10 min.

```bash
./carl.sh tune qwen3.8-27b-iq3            # all modes
./carl.sh tune qwen3.8-27b-iq3 --quick    # no MTP modes with 2 drafts, no 64K read, no parallel step (about 4 min)
./carl.sh tune qwen3.8-27b-iq3 --long     # also reads 128K and 192K, and the decode speed at each depth (+10-40 min)
./carl.sh tune all                        # every downloaded model, one after the other
```

It does these steps. The model loads one time for each speculation mode.
1. **Memory:** it finds the largest context window that fits, with 1 slot and with 2 slots.
2. **Speculation:** it measures these modes:
   - none, and n-gram with 2 drafts;
   - n-gram with 1 draft, when the file has no MTP head (n-gram is then its only speculation);
   - MTP, and MTP + n-gram, with 1 and 2 drafts, when the file has an MTP head (`--quick`: 1 draft only).

   Each mode writes prose, new code and a code re-emit, two times. The score is a weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2). A mode with drafting must be 3% better than a simpler mode to win.
3. **Prompt reading:** a cold read at 8K, 32K and 64K tokens.
   - `--quick`: no 64K read.
   - `--long`: also 128K and 192K, as far as the model's window fits this Mac. It measures the decode speed after each read, and estimates the time before the deep reads start.
   - From these reads, it calculates the time to read a full window. This gives the context zones of this Mac:

     | Cold read of the full window | Zone |
     |---|---|
     | 3 min or less | Fast (green) |
     | 10 min or less | Slow (yellow) |
     | More | Very slow (red) |

   - The fast zone always includes 96K: CARL never shows 96K or less as slow.
4. **Parallel requests** (not with `--quick`): the server stops. `llama-batched-bench` then measures the total decode and read speed with 1, 2, 3 and 4 requests at the same time (as subagents that work together). The MODEL card shows the result.
5. **Result:** q4_0 KV, the best speculation, the context window and the slots (2 if two windows fit).
   - **The context is never less than 96K if 96K fits one slot.** Users need that much context for their work.
   - Above 96K, Auto-tune keeps the catalogue window if a cold read of it is not worse than slow on this Mac, and if it fits. Else, it selects the largest standard window in the fast zone (minimum 96K).
   - If 96K does not fit (for example `orcarouter-27b-q3` on a 24 GB Mac), it selects the largest standard window that fits.

- CARL saves the result in `~/.config/carl/models.json`. Each later start of the model uses it. A value that you set in the settings file has priority ([The settings file](#the-settings-file)).
- `./carl.sh tune all` tunes each downloaded model, one after the other. A model that fails does not stop the others.
- CAUTION: **Auto-tune needs the GPU for itself.** It does not start in these conditions: a server runs on port 8080, another large process (more than 8 GB, `BIG_GB`) is in memory, or a known model server runs. `ALLOW_SECOND_MODEL=1` skips the check for a large process. Stop the server first. The **Auto-tune** panel of the dashboard stops the server for you, and starts it again after the tune.
- Auto-tune uses its own server on port 8093 (`--port` changes it). `--dry-run` measures, but does not save.
- NOTE: The re-emit workload copies `tools/carl_core/adapters/llama_server.py`. Thus, the re-emit scores are not directly comparable with older Auto-tune results.

---

## 10. The dashboard

The dashboard (also called the monitor) is `tools/llama-monitor.py`. It shows the live state of the server, and it changes the server's settings.

### Start and quit

**It runs in the terminal in which you start the server.**
- `./carl.sh` with no arguments opens the dashboard. It attaches to a server on port 8080. If no server runs, it starts llama.cpp with your saved settings. If no model is downloaded, it offers to download auto fit's pick for this Mac.
- `./carl.sh llama` starts the server in the background, in its own process group under `nohup`. Thus, Ctrl-C cannot stop the server by accident. The dashboard then uses the terminal.
- The server writes its log file (`~/models/logs/llama-server-<time>.log`). Its console output goes to `~/models/logs/.console-<port>.out`. The Log tab shows the log file. If that file is missing or empty, it shows the console file.
- If the server stops on its own (a crash, a failed start), the header shows **EXITED**, and the log shows the cause. Then `q` opens a short dialog ("No server runs."): push `q` again to quit.
- `MONITOR=0 ./carl.sh llama` runs the server in the foreground with plain log output. Scripts and `nohup` starts do this automatically.

**To quit:**
1. Push `q` or Ctrl-C, or click **[ Quit ]** (top right).
2. Select an option:

   | Key | Option |
   |---|---|
   | `s` | **Stop server.** The dashboard saves the sessions first. It sends SIGTERM, and SIGKILL after 30 s. |
   | `d` (or `q`) | **Leave it running.** The server continues after you close the terminal. To attach again, run `./carl.sh monitor`. |
   | Esc (or `n`, `c`) | **Cancel** |

### A turn is running

Stop, Apply, Auto-tune and a router load stop the model. If an agent's turn is running, the dashboard asks first:

| Key | Option | What occurs |
|---|---|---|
| `w` | **Wait for the turn to end** | CARL waits until the agent's turn ends (all its tool calls and its last reply) and its session is saved. Then it stops. OpenCode and Pi with CARL mark each turn. For a different client, CARL waits for an idle slot. |
| `y` (or `s`) | **Now** | The reply stops. The client shows an error (Pi tries again), and the session goes back to its last save. The earlier turns stay in the client. |
| Esc (or `n`) | **Cancel** | Nothing changes. |

- "Idle" means two checks in a row, 0.5 s apart: no busy slot, no turn mark, and no new save in the last 2 s. The wait also ends if the server stops.
- There is no time limit. A turn mark that a client did not refresh for 600 s is ignored.

**To attach by hand** (after "leave it running", or from a different terminal):
```bash
./carl.sh monitor                       # finds the server's address itself
./carl.sh monitor --port 8081           # a server on a different port
./carl.sh monitor --once --expand       # show one snapshot with every card in full detail
```

### Layout

| Part | Contents |
|---|---|
| **Header** | The logo, a status badge, the model (in router mode: `llama.cpp router (N models)`), the uptime, the clock and **[ Quit ]**. The CARL logo shows in iTerm2, Ghostty, WezTerm and kitty. Other terminals show 😎. `CARL_LOGO=0` turns the logo off. |
| **Tabs** | 1 Overview, 2 Connect, 3 Requests, 4 Log, 5 Settings. Click a tab, or push `1`–`5` or Tab. |
| **Footer** | The keys of the panel that you see, then `1-5 tabs · q quit · ? all keys`. A message or the progress of a job replaces it for a short time. |

| Badge | Meaning |
|---|---|
| IDLE | The server waits for a request. |
| READING | The server reads a prompt. |
| GENERATING | The server writes a reply. |
| BUSY ×N | N slots work at the same time. |
| LOADING | The model loads. |
| UP | A server runs, but it does not show its slots. |
| OFFLINE | No server answers. |
| EXITED | The server that the dashboard started stopped. |

**How a panel is laid out** (Settings panels and the Connect tab):
- The controls and the data are on the left.
- On a wide terminal, the explanations are in a column on the right. The card must have 140 columns or more, with at least 44 for the explanations. On a narrower terminal, they are below the controls.
- The explanations start with a **Quick tip**: one line about what you selected, and the key that acts on it.
- Then come short sections, each with its own header (for example **About this setting** and **Status** in the Server panel).
- The keys are not in the text. The **footer** shows the keys of the panel that you see. `?` opens a card with every key of that panel and of every tab. Push `?` again to close it.
- `CARL_DEMO=1` masks the key and the home folder (for screenshots).

| Panel | Sections of the side column |
|---|---|
| Connect, Setup | On this Mac, In a VM, Other computers, By hand |
| Connect, Clients | Who is in the list, Add a computer |
| Server | About this setting, Status, Colours |
| Models | Always below the list: Selected model, The list, Models folder |
| Auto fit | How auto fit picks, This Mac, Goal, From, The ranking, The other goal |
| Auto-tune | This model, Mode, What it measures (all models: All models, Mode) |
| Router | Who changes the model, Warning, Settings per model, Load and Unload (router mode only) |
| Caching | Experimental, How it works, Save, SWA models, RAM cache |

**Mouse:** use the left click only (titles, tabs, buttons). The wheel scrolls 3 lines. While a dialog is open, only its buttons work.

### The tabs

| Tab | Shows |
|---|---|
| **1 Overview** | Cards in two columns (from 100 columns; else one column): CONNECT, CONTEXT, MEMORY on the left; ACTIVITY, MODEL, HEALTH, SYSTEM on the right. Below the cards: the last 3 requests (8 at full detail) and the last 6 log lines. |
| **2 Connect** | Two sub-tabs. **Setup:** the endpoint, the model, the API key, who can reach the server, the connected clients, the key file, and the setup steps for Mac and VM clients. **Clients:** each client computer and its config ([Connect tab](#connect-tab)). |
| **3 Requests** | All finished requests in the log, newest first: start time, context size, new tokens, read speed, output tokens, generation speed, duration, draft acceptance, prompt tokens from the cache. The title shows the averages. |
| **4 Log** | The full server log, which you can scroll. `w` wraps the lines, `f` shows only errors and warnings, End (or **follow**) follows the newest line. |
| **5 Settings** | Six panels: **Server**, **Models**, **Auto fit**, **Auto-tune**, **Router**, **Caching** ([The Settings tab](#the-settings-tab)) |

### Overview cards

Click the title of a card to see more detail. Click again to see full detail. Click once more to collapse the card. The dots after the title show the level: `○○` collapsed, `●○` normal, `●●` full detail.

Each card keeps the same height while the server works. If a value is not available, the card shows `0`, `N/A` or `none`.

| Card | Normal | Full detail |
|---|---|---|
| CONNECT | Endpoint, model, API key (masked), reachable from, clients, the disk cache, copy buttons | Key file, install commands (this Mac: `./carl.sh install`) |
| CONTEXT | Fill bar, **KV quantization** (K, V), **KV cache RAM** (allocated / in use), recurrent state + checkpoints, total now / maximum | Per-token calculation, MTP-head estimate, cache limits, served and trained window |
| MEMORY | Weights, context (KV + state), other buffers | GPU limit, GPU memory now |
| ACTIVITY | What it does now. When it reads a prompt: progress and **ETA**. When it generates: speed. Averages, last request, draft acceptance. | Acceptance by draft position, totals, queue, peak context |
| MODEL | File, weight quant and size, speculation | Architecture and layer mix, experts, thinking control, batch, flash attention, PID |
| HEALTH | Log error and warning counts, **BROKEN** alarm on GPU out-of-memory or compute errors, kept awake, sleeps since start | Last errors, recent sleep and wake events |
| SYSTEM | Memory pressure (title), RAM, swap, GPU, power, thermal | Wired, compressed and free memory, CPU and load, free disk |

### Connect tab

**Setup sub-tab:**

| Button | Key | Does |
|---|---|---|
| **[ Install on this Mac ]** | `i`, then `y` | Runs `./carl.sh install` and shows its output. `n` or Esc: no. `x` closes the output. |
| **[ Update configs only ]** | `u`, then `y` | Runs `./carl.sh install --config-only` |
| **[ OpenCode config ]**, **[ Pi config ]**, **[ curl test ]** | `o`, `p`, `t` | Copies the snippet to the clipboard and shows it below |
| **Push config to clients** | `P` | Publishes the client config to the clients on other computers |

- The screen masks the key. Push `k` to show or hide it. The copy has the real key.
- CAUTION: Protect a copied config as you protect the key.
- When the OpenCode and Pi configs on this Mac do not agree with the installed models, the tab shows **2 Connect ⚠**. Push `u` to update them.

**Clients sub-tab.** Push `[` or `]` to change between Setup and Clients.
- It lists this Mac (its OpenCode and Pi configs) and each computer that syncs.
- For each computer: the host name, the user, the system, the address, the service or the start check, connected or the last time seen, and its config against the pushed config.
- **Forget clients not seen for a week** removes old rows.
- More: [Clients on other computers](reference/client-sync.md).

### The Settings tab

The Settings tab (tab 5) has six panels: **Server**, **Models**, **Auto fit**, **Auto-tune**, **Router** ([Router mode](#router-mode-switch-models-from-opencode-or-pi)) and **Caching** ([The disk cache](#7-fast-starts-the-disk-cache)). Push `[` or `]`, or click the name of a panel, to change the panel.

#### Server panel

**To change the server settings:**
1. Push `5`, or click **5 Settings**.
2. Push ↑ ↓ to select a row. The rows are the llama.cpp settings: model, KV cache, context/slot, slots, speculation, draft tokens, RAM cache, network, temperature, presence.
3. Push ← → (or click `[<]` `[>]`) to change the value. On a number row, push Enter to type a value. A `*` shows a value that is different from the server that runs (not on the model, slots, RAM cache, network and advanced rows).
4. Look at the colour of the values:

   | Colour | Meaning |
   |---|---|
   | Green | The tuned value for this model, or a fast setting |
   | Yellow | Changed from the tuned value, or slower |
   | Red | Very slow, or does not work on this model. For example: a context in the very slow zone, MTP speculation on a file without an MTP head, or MTP with more than 1 draft on an IQ quant. |

   A context of 96K or less is never yellow or red: 96K for each slot is the floor of the default window. Only larger windows get a warning. You can still select them.
5. Read the **MODEL** card below the settings. It tells you what the model is for, and why to select it.
6. Read the **Status** section. Its **fit** line tells you if the model is downloaded, and if it fits in the GPU memory with these settings. If it does not fit, you cannot apply the settings (the launcher also refuses the start).
7. Push `a` (or click **[ Apply and restart ]**; with no server: **[ Start server ]**). The dashboard refuses a setup that does not fit.
8. Push `y` to confirm.
9. Wait while the model loads (about 30 s to 2 min). The footer shows the progress.

**The rows:**
- **model:** push Enter (or click the model name) to open a list of all models: the catalogue, the models folder and your Hugging Face downloads.
  - `auto` is auto fit's pick for this Mac (`★`).
  - When you select a model, the rows change to the settings of that model: your profile, else its Auto-tune result, else its catalogue values.
- **network:** local (the default: this Mac only), vm (a VMware Fusion VM client too), and each address of this Mac (for example the LAN address). An address is saved as `llama.host`.
- **slots:** 3 and 4 show only when they fit this Mac.

**The model list beside the settings** (from 130 columns; below them on a narrower terminal):
- Click a model. Or push `m` to move the keys to the list, then ↑ ↓ and Enter. `m` or Esc gives the keys back to the settings.
- The list only selects. All the facts about the model are on the MODEL card.

| Mark | Meaning |
|---|---|
| `●` | Downloaded |
| `○` | Not downloaded |
| `★ auto → NAME` | The model that `auto` starts |
| `★` after a name | Auto fit's pick |
| A red name | Too large for this Mac (less than a 32K window) |

Above the list, `sort: … ▾ 1/5` and `show: … ▾ 1/10` open a list of each option when you click them. `s` / `S` and `f` / `F` step through them.

**The MODEL card.** Click its title to change the detail: collapsed (name, role and tags in the title), normal, full. `e` / `c` expand or collapse all cards. The wheel or PgUp / PgDn scroll the panel.

| Level | Contents |
|---|---|
| With `auto` (or auto fit's pick) selected | The card starts with **Auto fit**. It tells why auto fit picked the model: the goal, the scope, the plan, and the memory that it needs of what this Mac allows. It tells what a start uses while the pick is not downloaded. It lists the better-ranked models that auto fit passed over, with the reason for each. |
| Normal | The role, the **good for** tags, **why use it**, the **trade-offs**, and the hardware it is for. The speed: measured on this Mac after Auto-tune, else the catalogue figure and the Mac it came from. The recommended values next to yours, the context zones, and **why** the selected value is tuned that way. |
| Full | Also what *uncensored* means (abliterated models), the models to **pick instead** and when, and the quality **rank**. Then the description, the reason for each tuned value, the Auto-tune table, and the source and the file. |

**The side column** (on a narrow terminal: below the buttons):

| Section | Contents |
|---|---|
| **Quick tip** | How to change the selected row |
| **About this setting** | What the selected row does |
| **Status** | The **fit** line, auto fit's pick in one line, the settings file |
| **Colours** | What the colours mean |

For a model with sliding-window layers (Gemma), the fit line tells you which cache the start gets: the full cache (saved prompts go back) or the window only (Settings > Caching, SWA models).

**Other keys of the Server panel:**

| Key | Does |
|---|---|
| `A` | Opens the Auto fit panel. Or click the **auto fit** line under Status. |
| `x` | **Tuned values:** sets the defaults and the tuned values of the selected model. Then apply. |
| `r` | Discards your changes, and shows the values of the server that runs |

**Notes:**
- CAUTION: **Apply stops the server.** If an agent's turn is running, the dashboard asks first ([A turn is running](#a-turn-is-running)).
- **If the new server does not start,** the dashboard starts the old server again with its old values. A message shows the last lines of the error.
- **The dashboard saves the settings in `~/.config/carl/config.json`** ([The settings file](#the-settings-file)). `./carl.sh llama` uses this file the next time. Flags and environment variables have priority over the file.
  - Server-wide values go to the `llama` section. The dashboard writes only the values that are different from the defaults.
  - Model values (KV cache, context, slots, speculation, sampling) go to the profile of the model (`models.<name>`). The dashboard writes only the values that are different from the tuned values of that model.
- **After a change of the model, the context or the slots,** run `install.sh` again on each client. The clients then get the new context limit, and the coder subagent is added or removed.
- **If the catalogue or `models.json` cannot be loaded** (for example after a bad edit), the Settings tab shows a **SETTINGS UNAVAILABLE** card with the error. The other tabs continue to work.

**Advanced settings:**
1. Select the row **advanced**, and push → to show the advanced rows.
2. Select a row. Push ← → to select a preset value.
3. To type a value, push Enter. Type the number (for example `0.05`, or `96k` for a context).
4. Push Enter to keep the value, or Esc to cancel.

| Advanced rows (default) | Launcher variable |
|---|---|
| top_k (20), top_p (0.95), min_p (0), repeat penalty (1.0) | `TOP_K`, `TOP_P`, `MIN_P`, `REPEAT` |
| -ub batch (512), checkpoints (8), checkpoint step (4096) | `UB`, `CKPT`, `CKPT_STEP` (and `BATCH` for `-b`, default 2048) |

- CAUTION: **The default values are tuned and measured** ([The llama.cpp server](reference/server.md)). A change can make the model slower, or its answers worse. To go back, push `x` (Tuned values) and apply.
- The dashboard saves the advanced values in the same settings file. You can also set them as environment variables, for example `TOP_K=40 ./carl.sh llama`.

#### Models panel

The Models panel lists the catalogue models and each `.gguf` in the models folder. For each model: the size, the status (downloaded, partial, missing), if it fits this Mac, the speed, the role and the "good for" tags.

| Key | Does |
|---|---|
| ↑ ↓ | Select a model |
| Enter | Use this model. The Server panel opens with it. Push `a` there to start it. |
| `d` | Download (only a model with a Hugging Face source). A progress bar shows the speed and the ETA, then the checksum check. |
| `c` | Cancel the download. The partial file stays, and a new download continues it. |
| `v` | Verify the SHA-256 (about 1 min) |
| `x` | Delete the file (a downloaded or partial file). It asks first. It refuses the loaded model. |
| `u` | Open the Auto-tune panel for this model |
| `h` | Add a model from Hugging Face. Type `OWNER/REPO` (or a URL to a `.gguf`). A list of the GGUF files of the repo opens. Select one and push Enter to download it. |
| `e` | Edit the card of a custom model ([Cards for custom models](#cards-for-custom-models)). On a catalogue model, `e` tells you that its card is read-only. |
| `s` / `S`, `f` / `F` | Sort (next / previous), filter (next / previous) |

- **Sort and filter:** the two rows of chips above the list show each option, with the current one highlighted. Click one, or step with the keys.
  - Sort: downloaded first, quality (the rank), speed (measured: fastest first), size, name.
  - Show: all, a use case (agent coding, hard code, chat & writing, uncensored), stock, dense, MoE, downloaded, fits this Mac.
  - The same order applies to each model list (the Server panel's list and the model list of the model row).
- **Below the list** (the list keeps the full width for its role and good-for columns): the description, the source and the file of the selected model.
  - Its best Auto-tune result on this Mac shows in one line: the speculation that it selected, its prose, code and re-emit speeds, the KV cache, the window and the slots. The Auto-tune panel shows the full result.
- **The speed column** is Auto-tune's score (a weighted mean of its prose, code and re-emit tok/s):

  | Look | Meaning |
  |---|---|
  | Green | Measured on this Mac by Auto-tune |
  | Dim | The catalogue's figure from a different Mac (the model card names the Mac) |
  | `?` | Never measured |

  The speed sort uses this column, fastest first. Models without a figure come last: MoE before dense, then smaller files. The ranking of the Auto fit panel shows the same column.

#### Auto fit panel

The Auto fit panel shows the full [auto fit](#auto-fit-the-best-model-for-this-mac) answer for this Mac.

| Part | Contents |
|---|---|
| **Goal** and **From** | `everyday` or `hard code`; all catalogue models or downloaded models only. Click one, or push `g` / `f` for the other one. They are saved at once (`llama.auto_goal`, `llama.auto_fit`), and `model auto` starts the new pick. |
| **This Mac** | The GPU limit, the RAM less the reserve for macOS and apps (10 GiB while VMware's network is up, else 6), and what that leaves for a model |
| **The pick** | The model, its plan (slots × window, KV cache), the memory that it needs, if it is downloaded, and why it was picked. What `model auto` starts until it is downloaded. Each better-ranked model that it passed over, with the reason. |
| **[ Use this ]** (Enter) | The Server panel gets the pick with its context, slots and KV cache. Push `a` there to start it. If the pick is not downloaded, the dashboard asks if it must download it. |
| **[ Download it ]** (`d`) | Downloads the pick here, with the progress in the panel |
| **The other goal** and **the ranking** | The other goal's pick in one line. Then each model by rank: its arch, its weights, the largest window that fits this Mac (1 slot, q4_0), and if it is here. Last, what auto fit made of it: the pick, passed over and why, abliterated, or a custom model not opted in. |

↑ ↓, PgUp / PgDn or the wheel scroll the panel.

#### Auto-tune panel

1. Select a model with ← → (or click its name for a list). Only downloaded models are in the list.
2. Select the mode: **quick**, **default** or **long**. Click one, or push space for the next one.
3. Push Enter to run Auto-tune ([Auto-tune](#auto-tune)). The panel shows each step and the last lines of its output. `c` cancels.

- **All models:** push ← from the first model (or select **all downloaded models** in the list). Auto-tune then tunes each downloaded model, one after the other. A model that fails does not stop the others. The panel shows the last result of each model. On the command line: `./carl.sh tune all`.
- CAUTION: Auto-tune needs the GPU for itself. If a server runs, the panel asks first (and, if an agent's turn is running, if it must wait for its end). Then it stops the server, runs the tune, and starts the server again with the saved settings and the new tune.
- **Last result:** the date, the Mac, the selected settings, the speed of each speculation mode (prose, code, re-emit), the prompt read speeds and the context zones.
- **[ Use these values (clear my overrides) ]** removes your own values for this model from `config.json`. Then the tuned values apply.

#### Router panel

See [Router mode](#router-mode-switch-models-from-opencode-or-pi).

#### Caching panel

The Caching panel (EXPERIMENTAL) controls the [disk cache](#7-fast-starts-the-disk-cache).
- It works with the model that runs. The saved states of a different model wait for that model.
- Each change is saved at once (the `cache` section of `config.json`). It needs no restart.

| Row | Key | Choices |
|---|---|---|
| **Disk limit** | `d` (next value) | 2, 5, 10 (default), 20 or 50 GB (or a value from `config.json`). A lower limit removes the oldest files at once. The dashboard also checks the limit each minute. |
| **Prompts** | `p` | On or off, for OpenCode and Pi on this Mac. Off stops new saves and restores. The files stay until you clear them. |
| **Sessions** | `s` | On or off, as Prompts |
| **Save** | `o` | When a session is saved: auto (default), every turn, on a switch, before a stop ([When a session is saved](#when-a-session-is-saved)). The line below it tells what the choice means. |
| **Auto after** | `t` | For save = auto: the reading time of the part that is not saved before a save: 30 s, 2 min (default), 5 min, 10 min |
| **Shared** | `h` | Store conversations as patches against their prompt (on, the default), or whole |
| **SWA models** | `w` | auto, full cache, window only (models with sliding-window layers; from the next start) |
| **[ Clear the disk cache ]** | `c` | Asks, then removes each saved state. The server keeps what it holds now. |

- The tab label is **Caching (exp.)**.
- **On disk:** the space used of the limit, the folder, and up to 12 files: a prompt (model · agent) or a conversation (model · session), its size and its age.
- The RAM prompt cache (llama.cpp's own, lost when the server stops) is the **RAM cache** row of the Server panel.

### Keys

The footer shows the keys of the panel that you see. `?` shows all of them.

| Key | Does |
|---|---|
| `1`–`5`, Tab | Change the tab |
| `o` / `p` / `t` | Copy the OpenCode config / the Pi config / the curl test (opens the Connect tab) |
| `i` / `u`, then `y` | Connect tab: install OpenCode and Pi and their configs / update the configs only (`n` or Esc: no). `x` closes the installer's output. |
| `[` / `]` | Connect tab: the Setup and Clients sub-tabs. Settings tab: the previous / next panel. |
| `P` | Connect tab: push the client config to the clients on other computers |
| `k` | Show or hide the API key |
| `e` / `c` | Expand / collapse all cards |
| `w` / `f` | Log: wrap / errors and warnings only (on each tab) |
| ↑ ↓ PgUp PgDn, wheel | Scroll the current tab. End: go back to the newest log line (follow). |
| `+` / `-` | More / fewer log lines on the Overview (2 at a time, 2 to 60) |
| space | Refresh now |
| `?` | A card with every key of this panel and of every tab (`?` again closes it) |
| `q`, Ctrl-C | Quit (asks: stop / leave running / cancel) |
| ↑ ↓, ← →, Enter, `a`, `A`, `r`, `x`, `m`, `s` `S` `f` `F` | Settings tab, Server panel: select a row, change the value, open the model list (model row) or type a value (number rows), apply, Auto fit panel, revert, tuned values, keys to the model list, sort and filter the list |
| ↑ ↓, Enter, `d`, `v`, `u`, `x`, `h`, `c`, `e`, `s` `S` `f` `F` | Settings tab, Models panel: select a model, use it, download, verify, Auto-tune, delete, add from Hugging Face, cancel the download, edit the card, sort and filter |
| `g`, `f`, Enter, `d`, ↑ ↓ | Settings tab, Auto fit panel: the other goal, the other model set, use the pick, download it, scroll |
| ← →, space, Enter, `c` | Settings tab, Auto-tune panel: select a model, the mode, run, cancel |
| `d` `p` `s` `o` `t` `h` `w` `c` | Settings tab, Caching panel: disk limit, prompts, sessions, when to save, auto after, shared, SWA models, clear |
| `w` / `y` / Esc | A turn is running (Stop, Apply, Auto-tune, a router load): wait for the end of the turn / now / cancel |
| Click | Settings tab, Router panel: the mode, **Load**, **Unload** (the panel has no keys) |

`/carl` in OpenCode or Pi shows each CARL piece on that computer with its state, and the config sync ([The /carl panel](#carls-plugins-and-extensions)).

**Notes:**
- **The dashboard watches the server, and changes it only when you tell it to.**
  - It reads `/health`, `/slots`, `/metrics`, `/props`, `/v1/models` (and `/models` in router mode) and the log file. Thus, it is safe to use during a session.
  - It writes only for an action: it saves slots before a stop (`/slots/N?action=save`), and loads or unloads a model in router mode (`/models/load`, `/models/unload`).
  - It serves its own API on the server's port + 1, for the clients' cache and config sync ([Clients on other computers](reference/client-sync.md)).
- **The dashboard calculates the context memory. It does not measure it.** The server does not log it. Thus, the dashboard calculates it from the GGUF metadata of the model and the server flags ([Context memory](reference/memory.md#context-memory-kv-cache-and-recurrent-state)).

### Auto fit: the best model for this Mac

Auto fit picks the best **stock** model that fits this Mac, for a goal:

| Goal (`llama.auto_goal`) | Family first | Why |
|---|---|---|
| `everyday` (default) | The MoE builds (35B-A3B) | Fast, and usually sufficient. CARL gives priority to speed. |
| `hard-code` | The dense builds (27B) | Better at code and hard tasks, but slower |

- **Quality** is the catalogue `rank` (1 = best): parameters and density first, then the quantization.
- **The rule:** in the goal's family, the best rank that holds **two 96K windows** (the main session and a coder subagent). If none does, one 96K window. If none does, the largest window of at least 32K. If no build of the family fits, the best of the other family (it tells you).
- **Memory:** the smaller of the GPU limit and the RAM less a reserve for macOS and apps (6 GiB; 10 GiB while VMware's network is up; `RESERVE_GB` or `--reserve-gb`).
- **Stock only:** auto fit and each automatic default never pick an abliterated model. You select those by hand.
- **Custom models** (Hugging Face, the models folder) are candidates only when their [card](#cards-for-custom-models) switches `auto_fit` on (with a rank and an arch, and not abliterated). Your rank is not measured.
- **Gemma 4 models** have no rank, so auto fit does not pick them.
- `./carl.sh download default` and the download offer name only catalogue models.
- **Candidates (`llama.auto_fit`):**
  - `catalogue` (default): every catalogue model. Auto fit offers the download of the pick. Until then, a start with `model auto` uses the best downloaded model that fits.
  - `downloaded`: only the models on this Mac.
- **Where auto fit is used:** `llama.model = auto`, `./carl.sh download default`, the download offer of `./carl.sh` on a new Mac, the `auto` entry of the model list, and the **Auto fit** panel.

`./carl.sh fit` shows the pick for each goal, and why each better-ranked model was passed over. `./carl.sh fit --ram 24` (or 16, 36, 64, …) shows a different Mac. The picks, all with 2 × 96K:

| RAM | everyday | hard code |
|---|---|---|
| 16 GB | `qwen3.8-9b` (no MoE build fits, so the everyday goal falls back to it) | `qwen3.8-9b` |
| 24 GB | `qwen3.6-35b-a3b-iq3` | `qwen3.8-27b-iq3` |
| 32 GB and more | `qwen3.6-35b-a3b` | `qwen3.8-27b` |

- On 32 GB, this is true only while VMware's network is down. With the network up, CARL keeps 10 GiB for macOS and the VM, and the everyday pick becomes the IQ3.
- Previews (`--ram`) estimate the GPU limit at 2/3 of the RAM below 32 GB, and 3/4 from 32 GB. A real Mac reports its own limit.

**A start over the GPU limit is refused.** `serve-llama.sh` (and thus `./carl.sh llama` and the dashboard) checks the setup before the model loads. If it needs more than the GPU limit, it stops. It shows what the setup needs against the limit, the largest window that fits, and auto fit's alternative. Expert override: `FIT_CHECK=0 ./carl.sh llama ...`. The model can then fail to load, or make the Mac swap and become very slow.

### The settings file

The dashboard and the launchers keep your settings in `~/.config/carl/config.json`.

| Section | What it holds |
|---|---|
| `llama` | Server-wide llama.cpp settings: `model` (`auto` = auto fit's pick for this Mac), `auto_goal` (`everyday` \| `hard-code`), `auto_fit` (`catalogue` \| `downloaded`), `mode` (`single` \| `router`), `net` (`local` \| `vm`; default local), `host`, `cache_ram`, `ub`, `batch`, `ckpt`, `ckpt_step`, `think_toggle`, `extra_args` (more `llama-server` flags, as a list) |
| `models.<name>` | The profile of one model: `kv`, `ctx`, `slots`, `spec`, `spec_n`, `temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `alias` |
| `paths` | `models_dir` (default `~/models/gguf`) |
| `cache` | The [disk cache](#7-fast-starts-the-disk-cache): `disk_gb` (default 10), `prefix` (each agent's prompt, default true), `sessions` (each session, default true), `save` (`auto` \| `turn` \| `switch` \| `stop`), `auto_s` (default 120 s), `share` (default true), `swa` (`auto` \| `full` \| `window`) |

- **Order of priority** for a llama.cpp start: command-line flags, then environment variables, then `config.json`, then the Auto-tune result of this Mac, then the catalogue (`host/catalog.json`), then the built-in defaults.
- **CARL checks the file.** Each value must have the correct type, range or choice. A bad value stops the start with an error that names the key. CARL ignores an unknown key.
- **Earlier versions used `llama.env`** in the same folder. If `config.json` does not exist, CARL copies its values into it one time. After that, CARL does not read the old file.
- **A file from before 1.2.0 can hold settings of features that 1.2.0 removed** (CHANGELOG.md). It still loads: CARL ignores them (`./carl.sh config show` warns about a removed section). They go away the next time that CARL saves the file.
- `SETTINGS_FILE=none ./carl.sh llama` ignores `config.json`. The Auto-tune result and the catalogue still apply.
- To go back to the defaults, delete the file, or remove a value with `config unset`.

```bash
./carl.sh config show                                 # the file, then each key with its type and default
./carl.sh config get llama.net
./carl.sh config set models.qwen3.6-35b-a3b.ctx 128k  # sizes as N or Nk
./carl.sh config unset models.qwen3.6-35b-a3b.ctx
./carl.sh config path                                 # where the file is
```

### Log files

**Mac:**
```bash
tools/llama-log.sh -f                     # follow the current server log (plain text)
tools/llama-log.sh | grep "prompt processing"   # prompt progress lines
ls ~/models/logs/                         # one log per server start; llama-server-latest.log = the newest start
```

**How to read the progress of a request:**

| Log line | Meaning |
|---|---|
| `prompt processing, n_tokens = 8192, progress = 0.11, ... / 94.45 tokens per second` | The progress of the prompt read, about every 2K tokens |
| `prompt eval time = ... tokens per second` | The prompt read is complete. |
| `eval time = ... tokens per second` | The generation speed |
| `draft acceptance = 0.875` | How frequently the speculation guessed correctly |
| `n_tokens = 8961, truncated = 0` | The request is complete, and its size |

**Quick server checks (Mac):**
```bash
K=$(cat ~/.config/carl/api-key)
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:8080/v1/models          # which model is loaded
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:8080/props | python3 -c 'import json,sys; print(json.load(sys.stdin)["default_generation_settings"]["n_ctx"])'   # context window
PID=$(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')   # the server process
ps -o rss=,command= -p $PID                                                        # memory + exact flags
```

For a server that started with `--vm`, use `192.168.42.1` in place of `127.0.0.1`.

NOTE: CARL does not use `lsof`. On a Mac with a stale network share (for example a Time Machine SMB volume that is disconnected), `lsof` can hang, and Ctrl-C cannot stop it. `netstat -anv` shows the process ID in its `process:pid` column.

---

## 11. Updating the client configs

Run the installer again in these conditions:
- You downloaded or deleted a model. The lists show only the installed models. The dashboard's Connect tab warns when they are out of date: the tab shows ⚠, and `u` there updates the configs of this Mac.
- The bundle in `client/` changed (new options).
- You restarted the server with a different `--ctx`, model or number of slots.

**On the server Mac,** run `./carl.sh install --config-only` from the CARL folder, or push `u` in the dashboard's Connect tab.

**On other computers,** push the config from the dashboard: push `P` in the Connect tab, or run `./carl.sh push`. The sync service on each computer applies it ([Clients on other computers](reference/client-sync.md)).

**For a VM without the sync service:**
1. **Mac:** run `./carl.sh install --config-only`. It writes the list of installed models to `client/installed-models.json`. The copy of the client folder carries the list into the VM.
2. **Mac:** if `client/` changed, copy the bundle to the shared folder:
   ```bash
   rsync -a --delete client/ ~/Documents/carl-vm-client/
   ```
3. **VM:** copy the bundle in, and run the installer:
   ```bash
   cp -r /mnt/data/Documents/carl-vm-client/. ~/carl-vm-client/ && cd ~/carl-vm-client && ./install.sh
   ```

Without `installed-models.json` (a bundle from before 1.3.0), `install.sh` lists only what the server reports. In the dashboard's mode, this is the model that runs.

Then **fully restart OpenCode or Pi.**

### What `install.sh` does

**Backups.** Before it changes a config file that exists, it makes backups:

| Backup | Contents |
|---|---|
| `FILE.before-carl` | Your original file, from before CARL changed it the first time. The installer never overwrites this copy. |
| `FILE.bak.<timestamp>` | The version from before each later change |

- The summary shows the backups as `backed up`. If nothing changes, the installer writes nothing and makes no backup.
- To go back to your own config, copy `FILE.before-carl` back to `FILE`. For example: `cp ~/.config/opencode/opencode.json.before-carl ~/.config/opencode/opencode.json`.

**The API key.** It stores the key at `~/.config/carl/api-key` (mode 600). The configs refer to this key file. They do not contain the key.
- It takes the key from the first of these sources:
  1. `--key KEY` or `--key-file FILE`
  2. `$CARL_API_KEY`
  3. A file `api-key` next to `install.sh`
  4. `~/.config/carl/api-key` (the server's key on the Mac, or the key from an earlier run)
  5. `~/.config/llm-deploy/api-key`
  6. With `--local`: `~/.mtplx/api-key`
  7. `~/.config/mtplx/api-key`
  8. A prompt (the key does not show when you type it)
- Before 1.2.0, the client copy was `~/.config/mtplx/api-key`. If the installer finds no other key, it uses that one. It changes old configs to the new path. It does not delete the old file, because a provider of your own can use it. Delete it yourself when nothing uses it.

**The models.** It writes one entry for each installed model, under the model's own name.
- Each entry gets its family's thinking options (custom models: the *thinking* field of their card) and its context window. The model that runs gets the server's window. The others get their settings.
- The default model (`model` / `small_model`, Pi's `defaultModel`) becomes the model that a server start loads, if the default is still CARL's.
- It replaces the `llamacpp` provider as a complete block. Thus, removed models do not stay in the config. (A deep merge never deletes keys, so old variants stayed in the configs.) It keeps your other providers and settings.
- It sets the llama.cpp context limit from the server.

**The plugins.** It installs and registers CARL's plugins and extensions ([CARL's plugins and extensions](#carls-plugins-and-extensions)).
- It replaces the plugin `carl-prefix-cache` of earlier versions with `carl-cache`.
- It removes the client parts of the server support that 1.2.0 removed (a provider, an OpenCode plugin and a Pi extension; CHANGELOG.md), if an earlier CARL installed them. It removes only CARL's own items, with the usual backups.

**The sync service.** On a computer whose server is elsewhere (with `remote.json`), it adds the client sync service. `NO_SYNC_SERVICE=1` leaves it out.
- It records the switches of this install in `~/.config/carl/client-install.env`. A pushed config is then applied with the same switches, and without changes to your shell profile.
- `NO_CODER`, `CODER` and `NO_SYNC_SERVICE` are not recorded. Thus, a sync decides the coder from the number of slots again.
- The service writes its log to `~/.config/carl/client-sync.log` and `client-sync.err`.

**The smoke test.** At the end, it checks the connection to the server.

**It does not overwrite settings that you own.** `client/configure.py` does the merge:

| Item | Rule |
|---|---|
| Records | It records CARL's items in `carl.json` next to each config (`~/.config/opencode/`, `~/.pi/agent/`). It finds older installs by CARL's provider names and the key path. |
| Providers | If you already have your own provider with the id `llamacpp`, it stays. The installer then adds CARL's provider next to it as `carl`. |
| Default model | It sets the OpenCode `model` / `small_model` and the Pi defaults only if they are unset, or if they still have the value that it set before. If your default model is your own, it stays, and `small_model` follows it. |
| Agents, prompts, extensions | It never replaces your own `coder` agent, Pi `agents/coder.md` or `extensions/subagent`. CARL's coder becomes `carl-coder`, or the installer skips it and shows a note. |
| Lists (`plugin`, `instructions`) | It adds CARL's items to the end of the list, or removes them. It keeps your items. |
| Report | At the end, it shows a summary: added / updated / kept / removed. |
| A second run | With the same input, it changes nothing. |

**Formerly LLM-Deploy.** Before 1.2.0, CARL's files had the name `llm-deploy`: the folder `~/.config/llm-deploy`, the records `llm-deploy.json`, the prompt folder `~/.config/opencode/llm-deploy/`, the provider `llm-deploy` and the coder `llm-deploy-coder`.
- The first `./carl.sh` command (or this installer) moves the folder to `~/.config/carl`. It leaves a link with the old name, so the old configs continue to work.
- The installer then changes CARL's own items to the new names (`carl.json`, `~/.config/opencode/carl/`, `carl`, `carl-coder`), with the usual backups.
- Your own items with these names stay.

### The options of the installer

```bash
./install.sh                 # auto: the address in remote.json; else --vm on Linux (the VM), --local on macOS
./install.sh --vm [HOST]     # server at the VM host address (default 192.168.42.1)
./install.sh --local         # server on this Mac (the address that it listens on, else 127.0.0.1)
./install.sh --host ADDR     # any address
./install.sh --port N        # the llama.cpp port (default 8080)
./install.sh HOST [X] [PORT] # old positional form: X (an old second port) is ignored with a note; PORT = the llama.cpp port
```

---

## 12. Troubleshooting

| Symptom | Cause | Remedy |
|---|---|---|
| `error: MODEL needs N GiB of GPU memory at --ctx ..., but this Mac allows M GiB` at the start (the start is refused) | The model and the context window are larger than the memory that macOS lets the GPU use. The model would fail to load, or swap. | Use the suggested `--ctx`, auto fit's alternative (shown), a smaller build, or q4 KV. See `./carl.sh fit`. Expert override: `FIT_CHECK=0` (it can fail to load, or swap). |
| No dashboard shows; plain server output shows | You did not start the server from a terminal (a script, `nohup`), or `MONITOR=0` is set. | This is correct. To attach, run `./carl.sh monitor`. |
| The dashboard shows **EXITED** directly after the start | The server did not start (bad flag, file not found, out of memory). | Read the log lines on the screen. The full output is in `~/models/logs/.console-8080.out`. |
| You closed the terminal, and you do not know if the server still runs | The server continues to run, because it runs under `nohup`. | To attach again, run `./carl.sh monitor`. Then push `q` → `s` to stop it. |
| Clients on the Mac cannot connect to 127.0.0.1 | The server started for the VM (192.168.42.1). | Run `./carl.sh install --config-only` again (it uses the address on which the server listens). Or start the server without `--vm`. |
| The VM cannot reach 192.168.42.1:8080 | Since 1.3.0, the server serves only this Mac unless you ask for more. | Start it with `./carl.sh --vm`, or set **network** to `vm` in Settings (`llama.net = vm`). The Connect tab tells you which one runs. |
| `error: --vm: no interface has 192.168.42.1` | The Fusion network is not up. | Start VMware Fusion, or use `--local`. |
| The HEALTH card shows **BROKEN** | GPU out-of-memory or compute errors in the log | Restart the server. Make sure that no other large program runs. |
| `error: port 8080 is already in use by: llama-server ...` | A server runs already (one model at a time). | Stop it first: `./carl.sh monitor`, then `q` and `s`. Or use the `kill` command of [Daily use](#stop-the-server-or-keep-it-running). Or only watch it with `./carl.sh monitor`. |
| `error: another large process (probably a model) is in memory` | A model server runs already: llama.cpp, or a server that started in a different way. Two models do not fit in the GPU memory. | Stop the other server first. The message shows its process ID and its size. If the large process is not a model, start with `ALLOW_SECOND_MODEL=1`. |
| The prompt progress stops for many minutes, then continues | The Mac went to sleep (lid closed on battery power, or `KEEP_AWAKE=0`). | Connect the power supply and keep the lid open. To check, run `pmset -g log \| grep -E "Sleep\|Wake"`. |
| All requests fail with `Compute error`, but `/health` says ok | A GPU out-of-memory event. Usually, a second model started at the same time. | Restart the server. CAUTION: Do not run two models at the same time. |
| `HTTP 400 ... exceeds the available context size` | The session became larger than the server `--ctx`, and the client limit is higher. | Restart the server with a larger `--ctx`, or run `install.sh` again so that the client compacts in time. Compact the session manually now. |
| Thinking `none` / off still thinks | You did not restart OpenCode after `install.sh`, or the reply started before the change. | Fully restart OpenCode and send a new message. Make sure that the server has `--chat-template-file` (see the `ps` command in [Log files](#log-files)). |
| `/variants` shows options that must not be there | An old config in the VM | Run the current `install.sh` again (it replaces the providers). Then restart OpenCode. |
| The first message takes minutes | A cold prompt: the system prompt and the tools (~9K), or a long session after a restart without the disk cache | This is correct for the first session. The 35B reads prompts ~6× faster. The [disk cache](#7-fast-starts-the-disk-cache) makes later starts fast. |
| A new session or a session after a restart reads the whole prompt again | The disk cache is off, the prompt changed (a new tool, an update), or a different model file or llama.cpp build | Look at `/carl` and at the Caching panel. After a prompt change, the first new session saves the new prompt. |
| Slow replies late in a long session | The decode speed decreases as the context becomes larger (27B: ~7 tok/s at 60–80K). | Compact the session or start a new session. Or use the 35B. |
| The client shows the wrong model name | The client selection does not control the server. | Select the entry that agrees with `./carl.sh --model ...`. OpenCode's model check warns you. |
| `install.sh` waits at the API key prompt | The installer found no key. | Paste the key, put it in `./api-key`, or set `CARL_API_KEY`. |
| Smoke test: `--: llama.cpp not running on http://HOST:PORT` | The server is not up, or the VM cannot reach the Mac. | Start the server. From the VM, run `curl -s http://192.168.42.1:8080/health`. |
| The download stops or fails its checksum | A network interruption, or a bad file (`*.bad`) | First, delete the `.bad` file. Then run `download` again (it continues from where it stopped). |
| The dashboard or `./carl.sh` freezes, and Ctrl-C does not stop it (versions before 1.1.0) | Old versions used `lsof` to find the server. `lsof` checks each mounted volume. On a stale network share (for example a disconnected Time Machine SMB volume), it hangs, and you cannot stop it. | Update CARL: it now uses `netstat`. To release the hang now, eject the stale volume in Finder (or `diskutil unmount force /Volumes/NAME`), then close the terminal. |
| `CARL needs: llama.cpp aria2 ansifilter zstd (not installed)` | Homebrew tools are missing. | Answer `Y`, and CARL runs `brew install`. Or install them yourself. `SKIP_DEPS=1` skips the check. If Homebrew is missing, install it first (https://brew.sh). |
| `No model is downloaded yet.` | A new installation | Answer `Y` to download auto fit's pick for this Mac. Or answer `n`: the dashboard opens without a server, and you can download a model in its **Models** panel. |
| `auto fit picks X for this Mac, but it is not downloaded (...); starting Y, the best downloaded model that fits` | `llama.model` is `auto`, and auto fit picks from the whole catalogue (`llama.auto_fit catalogue`). | This is correct. To use X, run `./carl.sh download X` (or push `A` in the Settings tab). To pick only from downloaded models, run `./carl.sh config set llama.auto_fit downloaded`. To always use Y, run `./carl.sh config set llama.model Y`. |
| `auto fit: no downloaded stock model fits this Mac` | `llama.model` is `auto`, and only abliterated (or no fitting) models are downloaded. Auto fit never picks an abliterated model. | Download auto fit's pick (`./carl.sh download default`), or select the model by name: `./carl.sh config set llama.model NAME`. |
| `error: models.NAME.ctx: ... is out of range` (or a different key) | A bad value in `~/.config/carl/config.json` | Correct the value, or remove it with `./carl.sh config unset KEY`. `./carl.sh config show` lists the valid keys. |
| `error: a server is running on port 8080: stop it first` from `./carl.sh tune` | Auto-tune needs the GPU for itself. | Stop the server first, or run Auto-tune from the dashboard (Settings, **Auto-tune** panel): it stops and starts the server for you. |
| `error: another large process (probably a model) is in memory` from `./carl.sh tune` | A different model server, or another process larger than 8 GB (`BIG_GB`), runs. | Stop it first. If the process is not a model, run with `ALLOW_SECOND_MODEL=1`. |
| `error: …opencode.json is not plain JSON (comments?)` from `install.sh` | The installer cannot merge a config file with comments (JSONC). | Remove the comments, or move the file. Then run the installer again. It changed nothing. |
| A pushed config does not arrive on a client computer | The sync service is not running, the dashboard is not running, or auto-apply is off. | Look at the Clients sub-tab of the Connect tab, and at `/carl` on the client. With auto-apply off, select **Apply now** in `/carl`. |

**To see exactly what a client sends** (thinking settings, tool counts): see [Verifying behaviour](reference/verifying.md).

---

## 13. Benchmarking and testing

CAUTION: Some of these commands restart the server. Run them only when no session is active and **no other server runs**. The scripts that restart the server (`llama-spec-sweep.sh`, `llama-ab.sh`) use port 8080 on the default address.

The easy way to find the best speculation and context for a model on this Mac is Auto-tune: `./carl.sh tune NAME` ([Auto-tune](#auto-tune)). Stop the server first: Auto-tune does not start while a server runs on port 8080, or while another model is in memory.

```bash
# Speculation configs (model via MODEL=path; draft count via spec:n)
MODEL=$(./host/models.sh path qwen3.6-35b-a3b) LOG_FILE=none \
  tools/llama-spec-sweep.sh none:1 draft-mtp:1 ngram-mod:1 draft-mtp,ngram-mod:1 draft-mtp,ngram-mod:2

# KV-type and batch-size A/B (waits for 20 min idle first)
tools/llama-wait-idle.sh 1200 && tools/llama-ab.sh all
```

| Tool | Purpose |
|---|---|
| `./carl.sh tune NAME [--quick\|--long]` (`tools/carl-tune.py`) | Auto-tune: memory, speculation modes, prompt reading, parallel requests. It saves the result for this Mac in `~/.config/carl/models.json`. |
| `tools/llama-spec-sweep.sh CFG...` + `tools/llama-spec-bench.py` | A sweep of the speculative decoding. The sweep restarts the server for each config. The bench measures the prose, code and edit decode speed. NOTE: the edit workload re-emits the source of `llama-spec-bench.py`, which was rewritten in 1.1.0. Thus, new edit numbers are not directly comparable with older measurements. |
| `tools/llama-ab.sh [kv\|ub\|all]` | An A/B test of the KV type and `-ub`. It uses `tools/llama-kv-longctx.py` and `tools/llama-ab-measure.py`. It restarts the server. |
| `tools/llama-kv-longctx.py` | A ~64K haystack with 8 needles: cold prefill, decode, append, recall |
| `tools/llama-wait-idle.sh [SECS] [BASE]` | Waits until the server stays idle for SECS (default 1200) |
| `tools/llama-sesstest.py` | A multi-turn test of a long session (56K start, ~7K appends). It records the RSS. |
| `tools/req-capture-proxy.py LISTEN UPSTREAM LOG` | A pass-through with a log: it records what a client sends. It refuses a wildcard listen address (`0.0.0.0`, `::`), and it writes the log with mode 600. `--bodies DIR` saves the full requests. |
| `tools/prompt-size.py` | Counts the system prompt and the tool definitions of a captured request (and each tool), with the model's own template and tokenizer |

NOTE: Since 1.2.0, `llama-ab-measure.py`, `llama-kv-longctx.py` and `llama-sesstest.py` build their long prompts from the source files of this folder (Python, shell and JavaScript in `tools/`, `host/` and `client/`). Thus, their numbers are not directly comparable with older runs.

The file headers give more details. `tools/carl_bench.py` holds the helpers that these small tools share (the API key, chat requests, the PID of the server from `netstat`, the server memory).

**Unit tests** (no server and no model necessary), from the CARL folder:
```bash
python3 -m unittest discover -s tests            # tools/carl_core: domain, app, adapters
python3 -m unittest discover -s tests/scripts    # shell helpers, client/configure.py, the JavaScript tests, the small tools
python3 -m unittest discover -s tests/monitor -t tests/monitor   # the dashboard (tools/monitor)
```

**The sync service under systemd** (Docker necessary):
```bash
CARL_DOCKER_TESTS=1 python3 -m unittest tests/integration/test_sync_docker.py
```

---

## 14. Rules of thumb

- **Run only one model server at a time.**
- **Keep the Mac connected to power and awake** during long work.
- **Start OpenCode or Pi in the folder of your project.**
- **Run `install.sh` again and restart the client** after you change the model, the options, the slots or `--ctx`.
- **Select the client model that agrees with the server.**
- **Use thinking `low` as the default** on the 27B. Use `none` for quick, mechanical edits. Use `xhigh` only for difficult problems.
- **Use the 35B-A3B for speed.** Use the abliterated 27B when you need an uncensored model or the best measured quality.
- CAUTION: **Do not bind to `0.0.0.0`.** The macOS firewall is off, so a wildcard bind makes the model available to the LAN. The launchers refuse this address, also through `HOST=0.0.0.0`.

---

## 15. Command reference

**Mac:**

| Command | Does |
|---|---|
| `./carl.sh` | Opens the dashboard: attaches to a server on 8080, else starts llama.cpp with the saved settings. It first checks for `llama-server`, `aria2c`, `ansifilter` and `zstd` (offers `brew install`), and offers to download a model if none is downloaded. |
| `./carl.sh llama` | llama.cpp with auto fit's model (`qwen3.6-35b-a3b`; the IQ3 build on 24 GB; the 9B on 16 GB), q4 KV, 2 slots |
| `./carl.sh monitor` | Attaches the live dashboard (a server start shows it in the same terminal) |
| `./carl.sh --no-start` (or `dashboard`) | The dashboard only: attaches to a server, or opens it offline (no model loads) |
| `./carl.sh llama --local` / `--vm` | Serves only this Mac (the default) / the VM address too |
| `./carl.sh llama --host ADDR` | Serves on one address of this Mac (LAN, Parallels, …); never 0.0.0.0 |
| `./carl.sh --router` / `--single` | Router mode (OpenCode and Pi change models) / one model (the default); `llama.mode` saves the choice |
| `./carl.sh llama --slots 1` … `--slots 4` | One conversation / the main session and subagents (default: auto = 2 when they fit) |
| `./carl.sh --model NAME\|PATH` | A different model (catalogue, models folder or Hugging Face download) or a `.gguf` path |
| `./carl.sh --kv q8` / `--q8` / `--q4` | KV cache type |
| `./carl.sh --ctx 192k` | Context window for each slot (4k to 256k) |
| `./carl.sh install [opencode\|pi] [--config-only\|--clients-only] [--vm\|--host ADDR] [--port N]` | Installs OpenCode and Pi, and connects them to this server |
| `./carl.sh models` | The catalogue, the models folder and your downloads, with the download status |
| `./carl.sh fit [--ram GB] [--ctx N] [--slots N] [--goal everyday\|hard-code] [--scope catalogue\|downloaded] [--reserve-gb N]` | Auto fit's pick for each goal and the reasons; which models fit this Mac, and the largest window of each model |
| `./carl.sh download NAME\|default\|all` | Downloads catalogue models |
| `./carl.sh download hf:OWNER/REPO/FILE.gguf` | Downloads any GGUF from Hugging Face (verified). `hf:OWNER/REPO` lists its GGUF files. |
| `./carl.sh verify [NAME...]` | Verifies downloaded models (size and SHA-256). No names: every downloaded model. |
| `./carl.sh delete NAME` | Deletes a downloaded model file |
| `./carl.sh card NAME [set FIELD VALUE\|unset FIELD]` | A model's card. Custom models: set or remove one field of your card ([Cards for custom models](#cards-for-custom-models)). |
| `./carl.sh tune NAME\|all [--quick\|--long]` | Auto-tunes a model (or every downloaded model) for this Mac: speculation, context window, slots (~5–10 min; quick ~4 min; long +10–40 min, up to 192K; stop the server first) |
| `./carl.sh config [show\|path\|get KEY\|set KEY VALUE\|unset KEY]` | The settings file `~/.config/carl/config.json`. KEY is like `llama.net` or `models.NAME.ctx`. |
| `./carl.sh cache [show\|trim\|clear]` | The [disk cache](#7-fast-starts-the-disk-cache) that OpenCode and Pi fill: the files, trim to the limit (and store new conversations as patches), remove all |
| `./carl.sh push` | Pushes the client config (the installed models) to the clients on other computers ([Clients on other computers](reference/client-sync.md)) |
| `./carl.sh help [TOPIC]` | Help for one topic: llama, monitor, fit, models, card, download, verify, env, tuning |
| `./carl.sh -h` | The help: an overview of all commands. `<command> -h` for one command. |
| `./carl.sh --help-adv` | All `llama-server` flags |
| `./host/models.sh list\|download\|verify\|delete NAME\|path NAME\|get NAME FIELD\|default\|downloaded` | The models tool (a wrapper around `tools/carl.py`; `./carl.sh models\|download\|verify\|delete` use it): list, download, verify, delete, the local path, one field, the default model for this Mac, the downloaded models |
| `tools/make-share-zip.sh [--with-docs] [OUT]` | Makes a clean zip of the folder to share |
| `tools/llama-log.sh [-f] [FILE]` | The server log as plain text (no ANSI codes). `-f` follows the log. |

**Environment variables of the launcher:**

| Variable | Does |
|---|---|
| `CTX`, `KV`, `KV_K`, `KV_V`, `UB` | Context, cache types, batch size |
| `SPEC`, `SPEC_N` | Speculation type and draft count (default: the tune of each model) |
| `TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE`, `REPEAT` | Sampling (defaults: 1.0, 0.95, 20, 0, 0, 1.0) |
| `MODEL`, `ALIAS` | Model path and served name |
| `HOST`, `PORT`, `API_KEY_FILE` | Bind address, port, key file |
| `NET=local\|vm`, `VM_HOST` | Network mode (default local; `auto` from before 1.3.0 now means local) / the VM address (default 192.168.42.1) |
| `LOG_FILE` | The path of the log file. `none` turns off the log file. |
| `THINK_TOGGLE=0` | Use the chat template without the thinking rule |
| `KEEP_AWAKE=0` | Do not keep the Mac awake |
| `FIT_CHECK=0` | Expert override: start also when the setup needs more than the GPU limit (else the memory check refuses it) |
| `SLOTS` | Slots (default auto) |
| `CACHE_RAM` | The RAM prompt cache in MiB (default: sized from the free RAM, 1–8 GiB) |
| `RESERVE_GB` | The RAM that stays free for macOS and apps. The server uses it to size the cache, and auto fit to pick a model (default 6, 10 with the VM network up). |
| `MONITOR=0` | No dashboard: the server runs in the foreground |
| `ALLOW_SECOND_MODEL=1` | Start also when a process larger than 8 GB (`BIG_GB`) is in memory. CAUTION: a second model can stop the Mac. |
| `SETTINGS_FILE=none` | Ignore the saved settings in `~/.config/carl/config.json` |
| `MODELS_DIR` | The models folder (default `~/models/gguf`; also `paths.models_dir` in `config.json`) |
| `SKIP_DEPS=1` | Do not check for the Homebrew tools (`llama-server`, `aria2c`, `ansifilter`, `zstd`) |

The script sends all other arguments after the flags to `llama-server`.

**Installer switches** (put them in front of `./install.sh` or `./carl.sh install`):

| Variable | Does |
|---|---|
| `NO_CODER=1` / `CODER=1` | Without the coder subagent and its rule / with the coder in all conditions |
| `NO_BACKGROUND_SUBAGENTS=1` | The coder runs in the foreground |
| `NO_CACHE=1` (or `NO_PREFIX_CACHE=1`) | Without the prompt cache |
| `NO_MODEL_CHECK=1` | Without the OpenCode model check |
| `NO_SIDEBAR=1` | Without the OpenCode Subagents panel |
| `NO_SWITCHER=1` | Without the OpenCode session switcher |
| `NO_BROWSER=1`, `BROWSER_HEADED=1` | Without the browser / the browser with a window |
| `WEB_SEARCH=exa\|parallel\|off` | The web search provider, or none |
| `NO_LSP=1` (or `LSP=0`) | Without the OpenCode LSP tool |
| `NO_PROFILE=1` | Do not change `~/.zshrc` or `~/.bashrc`; show the line to add |
| `NO_SYNC_SERVICE=1` | Without the client sync service |
| `LLAMA_CTX=96k` | Sets the client context limit (N or Nk) |
| `CARL_API_KEY` | The API key (in place of a prompt) |

**VM (or another client computer):**

| Command | Does |
|---|---|
| `./install-clients.sh [opencode\|pi]` | Installs the clients |
| `./install.sh [--vm\|--local\|--host ADDR] [--port N] [--key-file FILE]` | Installs or updates the configs, then does a smoke test (auto: the address in `remote.json`, else VM on Linux, local on macOS) |
| OpenCode `/models` (`/mo`), `/variants` (ctrl+t steps) | Changes the model, the thinking level |
| OpenCode `/switch`, `@coder TASK` | Changes the session; gives a task to the coder |
| Pi `/model`, `/subagents` | Changes the model; lists and stops background subagents |
| `/carl` (OpenCode and Pi) | Every CARL piece and its state; the config sync |

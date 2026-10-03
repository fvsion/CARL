# CARL User Guide

CARL: Can't Afford Remote LLMs.

CARL runs a local Qwen coding model on an Apple Silicon Mac. You use the model from OpenCode or Pi. This guide tells you how to set up, run and use CARL. The clients can run in a VMware Fusion VM on the Mac, or directly on the Mac. For the technical details (architecture, repository layout, the llama.cpp server, how the thinking control works, model and quant choices, measurements, design reasons), see [REFERENCE.md](REFERENCE.md). For the short overview, see [README.md](README.md).

Commands with the label **Mac** run in the CARL folder on the Mac (`~/CARL`). Commands with the label **VM** run in the Kali VM. If you do not use a VM, all commands run on the Mac. See [Clients on the same Mac](#clients-on-the-same-mac-no-vm).

## Contents

1. [First-time setup](#1-first-time-setup)
2. [Daily use](#2-daily-use)
3. [Choosing a model](#3-choosing-a-model)
4. [Thinking on, off and effort](#4-thinking-on-off-and-effort)
5. [Context window](#5-context-window)
6. [KV cache: q4 or q8](#6-kv-cache-q4-or-q8)
7. [Downloading and adding models](#7-downloading-and-adding-models)
8. [Logs and monitoring](#8-logs-and-monitoring)
9. [Updating the client configs](#9-updating-the-client-configs)
10. [Troubleshooting](#10-troubleshooting)
11. [Benchmarking and testing](#11-benchmarking-and-testing)
12. [Rules of thumb](#12-rules-of-thumb)
13. [Command reference](#13-command-reference)

---

## 1. First-time setup

### Mac

**Requirements:**
- An **Apple Silicon** Mac. 36 GB runs all catalogue models. A 24 GB Mac runs the IQ3 35B (its default), the IQ3 27B and the Q3 27B entries (see [Sharing with a friend](#sharing-with-a-friend)).
- [Homebrew](https://brew.sh), for `llama.cpp` (tested with 0.4.1 and 0.5.0), `aria2` and `ansifilter`.
- `python3`. The first time that you use it, macOS offers to install it with the command-line developer tools. For the exact GPU limit, the `fit` command also uses `swift`. Both come with `xcode-select --install`.
- 11–23 GB of free disk space for each model.
- Optional: VMware Fusion with NAT (vmnet8) for VM clients.

1. **Install the tools:**
   ```bash
   brew install llama.cpp aria2 ansifilter
   ```
   - `llama.cpp` gives `llama-server`.
   - `aria2` makes downloads faster.
   - `ansifilter` makes the logs easy to read.
   - If you forget this step, `./carl.sh` finds the missing tools and asks to install them with Homebrew. `SKIP_DEPS=1` skips this check. Homebrew itself is not installed automatically.

2. **The API key:** the first server start makes the API key (`~/.config/carl/api-key`, mode 600, in a folder with mode 700) automatically, if it is missing. This is the same folder as `config.json`.
   - If you used CARL before 1.2.0, the first start of `./carl.sh llama` copies your old key (`~/.mtplx/api-key`) to the new path. It is the same key, so your clients continue to work.
   - The configs that `install.sh` writes refer to the key file. They do not contain the key.
3. **Select and download a model.** The `fit` command shows **auto fit's pick** for this Mac, why it picked it, and, for each model, the largest context window that fits in the GPU memory:
   ```bash
   ./carl.sh fit
   ./carl.sh download default          # auto fit's pick (qwen3.6-35b-a3b on 32-36 GB+, the IQ3 35B on 24 GB)
   ```
   `default` is auto fit's pick from the whole catalogue for the everyday goal (see [Auto fit](#auto-fit-the-best-model-for-this-mac) below).
   - If you forget this step, `./carl.sh` tells you that no model is downloaded. It shows auto fit's pick for this Mac and its size, and asks to download it. If you answer no, the dashboard opens without a server.
   - If auto fit's pick is not downloaded, but a different model is, the server starts with the best downloaded stock model that fits. The start-up output tells you this, and how to download the pick.
4. **The server serves this Mac only (127.0.0.1) by default.** For clients in a VMware Fusion VM, start Fusion first, then start the server with `--vm`: it listens on the Fusion NAT address `192.168.42.1`, which exists only while the Fusion network is up. To make the VM the default, set **network** to `vm` in Settings (or `./carl.sh config set llama.net vm`).
   - Before 1.3.0 the default was "auto": the VM address whenever the Fusion network was up. That exposed the server to the VM network without being asked, so it was removed. A saved `llama.net = auto` is converted to local once, with a note at start; `NET=auto` means local.
5. **Start the server:**
   ```bash
   ./carl.sh llama            # this Mac only (127.0.0.1): the default
   ./carl.sh llama --vm       # for a VMware Fusion VM client (fails if Fusion's network is down)
   ```
   - The server starts in the background. Then the **live monitor uses this terminal** ([section 8](#8-logs-and-monitoring)). SERVER shows "loading model…" for 10–60 s, then "idle".
   - The start-up banner shows the network mode and the values that the server selected, for example `slots: 2 (auto) x 98304 tokens, KV q4_0/q4_0, RAM prompt cache … MiB`.
   - The CONNECT section shows the URL and the API key. It also has buttons that copy a complete OpenCode or Pi config.
   - If you run `./carl.sh` with no arguments, it opens the dashboard ([section 2](#2-daily-use)). `./carl.sh -h` shows the help. To see the options of a command, run `./carl.sh help llama` (or a different command).

### Clients in the VM

The VM needs Python 3 and curl. The staging folder for the client bundle is `~/Documents/carl-vm-client` on the Mac (called `mtplx-vm-client` before 1.2.0; rename it or use the old one). The VM sees this folder through the Fusion shared folder at `/mnt/data/Documents/carl-vm-client`.

1. **Copy the bundle in:**
   ```bash
   cp -r /mnt/data/Documents/carl-vm-client/. ~/carl-vm-client/ && cd ~/carl-vm-client
   ```
2. **Install OpenCode and Pi** (first time only). The installer gets both clients from npm. It does not use sudo. It installs into `~/.local`, and it gets Node 22 (Linux or macOS build) if necessary:
   ```bash
   ./install-clients.sh          # or: ./install-clients.sh opencode | pi
   ```
   Make sure that `~/.local/bin` is on your `PATH`.
3. **Install the configs:**
   ```bash
   ./install.sh
   ```
   - On the first run, the installer asks for the API key. Paste the contents of `~/.config/carl/api-key` from the Mac. Alternatively, put the key in a file with the name `api-key` next to `install.sh`, or set `CARL_API_KEY`.
   - The installer keeps the key at `~/.config/carl/api-key`. It uses the key again on later runs.
   - At the end, the installer does a smoke test. If it shows `OK: llama.cpp reachable at http://192.168.42.1:8080/v1 -> "id":"qwen3.6-35b-a3b"` (the loaded model), you have a connection.
4. **Start a client:** run `opencode` or `pi`. Both clients use the llama.cpp model by default. The OpenCode config is `~/.config/opencode/opencode.json`. The Pi configs are `~/.pi/agent/models.json` and `~/.pi/agent/settings.json`.

### Clients on the same Mac (no VM)

Use this procedure for a Mac without a VM, for example the Mac of a friend. The server and OpenCode/Pi both run on the Mac. All the necessary files are in the CARL folder.

**The short way:** run `./carl.sh install`. It installs OpenCode and Pi, and then it connects them to the server on this Mac. It does the two steps below. In the dashboard, the Connect tab (tab 2) does the same: push `i` (**[ Install on this Mac ]**), or `u` (**[ Update configs only ]**) to write only the configs, then `y` to confirm. The installer's output shows in the tab.
- `./carl.sh install opencode` (or `pi`) installs only one client.
- `./carl.sh install --config-only` only writes the configs. `--clients-only` only installs the clients.
- `./carl.sh install --vm` or `--host ADDR` connects the clients to another address.

1. **Server:** do steps 1–3 above. Then run:
   ```bash
   ./carl.sh llama
   ```
2. **Clients** (in a second terminal, from the CARL folder):
   ```bash
   ./client/install-clients.sh       # first time: OpenCode + Pi into ~/.local (fetches Node 22 for macOS if needed)
   ./client/install.sh --local       # configs pointing at this Mac's server, using its own key file
   ```
   - On a Mac, `install.sh` uses `--local` by default. It sets the clients to the address that the server listens on now. This address is 127.0.0.1, or 192.168.42.1 if the server started for the VM. It reads the key directly from `~/.config/carl/api-key`.
   - `install.sh` needs `python3`. If macOS asks you to install the command-line developer tools, accept. Alternatively, run `xcode-select --install`.
3. **Start a client:** open a new terminal, so that `~/.local/bin` is on the `PATH`. Then run `opencode` or `pi`.

**Alternative without the installer:** in the monitor, click **[ OpenCode config ]** or **[ Pi config ]** (or press `o` / `p`). The monitor copies the config for the server that runs now to the clipboard, with the URL and the key filled in. Merge it into `~/.config/opencode/opencode.json` or `~/.pi/agent/models.json`.

CAUTION: The configs that the monitor copies contain the API key itself, so that you can paste them on a different machine. Protect these configs as you protect the key.

### Clients on another computer or another VM app

Use this procedure when the clients are not in the VMware Fusion VM and not on the server Mac. Examples: a Parallels VM, or a laptop on your network.

**1. Give the server an address that the client can reach.**
- Start the server on that address: `./carl.sh llama --host ADDR`. Or, in the dashboard, open Settings (tab 5), and select the address in the **network** row. The row shows each address of this Mac.
- To find the LAN address of the Mac, run `ipconfig getifaddr en0`.
- Parallels: use the address of the Mac on the Parallels network (often `10.211.55.2`; `ifconfig` shows it). To use it for `--vm`, set `VM_HOST=10.211.55.2`.
- The address must exist on this Mac. The launcher always refuses `0.0.0.0`.
- CAUTION: **With the LAN address, every computer on your network can connect to the server.** Only the API key protects it. Do not use the LAN address on a public or shared network.

**2. Get the API key.** In the dashboard, open the Connect tab (tab 2), and push `k` to show the key. Or run `cat ~/.config/carl/api-key` on the server Mac.

**3. On the client computer:** copy the `client/` folder to it. Then run:

```bash
./install-clients.sh
./install.sh --host ADDR --key-file key.txt
```

- `key.txt` is a file that contains only the key. Delete it after the installation; the installer keeps its own copy (`~/.config/carl/api-key`, mode 600).
- If you do not give a key, the installer asks for it. The key does not show when you type it.
- `--key KEY` also works. But then the key stays in your shell history. Thus, use the file or the prompt.

### Sharing with a friend

1. **Make the zip:** `tools/make-share-zip.sh [OUT]` writes `../CARL-YYYYMMDD.zip`, or the file `OUT`. The zip holds one folder, `CARL/`.
   - The zip does not include caches, `.DS_Store` files or key files.
   - Models, logs and the API key are outside the folder (`~/models`, `~/.config/carl`). Thus, the zip contains no personal data.
2. **On the Mac of your friend:** unzip the file. Then do the steps in [Mac](#mac) and [Clients on the same Mac](#clients-on-the-same-mac-no-vm). In summary:
   - `brew install llama.cpp aria2 ansifilter`
   - `./carl.sh fit`
   - `./carl.sh download default` (on 24 GB this is `qwen3.6-35b-a3b-iq3`)
   - `./carl.sh llama`
   - `./carl.sh install`

   The first server start on their Mac makes a key on that Mac.
3. **The memory decides the model.** On their Mac, `./carl.sh fit` gives the real numbers. To see the numbers from here, run `fit --ram 24`. These are the estimates for a **24 GB** Mac (GPU limit ≈ 16 GiB):

   | Model | Largest context window (q4 KV) |
   |---|---|
   | **`qwen3.6-35b-a3b-iq3`** (default on 24 GB: stock fast MoE, 14.1 GB) | 2 × 96K slots (the default) fit in ~15.3 GiB, thus subagents work. 1 slot can go to 256K. |
   | `qwen3.8-27b-iq3` (stock, the smallest 27B, 10.9 GB) | 2 × 96K slots (the default) fit, thus subagents work. 2 slots can go to ~128K each. 1 slot can go to 256K. |
   | `qwen3.8-27b-q3` (stock, 13.1 GB) | ~148K, 1 slot (the default 96K fits) |
   | `heretic-35b-a3b-iq3` (abliterated fast MoE, 13.6 GB) | 2 × 96K slots fit, thus subagents work. No MTP head: n-gram speculation. |
   | `orcarouter-27b-iq3` (abliterated, the smallest abliterated 27B, 12.6 GB) | 2 × 96K slots fit, thus subagents work. |
   | `orcarouter-27b-q3` (abliterated, 14.6 GB) | ~68K, 1 slot. Its default window is 64K (the one exception to the 96K floor: this build exists for 24 GB Macs). The start notes it and suggests `./carl.sh tune orcarouter-27b-q3`, which selects the largest window that fits. |
   | `qwen3.8-27b`, `orcarouter-27b` (Q4) | do not fit under the default limit |
   | `qwen3.6-35b-a3b` (Q4) | does not fit |

   - **To increase the GPU limit:** run `sudo sysctl iogpu.wired_limit_mb=18432`. Then the stock Q4 `qwen3.8-27b` can run with ~84K. This setting resets at reboot, and it keeps ~6 GB for macOS. The abliterated Q4 gets only ~16K, thus use its Q3.
   - **Monitor the SYSTEM card in the monitor.** If the memory pressure changes to WARNING/CRITICAL, use a smaller `--ctx`.
   - **The launcher refuses to start** a model and a context window that do not fit: it shows what they need against the limit, the largest window that fits, and auto fit's alternative. `FIT_CHECK=0` is the expert override.
   - With 1 slot, `install.sh` does not install the coder subagent ([section 5](#the-coder-subagent-opencode-and-pi)).
   - NOTE: These estimates did not get a test on a 24 GB machine.

---

## 2. Daily use

1. **Mac:** start Fusion (for VM clients). Then run `./carl.sh`.
   - If a server runs already on port 8080, the live monitor attaches to it ([section 8](#8-logs-and-monitoring)).
   - If no server runs, it starts llama.cpp with your saved settings. If no model is downloaded, it first offers to download the default model for this Mac.
   - To open only the dashboard, run `./carl.sh --no-start` (or `./carl.sh dashboard`). It attaches to a server that runs. If no server runs, it starts nothing: select a model in the Settings tab (tab 5), and push `a` to start it.
   - To start a specific server, give a command: for example `./carl.sh llama --model qwen3.8-27b` (see [section 3](#3-choosing-a-model)). Type `./carl.sh -h` for help.
   - CAUTION: **The server does not start if another model is in memory.** Two models do not fit. The launcher looks for any process larger than 8 GB, and it shows that process.
2. **VM or Mac:** run `opencode` or `pi`. Select the model that the server runs (`/models` in OpenCode, `/model` in Pi): the lists show the models installed on the server under their own names. This is necessary only if the server model is not the clients' default. If you pick another one, OpenCode shows a CARL warning (in [router mode](#router-mode-switch-models-from-opencode-or-pi), picking it loads it).
3. **Stop the server, or let it continue to run:** in the monitor, press `q` (or Ctrl-C, or click **[ Quit ]**). Then select one option:
   - `s`: stop the server and quit.
   - `d`: quit the monitor and let the server continue to run. The server continues to run after you close the terminal. To attach the monitor again, run `./carl.sh monitor`.
   - Esc: cancel.

   To stop the server without the monitor, run `./carl.sh monitor`, then push `q` and `s`. Or run this command (it finds the process that listens on port 8080):
   ```bash
   kill $(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')
   ```

**What to expect:**
- **The first message of a session is slow.** The system prompt and the tools of OpenCode are about 9K tokens. They take about 2 min on the 27B and 20 s on the 35B.
- **If you resume a long session after a server restart, the server reads all of the session again.** A 74K-token session takes about 13 min on the 27B.
- **Follow-up turns are fast.** The server uses its cache again, and it processes only the new tokens.
- **The Mac stays awake while the server runs** (`caffeinate`). But if you close the lid on battery power, the Mac goes to sleep. Then all requests in progress pause. Thus, connect the power supply for long work.

---

## 3. Choosing a model

Each model is served under its own name, and the clients list it under the same name (before 1.3.0, builds of one family shared a name, so the label could be wrong).

| You want | Run on the Mac | Select in the client |
|---|---|---|
| **The default:** Qwen3.6-35B-A3B (MoE, ~4× faster decode and ~6× faster prompt read than the 27B) | `./carl.sh llama` | `qwen3.6-35b-a3b` |
| The dense 27B (stock), if you specifically want it | `./carl.sh llama --model qwen3.8-27b` | `qwen3.8-27b` |
| Uncensored (abliterated) 27B, with the best measured quality in long sessions | `./carl.sh llama --model orcarouter-27b` | `orcarouter-27b` |
| Uncensored and fast: the abliterated A3B (Heretic, Q4 with the MTP head; 32 GB+) | `./carl.sh llama --model heretic-35b-a3b` | `heretic-35b-a3b` |
| A 24 GB Mac (the default on that Mac) | `./carl.sh llama`, which selects `qwen3.6-35b-a3b-iq3` | `qwen3.6-35b-a3b-iq3` |
| A 24 GB Mac, stock | `./carl.sh llama --model qwen3.8-27b-q3` | `qwen3.8-27b-q3` |
| A 24 GB Mac, stock, the smallest 27B (10.9 GB) | `./carl.sh llama --model qwen3.8-27b-iq3` | `qwen3.8-27b-iq3` |
| A 24 GB Mac (or any Mac), uncensored and fast: the abliterated A3B | `./carl.sh llama --model heretic-35b-a3b-iq3` | `heretic-35b-a3b-iq3` |
| A 24 GB Mac, uncensored 27B with subagents (2 × 96K) | `./carl.sh llama --model orcarouter-27b-iq3` | `orcarouter-27b-iq3` |
| A 24 GB Mac, uncensored 27B, better quality (1 slot) | `./carl.sh llama --model orcarouter-27b-q3` | `orcarouter-27b-q3` |

**Key points:**
- CAUTION: **Only one model can run at a time.** Stop the current server before you start a different server. Two models do not fit in 36 GB, and the collision breaks the model that runs.
- **By default the client does not change the server model.** llama-server answers with the model that is loaded, for all model names that the client sends. If the client selection and the server do not match, you get the loaded model with the wrong label and the wrong thinking options: OpenCode shows a CARL warning then. Router mode (below) is the other way round.
- **The clients list only the installed models** (downloaded, or in the models folder), one entry each. After a download or a delete, update them: `./carl.sh install --config-only`, or `u` in the dashboard's Connect tab, which warns when they are out of date.
- **Stock vs abliterated:** the stock models (`qwen3.8-27b`, the 35B) keep their refusals. The orcarouter builds do not have refusals. Only `orcarouter-27b` has full measurements in CARL. The stock 27B has the same architecture and speed profile.
- **Q3 vs Q4:** Q3 is 2.5–3.5 GB smaller, but its quality is lower (more slips in long agent sessions). Use Q3 if Q4 does not fit.
- **IQ3:** the IQ3 builds are smaller again, and their quality is lower again. The IQ formats unpack more slowly on Metal. MTP helps them less, and 2 drafts make them slower. Thus, their tuned speculation is MTP + n-gram with 1 draft (REFERENCE.md, "IQ3 speculation"). Use IQ3 only if nothing larger fits.
- **Other models:** each `.gguf` in `~/models/gguf` is a model too. See [section 7](#7-downloading-and-adding-models).

### Router mode: switch models from OpenCode or Pi

For users who prefer to choose models on the fly. In router mode, llama.cpp's router offers every downloaded model that fits this Mac, and loads the model that OpenCode (`/models`) or Pi (`/model`) asks for: one model at a time, the loaded one stops first. A switch takes 30 s to 2 min. The default is the dashboard's own mode (single model): auto fit and the Settings tab pick the model.

WARNING: every switch empties the prompt cache. The model that loads starts cold, so the next request re-reads the whole conversation (a long OpenCode session: minutes, see [section 5](#5-context-window)), and so does switching back. Switch with this in consideration.

- **Turn it on:** Settings (tab 5) → **Router** panel → **OpenCode / Pi switch models (router)**, then `y`. It saves `llama.mode = router` in `config.json`, restarts a running server, and then updates the OpenCode / Pi configs on this Mac (when CARL set them up here; a VM: run `./install.sh` there again). Or: `./carl.sh --router` (this start only), `./carl.sh config set llama.mode router`. Back: the panel's **Dashboard only**, or `./carl.sh --single`.
- **Each model gets the settings a single start of it would use** (your `config.json` profile, else its Auto-tune result, else the catalogue): window, slots, KV cache, speculation, sampling, RAM cache. A model whose setup doesn't fit the GPU limit is left out, and the start says why. `--model`, `--ctx`, `--kv` and `--slots` don't apply to a router start. The presets are written to `~/.config/carl/router-presets.ini` at each start (don't edit it).
- **The model loaded first** is the one a single start would load (`llama.model`, or auto fit's pick).
- **The Router panel** shows each model's state (loaded, loading, unloaded) with **Load** / **Unload** buttons, the recent switches, and whether the OpenCode / Pi lists match the installed models (**Update the OpenCode / Pi configs**).
- **A model that isn't installed** gets an error (HTTP 400 "not found"; nothing loads), and OpenCode shows a CARL warning. **Custom models:** set their card's *thinking* (Models panel, `e`) so OpenCode offers the right levels.
- The dashboard follows the loaded model (memory, context, requests); the header shows `llama.cpp router (N models)`.

---

## 4. Thinking on, off and effort

Qwen models "think" (hidden reasoning) before they answer. More thinking makes the model slower, but the answers to difficult problems are better.

### OpenCode: `/variants` (or ctrl+t)

In OpenCode 1.18 the thinking levels are model **variants**: type `/variants` and pick one, or press **ctrl+t** to step to the next. `/models` switches the model.

| Model | Options | Default |
|---|---|---|
| Qwen3.8-27B (llama.cpp) | `none` = off, `low`, `medium`, `xhigh` | `low` |
| Qwen3.6-35B-A3B | `none` = off, `high` = on | `high` |
| A model you added | from its card's *thinking* ([Cards for custom models](#cards-for-custom-models)): on / off (`none`, `high`), or effort levels (as the 27B); on / off without one | |

### Pi: thinking level

| Model | Options |
|---|---|
| Qwen3.8-27B | off, low, medium, xhigh |
| Qwen3.6-35B-A3B | off, high |

The Pi defaults are provider `llamacpp`, model `qwen3.6-35b-a3b` and thinking `low`. `install.sh` sets them only if they are unset, or if they still have the values that it set before.

**Notes:**
- **A change applies from the next message.** A reply that is in progress keeps its mode.
- **After you run `install.sh` again, fully restart OpenCode.** An open OpenCode instance keeps its old options. If OpenCode does not know an option, it silently uses the default.
- **Use `xhigh` only when necessary.** With `xhigh`, Qwen3.8 thinks too much on simple tasks. `low` is the correct default for agent work.
- **The sampling settings follow the mode automatically.**
  - With thinking on, the settings are the Qwen thinking settings: temperature 1.0, top_p 0.95, top_k 20.
  - The OpenCode `none` option changes to the Qwen non-thinking settings: temperature 0.7, top_p 0.8, presence penalty 1.5.
  - The Pi `off` option keeps the thinking settings.
  - To change the server defaults, run `TEMP=0.6 ./carl.sh llama`. If the 35B repeats itself, use `PRESENCE=1.5`. For details, see REFERENCE.md, "Sampling".
- **The 35B has no effort levels**, only on and off. Its template ignores low/medium/xhigh. Thus, only `none` and `high` are available.
- **How "off" works:** the server loads the chat template of the model with one added rule. With this rule, `reasoning_effort: none` means `enable_thinking: false`. To disable the rule, run `THINK_TOGGLE=0 ./carl.sh llama`. For details, see REFERENCE.md, "How thinking works".

---

## 5. Context window

The context window is the quantity of conversation that the model can hold. The default is 96K tokens for each slot. 96K is a floor: CARL does not select less if 96K fits (agent work needs that much context). The one exception is `orcarouter-27b-q3`, a build only for 24 GB Macs: it starts at 64K and suggests a tune. The Settings tab warns only about larger windows. Longer context windows work (recall stayed 8/8 up to ~150K), but they need more time. On the 35B, a cold prompt reads in these times:

- 64K: ~5 min
- 128K: ~19 min
- 150K: ~27 min

The decode speed decreases from 20 to 14 to 12 tok/s. Use `--ctx 128k` or `--ctx 160k` when you need it. For details, see REFERENCE.md, "Context length".

```bash
./carl.sh --ctx 192k      # bigger: N or Nk, between 4k and 256k
./carl.sh --ctx 96k       # smaller: less memory
```

**After you change `--ctx`, update the clients** (VM):
```bash
cd ~/carl-vm-client && ./install.sh     # reads the running server's window
```
Then restart OpenCode or Pi.

**Why this is important:**
- **The client limit only decides when the client compacts** (summarises) the conversation. The client does not send this limit to the server.
- **If the client thinks that it has 128K but the server has 96K**, all requests fail after the session is larger than 96K. The error is `HTTP 400 ... exceeds the available context size`. The client never compacts, because it did not reach its own limit.
- **A server context window that is larger than the client limit causes no problem.** It only uses memory that the client does not use.
- **`install.sh` sets the client limit** from the server that runs now, or from `LLAMA_CTX=128k ./install.sh`. If the server is down, it uses 96K.

**Memory:**
- The server allocates all of the KV cache at start: about 2.25 GiB at 128K with q4_0, 4.25 GiB with q8_0.
- 192K with q4 fits on this Mac. If you use a larger context window, keep the memory of the VM small.

---

### Subagents (OpenCode)

- **OpenCode runs a subagent as a separate conversation** (a child session).
- **By default, the server keeps two slots** (if they fit). The main session and a subagent each keep their own cache.
  - When the subagent is complete, the main session continues in about a second. It does not read its full context again. With one slot, the main session read everything again: this took minutes on the 27B at 60K tokens.
  - The banner shows `slots: 2 (auto) x 98304 tokens, KV q4_0/q4_0, RAM prompt cache … MiB`.
  - `--slots 1` forces one slot (less memory). `./carl.sh fit --slots 2` shows what fits.
- **Two at the same time:** a subagent can run while the main session keeps its position. OpenCode can also run two subagents in parallel.
  - On the 35B, two requests together get ~39% more total throughput.
  - On the 27B, the two requests share the GPU (each runs at about half speed).
- **The title agent of OpenCode stays on.** Earlier versions disabled it. With 2 slots, it runs at the same time as the main session. It does not wait in a queue behind the main session.
- **The monitor** shows one context bar for each slot. When both slots work, the header shows **BUSY ×2**.
- **In OpenCode, the sidebar shows a Subagents panel** (`install.sh` installs it):
  - the running subagents at the top, oldest first: a spinner, the agent and task, the elapsed time, the current tool, and the context size;
  - the finished subagents below them, newest first, one line each: ✓ (complete) or ✗ (error), the agent, the task and the duration. The panel shows the 5 newest, for 5 minutes after they finish. A `+N more` line holds the others: click it to see all of them;
  - when you go to a different parent session, the panel shows the subagents of that session;
  - click a subagent to see its model. Click it again to open its conversation. Click the panel header to collapse the panel.
  - To get a new version of the panel, run `install.sh` again. Then restart OpenCode.
  - The plugin is `client/opencode/plugins/subagents-sidebar/`. `install.sh` registers it in `~/.config/opencode/tui.json`. `NO_SIDEBAR=1 ./install.sh` installs without it.
- **More than two conversations at the same time** (for example, two subagents and the main session): the server parks the extra conversation in the RAM prompt cache. The size of that cache comes from the free memory. Thus, the conversation possibly does not fit in the cache. If it does not fit, the server reads it again.

### Switching sessions (OpenCode)

OpenCode 1.18.34 does not show the open sessions as tabs. Thus, `install.sh` adds a session switcher to the right side of the prompt box:

```
‹ 2/3 ● fix the log parser ›
```

- `2/3` is the position of this session in the list. The newest session is first.
- The icon shows the state of the session: `●` busy, `!` waits for a permission or a question, `○` idle.
- Click `‹` or `›` to go to the previous or next session.
- Click the title to open a list of the sessions. Then select one, and push Enter. Type `/switch` to open the same list.
- `+1●` after the title shows that another session is busy.

NOTE: The list contains the top-level sessions of this project that changed in the last 72 hours (maximum 9). It does not contain subagent sessions. For older sessions, use `/sessions`. The switcher does not show when there is only one session. The plugin is `client/opencode/plugins/session-switcher/`. `install.sh` registers it in `~/.config/opencode/tui.json`. `NO_SWITCHER=1 ./install.sh` installs without it.

### The coder subagent (OpenCode and Pi)

`install.sh` adds a specialist **coder** subagent. It also adds a rule that tells the main agent when to use the coder. It does this **only when the server has 2+ slots**. With one slot (for example, a 24 GB Mac with a 27B), each delegation removes the main session from its slot and forces a full read again. Thus, `install.sh` does not install the coder, or removes it.

`install.sh` checks the server that runs now. Run `install.sh` again after you change models or slots. `CODER=1` forces the coder on. The main agent delegates **on its own**, and only in these conditions:
1. **It is stuck:** a fix for the same code failed two times (its own attempts, or you tell it that earlier attempts failed).
2. **The task is large:** 3+ files or ~150+ lines. Examples: a new module, package or CLI, an implementation with tests, or a multi-step feature or refactor.

The main agent keeps questions, explanations, code searches and small edits.

- **How it works:**
  - The coder starts with a new context and works one step at a time.
  - Before it fixes a failure, it reproduces the failure.
  - It runs the tests or the build.
  - It does not leave placeholders.
  - After three failed approaches, it stops and reports.
  - Its report gives the result, the changed files, the verification, the root cause and the open issues. The main agent checks that report before it answers you.
- **Tested (35B, 2 slots, 2026-10-01):**
  - **OpenCode** delegated all large tasks (3/3) and all stuck tasks (2/2). It did the questions and small edits itself.
  - **Pi** delegated stuck tasks and most large tasks (2/3). It did the questions and small edits itself.
  - Delegation depends on the judgment of the model. Thus, delegation occurs "usually", not "always". If you ask for it explicitly, it always works.
- **How it codes:** its prompt puts five engineering standards in order of priority:
  - **secure**: validate inputs at boundaries, no string-built shell/SQL/paths, no secrets;
  - **typed**: full annotations, domain types, run the type checker;
  - **hexagonal**: domain logic behind ports, I/O in adapters, dependencies point inward, test the domain with fakes;
  - **clean**;
  - **object-oriented where it fits**.

  The coder applies the standards as follows:
  - New modules follow the standards fully. For changes to current code, the coder applies the standards within the change. If a structure blocks a clean change, the coder reports the structure and does not refactor it silently.
  - A definition-of-done checklist runs before each report (tests green, typed, ports/adapters, secure, real input samples, no placeholders). The report has a "Standards" section.
- **Same model:** the coder uses the loaded model. Thus, the gain is a new, focused context and more reasoning (OpenCode: effort medium), not a stronger model. With 2 slots, the main session keeps its cache while the coder works.
- **To ask for it directly:**
  - **OpenCode:** type `@coder` and your task in the prompt, e.g. `@coder add tests for parse_config` (subagents are in the `@` list; Tab completes the name). The task goes straight to the coder: the main agent does not have to decide to delegate. If you already had an agent of your own called `coder`, CARL's coder is `@carl-coder`. Writing "use the coder agent to …" works too.
  - **Pi:** there is no command for it: the coder is the `subagent` tool. Ask for it in the message: "use the coder subagent to …".
- **To turn it off:** run `NO_CODER=1 ./install.sh`.
- **Where it is:**
  - The only source files are `client/agents/coder.md` (frontmatter description = when to use; body = its instructions) and `client/agents/delegation.md` (the rule for the main agent). To change the coder, edit those files and run `install.sh` again.
  - OpenCode: `agent.coder` in `opencode.json` (mode subagent, reasoning medium, no nested subagents, ≤80 steps), and `instructions`. Its prompt is in `~/.config/opencode/carl/coder.md`.
  - Pi: `~/.pi/agent/agents/coder.md`, the `subagent` extension, and a marked block in `~/.pi/agent/APPEND_SYSTEM.md`.
  - `install.sh` never replaces your own `coder` agent, Pi `agents/coder.md` or `extensions/subagent`. Our agent then gets the name `carl-coder`, or the installer skips it and shows a note (if you also have your own `carl-coder`).

## 6. KV cache: q4 or q8

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

CAUTION: Do not mix KV types (`KV_K=q8_0 KV_V=q4_0`). If you mix them, the server reads prompts about 5× slower.

---

## 7. Downloading and adding models

```bash
./carl.sh models                    # the catalogue + the models folder + your downloads, with status and free space
./carl.sh fit                       # what fits this Mac (fit --ram 24 / --ctx 64k)
./carl.sh download qwen3.8-27b      # one catalogue model (aria2c, 16 connections, resumable)
./carl.sh download default          # the default model for this Mac
./carl.sh download all              # every catalogue model
./carl.sh download hf:OWNER/REPO/FILE.gguf   # any GGUF from Hugging Face (verified too)
./carl.sh download hf:OWNER/REPO    # list the GGUF files of that repo
./carl.sh verify [NAME...]          # re-check the size and SHA-256 (no names: every downloaded model)
./carl.sh delete NAME               # delete a model file (and its partial download)
./carl.sh card NAME                 # a model's card; custom models: card NAME set FIELD VALUE (see below)
```

- The files go to `~/models/gguf/`. To use a different folder, set `MODELS_DIR`, or `paths.models_dir` in the settings file ([section 8](#the-settings-file)).
- If a download stops, run the command again. The download continues from where it stopped.
- If a file fails its checksum, its name changes to `*.bad`.
- The dashboard can do the same: Settings tab, **Models** panel ([section 8](#8-logs-and-monitoring)).

**The catalogue** is [host/catalog.json](host/catalog.json). It holds the built-in models. For each model, it has:
- `hf`: the download source: the Hugging Face repo, a pinned revision, the file, its SHA-256 and its size;
- `summary` and `description`: what the model is (technical);
- the **model card**: `role` (a short headline), `good_for` (tags: `agent coding`, `hard code`, `chat & writing`, `uncensored`), `why_use`, `trade_offs`, `pick_instead` (another catalogue model, and when it is the better pick), `hardware`, `uncensored` (what it means; abliterated models only) and `rank` (the quality order, 1 = best: parameters and density first, then quantization; speed is the reverse). The catalogue is checked when it loads: unknown tags, `uncensored` on a stock model, or a `pick_instead` model that is not in the catalogue are errors;
- `tune`: the recommended server settings (KV cache, context, slots, speculation, draft tokens, sampling);
- `why`: the reason for each tuned value;
- `ctx_zones`: the context windows that read fast, slow and very slow;
- `measured`: the reference measurements.

The Settings tab shows the description and the reasons next to the settings. The fields `default` and `default_small` at the top of the file select the default models.

**Custom models (not in the catalogue):**
- **Each `.gguf` in the models folder is a model.** `./carl.sh models` lists it with the source `file`. Its name is the file name in lower case, without `.gguf`. Vision projectors (`mmproj…`) and the second and later parts of a split GGUF are not listed.
- **`./carl.sh download hf:OWNER/REPO/FILE.gguf` downloads any GGUF from Hugging Face.** It gets the revision, the size and the SHA-256 from the Hugging Face API. Then it downloads the file and verifies it. A `huggingface.co` URL to the file also works. `hf:OWNER/REPO` (no file) lists the GGUF files of the repo.
- CARL records these models in `~/.config/carl/models.json`. This file also holds the Auto-tune results for each model on this Mac.
- **A custom model gets its first settings from its GGUF header:** q4_0 KV, 96K for each slot (less only if the model was trained for less), and MTP + n-gram (1 draft) if the file has an MTP head. Without an MTP head: n-gram only (2 drafts). These settings are a guess. Run [Auto-tune](#auto-tune) to measure better values.
- To start it: `./carl.sh llama --model NAME`. A path to a `.gguf` also works.
- **Give it a card** (what it is good for): see [Cards for custom models](#cards-for-custom-models) below.
- Before you use it from the clients, examine the chat template of the model. The thinking options are different between model families. The server answers with the loaded model for all model names, but the client sends the thinking options of the entry that you select.

**To add a model to the catalogue** (for everyone who uses this folder):
1. Copy an entry in `models` in `host/catalog.json`. Change `name`, `label`, `summary`, `description` and `alias` (the served name that the clients use), and write its model card (`role`, `good_for`, `why_use`, `trade_offs`, `pick_instead`, `hardware`, `rank`; `uncensored` for an abliterated model).
2. Fill in `hf`: `repo`, `revision` (the commit SHA), `file`, `sha256` and `bytes`. The easy way: run `./carl.sh download hf:OWNER/REPO/FILE.gguf` first, and copy the `hf` block that it writes to `~/.config/carl/models.json`.
3. Set `tune` and `why`. For `spec`, use `draft-mtp,ngram-mod` if the GGUF has an MTP head. Use `spec_n` 1, except for a MoE K-quant, where 2 can be better (the Q4 35B). Do not use 2 on an IQ quant. For a file without an MTP head, use `ngram-mod`. Run `./carl.sh tune NAME` to measure.
4. Set `arch`, `mtp`, `min_ram_gb` and `ctx_zones`.
5. Before you add the model to the clients, examine the chat template of the model. The thinking options are different between model families.
6. Add the model to `client/opencode/opencode.json` and `client/pi/models.json`.
7. Copy the bundle to the shared folder.
8. Run `install.sh` again in the VM.

### Cards for custom models

CARL can't tell what a model is for from its file name, so a custom model starts without a card: the lists show only "Custom model …", it has no "good for" tags (the use-case filters hide it), no rank (sort by quality puts it last) and no dense / MoE mark. You write its card, with the same fields and the same rules as the catalogue's cards:

| Field | Value |
|---|---|
| `label` | the name the lists and the MODEL card show (default: the file name) |
| `role` | a headline, at most 60 characters |
| `good_for` | tags: `agent coding`, `hard code`, `chat & writing`, `uncensored` (`uncensored` only with `abliterated`) |
| `why_use`, `trade_offs`, `hardware` | text: why to choose it, when to pick something else, which Macs it is for |
| `abliterated` | yes / no: refusals removed (the **stock** filter hides it; auto fit never picks it) |
| `uncensored` | text: what uncensored means for this model (abliterated models only) |
| `arch` | `dense` or `moe` (the dense / MoE filters and auto fit's goals use it) |
| `quant` | the quantization label, for example `Q4_K_M` |
| `rank` | quality order, 1 = best (the catalogue's models are ranked 1–5); sort by quality uses it |
| `thinking` | `on-off` (thinking on or off only) or `effort` (effort levels) |
| `auto_fit` | yes / no (default no): auto fit may pick this model. It needs `rank` and `arch`, and a stock model. Your rank is not measured, so a custom model never takes part in auto fit unless you switch this on |
| `pick_instead` | other models and when they are the better pick |

- **In the dashboard:** Settings (5) → `]` Models panel → select the model → `e` (or **[ Edit card (e) ]**). The form has one row per field: ↑ ↓ select a field, Enter edits it (type the text; Enter keeps it, Esc drops it; paste works), ← → or space change a choice or tick a tag, `x` clears the field, and **+ add a model** under *pick instead* opens a list of models (then type when it is the better pick). `s` (or **[ Save ]**) checks the card and saves it; an error shows in the form, and nothing is saved until the card is valid. Esc cancels. When the card has no `arch` or `quant` yet, the form fills them in from the GGUF header.
- **On the command line:**

  ```bash
  ./carl.sh card NAME                                   # show a model's card (catalogue or yours)
  ./carl.sh card NAME set role "Fast local coder"
  ./carl.sh card NAME set good_for "agent coding,hard code"
  ./carl.sh card NAME set rank 4
  ./carl.sh card NAME set pick_instead qwen3.8-27b="harder code" qwen3.6-35b-a3b="long chats"
  ./carl.sh card NAME unset rank
  ```

  `set` checks the whole card; a bad value prints `error: …` and exits with 1. Yes / no fields take `yes`, `no`, `true`, `false`, `on`, `off`. `pick_instead` also takes a JSON list (`'[{"model": "qwen3.8-27b", "when": "harder code"}]'`).
- The card is saved in `~/.config/carl/models.json`, under the model (`card`). Deleting the model removes its card. A `pick_instead` entry whose model is gone later is left out.
- **Catalogue models are read-only:** `e` and `card NAME set` say so. Their cards come from `host/catalog.json`.
- After you save, the MODEL card, the role and tags in every model list, sort by quality and the filters use the card at once.

### Auto-tune

Auto-tune measures the best settings for one model on this Mac. It takes about 5–10 min.

```bash
./carl.sh tune qwen3.8-27b-iq3            # all modes
./carl.sh tune qwen3.8-27b-iq3 --quick    # no MTP modes with 2 drafts, no 64K read (about 4 min)
```

It does these steps. The model loads one time for each speculation mode.
1. **Memory:** the largest context window that fits, with 1 slot and with 2 slots.
2. **Speculation:** none, n-gram, and, if the file has an MTP head, MTP and MTP + n-gram with 1 and 2 drafts (`--quick`: the MTP modes with 1 draft only). N-gram alone always uses 2 drafts. Each mode writes prose, new code and a code re-emit, two times. The score is a weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2). A mode with drafting must be 3% better than a simpler mode to win.
3. **Prompt reading:** a cold read at 8K, 32K and 64K tokens (`--quick`: no 64K). From these, it calculates the time to read a full window. This gives the context zones of this Mac: a cold read of the full window in 3 min or less = fast (green), in 10 min or less = slow (yellow), more = very slow (red). The fast zone always includes 96K: CARL never shows 96K or less as slow.
4. **Result:** q4_0 KV, the best speculation, the context window and the slots (2 if two windows fit).
   - **The context is never less than 96K if 96K fits one slot.** Users need that much context to work.
   - Above 96K, Auto-tune keeps the catalogue window if a cold read of it is not worse than slow on this Mac and it fits. Otherwise, it selects the largest standard window in the fast zone (minimum 96K).
   - If 96K does not fit (for example `orcarouter-27b-q3` on a 24 GB Mac), it selects the largest standard window that fits.
   - NOTE: The re-emit workload now copies `tools/carl_core/adapters/llama_server.py`. Thus, the re-emit scores are not directly comparable with older Auto-tune results.

- CARL saves the result in `~/.config/carl/models.json`. Each later start of the model uses it. A value that you set in the settings file has priority ([section 8](#the-settings-file)).
- CAUTION: **Auto-tune needs the GPU for itself.** It does not start if a server runs on port 8080, or if another large process (more than 8 GB, `BIG_GB`) or a known model server is in memory. `ALLOW_SECOND_MODEL=1` skips that check. Stop the server first. The **Auto-tune** panel of the dashboard stops the server for you, and starts it again after the tune.
- Auto-tune uses its own server on port 8093 (`--port` changes it). `--dry-run` shows the result, but does not save it.

---

## 8. Logs and monitoring

### Live dashboard

The dashboard (the monitor) is `tools/llama-monitor.py`.

**It runs in the terminal from which you start the server.**
- `./carl.sh` with no arguments opens the dashboard. It attaches to a server on port 8080. If no server runs, it starts llama.cpp with your saved settings (it offers to download the default model for this Mac if no model is downloaded).
- `./carl.sh llama` starts the server in the background, in its own process group under `nohup`. Thus, Ctrl-C cannot stop the server by accident. Then the monitor uses the terminal.
- The server sends its console output to its log file, and also to `~/models/logs/.console-<port>.out`.
- **To quit**, press `q` or Ctrl-C, or click the **[ Quit ]** button (top right). A dialog asks you to select:
  - **Stop server** (`s`);
  - **Leave it running** (`d`): the server continues to serve after you close the terminal. To attach again, run `./carl.sh monitor`;
  - **Cancel** (Esc).
- If the server stops on its own (a crash, a failed start), the header badge shows **EXITED**, and the log shows the cause. Then `q` quits immediately.
- `MONITOR=0 ./carl.sh llama` runs the server in the foreground with plain log output. Scripts and `nohup` starts do this automatically.

**Attach by hand** (after "leave it running", or from a different terminal):
```bash
./carl.sh monitor                       # finds the server's address itself
./carl.sh monitor --port 8081           # a server on another port
./carl.sh monitor --once --expand       # print one snapshot with every card detailed
```

**Layout:**
- **Header:** the model, a status badge (IDLE, READING, GENERATING, LOADING, EXITED, OFFLINE), the uptime, the clock and **[ Quit ]**.
  - The CARL logo shows at the left of the header in iTerm2, Ghostty, WezTerm and kitty. Other terminals show 😎. To turn off the logo, set `CARL_LOGO=0`.
- **Tabs** (click them, or use the keys `1`–`5` / Tab):

| Tab | Shows |
|---|---|
| **1 Overview** | Cards in two columns: CONNECT, CONTEXT, MEMORY on the left; ACTIVITY, MODEL, HEALTH, SYSTEM on the right. Below the cards: the last 3 requests and the last 6 log lines. |
| **2 Connect** | Endpoint, model, API key, who can reach the server, connected clients, key file; setup steps for Mac and VM clients; **[ Install on this Mac ]** (`i`: runs `./carl.sh install` and shows its output) and **[ Update configs only ]** (`u`), both asked first; buttons **[ OpenCode config ]**, **[ Pi config ]**, **[ curl test ]**. A button copies its snippet to the clipboard and shows it below. The screen masks the key unless you reveal it. The copy has the real key. CAUTION: Protect a copied config as you protect the key. |
| **3 Requests** | All finished requests in the log, newest first: start time, context size, new tokens, read speed, output tokens, generation speed, duration, draft acceptance, prompt tokens from the cache. The title shows the averages. |
| **4 Log** | The full server log, which you can scroll. Buttons and keys: wrap (`w`), errors and warnings only (`f`), follow (End). |
| **5 Settings** | Five panels; `[` and `]` change the panel. **Server:** the model and the server setup, with an explanation of the model and of each tuned value. **Models:** the catalogue and the models folder: download, verify, delete, add from Hugging Face. **Auto fit:** the best stock model for this Mac, why, and the ranking. **Auto-tune:** measure a model on this Mac. **Router:** who switches the model (the dashboard, or OpenCode / Pi in router mode). See "The Settings tab" below. |

**The Overview cards:** click the title of a card to see more detail. Click again to see full detail. Click once more to collapse the card. The dots after the title show the level: `○○` collapsed, `●○` normal, `●●` full detail.

Each card keeps the same height while the server works. If a value is not available, the card shows `0`, `N/A` or `none`.

| Card | Normal | Detailed |
|---|---|---|
| CONNECT | endpoint, model, API key (masked), reachable from, clients, copy buttons | key file, install commands (this Mac: `./carl.sh install`) |
| CONTEXT | fill bar, **KV quantization** (K, V), **KV cache RAM** (allocated / in use), recurrent state + checkpoints, total now / max | per-token maths, MTP-head estimate, cache caps, served vs trained window |
| MEMORY | weights, context (KV + state), other buffers | GPU limit, GPU memory now |
| ACTIVITY | what it does now; when it reads a prompt: progress and **ETA**; when it generates: speed; averages, last request, draft acceptance | acceptance by draft position, totals, queue, peak context |
| MODEL | file, weight quant and size, speculation | architecture and layer mix, experts, thinking control, batch, flash attention, PID |
| HEALTH | log error/warning counts, **BROKEN** alarm on GPU out-of-memory or compute errors, kept awake, sleeps since start | last errors, recent sleep/wake events |
| SYSTEM | memory pressure (title), RAM, swap, GPU, power, thermal | wired / compressed / free, CPU and load, free disk |

**Keys:**

| Key | Does |
|---|---|
| `1`–`5`, Tab | change the tab |
| `o` / `p` / `t` | copy the OpenCode / Pi config / curl test (opens the Connect tab) |
| `i` / `u`, then `y` | Connect tab: install OpenCode and Pi and their configs / update the configs only (`n` or Esc: no); `x` closes the installer's output |
| `k` | show / hide the API key |
| `e` / `c` | expand / collapse all cards |
| `w` / `f` | log: wrap / errors only |
| ↑ ↓ PgUp PgDn, wheel | scroll the current tab (End: go back to the newest log line) |
| `+` / `-` | more / fewer log lines on the Overview |
| space | refresh now |
| `?` | show all keys in the footer |
| `q`, Ctrl-C | quit (asks stop / leave running / cancel) |
| `[` / `]` | Settings tab: the previous / next panel (Server, Models, Auto fit, Auto-tune, Router) |
| ↑ ↓, ← →, Enter, `a`, `A`, `r`, `x` | Settings tab, Server panel: select a row, change the value, open the model list (on the model row) or type a value, apply, open the Auto fit panel, revert, tuned values |
| ↑ ↓, Enter, `d`, `v`, `u`, `x`, `h`, `c` | Settings tab, Models panel: select a model, use it, download, verify, Auto-tune, delete, add from Hugging Face, cancel the download |
| `g`, `f`, Enter, `d`, ↑ ↓ | Settings tab, Auto fit panel: the other goal, the other model set, use the pick, download it, scroll |
| ← →, Enter, `c` | Settings tab, Auto-tune panel: select a model, run, cancel |

Mouse: left-click only (titles, tabs, buttons). The wheel scrolls.

**Notes:**
- **The monitor only reads from the server.** On llama.cpp, it reads only `/health`, `/slots`, `/metrics`, `/props`, `/v1/models` and the log file. Thus, it is safe to use during a session. It changes the server only when you select that: stop the server, or apply new settings.
- **The monitor calculates the context memory. It does not measure it.** The server does not log it. Thus, the monitor calculates it from the GGUF metadata of the model and the server flags (REFERENCE.md, "Context memory").

### Auto fit: the best model for this Mac

Auto fit picks the best **stock** model that fits this Mac, for a goal:

| Goal (`llama.auto_goal`) | Family first | Why |
|---|---|---|
| `everyday` (default) | the MoE builds (35B-A3B) | fast, and usually sufficient: CARL prioritises speed |
| `hard-code` | the dense builds (27B) | better at code and hard tasks, but slower |

- **Quality** is the catalogue `rank` (1 = best): parameters and density first, then the quantization.
- **The rule:** among the goal's family, the best rank that holds **two 96K windows** (the main session and a coder subagent); if none does, one 96K window; if none does, the largest window of at least 32K. If no build of the family fits, the best of the other family (it says so).
- **Memory:** the smaller of the GPU limit and the RAM minus a reserve for macOS and apps (6 GiB; 10 GiB while VMware's network is up; `RESERVE_GB` / `--reserve-gb`).
- **Stock only:** auto fit and every automatic default never pick an abliterated model. You pick those by hand. Models that you added (Hugging Face, the models folder) are candidates only when their [card](#cards-for-custom-models) switches `auto_fit` on (with a rank and an arch, and not abliterated): your rank is not measured. `./carl.sh download default` and the download offer only name catalogue models.
- **Candidates (`llama.auto_fit`):** `catalogue` (default) = every catalogue model: it offers the download of the pick, and until then a start with `model auto` uses the best downloaded model that fits; `downloaded` = only the models on this Mac.
- **Where it is used:** `llama.model = auto`, `./carl.sh download default`, the download offer of `./carl.sh` on a new Mac, the `auto` entry of the model list and the **Auto fit** panel in the Settings tab.
- `./carl.sh fit` shows the pick for each goal and why each better-ranked model was passed over; `./carl.sh fit --ram 24` (or 16, 36, 64, ...) shows another Mac. The picks: 16 GB nothing fits; 24 GB `qwen3.6-35b-a3b-iq3` / `qwen3.8-27b-iq3` (everyday / hard code); 32 GB and up: `qwen3.6-35b-a3b` / `qwen3.8-27b` (on 32 GB only while VMware's network is down: with it up, CARL keeps 10 GiB for macOS and the VM and the everyday pick becomes the IQ3). All with 2 × 96K. Previews (`--ram`) estimate the GPU limit at 2/3 of RAM below 32 GB and 3/4 from 32 GB up; a real Mac reports its own limit.

**A start over the GPU limit is refused.** `serve-llama.sh` (and so `./carl.sh llama` and the dashboard) checks the setup before the model loads. If it needs more than the GPU limit, it stops with what it needs against the limit, the largest window that fits, and auto fit's alternative. Expert override: `FIT_CHECK=0 ./carl.sh llama ...` (it may fail to load, or swap the Mac to a crawl).

### The Settings tab

The Settings tab (tab 5) has five panels: **Server**, **Models**, **Auto fit**, **Auto-tune** and **Router** ([router mode](#router-mode-switch-models-from-opencode-or-pi)). Push `[` or `]`, or click the name of a panel, to change the panel.

**Server panel: change the server settings:**
1. Press `5`, or click **5 Settings**.
2. Use ↑ ↓ to select a row. The rows are the llama.cpp settings: model, KV cache, context/slot, slots, speculation, draft tokens, RAM cache, network, temperature, presence.
   - On the **model** row, push Enter (or click the model name) to open a list of all models: the catalogue, the models folder and your Hugging Face downloads. `auto` is auto fit's pick for this Mac (`★`). When you select a model, the rows change to the settings of that model (your profile, else its Auto-tune result, else its catalogue values).
   - **Auto fit:** push `A` (or click the **auto fit** line under Status) for the Auto fit panel (below). Its **Use this** sets the model, the context, the slots and the KV cache for this Mac in one step.
   - The **network** row offers local (the default: this Mac only), vm (a VMware Fusion VM client too) and each address of this Mac (for example the LAN address). An address is saved as `llama.host`.

   Use ← → (or click `[<]` `[>]`) to change the value. A `*` shows a value that is different from the server that runs now.
3. Look at the colour of the values:
   - **green:** the tuned value for this model, or a fast setting;
   - **yellow:** changed from the tuned value, or slower;
   - A context of 96K or less is never yellow or red: 96K for each slot is the floor of the default window. Only larger windows get a warning (you can still select them).
   - **red:** very slow, or does not work on this model. For example, a context in the very slow zone, MTP speculation on a file without an MTP head, or MTP with more than 1 draft on an IQ quant.
4. **Pick a model from the list beside the settings** (on a wide terminal; below them on a narrow one): click a model, or push `m`, then ↑ ↓ and Enter (`m` or Esc goes back to the settings). `●` = downloaded, `○` = not downloaded, `★ auto → NAME` = the model `auto` starts, `★` after a name = auto fit's pick, a red name = too big for this Mac (less than a 32K window). Above the list, `sort: … ▾ 1/5` and `show: … ▾ 1/10` open a drop-down of every option when you click them; `s` / `S` and `f` / `F` step through them. The list only selects: everything about the model is on the card below.
5. Read the **MODEL** card below the settings. It tells you what the model is for and why to pick it. Click its title to change the detail: collapsed (name, role and tags in the title), normal, full. `e` / `c` expand or collapse all cards, and the mouse wheel or PgUp / PgDn scroll the panel.
   - With `auto` (or auto fit's pick) selected, the card starts with **Auto fit**: why it picked the model (goal, scope, the plan, the memory it needs of what this Mac allows), what a start uses while the pick is not downloaded, and the better-ranked models it passed over, with the reason for each.
   - **Normal:** the role, the **good for** tags (`agent coding`, `hard code`, `chat & writing`, `uncensored`), **why use it**, the **trade-offs**, the hardware it is meant for, the speed (measured on this Mac after Auto-tune, else the catalogue figure and the Mac it came from), the recommended values next to yours, the context zones, and **why** the selected value is tuned that way.
   - **Full:** also what *uncensored* means (abliterated models), the models to **pick instead** and when, the quality **rank**, the description, the reason for every tuned value, the Auto-tune table, and the source and file.
   - The model list (Enter on the model row) shows the role and the tags of each model, and *why use it* and the trade-offs of the selected one.
6. Below the rows, the card has four parts: **About this setting** (how to change the selected row and what it does), **Status** (the **fit** line, auto fit's pick in one line, the settings file), the buttons, and **Keys**. The fit line shows whether the model is downloaded and whether it fits in the GPU memory with these settings. If it does not fit, you cannot apply the settings (the launcher would refuse the start too). Every text wraps to the width of the terminal.
7. Press `a` (or click **[ Apply and restart ]**; with no server: **[ Start server ]**). Then press `y` to confirm.
8. Wait while the model loads (about 30 s to 2 min). The footer shows the progress.

- CAUTION: **Apply stops the server.** Requests in progress stop. Make sure that no client waits for an answer.
- **If the new server does not start,** the monitor starts the old server again with its old values. A message shows the last lines of the error.
- **The monitor saves the settings in `~/.config/carl/config.json`** ([The settings file](#the-settings-file)). `./carl.sh llama` uses this file the next time. Flags and environment variables have priority over the file.
  - Server-wide values go to the `llama` section. The monitor writes only values that are different from the defaults.
  - Model values (KV cache, context, slots, speculation, sampling) go to the profile of the model (`models.<name>`). The monitor writes only the values that are different from the tuned values of that model.
- **`x` (Tuned values)** sets the defaults, and the tuned values of the selected model. Then apply.
- **After a change of the model, the context or the slots,** run `install.sh` again on each client. The clients then get the new context limit, and the coder subagent is added or removed.
- **`r`** discards your changes and shows the values of the server that runs now.
- **If the catalogue or `models.json` cannot be loaded** (for example a bad edit), the Settings tab shows the error in the panel. The other tabs continue to work.

**Advanced settings:**
1. Select the row **advanced**, and push → to show the advanced rows.
2. Select a row. Use ← → to select a preset value.
3. To type a value, push Enter. Type the number (for example `0.05`, or `96k` for a context). Push Enter to keep it, or Esc to cancel.

| Advanced rows (default) | Launcher variable |
|---|---|
| top_k (20), top_p (0.95), min_p (0), repeat penalty (1.0) | `TOP_K`, `TOP_P`, `MIN_P`, `REPEAT` |
| -ub batch (512), checkpoints (8), checkpoint step (4096) | `UB`, `CKPT`, `CKPT_STEP` (and `BATCH` for `-b`, default 2048) |

- CAUTION: **The default values are tuned and measured** (REFERENCE.md). A change can make the model slower, or its answers worse. To go back, push `x` (Tuned values) and apply.
- The monitor saves the advanced values in the same settings file. You can also set them as environment variables, for example `TOP_K=40 ./carl.sh llama`.

**Models panel:**
- It lists the catalogue models and each `.gguf` in the models folder, with the size, the status (downloaded, partial, missing), whether it fits this Mac, and the role and "good for" tags.
- **Sort and filter:** the two rows of chips above the list show every option, the current one highlighted. Click one, or step with `s` / `S` (sort: next / previous) and `f` / `F` (filter: next / previous). Sort: downloaded first, quality (the catalogue rank), speed (MoE first, then smaller files), size, name. Show: all, a use case (agent coding, hard code, chat & writing, uncensored), stock, dense, MoE, downloaded, fits this Mac. The same order applies to every model list (the Server panel's list and the model drop-down). Below the list: the description, the source and the file of the selected model, and its Auto-tune result.
- **Enter:** use this model (the Server panel opens with it; push `a` to start it).
- **`d`:** download. A progress bar shows the speed and the ETA, then the checksum check. `c` cancels; the partial file stays, and Download continues it.
- **`v`:** verify the SHA-256 (about 1 min). **`x`:** delete the file (not the model that is loaded). **`u`:** open the Auto-tune panel for this model.
- **`h`:** add from Hugging Face. Type `OWNER/REPO` (or a URL to a `.gguf`). A list of the GGUF files of the repo opens. Select one and push Enter to download it.
- **`e`:** edit the card of a custom model (role, good-for tags, rank, ...; see [Cards for custom models](#cards-for-custom-models)). On a catalogue model, `e` says that its card is read-only.

**Auto fit panel:** the [auto fit](#auto-fit-the-best-model-for-this-mac) answer for this Mac, in full.
- **Goal** (`everyday` / `hard code`) and **From** (all catalogue models / downloaded models only): click one, or push `g` / `f` for the other one. They are saved at once (`llama.auto_goal` / `llama.auto_fit`), and `model auto` starts the new pick.
- **This Mac:** the GPU limit, the RAM less the reserve for macOS and apps (10 GiB while VMware's network is up, else 6), and what that leaves for a model.
- **The pick:** the model, its plan (slots × window, KV cache), the memory it needs, whether it is downloaded, why it was picked, what `model auto` starts until it is downloaded, and every better-ranked model it passed over with the reason.
- **[ Use this ]** (Enter): the Server panel gets the pick with its context, slots and KV cache; push `a` there to start it. If the pick is not downloaded, the dashboard asks whether to download it. **[ Download it ]** (`d`) downloads it here, with the progress in the panel.
- **The other goal's** pick, in one line, and the **ranking**: every model by rank with its arch, weights, the largest window that fits this Mac (1 slot, q4_0), whether it is here, and what auto fit made of it (the pick, passed over and why, abliterated, a custom model not opted in). ↑ ↓, PgUp / PgDn or the wheel scroll the panel.

**Auto-tune panel:**
- Select a model with ← → (or click its name for a list). Only downloaded models are in the list. Click the **quick** box for the quick mode.
- Push Enter to run Auto-tune ([section 7](#auto-tune)). The panel shows each step and the last lines of its output. `c` cancels.
- CAUTION: Auto-tune needs the GPU for itself. If a server runs, the panel asks first. Then it stops the server, runs the tune, and starts the server again with the saved settings (and the new tune).
- **Last result:** the date, the Mac, the selected settings, the speed of each speculation mode (prose, code, re-emit), the prompt read speeds and the context zones.
- **[ Use these values ]** removes your own values for this model from config.json. Then the tuned values apply.

### The settings file

The dashboard and the launchers keep your settings in `~/.config/carl/config.json`.

| Section | What it holds |
|---|---|
| `llama` | Server-wide llama.cpp settings: `model` (`auto` = auto fit's pick for this Mac), `auto_goal` (`everyday` \| `hard-code`), `auto_fit` (`catalogue` \| `downloaded`), `net` (`local` \| `vm`; default local), `host`, `cache_ram`, `ub`, `batch`, `ckpt`, `ckpt_step`, `think_toggle`, `extra_args` (more `llama-server` flags, as a list) |
| `models.<name>` | The profile of one model: `kv`, `ctx`, `slots`, `spec`, `spec_n`, `temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `alias` |
| `paths` | `models_dir` (default `~/models/gguf`) |

- **Order of priority** for a llama.cpp start: command-line flags, then environment variables, then `config.json`, then the Auto-tune result of this Mac, then the catalogue (`host/catalog.json`), then the built-in defaults.
- **CARL validates the file.** Each value must have the correct type, range or choice. A bad value stops the start with an error that names the key. CARL ignores an unknown key.
- **Earlier versions used `llama.env`** in the same folder. If `config.json` does not exist, CARL copies its values into it one time. After that, CARL does not read the old file.
- **A file from before 1.2.0 can hold settings of features that 1.2.0 removed** (CHANGELOG.md). It still loads: CARL ignores them (`./carl.sh config show` warns about a removed section), and they go away the next time CARL saves the file.
- `SETTINGS_FILE=none ./carl.sh llama` ignores `config.json` (the Auto-tune result and the catalogue still apply).
- To go back to the defaults, delete the file, or remove a value with `config unset`.

```bash
./carl.sh config show                                 # the file, then every key with its type and default
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
ls ~/models/logs/                         # one log per server start; llama-server-latest.log = newest successful start
```

**How to read the progress of a request:**
- `prompt processing, n_tokens = 8192, progress = 0.11, ... / 94.45 tokens per second`: the progress of the prompt read, about every 2K tokens.
- `prompt eval time = ... tokens per second`: the prompt read is complete.
- `eval time = ... tokens per second`: the generation speed.
- `draft acceptance = 0.875`: how frequently the speculation guessed correctly.
- `n_tokens = 8961, truncated = 0`: the request is complete, and its size.

**Quick server checks (Mac):**
```bash
K=$(cat ~/.config/carl/api-key)
curl -s -H "Authorization: Bearer $K" http://192.168.42.1:8080/v1/models          # which model is loaded
curl -s -H "Authorization: Bearer $K" http://192.168.42.1:8080/props | python3 -c 'import json,sys; print(json.load(sys.stdin)["default_generation_settings"]["n_ctx"])'   # context window
PID=$(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')   # the server process
ps -o rss=,command= -p $PID                                                        # memory + exact flags
```

NOTE: CARL does not use `lsof`. On a Mac with a stale network share (for example a Time Machine SMB volume that is disconnected), `lsof` can hang, and Ctrl-C cannot stop it. `netstat -anv` shows the process ID in its `process:pid` column.

---

## 9. Updating the client configs

Run the installer again in these conditions:
- you downloaded or deleted a model (the lists show the installed models only; the dashboard's Connect tab warns when they are out of date: its tab shows ⚠, and `u` there updates this Mac's configs),
- the bundle in `client/` changed (new options), or
- you restarted the server with a different `--ctx`.

On the server Mac, `./carl.sh install --config-only` is enough. **For a VM,** run it on the Mac first: it writes the list of installed models to `client/installed-models.json`, and the copy of the client folder carries the list into the VM. Without that file (a bundle copied before 1.3.0), `install.sh` lists only what the server reports (in the dashboard's mode: the model it runs).

```bash
# Mac (only if client/ changed): stage the bundle in the shared folder
rsync -a --delete client/ ~/Documents/carl-vm-client/

# VM
cp -r /mnt/data/Documents/carl-vm-client/. ~/carl-vm-client/ && cd ~/carl-vm-client && ./install.sh
```

Then **fully restart OpenCode or Pi**.

**What `install.sh` does:**
- Before it changes a config file that exists, it makes backups:
  - `FILE.before-carl`: your original file, from before CARL changed it the first time. The installer never overwrites this copy.
  - `FILE.bak.<timestamp>`: the version from before each later change.
  - The summary shows the backups as `backed up`. If nothing changes, the installer writes nothing and makes no backup.
- To go back to your own config, copy `FILE.before-carl` back to `FILE`. For example: `cp ~/.config/opencode/opencode.json.before-carl ~/.config/opencode/opencode.json`.
- It stores the API key at `~/.config/carl/api-key` (mode 600). The configs refer to this key file. They do not contain the key.
  - Before 1.2.0, the client copy was `~/.config/mtplx/api-key`. If the installer finds no other key, it uses that one. It changes old configs to the new path. It does not delete the old file (a provider of your own can use it): delete it yourself when nothing uses it.
  - The order of the key sources: `--key` / `--key-file`, `$CARL_API_KEY`, `./api-key` next to `install.sh`, the key file of the server (on a Mac with `--local`), the key from an earlier run, a prompt.
- It writes one entry per installed model, under the model's own name, with its family's thinking options (custom models: their card's *thinking*) and its context window (the running model: the server's window; the others: their settings). The default model (`model` / `small_model`, Pi's `defaultModel`) becomes the model a server start loads, if the default is still CARL's.
- It installs the OpenCode plugin **carl-model-check** (in `opencode.json`'s `plugin` list, with CARL's provider id): a warning when the model you pick isn't the one the server runs, isn't installed, or is being loaded (router mode). `NO_MODEL_CHECK=1 ./install.sh` leaves it out (and removes it).
- It replaces the `llamacpp` provider as a complete block. Thus, removed models do not stay in the config. (A deep merge never deletes keys, so old variants stayed in the configs.) It keeps your other providers and settings.
- It removes the client parts of the server support that 1.2.0 removed (a provider, an OpenCode plugin and a Pi extension; CHANGELOG.md), if an earlier CARL installed them. It removes only CARL's own items, with the usual backups.
- It sets the llama.cpp context limit from the server.
- It copies the plugins and the extensions. Then it does a smoke test of the server.

**It does not overwrite settings that you own.** `client/configure.py` does the merge:
- **It records our items** in `carl.json` next to each config (`~/.config/opencode/`, `~/.pi/agent/`). It identifies older installs by our provider names and the key path.
- **Providers:** if you already have your own provider with the id `llamacpp`, it stays. The installer then adds ours next to it as `carl`.
- **Default model:** it sets the OpenCode `model` / `small_model` and the Pi defaults only if they are unset, or if they still have the value that it set before. If your default model is your own, it stays, and `small_model` follows it.
- **Agents, prompts, extensions:** it never replaces your own `coder` agent, Pi `agents/coder.md` or `extensions/subagent`. Our coder becomes `carl-coder`, or the installer skips it and shows a note.
- **Lists** (`plugin`, `instructions`): it adds our items to the end of the list, or removes them. It keeps your items.
- **Report:** at the end, it shows a summary: added / updated / kept / removed.
- **If you run it again with the same input, it changes nothing.**
- **Formerly LLM-Deploy:** before 1.2.0, CARL's files had the name `llm-deploy`: the folder `~/.config/llm-deploy`, the records `llm-deploy.json`, the prompt folder `~/.config/opencode/llm-deploy/`, the provider `llm-deploy` and the coder `llm-deploy-coder`. The first `./carl.sh` command (or this installer) moves the folder to `~/.config/carl` and leaves a link with the old name, so the old configs keep working. The installer then changes CARL's own items to the new names (`carl.json`, `~/.config/opencode/carl/`, `carl`, `carl-coder`), with the usual backups. Your own items with these names stay.

On the Mac (no VM), run `./carl.sh install --config-only` again from the CARL folder (or push `u` in the dashboard's Connect tab).

The options of the installer:
```bash
./install.sh                 # auto: --vm on Linux (the VM), --local on macOS
./install.sh --vm [HOST]     # server at the VM host address (default 192.168.42.1)
./install.sh --local         # server on this Mac (the address it listens on, else 127.0.0.1)
./install.sh --host ADDR     # any address
./install.sh --port N        # the llama.cpp port (default 8080)
./install.sh HOST [X] [PORT] # old positional form: X (an old second port) is ignored with a note; PORT = the llama.cpp port
```

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: MODEL needs N GiB of GPU memory at --ctx ..., but this Mac allows M GiB` on start (the start is refused) | The model and the context window are larger than the memory that macOS lets the GPU use: it would fail to load or swap | Use the suggested `--ctx`, auto fit's alternative (shown), a smaller build, or q4 KV. See `./carl.sh fit`. Expert override: `FIT_CHECK=0` (it may not load, or swap). |
| No monitor shows; plain server output shows instead | You did not start the server from a terminal (script, `nohup`), or `MONITOR=0` is set | This is the expected result. To attach, run `./carl.sh monitor`. |
| The monitor shows **EXITED** immediately after start | The server did not start (bad flag, file not found, out of memory) | Read the log lines on the screen. The full output is in `~/models/logs/.console-8080.out`. |
| You closed the terminal, and you do not know if the server still runs | The server continues to run, because it runs under `nohup` | To attach again, run `./carl.sh monitor`. Then press `q` → `s` to stop it. |
| Clients on the Mac cannot connect to 127.0.0.1 | The server started for the VM (192.168.42.1) | Run `./carl.sh install --config-only` again (it uses the address that the server listens on). Or, start the server without `--vm`. |
| The VM cannot reach 192.168.42.1:8080 | Since 1.3.0 the server serves this Mac only unless asked | Start it with `./carl.sh --vm`, or set **network** to `vm` in Settings (`llama.net = vm`). The Connect tab says which one runs. |
| `error: --vm: no interface has 192.168.42.1` | The Fusion network is not up | Start VMware Fusion, or use `--local`. |
| The monitor HEALTH card shows **BROKEN** | GPU out-of-memory or compute errors in the log | Restart the server. Make sure that no other large program runs. |
| `error: port 8080 is already in use by: llama-server ...` | A server already runs (one model at a time) | Stop it first: `./carl.sh monitor`, then `q` and `s`. Or use the `kill` command of [section 2](#2-daily-use). Or, only monitor it with `./carl.sh monitor`. |
| `error: another large process (probably a model) is in memory` | A model server runs already: llama.cpp, or a server that was started in a different way. Two models do not fit in the GPU memory. | Stop the other server first. The message shows its process ID and its size. If the large process is not a model, start with `ALLOW_SECOND_MODEL=1`. |
| Prompt progress stops for many minutes, then continues | The Mac went to sleep (lid closed on battery power, or `KEEP_AWAKE=0`) | Connect the power supply and keep the lid open. To check, run `pmset -g log \| grep -E "Sleep\|Wake"`. |
| All requests fail with `Compute error`, but `/health` says ok | A GPU out-of-memory event. Usually, a second model started at the same time | Restart the server. CAUTION: Do not run two models at the same time. |
| `HTTP 400 ... exceeds the available context size` | The session became larger than the server `--ctx`, and the client limit is higher | Restart the server with a larger `--ctx`, or run `install.sh` again so that the client compacts in time. Compact the session manually now. |
| Thinking `none`/off still thinks | You did not restart OpenCode after `install.sh`, or the message generation started before the change | Fully restart OpenCode and send a new message. Make sure that the server has `--chat-template-file` (see the `ps` command above). |
| `/variants` shows options that must not be there | Old config in the VM | Run the current `install.sh` again (it replaces the providers). Then restart OpenCode. |
| The first message takes minutes | Cold prompt: the system prompt and the tools (~9K), or a resumed long session | This is the expected result. The 35B reads prompts ~6× faster. Later turns use the cache again. |
| Slow replies late in a long session | The decode speed decreases as the context gets larger (27B: ~7 tok/s at 60–80K) | Compact the session or start a new session. Or, use the 35B. |
| The client shows the wrong model name | The client selection does not control the server | Select the entry that matches `./carl.sh --model ...`. |
| `install.sh` waits at the API key prompt and does not continue | The installer found no key | Paste the key, put it in `./api-key`, or set `CARL_API_KEY`. |
| Smoke test: `--: llama.cpp not running` | The server is not up, or the VM cannot reach the Mac | Start the server. From the VM, run `curl -s http://192.168.42.1:8080/health`. |
| The download stops or fails its checksum | Network interruption, or a corrupted file (`*.bad`) | First, delete the `.bad` file. Then run `download` again (it continues from where it stopped). |
| The monitor or `./carl.sh` freezes, and Ctrl-C does not stop it (versions before 2026-10-03) | Old versions used `lsof` to find the server. `lsof` checks each mounted volume. On a stale network share (for example a disconnected Time Machine SMB volume), it hangs, and you cannot stop it. | Update CARL: it now uses `netstat`. To release the hang now, eject the stale volume in Finder (or `diskutil unmount force /Volumes/NAME`), then close the terminal. |
| `CARL needs: llama.cpp aria2 ansifilter (not installed)` | Homebrew tools are missing | Answer `Y`, and CARL runs `brew install`. Or install them yourself. `SKIP_DEPS=1` skips the check. If Homebrew is missing, install it first (https://brew.sh). |
| `No model is downloaded yet.` | A new installation | Answer `Y` to download the default for this Mac. Or answer `n`: the dashboard opens without a server, and you can download a model in its **Models** panel. |
| `auto fit picks X for this Mac, but it is not downloaded (...); starting Y, the best downloaded model that fits` | `llama.model` is `auto` and auto fit picks from the whole catalogue (`llama.auto_fit catalogue`) | This is the expected result. To use X, run `./carl.sh download X` (or press `A` in the Settings tab). To pick only from downloaded models, run `./carl.sh config set llama.auto_fit downloaded`. To always use Y, run `./carl.sh config set llama.model Y`. |
| `auto fit: no downloaded stock model fits this Mac` | `llama.model` is `auto`, and only abliterated (or no fitting) models are downloaded: auto fit never picks an abliterated model | Download auto fit's pick (`./carl.sh download default`), or choose the model by name: `./carl.sh config set llama.model NAME`. |
| `error: models.NAME.ctx: ... is out of range` (or a different key) | A bad value in `~/.config/carl/config.json` | Correct the value, or remove it with `./carl.sh config unset KEY`. `./carl.sh config show` lists the valid keys. |
| `error: a server is running on port 8080: stop it first` from `./carl.sh tune` | Auto-tune needs the GPU for itself | Stop the server first, or run Auto-tune from the dashboard (Settings, **Auto-tune** panel): it stops and starts the server for you. |
| `error: another large process (probably a model) is in memory` from `./carl.sh tune` | A different model server, or another process larger than 8 GB (`BIG_GB`), runs | Stop it first. If the process is not a model, run with `ALLOW_SECOND_MODEL=1`. |

**To see exactly what a client sends** (thinking settings, tool counts): see REFERENCE.md, "Verifying behaviour".

---

## 11. Benchmarking and testing

CAUTION: Some of these commands restart the server. Run them only when no session is active and **no other server runs**. The scripts that restart the server (`llama-spec-sweep.sh`, `llama-ab.sh`) use port 8080 on the default address.

The easy way to find the best speculation and context for a model on this Mac is Auto-tune: `./carl.sh tune NAME` ([section 7](#auto-tune)). Stop the server first: Auto-tune does not start while a server runs on port 8080, or while another model is in memory.

```bash
# Speculation configs (model via MODEL=path; draft count via spec:n)
MODEL=$(./host/models.sh path qwen3.6-35b-a3b) LOG_FILE=none \
  tools/llama-spec-sweep.sh none:1 draft-mtp:1 ngram-mod:1 draft-mtp,ngram-mod:1 draft-mtp,ngram-mod:2

# KV-type and batch-size A/B (waits for 20 min idle first)
tools/llama-wait-idle.sh 1200 && tools/llama-ab.sh all
```

| Tool | Purpose |
|---|---|
| `./carl.sh tune NAME [--quick]` (`tools/carl-tune.py`) | Auto-tune: memory, speculation modes, prompt reading at 8K/32K/64K. It saves the result for this Mac in `~/.config/carl/models.json`. |
| `tools/llama-spec-sweep.sh CFG...` + `tools/llama-spec-bench.py` | A speculative-decoding sweep. The sweep restarts the server for each config. The bench measures prose, code and edit decode speed. NOTE: the edit workload re-emits the source of `llama-spec-bench.py`, which was rewritten in 1.1.0. Thus, new edit numbers are not directly comparable with older measurements. |
| `tools/llama-ab.sh [kv\|ub\|all]` | An A/B test of the KV type and `-ub`. It uses `tools/llama-kv-longctx.py` and `tools/llama-ab-measure.py`. It restarts the server. |
| `tools/llama-kv-longctx.py` | A ~64K haystack with 8 needles: cold prefill, decode, append, recall. |
| `tools/llama-wait-idle.sh [SECS] [BASE]` | Waits until the server stays idle for SECS (default 1200). |
| `tools/llama-sesstest.py` | A multi-turn test of a long session (56K start, ~7K appends). It records the RSS. |
| `tools/req-capture-proxy.py LISTEN UPSTREAM LOG` | A pass-through with a log: it records what a client actually sends. It refuses a wildcard listen address (`0.0.0.0`, `::`), and it writes the log with mode 600. |

NOTE: Since 1.2.0, `llama-ab-measure.py`, `llama-kv-longctx.py` and `llama-sesstest.py` build their long prompts from the source files of this folder (Python, shell and JavaScript in `tools/`, `host/` and `client/`). Thus, their numbers are not directly comparable with older runs.

The file headers give more details. `tools/carl_bench.py` holds the helpers that these small tools share (the API key, chat requests, the PID of the server from `netstat`, the server memory).

**Unit tests** (no server and no model necessary), from the CARL folder:
```bash
python3 -m unittest discover -s tests            # tools/carl_core: domain, app, adapters
python3 -m unittest discover -s tests/scripts    # shell helpers, client/configure.py, the small tools
python3 -m unittest discover -s tests/monitor -t tests/monitor   # the dashboard (tools/monitor)
```

---

## 12. Rules of thumb

- **Run only one model server at a time.**
- **Keep the Mac connected to power and awake** during long work.
- **Run `install.sh` again and restart the client** after you change models, options or `--ctx`.
- **Select the client model that matches the server.**
- **Use thinking `low` as the default** on the 27B. Use `none` for quick, mechanical edits. Use `xhigh` only for difficult problems.
- **Use the 35B-A3B for speed.** Use the abliterated 27B when you need an uncensored model or want the best measured quality.
- CAUTION: **Do not bind to `0.0.0.0`.** The macOS firewall is off, so a wildcard bind makes the model available to the LAN. The launchers refuse this address, also through `HOST=0.0.0.0`.

---

## 13. Command reference

**Mac:**

| Command | Does |
|---|---|
| `./carl.sh` | open the dashboard: attach to a server on 8080, else start llama.cpp with the saved settings. It first checks for `llama-server`, `aria2c` and `ansifilter` (offers `brew install`), and offers to download a model if none is downloaded |
| `./carl.sh llama` | llama.cpp, default model (`qwen3.6-35b-a3b`; the IQ3 build on 24 GB), q4 KV, 2 slots |
| `./carl.sh monitor` | attach the live dashboard (a server start shows it automatically in the same terminal) |
| `./carl.sh llama --local` / `--vm` | serve only this Mac (the default) / the VM address too |
| `./carl.sh --router` / `--single` | router mode (OpenCode / Pi switch models) / one model (the default); `llama.mode` saves the choice |
| `./carl.sh llama --host ADDR` | serve on one address of this Mac (LAN, Parallels, …); never 0.0.0.0 |
| `./carl.sh --no-start` (or `dashboard`) | the dashboard only: attach to a server, or open it offline (no model loads) |
| `./install.sh --host ADDR --key-file FILE` | connect the clients to that address, with the key from a file (`--key KEY` also works) |
| `./carl.sh llama --slots 1` / `--slots 2` | one conversation / main session and a subagent (default: auto = 2 when they fit) |
| `./carl.sh help [COMMAND]` | help for one command: llama, monitor, fit, models, card, download, verify, env, tuning |
| `./carl.sh --model NAME\|PATH` | a different model (catalogue, models folder or Hugging Face download) or a `.gguf` path |
| `./carl.sh --kv q8` / `--q8` / `--q4` | KV cache type |
| `./carl.sh --ctx 192k` | context window |
| `./carl.sh models` | the catalogue, the models folder and your downloads, with the download status |
| `./carl.sh fit [--ram GB] [--ctx N] [--goal everyday\|hard-code] [--scope catalogue\|downloaded]` | auto fit's pick for each goal and the reasons; which models fit this Mac, and the largest context window for each model |
| `tools/make-share-zip.sh [OUT]` | make a clean zip of the folder to share |
| `./carl.sh download NAME\|default\|all` | download catalogue models |
| `./carl.sh download hf:OWNER/REPO/FILE.gguf` | download any GGUF from Hugging Face (verified); `hf:OWNER/REPO` lists its GGUF files |
| `./carl.sh verify [NAME...]` | verify downloaded models (size and SHA-256); no names: every downloaded model |
| `./carl.sh delete NAME` | delete a downloaded model file |
| `./carl.sh tune NAME [--quick]` | Auto-tune a model for this Mac: speculation, context window, slots (~5–10 min; stop the server first) |
| `./carl.sh config [show\|path\|get KEY\|set KEY VALUE\|unset KEY]` | the settings file `~/.config/carl/config.json`; KEY like `llama.net` or `models.NAME.ctx` |
| `./carl.sh card NAME [set FIELD VALUE\|unset FIELD]` | a model's card; custom models: set or remove one field of your card ([Cards for custom models](#cards-for-custom-models)) |
| `./host/models.sh list\|download\|verify\|delete NAME\|path NAME\|get NAME FIELD\|default\|downloaded` | the models tool (a wrapper around `tools/carl.py`; `./carl.sh models\|download\|verify\|delete` use it): list, download, verify, delete, the local path, one field, the default model for this Mac, the downloaded models |
| `./carl.sh -h` | print the help: overview of all commands; `<command> -h` for one command |
| `./carl.sh --help-adv` | all `llama-server` flags |
| `tools/llama-log.sh [-f] [FILE]` | server log as plain text (no ANSI codes); `-f` follows the log |

**Environment variables (llama preset):**

| Variable | Does |
|---|---|
| `CTX`, `KV`, `KV_K`, `KV_V`, `UB` | context, cache types, batch size |
| `SPEC`, `SPEC_N` | speculation type and draft count (default: the tune of each model) |
| `MODEL`, `ALIAS` | model path and served name |
| `HOST`, `PORT`, `API_KEY_FILE` | bind address, port, key |
| `LOG_FILE` | the path of the log file; `none` turns off the log file |
| `THINK_TOGGLE=0` | use the unpatched chat template |
| `KEEP_AWAKE=0` | do not keep the Mac awake |
| `FIT_CHECK=0` | expert override: start even when the setup needs more than the GPU limit (the memory check refuses it otherwise) |
| `SLOTS`, `CACHE_RAM`, `RESERVE_GB` | slots (auto) / RAM prompt cache in MiB (auto-sized) / RAM that stays free for macOS and apps when the server sizes the cache and auto fit picks a model |
| `NO_SIDEBAR=1 ./install.sh` | install without the OpenCode subagents sidebar |
| `NO_SWITCHER=1 ./install.sh` | install without the OpenCode session switcher |
| `NO_CODER=1 ./install.sh` | install without the coder subagent and its delegation rule |
| `MONITOR=0` | no monitor: the server runs in the foreground |
| `ALLOW_SECOND_MODEL=1` | start even when a process larger than 8 GB (`BIG_GB`) is in memory. CAUTION: a second model can stop the Mac. |
| `NET=local\|vm`, `VM_HOST` | network mode (default local; `auto` before 1.3.0 now means local) / the VM address (default 192.168.42.1) |
| `SETTINGS_FILE=none` | ignore the saved settings in `~/.config/carl/config.json` |
| `MODELS_DIR` | the models folder (default `~/models/gguf`; also `paths.models_dir` in config.json) |
| `SKIP_DEPS=1` | do not check for the Homebrew tools (`llama-server`, `aria2c`, `ansifilter`) |

The script sends all other arguments after the flags directly to `llama-server`.

**VM:**

| Command | Does |
|---|---|
| `./install-clients.sh [opencode\|pi]` | install the clients |
| `./install.sh [--vm\|--local\|--host ADDR] [--port N]` | install or update the configs, then do a smoke test (auto: VM on Linux, local on macOS) |
| `LLAMA_CTX=96k ./install.sh` | force the client context limit |
| OpenCode `/models` (`/mo`), `/variants` (ctrl+t cycles) | change the model, the thinking level |
| Pi `/model` | change the model |

# CARL Reference: Windows and Linux

[Index](README.md) · the setup of the server on a Windows 11 or Linux PC with a GPU, and what is different from a Mac.

CARL runs the llama.cpp server on a Mac (Metal), on a Windows 11 PC and on a Linux PC. This page is the setup for a PC. The User Guide describes the Mac.

**What is tested.** NVIDIA is tested: a GeForce RTX 2070 SUPER (8 GB) on Windows 11, and the same card in WSL (Ubuntu 24.04) for Linux. CARL names CUDA (NVIDIA), Metal (a Mac) and the CPU.

## What you need

- Windows 11 or a current Linux (x86-64), and Python 3.9 or newer (from python.org or `uv` on Windows).
- The driver of your GPU. For an NVIDIA GeForce RTX 20 series or newer with a current driver, CARL uses CUDA 13. For an older driver it uses CUDA 12.
- Disk space: 4.6–23 GB for each model.
- No compiler, no Homebrew, no admin rights. CARL downloads a pinned llama.cpp build by itself.

## Set up

1. **The command `carl`.** In the CARL folder: `uv tool install --editable .` makes the command for every folder (`uv tool update-shell` puts it on the path of a new window). Without `uv`, run `python3 carl.py` (Linux) or `py carl.py` (Windows) in the CARL folder in place of `carl`.
2. **See your GPU.** `carl gpu` shows each GPU and its free memory, what a model can use on it, the llama.cpp build for this computer and the files of its release.
3. **Install llama.cpp.** `carl engine install` downloads the llama.cpp build that CARL is tested with, checks it against the SHA-256 of the release and unpacks it into the settings folder (`llama.cpp/bNNNNN-BACKEND`). It sets `llama.path`. The previous build stays. `carl engine install --list` shows the builds that CARL installed. `--backend BUILD` takes `auto` (the default), `cuda`, `cuda-13`, `cuda-12` or `cpu`. The first start of a new build can take 30 seconds (CUDA warms up).
4. **See what fits.** `carl fit` shows the model that Auto fit chooses for the free memory of your GPU, and the largest context of each model. `carl fit --vram 8,8` previews a computer with two 8 GB GPUs.
5. **Download a model and start.** `carl download default`, then `carl`. The server starts and the dashboard opens in the terminal. Ctrl-C or `q` in the dashboard ends it, as on a Mac.
6. **The clients.** On Windows, `client\setup.cmd` in the client package installs OpenCode and Pi (Node's zip and npm are fetched; the Task Scheduler runs the sync service and the user's Path is changed). On Linux, `./setup` does it with `systemd --user`. `carl package` makes the client package as on a Mac; `carl install` is not available on Windows yet (run `setup.cmd` from the package). See [Clients on other computers](client-sync.md).

You already have llama.cpp? Set `llama.path` (or the variable `CARL_LLAMA_DIR`) to the folder of the build, or put `llama-server` on the path. The variable wins over the setting. Without both, CARL uses the newest build that `carl engine install` made, then the path.

## Where the files are

| What | Windows | Linux |
|---|---|---|
| Models | `%USERPROFILE%\models\gguf\MODEL\QUANT\FILE.gguf` (a folder for each model and quantization; `mlx` beside `gguf`) | `~/models/gguf/MODEL/QUANT/FILE.gguf` |
| Settings, the API key, the disk cache | `%USERPROFILE%\.config\carl` | `~/.config/carl` |
| llama.cpp builds from `carl engine install` | `%USERPROFILE%\.config\carl\llama.cpp` | `~/.config/carl/llama.cpp` |
| Logs | `%USERPROFILE%\models\logs` | `~/models/logs` |

`CARL_CONF_DIR` and `MODELS_DIR` move them. In an elevated terminal (run as administrator), CARL gives your user the access to its folders that an elevated run made; if Windows still says "Access is denied", the message names the one command to run in an administrator terminal.

## The GPU has its own memory

On a Mac the GPU and the CPU share the RAM. On a PC the GPU has its own memory (VRAM), and the model must fit there, or its MoE experts go to the RAM ([Memory](memory.md#a-pc-with-a-discrete-gpu)).

- CARL plans for the **free** VRAM when it looks, plus what its own server holds, less the memory kept free (`gpu.vram_keep`, 0.5 GiB for each GPU). The desktop, a browser and games use VRAM too. Close them to get more.
- A model that does not fit is refused with a sentence that says what it needs, what the GPU has and the largest context that fits. `FIT_CHECK=0` skips the check (CAUTION: NVIDIA's driver may then move the model into system memory, where it crawls).
- With more than one GPU, CARL splits the layers over them by the room of each (`-dev`, `-ts`, `--split-mode layer`). `gpu.devices` takes the numbers that `carl gpu` shows (`0,1`).
- On a Windows or Linux PC CARL uses the default `-ub` 2048 (a Mac 512), and Auto-tune measures 512, 1024, 2048 and 4096 for each model.

## The llama.cpp build

| GPU | Build |
|---|---|
| NVIDIA, compute capability 7.5 or newer, a driver for CUDA 13 | CUDA 13 |
| NVIDIA with a driver for CUDA 12.4 or newer | CUDA 12 |
| No GPU | The CPU build |

`gpu.backend` (`auto`, `cuda`, `cpu`) picks the build. The CUDA build needs matching K and V cache types; a start with `KV_K` and `KV_V` that differ is refused.

## What is different from a Mac

| Part | Windows | Linux |
|---|---|---|
| The server, the dashboard, router mode, Auto-tune | Yes | Yes |
| The words | "this computer", not "this Mac"; the command word is `carl` | The same |
| Keep-awake | `SetThreadExecutionState`, held while the server runs (`KEEP_AWAKE=0` turns it off) | `systemd-inhibit`, else a note |
| llama.cpp updates | `carl engine install` | `carl engine install` |
| The VM network (`--vm`) | Not used | Not used |
| `--host ADDR` | Serves one address; the start prints a firewall note, because Windows Firewall may ask once | Serves one address |
| Stop | `q` then `s` in the dashboard, or Ctrl-C in the foreground; the console stop reaches the server | The same |
| The client sync service | Task Scheduler at logon | `systemd --user` (`loginctl enable-linger $USER` keeps it after you log out) |

- **Windows Terminal** draws the dashboard's boxes and colours; the old console host may not. If the PowerShell alias of the Microsoft Store answers `python`, use `py`.
- CARL's private folders get an access list for your user alone, instead of the mode 700 of a Mac.
- The tests for these systems run on a Windows PC and in WSL.

## Experts in the RAM and the load mode

A mixture-of-experts model (the Qwen3.6 35B-A3B, the Gemma 4 26B-A4B) can run on a small card with the experts of some layers in the RAM (`--n-cpu-moe N`). CARL finds the smallest N for which both sides fit; `llama.n_cpu_moe` sets it by hand (`0` keeps every expert on the GPUs). Details and the measurements: [Memory](memory.md#experts-in-the-ram-layer-placement).

With any expert layer in the RAM, a start uses n-gram speculation and leaves MTP off, unless you or Auto-tune chose a speculation. `carl tune` measures MTP against n-gram there and keeps MTP only if it wins by 3%.

On a PC, `carl start` uses `--load-mode none` (the weights are read into the RAM, not mapped) whenever the free RAM holds what the server keeps there and the load reserve (`llama.load_reserve_gib`, 8 GiB). Else it uses the mapped load. Your own `--load-mode`, `--mmap` or `--no-mmap` wins.

## GPUs that share the RAM

Designed and tested with fixtures:

- It finds the GPU through the driver's files (Linux) or Windows' adapter and counters, and through the device list of llama-server.
- An **APU** shares the RAM with the CPU. Its memory is the carve-out that the BIOS sets aside plus the system memory the driver maps (on Linux the driver's GTT; on Windows the shared memory), capped by the RAM less the reserve. `gpu.shared_limit_gib` sets a limit by hand (0 = CARL works it out). With everything in this room, a model runs with every layer on the GPU. `carl gpu` calls such a PC "a shared-memory PC".

## Settings and variables for a PC

| Setting | Variable | Default | What it does |
|---|---|---|---|
| `gpu.vram_keep` | `CARL_VRAM_KEEP_GIB` | 0.5 | The VRAM kept free on each GPU, in GiB |
| `gpu.backend` | `CARL_GPU_BACKEND` | auto | The llama.cpp build |
| `gpu.devices` | `CARL_GPU_DEVICES` | every GPU | The GPUs a model uses, by their numbers in `carl gpu` |
| `gpu.shared_limit_gib` | `CARL_SHARED_LIMIT_GIB` | 0 | What a model may use on a GPU that shares the RAM (0 to 1024) |
| `llama.path` | `CARL_LLAMA_DIR` | empty | The folder of the llama.cpp build |
| `llama.n_cpu_moe` | `N_CPU_MOE` | auto | The layers whose MoE experts stay in the RAM |
| `llama.load_reserve_gib` | `LOAD_RESERVE_GIB` | 8 | The free RAM that load mode none leaves |
| `engine.name` | `CARL_ENGINE` | auto | The engine that serves the models ([Glossary](glossary.md#11-pcs-gpus-and-engines)) |

`carl config show` lists every setting with what it does, except `engine.name`: it is listed once there is a second engine. The Settings tab's Server panel has the GPU rows on a PC.

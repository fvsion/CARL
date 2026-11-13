# CARL Reference: Memory: what fits and what context costs

[Index](README.md) · the GPU memory limit, a PC's discrete GPU, the context length and the context memory.

## GPU memory limit and what fits

macOS sets a maximum for the memory that the GPU can use (the GPU memory limit): the Metal value `recommendedMaxWorkingSetSize`.

| Mac | GPU memory limit |
|---|---|
| M3 Pro, 36 GB (measured) | **28.1 GiB (78%)** |
| M2 Max, 32 GB (measured) | 25.0 GiB (78%) |
| Less than 32 GB (estimate) | About **2/3 of RAM**: ~16 GiB on a 24 GB Mac, ~10.7 GiB on a 16 GB Mac |
| 32 GB or more (estimate) | About **3/4 of RAM** |

- `sudo sysctl iogpu.wired_limit_mb=N` overrides this limit until the next restart of the Mac.
- `carl fit` (`src/carl/cli/commands/fit.py`) calculates these values for each model in the catalogue and in the models folder:
  - **Memory needed** = weights (the whole file; an MTP drafter while MTP runs) + context memory (the KV cache: context × slots × bytes per token) + recurrent state for each slot + compute buffers + with MTP the draft context + a margin of 0.75%. See [The estimate](#the-estimate-what-is-counted-and-the-measured-check).
  - The largest context for each slot that keeps the memory needed ≤ the memory that a model can use. That memory is the GPU memory limit, or the RAM minus the memory kept free for macOS and apps (6 GiB; 10 GiB while the VM network is up), whichever is smaller. CARL offers contexts in 4K steps. On a PC with discrete GPUs a MoE model shows its largest context with the experts of some layers in the RAM (a note names how many layers; decision A48), and every figure is at the model's own `-ub`.
- A model with sliding-window layers (Gemma 4) needs these layers at full length with the full cache (`--swa-full`). With the window cache, they need only the window + the batch size (`-ub`, 512 on a Mac) for each slot ([Sliding-window models](caching.md#sliding-window-models)).
- `carl fit --ram 24` shows the values for a 24 GB Mac (the estimated limit).

**Sources of the inputs:**

| Input | Source |
|---|---|
| The GPU memory limit | A Swift probe that runs one time (cached in `~/models/.metal-limit`). An `iogpu.wired_limit_mb` override has priority. Without Swift, the 2/3 or 3/4 estimate. |
| The model shape | The local GGUF. For a model that is not downloaded, the first 24 MB of the file with an HTTP range request (cached in `~/models/.gguf-shapes.json`). |

**Results** (the largest context for each slot, 1 slot, q4, with the speculation of the catalogue: MTP counted; Phase 23.4.4 estimate):

| Model | Weights | M3 Pro 36 GB (28.1 GiB) | 24 GB Mac (~16 GiB) | 24 GB, limit raised to 18 GiB |
|---|---|---|---|---|
| `qwen3.8-27b` (Q4) | 15.3 GiB | 256K | does not fit | ~52K |
| `orcarouter-27b` (Q4) | 16.6 GiB | 256K | does not fit | ~20K |
| `qwen3.8-27b-q3` | 12.2 GiB | 256K | ~84K | ~136K |
| `orcarouter-27b-q3` | 13.6 GiB | 256K | ~44K | ~100K |
| `qwen3.6-35b-a3b` | 21.1 GiB | 256K | does not fit | does not fit |
| `qwen3.6-35b-a3b-iq3` (24 GB default) | 13.1 GiB | 256K | ~144K (2 × 96K with n-gram: 15.0 GiB) | 256K |
| `qwen3.8-27b-iq3` | 10.2 GiB | 256K | ~140K (2 × 96K with n-gram: 15.0 GiB) | ~196K |

What a start of each model gets on a 24 GB Mac (16.0 GiB), with Auto fit's order ([Auto fit's order](#auto-fits-order)):
- The IQ3 35B and the IQ3 27B: 2 × 96K with n-gram (MTP does not fit). `qwen3.8-27b-q3`: 2 × 64K with n-gram. `orcarouter-27b-q3` (catalogue context 64K): 1 × 64K with n-gram.
- `orcarouter-27b-q3` and `saluki-27b` are the exceptions to the 96K window of the catalogue. `orcarouter-27b-q3`'s catalogue context is 64K, because 96K does not fit a 24 GB Mac (larger Macs use the Q4 build). `saluki-27b`'s is 48K, for 16 GB Macs.
- The Gemma models (window cache): the E4B and the 12B get 2 × 96K with MTP. The 26B-A4B gets 2 × 64K with n-gram. The 31B does not fit.
- A start of `orcarouter-27b-q3` without an Auto-tune result shows a note: run `carl tune orcarouter-27b-q3`. Auto-tune selects 96K when it fits.

**16 GB Macs** (~10.7 GiB GPU memory limit, estimate; a model can use 10.0 GiB, because CARL keeps 6 GiB free):
- Auto fit chooses `gemma-4-e4b` (4.6 GB) for the everyday goal: 2 × 96K with MTP need 8.3 GiB with the window cache.
- `saluki-27b` (7.9 GB): 2 × 48K, its catalogue window, need 9.98 GiB with n-gram (measured: 9.89 GiB). The file with the MTP head (8.35 GB, not in the catalogue) and MTP needs 12.2 GiB (measured: 12.12 GiB): MTP does not fit.
- Auto fit chooses `gemma-4-12b` (7.2 GB) for the hard code goal: 2 × 96K in 8.6 GiB with n-gram and the window cache (with MTP 10.2 GiB: MTP does not fit). Its quality rank (8) is above Saluki's (9) since 2026-10-13: on the medium agent tasks both passed every hidden test, and the 12B hands work to the coder as CARL's rule says.
- `qwen3.8-9b` (5.8 GB) also fits with 2 × 96K (8.2 GiB with n-gram; 11.1 GiB with MTP). Its quality rank (10) is below the Gemma models.
- No other catalogue model fits under the default limit. The IQ3 27B (10.2 GiB of weights) needs a raised limit (`sudo sysctl iogpu.wired_limit_mb=…`).
- `carl fit --ram 16` shows the values.

**The hybrid Qwen3.6 and Qwen3.8 models on Metal** (measured 2026-10-03, M2 Max 32 GB, llama.cpp 0.5.0):
- `llama-bench` on the 9B (5.4 GiB, `qwen35`: 8 attention + 24 recurrent layers) gives a read speed of 464 tok/s (pp512) and a write speed of 24.4 tok/s (tg128; 25.4 with q4 context memory). CARL's flags cost nothing.
- The recurrent layers keep the write speed of dense Qwen3.8 far below what the memory bandwidth allows. This is also true for the 27B.
- The 35B-A3B MoE reads 3B parameters for each token: ~46 tok/s.
- A short, predictable answer ("count to 20") is written at ~47 tok/s on the 9B, because 90% of the MTP guesses are correct. Prose and code do not get this speed.

**Parallel requests** (the same Mac and date; `llama-batched-bench`, 1K-token prompts, 128 tokens written, q4 context memory, the total write speed in tok/s):

| Model | 1 at once | 2 | 4 | 8 |
|---|---|---|---|---|
| 9B | 25.5 | 29.2 | 40.6 | 45.0 |
| 35B-A3B IQ3 | 45.7 | 50.4 | 65.7 | 69.8 |

- The total read speed decreases with parallel requests: 9B 481 → ~290 tok/s, 35B 599 → ~510 tok/s.
- CARL allows 4 slots at most, because each request becomes slower as more run at the same time. CARL offers 3 and 4 slots only where they fit.
- The parallel step of Auto-tune measures 1–4 requests for each model.

**Limits of the calculation:**
- The compute buffers and the draft context are calibrated on one hybrid (Saluki, a Qwen3.8-27B) and checked on the Gemma 4 E4B. The MoE and the other Gemma sizes use the same rule, but CARL has no measurement of their buffers on llama.cpp 0.6 ([The estimate](#the-estimate-what-is-counted-and-the-measured-check)).
- The fit check does not include the RAM that macOS and its apps need, or the RAM of the server outside the GPU (the CPU compute buffers: 0.1 to 0.4 GiB; the context checkpoints; the RAM cache).
- CAUTION: Keep ≥ 6 GiB free. Look at the memory pressure in the dashboard (the THIS MAC card of the Live tab).

## The estimate: what is counted, and the measured check

Phase 23.4.4 (2026-10-09) replaced the flat 1 GiB of buffers with the parts that llama.cpp 0.6 allocates (`src/carl/domain/memory/fit.py`). **Memory needed** = the sum of these parts + 0.75%:

| Part | Rule |
|---|---|
| Weights | The whole model file. A file with an MTP head counts the head also when MTP is not used. A separate drafter file (Gemma 4) counts only while MTP runs. |
| Vision projector | When `llama.vision` is on and the projector is downloaded: its file (about 1 GB), with the margin. Its compute buffer is in the margin. |
| Other drafters | A DFlash, DSpark or EAGLE-3 drafter counts only while its speculation runs: its file, and a second context of the size of an MTP draft context's compute buffers (no recurrent state of its own). Its own KV is small beside the model's and not measured. |
| Context memory (KV) | Attention layers × KV heads × (key + value length) × bytes for each element × context × slots. Sliding-window layers: the window + `-ub` tokens for each slot, or the full context with the full cache. |
| Recurrent state | For each slot. With MTP: × (1 + guesses). llama.cpp keeps one state for each guess, to go back when a guess is wrong. |
| Compute buffer | KV pool (context × slots) × (the K and V of the widest attention layer in f16 + the batch size `-ub` × 2 bytes of mask) + `-ub` × the widest activation of one token × 8 bytes + 32 MiB. With the full sliding-window cache, a second mask over the pool. |
| MTP draft context | When MTP runs: the KV of the MTP head's layer in f16 at the full pool (a drafter file shares the model's KV: 0), and two more compute buffers of the same size as the main one. |
| Margin | 0.75% of the sum: allocator slack and the error of the rule. |

- **The widest activation** comes from the GGUF header: the FFN length; for an MoE the outputs of the used experts before they are added (embedding length × used experts: 2,048 × 8 on the 35B-A3B); the projections of the recurrent layers.
- **The batch size changes the compute buffer.** On the 27B at 2 × 32K: 0.41 GiB at `-ub 512`, 0.35 GiB at `-ub 256`.
- **Why 0.75%:** below 0.51%, the 35B Q4 with MTP + n-gram at 2 × 96K fits the M2 Max (25.0 GiB), where it paged and failed (below). Above 0.78%, Saluki's 2 × 48K (measured 9.89 GiB) does not fit a 16 GB Mac (10.0 GiB).

**The measured check** (Saluki, `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`, M3 Pro, llama.cpp 0.6.0, KV q4_0, `-lv 4`; Phase 23.4.2): the estimate of KV + recurrent state + compute (+ the draft context) against the buffers in the server logs, in GiB:

| Setup | Speculation | `-ub` | Measured | Estimate | Error |
|---|---|---|---|---|---|
| 1 × 64K | none, n-gram | 512 | 1.682 | 1.681 | −1 MiB |
| 2 × 32K | none | 512 | 1.828 | 1.827 | −1 MiB |
| 2 × 48K | none | 512 | 2.547 | 2.546 | −1 MiB |
| 2 × 32K | none | 256 | 1.771 | 1.763 | −8 MiB |
| 2 × 48K | none | 256 | 2.475 | 2.466 | −9 MiB |
| 1 × 48K | MTP n=1 | 512 | 2.326 | 2.320 | −7 MiB |
| 1 × 64K | MTP n=1, MTP + n-gram | 512 | 2.904 | 2.898 | −6 MiB |
| 1 × 64K | MTP n=2 | 512 | 3.050 | 3.044 | −6 MiB |
| 2 × 32K | MTP n=1, MTP + n-gram | 512 | 3.196 | 3.190 | −6 MiB |
| 2 × 48K | MTP n=1 | 512 | 4.354 | 4.346 | −8 MiB |
| 1 × 64K | MTP n=1 | 256 | 2.698 | 2.704 | +6 MiB |
| 2 × 32K | MTP n=1 | 256 | 3.001 | 2.997 | −5 MiB |
| 1 × 64K | MTP n=1 | 128 | 2.599 | 2.608 | +9 MiB |

- **The Gemma 4 E4B** (2 × 64K, `-ub 512`; Phase 20): compute 714.3 MiB measured with the window cache, 718.0 estimated; 840.8 MiB with the full cache, 840.0 estimated.
- **The weights:** with MTP, the model buffer was 7.77 GiB (the file: 8.35 GB, 7.78 GiB). **Without MTP, llama.cpp 0.6.0 did not put the head on the GPU:** the model buffer was 7.35 GiB, 0.42 GiB less. CARL still counts the whole file (the estimate is 0.42 GiB too large then). For this reason, the catalogue takes the file without the head when MTP is off.
- **The draft context:** MTP creates a second context ("creating MTP draft context against the target model"). Its KV is f16 also when the main KV is q4_0 (256 MiB at 64K for the head's one layer). It has two schedulers, each with a compute buffer of the main one's size (423 MiB against 421 MiB at 64K). The recurrent state of the main context doubles with 1 guess and triples with 2.
- **Not GPU memory:** the CPU-mapped part of the model (265 MiB, inside the file mapping) and the CPU compute buffers (0.10 GiB without MTP, 0.27 GiB with MTP at `-ub 512`).

**The two starts that did not fit** (Phase 23.5 item 5a; no buffer sizes were logged for them, so the check uses their GGUF headers and file sizes):

| Start | Mac | Old estimate | New estimate | Result |
|---|---|---|---|---|
| `gemma-4-12b`, 2 × 96K, full cache, MTP from the drafter | M3 Pro 36 GB (28.1 GiB) | 25.7 GiB | 30.8 GiB | Does not fit. `cache.swa = auto` takes the window cache (10.2 GiB). |
| `qwen3.6-35b-a3b` Q4, 2 × 96K, MTP + n-gram, 2 guesses | M2 Max 32 GB (25.0 GiB) | 23.3 GiB | 25.06 GiB | Does not fit. Auto fit and the launcher keep 2 × 96K with n-gram (23.1 GiB). |

- The 12B with the full cache: the sliding-window layers (8 KV heads × 512) are wider than the full-attention layers, so the compute buffer grows to 2.0 GiB, and the drafter adds two more (3.9 GiB). Without MTP the full cache needs 26.6 GiB.
- The 35B: the draft context adds 1.69 GiB (its KV 0.38 GiB, two compute buffers of 0.66 GiB) and the recurrent state × 3 adds 0.25 GiB. The new estimate is only 0.06 GiB over the limit. Measure the 35B with `-lv 4` to confirm it.

## A PC with a discrete GPU

On a Mac, a model may use the GPU memory limit (see above). On a Windows or Linux PC, the GPU has its own memory (VRAM), apart from the RAM. [Windows and Linux](windows-linux.md) gives the setup. This section gives the rules of the memory.

| Memory model | Where | What a model may use |
|---|---|---|
| Unified | A Mac; a PC with no GPU (the model runs from the RAM) | The smaller of the GPU memory limit and the RAM less the memory kept free |
| Discrete | A PC with a GPU | On each GPU: the free VRAM when CARL looks, plus what CARL's own server holds there, less the VRAM kept free; the GPUs added up. The RAM does not cap it. |
| Shared | A GPU that shares the RAM (an APU) | The carve-out that the BIOS sets aside, plus the smaller of the system memory the driver maps and the RAM less the memory kept free |

- **VRAM kept free.** `gpu.vram_keep` (default 0.5 GiB, `CARL_VRAM_KEEP_GIB`) is the part of each GPU that CARL leaves free, for the growth of the desktop while the server runs. The context of the backend is already in the estimate. For a program that shares the card, set it to 0.5 GiB plus that program's peak.
- **Free, not total.** The desktop, a browser and games use VRAM. The free VRAM is read again after 5 seconds, so `carl fit` gives the same answer with the server running as with it stopped (CARL's own server is added back).
- **The split.** A model that fits the GPUs added up is spread over them by layers, each GPU getting layers in proportion to its room after the fixed costs. A GPU that would get no layer is left out, with the reason in `carl gpu`. A start on one GPU (`gpu.devices`) fits when it fits that GPU's own room. `carl fit --vram 8,8` and `--vram 12,6` preview two GPUs.
- **Refused starts.** A start that does not fit is refused with one `error:` sentence: what it needs, what each GPU has, and the largest context that fits. `carl fit --check` is the same check.
- **The estimate by backend.** See [Off Metal](#off-metal-cuda): the constants of CUDA are measured on a GeForce RTX 2070 SUPER.
- **Batch size.** A PC starts with `-ub` 2048 (a Mac 512) until Auto-tune measures the model's own. Order: an `-ub` you typed, then the model's saved choice, then its Auto-tune record, then the default. The context comes first: a larger `-ub` may not move the setup down Auto fit's order (2 × 96K, 2 × 64K, 2 × 48K, then one slot of 96K, 64K, 48K).

### Experts in the RAM: layer placement

A mixture-of-experts model (Qwen3.6 35B-A3B, Gemma 4 26B-A4B) has many experts in each layer, and each token uses a few. When the model does not fit the GPUs whole, CARL keeps the experts of the first N layers in the RAM (`--n-cpu-moe N`) and everything else on the GPUs. This is the placement that llama.cpp does, by layers.

- CARL finds the smallest N for which the GPU side fits the GPUs (split as above) and the RAM side fits the RAM less the memory kept free, with a RAM cache of at least 1 GiB. The smallest N keeps the most experts on the GPUs. The plan leaves 128 MiB of each GPU to spare, because the free VRAM moves.
- `llama.n_cpu_moe` sets N: a number is used as it is (and checked), `0` keeps every expert on the GPUs, `auto` lets the plan find it. A dense model, a Mac and a GPU that shares the RAM never offload.
- With any expert layer in the RAM, MTP is off by default (n-gram speculation instead), because its draft context takes GPU memory and it did not pay on a card with experts in the RAM. `carl tune` measures MTP against n-gram there and keeps it only if it wins by 3%.
- **Measured** on an 8 GB RTX 2070 SUPER: the A3B IQ3 at 2 × 96K keeps the experts of 40 layers in the RAM at `-ub` 1024 and writes 25.0 tok/s; at 2048 all 41 layers' experts are in the RAM and it writes 21.6 tok/s. Auto-tune weighs the two: the context first, then the write speed, and a larger `-ub` only within about 3% of the best.
- **Load mode.** `--load-mode none` reads the weights into the RAM instead of mapping them. A PC uses it whenever the free RAM covers what the server keeps there plus the load reserve (`llama.load_reserve_gib`, 8 GiB). With the experts in the RAM it reads prompts about 11% faster (pinned host memory) with 2.9 GiB less working set. A Mac takes the same rule (on Metal both modes read and write at the same speed).

### The MoE plugin

The MoE plugin is for MoE models on discrete GPUs; it is off on unified memory and for dense models. It will be CARL's first built-in plugin ([CARL's plugins](plugins.md#carls-own-plugins-the-plugin-seam)). What exists in 1.21.0 and what comes next:

| Part | State |
|---|---|
| Layer placement (`--n-cpu-moe`, above) | In CARL, without the plugin |
| The expert trace logger and the cache simulator | Built, as development tools. They measure which experts each layer picks and whether a changing cache beats a fixed set. |
| The native build of llama.cpp | Built, as a development tool, and checked against the release build. It is the toolchain for the engine's patches. |
| The expert engine, mode 1: the most-used experts on the GPUs from a profile of use, the rest on the CPU, a repacked file | Next version |
| The expert engine, mode 2: mode 1 plus predictive prefetch of the next layer's experts into GPU slots, with a CPU fallback, so the output never changes | Next version |
| The profile: per-expert counts (no text) that refresh the placement at the next load | Next version |

The engine will pick its mode for each machine from the measured copy speed over PCIe, and its output is the same as layer placement's. The native build is the only native code in CARL; CARL's Python stays standard library only. On Windows the engine needs CARL's own source build (LLVM's clang for the CPU code, Visual Studio 2022 17.4 or later for its headers and as the CUDA host compiler, CUDA); the release build, which `carl engine install` fetches, runs layer placement only. On Linux the system's gcc matches the release. CARL changes llama.cpp only by addition (new files, wired in through upstream's own extension points), so a new llama.cpp version takes the patches without a rewrite.

## Off Metal: CUDA

Everything above is measured on Metal, and a Mac's estimate is unchanged. On a PC the same rule takes the constants of the
backend that runs the start (`src/carl/domain/memory/calibration.py`). The backend is the one in llama.cpp's name for the
device the start runs on (CUDA0); a start on the CPU uses the Mac's formula.

| Backend | Compute buffers | The backend's own context (each GPU) | Weights on the GPU |
|---|---|---|---|
| Metal | the formula above, its activations part lifted where the M3 Pro measured more (A57, below) | 0 | the file less the idle MTP head (A57) |
| CUDA | the larger of a small pool's buffer and act + what the pool adds, never under a measured start and about 5% over at most (A58, below) | 177 MiB | the file less `token_embd` and Gemma's per-layer embeddings, which stay in the RAM, plus a copy of `token_embd` as the output layer when the model ties the two |

- **Measured once, so far** (llama.cpp b11429, a 0.6B Q8_0 with 2 slots of 8K and q4_0 K and V, on an 8 GB GeForce RTX 2070 SUPER): llama.cpp's CUDA0 buffers were 604.15 MiB of weights, 504.00 MiB of KV and 136.53 MiB of compute. The KV matches the estimate exactly. The Metal formula gives 124.0 MiB of compute, so the CUDA compute buffer is 10% above it. The 157.65 MiB of `token_embd` stay in the RAM, and the output layer (tied to it) is a copy on the GPU. Outside llama.cpp's buffers the CUDA context holds 0.1 GiB (the process held 1351 MiB).
- **Measured again on the pinned build** (llama.cpp b11738, eleven starts on the same card: a 0.6B at 8K x 2, 8K, 32K q8; the Gemma 4 E4B at 8K, 32K, 16K x 2, 32K q8, 48K, 32K with the window cache): the KV matches the estimate to the byte, the compute buffer of the E4B is within 3.5% of the Metal formula (so no extra 10%), the 0.6B's is 14% to 33% below it (the estimate stays above), and the context beyond the buffers is 113 MiB for the 0.6B and 233 MiB for the E4B on CUDA. The constants above follow from that; they are not yet checked on a card of 16 GB or more.
- **Gemma's per-layer embeddings** (the E4B's `per_layer_token_embd`, 1565 MiB of 4378) stay in the RAM too, and the weights on the GPU leave them out: the E4B's estimate on CUDA is within 0.2% of the process's memory (it was 40% to 43% over, and the E4B at 32K with q8 on the 8 GB card was refused at 5.0 GiB where it ran in 3.6 GiB). With the MoE experts in the RAM they count in the RAM side.
- **MoE models on CUDA** (the A3B IQ3 and Gemma 4 26B-A4B with their experts in the RAM, `-ub` 512 to 4096, b11738): the compute buffer on the card is 0.9 to 1.6 times the formula, by `-ub` and by the sliding-window cache.
- **The compute factor by `-ub` on CUDA** (measured on the Windows test PC at 32K x 1, b11738; the buffer against the formula): the dense E4B 1.02, 1.11 and 1.19 at `-ub` 512, 1024 and 2048 (685 MiB against 576 at 2048); the A3B IQ3 1.44, 1.15 and 1.26 (1.40 at 4096); the 26B-A4B 1.15 and 1.02 with the full cache. CARL took 1.0, 1.1, 1.2 and 1.25 for a dense model and 1.5, 1.25, 1.3 and 1.4 for a MoE model at 512, 1024, 2048 and 4096. A larger pool is nearer the formula (the A3B at 2 x 96K: 1.02 at 1024, 1.07 at 2048), so the part over 1 shrinks with the square root of 32K over the pool.
- **The estimate corrected from the 128K measurements** (A56, replaced by the bullet below in A57; WCTX, 2026-10-29, 62 starts of the A3B IQ3 and Q4_K_M with the experts in the RAM, and the compute-buffer sweep of the E4B, the A3B, the 26B-A4B and the 0.6B):
  - The CUDA context beyond llama.cpp's buffers is 163 MiB (the median of the 62 starts; CARL counted 256 MiB). The E4B held 233 MiB there; the 70 MiB are inside the margin.
  - Dense at `-ub` 4096: 1.55 (the E4B 1.54 at 32K and 1.47 with the window cache; the 1.25 was 19% under at 32K). A small dense model is over-stated there (the 0.6B is at 1.00).
  - MoE at `-ub` 512: 1.6 (the A3B IQ3 1.57, the 26B-A4B with the window cache 1.62). 1024, 2048 and 4096 hold.
  - A MoE start with a pool of 128K or more (context x slots) at `-ub` up to 2048: the formula plus 120 MiB, where the square-root share was 1% to 15% over (the A3B buffer is the formula plus 46, 14 and 94 MiB at 512, 1024 and 2048 for the IQ3, 112, 80 and 66 for the Q4_K_M; the largest is 112). At 4096 the square-root share stays.
  - A K-quant MoE model (Q4_K_M and the like; not the IQ quants) at a pool of 32K or less: 140 MiB more compute buffer (the A3B Q4_K_M 442 MiB against the IQ3's 302 at 512 and 32K x 1, 35% under before). The rungs of Auto fit (48K and more) barely move.
  - Against the process (the buffers + 163 MiB) of the 80 cells of WCTX (2 x 96K, 1 x 128K and 2 x 128K at `-ub` 512 to 4096, N 40 and 36), with the MTP layer not counted (A52): the estimate was 2.3% to 9.7% over (median 4.2%), now 1.0% to 8.0% over (median 2.1%), never under; the four cells over 5% are at `-ub` 4096.
- **The estimate rebuilt as an upper bound** (A57, slice WEST; its rows and its "where it is over" are replaced by the bullet below, A58, and the rows of both by the model of A60 further down; the user: "We can't do allowances ... we would take an over instead of any unders"). The compute buffer is the sum of three parts, each from the formula's physical terms: `act`, which grows with `-ub` alone (the widest activation x 8 bytes x `-ub` and 32 MiB in the Mac's formula; on every backend the larger of that and `act_rows`, a table by `-ub` of the largest measured ub-only part, dense and MoE apart); `mask`, which grows with `-ub` x pool (2 bytes per `-ub` row and token of the pool, twice with the full sliding-window cache: the A3B at `-ub` 4096 grows 8 MiB per 1K tokens of pool from 32K to 192K); and `kv`, which grows with the pool alone (one layer's K and V in f16). The allocator's peak is at most the sum, so the estimate is an upper bound by construction; there are no factors, no step constants by pool range or quant, and it only grows with the pool and with `-ub` (the bisections in fit, offload and split rely on it; a unit test checks it). Between two `-ub` entries `act` is on the line between them, below the first in proportion to `-ub`, above the last on its slope.
  - The rows (MiB, dense / MoE): CUDA 77 / 346 at `-ub` 512, 152 / 346 at 1024, 446 / 429 at 2048, 954 / 1025 at 4096; Metal 73 / 73, 128 / 156, 366 / 375, 730 / 813. Each is the largest of the measured starts at that `-ub` (the compute buffer less the formula's `mask` and `kv`): CUDA from WCTX's 91 starts (the dense rows are the E4B at 16K; the MoE rows the A3B Q4_K_M at 32K for 512 and the 26B-A4B window cache for 1024 to 4096), Metal from the 19 starts of the M3 Pro (A3B IQ3 and E4B, llama.cpp 0.6.0).
  - Where it is over: the A3B IQ3 by 140 to 200 MiB of compute buffer at `-ub` 512 (the Q4_K_M holds 140 MiB more at 32K and 66 more at 128K, and the row keeps the larger), and at `-ub` 4096 the sum is over by 2 bytes x pool x the K and V width (384 MiB at 192K) because the allocator reuses the f16 K and V space for the mask there. The whole start against the buffers (and the context where measured) is over by 1% to 10% (median 4%) for the A3B, E4B and 26B-A4B, never under; a 0.6B at `-ub` 4096 is 30% over (a small model holds 20% to 40% of the E4B's ub-only part).
  - The CUDA context is the largest measured value, 177 MiB: the process less the buffers over WCTX's starts is 106 to 170 MiB for the A3B (median 163 of 66 starts) and 113 to 177 MiB for the E4B, 26B-A4B and 0.6B (their process is nvidia-smi's delta, which the desktop moves by up to 45 MiB). A56 used the median.
  - **The Mac** (M3 Pro, 19 starts, 0.6.0 build 11429, results/WEST/dryrun.md): the Mac's formula was under the server's compute buffer in 10 starts, up to 377 MiB (the E4B at 32K and `-ub` 4096: 992 against 1369 MiB; the A3B IQ3 at 32K and `-ub` 4096: 864 against 1132); the `act` rows lift it, so the Mac's numbers move at `-ub` 2048 and above and for models whose widest activation is small. The idle MTP layer is not loaded on Metal either (the A3B IQ3's model buffer is the file less 322 MiB): the estimate leaves it out when MTP is off, like the PCs (the 27B Saluki's measured totals were 0.31 GiB under the old estimate for the same reason).
  - **Use tuned** (A51) keeps removing `llama.ub` for the model: there is no global `-ub`, so a model with no Auto-tune record is started at the default `-ub` (512), or the plan's pick (A52), not at the config value of another model.
  - **llama.cpp's own dry run** (`llama-fit-params --fit-print on`, with the start's flags) was measured as the source and is not (results/WEST/dryrun.md): its model and context buffers equal the header's sums to the MiB on CUDA, but its compute buffer has a floor of `-ub` x about 1 MiB (the logits), 1.0 to 5.3 times the server's; it has no `--kv-unified`, so two slots come out 8% to 13% under; on Metal it leaves out the output layer's copy (398 MiB, the A3B) and Gemma's per-layer embeddings (1512 MiB, the E4B). It runs beside the estimate and the start line records the gap.
- **The estimate within about 5% over** (A58, slice WESTb; the user: "the overage needs to be around 5%"). A57 left the whole start 8% to 10% over for the A3B IQ3 at `-ub` 4096 and 6.6% at `-ub` 512. The compute buffer is now the larger of two numbers, and still never under any measured start, with no allowance for a pool range, a quant or a model:
  - **Act and the pool's part.** `act` + `pool`, where `pool` is what the mask and the K and V view add together. The allocator lays the f16 mask over the K and V space: the A3B's buffers grow by 6 MiB per 1K tokens of pool at `-ub` 2048 (mask 4 + view 2) and by 8, not 10, at `-ub` 4096 (the mask alone), from 32K to 256K (the intercept at 4096 is 951 MiB from 32K to 192K). `ComputeParts.pool` is mask + view up to a mask twice the view, the mask alone from four times, and between them on the line; it only grows. `act` is the part under the pool's, by `-ub`, measured at pools of 96K and more: it was the largest of all the starts at that `-ub`, small pools included, which is what put 384 MiB over the A3B at 192K.
  - **The floor.** A small pool's buffer is not in the pool's part but in the activations: the A3B IQ3 at 32K holds 302 MiB at `-ub` 512 and 606 at `-ub` 2048, where `act` + `pool` is 238 and 574; the E4B at 16K 637 at 2048 (503 with the window cache). `floor_rows` holds them by `-ub`; the estimate is the larger of the two.
  - **The stage.** With the experts in the RAM llama.cpp copies a layer's experts to the GPU for a large batch, and the compute buffer holds the copy: the A3B Q4_K_M, with Q6_K layers of 498 MiB where the IQ3's largest is 356, holds 34 MiB more with those layers in the RAM, 140 MiB more than the IQ3 at `-ub` 4096 and at 32K and `-ub` 512 (the 142 MiB the layers differ by), 66 MiB at 512 and 1024 at 128K and more, and 78 MiB less at 2048. `stage_of` is the largest expert layer and each row counts it by its weight (0.465 at 512 and 1024, 0 at 2048, 1 at 4096; the floor 1). A dense model has none. The rows are fitted so that the Q4_K_M and the IQ3 are both covered; a quant with a larger layer follows by itself.
  - **The rows** (MiB, dense / dense with a window cache / MoE / MoE with sliding-window layers / the same with a window cache; the weight): CUDA `act` 77 / 77 / -23 / -4 / 112 at `-ub` 512 (0.465), 152 / 152 / 9 / 29 / 141 at 1024 (0.465), 302 / 297 / 383 / 383 / 429 at 2048 (0), 1018 / 730 / 595 / 595 / 617 at 4096 (1); the floors 0 / 0 / -54 at 512, 3 at 1024, 638 / 504 / 251 at 2048 and 4096. Metal's are in `calibration.py` (no stage: the experts are in the same memory).
  - **Against the measured starts** (110 on CUDA, 24 on Metal): never under; the whole start (against the buffers and the context where measured) is over by 1% to 4.5% for every start of the A3B, the Q4_K_M, the E4B and the 26B-A4B on CUDA, and 0.8% to 1.6% on Metal. The compute buffer itself is within 5% over for most and over by up to 12% for: the Q4_K_M with its Q6_K layers on the GPU (the stage counts the largest layer of all, 8.8% at `-ub` 512 and N 18 to 30), the Q4_K_M at `-ub` 2048 (5.4%), the 26B-A4B with the full cache at 512 to 2048 (7% to 23%: the window cache holds 116 MiB more at 512 and the row keeps the larger), the E4B at 8K and 12K (11% and 5%: the floor is flat) and the Mac's A3B IQ3 at 512 (14%: the Mac's formula is the floor of `act`; it is 1% on the whole start). A 0.6B (a test model) is over by 35% to 174% in compute and 5% to 32% in the whole start; it is reported, not hidden (the dense rows are the E4B's).
  - **Against the Windows test PC now** (the desktop at 2.9 GiB, `CARL_VRAM_KEEP_GIB=1`, the estimate against nvidia-smi's process of the start, results/WESTb): the A3B IQ3 at 1 x 128K, 2 x 96K, 1 x 96K, 1 x 64K, 2 x 64K and 1 x 48K, `-ub` 512 to 4096, and the Q4_K_M, are 2.6% to 3.4% over (one of 4.5%: the E4B at 8K); the E4B at 16K and 32K 1.1% to 2.7%, with the window cache and with the full one. The cases that do not fit with 1 GiB kept free (the A3B at 2 x 96K and 1 x 128K at `-ub` 2048 and 4096) were not started: the estimate says 4.3 to 5.5 GiB, the room was 4.2.
  - **The dry run** (`dry_run.argv_of`): with `--kv-unified` the dry run gets `--parallel 1` and the same `-c`, one stream of the whole pool, as the server has it; at 2 x 96K and `-ub` 512 it says 718 MiB where the server holds 718 (it said 622). Its logits floor still makes it 1.7 to 3.5 times the server's at larger `-ub`; it stays a cross-check.
  - **A buffer measured after a start is not added** to the estimate (the user: "Leave the buffer for the engine"): the MoE engine's slot pool takes that room.
- **The estimate built from the model** (A60, slice WESTc; the user: "The backup formula should be more general and detective than that. Like analyze the model and generically build the buffer"). The rows of the bullets above (a column for each kind of model, a weight for the stage) are gone: the compute buffer of a backend is one model built from the header alone (`calibration.py`, the sums in `gguf.width_sums`). The graph of a batch runs layer by layer, a layer's big tensors are freed when it is done, and what stays live through the whole graph is the attention mask, so the buffer is the masks and the largest of three working sets:
  - **The masks** are exact (2 bytes per `-ub` row and token of the pool, a second one for the sliding-window layers: their window and one batch with the window cache, the whole pool with `--swa-full`): the 26B-A4B's full cache holds the window cache's buffer plus the second mask to the byte.
  - **The attention's set**: the K and V view in f16 of the widest layer kind (exact: 2 MiB per 1K tokens of pool for the A3B beyond the mask, 4 for the 0.6B), the attention's and the recurrent layers' matrices' outputs times `-ub` (`attn_width`), Gemma's per-layer inputs (`extra_width`) and a part of the experts' copy. **The feed-forward's set**: the experts' copy (`stage_of`) and the gate, up and activation of the experts' choice or the dense width (`ff_width`, three tensors of `-ub` x width x 4 bytes), with the per-layer inputs and a small fixed part. **The big set**: every matrix's output of the widest layer (`layer_width`), of the attention and of the inputs outside the layers (`extra_width`), which is the buffer at a large `-ub`, and a part of the experts' copy. The sets are sums of named terms (`calibration.terms`) with a coefficient each; the view, the experts' copy in the feed-forward's set and the feed-forward's three tensors have their physical values. No term is a kind of model: the first version had a constant for the dense model (129 MiB), one for the hybrid (51.5) and one for the MoE (-279), each the allowance of the one model of its kind, and the review removed them (A60).
  - **The experts' copy follows the layers in the RAM** (WESTc, measured on the Windows test PC: the A3B IQ3 at 32K and `-ub` 512 and 1024, N 34, 35, 39 and 40; the Q4_K_M at N 18 to 41). With the experts of the blocks below N in the RAM llama.cpp copies them to the GPU for a large batch. With separate gate and up matrices the buffer holds the largest down matrix of those blocks and the median one, to the MiB: the IQ3 220 MiB at N 34 (all its down matrices are 110) and 246 from N 35 (block 34's is 136), the Q4_K_M 352 at N 30 (176) and 386 from N 35 (block 34's Q6_K is 210); with one gate_up matrix (the 26B-A4B) it is the largest layer, 408 MiB. The header's per-tensor sums give it (`expert_down_bytes`, `expert_fused`, header cache version 5), the planner's compute buffer follows N (`Load.compute_by_n`, `offload.gpu_load`), and the layer-total model of A58 (the largest expert layer of the whole model) was under by 9% to 17% at N 34 and 35. A measured copy is not a function of the quant's name.
  - **The same graph on every backend.** The Gemma 4 E4B beyond the masks is 429.4 MiB at `-ub` 2048 and 857.4 at 4096 on Metal and on CUDA alike; the A3B IQ3's big set is 214 to 219 KiB per token of `-ub` on Metal and 233 on CUDA, proportional to `-ub` from 1024 to 4096. What CUDA adds is its staged copy and a few buffers (the E4B's full cache holds one more feed-forward tensor, the A3B Q4_K_M's feed-forward set follows the layer it stages).
  - **Fitted, not tuned.** A development tool finds the coefficients by a linear program: the least total over the measured buffers (126 CUDA starts of the five models, the 0.6B too, 24 on Metal), no start under; a start belongs to the set that fits it best, found from the data (a bigger pool than the smaller pools of its group means the attention's set). Each is rounded up. 12 numbers for CUDA and 7 for Metal (the view, the experts' copy and the feed-forward's three tensors in the feed-forward's set are fixed at their physical values); none for a model, a quant or a pool, and no constant that is negative.
  - **Against the measured starts**: never under (150 of 150). The whole start is over by 1.2% to 7.0% for the CUDA starts (five are over 5%: the A3B IQ3 at 32K and `-ub` 2048 with N 40 is the far one, 6.9%) and 0.8% to 1.8% on Metal. The compute buffer is 2.4% over at the median, 8.7% at the 90th percentile and 18.3% at most on CUDA (46 of 126 starts over 5%, where the first version of A60, with the three constants, had 35 and 12.3% at most, and the rows of A58, fitted to each start's model, 13 and 18.1% at most on the 16 starts WESTc added), and on Metal within 0.9% (24 starts; the rows were 13.9% at most). A60's target, within about 5% on every start, is not met on CUDA. The overs are in the attention's, the feed-forward's and the big set at `-ub` 1024 and 2048: the A3B IQ3 holds 174 MiB in the attention's set at 1024 and 382 at 2048 beyond the view and 142 at 512, the Q4_K_M 240, 304 and 208, and at `-ub` 2048 and 32K the A3B's buffer is 232 MiB beyond its experts' copy and the 26B-A4B's 172 although the 26B-A4B's layers are 25% wider: steps of the allocator's layout that one function of the header does not follow, and the data hold no other model of a family to tell them apart.
  - **The check with each model held out** (a test of that tool): A60's gate is never under the model that was held out. It is met for the 0.6B (a dense model; 6% to 18% over) and, on Metal, for the E4B; it is not met for the others: without the A3B IQ3, 66 starts are 6.5% under at worst (the Q4_K_M holds 140 MiB more of the experts' copy than the IQ3 at `-ub` 4096, and with only the IQ3 fitted the copy's coefficient in the big set is not pinned to 1) and up to 14% over; without the Q4_K_M, 31 starts are 17% under at worst; without the 26B-A4B, the only Gemma MoE, 11 starts are 0.1% under at worst (one start) and up to 28% over; without the E4B, the only dense model with per-layer inputs, 13 starts are 11% to 27% under. On Metal two models cannot identify each other: without the A3B IQ3 the E4B alone leaves the recurrent layers 16% under, without the E4B the A3B alone leaves the per-layer inputs 11% to 81% over. The not-met cases are marked as expected failures in the test, so a fix has to remove the mark. The model carries to a MoE of another family (the 26B-A4B), and not reliably to a quant of a family that was met or to a kind of model that has an input of its own; that needs a measurement of it. A model not like the measured ones is the estimate's weakest case, and llama.cpp's dry run (below) is the check for a model whose file is local. The user's "never under, within about 5%" is met by the measured starts only for the compute buffer's never under; the 5% and the held-out models are the user's to accept.
- **MTP's draft context on CUDA** (the A3B IQ3 with every expert in the RAM): its KV is the head's (96 MiB at 48K, 192 MiB at 96K, as estimated), and it has one compute buffer of about half the main one's (352 against 670 MiB at 1 x 48K and `-ub` 2048; 272 against 558 at 1 x 96K and `-ub` 1024), with the recurrent state twice. Metal holds two buffers of the main one's size. MTP costs about 0.5 GiB on the card there, where CARL estimated 1.5 GiB.
- **The sliding-window cache grows with `-ub`**: the window plus one batch for each slot (Gemma 4 26B-A4B: 264, 292, 349 and 461 MiB of KV at `-ub` 512, 1024, 2048 and 4096).
- **Different K and V types** run the CUDA build's flash attention on the CPU, so a CUDA start with them is refused. Metal reads prompts about 5 times more slowly with them, and keeps the mix.

- **The need from llama-server's own fit** (A67, slice WESTd; the user: "We can't be under", "It has to be some form of
  E+D+B"). The estimate above still makes the plan and carl fit's table; the start then measures its plan with E, the
  server's own fit (`--fit on`, stopped when it has printed its memory breakdown, about 1 s, no weight loaded), which is
  the server's own buffers (exact at 232 of 232 starts, WESTc). The need is each number of its device rows plus 1 MiB for
  the print's floor and the backend's context; when it is over the estimate the plan is made again with that much less
  room, and the check refuses a start whose E does not fit. The start's `Memory:` line names the need and its source.
  - **A drafter in its own file** is not measured by E. Its need is its file, no KV, and a compute buffer bounded from the
    target's header and the drafter file: T, the f16 K and V of the target's two shared layers, plus terms by `-ub` of
    the cells, of the target's width and of the drafter file's size, fitted by linear programming over every draft
    context measured (36 on CUDA, 37 on Metal; none under), plus 50% on CUDA and 35% on Metal (what each backend needs
    when each model is left out of the fit). The first bound, fitted on the E4B and the 26B-A4B at a few settings, was
    under held-out starts: the 26B-A4B on Metal at -ub 512 and 1024 with the window cache (14% under), the 26B-A4B at -ub
    4096 on CUDA (5%); its second fit was under the 31B at -ub 4096 on Metal (30%). A fourth round, left out of the fit
    and run after the margin was set (the Gemma 4 12B and 31B, the E4B and the 26B-A4B at other -ub, contexts and slots,
    on the Windows test PC and the M3 Pro), is never under. After a first real start the draft context's buffers from its log are used
    instead. A start with a drafter always adds its record or bound, whatever the fit's log says about it, unless the
    fit printed a block for it (a build that can measure it): then that block counts, the drafter's weights too.
  - **Before a download** E runs on a sparse stub (the header from CARL's 24 MB read, then a hole to the real size): the
    same rows as the file. Auto fit's pick is measured so: `carl fit` gives each goal's pick with its need and the
    source (E on the file, stub E before a download, B when the fit cannot run, for example offline), and the
    catalogue pick (`carl download default`, the first run) takes the measured pick. When the need is more than the room,
    Auto fit chooses again with that much less room, as a start makes its plan again.
  - **The table's rows** (A72): `carl fit` measures Auto fit's picks and the models named on the command line
    (`carl fit NAME`; m in the dashboard's Auto fit and Models panels) the same way, at the largest window of each
    context memory type, and keeps each answer in `server-fits.json` (by the build, the file, the arguments and the GPU).
    Every other figure is CARL's estimate, marked `~`, and shows the kept answer once there is one. The three catalogue
    files that are not in the model library are measured by their 24 MB header read from Hugging Face, like any model not on disk.
  - **On Metal** the need counts the Host row's model too: Metal maps the whole file into its model buffer, so a real
    start's `MTL0_Mapped` buffer is the device's model and the Host's (the token embedding).
  - **Against real starts**: every catalogue model in the model library and gpt-oss-20b, two starts each (1 slot at -ub 512; 2
    slots at -ub 2048, with MTP where the file has its head), on the Windows test PC (CUDA; the experts in the RAM, or the last layers on
    the card, for a model larger than it) and on the M3 Pro (Metal; 8 models on their files, and all 14 on a sparse stub
    of the header, since its link reads the model library at about 6 to 15 MB/s: the 8 give the same buffers on the stub as on the
    file, to the hundredth of a MiB): E is never under the start's device
    buffers, 1 to 5 MiB over (the print's floor); on Metal the Gemma 4 models are 0.7 to 1.4 GiB over (the Host's
    model holds their per-layer inputs, which Metal maps only in part). The orcarouter Q3 and IQ3 and the heretic Q4_K_M are not in the model library
    and were not measured.
  - **B**, the upper bound where no llama-server with `--fit` is present: the same terms refitted over the measured
    starts and the E points, 620 on CUDA and 404 on Metal (every catalogue model in the model library and gpt-oss-20b; -ub 512
    to 4096, 1 x 16K to 1 x 128K and 2 x 96K, N 0, half and all on CUDA, both caches), never under any, and never under
    a model held out of the fit (Metal takes 22.7% more for that: gpt-oss-20b held out). It is an upper bound, not an
    estimate: +32% (CUDA) and +39% (Metal) at the median of the compute buffer, far more on small buffers. The estimate
    itself was under 63 of the 620 CUDA points (gpt-oss-20b by up to 18%, the others by 2.1% at most) and under 173 of
    the 348 Metal ones without MTP (gpt-oss-20b by up to 22%, the Gemma 4 31B by 11%, the 27B dense models by 6.5%, the A3B by the
    print's floor); E covers that at every start. B's MTP draft context is never under the 128 MTP E points: on CUDA
    it holds 0.6 of the main compute buffer (the estimate's 0.55 was 3.3% under the A3B IQ3 at 2 x 64K and -ub 4096).
  - **D**, the dry run with the output off the GPU, runs beside E on a start whole on the GPU; a gap over 1 MiB is a
    line (a changed build), never the need.
## Auto fit's order

On every Mac (user, 2026-10-09: "every Mac with 48K floor"), Auto fit and a start whose context is not set by you try these setups, in this order:

1. 2 slots × 96K tokens
2. 2 slots × 64K tokens
3. 2 slots × 48K tokens
4. 1 slot × 96K tokens
5. 1 slot × 64K tokens
6. 1 slot × 48K tokens

- **On a PC with an NVIDIA GPU (CUDA) the order starts at 128K** (decision A53, 2026-10-29: "Agreed on 128k proposal"): 2 slots × 128K, 2 × 96K, 2 × 64K, 2 × 48K, then 1 slot × 128K, 1 × 96K, 1 × 64K, 1 × 48K. The cache stays q4_0. Each setup is checked against the VRAM that is free when the start runs, so a busy desktop costs the setup and nothing else: the start says which setup it took and why (`... the first setup in Auto fit's order that fits: 2 slots × 128K tokens do not fit`). A Mac and the CPU keep the six setups above; on a Mac, 128K is for [Auto-tune](server.md) to find on a Mac with a large RAM. The measurements are WCTX's on the RTX 2070 SUPER (8 GiB), the A3B IQ3 with its experts in the RAM: 2 × 128K needs 34 to 37 expert layers in the RAM beside a quiet desktop and 39 to 40 beside a busy one (`-ub` 512 or 1024), decodes the same as 2 × 96K within the noise (0 to 6% slower at the same `-ub`), and with a busy desktop cannot use `-ub` 2048 (the prompt read falls to 947 tok/s at 1024 or 643 at 512, from 1281 for 2 × 96K at 2048). The `-ub` on each setup follows the order of [Batch size](#a-pc-with-a-discrete-gpu): the context comes first.
- Nothing below 48K. A model that fits none of them does not fit.
- **A model with a smaller catalogue window** (`saluki-27b`: 48K, sized for 16 GB Macs) tries each setup at its own window: 2 slots × 48K is its first setup, so it competes with the others' 2 × 96K on quality rank.
- **MTP goes first.** When a setup fits only without MTP, Auto fit uses n-gram only (MTP + n-gram keeps its n-gram part) before it removes a slot or makes the context smaller. `carl fit`, the Server panel and the start lines say so.
- **The speculation it starts from** (Phase 23.5.1.8): this Mac's Auto-tune result for the model, else the catalogue's. Before MTP goes, Auto fit tries the other MTP modes measured for the model, best score first: this Mac's Auto-tune results, else the catalogue's table (`tune.speeds`: each measured mode's prose, code and edit speed, its score, the Mac, the llama.cpp version and the date). MTP's memory hardly depends on its guesses, so this seldom changes the result.
- A start never gets a larger context than the model's own (its catalogue or header context). A context from your settings or from Auto-tune is not changed: then the launcher takes 2 slots when they fit, MTP goes before the second slot, and the check refuses a start that does not fit.
- The memory that a model can use: the GPU memory limit, or the RAM minus the memory kept free, whichever is smaller (the launcher's slots too).

## Context length: what a larger context costs

- The default is **96K tokens for each slot**, for all models. (Auto fit starts at 128K on a PC with an NVIDIA GPU when 128K fits: [Auto fit's order](#auto-fits-order).)
- **96K is the general floor:** users need that much context to work. The catalogue, the custom-model defaults and Auto-tune never select less if 96K fits. When it does not fit, Auto fit's order goes down to 48K ([Auto fit's order](#auto-fits-order)).
- The Server panel warns (a ⚠ sentence under the table) only about a context in the very slow zone. A context of 96K or less never gets a warning.
- You can select a larger context. A larger context works. But each cold read of a long session is slower, and each new token is slower.

The table shows measurements on the 35B (q4 context memory, 2 slots, M3 Pro 36 GB, llama.cpp 0.4.1, the long-context haystack test, 2026-10-01):

| Session size | Read speed, cold | Time to read it all | Write speed (prose) | Write speed (edit) | Recall (8 needles) |
|---|---|---|---|---|---|
| ~64K | 229 tok/s | ~4.8 min | 20.0 tok/s | 46.3 tok/s | 8/8 |
| ~128K | 117 tok/s | ~19 min | 13.9 tok/s | 33.1 tok/s | 8/8 |
| ~150K | 97 tok/s | ~27 min | 12.1 tok/s | 17.0 tok/s | 8/8 |

- **Cold reads occur after a restart or an eviction.** Follow-up turns use the cache again and read only the new tokens. At these depths, 434–477 new tokens took 4–11 s.

To use a larger context:
1. Start the server with `carl start --ctx 128k` (or `160k`).
2. Run the setup again (`carl install --config-only`, or `./setup` on other computers), so that the client limit agrees.

- On the 35B, the additional context memory is small: 5.6 KiB for each token at q4 (2 × 160K = 1.75 GiB).
- On the 27B, it is 18 KiB for each token (2 × 160K = 5.6 GiB).
- **All slots always get the same context.** A shared pool smaller than slots × context was tested and rejected. Two long sessions used more than the pool. Then **both requests failed** with HTTP 500 "Context size has been exceeded" after minutes of work (40K/64K test pool, two ~36K prompts). llama.cpp does not shorten the context to continue.
- **Memory on 36 GB:** the 35B (21.1 GiB of weights) and a VM with 4 GiB leave little memory for macOS. Swap reached 3–6 GiB during the 128K and 150K runs. Look at the memory in the dashboard.

## Context memory (KV cache) and recurrent state

The context memory is llama.cpp's KV cache: for each token of the context, the keys and values of the attention layers. Qwen3.8 (`qwen35`) and Qwen3.6-35B-A3B (`qwen35moe`) are **hybrid** models:
- **Only each 4th layer is a full-attention layer** with a KV cache (`full_attention_interval = 4`).
- The other three layers are Gated-DeltaNet layers. These layers have a recurrent state of fixed size, which does not increase with the context.
- **The MTP head is one more attention layer** (`nextn_predict_layers = 1`).

**Formulas** (from the GGUF metadata; the dashboard uses the same formulas):
- **KV bytes for each token** = attention layers × KV heads × (key length + value length) × bytes for each element.
- When the GGUF gives the KV heads for each layer (Gemma 4), the formula uses the sum of the heads of the layers.
- Bytes for each element: f16 2, q8_0 34/32, q4_0 18/32 (blocks of 32 values).
- **The type on the CPU alone** (decision A71, `src/carl/domain/memory/cpu_kv.py`): the default q4_0 saves memory that a CPU start does not lack, and costs prompt-reading speed there (llama-bench at 4K tokens: f16 267 tok/s, q8_0 103, q4_0 81). A start on the CPU alone that nobody gave a type takes the first of f16, q8_0, q4_0 for which `need + load reserve <= free RAM`, the need being what the server holds in the RAM under load mode none (`FitService.load_need`: the whole start) for the start's own slots and window, which do not change; f16 is 3.5 times q4_0 per token (2 against 18/32 bytes per element). The free RAM is the OS's (the load mode's test), or the RAM less the reserve when the OS does not say; a type the memory check would refuse is skipped. Router mode's presets take the same rule for each model (decision A72). A tune record that measured the types holds for starts on the CPU alone only, and says the llama.cpp build and the backend it was measured on; a start on another build or backend keeps its settings and calls the result an estimate (`src/carl/domain/tune/provenance.py`).
- **Recurrent state** = recurrent layers × [heads × 128 × 128 × 4 B (f32 state) + 3 × (inner size + 2 × groups × 128) × 4 B (conv state)].

| | Qwen3.8-27B (`qwen35`) | Qwen3.6-35B-A3B (`qwen35moe`) |
|---|---|---|
| Layers | 65 = 16 attention + 48 recurrent + 1 MTP | 41 = 10 attention + 30 recurrent + 1 MTP |
| KV heads × (K + V) length | 4 × (256 + 256) | 2 × (256 + 256) |
| **KV for each token** q4_0 / q8_0 / f16 | 18 KiB / 34 KiB / 64 KiB | 5.6 KiB / 10.6 KiB / 20 KiB |
| KV at 96K, q4_0 | 1.69 GiB | 540 MiB |
| **KV at 128K** q4_0 / q8_0 | **2.25 GiB** / 4.25 GiB | **720 MiB** / 1.33 GiB |
| KV at 256K, q4_0 | 4.5 GiB | 1.41 GiB |
| Recurrent state (for each sequence) | ~150 MiB | ~63 MiB |
| Context checkpoints (up to 8) | ≤ 8 × ~150 MiB = 1.2 GiB | ≤ 8 × ~63 MiB = 0.5 GiB |
| RAM cache (`--cache-ram`, auto at 2 × 96K, M3 Pro 36 GB) | q4_0 4864 MiB (~276K tokens), q8_0 1792 MiB (~54K tokens) | q4_0 2560 MiB (~466K tokens), q8_0 1792 MiB (~173K tokens) |

**Cross-checks:**
- The 2.25 and 4.25 GiB of the 27B at 128K agree with earlier measurements.
- The ~150 MiB recurrent state agrees with the ~150 MiB checkpoints that llama.cpp reported.
- The KV of the MTP head: llama.cpp 0.6 allocates it in the draft context, in f16, for the full context (measured: 256 MiB at 64K on the 27B; [The estimate](#the-estimate-what-is-counted-and-the-measured-check)).

**Results:**
- **The server allocates the full context memory at start.** "In use" is only the part that contains data.
- **35B:** the context memory is small. At 128K, `--kv q8` costs approximately 0.6 GiB more. A 256K q4 context costs approximately 0.7 GiB more than 128K. With q8 at 96K, the 35B read prompts approximately 4% faster, with the same write speed ([The RAM cache and checkpoints](caching.md#the-ram-cache-and-checkpoints-measured-2026-10-02)).
- **27B:** the context memory is the largest part of the context cost. For this reason, q4 is the default (−2 GiB at 128K against q8).

## The RAM cache

The RAM cache (`--cache-ram`) and its measurements are on the page [Caching](caching.md#llamacpps-own-caches).

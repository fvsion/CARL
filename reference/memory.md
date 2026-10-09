# CARL Reference: Memory: what fits and what context costs

[Index](README.md) · the GPU memory limit, the context length and the context memory.

## GPU memory limit and what fits

macOS sets a maximum for the memory that the GPU can use (the GPU memory limit): the Metal value `recommendedMaxWorkingSetSize`.

| Mac | GPU memory limit |
|---|---|
| M3 Pro, 36 GB (measured) | **28.1 GiB (78%)** |
| M2 Max, 32 GB (measured) | 25.0 GiB (78%) |
| Less than 32 GB (estimate) | About **2/3 of RAM**: ~16 GiB on a 24 GB Mac, ~10.7 GiB on a 16 GB Mac |
| 32 GB or more (estimate) | About **3/4 of RAM** |

- `sudo sysctl iogpu.wired_limit_mb=N` overrides this limit until the next restart of the Mac.
- `./carl.sh fit` (`tools/llama-fit.py`) calculates these values for each model in the catalogue and in the models folder:
  - **Memory needed** = weights (the whole file; an MTP drafter while MTP runs) + context memory (the KV cache: context × slots × bytes per token) + recurrent state for each slot + compute buffers + with MTP the draft context + a margin of 0.75%. See [The estimate](#the-estimate-what-is-counted-and-the-measured-check).
  - The largest context for each slot that keeps the memory needed ≤ the memory that a model can use. That memory is the GPU memory limit, or the RAM minus the memory kept free for macOS and apps (6 GiB; 10 GiB while the VM network is up), whichever is smaller. CARL offers contexts in 4K steps.
- A model with sliding-window layers (Gemma 4) needs these layers at full length with the full cache (`--swa-full`). With the window cache, they need only the window + 512 tokens for each slot ([Sliding-window models](caching.md#sliding-window-models)).
- `./carl.sh fit --ram 24` shows the values for a 24 GB Mac (the estimated limit).

**Sources of the inputs:**

| Input | Source |
|---|---|
| The GPU memory limit | A Swift probe that runs one time (`tools/metal-limit.swift`, cached in `~/models/.metal-limit`). An `iogpu.wired_limit_mb` override has priority. Without Swift, the 2/3 or 3/4 estimate. |
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
- `orcarouter-27b-q3` is the one exception to the 96K window of the catalogue. Its catalogue context is 64K, because 96K does not fit a 24 GB Mac. Larger Macs use the Q4 build.
- The Gemma models (window cache): the E4B and the 12B get 2 × 96K with MTP. The 26B-A4B gets 2 × 64K with n-gram. The 31B does not fit.
- A start of `orcarouter-27b-q3` without an Auto-tune result shows a note: run `./carl.sh tune orcarouter-27b-q3`. Auto-tune selects 96K when it fits.

**16 GB Macs** (~10.7 GiB GPU memory limit, estimate; a model can use 10.0 GiB, because CARL keeps 6 GiB free):
- Auto fit chooses `gemma-4-e4b` (4.6 GB) for the everyday goal: 2 × 96K with MTP need 8.3 GiB with the window cache.
- Auto fit chooses `gemma-4-12b` (7.2 GB) for the hard code goal: 2 × 96K need 8.6 GiB with n-gram and the window cache. With MTP they need 10.2 GiB: MTP does not fit.
- `qwen3.8-9b` (5.8 GB) also fits with 2 × 96K (8.2 GiB with n-gram; 11.1 GiB with MTP). Its quality rank (10) is below the Gemma models.
- Saluki (Phase 23.4.2): the file without the MTP head (7.9 GB) at 2 × 48K needs 9.98 GiB with n-gram (measured: 9.89 GiB). The file with the head (8.35 GB) and MTP needs 12.2 GiB (measured: 12.12 GiB): MTP does not fit.
- No other catalogue model fits under the default limit. The IQ3 27B (10.2 GiB of weights) needs a raised limit (`sudo sysctl iogpu.wired_limit_mb=…`).
- `./carl.sh fit --ram 16` shows the values.

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

Phase 23.4.4 (2026-10-09) replaced the flat 1 GiB of buffers with the parts that llama.cpp 0.6 allocates (`tools/carl_core/domain/fit.py`). **Memory needed** = the sum of these parts + 0.75%:

| Part | Rule |
|---|---|
| Weights | The whole model file. A file with an MTP head counts the head also when MTP is not used. A separate drafter file (Gemma 4) counts only while MTP runs. |
| Context memory (KV) | Attention layers × KV heads × (key + value length) × bytes for each element × context × slots. Sliding-window layers: the window + 512 tokens for each slot, or the full context with the full cache. |
| Recurrent state | For each slot. With MTP: × (1 + guesses). llama.cpp keeps one state for each guess, to go back when a guess is wrong. |
| Compute buffer | KV pool (context × slots) × (the K and V of the widest attention layer in f16 + the batch size `-ub` × 2 bytes of mask) + `-ub` × the widest activation of one token × 8 bytes + 32 MiB. With the full sliding-window cache, a second mask over the pool. |
| MTP draft context | When MTP runs: the KV of the MTP head's layer in f16 at the full pool (a drafter file shares the model's KV: 0), and two more compute buffers of the same size as the main one. |
| Margin | 0.75% of the sum: allocator slack and the error of the rule. |

- **The widest activation** comes from the GGUF header: the FFN length; for an MoE the outputs of the used experts before they are added (embedding length × used experts: 2,048 × 8 on the 35B-A3B); the projections of the recurrent layers.
- **The batch size changes the compute buffer.** On the 27B at 2 × 32K: 0.41 GiB at `-ub 512`, 0.35 GiB at `-ub 256`.
- **Why 0.75%:** below 0.51%, the 35B Q4 with MTP + n-gram at 2 × 96K fits the M2 Max (25.0 GiB), where it paged and failed (below). Above 0.78%, Saluki's 2 × 48K (measured 9.89 GiB) does not fit a 16 GB Mac (10.0 GiB).

**The measured check** (Saluki, `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`, M3 Pro, llama.cpp 0.6.0, KV q4_0, `-lv 4`; `docs/phase-plans/phase23.4.2/results/fit/`): the estimate of KV + recurrent state + compute (+ the draft context) against the buffers in the server logs, in GiB:

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

- **The Gemma 4 E4B** (2 × 64K, `-ub 512`, `docs/phase-plans/phase20/research-gemma4/smoke/`): compute 714.3 MiB measured with the window cache, 718.0 estimated; 840.8 MiB with the full cache, 840.0 estimated.
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

## Auto fit's order

On every Mac (user, 2026-10-09: "every Mac with 48K floor"), Auto fit and a start whose context is not set by you try these setups, in this order:

1. 2 slots × 96K tokens
2. 2 slots × 64K tokens
3. 2 slots × 48K tokens
4. 1 slot × 96K tokens
5. 1 slot × 64K tokens
6. 1 slot × 48K tokens

- Nothing below 48K. A model that fits none of them does not fit.
- **MTP goes first.** When a setup fits only without MTP, Auto fit uses n-gram only (MTP + n-gram keeps its n-gram part) before it removes a slot or makes the context smaller. `./carl.sh fit`, the Server panel and the start lines say so.
- A start never gets a larger context than the model's own (its catalogue or header context). A context from your settings or from Auto-tune is not changed: then the launcher takes 2 slots when they fit, MTP goes before the second slot, and the check refuses a start that does not fit.
- The memory that a model can use: the GPU memory limit, or the RAM minus the memory kept free, whichever is smaller (the launcher's slots too).

## Context length: what a larger context costs

- The default is **96K tokens for each slot**, for all models.
- **96K is the general floor:** users need that much context to work. The catalogue, the custom-model defaults and Auto-tune never select less if 96K fits. When it does not fit, Auto fit's order goes down to 48K ([Auto fit's order](#auto-fits-order)).
- The Server panel warns (a ⚠ sentence under the table) only about a context in the very slow zone. A context of 96K or less never gets a warning.
- You can select a larger context. A larger context works. But each cold read of a long session is slower, and each new token is slower.

The table shows measurements on the 35B (q4 context memory, 2 slots, M3 Pro 36 GB, llama.cpp 0.4.1, `tools/llama-kv-longctx.py`, 2026-10-01):

| Session size | Read speed, cold | Time to read it all | Write speed (prose) | Write speed (edit) | Recall (8 needles) |
|---|---|---|---|---|---|
| ~64K | 229 tok/s | ~4.8 min | 20.0 tok/s | 46.3 tok/s | 8/8 |
| ~128K | 117 tok/s | ~19 min | 13.9 tok/s | 33.1 tok/s | 8/8 |
| ~150K | 97 tok/s | ~27 min | 12.1 tok/s | 17.0 tok/s | 8/8 |

- **Cold reads occur after a restart or an eviction.** Follow-up turns use the cache again and read only the new tokens. At these depths, 434–477 new tokens took 4–11 s.

To use a larger context:
1. Start the server with `./carl.sh llama --ctx 128k` (or `160k`).
2. Run the setup again (`./carl.sh install --config-only`, or `./setup` on other computers), so that the client limit agrees.

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

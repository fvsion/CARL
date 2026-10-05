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
  - **Memory needed** = weights (the file, with an MTP drafter) + context memory (the KV cache: context × slots × bytes per token) + recurrent state for each slot + ~1 GiB for compute buffers and the MTP draft context.
  - The largest context for each slot that keeps the memory needed ≤ the memory that a model can use. That memory is the GPU memory limit, or the RAM minus the memory kept free for macOS and apps (6 GiB; 10 GiB while the VM network is up), whichever is smaller. CARL offers contexts in 4K steps.
- A model with sliding-window layers (Gemma 4) needs these layers at full length with the full cache (`--swa-full`). With the window cache, they need only the window + 512 tokens for each slot ([Sliding-window models](caching.md#sliding-window-models)).
- `./carl.sh fit --ram 24` shows the values for a 24 GB Mac (the estimated limit).

**Sources of the inputs:**

| Input | Source |
|---|---|
| The GPU memory limit | A Swift probe that runs one time (`tools/metal-limit.swift`, cached in `~/models/.metal-limit`). An `iogpu.wired_limit_mb` override has priority. Without Swift, the 2/3 or 3/4 estimate. |
| The model shape | The local GGUF. For a model that is not downloaded, the first 24 MB of the file with an HTTP range request (cached in `~/models/.gguf-shapes.json`). |

**Results** (the largest context for each slot, 1 slot, q4):

| Model | Weights | M3 Pro 36 GB (28.1 GiB) | 24 GB Mac (~16 GiB) | 24 GB, limit raised to 18 GiB |
|---|---|---|---|---|
| `qwen3.8-27b` (Q4) | 15.3 GiB | 256K | does not fit | ~84K |
| `orcarouter-27b` (Q4) | 16.6 GiB | 256K | does not fit | ~16K |
| `qwen3.8-27b-q3` | 12.2 GiB | 256K | ~148K | 256K |
| `orcarouter-27b-q3` | 13.6 GiB | 256K | ~68K | ~184K |
| `qwen3.6-35b-a3b` | 21.1 GiB | 256K | does not fit | does not fit |
| `qwen3.6-35b-a3b-iq3` (24 GB default) | 13.1 GiB | 256K | 256K (2 × 96K slots: ~15.3 GiB) | 256K |
| `qwen3.8-27b-iq3` | 10.2 GiB | 256K | 256K (2 slots: ~128K each) | 256K |

- **On a 24 GB Mac, the Q3 27B entries get only 1 slot.** Thus, the setup does not install the coder subagent there (it needs 2 or more slots).
- The IQ3 35B and the IQ3 27B get 2 × 96K slots on a 24 GB Mac.
- `orcarouter-27b-q3` is the one exception to the 96K minimum. Its catalogue context is 64K, because 96K does not fit a 24 GB Mac (~68K, estimate). Larger Macs use the Q4 build.
- The Gemma models on a 24 GB Mac (window cache): the E4B and the 12B get 2 × 96K slots. The 26B-A4B gets 1 slot (up to ~192K): its 2 × 96K need 16.1 GiB. The 31B does not fit.
- A start of `orcarouter-27b-q3` without an Auto-tune result shows a note: run `./carl.sh tune orcarouter-27b-q3`. Auto-tune selects 96K when it fits.

**16 GB Macs** (~10.7 GiB GPU memory limit, estimate; a model can use 10.0 GiB, because CARL keeps 6 GiB free):
- Auto fit chooses `gemma-4-e4b` (4.6 GB) for the everyday goal: 2 × 96K need 6.2 GiB with the window cache.
- Auto fit chooses `gemma-4-12b` (7.2 GB) for the hard code goal: 2 × 96K need 9.1 GiB with the window cache.
- `qwen3.8-9b` (5.8 GB) also fits with 2 × 96K (8.2 GiB). Its quality rank (10) is below the Gemma models.
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
- The ~1 GiB buffer allowance is a conservative estimate. On the 35B, the dashboard measured ~0.5 GiB of "other" memory.
- The fit check does not include the RAM that macOS and its apps need.
- CAUTION: Keep ≥ 6 GiB free. Look at the memory pressure in the dashboard (the MEMORY card of the Live tab).

## Context length: what a larger context costs

- The default is **96K tokens for each slot**, for all models.
- **96K is the minimum:** users need that much context to work. The catalogue, the custom-model defaults and Auto-tune never select less if 96K fits.
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
- The KV of the MTP head (one more layer, ~1/16 of the KV of the 27B) is an estimate. It is not known if llama.cpp allocates it for the full context.

**Results:**
- **The server allocates the full context memory at start.** "In use" is only the part that contains data.
- **35B:** the context memory is small. At 128K, `--kv q8` costs approximately 0.6 GiB more. A 256K q4 context costs approximately 0.7 GiB more than 128K. With q8 at 96K, the 35B read prompts approximately 4% faster, with the same write speed ([The RAM cache and checkpoints](caching.md#the-ram-cache-and-checkpoints-measured-2026-10-02)).
- **27B:** the context memory is the largest part of the context cost. For this reason, q4 is the default (−2 GiB at 128K against q8).

## The RAM cache

The RAM cache (`--cache-ram`) and its measurements are on the page [Caching](caching.md#llamacpps-own-caches).

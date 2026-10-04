# CARL Reference: Memory: what fits and what context costs

[Index](../REFERENCE.md) · the GPU limit, context length and context memory, the RAM prompt cache.

## GPU memory limit and what fits

macOS sets a maximum for the memory that the GPU can use: the Metal value `recommendedMaxWorkingSetSize`.

| Mac | GPU limit |
|---|---|
| M3 Pro, 36 GB (measured) | **28.1 GiB (78%)** |
| M2 Max, 32 GB (measured) | 25.0 GiB (78%) |
| Less than 32 GB (estimate) | About **2/3 of RAM**: ~16 GiB on a 24 GB Mac, ~10.7 GiB on a 16 GB Mac |
| 32 GB or more (estimate) | About **3/4 of RAM** |

- `sudo sysctl iogpu.wired_limit_mb=N` overrides this limit until the next restart of the Mac.
- `./carl.sh fit` (`tools/llama-fit.py`) calculates these values for each model in the catalogue and in the models folder:
  - **Need** = weights (file size) + KV cache (window × slots × bytes per token) + recurrent state for each slot + ~1 GiB for compute buffers and the MTP draft context.
  - The largest window for each slot that keeps the need ≤ the limit. CARL offers windows in 4K steps.
- A model with sliding-window layers (Gemma 4) needs these layers at full length when the server runs with `--swa-full`. Without it, they need only the window + 512 tokens for each slot ([Sliding-window models](cache.md#sliding-window-models)).
- `./carl.sh fit --ram 24` shows the values for a 24 GB Mac (the estimated limit).

**Sources of the inputs:**

| Input | Source |
|---|---|
| The limit | A Swift probe that runs one time (`tools/metal-limit.swift`, cached in `~/models/.metal-limit`). An `iogpu.wired_limit_mb` override has priority. Without Swift, the 2/3 or 3/4 estimate. |
| The model shape | The local GGUF. For a model that is not downloaded, the first 24 MB of the file with an HTTP range request (cached in `~/models/.gguf-shapes.json`). |

**Results** (largest window for each slot, 1 slot):

| Model | Weights | M3 Pro 36 GB (28.1 GiB) | 24 GB Mac (~16 GiB) | 24 GB, limit raised to 18 GiB |
|---|---|---|---|---|
| `qwen3.8-27b` (Q4) | 15.3 GiB | 256K | does not fit | ~84K |
| `orcarouter-27b` (Q4) | 16.6 GiB | 256K | does not fit | ~16K |
| `qwen3.8-27b-q3` | 12.2 GiB | 256K | ~148K | 256K |
| `orcarouter-27b-q3` | 13.6 GiB | 256K | ~68K | ~184K |
| `qwen3.6-35b-a3b` | 21.1 GiB | 256K | does not fit | does not fit |
| `qwen3.6-35b-a3b-iq3` (24 GB default) | 13.1 GiB | 256K | 256K (2 × 96K slots: ~15.3 GiB) | 256K |
| `qwen3.8-27b-iq3` | 10.2 GiB | 256K | 256K (2 slots: ~128K each) | 256K |

- **On a 24 GB Mac, the Q3 27B entries get only 1 slot.** Thus, `install.sh` does not install the coder subagent there (it needs 2 or more slots).
- The IQ3 35B and the IQ3 27B get 2 × 96K slots on a 24 GB Mac.
- `orcarouter-27b-q3` is the one exception to the 96K minimum. Its catalogue window is 64K, because 96K does not fit a 24 GB Mac (~68K, estimate). Larger Macs use the Q4 build.
- A start of `orcarouter-27b-q3` without an Auto-tune result shows a note: run `./carl.sh tune orcarouter-27b-q3`. Auto-tune selects 96K when it fits.

**16 GB Macs** (~10.7 GiB limit, estimate; 10.0 GiB allowed with the 6 GiB reserve):
- `qwen3.8-9b` (5.8 GB) fits with 2 × 96K (8.2 GiB). It is auto fit's pick for both goals.
- No other catalogue model fits under the default limit.
- The IQ3 27B (10.2 GiB of weights) needs a raised limit (`sudo sysctl iogpu.wired_limit_mb=…`).
- `./carl.sh fit --ram 16` shows the values.

**The hybrid Qwen3.6 and Qwen3.8 models on Metal** (measured 2026-10-03, M2 Max 32 GB, llama.cpp 0.5.0):
- `llama-bench` on the 9B (5.4 GiB, `qwen35`: 8 attention + 24 recurrent layers) gives pp512 464 tok/s and tg128 24.4 tok/s (25.4 with q4_0 KV). CARL's flags cost nothing.
- The recurrent layers keep the decode of dense Qwen3.8 far below what the memory bandwidth allows. This is also true for the 27B.
- The 35B-A3B MoE reads 3B parameters for each token: ~46 tok/s.
- A short, predictable answer ("count to 20") decodes at ~47 tok/s on the 9B, because the MTP drafts are 90% accepted. Prose and code do not.

**Parallel requests** (`llama-batched-bench`, 1K-token prompts, 128 tokens generated, q4_0 KV, total decode tok/s):

| Model | 1 at once | 2 | 4 | 8 |
|---|---|---|---|---|
| 9B | 25.5 | 29.2 | 40.6 | 45.0 |
| 35B-A3B IQ3 | 45.7 | 50.4 | 65.7 | 69.8 |

- The total prompt read speed decreases with parallel requests: 9B 481 → ~290 tok/s, 35B 599 → ~510 tok/s.
- CARL allows 4 slots at most, because each request becomes slower as more run at the same time. CARL offers 3 and 4 slots only where they fit.
- The parallel step of Auto-tune measures 1–4 requests for each model.

**Limits of the calculation:**
- The ~1 GiB buffer allowance is a conservative estimate. On the 35B, the dashboard measured ~0.5 GiB of "other" memory.
- The fit check does not include the RAM that macOS and its apps need.
- CAUTION: Keep ≥ 6 GB free. Monitor the memory pressure in the dashboard.

## Context length: what longer windows cost

- The default is **96K tokens for each slot**, for all models.
- **96K is the minimum:** users need that much context to work. The catalogue, the custom-model defaults and Auto-tune never select less if 96K fits.
- The Settings tab shows a warning colour (yellow, red) only for windows above 96K.
- You can select a larger window. Longer windows work. But each cold read of a long session is slower, and each new token is slower.

The table shows measurements on the 35B (q4_0 KV, 2 slots, M3 Pro 36 GB, `tools/llama-kv-longctx.py`, 2026-10-01):

| Session size | Cold prompt read | Time to read it all | Decode (prose) | Decode (re-emit) | Recall (8 needles) |
|---|---|---|---|---|---|
| ~64K | 229 tok/s | ~4.8 min | 20.0 tok/s | 46.3 tok/s | 8/8 |
| ~128K | 117 tok/s | ~19 min | 13.9 tok/s | 33.1 tok/s | 8/8 |
| ~150K | 97 tok/s | ~27 min | 12.1 tok/s | 17.0 tok/s | 8/8 |

- **Cold reads occur after a restart or an eviction.** Follow-up turns use the cache again and read only the new tokens. At these depths, 434–477 new tokens took 4–11 s.

To use a longer window:
1. Start the server with `./carl.sh llama --ctx 128k` (or `160k`).
2. Run `client/install.sh` again, so that the client limit agrees.

- On the 35B, the additional KV is small: 5.6 KiB for each token at q4 (2 × 160K = 1.75 GiB).
- On the 27B, it is 18 KiB for each token (2 × 160K = 5.6 GiB).
- **All slots always get the same window.** A shared pool smaller than slots × window was tested and rejected. Two long sessions used more than the pool. Then **both requests failed** with HTTP 500 "Context size has been exceeded" after minutes of work (40K/64K test pool, two ~36K prompts). llama.cpp does not shorten the context to continue.
- **Memory on 36 GB:** the 35B (21 GB of weights) and a 4 GB VM leave little memory for macOS. Swap reached 3–6 GB during the 128K and 150K runs. Monitor the memory in the dashboard.

## Context memory: KV cache and recurrent state

Qwen3.8 (`qwen35`) and Qwen3.6-35B-A3B (`qwen35moe`) are **hybrid** models:
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
| Prompt cache (`--cache-ram`, auto at 2 × 96K, M3 Pro) | q4_0 4864 MiB (~276K tokens), q8_0 1792 MiB (~54K tokens) | q4_0 2560 MiB (~466K tokens), q8_0 1792 MiB (~173K tokens) |

**Cross-checks:**
- The 2.25 and 4.25 GiB of the 27B at 128K agree with earlier measurements.
- The ~150 MiB recurrent state agrees with the ~150 MiB checkpoints that llama.cpp reported.
- The KV of the MTP head (one more layer, ~1/16 of the KV of the 27B) is an estimate. It is not known if llama.cpp allocates it for the full window.

**Results:**
- **The server allocates the full KV cache at start.** "In use" is only the part that contains data.
- **35B:** the KV cache costs little memory. At 128K, `--kv q8` costs approximately 0.6 GiB more. A 256K q4 window costs approximately 0.7 GiB more than 128K. With q8 at 96K, the 35B read prompts approximately 4% faster, with the same decode speed (see the next section).
- **27B:** the KV cache is the largest part of the context cost. For this reason, q4_0 is the default (−2 GiB at 128K against q8_0).

## RAM prompt cache and checkpoints (measured 2026-10-02)

- The server has 2 slots by default. When a third conversation starts, it takes the slot of the conversation that was used least recently.
- The server keeps the removed prompt in the **RAM prompt cache** (`--cache-ram`).
- When that conversation continues, the server copies the prompt back from RAM. It does not read the prompt again.
- **The launcher sets the cache size.** The size is the free memory after the model and a reserve for macOS (10 GiB when the VMware network is up, else 6 GiB). The limits are 1024 MiB and 8192 MiB.
- **Test:** the 35B, 2 × 96K, M3 Pro. Three conversations of ~20K tokens each, then one follow-up in each conversation. Then a turn with thinking, and the turn after it.

| Configuration | Cold read of each prompt | Follow-up after eviction | Turn after a thinking turn | Swap |
|---|---|---|---|---|
| Default: cache 2560 MiB, 8 checkpoints at least 4096 tokens apart | 44–47 s (~450 tok/s) | **0.7–1.2 s** (22 tokens read) | 26 tokens read | 0.90 GB |
| `--cache-ram 0` | the same | **40–45 s** (~19K tokens read again) | 26 tokens read | 0.90 GB |
| 16 checkpoints at least 1024 tokens apart | the same | 0.7–1.1 s | 26 tokens read | 0.90 GB |
| `--kv q8` (cache 1792 MiB) | 42–45 s (~470 tok/s) | 0.8–1.2 s | 26 tokens read | 1.26 GB |

**Results:**
- **The RAM prompt cache is necessary.** Without it, the server reads a removed conversation again in full: 40–45 s for 20K tokens, and minutes for a long session.
- **A larger cache gives no gain on the 35B.** 2560 MiB holds approximately 466K tokens of q4_0 KV. This is almost five full 96K windows.
- **More checkpoints give no gain.** Each turn adds to the end of the conversation. The server keeps earlier reasoning (`preserve_thinking`). Thus, the cached prompt stays the start of the new prompt, and the server reads only the new tokens.
- **q8_0 fits at 96K on 36 GB.** On the 35B, it reads approximately 4% faster, with the same decode speed and 0.36 GB more swap. q4_0 stays the default, because it uses less memory and has the recall tests.
- NOTE: **The 27B with `--kv q8` has a small cache.** The automatic size is 1792 MiB. At 34 KiB for each token, this holds only ~54K tokens. Thus, the server reads a removed long 27B conversation again in full. With q4_0, the 27B gets 4864 MiB (~276K tokens), which is sufficient.
- The RAM cache is lost when the server stops or when router mode changes the model. The disk prompt cache of OpenCode and Pi covers these cases ([The disk prompt cache](cache.md)).

# CARL Reference: Memory: what fits and what context costs

[Index](../REFERENCE.md) · the GPU limit, context length and context memory, the RAM prompt cache.

## GPU memory limit and what fits

- **macOS sets a maximum for the memory that the GPU can use:** the Metal value `recommendedMaxWorkingSetSize`.
  - On this M3 Pro 36 GB, it is **28.1 GiB (78%)**.
  - Macs below 32 GB get approximately **2/3 of RAM**: ~16 GiB on a 24 GB Mac (estimate; not measured here). Macs with 32 GB or more get approximately **3/4** (a 32 GB M2 Max reports 25.0 GiB, 78%).
  - `sudo sysctl iogpu.wired_limit_mb=N` overrides this limit until the next reboot.
- **`./carl.sh fit`** (`tools/llama-fit.py`) calculates these values for each model (the catalogue and the models folder):
  - **Need** = weights (file size) + KV cache (window × bytes per token) + recurrent state + ~1 GiB for compute buffers and the MTP draft context.
  - The largest context window for each KV type that keeps need ≤ limit.
- **Sources of the inputs:**
  - **The limit:** a Swift probe that runs one time (`tools/metal-limit.swift`, cached in `~/models/.metal-limit`). An `iogpu.wired_limit_mb` override has priority. Without Swift, the script uses the 2/3–3/4 estimate.
  - **Model shapes:** the local GGUF. For models that are not downloaded yet, the script gets the first 24 MB with an HTTP range request (cached in `~/models/.gguf-shapes.json`).
- **Results:**

| Model | Weights | This Mac (28.1 GiB) | 24 GB Mac (~16 GiB) | 24 GB, limit raised to 18 GiB |
|---|---|---|---|---|
| `qwen3.8-27b` (Q4) | 15.3 GiB | 256K | does not fit | ~84K |
| `orcarouter-27b` (Q4) | 16.6 GiB | 256K | does not fit | ~16K |
| `qwen3.8-27b-q3` | 12.2 GiB | 256K | ~148K | 256K |
| `orcarouter-27b-q3` | 13.6 GiB | 256K | ~68K | ~184K |
| `qwen3.6-35b-a3b` | 21.1 GiB | 256K | does not fit | does not fit |
| `qwen3.6-35b-a3b-iq3` (24 GB default) | 13.1 GiB | 256K | 256K (2 × 96K slots: ~15.3 GiB) | 256K |
| `qwen3.8-27b-iq3` | 10.2 GiB | 256K | 256K (2 slots: ~128K each) | 256K |

- **On a 24 GB Mac, the Q3 27B entries get only 1 slot.** Thus, `install.sh` does not install the coder subagent there. The IQ3 35B gets 2 × 96K slots. The IQ3 27B gets 2 × 96K slots (the default window). `orcarouter-27b-q3` is the one exception to the 96K floor: its catalogue window is 64K, because 96K does not fit a 24 GB Mac (~68K, estimate) and larger Macs use the Q4 build. An untuned start prints a note to run `./carl.sh tune orcarouter-27b-q3`; Auto-tune selects 96K wherever it fits.
- **16 GB Macs (~10.7 GiB limit, estimate; 10.0 GiB allowed with the 6 GiB reserve):** `qwen3.8-9b` (5.8 GB) fits with 2 × 96K (8.2 GiB) and is auto fit's pick for both goals; nothing else in the catalogue fits under the default limit. The IQ3 27B (10.2 GiB of weights) needs a raised limit (`sudo sysctl iogpu.wired_limit_mb=…`). `./carl.sh fit --ram 16` shows the numbers.
- **The hybrid Qwen3.6/3.8 models on Metal (measured 2026-10-03, M2 Max 32 GB, llama.cpp 0.5.0):** `llama-bench` on the 9B (5.4 GiB, `qwen35`: 8 attention + 24 recurrent layers) gives pp512 464 tok/s, tg128 24.4 (25.4 with q4_0 KV): CARL's flags cost nothing; the recurrent layers make dense Qwen3.8 decode far below what memory bandwidth allows (the 27B too). The 35B-A3B MoE reads 3B parameters per token: ~46 tok/s. A short, predictable answer ("count to 20") decodes ~47 tok/s on the 9B because MTP drafts are 90% accepted; prose and code don't.
- **Parallel requests** (`llama-batched-bench`, 1K-token prompts, 128 generated, q4_0 KV, total decode tok/s for 1 / 2 / 4 / 8 at once): 9B 25.5 / 29.2 / 40.6 / 45.0; 35B-A3B IQ3 45.7 / 50.4 / 65.7 / 69.8. Prompt reading in total drops with parallel requests (9B 481 → ~290; 35B 599 → ~510). CARL allows 4 slots at most (each request slows as more run at once), offered only where they fit; Auto-tune's parallel step measures 1-4 per model.
- **The ~1 GiB buffer allowance is a conservative estimate.** On the 35B, the monitor measured ~0.5 GiB of "other" memory.
- **The fit check does not include** the RAM that the rest of macOS and its apps need.
  - CAUTION: Keep ≥ 6 GB free. Monitor the pressure line on the SYSTEM card.

## Context length: what longer windows cost

The default is **96K tokens per slot** (both slots), for all models. **96K is a floor:** users need that much context to work. The catalogue, the custom-model defaults and Auto-tune never select less if 96K fits. The Settings tab warns (yellow, red) only about windows above 96K. You can still select a larger window. Longer context windows work. But each cold read of a long session is slower, and each new token is slower. The table shows measurements on the 35B (q4_0 KV, 2 slots, M3 Pro 36 GB, `tools/llama-kv-longctx.py`, 2026-10-01):

| Session size | Cold prompt read | Time to read it all | Decode (prose) | Decode (re-emit) | Recall (8 needles) |
|---|---|---|---|---|---|
| ~64K | 229 tok/s | ~4.8 min | 20.0 tok/s | 46.3 tok/s | 8/8 |
| ~128K | 117 tok/s | ~19 min | 13.9 tok/s | 33.1 tok/s | 8/8 |
| ~150K | 97 tok/s | ~27 min | 12.1 tok/s | 17.0 tok/s | 8/8 |

- **Cold reads are important after a restart or an eviction.** Follow-up turns use the cache again and read only the new tokens. At these depths, 434–477 new tokens took 4–11 s.
- **To use a longer context window:**
  1. Start the server with `./carl.sh llama --ctx 128k` (or `160k`).
  2. Run `client/install.sh` again, so that the client limit matches.
  - On the 35B, the additional KV is small (5.6 KiB/token at q4: 2 × 160K = 1.75 GiB).
  - On the 27B, it is 18 KiB/token (2 × 160K = 5.6 GiB).
- **Both slots always get the same context window.** A shared pool smaller than slots × window was tested and rejected. Two long sessions used more than the pool. Then **both requests failed** with HTTP 500 "Context size has been exceeded" after minutes of work (40K/64K test pool, two ~36K prompts). There is no graceful truncation.
- **Memory on 36 GB:** The 35B (21 GB weights) and a 4 GB VM leave little memory for macOS. Swap reached 3–6 GB during the 128K and 150K runs. Monitor the SYSTEM card of the monitor.

## Context memory: KV cache and recurrent state

Qwen3.8 (`qwen35`) and Qwen3.6-35B-A3B (`qwen35moe`) are **hybrid** models.
- **Only every 4th layer is a full-attention layer** with a KV cache (`full_attention_interval = 4`). The other three layers are Gated-DeltaNet layers. These layers have a recurrent state of fixed size, which does not grow with the context.
- **The MTP head is one more attention layer** (`nextn_predict_layers = 1`).

**Formulas** (from the GGUF metadata; the monitor uses the same formulas):
- **KV bytes per token** = attention layers × KV heads × (key length + value length) × bytes per element.
  - Bytes per element: f16 2, q8_0 34/32, q4_0 18/32 (32-value blocks).
- **Recurrent state** = recurrent layers × [heads × 128 × 128 × 4 B (f32 state) + 3 × (inner size + 2 × groups × 128) × 4 B (conv state)].

| | Qwen3.8-27B (`qwen35`) | Qwen3.6-35B-A3B (`qwen35moe`) |
|---|---|---|
| Layers | 65 = 16 attention + 48 recurrent + 1 MTP | 41 = 10 attention + 30 recurrent + 1 MTP |
| KV heads × (K + V) length | 4 × (256 + 256) | 2 × (256 + 256) |
| **KV per token** q4_0 / q8_0 / f16 | 18 KiB / 34 KiB / 64 KiB | 5.6 KiB / 10.6 KiB / 20 KiB |
| KV at 96K, q4_0 | 1.69 GiB | 540 MiB |
| **KV at 128K** q4_0 / q8_0 | **2.25 GiB** / 4.25 GiB | **720 MiB** / 1.33 GiB |
| KV at 256K, q4_0 | 4.5 GiB | 1.41 GiB |
| Recurrent state (per sequence) | ~150 MiB | ~63 MiB |
| Context checkpoints (up to 8) | ≤ 8 × ~150 MiB = 1.2 GiB | ≤ 8 × ~63 MiB = 0.5 GiB |
| Prompt cache (`--cache-ram`, auto at 96K × 2 slots) | q4_0 4864 MiB (~276K tokens), q8_0 1792 MiB (~54K tokens) | q4_0 2560 MiB (~466K tokens), q8_0 1792 MiB (~173K tokens) |

- **Cross-checks:**
  - The 2.25 / 4.25 GiB of the 27B at 128K agree with the values that were measured before.
  - The ~150 MiB recurrent state agrees with the ~150 MiB checkpoints that llama.cpp reported.
  - The KV of the MTP head (one more layer, ~1/16 of the KV of the 27B) is an estimate. It was not checked if llama.cpp allocates it for the full window.
- **The server allocates the full KV cache at start.** "In use" is only the part of it that contains data.
- **Result for the 35B:** The KV cache costs little memory. At 128K, `--kv q8` costs approximately 0.6 GiB more. A 256K q4 window costs approximately 0.7 GiB more than 128K. With q8 at 96K, the 35B read prompts approximately 4% faster, and the decode speed did not change (see the next section).
- **Result for the 27B:** The KV cache is the largest part of the context cost. For this reason, q4_0 was selected (−2 GiB at 128K vs q8_0).

## RAM prompt cache and checkpoints (measured 2026-10-02)

The server has 2 slots. When a third conversation starts, it takes the slot of the conversation that was used least recently. The server keeps the evicted prompt in the **RAM prompt cache** (`--cache-ram`). When that conversation continues, the server copies the prompt back from RAM. It does not read the prompt again.

- **The launcher sets the cache size automatically.** The size is the free memory after the weights, the KV cache and a reserve for macOS (10 GB when the VMware network is up, otherwise 6 GB). The limits are 1024 MiB and 8192 MiB.
- **Test:** the 35B, 2 × 96K, three conversations of ~20K tokens each, then one follow-up in each conversation. Then a turn with thinking, and the turn after it.

| Configuration | Cold read of each prompt | Follow-up after eviction | Turn after a thinking turn | Swap |
|---|---|---|---|---|
| Default: cache 2560 MiB, 8 checkpoints every ≥4096 tokens | 44–47 s (~450 tok/s) | **0.7–1.2 s** (22 tokens read) | 26 tokens read | 0.90 GB |
| `--cache-ram 0` | the same | **40–45 s** (~19K tokens read again) | 26 tokens read | 0.90 GB |
| 16 checkpoints every ≥1024 tokens | the same | 0.7–1.1 s | 26 tokens read | 0.90 GB |
| `--kv q8` (cache 1792 MiB) | 42–45 s (~470 tok/s) | 0.8–1.2 s | 26 tokens read | 1.26 GB |

**Results:**
- **The RAM prompt cache is necessary.** Without it, a conversation that comes back after an eviction is read again in full: 40–45 s for 20K tokens, and minutes for a long session.
- **A larger cache gives no gain on the 35B.** 2560 MiB holds approximately 466K tokens of q4_0 KV. This is almost five full 96K windows.
- **More checkpoints give no gain.** Each turn adds to the end of the conversation. The server keeps earlier reasoning (`preserve_thinking`). Thus, the cached prompt stays the start of the new prompt, and the server reads only the new tokens.
- **q8_0 fits at 96K on 36 GB.** On the 35B, it reads approximately 4% faster, with the same decode speed and 0.36 GB more swap. q4_0 stays the default, because it uses less memory and has the recall tests.
- NOTE: **The 27B with `--kv q8` has a small cache.** The automatic size is 1792 MiB. At 34 KiB for each token, this holds only ~54K tokens. Thus, the server reads an evicted long 27B conversation again in full. With q4_0, the 27B gets 4864 MiB (~276K tokens), which is sufficient.

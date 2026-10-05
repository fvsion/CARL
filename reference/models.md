# CARL Reference: Models and quantization

[Index](README.md) · the catalogue models and why, IQ quants, the coder subagent.

## Model and quantization choices

### Why these models

The catalogue is `host/catalog.json`. Each model is served under its CARL name (the first column).

| Model | Build, size | Why it is here |
|---|---|---|
| `qwen3.6-35b-a3b` (**default**) | unsloth Qwen3.6-35B-A3B UD-Q4_K_M (MTP build), 22.7 GB | Stock MoE with 3B active. About 4× the write speed and 6× the read speed of the 27B (M3 Pro 36 GB, llama.cpp 0.4.1, 2026-09-25). Its context memory is very small, so long sessions and 2 slots cost little. It is the default since 2026-10-01 (the dense 27B is slow for general use). |
| `qwen3.8-27b` | unsloth Qwen3.8-27B UD-Q4_K_M, 16.5 GB | Stock (not abliterated) dense 27B. It is the smallest Q4 build of the 27B, so it fits on more Macs. It has the same architecture and speed as the measured orcarouter build. |
| `orcarouter-27b` | orcarouter Qwen3.8-27B Q4_K_M (bartowski), 17.8 GB | Abliterated, with published values (0–2.7% refusals, MMLU +0.4). Its imatrix was calibrated with many tool calls. Q4_K_M is the nearest GGUF to the Q4_K_L class that was asked for first. |
| `heretic-35b-a3b` | llmfan46 Heretic Qwen3.6-35B-A3B Q4_K_M, MTP head kept, 21.8 GB | Abliterated 35B-A3B: the speed of the stock default without refusals, for 32 GB+ Macs. Its speculation is copied from the stock Q4 35B and not measured on this build. |
| `qwen3.8-27b-q3` | unsloth Qwen3.8-27B UD-Q3_K_XL, 13.1 GB | For 24 GB Macs: a ~148K context under a 16 GiB GPU memory limit (estimate). The unsloth "dynamic" 3-bit format keeps sensitive layers at a higher precision. |
| `orcarouter-27b-q3` | orcarouter Qwen3.8-27B Q3_K_M, 14.6 GB | Abliterated, for 24 GB Macs: a ~68K context (estimate). Q3_K_L (15.3 GB) and Q3_K_XL (16.4 GB, larger than the stock Q4) were not used. |
| `qwen3.6-35b-a3b-iq3` | unsloth Qwen3.6-35B-A3B UD-IQ3_XXS, 14.1 GB | **The default on 24 GB Macs.** Its context memory is very small (5.6 KiB/token at q4), so 2 × 96K slots fit in ~15.3 GiB. Thus, subagents work on these Macs. IQ formats unpack more slowly on Metal than K-quants. But only 3B parameters are active for each token: ~45–50 tok/s on an M2 Max ([IQ3 speculation](#iq3-speculation-measured-2026-10-03)). |
| `qwen3.8-27b-iq3` | unsloth Qwen3.8-27B UD-IQ3_XXS, 10.9 GB | The smallest stock 27B. For 24 GB Macs that need the dense 27B with 2 slots (2 × 96K fit), or a long context with 1 slot. It does not fit a 16 GB Mac under the default GPU memory limit (catalogue `min_ram_gb` 24). IQ3 loses more quality than Q3_K and Q4 in tool calls and edits: use it only when no larger build fits. ~10 tok/s on new text, 31 tok/s on an edit (M2 Max). |
| `orcarouter-27b-iq3` | orcarouter Qwen3.8-27B IQ3_XXS imatrix (bartowski), 12.6 GB | Abliterated 27B: two 96K slots fit a 24 GB Mac |
| `heretic-35b-a3b-iq3` | mradermacher i1-IQ3_XXS of llmfan46's Heretic 35B-A3B, 13.6 GB | Abliterated 35B-A3B for 24 GB Macs. **No MTP head**: n-gram speculation only. |
| `qwen3.8-9b` | empero-ai Qwen3.8-9B Distill Q4_K_M, 5.8 GB | A small model for 16 GB Macs, or for more subagents at the same time. Thinking on and off only. In CARL's code test (2026-10-04) it was less accurate than the Gemma 4 E4B, and half as fast, so Auto fit now chooses the E4B or the 12B on 16 GB Macs. |

Auto fit uses the catalogue `rank` (the quality rank, 1 = best: published benchmarks and CARL's code test first, then the quantization), the `arch` (`moe` or `dense`) and `fast` (a small dense model that the everyday goal takes with the MoE builds: the E4B). It never chooses an abliterated model ([Auto fit](server.md#auto-fit-and-the-start-model)).

| `rank` | MoE | Dense |
|---|---|---|
| 1 | | `qwen3.8-27b` (`orcarouter-27b`) |
| 2 | | `qwen3.8-27b-q3` (`orcarouter-27b-q3`) |
| 3 | `qwen3.6-35b-a3b` (`heretic-35b-a3b`) | |
| 4 | | `qwen3.8-27b-iq3` (`orcarouter-27b-iq3`) |
| 5 | `qwen3.6-35b-a3b-iq3` (`heretic-35b-a3b-iq3`) | |
| 6 | | `gemma-4-31b` |
| 7 | `gemma-4-26b-a4b` | |
| 8 | | `gemma-4-12b` |
| 9 | | `gemma-4-e4b` (`fast`) |
| 10 | | `qwen3.8-9b` |

The abliterated builds are in parentheses. The Gemma ranks are temporary (2026-10-04): published benchmarks and CARL's code test. A later CARL version measures quality.

### Gemma 4

Google's Gemma 4 models joined the catalogue on 2026-10-04. Each one is the QAT build: Google trained the weights for 4-bit, and ggml-org quantized them to Q4_0 (the token embedding at Q8_0). Q4_0 is the fastest 4-bit format on Metal.

| Model | Build, size | Why it is here |
|---|---|---|
| `gemma-4-e4b` | ggml-org gemma-4-E4B-it Q4_0 (QAT), 4.6 GB | Small and fast: 2 × 96K with the full cache needs ~8.2 GiB, so it fits a 16 GB Mac. ~46 tok/s on an M2 Max (no speculation, `llama-bench`), about 2× the 9B. More accurate than the 9B in CARL's code test (13 of 18 tasks), weaker than the 12B on hard code. Auto fit's everyday choice on 16 GB Macs (`fast: true`). Trained context 128K. |
| `gemma-4-12b` | ggml-org gemma-4-12B-it Q4_0 (QAT), 7.2 GB | Dense 12B, added on 2026-10-04. 48 layers: 8 full-attention (4.5 KiB/token at q4) and 40 sliding-window (window 1,024, 90 KiB/token at full length). With its drafter, 2 × 96K needs ~9.1 GiB with the window cache (it fits a 16 GB Mac) and ~25.7 GiB with the full cache. Google's model card: LiveCodeBench v6 72.0%, Codeforces ELO 1659, GPQA Diamond 78.8%, Tau2 69.0%. CARL's code test (2026-10-04): 17 of 18 tasks at Google's sampling. It thinks for a long time (~7,300 tokens for each small task). Auto fit's hard code choice on 16 GB Macs. |
| `gemma-4-26b-a4b` | ggml-org gemma-4-26B-A4B-it Q4_0 (QAT), 14.6 GB | MoE, 3.8B active. 2 × 96K with the full cache needs ~26.2 GiB: it fits a 36 GB Mac (28.1 GiB GPU memory limit). On a 32 GB Mac it gets 2 slots with the window cache only. |
| `gemma-4-31b` | ggml-org gemma-4-31B-it Q4_0 (QAT), 18.0 GB | Dense 31B: the strongest Gemma, and slow: ~7 tok/s without speculation (M3 Pro). MTP + n-gram almost doubles the Auto-tune score (6.9 to 12.8). Its full cache needs ~64 GiB at 2 × 96K, so it runs with the window cache (~22.9 GiB at 2 × 96K) on 32 and 36 GB Macs. CARL's code test: 17 of 18 tasks. |

- **Quality ranks 6–9** (temporary): the 31B 6, the 26B-A4B 7, the 12B 8, the E4B 9. Qwen is better at agent coding in the published benchmarks, so the Qwen 27B and 35B-A3B builds keep ranks 1–5. On 16 GB Macs, Auto fit chooses the E4B (everyday) or the 12B (hard code).
- **Sliding-window layers.** Most Gemma layers keep only a window (the E4B: 512 tokens; the 12B, the 26B and the 31B: 1,024 tokens). A saved prompt or session restores only with the full cache: every layer keeps the full context (`--swa-full`). `cache.swa = auto` takes the full cache when it fits ([Caching](caching.md)). The memory figures above come from the GGUF headers. llama.cpp's allocation matched them on the E4B (2 × 64K). On the 26B-A4B (M3 Pro 36 GB, llama.cpp 0.4.1, 2026-10-04, 2 × 96K, q4, with the drafter), the server used 1.6 GiB more than the weights with the window cache, and approximately 11 GiB more with the full cache. CARL plans 2.2 GiB and 12.6 GiB (each includes 1 GiB for buffers). Thus, the estimate is safe. The full cache is also slower. It read a prompt of 14.5K tokens at 300 tok/s (the window cache: 472 tok/s), and it wrote at 31 tok/s (the window cache: 34 tok/s). `cache.swa = auto` still uses the full cache when it fits, because CARL can then restore a saved state and does not read the prompt again.
- **KV heads per layer.** The 12B, the 26B and the 31B have fewer KV heads on the full-attention layers. Their headers list the heads per layer, and CARL reads that list (it read none before 2026-10-04, so their context memory counted as free).
- **Speculation: the MTP drafter.** Gemma 4 has no MTP head in the model file. Google ships a separate drafter for each model: `mtp-gemma-4-*-Q4_0.gguf` in the same ggml-org repo (QAT, 60 MB for the E4B, 0.25 GB for the 12B and the 26B-A4B, 0.28 GB for the 31B). The catalogue names it in the `draft` field of each entry.
  - `./carl.sh download NAME` gets the model and its drafter. `verify` checks both files, and `delete` removes both.
  - A start gives the drafter to llama-server with `-md` when the speculation uses MTP (`--spec-type draft-mtp`). The drafter uses the context memory of the model, so only its weights add memory. The fit checks count these weights.
  - If the drafter is not downloaded, the start uses n-gram speculation and tells you one time. `./carl.sh download NAME` then gets only the drafter.
  - The drafter file is not a model. The model list does not show it.
  - **Custom Gemma 4 models** (another repository, a fine-tune) get the drafter of the catalogue model with the same size. CARL finds the size in the header: architecture `gemma4`, the layer count and the expert count (E4B 42, 12B 48, 26B-A4B 30 with 128 experts, 31B 60). `download hf:…` gets the drafter with the model. For a custom model that you have already, `./carl.sh download NAME` gets only the drafter. CARL records it as `draft` in the model's `models.json` entry, and the model then starts with MTP + n-gram, 2 guesses. A fine-tune can accept fewer guesses: Auto-tune measures it (`tools/carl_core/domain/drafters.py`).
- **Speculation, measured 2026-10-04.** The E4B and the 12B on an M2 Max 32 GB (llama.cpp 0.5.0), the 26B-A4B and the 31B on an M3 Pro 36 GB (llama.cpp 0.4.1). Three tasks for each mode: about 600 tokens of prose, 700 tokens of new code, and a 110-line file that the model writes again with one change (an edit). Temperature 0, thinking off. The score is Auto-tune's weighted mean of the three speeds (tok/s).

  | Mode (llama.cpp name) | Guesses | E4B | 12B | 26B-A4B | 31B |
  |---|---|---|---|---|---|
  | none | – | 49.4 | 26.7 | 40.9 | 6.9 |
  | n-gram (`ngram-mod`) | 2 | 69.5 | 37.1 | 57.9 | 10.2 |
  | MTP (`draft-mtp`) | 1 | – | – | – | 9.2 |
  | MTP (`draft-mtp`) | 2 | 58.4 | 33.7 | 58.2 | 10.1 |
  | MTP (`draft-mtp`) | 3 / 4 | – | 33.2 / 32.9 | 55.5 / 48.8 | 9.2 / 6.8 |
  | MTP + n-gram (`draft-mtp,ngram-mod`) | 1 | – | – | – | 12.7 |
  | MTP + n-gram (`draft-mtp,ngram-mod`) | 2 | **77.0** | **41.4** | **74.9** | **12.8** |
  | MTP + n-gram (`draft-mtp,ngram-mod`) | 3 | – | 40.8 | 70.8 | 11.9 |

  - MTP + n-gram with 2 guesses is the best on each model: 55% to 85% faster than no speculation. The catalogue uses it for all four Gemma models.
  - On the 31B, 2 guesses (12.8) and 1 guess (12.7) are nearly equal. Three more runs of each gave the same numbers every time (the model is deterministic at temperature 0), so the catalogue uses 2 guesses. Auto-tune's 3% rule would keep 1 guess on this Mac.
  - MTP makes new text faster: 63% to 84% of its guesses are correct. On the 26B-A4B, new code goes from 41.4 to 61.1 tok/s.
  - n-gram makes edits faster: 130 tok/s on the 12B, 233 tok/s on the 26B-A4B. On new text it finds nothing to copy and does nothing.
  - More than 2 MTP guesses make prose slower, because fewer guesses are correct.
  - The E4B column comes from `./carl.sh tune gemma-4-e4b --quick`, the others from the same three tasks run directly. Auto-tune measures the modes on your Mac.
- **Sampling.** Google's model card: temperature 1.0, top_p 0.95, top_k 64. The catalogue `tune` of each Gemma model has these values, and a start uses them.
- **Thinking.** On or off only (catalogue `thinking: on-off`). llama-server turns it on by default. `reasoning_effort: none` turns it off ([Thinking](thinking.md#thinking-by-model)).
- **Images.** The models also take images. CARL starts them text-only (`--no-mmproj`).

### Notes on the catalogue

- **MTP speculation (`mtp`, `draft`).** `mtp: true` in the catalogue means that MTP speculation is available for the model: from an MTP head in the model file, or from a separate drafter file. `draft` names that drafter file (repo, revision, file, sha256, bytes, as `hf` does). CARL checks it as it checks `hf`.
  - The catalogue marks 10 of the 11 Qwen entries with an MTP head. `heretic-35b-a3b-iq3` has none. The Gemma 4 entries have no head in the model file, but each one has a drafter (`draft`).
  - When a speculation setting uses MTP and the model has no head and no downloaded drafter, a start uses n-gram and tells you one time.
  - For five Q4 and Q3 entries, the GGUF headers were checked: `nextn_predict_layers = 1` and the four `nextn` tensors in block 64.
  - For the two stock IQ3 builds, `nextn_predict_layers = 1` was checked on 2026-10-03, and MTP speculation ran on the 35B IQ3.
  - Auto-tune reads the header, and tests MTP only if the head is there or the drafter is downloaded.
  - unsloth also publishes the head as a separate file (`MTP/mtp-Qwen3.8-27B-Q4_0.gguf`). The main unsloth files include the head, so you do not need that file.
- **The model names.** Each build has its own name, also in the client configs (since 1.3.0). Before 1.3.0, a Q3 build used the name of its Q4 build.
- **The default model.** With `llama.model = auto`, a start uses Auto fit's choice for this Mac. If the choice is not downloaded, the start uses the best downloaded stock model that fits. `./carl.sh fit` shows which model this Mac gets, and why.
- The catalogue `default` (`qwen3.6-35b-a3b`) and `default_small` (`qwen3.6-35b-a3b-iq3`) are only the offline fallback, when CARL cannot read the GGUF headers.

**Policy:** Use only models with a known and documented uncensoring method. Do not use "uncensored" fine-tunes with an undisclosed method, also when they are popular.

### IQ quants (2026-09-24 study; IQ3 builds adopted 2026-10-03)

- **On a 36 GB Mac, IQ quants are not used.** The 2026-09-24 study (below) found the gain too small and the quality loss too large for the 27B.
- **Since 2026-10-03, IQ3 builds are in the catalogue** for Macs where no larger file fits: `qwen3.6-35b-a3b-iq3` (the 24 GB default), `qwen3.8-27b-iq3` (the smallest 27B), and the abliterated `orcarouter-27b-iq3` and `heretic-35b-a3b-iq3`.
- **Their speculation:** the stock IQ3 builds use MTP + n-gram with 1 guess, as the 27B K-quants. MTP alone helps much less than on the K-quants, and 2 guesses lose ([IQ3 speculation](#iq3-speculation-measured-2026-10-03)).

#### The 2026-09-24 study (27B, IQ4_XS and IQ3_M)

The candidates were IQ4_XS and IQ3_M from `HauhauCS/Qwen3.8-27B-uncensored-hauhaucs-aggressive-mtp-gguf`.

| Quant | File size | vs Q4_K_M (17.77 GB) | Write speed ceiling | Expected real gain | Quality |
|---|---|---|---|---|---|
| IQ4_XS | 15.71 GB | −2.1 GB | ~+13% | ~+8–12% | usually within ~1–2% of Q4_K_M on perplexity |
| IQ3_M | 12.79 GB | −5.0 GB | ~+39% | ~+10–25% | clearly worse (several %); more errors in tool calls and edits |

The gains are smaller than the size difference shows, for these reasons:
- **The write speed follows the bytes read for each token** (mostly weights). This sets the ceiling in the table.
- **IQ formats cost more to unpack on Metal than K-quants.** IQ4_XS costs little. IQ3_M uses a lookup-table format that is slow for each byte on Metal.
- **The read speed is compute-bound.** Thus, both IQ quants would probably read prompts a little *more slowly* than Q4_K_M.
- **Speculation does not change this result.** MTP guesses use the same kernels. Also, the MTP speed-up in llama.cpp PR #29110 applies only to Q4_0 and Q8_0 weights.

The HauhauCS model is also different from orcarouter in ways that are not related to the quant:
- **Method not disclosed:** it claims "aggressive" uncensoring with 0/465 refusals. But it has no published KL, MMLU or coding values. It does not state if it uses an imatrix. It is very popular (~2M downloads).
- **"Minimal preamble":** it can give shorter answers. This could save more time than the quant.
- **FastMTP:** its "up to 3×" claim needs a patched llama.cpp build, and was measured on NVIDIA. Homebrew llama.cpp cannot run it. The standard MTP head works as usual.

**Decision (2026-09-24): not adopted on 36 GB.** The reasons:
- The gain was small.
- IQ3_M loses too much quality for agent work.
- The HauhauCS method is not documented.

To examine this again:
1. Test **orcarouter IQ4_XS** first (bartowski, 15.57 GB). It is the same model, so the test isolates the effect of the quant.
2. Use `tools/llama-spec-sweep.sh`, the 66K recall test (`tools/llama-kv-longctx.py`) and a real OpenCode session.

IQ3_M is a good choice only where no larger file fits, for example on a 24 GB Mac. This is the role of the IQ3 builds in the catalogue now.

#### IQ3 speculation (measured 2026-10-03)

**Setup:** Apple M2 Max 32 GB, llama.cpp 0.5.0 (build 11146), 400-token runs, thinking off. The values are the write speed in tok/s (the mean of 2 runs): prose / code / edit (the model writes code again with small changes). The bold row is the catalogue setting.

**`qwen3.6-35b-a3b-iq3`** (unsloth UD-IQ3_XXS, 14.1 GB):

| Speculation | Prose | Code | Edit |
|---|---|---|---|
| none | 49 | 45 | 46 |
| MTP, 1 guess | 47 | 44 | 46 |
| MTP, 2 guesses | 40 | 41 | 49 |
| n-gram (`ngram-mod`), 2 guesses | 47 | 42 | 117 |
| **MTP + n-gram, 1 guess** | **49** | **50** | **93** |
| MTP + n-gram, 2 guesses | 42 | 43 | 83 |

**`qwen3.8-27b-iq3`** (unsloth UD-IQ3_XXS, 10.9 GB), the same setup:

| Speculation | Prose | Code | Edit |
|---|---|---|---|
| none | 9.4 | 8.9 | 8.9 |
| MTP, 1 guess | 9.7 | 9.8 | 11.0 |
| MTP, 2 guesses | 8.9 | 9.4 | 11.3 |
| n-gram (`ngram-mod`), 2 guesses | 9.8 | 9.6 | 21.5 |
| **MTP + n-gram, 1 guess** | **10.0** | **9.8** | **31.1** |
| MTP + n-gram, 2 guesses | 8.7 | 9.7 | 23.3 |

**Results:**
- **MTP alone helps little on IQ3.** On the 35B IQ3, it adds about nothing. On the 27B IQ3, it adds only ~5–10%. On the Q4 builds (M3 Pro), MTP alone added 27–50% ([Performance](performance.md#performance-measured)). The IQ kernels make the check of the guesses expensive. Thus, for MTP alone, the hypothesis "IQ quants do not do well with MTP" is correct.
- **2 guesses lose on IQ3.** On the 35B IQ3, MTP with 2 guesses is about 15% slower on new text than no speculation.
- **MTP + n-gram with 1 guess is the best on both models.** The Auto-tune score is a weighted geometric mean (prose 0.4, code 0.4, edit 0.2). With this score, MTP + n-gram is about 4–5% better than n-gram alone on the 35B IQ3. On the 27B IQ3, it is about 9% better. n-gram alone is better only on a code edit on the 35B (117 vs 93 tok/s).
- **Thus, the catalogue tune for both stock IQ3 builds is MTP + n-gram (`draft-mtp,ngram-mod`) with 1 guess.** The earlier setting of the 35B IQ3 (MTP + n-gram, 2 guesses, copied from the Q4) was the second slowest mode on new text. The Q4 35B keeps 2 guesses (measured on the M3 Pro).
- The Server panel warns about MTP with more than 1 guess on an IQ quant: `⚠ Speculation: MTP with more than 1 guess is about 15% slower on IQ quantizations (measured).` To measure a model on your own Mac, run `./carl.sh tune NAME`.

**The abliterated IQ3 builds** (the same setup, 2026-10-03):

**`heretic-35b-a3b-iq3`** (mradermacher i1-IQ3_XXS of llmfan46's Heretic 35B-A3B, 13.6 GB; **no MTP head**, so n-gram only):

| Speculation | Prose | Code | Edit |
|---|---|---|---|
| none | 49.3 | 46.5 | 41.3 |
| **n-gram, 1 guess** | **51.5** | **48.5** | **96.1** |
| n-gram, 2 guesses | 50.4 | 45.4 | 85.0 |
| n-gram, 3 guesses | 46.0 | 44.9 | 83.1 |

**`orcarouter-27b-iq3`** (bartowski IQ3_XXS imatrix, 12.6 GB):

| Speculation | Prose | Code | Edit |
|---|---|---|---|
| none | 8.6 | 8.6 | 8.5 |
| MTP, 1 guess | 8.6 | 9.3 | 9.6 |
| MTP, 2 guesses | 8.1 | 9.3 | 9.9 |
| **n-gram, 2 guesses** | **8.8** | **8.6** | **19.8** |
| MTP + n-gram, 1 guess | 8.7 | 9.0 | 18.2 |
| MTP + n-gram, 2 guesses | 7.6 | 8.3 | 20.1 |

- **Heretic 35B IQ3:** n-gram with 1 guess is the best on all three workloads (score 57.0, vs 53.6 for 2 guesses and 46.5 without speculation). The catalogue uses `ngram-mod` with 1 guess. An IQ3 of this model with the MTP head would probably do better on new text. It does not exist yet (CARL may quantize one later). Auto-tune now also measures n-gram with 1 guess on a model without an MTP head.
- **orcarouter 27B IQ3:** n-gram alone (2 guesses) and MTP + n-gram (1 guess) have the same score (10.2). The Auto-tune rule selects the simpler mode for a tie, so the catalogue uses `ngram-mod` with 2 guesses. MTP alone adds only ~6% on this build (the stock 27B IQ3: 5–10%).
- **9B Distill** (Auto-tune, M2 Max, llama.cpp 0.5.0, 2026-10-03, prose / code / edit): none 24.8 / 24.1 / 24.3, n-gram with 2 guesses 23.7 / 24.3 / 112.5, MTP with 1 guess 24.9 / 24.1 / 27.7, **MTP + n-gram with 1 guess 25.8 / 25.8 / 103.7** (the best score). New text is almost not faster. A code edit is ~4× faster.

### The coder subagent

**One definition, two clients.** `client/agents/coder.md` holds the definition:
- The frontmatter `description` tells when to use the coder. The main model reads this text.
- The body holds the method of the coder:
  1. Understand.
  2. Plan.
  3. Work in small, checked steps. For a stuck task, reproduce the failure before you fix it.
  4. Verify.
  5. Stop after three failed approaches.
- It also sets rules (no placeholders), engineering standards, a definition of done, and a structured report.
- `client/agents/delegation.md` holds the rule for the main agent. The installer adds this rule to the system prompt.

**When the main agent uses the coder.** It decides before the first tool call. It uses the coder only in these conditions:
- It is **stuck**: two fixes failed (its own fixes, or fixes that the user reports as failed).
- The task is **large**: 3+ files, ~150+ lines, a new module, package or CLI, an implementation with tests, or a multi-step refactor.
- All other work stays in the main session.
- When background subagents are on, the rule also tells the main agent to run the coder in the background.

**OpenCode** (`agent.coder` in `opencode.json`):

| Key | Value |
|---|---|
| `mode` | `subagent` |
| `prompt` | `{file:…/.config/opencode/carl/coder.md}` (an absolute path) |
| `options.reasoningEffort` | `medium` (the main session uses `low`; on the 35B, it only means thinking on) |
| `temperature` | `0.6` (see "Coder sampling" below) |
| `permission.task` | `deny` (no nested subagents) |
| `steps` | 80 |
| `color` | `secondary` |

- The rule is in `~/.config/opencode/carl/delegation.md`. The installer adds it to your `instructions` list.
- Earlier versions used `~/.config/opencode/prompts/`. The installer removes those files.
- If you have your own agent `coder`, CARL's coder is `carl-coder`.

**Pi:**
- Pi uses its official `subagent` example extension. A vendored copy is in `client/pi/extensions/subagent` (MIT, pi-coding-agent 1.0.0).
- The copy has a patch: the tool description lists the installed agents and their descriptions. The upstream version lists no agents, so the model could not select one itself.
- The coder runs as a separate `pi --mode json -p --no-session` process with the same model.
- The coder gets each tool except `subagent` and `tool_search` (`exclude-tools: subagent, tool_search`). It keeps web search (an MCP server that is always loaded). It cannot start nested subagents, and it cannot load the browser tools (`tool_search` loads them).
- The rule is a marked block in `~/.pi/agent/APPEND_SYSTEM.md`.

**When the setup installs it:** with the answer `auto` (the default), if the server has 2 or more slots (`/props` `total_slots`). The answers `on` and `off` (`--coder`, or `CODER=1` and `NO_CODER=1`) override this, and the setup keeps them. If the server is not reachable, the setup keeps the earlier choice.

**Same model, different context.** With a single model, the server loads only one model, so the coder does not add capability. The coder gets a new context with only the task, more reasoning and a stricter method. With 2 slots, the main session keeps its cache while the coder runs.

**Reason for the rule:** with only the agent description, the 35B never gave a task to the coder. It did 4 of 4 tasks itself. These tasks included a multi-file package and a bug that the user reported as stuck.

**Coder sampling (measured 2026-10-02, M3 Pro 36 GB, llama.cpp 0.4.1):** the 35B (2 × 96K, q4) did the same task 3 times for each setting. The task was a new package with a CLI that parses real llama-server log lines, with tests. The main agent sent it to the coder each time.

| Coder setting | Average time | Tests written | Typed functions | Packaging file builds |
|---|---|---|---|---|
| Thinking on, temperature 1.0 | 405 s | 16, 18, 12 | 10/13 | 1 of 2 |
| **Thinking on, temperature 0.6** | **299 s** | **35, 18, 4** | **20/20** | 1 of 3 |
| Thinking off (0.7 / 0.8 / presence 1.5) | 234 s | 9, 9, 7 | 10/10 | 0 of 3 |

- All 9 runs passed their own tests, and all parsed the real log correctly.
- **Selected: thinking on, temperature 0.6.** It typed each function, wrote the most tests on average, and was 26% faster than 1.0. It also agrees with the Qwen model cards, which give 0.6 for precise coding.
- Thinking off was the fastest. But it wrote about half the tests, and none of its packaging files built.
- 6 of 8 packaging files (`pyproject.toml`) did not build, with all settings. Thus, the coder prompt now has a "Builds" item in its definition of done.
- NOTE: 3 runs for each setting show a direction, not proof. The test count for one setting went from 4 to 35.
- NOTE: **The 27B coder uses the same setting, but it did not get its own test.** A full test of the 27B is a v2 item.
- NOTE: **Pi has no temperature for subagents.** Its subagent extension sends only the thinking level. Thus, the Pi coder uses the server temperature (1.0).

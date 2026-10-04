# CARL Reference: Models and quantization

[Index](../REFERENCE.md) · why these models, IQ quants, the coder subagent.

## Model and quantization choices

### Why these models

| Model | Why it is here |
|---|---|
| unsloth Qwen3.8-27B UD-Q4_K_M (`qwen3.8-27b`) | Stock (not abliterated) 27B. It is the smallest Q4 build of the 27B (16.5 GB), so it fits on more Macs. It has the same architecture and speed profile as the measured orcarouter build. |
| orcarouter Qwen3.8-27B Q4_K_M (bartowski, `orcarouter-27b`) | Abliteration with published figures (0–2.7% refusals, MMLU +0.4). Its imatrix was calibrated with a large quantity of tool calls. MTP head in the file. Q4_K_M is the nearest GGUF to the Q4_K_L class that was requested first. |
| unsloth Qwen3.6-35B-A3B UD-Q4_K_M (MTP build, **default**, `qwen3.6-35b-a3b`, the `default` of `host/catalog.json`) | Stock MoE with 3B active. It has approximately 4× the decode speed and 6× the prompt read speed of the 27B. Its KV cache is very small, so long sessions and 2 slots cost little. It is the default since 2026-10-01 (the dense 27B is heavy for general use). |
| unsloth Qwen3.8-27B UD-Q3_K_XL (`qwen3.8-27b-q3`, 13.1 GB) | For 24 GB Macs: ~148K window under a 16 GiB GPU limit (estimate). The unsloth "dynamic" 3-bit format keeps sensitive layers at higher precision. |
| unsloth Qwen3.6-35B-A3B UD-IQ3_XXS (`qwen3.6-35b-a3b-iq3`, 14.1 GB) | **Default on 24 GB Macs** (catalogue `default_small`, selected when the main default cannot fit). Its MoE KV is very small (5.6 KiB/token at q4), so 2 × 96K slots fit in ~15.3 GiB. Thus, subagents work on these Macs. IQ formats unpack more slowly on Metal than K-quants, but only 3B parameters are active for each token: ~45–50 tok/s on an M2 Max (measured 2026-10-03, [below](#iq3-speculation-measured-2026-10-03)). Speculation: MTP + n-gram, 1 draft. |
| unsloth Qwen3.8-27B UD-IQ3_XXS (`qwen3.8-27b-iq3`, 10.9 GB) | The smallest 27B build. For 24 GB Macs that want the dense 27B with 2 slots (2 × 96K fit), or a long window with 1 slot. It does not fit a 16 GB Mac under the default GPU limit (catalogue `min_ram_gb` 24; on 16 GB it needs a raised GPU limit). IQ3 loses more quality than Q3_K/Q4 in tool calls and edits: use it only when nothing larger fits. ~10 tok/s on new text, 31 tok/s on a re-emit (M2 Max). Speculation: MTP + n-gram, 1 draft. |
| orcarouter Qwen3.8-27B Q3_K_M (`orcarouter-27b-q3`, 14.6 GB) | Abliterated, for 24 GB Macs: ~68K window (estimate). Q3_K_L (15.3 GB) and Q3_K_XL (16.4 GB, bigger than the stock Q4) were not used. |

- **All seven entries have the MTP head in the file.** For five entries, this was checked in the GGUF headers: `nextn_predict_layers = 1` and the four `nextn` tensors in block 64. For the two IQ3 builds, `nextn_predict_layers = 1` was checked on 2026-10-03, and MTP drafting ran on the 35B IQ3. Auto-tune reads the header, and tests MTP only if the head is there.
  - unsloth also publishes the head as a separate file (`MTP/mtp-Qwen3.8-27B-Q4_0.gguf`). But the main unsloth files include the head, so you do not need that file.
- **Q3 entries use the model name of their Q4 sibling**, so client configs work without change.
- **The default model is `default` in `host/catalog.json`.** If that model cannot fit one window, the launcher uses the default for small Macs (`default_small`, `qwen3.6-35b-a3b-iq3`). If the selected default is not downloaded, the launcher uses the first downloaded model. `./carl.sh fit` shows which model this Mac gets.

**Policy:** Use only models with a known and documented uncensoring method. Do not use "uncensored" fine-tunes with an undisclosed method, even if they are popular.

### IQ quants (2026-09-24 study; IQ3 builds adopted 2026-10-03)

- **On a 36 GB Mac, IQ quants are not used.** The 2026-09-24 study (below) found the gain too small and the quality loss too large for the 27B.
- **Since 2026-10-03, two IQ3 builds are in the catalogue,** for Macs where no larger file fits: `qwen3.6-35b-a3b-iq3` (the 24 GB default) and `qwen3.8-27b-iq3` (the smallest 27B).
- **Their speculation:** MTP + n-gram with 1 draft token, as on the 27B K-quants. MTP alone helps much less than on the K-quants, and 2 drafts lose ([IQ3 speculation](#iq3-speculation-measured-2026-10-03)).

#### The 2026-09-24 study (27B, IQ4_XS and IQ3_M)

The candidates were IQ4_XS and IQ3_M from `HauhauCS/Qwen3.8-27B-uncensored-hauhaucs-aggressive-mtp-gguf`.

| Quant | File size | vs Q4_K_M (17.77 GB) | Decode ceiling | Expected real decode gain | Quality |
|---|---|---|---|---|---|
| IQ4_XS | 15.71 GB | −2.1 GB | ~+13% | ~+8–12% | usually within ~1–2% of Q4_K_M on perplexity |
| IQ3_M | 12.79 GB | −5.0 GB | ~+39% | ~+10–25% | clearly worse (several %); more errors in tool calls and edits |

The gains are smaller than the size difference suggests, for these reasons:
- **Decode speed follows the bytes read for each token** (mostly weights). This sets the ceiling in the table.
- **IQ formats cost more to unpack on Metal than K-quants.** IQ4_XS costs little. IQ3_M uses a lookup-table format that is known to be slow per byte on Metal.
- **Prompt read speed is compute-bound**, so both IQ quants would probably read prompts slightly *slower* than Q4_K_M.
- **Speculation does not change this result.** MTP drafts use the same kernels. Also, the MTP speed-up in llama.cpp PR #29110 applies only to Q4_0/Q8_0 weights.

The HauhauCS model also has differences from orcarouter that are not related to the quant:
- **Method not disclosed:** It claims "aggressive" uncensoring with 0/465 refusals. But it has no published KL, MMLU or coding figures. It does not state if it uses an imatrix. It is very popular (~2M downloads).
- **"Minimal preamble":** It can give shorter answers. This could save more time than the quant.
- **FastMTP:** Its "up to 3×" claim needs a patched llama.cpp build, and was measured on NVIDIA. Homebrew llama.cpp cannot run it. The standard MTP head works as usual.

**Decision (2026-09-24): not adopted on 36 GB.** The reasons are:
- The gain was small.
- IQ3_M loses too much quality for agent work.
- The HauhauCS method is not documented.

If you examine this again, do these steps:
1. Test **orcarouter IQ4_XS** first (bartowski, 15.57 GB). It is the same model, so the test isolates the effect of the quant.
2. Use `tools/llama-spec-sweep.sh`, the 66K recall test (`tools/llama-kv-longctx.py`) and a real OpenCode session.

IQ3_M is a good choice only where no larger file fits, for example on a 24 GB Mac. This is the role of the IQ3 builds in the catalogue now.

#### IQ3 speculation (measured 2026-10-03)

**Setup:** Apple M2 Max 32 GB, llama.cpp 0.5.0 (build 11146), 400-token runs, thinking off. The values are the decode speed in tok/s (the mean of 2 runs): prose / code / re-emit (the model writes code again). The bold row is the catalogue setting.

**`qwen3.6-35b-a3b-iq3`** (unsloth UD-IQ3_XXS, 14.1 GB):

| Speculation | Prose | Code | Re-emit |
|---|---|---|---|
| none | 49 | 45 | 46 |
| MTP, n=1 | 47 | 44 | 46 |
| MTP, n=2 | 40 | 41 | 49 |
| n-gram (`ngram-mod`, n=2) | 47 | 42 | 117 |
| **MTP + n-gram, n=1** | **49** | **50** | **93** |
| MTP + n-gram, n=2 | 42 | 43 | 83 |

**`qwen3.8-27b-iq3`** (unsloth UD-IQ3_XXS, 10.9 GB), same setup:

| Speculation | Prose | Code | Re-emit |
|---|---|---|---|
| none | 9.4 | 8.9 | 8.9 |
| MTP, n=1 | 9.7 | 9.8 | 11.0 |
| MTP, n=2 | 8.9 | 9.4 | 11.3 |
| n-gram (`ngram-mod`, n=2) | 9.8 | 9.6 | 21.5 |
| **MTP + n-gram, n=1** | **10.0** | **9.8** | **31.1** |
| MTP + n-gram, n=2 | 8.7 | 9.7 | 23.3 |

**Results:**
- **MTP alone helps little on IQ3.** On the 35B IQ3, it adds about nothing. On the 27B IQ3, it adds only ~5–10%. On the Q4 builds (M3 Pro), MTP alone added 27–50% ([section 12](performance.md#performance-measured)). The IQ kernels make the verification of the drafts expensive. Thus, for MTP alone, the hypothesis "IQ quants do not do well with MTP" is correct.
- **2 drafts lose on IQ3.** On the 35B IQ3, MTP n=2 is about 15% slower on new text than no speculation.
- **MTP + n-gram with 1 draft is the best on both models.** With the Auto-tune score (weighted geometric mean: prose 0.4, code 0.4, re-emit 0.2), it is about 4–5% better than n-gram alone on the 35B IQ3, and about 9% better on the 27B IQ3. n-gram alone is better only on a code re-emit on the 35B (117 vs 93 tok/s).
- **Thus, the catalogue tune for both IQ3 builds is `draft-mtp,ngram-mod` with n=1.** The earlier setting of the 35B IQ3 (MTP + n-gram, n=2, copied from the Q4) was the second slowest mode on new text. The Q4 35B keeps n=2 (measured on the M3 Pro).
- The Settings tab shows MTP with more than 1 draft on an IQ quant in red. To measure a model on your own Mac, run `./carl.sh tune NAME`.

**The abliterated IQ3 builds** (same setup, 2026-10-03):

**`heretic-35b-a3b-iq3`** (mradermacher i1-IQ3_XXS of llmfan46's Heretic 35B-A3B, 13.6 GB; **no MTP head**, so n-gram only):

| Speculation | Prose | Code | Re-emit |
|---|---|---|---|
| none | 49.3 | 46.5 | 41.3 |
| **n-gram, n=1** | **51.5** | **48.5** | **96.1** |
| n-gram, n=2 | 50.4 | 45.4 | 85.0 |
| n-gram, n=3 | 46.0 | 44.9 | 83.1 |

**`orcarouter-27b-iq3`** (bartowski IQ3_XXS imatrix, 12.6 GB):

| Speculation | Prose | Code | Re-emit |
|---|---|---|---|
| none | 8.6 | 8.6 | 8.5 |
| MTP, n=1 | 8.6 | 9.3 | 9.6 |
| MTP, n=2 | 8.1 | 9.3 | 9.9 |
| **n-gram, n=2** | **8.8** | **8.6** | **19.8** |
| MTP + n-gram, n=1 | 8.7 | 9.0 | 18.2 |
| MTP + n-gram, n=2 | 7.6 | 8.3 | 20.1 |

- **Heretic 35B:** n-gram with 1 draft is the best on all three workloads (score 57.0 vs 53.6 for n=2 and 46.5 without speculation). The catalogue uses `ngram-mod` n=1. An MTP-preserving IQ3 of this model would likely do better on new text; it does not exist yet (CARL may quantize one later).
- **orcarouter 27B IQ3:** n-gram alone (n=2) and MTP + n-gram (n=1) tie (score 10.2). The Auto-tune rule picks the simpler mode for a tie, so the catalogue uses `ngram-mod` n=2. MTP alone adds only ~6% on this build (the stock 27B IQ3: 5-10%).

### The coder subagent

- **One definition, two clients.** `client/agents/coder.md` contains the definition:
  - The frontmatter `description` tells when to use the coder. The main model reads this text.
  - The body contains the instructions for the coder:
    - understand
    - plan
    - small verified steps
    - reproduce before you fix
    - no placeholders
    - stop after three failed approaches
    - structured report.
  - `client/agents/delegation.md` contains the rule for the main agent. This rule is added to the system prompt.
- **When the coder is used:** The main agent decides before the first tool call. It uses the coder only in these conditions:
  - It is **stuck**: two fixes failed (its own fixes, or fixes that the user reports as failed).
  - The task is **large**: 3+ files, ~150+ lines, a new module/package/CLI, implementation plus tests, or a multi-step refactor.
  - All other work stays in the main session.
- **OpenCode:** `agent.coder` in `opencode.json`:
  - `mode: subagent`, `prompt: {file:…/.config/opencode/carl/coder.md}` (an absolute path; the rule is `~/.config/opencode/carl/delegation.md`; earlier versions used `~/.config/opencode/prompts/`, and the installer removes those files);
  - `options.reasoningEffort: medium` (the main session uses low; on the 35B, it only means thinking on);
  - `temperature: 0.6` (see "Coder sampling" below);
  - `permission.task: deny` (no nested subagents), `steps: 80`, `color: secondary`.
  - The rule is added through `instructions`. The installer adds it to the instructions that you already have.
- **Pi:**
  - Pi uses its official `subagent` example extension. A vendored copy is in `client/pi/extensions/subagent` (MIT, pi-coding-agent 1.0.0).
  - The copy has a patch: the tool description lists the installed agents and their descriptions. The upstream version lists no agents, so the model could not select one itself.
  - The coder runs as a separate `pi --mode json -p --no-session` process with the same model. It has the tools read, bash, edit, write, grep, find, ls. It does not have `subagent`, so it cannot start nested subagents.
  - The rule is a marked block in `~/.pi/agent/APPEND_SYSTEM.md`.
- **When `install.sh` installs it:** if the server has 2+ slots. `CODER=1` / `NO_CODER=1` override this.
- **Same model, different context.** The server loads only one model, so the coder does not add capability. The coder gets a new context with only the task, more reasoning and a stricter method. With 2 slots, the main session keeps its cache while the coder runs.
- **Reason for the rule:** With only the agent description, the 35B never delegated. It did 4/4 tasks itself. These tasks included a multi-file package and a bug that the user reported as stuck.


**Coder sampling (measured 2026-10-02):** the 35B (2 × 96K, q4_0) did the same task 3 times for each setting. The task was a new package with a CLI that parses real llama-server log lines, plus tests. The main agent sent it to the coder each time.

| Coder setting | Average time | Tests written | Typed functions | Packaging file builds |
|---|---|---|---|---|
| Thinking on, temperature 1.0 | 405 s | 16, 18, 12 | 10/13 | 1 of 2 |
| **Thinking on, temperature 0.6** | **299 s** | **35, 18, 4** | **20/20** | 1 of 3 |
| Thinking off (0.7 / 0.8 / presence 1.5) | 234 s | 9, 9, 7 | 10/10 | 0 of 3 |

- All 9 runs passed their own tests, and all parsed the real log correctly.
- **Selected: thinking on, temperature 0.6.** It typed every function, wrote the most tests on average, and was 26% faster than 1.0. It also agrees with the Qwen model cards, which give 0.6 for precise coding.
- Thinking off was the fastest. But it wrote about half the tests, and none of its packaging files built.
- 6 of 8 packaging files (`pyproject.toml`) did not build, with all settings. Thus, the coder prompt now has a "Builds" item in its definition of done.
- NOTE: 3 runs for each setting give a direction, not proof. The test count for one setting went from 4 to 35.
- NOTE: **The 27B coder uses the same setting, but it did not get its own test.** A full test of the 27B is a v2 item.
- NOTE: **Pi has no temperature for subagents.** Its subagent extension passes only the thinking level. Thus, the Pi coder uses the server temperature (1.0).

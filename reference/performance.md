# CARL Reference: Performance

[Index](../REFERENCE.md) · measured speeds.

## Performance (measured)

All values come from this M3 Pro 36 GB with llama.cpp 0.4.1. The decode speed is in tok/s, for 400-token generations: prose / code / edit (the model writes a 1.9K-token file again).

| Speculation | Qwen3.8-27B (dense) | Qwen3.6-35B-A3B (MoE) |
|---|---|---|
| none | 7.4 / 7.3 / 7.3 | 33 / 34 / 33 |
| MTP, 1 draft | 11.1 / 11.4 / 11.4 | 42 / 41 / 44 |
| ngram-mod only | 6.9 / 6.9 / 17.1 | 33 / 33 / 67 |
| MTP + ngram, 1 draft | **10.5 / 10.9 / 27.3** | 44 / 44 / 70 |
| MTP + ngram, 2 drafts | 9.1 / 10.3 / 17.4 | **42 / 45 / 117** |

The bold rows are the catalogue settings (`tune.spec`, `tune.spec_n`): MTP + ngram is the best of 7 configs on both models. The 27B uses 1 draft. The 35B uses 2. The IQ3 builds use MTP + n-gram with 1 draft ([IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)). Auto-tune (`./carl.sh tune NAME`) repeats this measurement on your Mac.

| | Qwen3.8-27B | Qwen3.6-35B-A3B |
|---|---|---|
| Prompt read, cold | ~85–90 tok/s at 2–9K; ~56–65 tok/s at 66K (56K ≈ 16 min) | ~490–580 tok/s at 2–9K |
| Append to a long session | ~35–45 tok/s (6K tokens ≈ 2.5–3 min); small follow-ups use the cache again (~5 s) | not measured |
| Decode far into a session | 6.5–8 tok/s at 60–80K | not measured |
| Memory (RSS, 128K, q4_0 KV) | ~20 GB (q8_0: ~22) | ~22.8 GB |
| KV cache at 128K, q4_0 / q8_0 (calculated) | 2.25 / 4.25 GiB (18 KiB/token at q4) | 0.70 / 1.33 GiB (5.6 KiB/token at q4) |
| Long-context recall | 8/8 needles at 66K, q4 and q8 | not measured |

For the 35B at 64K–150K, see [Context length](memory.md#context-length-what-longer-windows-cost).

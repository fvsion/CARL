# CARL Reference: Performance

[Index](README.md) · measured speeds.

## Performance (measured)

All values in this page come from an M3 Pro 36 GB with llama.cpp 0.4.1 (2026-09-24 to 2026-10-01). The write speed is in tok/s, for 400-token answers: prose / code / edit (the model writes a 1.9K-token file again with small changes).

| Speculation | Qwen3.8-27B (dense) | Qwen3.6-35B-A3B (MoE) |
|---|---|---|
| none | 7.4 / 7.3 / 7.3 | 33 / 34 / 33 |
| MTP, 1 guess | 11.1 / 11.4 / 11.4 | 42 / 41 / 44 |
| n-gram only, 1 guess | 6.9 / 6.9 / 17.1 | 33 / 33 / 67 |
| MTP + n-gram, 1 guess | **10.5 / 10.9 / 27.3** | 44 / 44 / 70 |
| MTP + n-gram, 2 guesses | 9.1 / 10.3 / 17.4 | **42 / 45 / 117** |

- The bold values are the catalogue settings (`tune.spec`, `tune.spec_n`). MTP + n-gram is the best of 7 configurations on both models.
- The 27B uses 1 guess. The 35B uses 2.
- The 27B with MTP alone and more guesses: 2 guesses 9.0 / 9.6 / 11.5, 3 guesses 6.6 / 7.7 / 9.8.
- The IQ3 builds and the 9B were measured on an M2 Max ([IQ3 speculation](models.md#iq3-speculation-measured-2026-10-03)). The Gemma 4 models: [Gemma 4](models.md#gemma-4).
- Auto-tune (`./carl.sh tune NAME`) does this measurement again on your Mac.

| | Qwen3.8-27B | Qwen3.6-35B-A3B |
|---|---|---|
| Read speed, cold | ~85–90 tok/s at 2–9K; ~56–65 tok/s at 66K (56K ≈ 16 min) | ~490–580 tok/s at 2–9K |
| Add to a long session | ~35–45 tok/s (6K tokens ≈ 2.5–3 min). Small follow-ups use the cache again (~5 s). | not measured |
| Write speed far into a session | 6.5–8 tok/s at 60–80K | ~20 tok/s at 48K–64K |
| Memory of the server (RSS, 128K, q4 context memory) | ~20 GiB (q8: ~22) | ~22.8 GiB |
| Context memory at 128K, q4 / q8 (calculated) | 2.25 / 4.25 GiB (18 KiB/token at q4) | 0.70 / 1.33 GiB (5.6 KiB/token at q4) |
| Long-context recall | 8/8 needles at 66K, q4 and q8 | 8/8 at 64K–150K, q4 |
| Two slots that write at the same time | 8.8 vs 9.6 tok/s alone (no gain) | +39% in total |

For the 35B at 64K–150K, see [Context length](memory.md#context-length-what-a-larger-context-costs).

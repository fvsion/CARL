# CARL Reference: Sampling, output limits and context limits

[Index](../REFERENCE.md) · Qwen's sampling values, what runs, context limits.

## Sampling and output limits

### Qwen's recommendations (model cards, checked 2026-10-01)

| Mode | temperature | top_p | top_k | min_p | presence | repetition |
|---|---|---|---|---|---|---|
| Qwen3.8, thinking | 1.0 | 0.95 | 20 | 0 | 0 | 1.0 |
| Qwen3.8 / Qwen3.6, non-thinking (instruct) | 0.7 | 0.80 | 20 | 0 | 1.5 | 1.0 |
| Qwen3.6, thinking, general tasks | 1.0 | 0.95 | 20 | 0 | 1.5 | 1.0 |
| Qwen3.6, thinking, precise coding (e.g. WebDev) | 0.6 | 0.95 | 20 | 0 | 0 | 1.0 |

Qwen ran its agentic coding benchmarks (SWE-bench, Terminal-Bench, Claude Code harness) at temperature 1.0, top_p 0.95 for both models. The card says that presence 0–2 decreases endless repetition. But higher values can cause language mixing.

### What actually runs

| Situation | Sampling | Source |
|---|---|---|
| Thinking on, any llama.cpp model (normal use) | 1.0 / 0.95 / 20 / 0 / presence 0 / repetition 1.0 | `serve-llama.sh` sets these values explicitly (`TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE` override them). The clients send no sampling fields. The capture proxy showed only `model`, `stream`, `store`, `reasoning_effort`. |
| OpenCode `none` variant (thinking off) | 0.7 / 0.80 / 20 / 0 / presence 1.5 | The variant sends `temperature`, `top_p`, `presence_penalty` with `reasoning_effort: none`. OpenCode forwards variant options into the request body. This is the same mechanism that carried `chat_template_kwargs` in the 2026-09-24 captures. These fields were not captured again yet. |
| Pi `off` (thinking off) | thinking-mode values | Pi changes only `chat_template_kwargs` for each level. It cannot change the sampling for each level. Small effect: direct answers are slightly more random. |

- **Reason for explicit flags:** The five catalogue GGUFs that were checked contain 1.0 / 20 / 0.95 (`general.sampling.*`). The flags also cover the two IQ3 builds and custom models, which were not checked. But a GGUF without these values would get the generic llama.cpp defaults with no warning (temperature 0.8, top_k 40, min_p 0.05).
- **35B-A3B choices:** Temperature 1.0 with presence 0 agrees with the Qwen agentic benchmark settings.
  - If the model loops or repeats text, start the server with `PRESENCE=1.5` (the general recommendation of the card).
  - For precise code work, use `TEMP=0.6`.
  - Neither setting was compared here.

| | llama.cpp |
|---|---|
| Output cap | None by default (`n_predict -1`): the model generates until it stops or the context is full |
| Client sends | OpenCode: no `max_tokens` (seen with the capture proxy). Pi: `max_tokens` = `maxTokens` (`install.sh` caps it at half the window). |

NOTE: The presence 1.5 of the non-thinking variant is the Qwen recommendation for that mode.

---

## Context limits: server vs client

- **The server setting `--ctx` is the real limit.**
- **The client limit only decides when the client compacts.** The client limits are `limit.context` in OpenCode and `contextWindow` in Pi. The clients do not send these values to the server.
- **Client limit above server limit:** When the session is larger than the server context window, each request fails with `HTTP 400 exceed_context_size_error`. The client never compacts by itself.
- **Server limit above client limit:** This causes no problem. The extra memory is not used.
- **`client/install.sh` keeps the two limits the same.** The order of the sources is: `LLAMA_CTX`, then `/props` → `default_generation_settings.n_ctx` of the running server, then 96K (98304). It caps the output at half the context window. After you change `--ctx`, run it again. Then restart the client.

# CARL Reference: Sampling, output limits and context limits

[Index](README.md) · Qwen's sampling values, what runs, context limits.

## Sampling and output limits

### Qwen's recommendations (model cards, checked 2026-10-01)

| Mode | temperature | top_p | top_k | min_p | presence | repetition |
|---|---|---|---|---|---|---|
| Qwen3.8, thinking | 1.0 | 0.95 | 20 | 0 | 0 | 1.0 |
| Qwen3.8 and Qwen3.6, non-thinking (instruct) | 0.7 | 0.80 | 20 | 0 | 1.5 | 1.0 |
| Qwen3.6, thinking, general tasks | 1.0 | 0.95 | 20 | 0 | 1.5 | 1.0 |
| Qwen3.6, thinking, precise coding (for example WebDev) | 0.6 | 0.95 | 20 | 0 | 0 | 1.0 |

- Qwen ran its agentic coding benchmarks (SWE-bench, Terminal-Bench, Claude Code harness) at temperature 1.0 and top_p 0.95 for both models.
- The card tells that a presence of 0–2 decreases endless repetition. But higher values can cause language mixing.

### What actually runs

| Situation | Sampling | Source |
|---|---|---|
| Thinking on, any llama.cpp model (normal use) | 1.0 / 0.95 / 20 / 0 / presence 0 / repetition 1.0 | `serve-llama.sh` sets these values explicitly. `TEMP`, `TOP_P`, `TOP_K`, `MIN_P`, `PRESENCE` and `REPEAT` override them, and so do the `models.<name>` keys in `config.json`. The clients send no sampling fields: the capture proxy showed only `model`, `stream`, `store` and `reasoning_effort`. |
| OpenCode `none` variant (thinking off) | 0.7 / 0.80 / 20 / 0 / presence 1.5 | The variant sends `temperature`, `top_p` and `presence_penalty` with `reasoning_effort: none` (`client/carl_models.py`). OpenCode sends variant options in the request body. The same mechanism carried `chat_template_kwargs` in the 2026-09-24 captures. These fields were not captured again yet. |
| Pi `off` (thinking off) | the thinking-mode values | Pi changes only `chat_template_kwargs` for each level. It cannot change the sampling for each level. The effect is small: direct answers are a little more random. |

- **Reason for the explicit flags:** the five catalogue GGUFs that were checked contain 1.0 / 20 / 0.95 (`general.sampling.*`). The flags also cover the other catalogue builds and custom models, which were not checked. A GGUF without these values would get the generic llama.cpp defaults with no warning (temperature 0.8, top_k 40, min_p 0.05).
- **The 35B-A3B choices:** temperature 1.0 with presence 0 agrees with the Qwen agentic benchmark settings.
  - If the model repeats text in a loop, start the server with `PRESENCE=1.5` (the general value of the card).
  - For precise code work, use `TEMP=0.6`.
  - These two settings were not compared here.
- NOTE: the presence 1.5 of the non-thinking variant is the Qwen value for that mode.

### Output limits

| | llama.cpp |
|---|---|
| Server limit | None by default (`n_predict -1`): the model writes until it stops or the context is full. |
| OpenCode | The config sets `limit.output` = half the window, at most 32,000. The capture proxy showed no `max_tokens` in the request. |
| Pi | Sends `max_tokens` = `maxTokens`. The config sets it to half the window, at most 32,768. |

---

## Context limits: server vs client

- **The server setting `--ctx` is the real limit.**
- **The client limit only decides when the client compacts.** The client limits are `limit.context` in OpenCode and `contextWindow` in Pi. The clients do not send these values to the server.
- **Client limit above the server limit:** when the session is larger than the server window, each request fails with `HTTP 400 exceed_context_size_error`. The client never compacts by itself.
- **Server limit above the client limit:** this causes no problem. The extra memory is not used.

**`client/install.sh` keeps the two limits the same:**
- Each installed model gets the window of its own settings (`installed-models.json`).
- The model that runs now gets its limit from `LLAMA_CTX`, then from `/props` → `default_generation_settings.n_ctx` of the server, then 96K (98304).
- After you change `--ctx`, run `install.sh` again. Then restart the client.

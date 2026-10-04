# CARL Reference: Thinking

[Index](../REFERENCE.md) · how thinking works, by model, by client, the full matrix.

## How thinking works

Qwen models write hidden reasoning between `<think>` and `</think>`, before the answer. The **chat template** is a Jinja program in the model file. It decides if that block opens. It uses variables that the server gives to it:

| Template variable | Meaning |
|---|---|
| `enable_thinking` | `false` → the template writes an empty, closed `<think></think>`, and the model answers directly. Not set or `true` → thinking on. |
| `reasoning_effort` | Qwen3.8 27B only: sets how much the model thinks. The Qwen3.6 35B and the 9B do not use it. |
| `preserve_thinking` | `true` → the reasoning of earlier turns stays in the prompt. The prompt cache needs this. |

llama.cpp gives these request fields to the template:
- the top-level `reasoning_effort`
- each field in `chat_template_kwargs`
- the server-wide `--chat-template-kwargs '{"preserve_thinking":true}'`

`--reasoning-format deepseek` moves the thinking text into the `reasoning_content` field of the response.

**The patch.** OpenCode can send only `reasoning_effort`. The Qwen3.8 template has no effort value that means "off". For this reason, `host/serve-llama.sh` extracts the template from the GGUF (`host/gguf-chat-template.py`) and adds one rule at the start:

```jinja
{%- if reasoning_effort is defined and reasoning_effort in ('none', 'minimal', 'off', 'disable', 'disabled') %}{%- set enable_thinking = false %}{%- endif %}
```

- The rest of the template does not change, with one exception: a Qwen template without `preserve_thinking` (the 9B) gets it ([Whether a state fits](cache.md#whether-a-state-fits)).
- The patched copy is cached as `~/models/templates/<model>.thinking-toggle.jinja`.
- The launcher makes it again when the GGUF or the script is newer than the copy.
- A model whose template has no `enable_thinking` gets its stock template.
- `THINK_TOGGLE=0` (or `llama.think_toggle = false`) turns the patch off.

---

## Thinking by model

| | Qwen3.8-27B (stock unsloth; abliterated orcarouter) | Qwen3.6-35B-A3B (stock unsloth; Heretic) | Qwen3.8-9B Distill |
|---|---|---|---|
| Catalogue `thinking` | `effort` | `on-off` | `on-off` |
| On/off switch | `enable_thinking` | `enable_thinking` | `enable_thinking` |
| Effort levels | `low`, `medium`, `xhigh` | none: the template ignores each `reasoning_effort` value | none |
| Default if the request sends nothing | thinking on, effort `xhigh` | thinking on | thinking on |
| Effort value that is not valid (for example `high`, or `none` without the patch) | **the template raises an error**, and the request fails | ignored | ignored |
| Measured (the same prompt, "Is 91 prime?") | `low` 32–39 reasoning chars, `xhigh` 115–165, off 0 | low 1027, high 861, xhigh 984, off 0 | not measured |
| `preserve_thinking` | supported | supported | added by the patch |

- The stock Qwen3.8-27B template was not checked. CARL assumes that it is the same as the template of the abliterated build.
- **Abliteration does not change thinking.** It removes refusals.
- **Qwen3.8 thinks too much at `xhigh`** on simple tasks. `low` is the default for agent work.
- A custom model gets `effort` when its GGUF template has `reasoning_effort`, else `on-off`. Its card can set `thinking`.

---

## Thinking by client

### What each client sends

| Client → server | Config mechanism | Thinking on | Thinking off |
|---|---|---|---|
| OpenCode → llama.cpp | `@ai-sdk/openai-compatible`, `options.reasoningEffort` and variants | top-level `reasoning_effort: "<level>"` | top-level `reasoning_effort: "none"` (patched template → `enable_thinking=false`) |
| Pi → llama.cpp | `thinkingFormat: "chat-template"` and `chatTemplateKwargs` | `chat_template_kwargs {enable_thinking: true, preserve_thinking: true, reasoning_effort: "<level>"}` | `chat_template_kwargs {enable_thinking: false, preserve_thinking: true}` (no effort value) |

**OpenCode precedence:** provider options → model `options` → agent `options` → **variant**.
- OpenCode merges the variant last, so the variant has priority.
- If the variant name is unknown (an old config), OpenCode uses the base options of the model. It gives no warning.
- For this reason, fully restart OpenCode after `install.sh`.

---

## The full thinking matrix

What you select → what occurs. The provider is `llamacpp` (or `carl` when you have your own `llamacpp` provider). The model ids are the CARL names (`client/carl_models.py`).

### OpenCode (`/variants`, ctrl+t)

| Model type | Selection | Sent | Result |
|---|---|---|---|
| `effort` (for example `llamacpp/qwen3.8-27b`, `llamacpp/orcarouter-27b`) | `none` | `reasoning_effort: none` | **off** (0 reasoning) |
| | `low` (default) | `low` | short thinking |
| | `medium` | `medium` | medium |
| | `xhigh` | `xhigh` | long thinking |
| `on-off` (for example `llamacpp/qwen3.6-35b-a3b`, `llamacpp/qwen3.8-9b`) | `none` | `none` | **off** |
| | `high` (default) | `high` | on (the template ignores the level) |

- `effort` models: `minimal` and `high` are disabled. `minimal` would only repeat `none` (off). `high` is not a Qwen3.8 level (the template raises an error).
- `on-off` models: `minimal`, `low`, `medium` and `xhigh` are disabled, because they have no different effect.
- The `none` variant also sends Qwen's sampling values for thinking off ([Sampling](sampling.md#what-actually-runs)).

### Pi (thinking level)

| Model type | Levels offered | `off` | Other levels |
|---|---|---|---|
| `effort` | off, low, medium, xhigh | `enable_thinking: false` → off | sent as `chat_template_kwargs.reasoning_effort` |
| `on-off` | off, high | off | `high` = on (the level is ignored) |

The Pi `thinkingLevelMap` hides levels with `null`. `minimal` is hidden for all models.

### Raw API (curl, scripts)

| Goal | llama.cpp |
|---|---|
| Thinking off | `"reasoning_effort":"none"` (patched) or `"chat_template_kwargs":{"enable_thinking":false}` |
| Set a level (Qwen3.8 27B) | `"reasoning_effort":"low"\|"medium"\|"xhigh"` |
| Do not use | `"reasoning_effort":"high"` on Qwen3.8 27B (template error) |

**Timing:** a change applies from the next message. A reply that is in progress keeps its mode.

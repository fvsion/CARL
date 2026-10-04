# CARL Reference: Thinking

[Index](../REFERENCE.md) · how thinking works, by model, by client, the full matrix.

## How thinking works

Qwen models write hidden reasoning between `<think>` and `</think>`, before the answer. The **chat template** is a Jinja program in the model file. It decides if that block opens. It uses variables that the server gives to it:

| Template variable | Meaning |
|---|---|
| `enable_thinking` | `false` → the template writes an empty, closed `<think></think>`, so the model answers directly. Not defined or `true` → thinking on. |
| `reasoning_effort` | Qwen3.8 only: sets how much the model thinks. Qwen3.6 does not have this variable. |
| `preserve_thinking` | `true` → reasoning from earlier turns stays in the prompt (necessary for prompt-cache reuse) |

llama.cpp gives these request fields to the template:
- top-level `reasoning_effort`
- all fields in `chat_template_kwargs`
- the server-wide `--chat-template-kwargs '{"preserve_thinking":true}'`.

`--reasoning-format deepseek` moves the thinking text into the `reasoning_content` field of the response.

**The patch.** OpenCode can send only `reasoning_effort`. The Qwen3.8 template has no effort value that means "off". For this reason, `host/serve-llama.sh` extracts the template from the GGUF (`host/gguf-chat-template.py`). It then adds one rule at the start. The rest of the template does not change:

```jinja
{%- if reasoning_effort is defined and reasoning_effort in ('none', 'minimal', 'off', 'disable', 'disabled') %}{%- set enable_thinking = false %}{%- endif %}
```

The patched copy is cached as `~/models/templates/<model>.thinking-toggle.jinja`. It is generated again when the GGUF or the script changes. Models with no `enable_thinking` in their template get their stock template.

---

## Thinking by model

| | Qwen3.8-27B (abliterated orcarouter; stock unsloth) | Qwen3.6-35B-A3B (stock unsloth) |
|---|---|---|
| On/off switch | `enable_thinking` | `enable_thinking` |
| Effort levels | `low`, `medium`, `xhigh` | none: the template ignores all `reasoning_effort` values |
| Default if the request sends nothing | thinking on, effort `xhigh` | thinking on |
| Invalid effort (e.g. `high`, or `none` without the patch) | **template raises an error**, and the request fails | ignored |
| Measured (same prompt, "Is 91 prime?") | `low` 32–39 reasoning chars, `xhigh` 115–165, off 0 | low 1027, high 861, xhigh 984, off 0 |
| `preserve_thinking` | supported | supported |

- The stock Qwen3.8-27B (`qwen3.8-27b`) was not downloaded or checked. Its template is assumed to be the same as the template of the abliterated build.
- **Abliteration does not change thinking.** It removes refusals.
- **Qwen3.8 thinks too much at `xhigh`** on simple tasks. `low` is the default for agent work.

---

## Thinking by client

### What each client sends

| Client → server | Config mechanism | Thinking on | Thinking off |
|---|---|---|---|
| OpenCode → llama.cpp | `@ai-sdk/openai-compatible`, `options.reasoningEffort` + variants | top-level `reasoning_effort: "<level>"` | top-level `reasoning_effort: "none"` (patched template → `enable_thinking=false`) |
| Pi → llama.cpp | `thinkingFormat: "chat-template"` + `chatTemplateKwargs` | `chat_template_kwargs {enable_thinking: true, preserve_thinking: true, reasoning_effort: "<level>"}` | `chat_template_kwargs {enable_thinking: false, preserve_thinking: true}` (no effort value) |

**OpenCode precedence:** provider options → model `options` → agent `options` → **variant**.
- OpenCode merges the variant last, so the variant has priority.
- If the variant name is unknown (old config), OpenCode uses the base options of the model. It gives no warning.
- For this reason, fully restart OpenCode after `install.sh`.

---

## The full thinking matrix

What you select → what occurs.

### OpenCode (`/variants`, ctrl+t)

| Model entry | Selection | Sent | Result |
|---|---|---|---|
| `llamacpp/qwen3.8-27b-abliterated-llama` | `none` | `reasoning_effort: none` | **off** (0 reasoning) |
| | `low` (default) | `low` | short thinking |
| | `medium` | `medium` | medium |
| | `xhigh` | `xhigh` | long thinking |
| `llamacpp/qwen3.6-35b-a3b` | `none` | `none` | **off** |
| | `high` (default) | `high` | on (the template ignores the level) |

`minimal` and `high` are disabled for the Qwen3.8 entries. `minimal` would only repeat `none` ("off"). `high` is not a Qwen3.8 level (the template raises an error). For the 35B, `low`, `medium` and `xhigh` are disabled, because they have no different effect.

### Pi (thinking level)

| Model | Levels offered | `off` | Other levels |
|---|---|---|---|
| `qwen3.8-27b-abliterated-llama` | off, low, medium, xhigh | `enable_thinking: false` → off | sent as `chat_template_kwargs.reasoning_effort` |
| `qwen3.6-35b-a3b` | off, high | off | `high` = on (level ignored) |

The Pi `thinkingLevelMap` hides levels with `null`. `minimal` is hidden for all models.

### Raw API (curl, scripts)

| Goal | llama.cpp |
|---|---|
| Thinking off | `"reasoning_effort":"none"` (patched) or `"chat_template_kwargs":{"enable_thinking":false}` |
| Set a level (Qwen3.8) | `"reasoning_effort":"low"\|"medium"\|"xhigh"` |
| Avoid | `"reasoning_effort":"high"` on Qwen3.8 (template error) |

**Timing:** A change applies from the next message. A reply that is in progress keeps its mode.

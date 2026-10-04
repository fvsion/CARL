# CARL Reference: OpenCode config

[Index](../REFERENCE.md) · the OpenCode and Pi configs that CARL writes.

## OpenCode config

The installer writes these settings into `~/.config/opencode/opencode.json` (template: `client/opencode/opencode.json`). It makes a backup first, and it changes only CARL's own entries (`client/configure.py`).

### The provider and the models

| Item | Value |
|---|---|
| Provider id | `llamacpp`, or `carl` when you have your own provider `llamacpp` |
| Provider package | `@ai-sdk/openai-compatible`, base URL `http://HOST:PORT/v1`, the key from `~/.config/carl/api-key` |
| Models | One entry for each installed model, under its CARL name (`client/carl_models.py`) |
| Context (`limit.context`) | The running model: the server window. The other models: the window of their own settings. |
| Output (`limit.output`) | Half the window, at most 32,000 |
| Model options | `reasoningEffort` (the default variant) and `parallel_tool_calls: true` |
| `model`, `small_model` | The model that a server start loads. The installer sets them only when they are not set, or still have CARL's earlier value. |

| Thinking type | Variants (`/variants`) | Default |
|---|---|---|
| `on-off` (Qwen3.6 35B-A3B, Heretic, 9B) | `none` (off), `high` (on) | `high` |
| `effort` (Qwen3.8 27B, orcarouter) | `none` (off), `low`, `medium`, `xhigh` | `low` |

[Thinking](thinking.md#thinking-by-client) gives what each variant sends.

- `parallel_tool_calls: true`: llama.cpp lets the model call more than one tool in a turn only when the request asks for it. Measured 2026-10-03: 2 reads in one turn with it, 1 without it.
- **Timeouts:** the provider sets `timeout: false` and `chunkTimeout: 900000` (15 min). A long cold prompt can take many minutes before the first token.
- **Title agent:** the title agent of OpenCode stays on (earlier versions turned it off). With 2 slots, it runs at the same time as the main session. It does not wait in a queue behind the main session. The prompt cache sends title and summary requests with thinking off.

### Plugins and agents

| Item | Where | What it does |
|---|---|---|
| `carl-cache` | `plugin` in `opencode.json` | The disk prompt cache ([The disk prompt cache](cache.md)). `NO_CACHE=1` leaves it out. |
| `carl-model-check` | `plugin` in `opencode.json` | Warns when the selected model is not the one that the server runs, is not installed, or loads. `NO_MODEL_CHECK=1` leaves it out. |
| `subagents-sidebar` | `~/.config/opencode/tui.json` | A live list of the subagents. `NO_SIDEBAR=1` leaves it out. |
| `session-switcher` | `~/.config/opencode/tui.json` | A session switcher in the prompt box, and `/switch`. `NO_SWITCHER=1` leaves it out. |
| `carl-panel` | `~/.config/opencode/tui.json` | The `/carl` panel ([Clients on other computers](client-sync.md)) |
| `coder` agent | `agent` in `opencode.json` | [The coder subagent](models.md#the-coder-subagent) |
| `browser` agent | `agent` in `opencode.json` | The Playwright MCP tools (`carl-browser`). They are off for the main agents and on for this subagent. `NO_BROWSER=1` leaves it out. |

To update the plugins, run `install.sh` again and restart OpenCode.

### Tool switches

OpenCode reads some tool switches only from the environment. The installer writes them to `~/.config/carl/opencode.env`. A marked 3-line block in `~/.zshrc` and `~/.bashrc` (when they exist) reads that file. `NO_PROFILE=1` leaves the shell profiles unchanged.

| Switch | Default | Change it with |
|---|---|---|
| Web search (`OPENCODE_ENABLE_EXA` or `OPENCODE_ENABLE_PARALLEL`, `OPENCODE_WEBSEARCH_PROVIDER`) | Exa. The queries leave this computer. | `WEB_SEARCH=exa\|parallel\|off` |
| Background subagents (`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS`) | On | `NO_BACKGROUND_SUBAGENTS=1` |
| LSP tool (`OPENCODE_EXPERIMENTAL_LSP_TOOL`, and `"lsp": true` in the config) | On. OpenCode then downloads and runs language servers. | `NO_LSP=1` |

### Tools and the prompt budget

Measured in Phase 8 (2026-10-03, OpenCode 1.18.34, the template and the tokenizer of Qwen3.6 35B-A3B IQ3). The values are the tokens of the system prompt and the tool definitions in the first request. `tools/req-capture-proxy.py --bodies DIR` captured the full request. `tools/prompt-size.py BODY --per-tool` counted it (`/apply-template` and `/tokenize`).

| Setup | System + tools | Note |
|---|---|---|
| As installed before Phase 8 | 8,564 | 11 tools: bash 1,336 · task 1,084 · todowrite 659 · read 477 · edit 462 · question 394 · webfetch 329 · grep 311 · glob 266 · write 250 · skill 165; system 2,634 |
| + web search (`OPENCODE_ENABLE_EXA`) | +474 | `websearch`. The gate is `providerID opencode` or this flag. |
| + LSP tool (`OPENCODE_EXPERIMENTAL_LSP_TOOL`) | +577 | It does nothing without `"lsp": true` in the config (1.18 starts no language server by default). |
| + background subagents | +97 | The `background` parameter of the task tool |
| + plan mode | 0 | Its tool is for the `cli` client only. |
| + Playwright MCP in the main agent | +4,779 | 26 tools: thus the browser subagent (its own prompt: 7.5K) |
| `experimental.batch_tool` | 0 | Not in the registry of 1.18.34. Parallel tool calls are used instead. |
| **As installed by Phase 8** | **9,870** | 13 tools and the descriptions of the browser and coder subagents. Target: less than 10.5K. |

- The tool registry of OpenCode 1.18.34: question (TUI clients or `OPENCODE_ENABLE_QUESTION_TOOL`), bash, read, glob, grep, edit, write, task, webfetch, todowrite, websearch (provider gate), skill, apply_patch (GPT models only), `lsp` (flag), `plan` (flag, cli client) and code mode (flag).
- OpenCode sends the model options in the request body as they are.
- Measure again after an OpenCode update.

## Pi config

The installer writes `~/.pi/agent/models.json` (template: `client/pi/models.json`) and `~/.pi/agent/settings.json`.

| Item | Value |
|---|---|
| Provider | `llamacpp` (or `carl`), API `openai-completions`, the key from `~/.config/carl/api-key` |
| Thinking format | `thinkingFormat: "chat-template"` with `chatTemplateKwargs` ([Thinking by client](thinking.md#thinking-by-client)) |
| Models | One entry for each installed model: `contextWindow` = its window, `maxTokens` = half the window, at most 32,768 |
| Defaults in `settings.json` | `defaultProvider` = CARL's provider, `defaultModel` = the model that a start loads, `defaultThinkingLevel` = `low`. The installer sets them only when they are not set or still have CARL's earlier values. |
| Tools | `grep`, `find` and `ls` on (`defaultTools`, unless you set your own) |
| Web search | The MCP server `carl-web-search` in `~/.pi/agent/mcp.json` (`WEB_SEARCH` as for OpenCode) |
| Browser | The MCP server `carl-browser` in `mcp.json`, loaded when the model needs it. `NO_BROWSER=1` leaves it out. |
| Extensions | `carl-cache`, `carl-panel`, `subagent` (with the coder) |

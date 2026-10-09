# CARL Reference: OpenCode and Pi configs

[Index](README.md) · the OpenCode and Pi configs that CARL writes.

## OpenCode config

The installer writes these settings into `~/.config/opencode/opencode.json` (template: `client/opencode/opencode.json`). It makes a backup first (`FILE.before-carl` once, then one `FILE.bak` with the version before the last change), and it changes only CARL's own entries (`client/configure.py`). With `~/.config/opencode` behind a symbolic link, the plugin paths are written with the link resolved.

### The provider and the models

| Item | Value |
|---|---|
| Provider id | `llamacpp`, or `carl` when you have your own provider `llamacpp` |
| Provider package | `@ai-sdk/openai-compatible`, base URL `http://HOST:PORT/v1`, the key from `~/.config/carl/api-key` |
| Models | Single-model mode: only the model that the server runs. Router mode: one entry for each installed model. Each entry has its CARL name (`client/carl_models.py`). |
| Context (`limit.context`) | The running model: the context of the server. The other models (router mode): the context of their own settings. |
| Output (`limit.output`) | Half the context, at most 32,000 |
| Model options | `reasoningEffort` (the model's **Main thinking**: `none` for off, `high` for on, or the level) and `parallel_tool_calls: true`. A variant that you select is stronger than it. |
| `model`, `small_model` | The model that the server runs now, when it runs a single model that is in the installed list. Else (and in router mode), the model that a server start loads (`llama.model`, or Auto fit's choice). `default_model()` in `client/configure.py`. The installer sets them only when they are not set, or still have CARL's earlier value. |

### The model list: single-model mode and router mode (Phase 23.4.4)

In single-model mode, the server answers with its one model, whatever model a request names. Thus the OpenCode and Pi configs list only that model, not all the installed models.

- On the server Mac, `tools/carl.py client-models --running NAME` writes `installed-models.json` with only the model that the server runs (NAME: the `model_alias` in the server's `/props`). With no server, the list has the model that a server start loads. When CARL cannot find that model, the list has all the installed models.
- `client/configure.py --running NAME` also keeps only NAME when the list has it (`only_running()` in `client/carl_models.py`). Thus an older list with all the models also gives one entry.
- Router mode (`role: router` in `/props`, or `llama.mode = router` with no server): all the installed models, as before (`client-models --router`).
- When the server starts a different model, the configs are out of date. The dashboard says so on the Connect tab (Setup, This Mac: the client list against the model of the server; the tab label gets ⚠). Press **u** to write the configs on this Mac again. For the other computers, the Connect tab says that the last config sent is out of date: press **P** to send the new one. The sync service applies it. The client package (**z**) has the model that the server runs when you make it.

| Thinking type | Variants (`/variants`) | Default (Main thinking not changed) |
|---|---|---|
| `on-off` (Qwen3.6 35B-A3B, Heretic, 9B, Gemma 4) | `none` (off), `high` (on) | `high` |
| `effort` (Qwen3.8 27B, orcarouter) | `none` (off), `low`, `medium`, `xhigh` | `low` |

### Thinking per role (Phase 23.4.4)

The dashboard has two thinking settings for each model (Settings > Agents): **Main thinking** and **Coder thinking** (`models.NAME.thinking_main`, `models.NAME.thinking_coder` in `config.json`). They travel to the clients in `installed-models.json` (`thinking_main`, `thinking_coder` for each model), as the context does: the setup on this Mac, the push and the sync on other computers, and the client package. A list without them (from an older server) gets the defaults.

| | Values | Default | OpenCode | Pi |
|---|---|---|---|---|
| Main thinking | off, on; `effort` models: off, low, medium, xhigh | on; `effort` models: low | the model entry's `options.reasoningEffort` | `modelThinkingLevels` in `settings.json`, only for a value that is not the default |
| Coder thinking | `main` (shown as "same as main"), and the values of Main thinking | `main` | `coderThinking` in the options of `carl-delegation`: `{"PROVIDER/MODEL": EFFORT}`. The plugin's `chat.params` hook sets `reasoningEffort` on each request of the coder, for the model that the coder runs on. The coder agent has no `reasoningEffort`. | `"thinking": {"coder": {"PROVIDER/MODEL": LEVEL}}` in `~/.pi/agent/carl.json`; the subagent extension passes `--thinking LEVEL` when it starts the coder |

- `main` (the default of Coder thinking): the coder thinks as the main session does with the model. The model has no entry in `coderThinking` or in `carl.json` `"thinking"`. OpenCode sends the request of the coder with the model's own `reasoningEffort` (Main thinking). Pi passes the thinking level of the session, as before Phase 23.4.4.
- The coder's thinking follows the model that the coder runs on, in both clients. The test session and the code session of the chain are coder sessions, so they use Coder thinking too.
- Main thinking keeps what CARL did before: OpenCode's model option `high` / `low`; Pi's `defaultThinkingLevel` `low`. One change in OpenCode: the coder agent had a fixed `reasoningEffort` `medium` for every model. With `main`, the coder now thinks as the main session (on the 27B: `low`).
- Off: OpenCode `none` (CARL's template patch turns thinking off), Pi `off`. Only thinking changes: the sampling stays the server's, and the coder's temperature stays 0.6.
- On: OpenCode `high` for an on / off model; on an `effort` model, "on" is the role's level (Main thinking `low`, Coder thinking `medium`). Pi: `high`, the level that an on / off model offers.
- A `modelThinkingLevels` entry that you changed in Pi stays. The installer changes only an entry that still has the value that it wrote (`model_thinking` in `~/.pi/agent/carl.json`).
- How `chat.params` was checked (2026-10-09, OpenCode 1.18.34): the real OpenCode against agent-bench's fake server (`tools/agent-bench/fakeserver.py`) in a temporary HOME. A plugin set `output.options.reasoningEffort` to `none` for the coder agent. The request body of the coder had `reasoning_effort: "none"`; the request of the main agent kept the model's `low`.

[Thinking](thinking.md#thinking-by-client) gives what each variant sends.

#### An external coder model (Phase 23.4.5)

`/carl`'s Coder model (`CODER_MODEL=PROVIDER/MODEL` in `~/.config/carl/client-install.env`; `main` or no line: the main session's model) is a setup switch like the others. `install.sh` passes it to `configure.py` (`--coder-model`, and `--coder-model-thinking` for `CODER_MODEL_THINKING`):

| | OpenCode | Pi |
|---|---|---|
| The coder's model | the coder agent's `"model": "PROVIDER/MODEL"` in `opencode.json` | `"coder_model": "PROVIDER/MODEL"` in `~/.pi/agent/carl.json`; the `subagent` tool passes `--model` |
| CARL's sampling | the coder agent has no `"temperature"` (with `main`: 0.6) | none (Pi's coder never had one) |
| Its thinking | `carl-delegation`'s options `coderModel` and, with a value, `coderVariant` (a variant of that model) | `"thinking": {"coder": {"PROVIDER/MODEL": LEVEL}}` in `carl.json` (only with a value) |
| The state file | `"coder_model"` (`main` when none) | `"coder_model"` (`main` when none) |

- The setup writes only the model's name. The provider and its key are the client's own: CARL adds no provider for it and reads no key.

`/carl`'s Tests (Phase 23.4.6: `CODER_TESTS=before|after|off` in `client-install.env`; `before` or no line: the default) goes to `configure.py` as `--coder-tests`. Both state files get `"coder_tests"`; OpenCode's `carl-delegation` gets the option `"stateFile"` (the path of its `carl.json`) and reads the value from there at each coder task, as Pi's `subagent` tool reads its own `carl.json`. `carl-sync.py set CODER_TESTS=...` therefore asks neither client to restart ([The Tests setting](delegation.md#the-tests-setting)).
- The setup's coder rule: with `CODER_MODEL` set, `auto` turns the coder on also with a 1-slot server ("The coder runs on PROVIDER/MODEL, not on the server."). `NO_CODER=1` still turns it off.
- One switch for the computer: both clients get the same `PROVIDER/MODEL`. A client that does not know that model fails the coder's task with its error.

#### /carl's Coder thinking on one computer

`/carl` > **Coder subagent** > **Coder thinking** overrides the dashboard's Coder thinking on one computer, for one model (user, 2026-10-09: "/carl overrides the dashboards default for that computer only"; stored per model).

- It is a setup switch: `CODER_THINKING=MODEL:VALUE,MODEL:VALUE` in `~/.config/carl/client-install.env` (VALUE: `main`, `on`, `off`, `low`, `medium`, `xhigh`). `carl-sync.py set CODER_THINKING=MODEL:VALUE` changes one model's entry and keeps the others; `set CODER_THINKING=MODEL:default` (`/carl`'s `dashboard default`) removes the model's entry, so the dashboard's value applies again (no entry left: the line goes). `./setup` and each sync apply it again, as the other switches. `CODER_THINKING=… ./setup` also works.
- `install.sh` passes it to `configure.py` (`--coder-thinking`). `configure.py` puts each entry over the dashboard's `thinking_coder` of that model (from `installed-models.json`) before it writes `coderThinking` (OpenCode) and `"thinking": {"coder": …}` (Pi). A model that the entry does not name keeps the dashboard's value. An entry that is not a model name and a value is left out. A level on an on / off model is `on` there, as in the dashboard.
- Both state files (`~/.config/opencode/carl.json`, `~/.pi/agent/carl.json`) get `"models"`: for each model that the client has, `{"thinking": KIND, "main": VALUE, "coder": VALUE, "dashboard": VALUE}`: main and coder as written (the override merged), and `dashboard`, the dashboard's coder value without the override (what `dashboard default` gives). The `/carl` panel reads its Coder thinking row from it, and this computer's entries from `client-install.env`.
- Which model's entry `/carl` sets: the model that the coder runs on. With Coder model `same as main`, the model of the session `/carl` is opened in, else the config's default model ([the panel](plugins.md#carl-panel-the-carl-panel-opencode-and-pi)). The coder really runs on the session's model in both clients: OpenCode's task tool starts a subagent with no model of its own on the model of the calling message (`agent.model ?? {modelID, providerID}` of the main agent's message, read in OpenCode 1.18.35), and with its variant; Pi's `subagent` tool passes `--model PROVIDER/MODEL` of `ctx.model` to an agent with no model. Checked with the real clients against agent-bench's fake server (2026-10-09; a temp HOME made by `configure.py`, two models, the default model A): a session on model B gave the coder's requests model B in OpenCode 1.18.35 and Pi 1.1.0; with `--coder-thinking B:xhigh` the coder's request had `reasoning_effort` `xhigh` (Pi: `chat_template_kwargs.reasoning_effort`), and a session on model A gave the coder A's own thinking (same as main).
- OpenCode must restart to use a change (the plugin reads its options when it starts). Pi reads `carl.json` each time it starts the coder.

- The `none` variant of a Qwen model also sends Qwen's sampling for thinking off (temperature 0.7, top_p 0.8, presence penalty 1.5, from the Qwen model cards). Gemma 4 and the custom models use one sampling for all uses, so their `none` variant only turns thinking off (`off_sampling` in `installed-models.json`: `qwen` or `same`).

- `parallel_tool_calls: true`: llama.cpp lets the model call more than one tool in a turn only when the request asks for it. Measured 2026-10-03: 2 reads in one turn with it, 1 without it.
- **Timeouts:** the provider sets `timeout: false` and `chunkTimeout: 900000` (15 min). A long cold prompt can take many minutes before the first token.
- **Title agent:** the title agent of OpenCode stays on (earlier versions turned it off). With 2 slots, it runs at the same time as the main session. It does not wait in a queue behind the main session. The disk cache (`carl-cache`) sends title and summary requests with thinking off.

### Plugins and agents

| Item | Where | What it does |
|---|---|---|
| `carl-cache` | `plugin` in `opencode.json` | The disk cache ([Caching](caching.md)). `NO_CACHE=1` leaves it out. |
| `carl-model-check` | `plugin` in `opencode.json` | Warns when the selected model is not the one that the server runs, is not installed, or loads. `NO_MODEL_CHECK=1` leaves it out. |
| `carl-background` | `plugin` in `opencode.json` | Runs CARL's coder in the background ([plugins](plugins.md#carl-background-the-coder-in-the-background-opencode)). It comes with the coder. `NO_BACKGROUND_SUBAGENTS=1` leaves it out. |
| `carl-delegation` | `plugin` in `opencode.json`, with `reminder`, `cacheApi`, `coder` and `coderThinking` | The delegation rule for the main agent only, the reminder, the new-file gate and the coder's thinking per model ([the hand-off to the coder](delegation.md)). It comes with the coder. `NO_REMINDER=1` (or `/carl`) turns the reminder off. The gate is a setting of the dashboard (Connect > Setup); the plugin reads it through `cacheApi`. |
| `/code` | `~/.config/opencode/command/code.md` | Gives a task straight to the coder. It comes with the coder. |
| `subagents-sidebar` | `~/.config/opencode/tui.json` | A live list of the subagents. `NO_SIDEBAR=1` leaves it out. |
| `session-switcher` | `~/.config/opencode/tui.json` | A session switcher in the prompt box, and `/switch`. `NO_SWITCHER=1` leaves it out. |
| `carl-panel` | `~/.config/opencode/tui.json` | The `/carl` panel ([Clients on other computers](client-sync.md)) |
| `coder` agent | `agent` in `opencode.json` | [The coder subagent](models.md#the-coder-subagent) |
| `browser` agent | `agent` in `opencode.json` | The Playwright MCP tools (`carl-browser`). They are off for the main agents and on for this subagent. `NO_BROWSER=1` leaves it out. |

To update the plugins, run the setup again (`./carl.sh install`, or a new client package and `./setup` on other computers) and restart OpenCode.

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
| Models | One entry for each installed model: `contextWindow` = its context, `maxTokens` = half the context, at most 32,768 |
| Defaults in `settings.json` | `defaultProvider` = CARL's provider, `defaultModel` = the same default model as OpenCode's `model`, `defaultThinkingLevel` = `low`. The installer sets them only when they are not set or still have CARL's earlier values. `modelThinkingLevels`: the Main thinking of a model when it is not the default ([Thinking per role](#thinking-per-role-phase-2344)). |
| Tools | `grep`, `find` and `ls` on (`defaultTools`, unless you set your own) |
| Web search | The MCP server `carl-web-search` in `~/.pi/agent/mcp.json` (`WEB_SEARCH` as for OpenCode) |
| Browser | The MCP server `carl-browser` in `mcp.json`, loaded when the model needs it. `NO_BROWSER=1` leaves it out. |
| Extensions | `carl-cache`, `carl-panel`, `subagent` and `carl-delegation` (both with the coder) |
| The coder's thinking | `"thinking": {"coder": {...}}` in `~/.pi/agent/carl.json` ([Thinking per role](#thinking-per-role-phase-2344)) |
| The hand-off | `"delegation": {"reminder": true}` in `~/.pi/agent/carl.json` (the new-file gate comes from the dashboard: `cache_api`) ([the hand-off to the coder](delegation.md)); the delegation rule is the last block of `~/.pi/agent/APPEND_SYSTEM.md` (main agent only) |
| `/code` | `~/.pi/agent/prompts/code.md`: gives a task straight to the coder (with the coder) |

# CARL Reference: The plugins and extensions

[Index](README.md) · every OpenCode plugin and Pi extension that CARL installs: what it does, how it works, its files and its switch.

The setup (`./carl.sh install` on the server Mac, `./setup` in the client package on another computer) copies these pieces and registers them. It keeps your own plugins and extensions. A piece of yours with the same name stays, and CARL does not install its own.

Type `/carl` in OpenCode or Pi to turn each piece on this computer on or off.

## Overview

| Piece | Client | Kind | What you get | Switch |
|---|---|---|---|---|
| `carl-cache` | OpenCode, Pi | server plugin / extension | The disk cache: fast starts, sessions back after a restart | `NO_CACHE=1` |
| `carl-model-check` | OpenCode | server plugin | A warning when the model that you select is not the model that the server runs | `NO_MODEL_CHECK=1` |
| `carl-background` | OpenCode | server plugin | The coder runs in the background | `NO_BACKGROUND_SUBAGENTS=1` |
| `carl-delegation` | OpenCode, Pi | server plugin / extension | The hand-off to the coder: the delegation rule for main agents only (OpenCode), the reminder, the brief check, the coder's gates, the new-file gate; in OpenCode also the chain (tests first, then the code) and the coder's thinking per model | comes with the coder; `NO_REMINDER=1` or `/carl`; the gate: the dashboard (Connect > Setup) |
| `subagent` | Pi | extension | The `subagent` tool: the coder and other agents, also in the background; the chain (tests first, then the code) | comes with the coder (`NO_CODER=1`) |
| `subagents-sidebar` | OpenCode | TUI plugin | The Subagents panel in the sidebar | `NO_SIDEBAR=1` |
| `session-switcher` | OpenCode | TUI plugin | `‹ 2/3 ● title ›` in the prompt box, `/switch` | `NO_SWITCHER=1` |
| `carl-panel` | OpenCode, Pi | TUI plugin / extension | The `/carl` control panel: the switches of the pieces; the config sync | always |

Put a switch in front of the setup, for example `NO_SIDEBAR=1 ./carl.sh install --config-only`. The setup then removes that piece, and keeps it off at the next setup and sync. `NO_SIDEBAR=0` puts it back.

## Where the pieces are

| Client | Folder | Registered in |
|---|---|---|
| OpenCode | `~/.config/opencode/plugins/NAME/` | Server plugins: `plugin` in `~/.config/opencode/opencode.json`. TUI plugins: `plugin` in `~/.config/opencode/tui.json`. |
| Pi | `~/.pi/agent/extensions/NAME/` | Pi loads every folder in `extensions/`. |

- The source is in `client/opencode/plugins/` and `client/pi/extensions/`.
- The code that more than one piece uses is in `client/shared/`: `carl-cache.js` (the disk cache), `carl-panel.js` (the /carl panel), `carl-delegation.js` (the hand-off rules), `carl-brief.js` (the coder's TOML brief, revision 4: the reader, the check, the report), `carl-chain.js` (the chain of a test session and a code session) and `carl-tui.js` (the TUI helpers of the session switcher and the subagents panel). The setup copies each file into each piece that uses it.
- OpenCode and Pi load the pieces when they start. After a setup, restart OpenCode or Pi.
- To get new versions of the pieces on another computer, unzip a new client package over the old folder and run `./setup` again.

## carl-cache: the disk cache

The disk cache saves prompts and sessions on the server's disk, through the server. Then a new session, a restart or a router switch does not mean reading everything again. The pages [Caching](caching.md) and [Caching](caching.md#how-a-saved-state-is-built) give the details.

| | OpenCode | Pi |
|---|---|---|
| Hooks | `chat.headers` marks each request of CARL's provider with the session and the agent. A wrapper around `fetch` changes the marked requests and reads the replies. `event` (`session.idle`) ends the turn marks. | `before_provider_request`, `after_provider_response`, `message_end` (a reply that ends the turn), `agent_end` |
| Agent name | OpenCode's agent (`build`, `plan`, `coder`, …) | `pi`, or `pi-NAME` in a subagent (`CARL_AGENT`) |
| Options | `provider`, `cacheApi` (the plugin entry) | read from `~/.pi/agent/carl.json` |

For each request, the cache does these steps:

1. It puts the parts of the system prompt that change between projects and days (the folder, the date, AGENTS.md) after the shared part: the start of the first user message with Qwen (the model follows AGENTS.md more often there; measured 2026-10-08); with Gemma 4 they stay; with other templates they stay too, unless Settings > Caching > Other templates moves them to the first user message ([Caching](caching.md#before-each-request)).
2. It claims a free slot (a `.claim+MODEL+SLOT` file) and pins the request to it (`id_slot`).
3. It marks the slot's turn as running (a `.turn+MODEL+SLOT` file). The mark stays until the turn ends and its save or record is on disk.
4. If the slot does not hold the session, it puts back the session's file, else the agent's prompt file. If there is no prompt file, it reads the prompt one time and saves it.
5. When the turn ends, it saves the session, or it records which session the slot holds (a `.resident+MODEL+SLOT.json` file). The Save setting decides which (Settings > Caching).

- The claim, turn and record files have the mode 0600. A saved-state name from a record or a patch is used only when it is a plain file name.
- The dashboard's Stop, Apply, Auto-tune and router loads read the turn marks. They can wait for the end of a turn, so that the session is saved whole.
- A client on another computer does the same through the dashboard's API ([Clients on other computers](client-sync.md)).
- **Only CARL's provider** (Phase 23.4.5, the coder on an external model). OpenCode: `chat.headers` marks only a request whose model's provider is CARL's (the plugin's `provider` option); the `fetch` wrapper sends each unmarked request on as it is, and reads the `Authorization` header only of a marked one. Pi: the hooks act only on a model whose provider is in `"providers"` of `~/.pi/agent/carl.json`; only then do they ask Pi for the key (`getApiKeyAndHeaders`). So a request to another provider (an external coder model) is not marked, saved, restored or claimed, and the cache sends nothing to CARL's server or the dashboard API for it. Tested in `tests/js/opencode-plugins.test.mjs` and `tests/js/pi-cache.test.mjs`.
- `CARL_CACHE=0` turns the cache off for one run. `CARL_CACHE_SAVE=turn|auto|switch|stop` sets the save rule for one client. `CARL_CACHE_LOG=FILE` writes what the cache does to a file, also each failure it lets go (a request never fails because of the cache). The file never holds the key or a request body.

## carl-model-check: the model warning (OpenCode)

Before each request to CARL's provider, the plugin compares the model that you selected with the server. It shows a message (a toast) one time for each situation:

| Mode | The message says |
|---|---|
| Single model | The server runs another model. The server answers with its own model. |
| Router | The model is not installed on the server (HTTP 400), or the model loads now. |

The plugin asks the server for its models at most every 5 s. OpenCode's log has the same lines (`service=carl-model-check`).

It checks only requests to CARL's provider (its `provider` option). A request to another provider, for example the coder on an external model (Phase 23.4.5), gets no check and no message, and the plugin sends no request to CARL's server for it. Tested in `tests/js/opencode-plugins.test.mjs`.

## carl-background: the coder in the background (OpenCode)

OpenCode's task tool waits for a subagent, unless the model asks for the background (`background: true`). A local model often does not ask, even when the delegation rule tells it to. Then the main session waits in its slot while the coder works in the other slot.

- The plugin uses the hook `tool.execute.before`. When the task tool starts CARL's coder (`coder` or `carl-coder`) and the call does not set `background`, the plugin sets `background: true`.
- The model can still ask for the foreground with `background: false`. A call that continues an earlier task (`task_id`) stays as the model asked.
- The coder's result comes back to the main session as a message when the coder ends.
- It needs `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1`, which the setup puts in `~/.config/carl/opencode.env`.
- The setup adds it only with the coder (2 slots or more, or `--coder on`).

## carl-delegation: the hand-off to the coder (OpenCode and Pi)

The rules are in `client/shared/carl-delegation.js` and `client/shared/carl-brief.js` (the setup copies both into the plugin and the extension). The OpenCode plugin also gets `client/shared/carl-chain.js`. The details and the measurements are in [the hand-off to the coder](delegation.md).

- **OpenCode** (server plugin, with the options `reminder`, `cacheApi`, `coder`, `coderThinking`, `coderModel` and `coderVariant` in its `plugin` entry; `"brief": false` turns the brief check off and `"chain": false` the chain, for measurements):
  - `chat.params` (Phase 23.4.4): on each request of CARL's coder (`coder` or `carl-coder`, also the test session and the code session of the chain), it sets `output.options.reasoningEffort` to the value that `coderThinking` (`{"PROVIDER/MODEL": EFFORT}`, the dashboard's Coder thinking) gives for the model of the request. OpenCode sends it as `reasoning_effort` (checked with OpenCode 1.18.34 against agent-bench's fake server). A model with no entry (Coder thinking "same as main"), and every other agent: the request stays as it is ([details](delegation.md#the-coders-thinking)). Tested in `tests/js/opencode-plugins.test.mjs`.
  - `chat.params` with an external coder model (Phase 23.4.5; `coderModel`: `/carl`'s Coder model, the coder agent's `model`): `coderThinking` has no entry for it, so CARL's per-model table does not apply. With `coderVariant` (`/carl`'s Coder thinking for that model), the hook merges that variant of the model (`input.model.variants`, OpenCode's own variants) into `output.options` of the coder's requests to that model. Without it (`model default`), the request stays as it is. The hook never sets a temperature or another sampling value.
  - `experimental.chat.system.transform`: in a subagent's session (a session with a parent), it takes CARL's marked delegation rule out of the system prompt. It changes OpenCode's list in place.
  - `experimental.chat.messages.transform`: it adds the reminder to the end of each user message of a main session.
  - `tool.execute.before`: a task call for the coder (`subagent_type` coder or carl-coder, no `task_id`) whose TOML brief fails the check (the `existing_tests` paths in the project folder too) throws `[CARL] Brief refused` and the points to fix, so the coder does not start. In the coder's own session, a write against its `work_mode` (a test file in `code`, another file in `tests-only`) or of a known file to read only throws `[CARL] Blocked`; any other file passes (the known files are a start, not a limit). When the dashboard sets the gate (`delegation.gate`, read from `config.json` on the server Mac or the dashboard API elsewhere, every 10 s), it stops the main agent's write that makes the Nth new file of a turn (`chat.message` starts a new turn).
  - `chat.message`: in a session of the coder (the message's agent), the first message is the brief: the plugin keeps it for that session's gates.
  - **The chain** ([details](delegation.md#the-chain-tests-first-in-a-separate-session)): `tool.execute.before` makes a coder task with `work_mode = "code"` and `work_type = "new_feature"` the test session (its `prompt` becomes the test brief: `work_mode = "tests-only"`, without `design_notes` and the known files). When the test session ends, the plugin starts the code session through the SDK (`session.create`, `session.promptAsync`) and waits for its end (`session.idle` in the `event` hook, or its messages). The base, on every OpenCode version: the task runs in the foreground (`background: false`), and `tool.execute.after` replaces its output with the one result; when the hold is off, a warning goes to OpenCode's log and a toast to the TUI, once. The background hold, only on the verified versions (1.18.34, 1.18.35; read from the running program's npm package or its `--version`) and after a self-check at load (the SDK methods it needs): `chat.message` holds back the test session's `<task …>` message (it throws `[CARL] Held`, so OpenCode does not save it and the main agent gets no turn), and the one result comes later as the same kind of message. Each held chain has a record in `~/.config/carl/chains/` (mode 0600); at the next start, a chain that OpenCode left undelivered gets what exists. `CARL_TEST_OPENCODE_VERSION` replaces the version that the plugin reads (for tests). With `work_type = "follow_up"` or `"bug_fix"`, the result also says whether the brief's `existing_tests` changed. The Tests setting (Phase 23.4.6; the option `stateFile`, the client's `carl.json` `"coder_tests"`, read at each coder task): `after` makes the task the code session and the plugin's session the test session; `off` leaves the task as it is and adds one line to its result ([The Tests setting](delegation.md#the-tests-setting)).
- **Pi** (extension; the reminder and `"brief"` are in `"delegation"` in `~/.pi/agent/carl.json`, the gate comes from the dashboard): the `input` event adds the reminder to your messages; `tool_call` blocks a subagent call with an incomplete coder brief (the `existing_tests` paths are checked in the call's folder), and blocks with the gate. In the coder's own process (`CARL_AGENT` is the coder's name), `before_agent_start` reads the brief from the prompt and `tool_call` blocks the writes that the gates refuse. In another subagent it does nothing.
- The setup installs it with the coder, and removes it with `--coder off`.

## subagent: the subagent tool (Pi)

Pi has no subagents of its own. CARL installs the `subagent` extension (from Pi's examples, with changes marked `CARL:`).

| Mode | Call | What happens |
|---|---|---|
| Single | `agent`, `task` | One agent does the task in its own `pi` process. |
| Parallel | `tasks: [{agent, task}, …]` | Up to 8 tasks, 4 at a time. |
| Chain | `chain: [{agent, task}, …]` | One after the other; `{previous}` puts in the last result. |
| Background | `background: true` (single, parallel) | The tool returns at once. Each result comes back as a message when its agent ends. |
| The chain | a coder brief with `work_mode = "code"` and `work_type = "new_feature"` (single, background, a chain step) | Two coder processes, one after the other: the test session, then the code session. One result. |

- **CARL's changes:**
  - The tool's description lists the installed agents and when to use each one.
  - The delegation rule goes into Pi's system prompt (`APPEND_SYSTEM.md`), for the main agent only: the tool starts every agent with `--append-system-prompt` (its own prompt, or an empty one), and with that option Pi does not read `APPEND_SYSTEM.md`.
  - An agent file can say `exclude-tools:`. The agent then gets every tool except those, the MCP tools included. The coder says `exclude-tools: subagent, tool_search`: it gets web search when it is installed, but it cannot start nested subagents or load the browser tools.
  - Each subagent's process gets `CARL_AGENT`, so its prompt file has its own name.
  - The coder has no browser. Its report has a "Needs a browser check" part; the main agent starts the app and does the check (OpenCode: the browser subagent; Pi: its own browser tools).
  - **The chain** (`runAgentTask`, with `carl-chain.js` and `carl-brief.js` next to the extension): a coder task with `work_mode = "code"` and `work_type = "new_feature"` runs as two `pi` processes, the test session and then the code session, and gives one result ([details](delegation.md#the-chain-tests-first-in-a-separate-session)). `work_type = "follow_up"` or `"bug_fix"`: one process; the result says whether the brief's `existing_tests` changed. A parallel call with more than one task refuses such a brief. `"delegation": {"chain": false}` in `~/.pi/agent/carl.json` turns it off. The Tests setting (Phase 23.4.6; `"coder_tests"` in the same file, read at each coder task): `after` runs the code process first and the test process after it; `off` runs one process and adds one line to its result ([The Tests setting](delegation.md#the-tests-setting)).
  - **The coder's model** (Phase 23.4.5): with `"coder_model": "PROVIDER/MODEL"` in `~/.pi/agent/carl.json` (`/carl`'s Coder model; `"main"`: none), the tool starts CARL's coder with `--model PROVIDER/MODEL`, in every coder session (the test session and the code session too). Its thinking: the level of that model in `"thinking"` (`/carl` sets it), else no `--thinking` (Pi's own default for the model, `model default`); the session's level does not apply to it. When that provider fails, the coder's process ends with the provider's error, and the task fails with it: the tool does not try another model. Tested in `tests/js/pi-chain.test.mjs`.
  - **The coder's thinking** (Phase 23.4.4): the tool starts CARL's coder with `--thinking LEVEL`, the level that `"thinking": {"coder": {"PROVIDER/MODEL": LEVEL}}` in `~/.pi/agent/carl.json` gives for the model of the coder (the dashboard's Coder thinking; [details](delegation.md#the-coders-thinking)). Every coder session gets it, the test session of the chain too. A model without an entry (Coder thinking "same as main", the default), and every other agent: the level of the session, as upstream does. Tested in `tests/js/pi-chain.test.mjs`.
- **Background:**
  - CARL's coder runs in the background unless the call says `background: false`.
  - `/subagents` lists the agents that run in the background, and stops one.
  - The footer shows how many run.
  - When Pi closes, the running agents stop.
  - **The result on the screen:** a background result comes back as a message. The model reads it as it is (`<subagent id="…" agent="coder" state="done" took="42 s">…</subagent>`). The screen shows `✓ Coder finished (42 s)` (`✗ Coder failed (…)`, `■ Coder was stopped (…)`) and the first 3 lines of the result. Ctrl+O shows all of it. Before, the screen showed the `<subagent …>` text. The formatting is in `client/pi/extensions/subagent/result.js` (tested in `tests/js/pi-subagent.test.mjs`).
  - `NO_BACKGROUND_SUBAGENTS=1` turns the background off (`"background_subagents": false` in `~/.pi/agent/carl.json`).

## subagents-sidebar: the Subagents panel (OpenCode)

A TUI plugin. The panel in the sidebar shows the subagents of the session on the screen.

- The running subagents are at the top, oldest first: the agent, the task, the time, the tool that runs now and the context size.
- The finished subagents are below, newest first: ✓ or ✗, the agent, the task and the duration. The panel shows the 5 newest for 5 minutes. A `+N more` line holds the others.
- Click a subagent to see its model. Click it again to open its session. Click the header to collapse the panel.
- The plugin reads the child sessions one time for each parent session. Then it follows OpenCode's events (`session.created`, `session.updated`, `session.status`, `session.idle`, `session.error`).

## session-switcher: the session switcher (OpenCode)

A TUI plugin. OpenCode 1.18 does not show the open sessions as tabs, so the switcher is in the prompt box: `‹ 2/3 ● fix the log parser ›`.

| Part | Meaning |
|---|---|
| `2/3` | This session's place in the list (the newest is first) |
| `●` `!` `○` | Busy; waits for a permission or a question; idle |
| `‹` `›` | The previous or the next session |
| The title | Click it (or type `/switch`) for the list |
| `+1●` | Another session is busy |

The list has the top-level sessions of this project that changed in the last 72 hours (at most 9). It has no subagent sessions.

## carl-panel: the /carl panel (OpenCode and Pi)

Type `/carl`. The panel is a control panel (user, 2026-10-08: no explanations): one list, one row for each CARL piece on this computer, with its label and its state. The state values are short and lower case: `on`, `off`, `exa`, `parallel`, `connected`. The rows keep the order of their groups: the coder, the tools, the side panels, the server, the config sync. The coder's rows are a sub-list under one row, **Coder Subagent** (Phase 23.4.4; user, 2026-10-09: "they should be a sub selection under a top level 'Coder Subagent'"); its state is the coder's `on` / `off`, shown with a `›` (`on ›`: it opens a list; user, 2026-10-09: "the Coder Subagent needs a little right arrow to indicate it expands, the on indicator is good here"). Only this row has the arrow (`arrow` in the row; `shownState()` in `carl-panel.js` gives the text that both clients show).
- **OpenCode** (a TUI plugin): one `DialogSelect`. The label is the row's title, the state is its footer (at the right). The options come from a Solid signal, so a change updates the list in place and the cursor stays. The dialog is OpenCode's medium size (60 columns): every label and state fits. No group headings: OpenCode shows at most half the terminal height minus 6 lines, and the list fits 13 rows on a 40-row terminal.
- **Pi** (an extension): Pi's `SettingsList` (as Pi's `/settings`) in `ctx.ui.custom`, with Pi's own hint line.
- **Enter** (Pi: also **Space**) on a switch runs its action at once: the state reads `turning off…`, then the new state. **Web Search** opens its values, the current one marked, each provider with where the queries go. **Coder Subagent** opens its list: OpenCode a second `DialogSelect` (its title "Coder Subagent"), Pi a new `SettingsList` in the same place. **Esc goes back one list** in both clients (from a list of values to its list, from the Coder Subagent list to the first list); Esc on the first list closes `/carl`. A choice with one value (Coder Model) opens nothing. **Sync Service** opens a read-only view of label rows (OpenCode: a `DialogAlert`). **Check for a New Config** and **Apply the Waiting Config** run at once (`checking…`, `applying…`).
- **Esc in OpenCode** (Phase 23.4.4; user, 2026-10-09: "I would like to be able to go back too"). OpenCode's dialog stack (1.18, read in its TUI code) holds one dialog: `api.ui.dialog.replace(render, onClose)` runs the `onClose` of the dialog shown, then shows the new one; Esc (and ctrl+c) runs the shown dialog's `onClose`, then empties the stack; a click outside the dialog runs `dialog.clear()`, which runs `onClose` too. There is no push. So each list that `/carl` opens from another one gets an `onClose` that shows the parent list again, after OpenCode's Esc is done (a timer), and only when: the key was Esc (the plugin listens to `api.renderer.keyInput` and keeps the time of the last key press when it was Esc; one Esc goes back one list at most), nothing was opened since (`api.ui.dialog.depth` is 0, and `/carl` showed no other dialog: its own `replace()` calls do not count). ctrl+c and a click outside close `/carl` from any list, as they close OpenCode's own dialogs; the first list has no `onClose` action, so Esc there closes `/carl`. Checked with the real OpenCode 1.18.35 in a pty (a temp HOME made by `configure.py`, agent-bench's fake server): Esc from the values of Coder Thinking showed the Coder Subagent list, Esc again the first list, Esc again closed `/carl`; ctrl+c in the values closed it. Tested in `tests/js/carl-panel.test.mjs` with a fake of the same stack.
- The title says `Restart OpenCode to use N changes.` (Pi: `Restart Pi …`) while rows changed since the program started, or a config was applied.
- One action at a time; a second Enter waits.

| Row | Client | State from | Action |
|---|---|---|---|
| Sync Service (a view), Apply the Waiting Config, Check for a New Config | OpenCode, Pi (only with `remote.json`) | `~/.config/carl/client-sync.json` | `carl-sync.py apply`, `carl-sync.py once` |
| Apply New Configs at Once | OpenCode, Pi (only with `remote.json`) | `client-sync.json` `auto_apply` | `carl-sync.py auto on\|off` |
| Coder Subagent (a list) | OpenCode, Pi | the coder agent: its state is Coder's | opens the rows below |
| Coder Subagent > Coder | OpenCode, Pi | the coder agent (`opencode.json`; Pi: `agents/coder.md`) | `set NO_CODER=1\|on` |
| Coder Subagent > Background Coder | OpenCode, Pi | `opencode.env`; Pi: `carl.json` `background_subagents` | `set NO_BACKGROUND_SUBAGENTS=1\|on` |
| Coder Subagent > Delegation Reminder | OpenCode, Pi | the `carl-delegation` entry's `reminder`; Pi: `carl.json` `delegation.reminder` | `set NO_REMINDER=1\|on` |
| Coder Subagent > Tests | OpenCode, Pi | `"coder_tests"` in the state file (`~/.config/opencode/carl.json`, `~/.pi/agent/carl.json`; `before` when none) | `set CODER_TESTS=before\|after\|off` (no restart: the chain reads it at each coder task) |
| Coder Subagent > Request Check | OpenCode, Pi | `"request_check"` in the state file (`reminder` when none) | `set CODER_REQUEST_CHECK=reminder\|on\|off` (no restart: carl-delegation reads it at each coder task) |
| Coder Subagent > Coder Thinking | OpenCode, Pi | `"models"` in the state file (`~/.config/opencode/carl.json`, `~/.pi/agent/carl.json`): each model's thinking kind, main and coder values as the setup wrote them, and the dashboard's coder value; this computer's values: `CODER_THINKING` in `client-install.env` | `set CODER_THINKING=MODEL:VALUE` (`MODEL:default` removes the model's value) |
| Coder Subagent > Coder Model | OpenCode, Pi | the coder agent's `model` in `opencode.json`; Pi: `carl.json` `coder_model`; the values: the client's models (`Session.external`) | `set CODER_MODEL=PROVIDER/MODEL` (`main`: same as main, with `CODER=1`) |
| Browser | OpenCode, Pi | `carl-browser` (OpenCode `mcp`; Pi `mcp.json`) | `set NO_BROWSER=1\|on` |
| Web Search | OpenCode, Pi | `opencode.env`; Pi: `mcp.json` `carl-web-search` | `set WEB_SEARCH=exa\|parallel\|off` |
| LSP | OpenCode | `"lsp": true` and `opencode.env` | `set NO_LSP=1\|on` |
| Subagents Side Panel | OpenCode | `subagents-sidebar` in `tui.json` | `set NO_SIDEBAR=1\|on` |
| Session Switcher | OpenCode | `session-switcher` in `tui.json` | `set NO_SWITCHER=1\|on` |
| Disk Cache | OpenCode, Pi | `carl-cache` in `opencode.json`; Pi: `extensions/carl-cache` | `set NO_CACHE=1\|on` |
| Model Check | OpenCode | `carl-model-check` in `opencode.json` | `set NO_MODEL_CHECK=1\|on` |

- **A switch** runs `client/carl-sync.py set KEY=VALUE` (in the client folder that the setup recorded). It writes the key to `~/.config/carl/client-install.env` (`1`: off; `on`: the line goes, so the default holds; the other lines stay; mode 0600), then runs `install.sh` next to it with the recorded switches and `CARL_SYNC=1`, as a sync does, but with the models that the folder has now (`installed-models.json`): it gets no new config from the dashboard. It holds the sync's lock, so it never runs beside a sync. It does not set `NO_PROFILE`: a switch can add the shell-profile line for OpenCode's tool switches, as `./setup` does. The output of the installer goes to `~/.config/carl/client-sync.log`.
- `set` prints JSON: `changed` (each key, `from` and `to`), `ok` and `error`, `restart` (the clients that must restart: `opencode`, `pi`; the keys of LSP, the side panel, the switcher and the model check are OpenCode only), `new_terminal` (OpenCode reads web search, LSP and the background coder from the shell, so it must start from a new terminal) and `slots` (the server's slots that `install.sh` read for its coder rule, `/props` `total_slots`; `null` when the server did not answer). An unknown key or value: exit code 2, and nothing changes.
- After a switch, the panel reads the rows again. A message says what changed and which program must restart ("CARL turned the subagents side panel off. Restart OpenCode to use it."); it is red when the switch failed, or when the state did not change.
- Turning the coder on also sets `CODER=1`: it stays on with a 1-slot server.
- When the coder goes on and the server runs 1 slot (`set`'s `slots`; not known: no warning), the message is a warning (OpenCode: a `warning` toast; Pi: a `warning` notice) that says that the coder takes the main session's slot while it works.
- Without the coder, the Coder Subagent list has only the Coder row.
- **Coder Thinking** (Phase 23.4.4) overrides the dashboard's Coder thinking (Settings > Agents) on this computer only, per model. One row, for **the model that the coder runs on** (`coderModel()` in `carl-panel.js`; user, 2026-10-09: "there has to be a way for the default model case because what if we choose a different model for a main session"; "Agreed on thinking follows whatever the coder model says"). With Coder Model `same as main`, that is the model of the session `/carl` is opened in (`Session`: OpenCode, the newest message of the main session with a model: a user message's `model`, an assistant message's `providerID` / `modelID`, from `api.state.session.messages` of the session in `api.route.current` or its parent; Pi, `ctx.model`), when it is one of CARL's models in the state file (provider: the state file's `"providers"`). Else (OpenCode's home screen, a session with no message yet, a model that is not CARL's): the config's default model (OpenCode `model`; Pi `defaultProvider` / `defaultModel`), else the first model. A model picked in OpenCode's prompt counts after its first message: the plugin API has no reader for the prompt's model. The values' title names the model (`Coder Thinking with MODEL`). There is no list of models any more (the 23.4.4 build had one in router mode).
  - Its values: `dashboard default` (`default`: this computer has no value for the model; its note in grey: what the dashboard's value gives, `medium` or `same as main (low)`), `same as main` (`main`; its note: `the main session's thinking (LEVEL)`, or without a session `the default model's thinking (LEVEL)`), then `off` and `on`, or `off`, `low`, `medium`, `xhigh` for an `effort` model, in the dashboard's order; OpenCode marks the current one with ●. The row shows `dashboard default` when `CODER_THINKING` has no entry for the model. A state file without `"models"` (an install from before 23.4.4): no row, until the next setup or sync. A state file without `"dashboard"` (before this change): no note for `dashboard default` while the computer has a value.
  - The action runs `carl-sync.py set CODER_THINKING=MODEL:VALUE` (`busy`: `turning off…`, `switching to low…`, `switching to dashboard default…`). `set` keeps one line `CODER_THINKING=MODEL:VALUE,MODEL:VALUE` in `client-install.env`: it changes the named model's entry and keeps the others; `MODEL:default` removes the model's entry (no entry left: the line goes) ([the override](client-configs.md#carls-coder-thinking-on-one-computer)).
  - The message: "CARL set the coder's thinking with MODEL to off on this computer. Use it only with a full spec (spec-kit or a similar tool). Restart OpenCode to use it." (`dashboard default`: "… to dashboard default (medium) …"). The full-spec sentence comes with every value that makes the coder think off: `off`, `same as main` when the main session does not think, and `dashboard default` when the dashboard's value gives off.
  - `set` names only OpenCode in `restart` for this key: Pi reads `carl.json` each time it starts the coder. Pi's title does not count the change.
- **Coder Model** (Phase 23.4.5; user, 2026-10-09: "model selection could also allow you to choose an external free or paid model to do the code"). Its values: `same as main` (the coder runs on the main session's model, on CARL's server; its note in grey: `CARL: MODEL`, the model that Coder Thinking is for), then the models that this computer's client can use, asked from the client itself, each as `PROVIDER/MODEL` with a note in grey: `HOST, free` or `HOST, paid` (`HOST` only when the client has no price for it). The front end passes them in `Session.external`:
  - **OpenCode:** `api.state.provider` of the TUI plugin API (the providers that OpenCode lists as connected, from `config.providers`, with their models). `openCodeModels()` in `carl-panel.js` reads only each provider's `id`, `name` and `options.baseURL`, and each model's `id`, `api.url`, `cost` and `variants`: never the provider's `key` or `options.apiKey` (a test proves it with a provider whose key fields throw when read).
  - **Pi:** `ctx.modelRegistry.getAvailable()` (the models that Pi has set-up auth for) and, for each one, `getSupportedThinkingLevels()` of `@earendil-works/pi-ai`. `piModels()` reads only `provider`, `id`, `baseUrl` and `cost`.
  - The host: the base URL's host name (OpenCode: the provider's `options.baseURL`, else the model's `api.url`; Pi: `baseUrl`), else the provider's name. Free: every price of the model's cost (input, output, the cache prices it has) is 0; paid: one is more; no cost: the host only. A model whose provider is CARL's (the state file's `"providers"`) is left out: CARL's other models come with the router switch (Phase 29).
  - The action runs `carl-sync.py set CODER_MODEL=PROVIDER/MODEL` (`same as main`: `set CODER_MODEL=main CODER=1`, so the coder stays on, as the Coder switch does). The message says where the coder's work goes and that a paid model costs money: "CARL set the coder's model to openrouter/example-coder-32b on this computer. The coder's work goes to openrouter.ai, and the model costs money. Restart OpenCode to use it." (free: no cost sentence). `set` names only OpenCode in `restart`: Pi reads `carl.json` each time it starts the coder.
  - The row reads the model that the setup wrote: OpenCode, the coder agent's `model` in `opencode.json`; Pi, `"coder_model"` in `carl.json`. A current model that this client does not list (chosen in the other client, or a provider that went away) stays in the values with the note `OpenCode does not list this model.` (`Pi …`).
  - With an external model, **Coder Thinking** is for that model: `model default` (`default`: CARL sends no thinking value; the client's own default for it), then the levels that the client knows for it (OpenCode: the model's variants; Pi: its supported thinking levels). The action runs `set CODER_MODEL_THINKING=VALUE` (`default` removes it); a new `CODER_MODEL` removes it too. The dashboard's per-model table does not apply to it. Off says to use a full spec, as for CARL's models.
  - With an external model there is no 1-slot warning: when the Coder switch goes on (or the model changes) and the coder's model is external, the message is a plain success. `same as main` with a 1-slot server gives the warning.
  - The data boundary (the brief, the files the coder reads and its tool results go to that provider) is in the user guide and in [the hand-off](delegation.md#an-external-coder-model).
- The new-file gate (`delegation.gate`) is not in `/carl`: it is a setting of the dashboard only.
- Without the sync service, the panel checks the server for a new config one time when OpenCode or Pi starts.
- When a config from the dashboard is applied while OpenCode or Pi runs, OpenCode shows a message and Pi shows a notice: restart it to use the new config.
- A state while an action runs uses the -ing form (`turning off…`); the messages are whole sentences ("New configs from the dashboard now wait for you.").

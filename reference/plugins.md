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
| `carl-delegation` | OpenCode, Pi | server plugin / extension | The hand-off to the coder: the delegation rule for main agents only (OpenCode), the reminder, the new-file gate | comes with the coder; `NO_REMINDER=1` or `/carl`; the gate: the dashboard (Connect > Setup) |
| `subagent` | Pi | extension | The `subagent` tool: the coder and other agents, also in the background | comes with the coder (`NO_CODER=1`) |
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
- The code that more than one piece uses is in `client/shared/`: `carl-cache.js` (the disk cache), `carl-panel.js` (the /carl panel), `carl-delegation.js` (the hand-off rules) and `carl-tui.js` (the TUI helpers of the session switcher and the subagents panel). The setup copies each file into each piece that uses it.
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

1. It puts the parts of the system prompt that change between projects and days (the folder, the date, AGENTS.md) after the shared part: a second system message with Qwen; with Gemma 4 they stay; with other templates they stay too, unless Settings > Caching > Other templates moves them to the first user message ([Caching](caching.md#before-each-request)).
2. It claims a free slot (a `.claim+MODEL+SLOT` file) and pins the request to it (`id_slot`).
3. It marks the slot's turn as running (a `.turn+MODEL+SLOT` file). The mark stays until the turn ends and its save or record is on disk.
4. If the slot does not hold the session, it puts back the session's file, else the agent's prompt file. If there is no prompt file, it reads the prompt one time and saves it.
5. When the turn ends, it saves the session, or it records which session the slot holds (a `.resident+MODEL+SLOT.json` file). The Save setting decides which (Settings > Caching).

- The claim, turn and record files have the mode 0600. A saved-state name from a record or a patch is used only when it is a plain file name.
- The dashboard's Stop, Apply, Auto-tune and router loads read the turn marks. They can wait for the end of a turn, so that the session is saved whole.
- A client on another computer does the same through the dashboard's API ([Clients on other computers](client-sync.md)).
- `CARL_CACHE=0` turns the cache off for one run. `CARL_CACHE_SAVE=turn|auto|switch|stop` sets the save rule for one client. `CARL_CACHE_LOG=FILE` writes what the cache does to a file, also each failure it lets go (a request never fails because of the cache). The file never holds the key or a request body.

## carl-model-check: the model warning (OpenCode)

Before each request to CARL's provider, the plugin compares the model that you selected with the server. It shows a message (a toast) one time for each situation:

| Mode | The message says |
|---|---|
| Single model | The server runs another model. The server answers with its own model. |
| Router | The model is not installed on the server (HTTP 400), or the model loads now. |

The plugin asks the server for its models at most every 5 s. OpenCode's log has the same lines (`service=carl-model-check`).

## carl-background: the coder in the background (OpenCode)

OpenCode's task tool waits for a subagent, unless the model asks for the background (`background: true`). A local model often does not ask, even when the delegation rule tells it to. Then the main session waits in its slot while the coder works in the other slot.

- The plugin uses the hook `tool.execute.before`. When the task tool starts CARL's coder (`coder` or `carl-coder`) and the call does not set `background`, the plugin sets `background: true`.
- The model can still ask for the foreground with `background: false`. A call that continues an earlier task (`task_id`) stays as the model asked.
- The coder's result comes back to the main session as a message when the coder ends.
- It needs `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1`, which the setup puts in `~/.config/carl/opencode.env`.
- The setup adds it only with the coder (2 slots or more, or `--coder on`).

## carl-delegation: the hand-off to the coder (OpenCode and Pi)

The rules are in `client/shared/carl-delegation.js`. The details and the measurements are in [the hand-off to the coder](delegation.md).

- **OpenCode** (server plugin, with the options `reminder`, `cacheApi` and `coder` in its `plugin` entry):
  - `experimental.chat.system.transform`: in a subagent's session (a session with a parent), it takes CARL's marked delegation rule out of the system prompt. It changes OpenCode's list in place.
  - `experimental.chat.messages.transform`: it adds the reminder to the end of each user message of a main session.
  - `tool.execute.before`: when the dashboard sets the gate (`delegation.gate`, read from `config.json` on the server Mac or the dashboard API elsewhere, every 10 s), it stops the main agent's write that makes the Nth new file of a turn (`chat.message` starts a new turn).
- **Pi** (extension; the reminder is `"delegation"` in `~/.pi/agent/carl.json`, the gate comes from the dashboard): the `input` event adds the reminder to your messages; `tool_call` blocks with the gate. In a subagent (`CARL_AGENT` set) it does nothing.
- The setup installs it with the coder, and removes it with `--coder off`.

## subagent: the subagent tool (Pi)

Pi has no subagents of its own. CARL installs the `subagent` extension (from Pi's examples, with changes marked `CARL:`).

| Mode | Call | What happens |
|---|---|---|
| Single | `agent`, `task` | One agent does the task in its own `pi` process. |
| Parallel | `tasks: [{agent, task}, …]` | Up to 8 tasks, 4 at a time. |
| Chain | `chain: [{agent, task}, …]` | One after the other; `{previous}` puts in the last result. |
| Background | `background: true` (single, parallel) | The tool returns at once. Each result comes back as a message when its agent ends. |

- **CARL's changes:**
  - The tool's description lists the installed agents and when to use each one.
  - The delegation rule goes into Pi's system prompt (`APPEND_SYSTEM.md`), for the main agent only: the tool starts every agent with `--append-system-prompt` (its own prompt, or an empty one), and with that option Pi does not read `APPEND_SYSTEM.md`.
  - An agent file can say `exclude-tools:`. The agent then gets every tool except those, the MCP tools included. The coder says `exclude-tools: subagent, tool_search`: it gets web search when it is installed, but it cannot start nested subagents or load the browser tools.
  - Each subagent's process gets `CARL_AGENT`, so its prompt file has its own name.
  - The coder has no browser. Its report has a "Needs a browser check" part; the main agent starts the app and does the check (OpenCode: the browser subagent; Pi: its own browser tools).
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

Type `/carl`. The panel is a control panel (user, 2026-10-08: no explanations): one list, one row for each CARL piece on this computer, with its label and its state. The state values are short and lower case: `on`, `off`, `exa`, `parallel`, `connected`. The rows keep the order of their groups: the coder, the tools, the side panels, the server, the config sync.
- **OpenCode** (a TUI plugin): one `DialogSelect`. The label is the row's title, the state is its footer (at the right). The options come from a Solid signal, so a change updates the list in place and the cursor stays. The dialog is OpenCode's medium size (60 columns): every label and state fits. No group headings: OpenCode shows at most half the terminal height minus 6 lines, and the list fits 13 rows on a 40-row terminal.
- **Pi** (an extension): Pi's `SettingsList` (as Pi's `/settings`) in `ctx.ui.custom`, with Pi's own hint line.
- **Enter** (Pi: also **Space**) on a switch runs its action at once: the state reads `turning off…`, then the new state. **Web search** opens its values, the current one marked, each provider with where the queries go. **Sync service** opens a read-only view of label rows (OpenCode: a `DialogAlert`). **Check for a new config** and **Apply the waiting config** run at once (`checking…`, `applying…`).
- The title says `Restart OpenCode to use N changes.` (Pi: `Restart Pi …`) while rows changed since the program started, or a config was applied.
- One action at a time; a second Enter waits.

| Row | Client | State from | Action |
|---|---|---|---|
| Sync service (a view), Apply the waiting config, Check for a new config | OpenCode, Pi (only with `remote.json`) | `~/.config/carl/client-sync.json` | `carl-sync.py apply`, `carl-sync.py once` |
| Apply new configs at once | OpenCode, Pi (only with `remote.json`) | `client-sync.json` `auto_apply` | `carl-sync.py auto on\|off` |
| Coder subagent | OpenCode, Pi | the coder agent (`opencode.json`; Pi: `agents/coder.md`) | `set NO_CODER=1\|on` |
| Background coder | OpenCode, Pi | `opencode.env`; Pi: `carl.json` `background_subagents` | `set NO_BACKGROUND_SUBAGENTS=1\|on` |
| Delegation reminder | OpenCode, Pi | the `carl-delegation` entry's `reminder`; Pi: `carl.json` `delegation.reminder` | `set NO_REMINDER=1\|on` |
| Browser | OpenCode, Pi | `carl-browser` (OpenCode `mcp`; Pi `mcp.json`) | `set NO_BROWSER=1\|on` |
| Web search | OpenCode, Pi | `opencode.env`; Pi: `mcp.json` `carl-web-search` | `set WEB_SEARCH=exa\|parallel\|off` |
| LSP | OpenCode | `"lsp": true` and `opencode.env` | `set NO_LSP=1\|on` |
| Subagents side panel | OpenCode | `subagents-sidebar` in `tui.json` | `set NO_SIDEBAR=1\|on` |
| Session switcher | OpenCode | `session-switcher` in `tui.json` | `set NO_SWITCHER=1\|on` |
| Disk cache | OpenCode, Pi | `carl-cache` in `opencode.json`; Pi: `extensions/carl-cache` | `set NO_CACHE=1\|on` |
| Model check | OpenCode | `carl-model-check` in `opencode.json` | `set NO_MODEL_CHECK=1\|on` |

- **A switch** runs `client/carl-sync.py set KEY=VALUE` (in the client folder that the setup recorded). It writes the key to `~/.config/carl/client-install.env` (`1`: off; `on`: the line goes, so the default holds; the other lines stay; mode 0600), then runs `install.sh` next to it with the recorded switches and `CARL_SYNC=1`, as a sync does, but with the models that the folder has now (`installed-models.json`): it gets no new config from the dashboard. It holds the sync's lock, so it never runs beside a sync. It does not set `NO_PROFILE`: a switch can add the shell-profile line for OpenCode's tool switches, as `./setup` does. The output of the installer goes to `~/.config/carl/client-sync.log`.
- `set` prints JSON: `changed` (each key, `from` and `to`), `ok` and `error`, `restart` (the clients that must restart: `opencode`, `pi`; the keys of LSP, the side panel, the switcher and the model check are OpenCode only) and `new_terminal` (OpenCode reads web search, LSP and the background coder from the shell, so it must start from a new terminal). An unknown key or value: exit code 2, and nothing changes.
- After a switch, the panel reads the rows again. A message says what changed and which program must restart ("CARL turned the subagents side panel off. Restart OpenCode to use it."); it is red when the switch failed, or when the state did not change.
- Turning the coder on also sets `CODER=1`: it stays on with a 1-slot server.
- Without the coder, the Background coder and Delegation reminder rows are not in the list.
- The new-file gate (`delegation.gate`) is not in `/carl`: it is a setting of the dashboard only.
- Without the sync service, the panel checks the server for a new config one time when OpenCode or Pi starts.
- When a config from the dashboard is applied while OpenCode or Pi runs, OpenCode shows a message and Pi shows a notice: restart it to use the new config.
- A state while an action runs uses the -ing form (`turning off…`); the messages are whole sentences ("New configs from the dashboard now wait for you.").

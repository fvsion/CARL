# CARL Reference: The plugins and extensions

[Index](../REFERENCE.md) · every OpenCode plugin and Pi extension that CARL installs: what it does, how it works, its files and its switch.

`./carl.sh install` (or `client/install.sh` on another computer) copies these pieces and registers them. It keeps your own plugins and extensions. A piece of yours with the same name stays, and CARL does not install its own.

Type `/carl` in OpenCode or Pi to see each piece and its state on this computer.

## Overview

| Piece | Client | Kind | What you get | Switch |
|---|---|---|---|---|
| `carl-cache` | OpenCode, Pi | server plugin / extension | The prompt cache: fast starts, sessions back after a restart | `NO_CACHE=1` |
| `carl-model-check` | OpenCode | server plugin | A warning when the model you pick is not the model the server runs | `NO_MODEL_CHECK=1` |
| `carl-background` | OpenCode | server plugin | The coder runs in the background | `NO_BACKGROUND_SUBAGENTS=1` |
| `subagent` | Pi | extension | The `subagent` tool: the coder and other agents, also in the background | comes with the coder (`NO_CODER=1`) |
| `subagents-sidebar` | OpenCode | TUI plugin | The Subagents panel in the sidebar | `NO_SIDEBAR=1` |
| `session-switcher` | OpenCode | TUI plugin | `‹ 2/3 ● title ›` in the prompt box, `/switch` | `NO_SWITCHER=1` |
| `carl-panel` | OpenCode, Pi | TUI plugin / extension | The `/carl` panel; the config sync check | always |

Put a switch in front of the installer, for example `NO_SIDEBAR=1 ./carl.sh install --config-only`. The installer then removes that piece.

## Where the pieces are

| Client | Folder | Registered in |
|---|---|---|
| OpenCode | `~/.config/opencode/plugins/NAME/` | Server plugins: `plugin` in `~/.config/opencode/opencode.json`. TUI plugins: `plugin` in `~/.config/opencode/tui.json`. |
| Pi | `~/.pi/agent/extensions/NAME/` | Pi loads every folder in `extensions/`. |

- The source is in `client/opencode/plugins/` and `client/pi/extensions/`.
- The code that both clients use is in `client/shared/` (`carl-cache.js`, `carl-panel.js`). The installer copies it into each piece that uses it.
- OpenCode and Pi load the pieces when they start. After an install, restart OpenCode or Pi.

## carl-cache: the prompt cache

The prompt cache saves prompt states on the server's disk, through the server. Then a new session, a restart or a router switch does not mean reading everything again. The pages [The disk prompt cache](cache.md) and [How the pieces fit](pieces.md) give the details.

| | OpenCode | Pi |
|---|---|---|
| Hooks | `chat.headers` marks each request of CARL's provider with the session and the agent. A wrapper around `fetch` changes the marked requests and reads the replies. `event` (`session.idle`) ends the turn marks. | `before_provider_request`, `after_provider_response`, `message_end` (a reply that ends the turn), `agent_end` |
| Agent name | OpenCode's agent (`build`, `plan`, `coder`, …) | `pi`, or `pi-NAME` in a subagent (`CARL_AGENT`) |
| Options | `provider`, `cacheApi` (the plugin entry) | read from `~/.pi/agent/carl.json` |

For each request, the cache does these steps:

1. It moves the parts of the system prompt that change between projects and days (the folder, the date, AGENTS.md) to the first user message.
2. It claims a free slot (a `.claim+MODEL+SLOT` file) and pins the request to it (`id_slot`).
3. It marks the slot's turn as running (a `.turn+MODEL+SLOT` file). The mark stays until the turn ends and its save or record is on disk.
4. If the slot does not hold the session, it puts back the session's file, else the agent's prompt file. If there is no prompt file, it reads the prompt one time and saves it.
5. When the turn ends, it saves the session, or it records which session the slot holds (a `.resident+MODEL+SLOT.json` file). The Save setting decides which (Settings > Caching).

- The dashboard's Stop, Apply, Auto-tune and router loads read the turn marks. They can wait for the end of a turn, so that the session is saved whole.
- A client on another computer does the same through the dashboard's API ([Clients on other computers](client-sync.md)).
- `CARL_CACHE=0` turns the cache off for one run. `CARL_CACHE_SAVE=turn|auto|switch|stop` sets the save rule for one client. `CARL_CACHE_LOG=FILE` writes what the cache does to a file.

## carl-model-check: the model warning (OpenCode)

Before each request to CARL's provider, the plugin compares the model you picked with the server. It shows a message (a toast) one time for each situation:

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
- It needs `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1`, which the installer puts in `~/.config/carl/opencode.env`.
- The installer adds it only with the coder (2 slots or more).

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
  - The delegation rule goes into Pi's system prompt.
  - An agent file can say `exclude-tools:`. The agent then gets every tool except those, the MCP tools included. The coder says `exclude-tools: subagent`, so it gets web search when it is installed, but it cannot start nested subagents.
  - Each subagent's process gets `CARL_AGENT`, so its prompt file has its own name.
- **Background:**
  - CARL's coder runs in the background unless the call says `background: false`.
  - `/subagents` lists the agents that run in the background, and stops one.
  - The footer shows how many run.
  - When Pi closes, the running agents stop.
  - `NO_BACKGROUND_SUBAGENTS=1` turns the background off (`"background_subagents": false` in `~/.pi/agent/carl.json`).

## subagents-sidebar: the Subagents panel (OpenCode)

A TUI plugin. The panel in the sidebar shows the subagents of the session on the screen.

- The running subagents are at the top, oldest first: the agent, the task, the time, the tool that runs now and the context size.
- The finished subagents are below, newest first: ✓ or ✗, the agent, the task and the duration. The panel shows the 5 newest for 5 minutes. A `+N more` line holds the others.
- Click a subagent to see its model. Click it again to open its conversation. Click the header to collapse the panel.
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

Type `/carl`. The panel shows one section for each CARL piece on this computer, with its state. A section opens a dialog with its lines and its actions.

| Section | It shows |
|---|---|
| Config sync | The server, the service, the config that is applied, a config that waits, auto-apply. Actions: auto-apply on or off, Apply now, Check the server now. |
| Prompt cache, Model check, Session switcher, Subagents sidebar | On or off, and the switch |
| Coder subagent | On or off, its tools, background on or off |
| Browser, Web search, LSP | On or off; web search says that its queries leave this computer |

- The panel reads the files that the installer wrote. It changes nothing, except through `client/carl-sync.py` (the actions).
- Without the sync service, the panel checks the server for a pushed config one time when OpenCode or Pi starts.
- When a pushed config is applied while OpenCode or Pi runs, OpenCode shows a message and Pi shows a notice: restart it to use the new config.

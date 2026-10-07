# CARL Reference: The plugins and extensions

[Index](README.md) · every OpenCode plugin and Pi extension that CARL installs: what it does, how it works, its files and its switch.

The setup (`./carl.sh install` on the server Mac, `./setup` in the client package on another computer) copies these pieces and registers them. It keeps your own plugins and extensions. A piece of yours with the same name stays, and CARL does not install its own.

Type `/carl` in OpenCode or Pi to see each piece and its state on this computer.

## Overview

| Piece | Client | Kind | What you get | Switch |
|---|---|---|---|---|
| `carl-cache` | OpenCode, Pi | server plugin / extension | The disk cache: fast starts, sessions back after a restart | `NO_CACHE=1` |
| `carl-model-check` | OpenCode | server plugin | A warning when the model that you select is not the model that the server runs | `NO_MODEL_CHECK=1` |
| `carl-background` | OpenCode | server plugin | The coder runs in the background | `NO_BACKGROUND_SUBAGENTS=1` |
| `carl-delegation` | OpenCode, Pi | server plugin / extension | The hand-off to the coder: the delegation rule for main agents only (OpenCode), the reminder, the new-file gate | comes with the coder; `NO_REMINDER=1`, `DELEGATION_GATE=N` |
| `subagent` | Pi | extension | The `subagent` tool: the coder and other agents, also in the background | comes with the coder (`NO_CODER=1`) |
| `subagents-sidebar` | OpenCode | TUI plugin | The Subagents panel in the sidebar | `NO_SIDEBAR=1` |
| `session-switcher` | OpenCode | TUI plugin | `‹ 2/3 ● title ›` in the prompt box, `/switch` | `NO_SWITCHER=1` |
| `carl-panel` | OpenCode, Pi | TUI plugin / extension | The `/carl` panel; the config sync check | always |

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

1. It moves the parts of the system prompt that change between projects and days (the folder, the date, AGENTS.md) to the first user message.
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

- **OpenCode** (server plugin, with the options `reminder`, `gate` and `coder` in its `plugin` entry):
  - `experimental.chat.system.transform`: in a subagent's session (a session with a parent), it takes CARL's marked delegation rule out of the system prompt. It changes OpenCode's list in place.
  - `experimental.chat.messages.transform`: it adds the reminder to the end of each user message of a main session.
  - `tool.execute.before`: with a gate, it stops the main agent's write that makes the Nth new file of a turn (`chat.message` starts a new turn).
- **Pi** (extension; the settings are `"delegation"` in `~/.pi/agent/carl.json`): the `input` event adds the reminder to your messages; `tool_call` blocks with the gate. In a subagent (`CARL_AGENT` set) it does nothing.
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

Type `/carl`. The panel shows one section for each CARL piece on this computer, with its state in a few words (for example `checks at start · applies new configs at once`). A section opens a dialog:
- First, its state and what the piece does, in plain sentences (at most 88 characters on a line), and its actions.
- A **Details** part: the addresses, the config version, the version of the client package, and the switches of the setup (for example "To turn it off, run the installer again with NO_CACHE=1.").
- **‹ back** goes back to the list. (OpenCode shows **‹ back** before the details, Pi after them.)

| Section | It shows |
|---|---|
| Config sync | If new configs are applied at once, the sync service, the last config from the dashboard ("Last config from the dashboard: today 10:53."), a config that waits. Actions: **Do not apply new configs at once** (or **Apply new configs at once**), **Apply the new config now** (only when a config waits), **Check for a new config now**. Details: the version of the client package (`VERSION` in the client folder) and the CARL version of the server at its last start before the package (`remote.json` in the client folder), the server, the dashboard API, the config version. When the two versions differ, the section says so and tells you to make a new package and run `./setup`. |
| Disk cache, Model check, Session switcher, Subagents sidebar | On or off, and what it does. Details: the switch. The Subagents sidebar section also explains ✓ and ✗. |
| Coder subagent | On or off, its tools, background on or off |
| Browser, Web search, LSP | On or off; web search shows its provider (Exa or Parallel) and says that its queries leave this computer |

- The panel reads the files that the setup wrote. It changes nothing, except through `client/carl-sync.py` (the actions).
- Without the sync service, the panel checks the server for a pushed config one time when OpenCode or Pi starts.
- When a config from the dashboard is applied while OpenCode or Pi runs, OpenCode shows a message and Pi shows a notice: restart it to use the new config.
- After an action, a short message says what happened, for example "CARL: new configs from the dashboard now wait for you.".

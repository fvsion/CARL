# CARL Reference: The hand-off to the coder

This page tells how CARL makes the main agent give large and stuck coding tasks to the coder subagent, why, and what was measured. For daily use, see [the coder subagent](../USERGUIDE.md#the-coder-subagent-opencode-and-pi) in the user guide.

## The parts

| Part | What it does | Where | Switch |
|---|---|---|---|
| The delegation rule | Tells the main agent when to give a task to the coder, and how to write the task | `client/agents/delegation.md`. OpenCode: `~/.config/opencode/carl/delegation.md` (in `instructions`). Pi: the last block of `~/.pi/agent/APPEND_SYSTEM.md` | comes with the coder |
| The rule for main agents only | Keeps the rule out of the prompt of every subagent | OpenCode: the `carl-delegation` plugin. Pi: the `subagent` tool | comes with the coder |
| The reminder | One line at the end of each of your messages in the main session | `carl-delegation` (OpenCode plugin and Pi extension) | on; `NO_REMINDER=1` turns it off |
| The coder's two modes | `mode = "code"` or `mode = "test"`, one in each brief | `client/agents/coder.md` | comes with the coder |
| The brief | Each coder task is a brief in TOML: ids, scope, files by action, checks | `client/agents/delegation.md`, `client/agents/coder.md`, `client/shared/carl-brief.js` | comes with the coder |
| The brief check | Sends an incomplete brief back to the main agent before the coder starts | `carl-delegation` | on; `"brief": false` turns it off (for measurements) |
| The coder's gates | Keep the coder to its brief's files and to its mode | `carl-delegation` | comes with the coder |
| The chain | A brief with `tests = "new"`: a test session, then a code session, each with a new context; one result. OpenCode: in the foreground; in the background only on the verified versions | OpenCode: `carl-delegation`. Pi: the `subagent` tool. Both: `client/shared/carl-chain.js` | on; `"chain": false` turns it off (for measurements) |
| `/code` | Gives your task straight to the coder | OpenCode: `~/.config/opencode/command/code.md`. Pi: `~/.pi/agent/prompts/code.md` | comes with the coder |
| The new-file gate | Stops the main agent at its Nth new file in a turn | `carl-delegation` | off; the dashboard only: Connect > Setup, full level, `g` (advanced, not recommended) |

All parts come with the coder (`--coder on`, or `auto` with 2 or more slots). With the coder off, the setup removes them all.

## The delegation rule, for main agents only

OpenCode gives its global `instructions` to every agent, the subagents too. A coder that reads "your first action is to delegate to `coder`" acts as the main session: it says that it gives the task on, and it does nothing. This happened with the Gemma 4 E4B (2026-10-06): the coder changed no file, and 0 of 4 hidden tests passed.

- **OpenCode:** the setup writes the rule between two marks (`<!-- carl:main-agents-only begin -->` and `end`). Before each request, `carl-delegation` checks the session. A session that OpenCode made with a parent is a subagent's session. The plugin takes the marked rule out of its system prompt. It changes OpenCode's own list of prompt parts in place: OpenCode ignores a new list. When the plugin cannot tell, the session keeps the rule.
- **Pi:** the `subagent` tool starts each subagent with `--append-system-prompt`, also for an agent without a prompt of its own. With that option, Pi does not read `APPEND_SYSTEM.md`, where the rule is.
- **Checked** (OpenCode 1.18.34, the request that the server gets): the main agent has the rule and the reminder; the coder has neither. After the fix, the same E4B task passed 3 of 4 hidden tests.

## The reminder

`carl-delegation` adds one line to the end of each of your messages in the main session:

```
[CARL reminder] Large coding work or a fix that already failed goes to coder (the task tool with subagent_type "coder"); questions and one small edit you do yourself.
```

- The line is the same in every message. Thus, the start of the conversation does not change from one request to the next, and the server reuses it (the RAM cache and the disk cache stay valid).
- Subagents never get it. In Pi, a command (a message that starts with `/`) does not get it.
- The setting: OpenCode, `"reminder"` in the `carl-delegation` entry of `plugin` in `opencode.json`; Pi, `"delegation": {"reminder": …}` in `~/.pi/agent/carl.json`. `NO_REMINDER=1 ./setup --yes` (or `./carl.sh install` on the server Mac) turns it off. `NO_REMINDER=0` turns it on again.

## The coder's two modes and the brief

Each coder task is a brief in TOML (Phase 23.4.3). Its `mode` says what the coder may change. The two modes are exclusive in one brief:

- **`mode = "code"`:** the coder writes and changes the program code. It runs the tests, but it does not change them. A test that looks wrong goes into its report.
- **`mode = "test"`:** the coder writes tests from the requirements, not from the current code. It writes at least one test for each requirement id, with the id in the test's name or docstring. It does not change the program code. A test that fails because the code is wrong is a finding in its report.

The main agent writes the brief from your request and the project's files. It invents nothing: examples are copied from a file or from your message. Thus, for a large request the rule lets it read first what the brief needs (the files to change, the test command, the project's rules), but it must not write or change a file itself; then it delegates. Before, the rule said "delegate first, do not explore", and the brief then had no real paths or commands. The schema (the full reference is `docs/phase-plans/phase23.4.3/full_plan.md`, item 2; `client/agents/delegation.md` has a complete example):

| Key | What it holds |
|---|---|
| `mode` | `"code"` or `"test"` |
| `tests` | `"new"`, `"existing"` or `"none"` (mode code) |
| `goal` | one or two sentences |
| `[scope]` `in`, `out` | what the task is about; what to leave alone (`{ text = "...", why = "..." }`). Mode code: at least one `out` entry |
| `[[file]]` `path`, `action` | `"create"`, `"change"` or `"read"` (context only, never written) |
| `[[requirement]]` `id`, `text` | one point each, with its own id (R1, R2, ...) |
| `[[check]]` `id`, `covers`, `run`, `expect` | the acceptance: the requirement ids it covers, the command, the result |
| `[[constraint]]` `text`, `source` | a project rule, copied, and the file it is from |
| `[[example]]` `source`, `text` | real input, copied from a file or from your message (`source = "user"`) |
| `[error]` `run`, `output`; `[[tried]]` `change`, `result` | only for a fix that failed |

**The rule for `tests`** (for the main agent, in this order): 1. a follow-up to the coder's work, a fix or a stuck task: `"existing"` when tests cover it (named in a check), else `"none"`; 2. new behaviour with no tests yet: `"new"`; 3. else `"existing"` when the project's tests cover the change, else `"none"`. With `mode = "code"` and `tests = "new"`, CARL runs the coder twice in separate sessions: first in mode test, then in mode code ([the chain](#the-chain-tests-first-in-a-separate-session)).

**The brief check.** Before the coder starts, `carl-delegation` reads the brief and checks it. The brief can be in a `` ```toml `` fence, with text around it. An incomplete brief goes back to the main agent: the call fails with `[CARL] Brief refused` and a list of whole sentences, for example `Requirement R2 is in no check's covers: add it to a check, or add a check for it.` A task text with no TOML at all gets one sentence that asks for the brief. The checks:

- `mode` is code or test; mode code: `tests` is new, existing or none; `goal` is not empty.
- At least one requirement, each with its own id and a text. Every requirement is in the `covers` of a check. Every check has an id and a `run`, and covers only ids that exist.
- Mode code: at least one file to create or change, and at least one `scope.out` entry. Each file has a path and a valid action. Mode test: each file to create or change is a test file.
- `[error]` has `run` and `output`; `[[tried]]` only with an `[error]`.
- `tests = "existing"`: a check's `run` names the existing tests (a test file, or the folder `tests/` or `test/`).
- No key that is not in the schema (`[[files]]`: "use file").

The reader is CARL's own (no package): strings in all four forms, lists, inline tables, `[table]`, `[[list of tables]]` and comments. It accepts what small models often write (an unknown escape such as `\d`, a `{ table }` over several lines). Errors give the line number. A task that continues an earlier one (OpenCode's `task_id`) is not checked. OpenCode: the check throws in `tool.execute.before`, as the new-file gate does. Pi: the `tool_call` event blocks the subagent call (also a coder item in `tasks` or `chain`).

**The coder's gates.** In the coder's own session, `carl-delegation` keeps the coder to its brief. A write is refused with one sentence (`[CARL] Blocked: ...`) when:

- the file is not one of the brief's files to create or change (a "read" file gets its own sentence);
- mode code: the file is a test file;
- mode test: the file is not a test file. With no test file in the brief, mode test may write any test file.

A test file is a file under `tests/` or `test/`, or a file named `test_*.py`, `*_test.py`, `*.test.*` or `*.spec.*` (`isTestFile` in `carl-brief.js`). The gates see the file tools (write, edit, patch) and the plain shell writes (redirections, `tee`, `touch`, `cp`, `mv`, `sed -i`). Paths outside the project folder pass. How the plugin finds the coder's session: OpenCode, `chat.message` names the agent of each user message; the first message of a coder session is its brief. Pi: the subagent tool runs the coder as its own Pi process with `CARL_AGENT` set; its prompt (`Task: ` and the brief) gives the brief.

**The coder's report** starts with a TOML block: `status` (done, partly or blocked), `summary`, one `[[requirement]]` for each id (`status`, `note`), one `[[check]]` for each id (`result`: pass, fail or not run; `summary`: the summary line of the run), `[[file]]` (the files changed), `[[finding]]` (mode test: the failing tests and why) and `[[open_issue]]`. A "Needs a browser check" section can follow the block. `parseReport` in `carl-brief.js` reads it.

CARL does not make or enforce a project's architecture or plans: the brief only carries the rules that the project already has.

## The chain: tests first, in a separate session

The user's decision (2026-10-09): the tests come from a session that did not see the code, so that they are not vacuous or bent to the code. One `coder` agent with its two modes; CARL chains two new sessions. There is no separate tester agent.

**When.** The brief has `mode = "code"`, `tests = "new"`, no `[error]` and no `[[tried]]`, and the task does not continue an earlier one (OpenCode's `task_id`). Else the coder runs one session, as before. A brief with `mode = "test"` is the test session alone.

**The steps** (`client/shared/carl-chain.js`):

1. **The test session.** A new coder session gets the brief in mode test: the goal, the scope, the requirements, the checks, the constraints and the examples; of the files, only the test files. It writes tests (at least one for each requirement id), runs them and reports.
2. **The red start.** At least one new test must fail before any code. The test session's report decides (a failed check or a finding), unless CARL ran a check itself: then CARL's run decides. CARL runs a check's `run` only when it is a plain test runner or ruff on the project's files ([the allowlist](#the-checks-that-carl-runs-itself)), once, in the project folder, with a 120 s limit. It looks at the checks whose `run` names tests or a test file that the session wrote; when none does, at every check. Any other command is not run, and one line of the result says so: ``CARL did not run `sh tests/check.sh` itself (it runs only a plain test runner or ruff on the project's files).`` No red start: the code session still runs, and the result has a warning.
3. **The freeze.** CARL hashes the project's test files (SHA-256). It looks at files under `tests/` or `test/` and files named `test_*.py`, `*_test.py`, `*.test.*` or `*.spec.*`; tool folders (`.git`, `node_modules`, `.venv`, ...) are left out. The files that changed during the test session (and the test files in its report) are its test files.
4. **The code session.** A new coder session gets the original brief (mode code) and CARL's `[test_session]` table: `files` (the test session's test files), `summary`, `failing` (each failing test, its requirement id and why) and `notes` (its open issues). `client/agents/coder.md` tells the coder what the table means. Its gates refuse every test-file write.
5. **One result.** After the code session, CARL hashes the test files again. The main agent gets one result:

```
[CARL] Chain: the coder ran in two new sessions: first the tests (mode test), then the code (mode code).
Red start: CARL ran `python -m pytest tests/test_csv_export.py -q` before the code: it failed (exit 1).
Tests unchanged after the code session (tests/test_csv_export.py).
Run the checks yourself before you answer the user.

## The test session's report (mode test)
...
## The code session's report (mode code)
...
```

- No red start: `[CARL] Warning: no new test failed before the code (no red start): ...`. The test session wrote no test file: `[CARL] Warning: the test session wrote no test file.`
- A test file that differs after the code session: `[CARL] Warning: the code session changed these test files after the test session: ...`.
- The test session failed (the session itself, not a test): the code session does not run, and the result says so.
- The main agent then runs the checks itself before it answers you (the delegation rule).

### The checks that CARL runs itself

The user's decision (2026-10-09): "C for checks but I would like to support ruff as well". `allowedCheck` in `carl-chain.js` reads the command itself and starts the program with no shell. It accepts:

| Part | Accepted |
|---|---|
| The runner | `pytest`, `python -m pytest`, `python3 -m pytest`, `node --test`, `npm test`, `npm run test`, `go test`, `cargo test`, `ruff check`, `ruff format --check` (`--check` is required: without it, ruff writes the files) |
| A prefix | `uv run` followed by the runner (no uv options); `uvx pytest`, `uvx ruff` |
| `KEY=value` words first | only `CI`, `NO_COLOR`, `FORCE_COLOR`, `TERM`, `TZ`, `LANG`, `LC_ALL`, `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE`, `PYTHONHASHSEED`, `PYTHONUNBUFFERED`, `PYTHONWARNINGS`, `NODE_ENV`, `RUST_BACKTRACE`, `RUST_LOG`, `RUST_TEST_THREADS`, `CGO_ENABLED`; the value is a path in the project too |
| The other words | flags (`-q`, `--tb=short`) and words (paths, test names, `-k` expressions in quotes). A path that is absolute or has `..` must stay in the project folder; so must a flag's value (`--basetemp=/tmp/x` is refused) |

Refused: any of ``; & | < > ` $ ( ) \ * ? [ ] { } ~ ! #`` or a line break (no pipes, redirections, variables, command substitution or globs); a quote that does not end; another program (`sh`, `make`, `python script.py`, `npm run build`); and flags that run another program, load code from elsewhere or write files: `ruff check --fix` (and `--fix-only`, `--unsafe-fixes`, `--add-noqa`, `--watch`), `go test -exec` (and `-toolexec`, `-overlay`), `cargo test --config` (and `-Z`), `node --test -e` (and `--eval`, `-p`, `--require`, `--import`, `--loader`), `npm --prefix`. Quotes only group words: with `$` and `` ` `` refused, `'…'` and `"…"` mean the same.

What an exit means: 0 passes. pytest: 1 fails, and so does 2 with a collection error (the tests cannot import the code yet); another code (5: no test collected; 2 to 4: an error) counts as not run. ruff: 1 fails, 2 (its own error) counts as not run. The other runners: any other code fails (a build error before the code exists is a red start too). A missing runner (`No module named pytest`, `Missing script: test`, a program that is not there) counts as not run. A check that is not run: the report decides.

**`tests = "existing"`.** One session. CARL hashes the test files that the checks name (a test file in a `run`, or the test files under a folder that a `run` names; none found: all the project's test files) before and after it. The result ends with `[CARL] Tests unchanged: ...` or `[CARL] Warning: the coder changed these test files: ...`.

**Pi.** CARL's `subagent` tool runs the chain: two `pi` processes, one after the other, each with a new context. It works for a single task, a background task (one job, one message) and a chain step. A `tasks` call (parallel) with more than one task refuses a brief that starts the chain, with one sentence: the two sessions of each task would see the test files of the other tasks. One task alone runs it. The result's usage is the sum of the two sessions. `"delegation": {"chain": false}` in `~/.pi/agent/carl.json` turns the chain off.

**OpenCode.** The task tool is OpenCode's own, so `carl-delegation` works around it. The user's decision (2026-10-09): the foreground chain is the design; the background hold is a layer on top, only for the OpenCode versions that CARL checked, and made safe.

*The base (every OpenCode version; only the documented hooks and the SDK):*

1. `tool.execute.before`: the task call's `prompt` becomes the test session's brief, its `description` gets `: tests`, and `background` becomes `false` (also over `carl-background`'s `true`). OpenCode's task tool runs the test session in the foreground.
2. `tool.execute.after`: the plugin starts the code session itself through the SDK: `session.create` (a child of the main session, title `DESCRIPTION: code (@coder subagent)`, agent coder, no task or todo tool, the main session's deny rules) and `session.promptAsync` (agent coder, the test session's model, the code session's brief). It waits until the session ends: its `session.idle` event, or its messages (read every 5 s with `session.messages`: the last message is an assistant message that completed, not for tool calls). Then it reads its last answer.
3. It replaces the tool's output with the one result. Its task id is the code session's, so a later `task_id` goes on with the code session.

The main session waits for both sessions (it holds its slot, but it does nothing). With the hold off, the plugin says so once: a warning in OpenCode's log (`service=carl-delegation`) and a toast in the TUI (`client.tui.showToast`; a headless run has the log only).

*The background hold (a layer).* A workaround until OpenCode has a supported hook that lets a plugin hold a task's completion; CARL removes it then. It is on only when both hold:

- **The version** is one of `VERIFIED_VERSIONS` in the plugin: **1.18.34 and 1.18.35** (checked end to end). The plugin input has no version, and OpenCode 1.18's plugin client has no `global.health()`. The plugin takes the first of: the SDK's `client.global.health()` where the client has it (`GET /global/health`, `{ healthy, version }`); the `package.json` of the npm package that holds the running program (`opencode-ai/bin/opencode.exe`, or `opencode-<os>-<cpu>/bin/opencode`); `PROGRAM --version` run once (5 s limit), only when the running program is OpenCode's (its name starts with `opencode`). A version that is not known: the base.
- **The self-check at load** passes: the SDK has `session.create`, `session.promptAsync`, `session.messages` and `session.get`. It only looks; it sends nothing. The event types cannot be checked without a session, and the base needs the same ones; what only the hold needs (the form of the `<task …>` message, and that a throw in `chat.message` drops the message) is what the version list covers.

The check starts at load; the first chained task waits for it. `CARL_TEST_OPENCODE_VERSION=VERSION` in OpenCode's environment replaces the version that the plugin reads. It is for tests: `0.0.0-test` forces the base on a verified OpenCode.

With the hold on, the task keeps its `background` (`carl-background` sets `true`):

1. OpenCode delivers a background result as a user message to the main session that starts with `<task id="…" state="completed">`. When that message is the test session's, `chat.message` throws (`[CARL] Held`): OpenCode then neither saves the message nor starts a turn of the main agent.
2. The code session runs as in the base. The one result comes as the same kind of message (`<task id="CODE SESSION" state="completed">`, `synthetic`, the main session's agent) through `session.promptAsync`. The main agent thus gets one completion.
3. A test session that failed: its message passes, with the text that the code session did not run.
4. A test session that ran in the background without the hold (another plugin set it after CARL): its message passes, with the text that the code session did not run.

**The state file.** Each held chain has a record in `~/.config/carl/chains/MAIN-SESSION.json` (mode 0600, the folder 0700): the main session, the test session, the stage (`test`: it runs; `held`: its completion was held and the code session did not start yet; `code`: the code session runs), the code session's id once it started, the description, the main session's agent, the project folder, the OpenCode process id and the chain's state (the brief, the test files' hashes, the red start, the test session's report). The record goes when the one result was sent. When OpenCode starts again in that project, the plugin looks at the records after 2 s. For each one whose OpenCode process is gone (a `.claim` file next to it keeps two OpenCode processes from both delivering it), it sends what exists:

- the code session finished (its messages say so): the one result, as if nothing had happened;
- it did not finish, or did not start: `[CARL] Chain: OpenCode stopped before the chain ended: the code session did not finish.` (or `did not run`), the red start, the test session's report and the code session's last answer.

Thus the main session never stays without a result. A chain in the foreground needs no record: OpenCode itself ends the tool call when it stops.

Checked against the real OpenCode 1.18.35 with agent-bench's fake server (2026-10-09, a temp HOME made by `configure.py`; the model asked for the background each time): the hold in the background, and the base in the foreground (`CARL_TEST_OPENCODE_VERSION=0.0.0-test`: the task ran with `background: false`, and the log had the notice). In both, the main agent got one result with both reports; with the hold, the test session's own completion never reached it. A stop during the code session (OpenCode killed, then started again in the project): the record had the stage `code` (mode 0600); at the start, the main session got the result that the code session did not finish, with the test session's report, and the record was gone. `"chain": false` in the plugin's entry turns the chain off.

**Pi** needs no state file for this: its chain is CARL's own code in the `subagent` tool. A background job that dies with Pi loses its result, as any background subagent of Pi does: its sessions are `pi` processes that end with it.

**Risks.** CARL runs a check's command itself (once, before the code session), outside the client's permission prompts, but only a plain test runner or ruff on the project's files (above): the project's tests run the project's code, as the coder's runs do. The OpenCode background hold depends on OpenCode 1.18's behaviour (the form of the `<task …>` message; a plugin error in `chat.message` stops the message without other effects): hence the version list. When OpenCode stops while a held chain runs, its result comes at the next start of OpenCode in that project.

## /code

`/code TASK` gives the task straight to the coder. The main agent does not decide.

- Both clients: the template tells the main agent to give the task to the coder now, as a TOML brief with `mode = "code"`. Before Phase 23.4.3, OpenCode ran the task on the coder directly (`agent: coder`, `subtask: true`). That text was no brief, so the brief check would refuse it.
- A `code.md` of your own stays: the setup changes only a file with `CARL:` in its description.

## The new-file gate (advanced)

The gate stops the main agent's write that makes the Nth new file of a turn. The write fails with `[CARL] Blocked: … is a new file …`, and the message tells the main agent to give the task to the coder. Edits of files that exist pass. After the coder starts, the gate stops nothing more in that turn.

- **Not recommended.** With N = 1 it sent every small task that needs one new file (a `.gitignore`, one test file) to the coder: 4 of 4 on the E4B. With N = 2 it added nothing to the reminder in the measurements.
- **A setting of the dashboard only** (1.12.0): in the Connect tab, open SET UP at its full level and push `g`. It steps off, 1, 2, 3, 5. The dashboard saves it in `config.json` as `delegation.gate` (`./carl.sh config set delegation.gate N` also works: 0 is off, 1 to 99 are the valid values).
- OpenCode and Pi read it from the dashboard within 10 s, with no setup run: on the server Mac from `config.json`, on another computer from the dashboard API (`GET /carl/cache/settings`). When the dashboard does not answer, the gate is off.
- Before 1.12.0 the setup set it (`DELEGATION_GATE=N`). The setup no longer reads that variable.

## What was measured (Phase 23)

**Method.** The agent bench (`tools/agent-bench/`) runs the real OpenCode (1.18.34) and Pi (1.0.2) against CARL's server, with 17 prompts in small fixture projects: 5 large tasks, 4 stuck fixes (the prompt says that two fixes failed), 4 small edits and 4 questions. A run stops at the first decisive action of the main agent: a call to the coder, or a write of its own. 15 tool calls or 300 s with no decision count as "undecided". One run of each prompt for each model, client and setting; default thinking. The pass mark: 90% of large and stuck tasks to the coder, 90% of small tasks and questions kept.

**Results** (the large and stuck tasks that went to the coder, OpenCode / Pi; 2026-10-05 to 2026-10-07):

| Model (Mac) | Without (the rule only) | With the reminder and the two modes |
|---|---|---|
| Qwen3.6 35B A3B Q4 (M3 Pro) | large 2/5, 3/5; stuck 0/4, 0/4 | large 5/5, 5/5; stuck 2/4, 2/4 |
| Gemma 4 12B (M3 Pro) | large 1/5, 2/5; stuck 0/4, 1/4 | large 5/5, 5/5; stuck 3/4, 3/4 |
| Gemma 4 E4B (M2 Max) | large 3/5, 5/5; stuck 2/4, 1/4 | large 5/5, 5/5; stuck 3/4, 3/4 |

- Small tasks and questions: with the reminder, the main agents kept all of them, except one small edit on the E4B in OpenCode.
- The reminder alone made the difference. The two modes do not change the hand-off; they are for the coder's work. A shorter rule and a nudge after the main agent's reads helped large tasks less, and stuck tasks not at all.
- No model reached the pass mark for stuck fixes: a fix that failed looks small, and the models often fix it again themselves.
- The dense Qwen3.8 27B often thought for more than 300 s before its first tool call, so its results show its speed more than its decisions.
- Gemma 4 models are not offered for agent coding (they code poorly as agents), but the hand-off works with them too.

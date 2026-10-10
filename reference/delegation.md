# CARL Reference: The hand-off to the coder

This page tells how CARL makes the main agent give large and stuck coding tasks to the coder subagent, why, and what was measured. For daily use, see [the coder subagent](../USERGUIDE.md#the-coder-subagent-opencode-and-pi) in the user guide.

## The parts

| Part | What it does | Where | Switch |
|---|---|---|---|
| The delegation rule | Tells the main agent when to give a task to the coder, and how to write the task | `client/agents/delegation.md`. OpenCode: `~/.config/opencode/carl/delegation.md` (in `instructions`). Pi: the last block of `~/.pi/agent/APPEND_SYSTEM.md` | comes with the coder |
| The rule for main agents only | Keeps the rule out of the prompt of every subagent | OpenCode: the `carl-delegation` plugin. Pi: the `subagent` tool | comes with the coder |
| The reminder | One line at the end of each of your messages in the main session | `carl-delegation` (OpenCode plugin and Pi extension) | on; `NO_REMINDER=1` turns it off |
| The coder's two modes | `work_mode = "code"` or `work_mode = "tests-only"`, one in each brief | `client/agents/coder.md` | comes with the coder |
| The brief | Each coder task is a brief in TOML (revision 4): the request distilled, the expected outcome, the current state, the design notes, the exact interfaces, requirements and checks by id, the known files | `client/agents/delegation.md`, `client/agents/coder.md`, `client/shared/carl-brief.js` | comes with the coder |
| The brief check | Sends an incomplete brief back to the main agent before the coder starts | `carl-delegation` | on; `"brief": false` turns it off (for measurements) |
| The coder's gates | Keep the coder to its work_mode and away from the files to read only | `carl-delegation` | comes with the coder |
| The chain | A brief with `work_mode = "code"` and `work_type = "new_feature"`: a test session, then a code session, each with a new context; one result. OpenCode: in the foreground; in the background only on the verified versions | OpenCode: `carl-delegation`. Pi: the `subagent` tool. Both: `client/shared/carl-chain.js` | on; `"chain": false` turns it off (for measurements) |
| The Tests setting | When the chain's test session runs: before the code session, after it, or not at all ([The Tests setting](#the-tests-setting)) | `/carl` > Coder subagent > Tests (`CODER_TESTS`); `"coder_tests"` in both clients' `carl.json`, read at each coder task | before code |
| The coder's thinking | Each coder session (the test session and the code session too) thinks as **Coder thinking** says, for the model that the coder runs on | The dashboard: Settings > Server, per model. OpenCode: `coderThinking` of `carl-delegation`, set on each request of the coder by its `chat.params` hook. Pi: `"thinking"` in `~/.pi/agent/carl.json`, read by the `subagent` tool | same as main; off only with a full spec ([The coder's thinking](#the-coders-thinking)) |
| The coder's model | The model that each coder session runs on: the main session's (the default), or an external model of the client ([An external coder model](#an-external-coder-model)) | `/carl` > Coder subagent > Coder model (`CODER_MODEL`). OpenCode: the coder agent's `model`. Pi: `"coder_model"` in `~/.pi/agent/carl.json`, read by the `subagent` tool | same as main |
| `/code` | Gives your task straight to the coder | OpenCode: `~/.config/opencode/command/code.md`. Pi: `~/.pi/agent/prompts/code.md` | comes with the coder |
| The new-file gate | Stops the main agent at its Nth new file in a turn | `carl-delegation` | off; the dashboard only: Connect > Setup, full level, `g` (advanced, not recommended) |

All parts come with the coder (`--coder on`, or `auto` with 2 or more slots or an external coder model). With the coder off, the setup removes them all.

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

Each coder task is a brief in TOML (Phase 23.4.3; revision 4, approved by the user on 2026-10-09: `docs/phase-plans/phase23.4.3/brief-v4-draft.md`). Its `work_mode` says what the coder may change. The two modes are exclusive in one brief:

- **`work_mode = "code"`:** the hand-off writes or changes program code. The coder runs the tests, but it does not change them. A test that looks wrong goes into its report.
- **`work_mode = "tests-only"`:** the hand-off writes or changes tests only. The coder writes tests from the requirements, not from the current code: at least one test for each requirement id, with the id in the test's name or docstring. It does not change the program code. A test that fails because the code is wrong is a finding in its report.

The main agent writes the brief from your request and the project's files. It invents nothing: rules and examples are copied from a file or from your message, and the exact interfaces from your request. Thus, for a large request the rule lets it read first what the brief needs (the files to change, the test command, the project's rules), but it must not write or change a file itself; then it delegates. Before, the rule said "delegate first, do not explore", and the brief then had no real paths or commands. TOML is there to structure the data, not to remove the prose: the user (2026-10-09) wanted the intent carried to the coder (the distilled request, the expected outcome, the design and reference documents), not only hard requirements. The keys (two or three words each; `client/agents/delegation.md` has the whole template):

| Key | What it holds | Required |
|---|---|---|
| `work_mode` | `"code"` or `"tests-only"` | yes |
| `work_type` | `"new_feature"` (new behaviour, in a new file or in existing code), `"follow_up"` (changes to the work the coder just did) or `"bug_fix"` (a fix, a stuck one too) | yes |
| `existing_tests` | the test files that already cover this work (a list of paths) | no; each path must be in the project |
| `task_summary` | the request distilled into a few sentences: what and why. Not a copy, and not a list | yes |
| `expected_outcome` | what the finished work looks like from the user's side | yes |
| `current_state` | what exists now that the coder builds on | for `follow_up` and `bug_fix` |
| `design_notes` | where the logic goes, the patterns to follow, what to reuse | no |
| `exact_interfaces` | every name, signature, command line, output format and message the request states, copied exactly (a list) | no (the template asks for it) |
| `scope_limits` | limits in plain words, for example "no API changes; do not touch config.py" | no |
| `[[reference_doc]]` `doc_path`, `doc_purpose` | architecture or reference documentation to read before starting (a path in the project, or a URL), and why | no |
| `[[known_file]]` `file_path`, `file_action` | the files the main agent knows of: `"create"`, `"change"` or `"read"` (context only, never written). The list does not have to be complete | in `code`: at least one to create or change |
| `[[task_requirement]]` `requirement_id`, `requirement_text` | one point of the request each, with its own id (R1, R2, ...) | at least one |
| `[[acceptance_check]]` `check_id`, `covers_requirements`, `run_command`, `expected_result` | the requirement ids it covers, the command, the result. Every requirement is covered | every requirement in one; `run_command` in each |
| `[[project_rule]]` `rule_text`, `rule_source` | a project rule that applies, copied, and the file it is from | no |
| `[[input_example]]` `example_source`, `example_text` | real input, copied, never invented | no |
| `[failed_attempt]` `run_command`, `error_output` | only for a `bug_fix` that already failed: the command that fails and its exact output | when `[[tried_fix]]` is there |
| `[[tried_fix]]` `fix_change`, `fix_result` | only with `[failed_attempt]`: what was changed and what happened | no |

The template in `client/agents/delegation.md` shows `[failed_attempt]` and `[[tried_fix]]` commented out, under one line that says they are for `work_type = "bug_fix"` only, so that a copy of the template as written (a `new_feature`) passes the check (the user, 2026-10-09: "A").

**What CARL does with it.** `work_mode = "code"` and `work_type = "new_feature"`: CARL's test session, then the code session ([the chain](#the-chain-tests-first-in-a-separate-session)). `work_mode = "code"` and `"follow_up"` or `"bug_fix"`: no test session; the `existing_tests` are frozen for the coder. `work_mode = "tests-only"`: one session that writes tests. When the test session runs is the Tests setting (Phase 23.4.6, [below](#the-tests-setting)): before code (the default), after code, or off.

**The brief check.** Before the coder starts, `carl-delegation` reads the brief and checks it. The brief can be in a `` ```toml `` fence, with text around it. An incomplete brief goes back to the main agent: the call fails with `[CARL] Brief refused` and a list of whole sentences that name the brief's keys, for example `Requirement R2 is in no check's covers_requirements: add it to a check, or add a check for it.` A task text with no TOML at all gets one sentence that asks for the brief. The checks:

- `work_mode` is code or tests-only; `work_type` is new_feature, follow_up or bug_fix; `task_summary` and `expected_outcome` are not empty; `current_state` is not empty for follow_up and bug_fix.
- Each `existing_tests` path is in the project folder (checked in the folder where the coder runs: OpenCode's project, Pi's `cwd`).
- At least one requirement, each with its own `requirement_id` and a `requirement_text`. Every requirement is in the `covers_requirements` of a check. Every check has a `check_id` and a `run_command`, and covers only ids that exist.
- `work_mode = "code"`: at least one known file to create or change (the list does not have to be complete). Each known file has a `file_path` and a valid `file_action`. `tests-only`: no known file needed, and each one to create or change is a test file.
- `[failed_attempt]` and `[[tried_fix]]` only with `work_type = "bug_fix"`; `[failed_attempt]` has `run_command` and `error_output`; `[[tried_fix]]` only with a `[failed_attempt]`, each with `fix_change` and `fix_result`.
- No key that is not in the schema, at the top or in a table (`known_file.path`). A key of the brief's first revision gets the new key's name: `mode` → `work_mode`, `tests` → `work_type` and `existing_tests`, `goal` → `task_summary`, `scope` → `scope_limits`, `file` → `known_file`, `requirement` → `task_requirement`, `check` → `acceptance_check`, `constraint` → `project_rule`, `example` → `input_example`, `error` → `failed_attempt`, `tried` → `tried_fix` (and plural forms such as `known_files` → `known_file`).

**The refusal guides the fix** (Phase 23.4.3 addendum; user, 2026-10-10: the refusal must always tell the main agent what is missing and the expected input). The E4B fixed one point of a brief and dropped another, wrote keys under the wrong block, broke the TOML, and once left the loop by switching `work_mode`:
- Every refusal of a readable brief ends with a checklist of the parts a brief needs and their state (`- [[acceptance_check]] covering every requirement  R5 not covered`), headed "keep each part that is ok when you fix the rest". The check lists every problem it finds, not only the first.
- A requirement in no check gets a ready `[[acceptance_check]]` with that id (all uncovered ids in one).
- A key under the wrong block (`acceptance_check.known_file`) gets TOML's rule (every line after a `[[...]]` header belongs to that block) and where it goes: its own block, the block it belongs to (`file_path`: `[[known_file]]`), or above the first header for a key of the brief itself. Not "remove it".
- Broken TOML: the reader's reason, the line as written, and the right form of the key on it (a block's key: the block, "one block for each item, not a list in [ ] or { }"; a key over several lines: the key above it).
- A task text with no brief at all: the brief's form (the template's needed parts).
- No switch to tests-only: after a refused brief in `work_mode = "code"`, a brief in `work_mode = "tests-only"` in the same turn is refused ("Keep work_mode = "code" and fix the points of the last refusal", with those points), until a code brief is taken or the user writes again (`Turn.brief` in `carl-delegation.js`). The E4B had sent its refused code task as tests-only, which needs no file to change: the coder wrote tests and no program code.

The reader is CARL's own (no package): strings in all four forms, lists, inline tables, `[table]`, `[[list of tables]]` and comments. It accepts what small models often write (an unknown escape such as `\d`, a `{ table }` over several lines). Errors give the line number. It also reads the same keys as JSON (a `` ```json `` fence, or a JSON object with text around it; the sentences then name the keys the JSON way); agent-bench's `brief_json` variant is obsolete (the user chose TOML). A task that continues an earlier one (OpenCode's `task_id`) is not checked. OpenCode: the check throws in `tool.execute.before`, as the new-file gate does. Pi: the `tool_call` event blocks the subagent call (also a coder item in `tasks` or `chain`).

**Checking a brief by hand** (Phase 23.4.4; user, 2026-10-09: "checking the template with a local script should be a part of the carl plugin set"). The check is also a command, `carl-brief-check.mjs`, installed next to `carl-brief.js` wherever the setup installs it: `~/.config/opencode/plugins/carl-delegation/`, `~/.pi/agent/extensions/carl-delegation/` and `~/.pi/agent/extensions/subagent/` (in the repository: `client/shared/carl-brief-check.mjs`). It needs node (CARL's client setup installs it in `~/.local/bin` when it is not there):

```
node ~/.config/opencode/plugins/carl-delegation/carl-brief-check.mjs brief.toml
node ~/.pi/agent/extensions/carl-delegation/carl-brief-check.mjs < brief.toml
node ~/.pi/agent/extensions/carl-delegation/carl-brief-check.mjs --root ~/projects/notes brief.toml
```

- The input is a file, or the standard input (no file, or `-`): a brief in TOML or JSON, or a task text with a brief in it (a `` ```toml `` fence with text around it is fine), as the main agent writes it.
- It prints the format (`TOML`, `JSON`, or `none`), then `The brief is valid.` or each problem as a sentence, the same sentences that the main agent gets when CARL refuses a brief (a TOML error names its line).
- `--root DIR`: the project folder. Each `existing_tests` path must be in it, as in the clients. Without `--root`, that rule is not checked (agent-bench checks saved briefs, with no project at hand).
- Exit code: 0 the brief is valid; 1 it has problems, or the text has no brief; 2 the command is wrong or the file cannot be read. `--help` shows the usage.
- `--json`: the standard input is a JSON list of task texts, the output a JSON list of `{format, valid, problems}`. agent-bench checks the briefs of its runs this way (`tools/agent-bench/agentbench/brief_check.mjs` runs the command's `--json` mode with node from `find_node()`): the harness, the clients and a user have one checker.

**The coder's gates.** In the coder's own session, `carl-delegation` keeps the coder to its brief. A write is refused with one sentence (`[CARL] Blocked: ...`) when:

- the file is a known file with `file_action = "read"` (it is never written);
- `work_mode = "code"`: the file is a test file;
- `work_mode = "tests-only"`: the file is not a test file.

Any other file passes: the known files are a start, not a limit. The gate on "only the listed files" went with revision 4 (the user, 2026-10-09): it would keep the coder from making a module file, and so break the ports and adapters directive of its engineering standards. A test file is a file under `tests/` or `test/`, or a file named `test_*.py`, `*_test.py`, `*.test.*` or `*.spec.*` (`isTestFile` in `carl-brief.js`). The gates see the file tools (write, edit, patch) and the plain shell writes (redirections, `tee`, `touch`, `cp`, `mv`, `sed -i`). Paths outside the project folder pass. How the plugin finds the coder's session: OpenCode, `chat.message` names the agent of each user message; the first message of a coder session is its brief. Pi: the subagent tool runs the coder as its own Pi process with `CARL_AGENT` set; its prompt (`Task: ` and the brief) gives the brief.

**The coder's report** starts with a TOML block, its keys of two or three words (the user: one-word keys confuse the model): `outcome_summary` (what it built and how that meets `expected_outcome`), `brief_deviations` (where it departed from `design_notes` or `exact_interfaces`, and why), `task_status` (done, partly or blocked), one `[[requirement_result]]` for each requirement id (`requirement_id`, `requirement_status`, `requirement_note`), one `[[check_result]]` for each check id (`check_id`, `run_result`: pass, fail or not run; `run_summary`: the summary line of the run), `[[changed_file]]` (`file_path`, `change_summary`), `[[test_finding]]` (tests-only: the failing tests, `test_name`, `requirement_id`, `failure_reason`) and `[[open_issue]]` (`issue_text`). A "Needs a browser check" section can follow the block. `parseReport` in `carl-brief.js` reads it (it needs `task_status`).

CARL does not make or enforce a project's architecture or plans: the brief only carries the rules that the project already has.

## The chain: tests first, in a separate session

The user's decision (2026-10-09): the tests come from a session that did not see the code, so that they are not vacuous or bent to the code. One `coder` agent with its two modes; CARL chains two new sessions. There is no separate tester agent.

**When.** The brief has `work_mode = "code"` and `work_type = "new_feature"`, no `[failed_attempt]` and no `[[tried_fix]]`, and the task does not continue an earlier one (OpenCode's `task_id`). Else the coder runs one session, as before. A brief with `work_mode = "tests-only"` is the test session alone.

**The steps** (`client/shared/carl-chain.js`):

1. **The test session.** A new coder session gets the brief in `work_mode = "tests-only"`: `task_summary`, `expected_outcome`, `current_state`, `exact_interfaces`, the requirements, the checks, `scope_limits`, `[[reference_doc]]`, `[[project_rule]]` and `[[input_example]]`. It does not get `design_notes` or the known files (the user, 2026-10-09: the tests come from the behaviour, not from the implementation), nor `existing_tests`. It writes tests (at least one for each requirement id), runs them and reports.
2. **The red start.** At least one new test must fail before any code. The test session's report decides (a failed check or a finding), unless CARL ran a check itself: then CARL's run decides. CARL runs a check's `run` only when it is a plain test runner or ruff on the project's files ([the allowlist](#the-checks-that-carl-runs-itself)), once, in the project folder, with a 120 s limit. It looks at the checks whose `run` names tests or a test file that the session wrote; when none does, at every check. Any other command is not run, and one line of the result says so: ``CARL did not run `sh tests/check.sh` itself (it runs only a plain test runner or ruff on the project's files).`` No red start: the code session still runs, and the result has a warning.
3. **The freeze.** CARL hashes the project's test files (SHA-256). It looks at files under `tests/` or `test/` and files named `test_*.py`, `*_test.py`, `*.test.*` or `*.spec.*`; tool folders (`.git`, `node_modules`, `.venv`, ...) are left out. The files that changed during the test session (and the test files in its report) are its test files.
4. **The code session.** A new coder session gets the original brief (`work_mode = "code"`, with `design_notes` and the known files) and CARL's `[test_session]` table, with two- or three-word keys as the rest of the brief (the user, 2026-10-09): `test_files` (the test session's test files), `session_summary` (its outcome summary), `session_notes` (its open issues) and one `[[test_session.failing_test]]` for each failing test (`test_name`, `requirement_id`, `failure_reason`: the keys of the report's `[[test_finding]]`). `client/agents/coder.md` tells the coder what the table means. Its gates refuse every test-file write.
5. **One result.** After the code session, CARL hashes the test files again. The main agent gets one result:

```
[CARL] Chain: the coder ran in two new sessions: first the tests (work_mode tests-only), then the code (work_mode code).
Red start: CARL ran `python -m pytest tests/test_model.py -q` before the code: it failed (exit 1).
Tests unchanged after the code session (tests/test_model.py).
Run the checks yourself before you answer the user.

## The test session's report (work_mode tests-only)
...
## The code session's report (work_mode code)
...
```

- No red start: `[CARL] Warning: no new test failed before the code (no red start): ...`. The test session wrote no test file: `[CARL] Warning: the test session wrote no test file.`
- A test file that differs after the code session: `[CARL] Warning: the code session changed these test files after the test session: ...`.
- The test session failed (the session itself, not a test): the code session does not run, and the result says so.
- The main agent then runs the checks itself before it answers you (the delegation rule).

### The Tests setting

`/carl` > Coder subagent > **Tests** (Phase 23.4.6; `CODER_TESTS` in `~/.config/carl/client-install.env`, for one computer). The setup writes it into both state files as `"coder_tests"` (`~/.config/opencode/carl.json`, `~/.pi/agent/carl.json`). OpenCode's `carl-delegation` (its option `stateFile`) and Pi's `subagent` tool read it at each coder task, so a change needs no restart. A missing or unknown value is `before`. It changes only a task that would get the chain (above); follow-ups and fixes are the same in each value.

- **before** (the default): the chain above: the test session, then the code session.
- **after**: the same two sessions the other way round (`Chain` with the order `after`). The code session gets the original brief (its gates refuse every test-file write; CARL hashes the test files before and after it). Then the test session gets the test brief (as above: no `design_notes`, no known files) and writes its tests against the code that is there. CARL then runs the checks once ([the allowlist](#the-checks-that-carl-runs-itself)); a failing run, or failing tests in the test session's report, is a finding. In OpenCode, the task tool runs the code session (`…: code`) and the plugin starts the test session (`…: tests`). The one result:

```
[CARL] Chain: the coder ran in two new sessions: first the code (work_mode code), then the tests (work_mode tests-only), as the Tests setting "after code" on this computer says.
The new tests fail: CARL ran `node --test tests/check.test.mjs` after both sessions: it failed (exit 1). Each failure is a finding (the test session's report says why): give the fixes to the coder (work_type = "follow_up", the new test files in existing_tests).
The test session wrote: tests/check.test.mjs.
Run the checks yourself before you answer the user.

## The code session's report (work_mode code)
...
## The test session's report (work_mode tests-only)
...
```

  When they pass: ``CARL ran `...` after both sessions: it passed.`` A failed code session stops the chain (no test session). A test file that the code session changed is a warning.
- **off**: one session (`work_mode = "code"`), and its result ends with `[CARL] Tests: the Tests setting is off on this computer, so CARL ran no test session, and the coder (work_mode code) wrote no tests. If the work needs tests, give the coder a tests-only task.`

OpenCode's held chain (the background hold) works the same in each order. Its record names the task tool's session `first` and the plugin's `second` (stages `first`, `held`, `second`; `"v": 2`); a record from before 23.4.6 (`test`, `code`) is read the same.

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

**`work_type = "follow_up"` or `"bug_fix"`** (with `work_mode = "code"`). One session, no test session. The `existing_tests` are frozen for the coder: CARL hashes them (a file as it is, a folder as the test files under it) before and after the session, and its gates refuse every test-file write. The result ends with `[CARL] Tests unchanged: ...` or `[CARL] Warning: the coder changed these test files: ...`. With no `existing_tests`, nothing is hashed.

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

**The state file.** Each held chain has a record in `~/.config/carl/chains/MAIN-SESSION.json` (mode 0600, the folder 0700): the main session, the first session (`first`: the task tool's; the test session, or with Tests `after code` the code session), the stage (`first`: it runs; `held`: its completion was held and the second session did not start yet; `second`: the second session runs), the second session's id once it started (`second`), the description, the main session's agent, the project folder and its identity (the folder's device and inode; OpenCode's project id), the OpenCode process id and the chain's state (the brief, the test files' hashes, the red start, the test session's report). The record goes when the one result was sent. When OpenCode starts again in that project, the plugin looks at the records after 2 s. A record is delivered only when it is still this project: the same folder (a folder made again at the same path is another one), the same OpenCode project, and its main session is still there in this folder; else it is dropped, never delivered (the 23.4.5 hand check: a new project at the same path got an old chain's result, and the old session started to work in the new project). For each one whose OpenCode process is gone (a `.claim` file next to it keeps two OpenCode processes from both delivering it), it sends what exists:

- the code session finished (its messages say so): the one result, as if nothing had happened;
- it did not finish, or did not start: `[CARL] Chain: OpenCode stopped before the chain ended: the code session did not finish.` (or `did not run`), the red start, the test session's report and the code session's last answer.

Thus the main session never stays without a result. A chain in the foreground needs no record: OpenCode itself ends the tool call when it stops.

Checked against the real OpenCode 1.18.35 with agent-bench's fake server (2026-10-09, a temp HOME made by `configure.py`; the model asked for the background each time): the hold in the background, and the base in the foreground (`CARL_TEST_OPENCODE_VERSION=0.0.0-test`: the task ran with `background: false`, and the log had the notice). In both, the main agent got one result with both reports; with the hold, the test session's own completion never reached it. A stop during the code session (OpenCode killed, then started again in the project): the record had the stage `code` (mode 0600; since 23.4.6 the stage is `second`); at the start, the main session got the result that the code session did not finish, with the test session's report, and the record was gone. `"chain": false` in the plugin's entry turns the chain off.

**Pi** needs no state file for this: its chain is CARL's own code in the `subagent` tool. A background job that dies with Pi loses its result, as any background subagent of Pi does: its sessions are `pi` processes that end with it.

**Risks.** CARL runs a check's command itself (once, before the code session), outside the client's permission prompts, but only a plain test runner or ruff on the project's files (above): the project's tests run the project's code, as the coder's runs do. The OpenCode background hold depends on OpenCode 1.18's behaviour (the form of the `<task …>` message; a plugin error in `chat.message` stops the message without other effects): hence the version list. When OpenCode stops while a held chain runs, its result comes at the next start of OpenCode in that project.

## The coder's thinking

The main session and the coder have a thinking setting each, for each model (Phase 23.4.4): **Main thinking** and **Coder thinking** in the dashboard (Settings > Server). The coder also has its own temperature (0.6, OpenCode; not with an external coder model).

- **Default: same as main** (`main` in `config.json`; the user, 2026-10-09: "default should be the same thinking mode as your main session"). The coder thinks as the main session does with the model that it runs on. CARL sends no thinking value of its own for the coder.
- **On:** a model with effort levels (the 27B builds) thinks at `medium` (the best of 9 coder runs on the 35B, 2026-10-02); the other models are on. The user (2026-10-09): coders do better with thinking on, because they solve problems.
- **Off:** use it only with a full spec (spec-kit or a similar tool). A coder without thinking does well only when every requirement is written down. The dashboard shows this note when the coder thinks off: Coder thinking off, or same as main with Main thinking off.
- **The model that the coder runs on decides.** Both clients apply the value of that model, request by request. In router mode, a coder on another model gets the value of that model. The coder runs on the main session's model (Coder model `same as main`): OpenCode's task tool gives a subagent with no model of its own the model (and the variant) of the main agent's message; Pi's `subagent` tool passes the session's model (`ctx.model`). Checked with OpenCode 1.18.35 and Pi 1.1.0 against agent-bench's fake server: a session on another model than the config's default gave the coder that model, and that model's Coder thinking ([details](client-configs.md#carls-coder-thinking-on-one-computer)).
- **One computer can have its own value:** `/carl` > Coder subagent > Coder thinking, for the model of the session that `/carl` is opened in. `dashboard default` removes that computer's value ([the panel](plugins.md#carl-panel-the-carl-panel-opencode-and-pi)).
- **Each coder session uses it:** in a chain, the test session and the code session both use Coder thinking.
- **OpenCode:** the coder agent has no `reasoningEffort`. `carl-delegation` has the option `coderThinking`: `{"PROVIDER/MODEL": EFFORT}` (`none` for off, `high` for on, or the level), only for the models whose Coder thinking is not `main`. Its `chat.params` hook sets `output.options.reasoningEffort` on each request of the coder agent (`coder` or `carl-coder`) whose model has an entry. OpenCode sends it as `reasoning_effort`. The test session (the task tool) and the code session (started by `carl-delegation`) are both coder sessions. Other agents and models with no entry: the request stays as it is (the model's own `reasoningEffort`, Main thinking).
- **Pi:** the `subagent` tool reads `"thinking": {"coder": {"PROVIDER/MODEL": LEVEL}}` in `~/.pi/agent/carl.json` and starts each coder session with `--thinking LEVEL` for the model of the coder. A model without an entry (`main`, or not one of CARL's): the coder uses the level of the session, as before. Other agents always use the level of the session.
- The clients get a change at their next update (the Connect tab, `u`; other computers: `P`).
- **An external coder model** (Phase 23.4.5): the dashboard's per-model table does not apply. Coder thinking is `model default` (CARL sends no thinking value: the client's own default for that model), or a level that `/carl` sets for it: OpenCode, a variant of that model (`coderVariant`; `chat.params` merges that variant's options); Pi, one of its thinking levels (`--thinking LEVEL`). Without a level, Pi starts the coder with no `--thinking`.

## An external coder model

`/carl` > Coder subagent > **Coder model** can put the coder on a model of another provider that OpenCode or Pi can use, free or paid (Phase 23.4.5; user, 2026-10-09: "model selection could also allow you to choose an external free or paid model to do the code"). The choice is for one computer (`CODER_MODEL=PROVIDER/MODEL` in `~/.config/carl/client-install.env`; the setup and the sync keep it).

| | Same as main (the default) | An external model |
|---|---|---|
| Where the coder's requests go | CARL's server | The provider of that model (OpenCode or Pi sends them, with its own key) |
| The server's slots | The coder uses a slot | No slot. The setup's coder rule turns the coder on also with 1 slot (`auto`), and `/carl` gives no 1-slot warning |
| Auto fit | Reserves a slot for the coder (2 slots first) | The same: Auto fit always counts a local coder (user, 2026-10-09: the server does not know which computers use an external coder, and a computer can go back to the local model at any time) |
| The brief, the check, the gates, the chain | As described above | The same. Both chain sessions run on the external model: OpenCode's task tool starts the test session on the coder agent's `model`, and `carl-delegation` starts the code session with the model of the test session (the task's metadata), or, when that is not known, with no model, so OpenCode takes the agent's own |
| Sampling | The coder's temperature 0.6 (OpenCode) and the server's sampling | None of CARL's: the coder agent has no `temperature`; `chat.params` sets no sampling value |
| Thinking | Coder thinking of that model (the dashboard; `/carl` per computer) | `model default`, or a level for that model ([The coder's thinking](#the-coders-thinking)) |
| The step limit | 80 (OpenCode `steps`) | 80 |
| The disk cache, the model check | On the coder's requests | Not on the coder's requests: they act only on CARL's provider ([the plugins](plugins.md#carl-cache-the-disk-cache)) |
| The dashboard | The Live tab shows the coder's requests | The Live tab shows only the main session. Connect > Clients shows each computer's coder model |

- **The data boundary.** The brief, the files that the coder reads and the results of its tools go to the provider. `/carl` says where ("The coder's work goes to openrouter.ai.") and that a paid model costs money. CARL shows no cost.
- **The provider's key** is the client's own (OpenCode's auth or config, Pi's). CARL never reads, copies or logs it: the setup writes only the model's name, `/carl` reads only the model list's names, base URLs and prices, and the disk cache reads the key only of CARL's provider.
- **When the provider fails** (no network, no quota, a key that expired), the coder's task fails with the provider's error, and the main agent sees it. There is no fallback to a CARL model (user, 2026-10-09: "The task fails when the provider fails with an error"). In OpenCode, the task tool's error, or the code session's error in the chain's result; in Pi, the coder process's error. Nothing in CARL starts the task again on another model (tests: `tests/js/opencode-plugins.test.mjs`, `tests/js/pi-chain.test.mjs`).
- **The context limit** of the external model applies to the brief and the coder's work, as the client knows it.
- No benchmark runs of external coders in agent-bench (user, 2026-10-09: "this is the end user deciding not something carl is responsible for").
- Checked with the real OpenCode 1.18.35 and Pi 1.1.0 (2026-10-09): a temp HOME made by `configure.py` with `--coder-model`, agent-bench's fake server as CARL's server, and a second fake server as the external provider. The main session's requests went to CARL's server; every request of the coder (the test session and the code session of the chain) went to the external server, without `temperature`, `id_slot` or a CARL thinking value; the chain gave its one result; the disk cache logged nothing for the coder's sessions.

## /code

`/code TASK` gives the task straight to the coder. The main agent does not decide.

- Both clients: the template tells the main agent to give the task to the coder now, as a TOML brief with `work_mode = "code"`. Before Phase 23.4.3, OpenCode ran the task on the coder directly (`agent: coder`, `subtask: true`). That text was no brief, so the brief check would refuse it.
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

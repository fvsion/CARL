# CARL Reference: The hand-off to the coder

This page tells how CARL makes the main agent give large and stuck coding tasks to the coder subagent, why, and what was measured. For daily use, see [the coder subagent](../USERGUIDE.md#the-coder-subagent-opencode-and-pi) in the user guide.

## The parts

| Part | What it does | Where | Switch |
|---|---|---|---|
| The delegation rule | Tells the main agent when to give a task to the coder, and how to write the task | `client/agents/delegation.md`. OpenCode: `~/.config/opencode/carl/delegation.md` (in `instructions`). Pi: the last block of `~/.pi/agent/APPEND_SYSTEM.md` | comes with the coder |
| The rule for main agents only | Keeps the rule out of the prompt of every subagent | OpenCode: the `carl-delegation` plugin. Pi: the `subagent` tool | comes with the coder |
| The reminder | One line at the end of each of your messages in the main session | `carl-delegation` (OpenCode plugin and Pi extension) | on; `NO_REMINDER=1` turns it off |
| The coder's two modes | `Mode: code` or `Mode: test`, one in each task | `client/agents/coder.md` | comes with the coder |
| The hand-off form | The fields of each coder task | `client/agents/delegation.md`, `client/agents/coder.md` | comes with the coder |
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

## The coder's two modes and the hand-off form

Each coder task starts with its mode. The two modes are exclusive in one task:

- **`Mode: code`:** the coder writes and changes the program code. It runs the tests, but it does not change them. A test that looks wrong goes into its report.
- **`Mode: test`:** the coder writes tests from the requirements, not from the current code. It does not change the program code. A test that fails because the code is wrong is a finding in its report.

For code with tests, the main agent can send a `Mode: test` task first, then a `Mode: code` task that makes the tests pass.

The main agent writes each task in this form, from your request and the project's files:

```
Mode: code | test
Goal: one or two sentences.
Files: the files it may create or change.
Requirements: what it must do, point by point.
Acceptance: the checks or tests that say it is done.
Constraints: the project's rules that apply (architecture, style, what not to touch).
Error: (a fix that failed) the exact error or failing test output.
Tried: (a fix that failed) what was already tried.
```

The coder changes only the files in `Files`, checks and reports each `Acceptance` item, and follows `Constraints` before its own defaults. CARL does not make or enforce a project's architecture or plans: the form only carries the rules that the project already has.

## /code

`/code TASK` gives the task straight to the coder. The main agent does not decide.

- OpenCode: the command runs the task on the coder (`agent: coder`, `subtask: true`) with `Mode: code`.
- Pi: the template tells the main agent to give the task to the coder now, in the hand-off form.
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

# agent-bench: the hand-off to the coder

agent-bench measures one decision of the main agent in OpenCode and in Pi: does it give a coding task to the `coder` subagent, or does it do the task itself? It sends fixed requests to the clients and records the first tool call of each one, and the first decisive action (the coder, or a change to the files). A later step (the agent quality tests of Auto-tune) uses the same harness.

The names and units are the names and units of [the glossary](../../reference/glossary.md).

## 1. What it measures

CARL's rule (`client/agents/delegation.md`, and the coder's description in `client/agents/coder.md`) tells the main agent:

- Give the task to `coder` when it is **large** (3 or more files, about 150 or more lines, a new module, CLI or package, an implementation plus tests, a feature in many steps) or when the agent is **stuck** (a fix failed two times).
- Do the task itself when it is **small** (one file, one function) or a **question**.

The main agent gives a task to the coder with these calls:

| Client | The call |
|---|---|
| OpenCode | the `task` tool with `subagent_type: "coder"` |
| Pi | the `subagent` tool with `agent: "coder"` (or a `tasks` or `chain` item with this agent) |

The harness measures each run in two ways.

**Strict: the first tool call decides.**

| Decision | When |
|---|---|
| `delegated` | The first tool call is the call to the coder above (`coder` or `carl-coder`). |
| `self` | The first tool call is a different tool: `read`, `write`, `edit`, `bash`, `grep`, a different subagent ... |
| `answer` | The turn ends with text and no tool call. |
| `error` | The client failed, the server sent an error, or the time limit came first. The result keeps the error text. |

**Practical: the first decisive action decides.** Many models look at the project first (read, glob, `ls`) and decide after that. The harness thus lets the client continue past the looks, to the first decisive action:

| Decision | When |
|---|---|
| `delegated` | The call to the coder. |
| `self-write` | The main agent changes files itself: OpenCode `write`, `edit`, `patch`, `multiedit`, `apply_patch`; Pi `write`, `edit`; or a `bash` command that clearly writes files in the project (see below). |
| `answer` | The turn ends with no decisive action. |
| `undecided` | No decisive action after 15 tool calls, or 300 s after the first model step (`--max-tools`, `--max-seconds`). Until 2026-10-06 the limits were 8 and 240 s (measure `practical-v1`): models then delegated after 8-11 looks, so the limits went up. |
| `error` | As above. |

All other tool calls are **looks**: `read`, `glob`, `grep`, `list`, `tool_search`, `webfetch`, the todo tools, a subagent that is not the coder, and `bash` commands that do not write (`ls`, `cat`, `find`, `rg`, the tests ...). The result has the number of looks before the decision.

A `bash` command writes files in the project (conservative: only these count):

- a redirection `>`, `>>`, `&>` or `>|` into a file of the project (not `/dev/...`, not a path outside the project, not `~/...`, not a path in a variable). The text of a here-document and quoted text do not count, so `cat <<EOF` counts only with a redirection or `tee`;
- `tee FILE`, `sed -i`, `perl -i`, `mkdir`, `touch`, `cp`, `mv`, `install`, `ln` or `rsync` into the project, `patch`, `git apply`.

A program that writes from the inside (for example `python3 -c "open('x', 'w')..."`) does not count. A command that the harness cannot read (an open quote) does not count.

The run stops when the practical decision is known (OpenCode: also when the step of the decisive tool has ended, at most 3 s later). A run of a different limit is a different measure (`practical-v2-t25-s480`, for example).

A decision is right when it is `delegated` for a large or stuck request. For a small request or a question, every decision except `delegated` is right: `self`, `self-write`, `answer` and `undecided` (the main agent did not give the task away). Thus `undecided` is wrong for a large or stuck request. An `error` is not right and not wrong: the report counts it apart.

## 2. The parts

| Path | What it is |
|---|---|
| `bench.py` | The CLI: `prepare`, `run`, `report`, `tokens`, `fetch`, `drop`, `variants` |
| `prompts.json` | 16 requests, 4 for each category (large, stuck, small, question). Each has its fixture and the decision it expects. The large requests with hidden tests are also the full runs. |
| `fixtures/` | 3 small projects: `pycli` (a notes CLI with tests), `pylib` (a text statistics library with one failing test), `webapp` (a static to-do page). Each has a README. |
| `hidden/` | The hidden tests of the full runs. The harness copies them into the project only after the run. |
| `variants/` | One Python file for each variant (see section 7). `baseline.py` changes nothing. |
| `fakeserver.py` | A fake OpenAI-compatible server with scripted replies, for the tests. It needs no model. Its `/tokenize` counts words. |
| `agentbench/events.py` | The JSON event streams of the clients, and the decision. No I/O. |
| `agentbench/clients.py` | The commands of OpenCode and Pi, a decision run and a full run. |
| `agentbench/briefs.py`, `brief_check.mjs` | The coder briefs of a run: their text, CARL's check of them (node with the repo's `client/shared/carl-brief-check.mjs`, the command that ships with CARL's client plugins: one checker), the refusals, the tokens (section 6). |
| `agentbench/server.py` | CARL's server for one model: the launcher `./carl.sh`, `/health`, stop. |
| `agentbench/home.py` | The harness HOME: the client package and `./setup`. |
| `agentbench/library.py`, `models.py` | The model copies: fetch and drop, and their rules. |
| `agentbench/matrix.py`, `results.py`, `report.py`, `fixtures.py`, `prompts.py`, `proc.py`, `variant.py` | The matrix, the results file, the tables, the project copies, the prompts, the child processes, the variants. |

The tests are in `tests/agent_bench/`.

## 3. Safety

- The clients run in a **harness HOME**, never in your HOME. `prepare` refuses your HOME and every folder that holds it. `run` refuses a folder that `prepare` did not make (it has no `.agent-bench.json`).
- The server uses its own work folder: the settings folder (`CARL_CONF_DIR`), the client folder (`CARL_CLIENT_DIR`), the API key (`API_KEY_FILE`), the log, and a HOME for the launcher (the launcher writes its chat templates to `~/models/templates`; with this HOME they go to the work folder). It does not change `~/.config/carl` or `~/models`. The models folder is given with `MODELS_DIR`.
- The server listens on 127.0.0.1, port 8097 by default. The harness never uses port 8080. It refuses a port that is in use.
- The harness stops the server by the PID of the process group that it started. It does not stop processes by name.
- `fetch` and `drop` only read the library. `drop` removes only files that the catalogue names, only when the library has a verified copy, and never a file on the keep list.
- A long run keeps the Mac awake with `caffeinate -i -s`.

## 4. Prepare the clients

`prepare` makes the harness HOME. It starts CARL's server for one model, makes the client package (`./carl.sh package --anyway`), unzips it in the harness HOME and runs its `./setup --yes --coder auto` (with `NO_SYNC_SERVICE=1` and `NO_PROFILE=1`). Then it installs the client versions of the baseline (OpenCode 1.18.34, Pi 1.0.2) and stops the server.

```bash
python3 tools/agent-bench/bench.py prepare --home ~/carl-phase23/bench-home --model qwen3.6-35b-a3b
```

- The model must be on the local disk (see section 5).
- `--latest` keeps the newest client versions. `--opencode-version` and `--pi-version` set other versions.
- `--coder auto|on|off` sets the coder subagent (`./setup --coder`). `auto` (the default) is the setup's own rule: the coder is on when the server runs 2 or more slots, else off (with 1 slot, a subagent takes the slot of the main session). `on` and `off` force it. Before Phase 23.4.4, the harness always gave `--coder on`.
- The HOME marker (`.agent-bench.json`) keeps the choice (`coder`) and what the setup did (`coder_state`: `on` or `off`, from its `Coder subagent:` line).
- The setup installs Node 22 into the harness HOME when the computer has no Node 22.19 or newer. It needs the network for npm.

Before each model batch, `run` makes a new client package and runs `./setup --no-install --coder CODER` again (`run --coder`, default `auto`). The client configs then have the model of the batch, the port and the context of the server that runs. With `auto`, the coder follows the slots of that server. With `--no-server` there is no new setup: the HOME keeps the coder of its last setup.

## 5. Models: fetch and drop

The models run from the local disk, never from the network share. The share is the **library**: `/Volumes/IP/AI_models/gguf` (`--library`). The models folder is `~/models/gguf` (`--models-dir`).

```bash
python3 tools/agent-bench/bench.py fetch gemma-4-26b-a4b        # the model and its MTP drafter
python3 tools/agent-bench/bench.py drop gemma-4-26b-a4b --dry-run
python3 tools/agent-bench/bench.py drop gemma-4-26b-a4b
```

- `fetch` copies the files of the model that `host/catalog.json` names (`hf.file`, and `draft.file` for a Gemma model) and checks the SHA-256 of each copy against the catalogue. A bad copy is deleted and copied again one time. A local copy with the correct SHA-256 stays.
- `drop` removes a local copy only when the library has a verified copy: the same size as the catalogue, and the catalogue's SHA-256 in the library's `SHA256SUMS` file. When `SHA256SUMS` has no line for the file, `drop` reads the library copy and checks its SHA-256 (this is slow on the share).
- `drop` never removes a file that is not in the catalogue, a link, or a file on the keep list. The keep list always has `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` and `orcarouter_Qwen3.8-27B-Uncensored-Q4_K_M.gguf` (your own models on the M3 Pro). `--keep FILE,FILE` adds files.
- `run --fetch` fetches each model before its batch. `run --drop` drops it after its batch.

## 6. Run and report

```bash
python3 tools/agent-bench/bench.py run --home ~/carl-phase23/bench-home \
    --models qwen3.6-35b-a3b,gemma-4-e4b --clients opencode,pi --thinking default,off --runs 2 \
    --variant baseline --out ~/carl-phase23/baseline.jsonl --fetch --drop
python3 tools/agent-bench/bench.py report ~/carl-phase23/baseline.jsonl          # text
python3 tools/agent-bench/bench.py report ~/carl-phase23/baseline.jsonl --md --output baseline.md
```

**The matrix.** The runs go model by model (one server start for each model): client, thinking, run, prompt. `--categories large,stuck` and `--prompts ID,ID` select prompts.

**Resumable.** Each run writes one line to the results file (JSONL) when it ends. A run that has a line is skipped. To continue after a stop, give the same command again. `--retry-errors` runs again the runs whose result is an error.

**For each model**, `run` does these steps:

1. With `--fetch`: copy the model in from the library.
2. Start the server: `HOME=… MONITOR=0 CARL_CONF_DIR=… CARL_CLIENT_DIR=… API_KEY_FILE=… MODELS_DIR=… PORT=8097 LOG_FILE=… ./carl.sh --model NAME --local --slots 2`. The settings folder is empty, so the server uses the catalogue's settings. The coder needs the second slot: with `--slots 1`, `--coder auto` turns the coder off. Wait for `/health` 200.
3. Write the client configs again (section 4), then apply the variant.
4. For each run: make a fresh copy of the fixture (a new folder, `git init`, one commit), start the client in it, read its events, record the result.
5. Stop the server. With `--drop`: remove the local copy.

**Thinking.** `default` is the client's own setting: OpenCode with no `--variant` (CARL's config: reasoning effort high), Pi with the level of `settings.json` (`low`; CARL's model map gives the server `high`). `off` is OpenCode's `none` variant and Pi's `:off`. A level name (for example `high`) also works. The result has the level that the client used (`thinking_level`).

**Decision runs** (the default) start the client with these commands:

| Client | The command (in the fixture copy) |
|---|---|
| OpenCode | `opencode run --format json --thinking --print-logs --log-level INFO -m llamacpp/MODEL [--variant none] "PROMPT"` |
| Pi | `pi -p --mode json --model llamacpp/MODEL:LEVEL -- "PROMPT"` |

The client runs until the practical decision is known (section 1), then the harness stops its process group. The time limit for a run is 600 s (`--limit`); when the model does not start in this time, the result is an error.

**Full runs** (`--full`) let a large task go to its end (limit 1800 s). They use the prompts that have hidden tests. OpenCode runs as `opencode serve` (the harness sends the task through its HTTP API), and Pi runs as `pi --mode rpc`, so the coder can finish in the background and its result can come back to the main agent. The harness then records:

- Did the main agent call the coder, and did the coder's result come back?
- Did the main agent run the tests (after the coder's result)?
- Do the hidden tests pass? Does the project's own suite pass, before and after? (The `pylib` fixture has one failing test before any work.)

The project copies of full runs stay in the work folder (`HOME-work/runs`, or `--work`). `--keep-runs` also keeps the copies of decision runs.

**The result line** has: `time`, `mode`, `measure` (`practical-v2`; `practical-v1` for the first baseline), `model`, `client`, `client_version`, `thinking`, `thinking_level`, `variant`, `category`, `prompt`, `run`, `expected`, `coder` and `coder_state` (the HOME's coder at the run, from its marker: the `--coder` choice and what the setup did; empty for a HOME of an older harness, which had the coder on), and:

- the practical decision: `decision`, `correct`, `decision_tool` and `decision_args` (the decisive tool call), `looks`, `decision_seconds` (from the first model step), `undecided_reason`;
- the strict decision: `decision_strict`, `correct_strict`, `tool` and `args` (the first tool call), `step_tools` (all tools of the first tool step), `tool_seconds` (from the first model step to the first tool call), `first_event` (the event of the first tool call, cut);
- `seconds` (from the client start to the stop), `startup_seconds`, `thinking_tokens` (until the first tool call) and `thinking_tokens_decision` (until the stop) when the client reports them, `thinking_chars`, `error`, and `full` (full runs).

Values in `args` and `decision_args` are cut to 400 characters (as before, for the older reports). The briefs are kept whole:

- `briefs`: one item for each coder task of the run, in order (decision and full runs; OpenCode: each `task` call with `subagent_type` coder or carl-coder; Pi: each `subagent` call with `agent` coder, and each coder item of its `tasks` and `chain` lists):
  - `text`: the task text, full (OpenCode `prompt`, Pi `task`);
  - `format`: `toml` or `json` (a brief that CARL's reader finds), `kv` (two or more `Key:` lines: 1.12.1's form), or `other`;
  - `valid`: a TOML or JSON brief that reads and passes CARL's check; `problems`: the check's sentences, or the reader's error with its line. The check is CARL's own: `agentbench/brief_check.mjs` runs `client/shared/carl-brief-check.mjs --json` (which uses `carl-brief.js`) with node, found on PATH, else in `~/.local/bin` of the user or of the harness HOME (without node: format `unknown`, valid `null`);
  - `refused`: the call's result starts with CARL's refusal mark `[CARL] Brief refused` (after OpenCode's `Error: `), so the coder did not start;
  - `tool`, `item` (`prompt`, `task`, `tasks[N]`, `chain[N]`), and `continues: true` for an OpenCode task with `task_id` (CARL does not check it).
- `brief_tokens`: one count for each brief, from the model server's `POST /tokenize` (the server of the batch, with the harness's API key). `null` when no server is known (`--no-server`): `bench.py tokens` fills them in afterwards.

**A decision run after a refusal.** The decision is the first call to the coder, as before. When CARL's brief check refused that brief, the run goes on (the main agent sends the brief again) until a brief is taken, the turn ends, an error, 15 more tool calls or 300 s from the first model step (`--max-tools`, `--max-seconds`). Pi: the run waits up to 30 s for the call's result (a refusal comes at once). So the result has every brief until the coder started; only `seconds` and `thinking_tokens_decision` grow for such a run.

**Tokens afterwards.** With a server of the same model on a port (it can be the harness's own: `bench.py` does not start one for this):

```bash
python3 tools/agent-bench/bench.py tokens results.jsonl --url http://127.0.0.1:8097 \
    --key-file ~/carl-phase2343/bench-home-work/server/api-key --model gemma-4-e4b
```

It counts the briefs whose `brief_tokens` is `null`, only in the lines of `--model`, and writes the file again in place.

**Resume and the measure.** A cell is done when the file has a line with the same model, client, thinking, variant, prompt, run, mode **and measure**. The lines of the older format have no `measure` field (the report shows them as `first-tool`, strict only); they never count as done for the practical measure.

**A new measure, and the keep shares.** Only the large and stuck requests were run again at the new limits (the user's choice, 2026-10-06). A `practical-v2` group with no small requests or questions takes its keep shares from the same model, client, thinking and variant in `practical-v1`, and the table marks them `[practical-v1]`.

**The report** has, for each model, client, thinking, variant and measure: the share of large and stuck requests that went to the coder (strict and practical), the share of small requests and questions that the main agent kept (strict and practical), the number of undecided runs and of errors, the median number of looks before a decisive action, the median times to the decision and to the first tool call, the median thinking tokens, and the pass mark for each measure (at least 90% and 90%). Then the practical share for each category, the wrong practical decisions (prompt, expected, got, first tool), the errors, and the full runs. `--md` gives Markdown.

**The coder briefs** (the lines that have `briefs`), for each model, client, variant and mode (decision or full): the runs with a brief, the briefs sent and their formats, the share of runs whose first brief was valid (TOML and JSON only: a kv brief has no check), the share whose last brief was taken (the coder started), the refusals per run (mean and most), the median tokens of the first brief and of all briefs, and for full runs the hidden tests passed and the median minutes.

## 7. Variants

**From 1.8.0 (Phase 23's result), CARL's own setup has the reminder (V2) and the coder's two modes (V7)**, in the
`carl-delegation` plugin and extension and in `client/agents/`. So `baseline` on a HOME set up by 1.8.0 or later is
what the variants `c2_v2_v7` measured on the older setup. The hook variants (`v2_turn_reminder`, `v4_read_nudge`,
`v5_new_file_gate`, `c1`-`c3`) were made for the older setup: on a 1.8.0 HOME they add a second reminder. Use them
only to repeat the Phase 23 runs on a HOME of the older setup, or turn CARL's reminder off first (`NO_REMINDER=1`).

A variant changes how the clients are set up before a batch. Each variant is one file in `variants/`. The file name is the variant's name.

```python
"""v2_turn_hook: one reminder line in the newest user message of each turn."""
from typing import List

DESCRIPTION = "A per-turn reminder of the delegation rule"


def apply(home: str) -> List[str]:
    """Change the configs or plugin files in the harness HOME. Return the changed files."""
    ...
    return [".config/opencode/plugins/carl-cache/index.js"]
```

- `run` calls `apply(home)` after each config refresh, so each variant starts from the configs of CARL's setup. With `--no-server` there is no refresh, so `apply()` must give the same result when it runs again on its own changes.
- The variant's name is part of each result line, and of the key that makes the runs resumable.
- Before a variant's own `apply()`, the switches that some variants change are set back to CARL's defaults: carl-cache's move on, the brief check and the chain on, TOML named in a refusal and in Pi's tool guidelines. So with `--no-server` (no refresh) no variant runs with the switches of the one before.
- `bench.py variants` lists the variants: `baseline`, the Phase 23 variants `v1`-`v7`, the combinations `c1`-`c3`, `project_in_system` (Phase 23.1), and `brief_kv` and `brief_json` (Phase 23.4.3, below).

**The brief's format (Phase 23.4.3).** Three formats of the coder's task, each with its own texts:

| Variant | The texts | CARL's brief check, gates, chain |
|---|---|---|
| `baseline` | CARL's: the TOML brief and the TOML report (`client/agents/`; revision 4 of the brief since 2026-10-09, `docs/phase-plans/phase23.4.3/brief-v4-draft.md`) | on |
| `brief_kv` | 1.12.1's `Mode: / Goal: / Files: ...` task: `variants/texts/brief_kv_delegation.md` and `brief_kv_coder.md` are `git show v1.12.1:client/agents/...` (the `carl:` markers cut as for CARL's rule); 1.12.1's coder lines in Pi's subagent tool guidelines | off: OpenCode `carl-delegation` options `brief: false`, `chain: false`; Pi `carl.json` `delegation.brief` and `delegation.chain` false |
| `brief_json` | **Obsolete** (the user dropped JSON on 2026-10-09; kept as a record): its texts have revision 1's keys, which CARL's check now refuses with the names of revision 4's keys. `variants/texts/brief_json_*.md`: CARL's rule and coder with the brief and the report as JSON with the same keys and structure (only the format parts converted; the examples read back to the same brief and report); Pi's guidelines name a JSON brief | on: `carl-brief.js` reads JSON too (a ```json fence, or a JSON object with prose around it), and its sentences name the keys the JSON way; the chain writes the sessions' briefs as JSON; a refusal of a task with no brief names JSON (`carl-delegation` option `briefFormat: "json"`, Pi `delegation.brief_format`) |

`brief_kv` and `brief_json` need CARL's coder and `carl-delegation` in both clients (`./setup --coder on`); they stop with an error otherwise.

## 8. Tests

```bash
python3 -m unittest discover -s tests/agent_bench -t tests/agent_bench
cd tools/agent-bench && uvx mypy --strict bench.py fakeserver.py agentbench variants/*.py
```

The tests need no model and no server:

- `test_events.py`: the real event streams of OpenCode 1.18.34 and Pi 1.0.2 (in `tests/agent_bench/events/`) for each kind of decision (also: looks then the coder, looks then a write, a shell write, undecided), both measures, the limits, and the shell write rules with their negatives.
- `test_library.py`: fetch and drop in temporary folders, with a fake catalogue and library.
- `test_runner.py`: the prompts, the matrix and its resume, the report, the fixtures, the client commands, the variants, the fake server.
- `test_briefs.py`: the coder briefs: the calls that give the coder a task, CARL's check through node, the refusals, a decision run that goes on after a refusal, the tokens (the fake server's `/tokenize`, `bench.py tokens`) and the report's table.
- `test_variants.py`: each variant on a harness HOME made by `_support.make_variant_home` (CARL's rule, coder, `carl-delegation` entry, Pi's `carl.json` and subagent extension), again on its own output, and the baseline after it.
- `test_server_flow.py`: the server start and stop with a fake launcher; the real `./carl.sh package` and `./setup --no-install` against the fake server.
- `test_integration.py`: the real `opencode` and `pi` against the fake server: each kind of decision (both measures), the thinking switch, a full run for each client, and `bench.py run` and `report`. It needs a client HOME with both clients (`AGENT_BENCH_CLIENT_HOME`); without one, it is skipped. It copies that HOME first and changes only the copy.

`tests/agent_bench/capture_events.py` captures the event streams again (for a new client version).

## 9. The event streams

**OpenCode** (`opencode run --format json`) writes one JSON object for each line:

- `step_start`, `reasoning` (only with `--thinking`), `text`, `tool_use`, `step_finish` (with `tokens.reasoning`), `error`.
- A `tool_use` event comes when the tool has run: `part.tool` is the tool, `part.state.input` its arguments.
- A call to the coder: `"tool": "task"` with `"input": {"subagent_type": "coder", "background": true, …}`. CARL's `carl-background` plugin starts every coder task in the background, so the task tool returns at once.
- The log (`--print-logs`) has a line when a tool starts: `message=evaluated permission=task pattern=coder`. When the `tool_use` event does not come in 10 s, the harness uses this line.
- An API error: `{"type":"error","error":{"name":"APIError" or "ContextOverflowError","data":{"message":…}}}`.
- `opencode run` stops when the main turn ends, also when a background task still runs. Thus full runs use `opencode serve`. A background result comes back as a user message that starts with `<task id="…" state="completed">`.
- A stopped `opencode run` leaves a lock in `~/.local/state/opencode/locks`, and the next start waits about 60 s for it. Before each OpenCode start, the harness removes the locks of processes that do not run (in the harness HOME only).

**Pi** (`pi -p --mode json`, and the same events in `--mode rpc`):

- `message_start`, `message_update`, `message_end` (with `message.role` system, user, assistant, toolResult or custom), `tool_execution_start`, `tool_execution_end`, `turn_end`, `agent_end`, `agent_settled`.
- The assistant's `message_end` has the content: `thinking`, `text` and `toolCall` items, `usage.reasoning`, `stopReason` and `thinkingLevel`.
- A call to the coder: `{"type":"toolCall","name":"subagent","arguments":{"agent":"coder","task":…,"background":true}}`. Its tool result says `Started in the background: bg-1 (coder)`.
- A background result comes back as a `message_end` with `role: custom` and the text `<subagent id="bg-1" agent="coder" state="done" …>`.
- An API error: an assistant `message_end` with `stopReason: error` and `errorMessage`. Pi tries again (`auto_retry_start`, `auto_retry_end`); the harness counts the result after the tries.

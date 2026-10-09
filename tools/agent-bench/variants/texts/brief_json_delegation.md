## Delegating to the coder subagent

You can hand work to a specialist subagent named `coder` (OpenCode: the task tool with subagent_type "coder"; Pi: the subagent tool with agent "coder"). If you ARE the coder subagent, ignore this section and do the task yourself.

**First step for every coding request:** check whether it is large (below). If it is large:
1. Do not write or change any file yourself.
2. Read only what the brief needs: the files to change, the test command, and the project's rules (for example AGENTS.md).
3. Then delegate it to `coder`.

Delegate to `coder` instead of doing the work yourself when either is true:

1. **Stuck.** A fix for the same piece of code has already failed twice: two of your own attempts in this conversation, or the user tells you earlier attempts failed. Do not make a third attempt yourself; delegate.
2. **Large.** The request needs 3 or more files, or roughly 150+ lines of new or changed code. Clear signs: it asks for a new module, package, tool or CLI; it lists several files or components to create; it asks for an implementation plus tests; or it is a multi-step feature or refactor. Decide this **before you write anything**: if the request is large, read only what the brief needs, then delegate it to `coder`. Do not start writing it yourself.

**The coder's two modes.** The brief's `mode` says what the coder may change, so you do not have to split the work yourself:
- `"mode": "code"`: it writes and changes the program code and runs the tests; it never edits tests. Use it for new code, for a fix (give it the error) and for code to existing tests.
- `"mode": "test"`: it writes tests from your requirements and never touches the program code; failing tests come back as findings. Use it when you want only tests.
- Code plus new tests: one brief with `"mode": "code"` and `"tests": "new"`. CARL then runs the coder twice, in separate sessions: first in mode test (it writes the tests), then in mode code. You get one result for both: the test session's report, the code session's report, and "Tests unchanged" or the test files that changed. A line that starts with `[CARL] Warning` tells you that no new test failed before the code, or that a test changed: read those tests before you trust a pass.

Writing or fixing code goes to `coder`, never to a general-purpose or explore subagent: those don't have its working method and standards.

Everything else you do yourself: questions, explanations, reading or searching code, and small or single-file edits.

<!-- carl:background opencode -->
**Run the coder in the background.** Start every `coder` task with the task tool's `background: true`. Then tell the user in one sentence what the coder does, and end your turn or go on with other work that does not overlap. Its result comes back to you as a message when it ends: do not wait, poll or check on it.
<!-- carl:background pi -->
**Run the coder in the background.** Start every `coder` task with the subagent tool's `background: true`. Then tell the user in one sentence what the coder does, and end your turn or go on with other work that does not overlap. Its result comes back to you as a message when it ends: do not wait, poll or check on it.
<!-- carl:background end -->

<!-- carl:browser opencode -->
**Browser checks after the coder.** The coder has no browser. When its report lists something under "Needs a browser check", do these steps:
1. Start the app yourself with bash, in the background, as the report says (for example `python3 -m http.server 8765 >/dev/null 2>&1 &`). Check that it answers (`curl -s -o /dev/null -w "%{http_code}" URL`). The browser subagent cannot start it.
2. Send the browser subagent (the task tool with subagent_type "browser") the URL and the list of checks.
3. Stop the app when the browser subagent reports.
4. Tell the user what the coder did and what the browser saw.
<!-- carl:browser pi -->
**Browser checks after the coder.** The coder has no browser. When its report lists something under "Needs a browser check", do these steps:
1. Start the app with bash, in the background, as the report says. Check that it answers.
2. Load the browser tools (`tool_search`), open the URL and do each check on the live page.
3. Stop the app.
4. Tell the user what the coder did and what the browser showed.
<!-- carl:nobrowser any -->
**Browser checks after the coder.** The coder has no browser, and neither do you here. When its report lists something under "Needs a browser check", pass that list on to the user to check by hand.
<!-- carl:browser end -->

When you delegate, the task text (OpenCode: `prompt`; Pi: `task`) is a brief in JSON, in this schema. The coder sees nothing else. This example is made up; take each value from the user's request and the project's own files:

```json
{
  "mode": "code",
  "tests": "new",
  "goal": "Add a --csv option to the report command. It writes the monthly report as CSV instead of the text table.",
  "scope": {
    "in": [
      { "text": "the report command's new --csv option and the CSV writer" }
    ],
    "out": [
      { "text": "the text table output", "why": "the user wants it as it is" },
      { "text": "data/orders.csv", "why": "real input: read it, never change it" }
    ]
  },
  "file": [
    { "path": "report/csv_export.py", "action": "create" },
    { "path": "report/cli.py", "action": "change" },
    { "path": "tests/test_csv_export.py", "action": "create" },
    { "path": "report/model.py", "action": "read" }
  ],
  "requirement": [
    { "id": "R1", "text": "report --csv FILE writes the report to FILE as CSV, one row for each month" },
    { "id": "R2", "text": "the first row is the header month,orders,total" },
    { "id": "R3", "text": "a total has two decimals and a dot, for example 1234.50" }
  ],
  "check": [
    { "id": "A1", "covers": ["R1", "R2", "R3"], "run": "python -m pytest tests/test_csv_export.py -q", "expect": "all tests pass" },
    { "id": "A2", "covers": ["R1"], "run": "python -m report --csv /tmp/report.csv data/orders.csv && head -3 /tmp/report.csv", "expect": "the header and the first two months" }
  ],
  "constraint": [
    { "text": "Standard library only: use the csv module, not pandas.", "source": "AGENTS.md" }
  ],
  "example": [
    { "source": "data/orders.csv", "text": "order_id,date,amount\n1001,2026-01-04,19.90\n1002,2026-01-17,42.00\n" }
  ]
}
```

The fields:
- `mode`: "code" or "test" (above).
- `tests`: "new", "existing" or "none" (the rule below). Mode code only.
- `goal`: one or two sentences.
- `scope.in`: what the task is about, in the user's words. `scope.out`: what the coder must leave alone, with `why` when there is a reason. Give at least one `scope.out` entry.
- `file`: one item for each file, with its `action`: "create" (a new file), "change" (a file that exists) or "read" (context only, never written). The coder writes only the "create" and "change" files.
- `requirement`: one item for each point, with its own `id` (R1, R2, ...).
- `check`: its items say how to check it is done: `covers` (the requirement ids), `run` (the command), `expect` (the result). Put every requirement in the `covers` of a check.
- `constraint`: one item for each project rule that applies, copied, with `source` (the file it is from).
- `example`: one item for each real input, copied from a file (`source` = its path) or from the user's message (`"source": "user"`).
- `error` and `tried`: only for a fix that failed. `error` (an object): `run` (the command that fails) and `output` (its exact output). `tried` (a list): `change` (what was changed) and `result` (what happened).

**The rule for `tests`.** Ask these questions in this order:
1. Is it a follow-up to the coder's earlier work, a fix, or a stuck task? Then `"tests": "existing"` when tests cover it (name them in a check's `run`), else `"tests": "none"`.
2. Is it new behaviour with no tests yet (a new module, CLI or feature)? Then `"tests": "new"`.
3. Else: `"tests": "existing"` when the project's tests cover the change, else `"tests": "none"`.

Invent nothing: take every path, command and rule from the user's request and the project's own files. Examples are copied, never invented: copy lines from an actual file or the user's message. CARL checks the brief before the coder starts. A brief with a gap comes back to you with `[CARL] Brief refused` and the points to fix: fix them and send the whole brief again. When the coder's result comes back (one result, also when CARL ran two sessions), run its checks yourself (the tests or the build it names) before you answer the user.

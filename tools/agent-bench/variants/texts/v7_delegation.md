## Delegating to the coder subagent

You can hand work to a specialist subagent named `coder` (OpenCode: the task tool with subagent_type "coder"; Pi: the subagent tool with agent "coder"). If you ARE the coder subagent, ignore this section and do the task yourself.

**First step for every coding request, before any tool call:** check whether it is large (below). If it is, your first and only action is to delegate it to `coder`; do not explore, plan or write files yourself first.

Delegate to `coder` instead of doing the work yourself when either is true:

1. **Stuck.** A fix for the same piece of code has already failed twice: two of your own attempts in this conversation, or the user tells you earlier attempts failed. Do not make a third attempt yourself; delegate.
2. **Large.** The request needs 3 or more files, or roughly 150+ lines of new or changed code. Clear signs: it asks for a new module, package, tool or CLI; it lists several files or components to create; it asks for an implementation plus tests; or it is a multi-step feature or refactor. Decide this **before your first tool call**: if the request is large, your first action is to delegate it to `coder`, not to start writing it yourself.

**The coder's two modes.** Start every `coder` task with the line `Mode: code` or `Mode: test`; the mode says what the coder may change, so you do not have to split the work yourself:
- `Mode: code`: it writes and changes the program code and runs the tests; it never edits tests. Use it for a fix (give it the failing test or error) and for code to existing tests.
- `Mode: test`: it writes the tests from your requirements and never touches the program code; failing tests come back as findings.
- A request for code plus tests: first a `Mode: test` task, then, when it is back, a `Mode: code` task that makes those tests pass.

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

When you delegate, write the task so it stands alone (the coder sees nothing else): the goal, the file paths, the requirements and how to check them, and for stuck tasks the exact error or failing test output and what was already tried. Give it real examples of any input format (copy lines from an actual file or the user's message, or name the file to read); never invent sample data. When it reports back, check its result (run the tests or the build it names) before you answer the user.

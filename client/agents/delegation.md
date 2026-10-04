## Delegating to the coder subagent

You can hand work to a specialist subagent named `coder` (OpenCode: the task tool with subagent_type "coder"; Pi: the subagent tool with agent "coder"). If you ARE the coder subagent, ignore this section and do the task yourself.

**First step for every coding request, before any tool call:** check whether it is large (below). If it is, your first and only action is to delegate it to `coder`; do not explore, plan or write files yourself first.

Delegate to `coder` instead of doing the work yourself when either is true:

1. **Stuck.** A fix for the same piece of code has already failed twice: two of your own attempts in this conversation, or the user tells you earlier attempts failed. Do not make a third attempt yourself; delegate.
2. **Large.** The request needs 3 or more files, or roughly 150+ lines of new or changed code. Clear signs: it asks for a new module, package, tool or CLI; it lists several files or components to create; it asks for an implementation plus tests; or it is a multi-step feature or refactor. Decide this **before your first tool call**: if the request is large, your first action is to delegate it to `coder`, not to start writing it yourself.

Writing or fixing code goes to `coder`, never to a general-purpose or explore subagent: those don't have its working method and standards.

Everything else you do yourself: questions, explanations, reading or searching code, and small or single-file edits.

<!-- carl:background opencode -->
**Run the coder in the background.** Start every `coder` task with the task tool's `background: true`. Then tell the user in one sentence what the coder does, and end your turn or go on with other work that does not overlap. Its result comes back to you as a message when it ends: do not wait, poll or check on it.
<!-- carl:background pi -->
**Run the coder in the background.** Start every `coder` task with the subagent tool's `background: true`. Then tell the user in one sentence what the coder does, and end your turn or go on with other work that does not overlap. Its result comes back to you as a message when it ends: do not wait, poll or check on it.
<!-- carl:background end -->

When you delegate, write the task so it stands alone (the coder sees nothing else): the goal, the file paths, the requirements and how to check them, and for stuck tasks the exact error or failing test output and what was already tried. Give it real examples of any input format (copy lines from an actual file or the user's message, or name the file to read); never invent sample data. When it reports back, check its result (run the tests or the build it names) before you answer the user.

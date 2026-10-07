## Coding work goes to `coder`

Before your first tool call, sort the request. (If you ARE the coder subagent, ignore this section.)

- **Hand it to `coder` at once** (OpenCode: the task tool with subagent_type "coder"; Pi: the subagent tool with agent "coder") when it asks for code in several files, a new module, package, tool or CLI, code plus tests, a feature or a refactor, OR a fix that already failed (the user says they or you tried before). Do not read or edit the files yourself first: the coder reads them.
- **Do it yourself** only for questions, explanations, finding things in the code, and one small edit (a rename, a flag, a typo, one test).

The coder sees nothing but your task text: give it the goal, the file paths, the requirements and how to check them, and for a failed fix the exact error and what was tried. Copy real input examples; never invent them. Check its result before you answer.

<!-- carl:background opencode -->
Start the coder with `background: true`, tell the user in one sentence what it does, and end your turn; its result comes back as a message.
<!-- carl:background pi -->
Start the coder with `background: true`, tell the user in one sentence what it does, and end your turn; its result comes back as a message.
<!-- carl:background end -->

<!-- carl:browser opencode -->
When the coder's report lists "Needs a browser check": start the app with bash in the background, send the browser subagent (subagent_type "browser") the URL and the checks, stop the app after, and tell the user what you saw.
<!-- carl:browser pi -->
When the coder's report lists "Needs a browser check": start the app with bash in the background, load the browser tools (`tool_search`), do each check, stop the app, and tell the user what you saw.
<!-- carl:nobrowser any -->
When the coder's report lists "Needs a browser check", pass that list to the user to check by hand.
<!-- carl:browser end -->

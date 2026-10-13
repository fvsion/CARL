# The compaction bench

Phase 23.5.2.1. How well a compaction keeps what a coding session needs: long sessions of the real OpenCode and Pi on
CARL's server are recorded as the model saw them; then, at points where the context is 40-70% full, each compaction
method makes the request shorter, and the same model is scored against what it did with the whole request.

```bash
B="python3 tools/compaction-bench/compbench.py"
$B prepare --home ~/bench-compact --models-dir ~/models/gguf             # OpenCode and Pi, the coder off
$B collect --home ~/bench-compact --out ~/compact-runs --models-dir ~/models/gguf
$B checkpoints ~/compact-runs --range 0.35:0.75             # every request at 35-75% of the window
$B score ~/compact-runs --home ~/bench-compact --methods none,mask:4:0.3,mask:8:0.3 --models-dir ~/models/gguf
$B report ~/compact-runs --md
```

- **prepare** makes a harness HOME (never yours; agent-bench's rules) with the tested OpenCode and Pi and the coder
  off: one main session does the work, so its context grows.
- **collect** runs one session per client and script, with thinking as CARL sets it up (`--thinking off`: off; Pi at
  CARL's level, `low`). A script is a series of the agent bench's tasks on one fixture project (`pylib`, `pycli`,
  `webapp`, `pyrules`), each run to its end in the same session, then follow-ups that read and run much (the whole
  suite, every changed file again, missing tests, a change list, every module once more). `coder-pylib` and
  `coder-pycli` are coder-style: a few tasks in one brief, worked through in one long tool loop; a follow-up brief only
  under 60% of the window (CARL's rule for sending the coder back to its session). A session goes on with follow-ups
  until its context reaches `--target` (74K: the 96K window's 35-75% band) and stops there. The clients' own
  compaction and OpenCode's prune are turned off in the harness HOME, so a session grows as it is. The clients talk to a capture proxy
  (`tools/req-capture-proxy.py`) on the configured port; CARL's server runs 10 ports above it. Each chat request's
  body is saved (`chat-NNN.json`), so a session's requests are its transcript as the model got it, CARL's own
  rewrites included. A finished session has `done.json`; a second run skips it.
- **checkpoints** picks the points: with `--range LO:HI` every request whose context fills that share of the window
  (`--every N`: every Nth), else the requests nearest to 40%, 55% and 70% (`--at`); each with what the model wrote
  next (the next request holds it). Eleven points tell only ~25-point differences apart; ~150-250 tell 10 points.
- **score** sends, for each checkpoint and method, two requests to the same model (temperature 0, the session's tools
  kept): the next step, compared with the real one (the same tool; the same file, command or pattern), and a probe
  that asks for a JSON answer (the files changed, every error seen with its fix, most recent first, the next step),
  compared with what code reads from the whole transcript (the write and edit calls; the last error: a failed command,
  or an edit's diagnostic, named by its test, file:line or the start of its message, in any form). Parallel calls count as a set. Each method is also
  compared with the reply of `none` (the same model with the whole request, temperature 0). Paths in the answer that
  appear nowhere in the transcript count as invented. `--answer-tokens` (4,096) leaves room for thinking. The prompt tokens and the read time are kept. Results: `scores.jsonl`
  (a second run continues).
- **report** gives one row per method: against `none` on the same points, the difference in the next-target rate with
  a 95% interval from resampling whole sessions, and the exact McNemar p; the prompt tokens, and the event's re-read:
  a compaction is read again once, whole, at idle (not per turn), at the server's measured read speed.

Methods: `none`; `mask:K:S` clears each tool result to one line ("[cleared: the output of read notes/store.py; call
it again to see it]") except the newest K results, those in the last S share of the context, and the latest error.
`mask:K:S+turn` is the compaction event's prune as agreed (2026-10-13): nothing under the drain line (40% of the
window); above it, the old part's results first, then the current turn's middle, then the files the turn changes
beyond their budget (10% of the window), until the context is under the line; the current turn's user message and a
raw working tail (S of the window, at least the newest K results, never a call without its result) stay. `+ph=facts`
gives the pruned results placeholders in STE100, built by code by kind (a read: its lines, and whether the file
changed after; a test: the failed tests and the counts; a command with side effects: "Do not do this command
again."; an edit: done or failed; a listing: the count and the first 10), and keeps results under 80 tokens and
listings under 300. `+args` prunes old edit and write arguments (an edit's old and new
text, a write's content, as "[pruned: N lines]"; the call, its path and the argument names stay), with the results of
their place in the order; never in the tail, never the edits after a kept view of a file the turn changes.
`+think=empty` or `+think=last` prunes earlier thinking: the old part's first (Qwen's template drops it by default; CARL
keeps it for the cache), the current turn's middle only with `+thinkmid`, never the tail; `last` keeps
"[pruned: N tokens]" and the block's last whole sentences up to ~50 tokens, else (a list) its last ~100 tokens.
`+notes`, `+files` and `+map` add the CARL note
before the current turn's user message: the session's files, the errors (open or fixed), the last test run, the todo
list, a map of the code, and the current contents of up to 5 files in work (rebuilt from the fixture and the session's
reads, writes and edits; a file whose current view is in the working tail is not copied); the reads it replaces go
first. When the event still cannot reach the line and the excess is in the current turn, the working set yields, then
the map. The score's `--window` sets the window. Each row also says whether the next step calls again what was pruned
(repeats) and whether a call copies a stub (imitated). Two methods that make the same request at a point are asked
once. **sample** writes the points where two methods chose differently side by side (the real next step, each
method's, the thinking the second one changed), for a person to read. Sizes: a message's text, thinking and calls at
3.35 characters a token, the tools at 4.0 (measured against the A3B's tokenizer). CARL's own summary is added as a
method when it is built.

Tests: `python3 -m unittest discover -s tests/compaction_bench -t tests/compaction_bench`.

## The continuation checks (`continuation/`)

Can llama.cpp continue a reply that was cut where carl-compaction would cut it (a reply that runs into the window:
CARL caps the output, compacts, and sends the partial reply back as the final assistant message)? Each script talks to
a llama-server started as CARL starts it (`--jinja --reasoning-format deepseek`, CARL's chat template,
`preserve_thinking`):

```bash
python3 tools/compaction-bench/continuation/continue_test.py  http://127.0.0.1:8199   # text, thinking, a tool call
python3 tools/compaction-bench/continuation/continue_test2.py http://127.0.0.1:8199   # exact, raw tool calls, a long write
python3 tools/compaction-bench/continuation/continue_test3.py http://127.0.0.1:8199 REQUEST.json ...   # streamed; real requests
```

`continue_test3.py` takes saved chat requests (a bench session's `chat-NNN.json`, with `_truth` optional), cuts each
half-way through its next tool call while streaming, rebuilds the cut call in the template's own text from the
streamed partial arguments, and checks that the same call comes back. Measured on llama.cpp 0.6.0 with the
Qwen3.6-35B-A3B IQ3: the text continues exactly; a cut tool call (also a long write, and real 43-61K agent requests)
comes back as the same call; a streamed continuation sends the part it was given again first.

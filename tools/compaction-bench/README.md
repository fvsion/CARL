# The compaction bench

Phase 23.5.2.1. How well a compaction keeps what a coding session needs: long sessions of the real OpenCode and Pi on
CARL's server are recorded as the model saw them; then, at points where the context is 40-70% full, each compaction
method makes the request shorter, and the same model is scored against what it did with the whole request.

```bash
B="python3 tools/compaction-bench/compbench.py"
$B prepare --home ~/bench-compact --models-dir ~/models/gguf             # OpenCode and Pi, the coder off
$B collect --home ~/bench-compact --out ~/compact-runs --models-dir ~/models/gguf
$B checkpoints ~/compact-runs
$B score ~/compact-runs --home ~/bench-compact --methods none,mask:4:0.3,mask:8:0.3 --models-dir ~/models/gguf
$B report ~/compact-runs --md
```

- **prepare** makes a harness HOME (never yours; agent-bench's rules) with the tested OpenCode and Pi and the coder
  off: one main session does the work, so its context grows.
- **collect** runs one session per client and script. A script is a series of the agent bench's tasks on one fixture
  project (`pylib`, `pycli`), each run to its end in the same session, then five follow-ups that read and run much (the whole suite, every changed file again, missing tests, a change list, every module once more): the tasks alone ended at 25-45K tokens. The clients talk to a capture proxy
  (`tools/req-capture-proxy.py`) on the configured port; CARL's server runs 10 ports above it. Each chat request's
  body is saved (`chat-NNN.json`), so a session's requests are its transcript as the model got it, CARL's own
  rewrites included. A finished session has `done.json`; a second run skips it.
- **checkpoints** picks, in each session, the requests nearest to 40%, 55% and 70% of the window (`--window`, `--at`),
  each with what the model wrote next (the next request holds it).
- **score** sends, for each checkpoint and method, two requests to the same model (temperature 0, the session's tools
  kept): the next step, compared with the real one (the same tool; the same file, command or pattern), and a probe
  that asks for a JSON answer (the files changed, the last error, the next step), compared with what code reads from
  the whole transcript (the write and edit calls; the last failed tool result). Paths in the answer that appear nowhere
  in the transcript count as invented. The prompt tokens and the read time are kept. Results: `scores.jsonl`
  (a second run continues).
- **report** gives one row per method.

Methods: `none`; `mask:K:S` clears each tool result to one line ("[cleared: the output of read notes/store.py; call
it again to see it]") except the newest K results, those in the last S share of the context, and the latest error.
CARL's own prune and summary are added as methods when they are built (23.5.2.2-.3).

Tests: `python3 -m unittest discover -s tests/compaction_bench -t tests/compaction_bench`.

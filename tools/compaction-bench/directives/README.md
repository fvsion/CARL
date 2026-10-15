# The directive test

Phase 23.5.2.2. Does a client's own compaction keep the user's directives, and does CARL's rules list help? The real
OpenCode runs a whole coding session against CARL's server, the user's rules are said once in the first message, and
each turn's own edits are graded against the rules, before and after the client's compactions.

```bash
D=tools/compaction-bench/directives
$D/run_oc.sh ~/carl-phase23/ocdir-home OUT          # compact48 and control96, two runs each
$D/run_hook.sh ~/carl-phase23/ocdir-home OUT        # rules48, two runs
```

- **The session.** The real OpenCode 1.19.0 with CARL's plugins, in a harness HOME made by `compbench.py prepare`
  (never yours), with the A3B IQ3 by CARL's server. The project is agent-bench's `pyrules` fixture without its
  `AGENTS.md`; its three rules are said by the user in the first message, before the first task:
  1. every function added or changed gets a docstring whose first line starts with `Does:`;
  2. every new test method is named `test_rule_...`;
  3. after every code change, the tests run (`python3 -m unittest discover -s tests`).

  Then the compaction bench's 25 user turns of the `pyrules` script (the four `rules-*` tasks, then the follow-ups).
- **The arms** (`oc_directives.py --arm`). OpenCode's `opencode.json` as CARL writes it for the window (output =
  min(32000, context / 2)):
  - `compact48`: a 48K window, OpenCode's own compaction on with its defaults (it compacts at about 24.5K);
  - `control96`: a 96K window, compaction off (the session, about 76K, never compacts);
  - `rules48`: `compact48` plus `carl-rules-hook/`, a test plugin (not shipped). At each compaction it gives OpenCode's
    summary prompt the rules list (`tools/monitor/rules.py`) through `experimental.session.compacting`, asking that the
    rules be kept in the summary word for word.
- **The grading.** From OpenCode's own messages, each turn's edit and write calls: the functions in their new text
  (a `Does:` docstring?) and the new test methods (named `test_rule_`?); for each turn with a code change, did the
  tests run after its last change; where the compactions were. Also the final project against the fixture, and
  agent-bench's hidden rule tests. A stand-in user answers OpenCode's question tool with all its options.

## Results (2026-10-14 and 2026-10-15, an M2 Max)

"Early" is the turns before the compaction arms' first compaction (1-6 or 1-7); "late" is the rest. The control is
split at the same turns.

| Arm | Runs | Rule 2, `test_rule_`: early | late | Rule 1, `Does:`: early | late | Rule 3 |
|---|---|---|---|---|---|---|
| compact48 (OpenCode's compaction) | 2 | 14/24 (58%) | **4/107 (4%)** | 3/40 | 1/116 | 21/21 turns |
| control96 (no compaction) | 2 | 19/20 (95%) | **65/67 (97%)** | 4/30 | 2/69 | 19/19 |
| rules48, run 1 (list from the pattern list) | 1 | 0/14 | 0/62 | 3/21 | 7/73 | 12/12 |
| rules48, run 2 (list from setfit2) | 1 | 0/9 | **76/79 (96%)** | 6/20 | 4/83 | 12/12 |

- OpenCode's compaction loses a directive the model was following: rule 2 from 97% without compaction to 4% after it.
- With the hook, all 22 of OpenCode's summaries in the two runs held the three rules word for word: the rules list
  gets the user's rules into the compacted context. Whether the model then follows them is a separate question: run 2
  followed rule 2 in 96% of its new tests after compaction, run 1 did not, with the same rules in its summaries.
- Rule 1 was not followed in any arm: said only in chat, this model ignores it. Rule 3 held everywhere.
- In identical early turns, before any compaction, the model followed rule 2 anywhere from 0% to 100% of the time:
  one run per arm says little; read the arms together.

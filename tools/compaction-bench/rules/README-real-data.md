# Real-data training labels for the rules list

Three extra training sets for the user-rules sentence classifier, in the same format as
`labels-train*.jsonl` (one session per line, `turns` = list of user turns, each a list of
`[sentence, label]`, 1 = a rule). They are training data only; nothing here was taken from or
compared with the held-out sets.

| File | Source | Sessions | Sentences | Rules | Rule share |
|---|---|---|---|---|---|
| `labels-train-real-devgpt.jsonl` | DevGPT | 64 | 571 | 79 | 13.8% |
| `labels-train-real-wildchat.jsonl` | WildChat-1M | 52 | 427 | 59 | 13.8% |
| `labels-train-real-agentfiles.jsonl` | public AGENTS.md files | 24 | 212 | 171 | 80.7% |
| total | | 140 | 1210 | 309 | 25.5% |

Extra keys per line: `source` (data set and licence) and `origin` (where the session came from).
The training script does not read these files by default; add them through `CARL_RULES_TRAIN`.

## Sources and licences

- **DevGPT** (developers' ChatGPT conversations shared in GitHub issues, pull requests and
  discussions; MSR 2024 mining challenge). Licence: **CC BY 4.0**, as recorded on the Zenodo
  record <https://doi.org/10.5281/zenodo.10086809>. Data: <https://github.com/NAIST-SE/DevGPT>,
  snapshot `snapshot_20231012`, the three files `*_discussion_sharings.json`,
  `*_pr_sharings.json` and `*_issue_sharings.json` (113 MB together; the 680 MB Zenodo zip was
  not downloaded). `origin` gives the shared conversation id and the GitHub repository whose
  issue, PR or discussion linked it.
- **WildChat-1M** (allenai). Licence: **ODC-BY 1.0** (dataset card, licence section:
  <https://huggingface.co/datasets/allenai/WildChat-1M>; the card states the change from the
  earlier ImpACT licence applies retroactively). The dataset is not gated. Rows were read through
  the public datasets-server `rows` endpoint (random pages of 100 rows), keeping only English,
  non-toxic conversations whose user turns look like coding work. `origin` gives the train row
  index and `conversation_hash`.
- **Public AGENTS.md files** from GitHub repositories with permissive licences (licence per
  session in `source`, repository path in `origin`): MIT, Apache-2.0 or CC0-1.0, checked through
  the GitHub API's licence field. The awesome-cursorrules collection was not used (it is already
  in `labels-train-cursorrules.jsonl`).

## How sentences were picked

Parts 1 and 2 (DevGPT, WildChat) are real user text only; nothing was written or reworded.

1. User turns only, first 8 turns of a conversation, English only.
2. Each turn was split into sentences; a bullet or numbered list item is one entry. Pasted code,
   logs and stack traces were kept as one entry when short (up to 6 lines, label 0) and dropped
   when longer. Conversations with very long pasted documents were skipped.
3. Sessions were chosen by hand from keyword-flagged candidates (`don't`, `never`, `only`,
   `without`, `use ... instead`, ...) plus a share of pure-task sessions (questions, bug reports,
   detailed specs) for hard negatives. Turns past the useful part were cut and a few junk entries
   (attachment markers, quoted third-party mail, pasted file headers) were removed.
4. Privacy: URLs were replaced by `<URL>`, e-mail addresses by `<EMAIL>`, home-directory user
   names by `<user>`. Sessions with credentials, personal data, jailbreak prompts, sexual or
   abusive content, or non-coding topics (stories, essays, homework reports about people) were
   skipped. Sentences naming an AI assistant product were dropped.
5. Duplicates: a sentence already present in any `labels-train*.jsonl` file (case and spacing
   ignored) was dropped, so no sentence appears twice across the training files.

Part 3 (agentfiles) follows `labels-train-cursorrules.jsonl`: rule lines from each file reworded
into chat voice as positives, plus statements of fact from the same files (layout, what a
command does) and a few short task requests or questions as negatives.

## How they were labelled

By the rules-list definition. **1**: the sentence sets how the work is done from then on or
limits the task it is in: a must/never, a convention, a style or tool/library choice, a
compatibility limit (including a plain statement of the stack the code must target, such as
"I'm using typescript and es2020."), an output preference ("Show me code only"), a permission
or scope limit, a standing process, or a later sentence that changes an earlier rule. A limit
inside a request or a how-to question counts ("write without any class", "... without
converting them to string?"). **0**: the one-off task, its spec (what the new code does, its
inputs, outputs, names, even with must/never/always), questions, information and narration,
bug reports, pasted errors and code, thanks, follow-up chores.

Hard cases settled this way: a corrected requirement of the code being written stays 0
("you should always update currentTimestamp after adding an element..."); a design statement
for the feature itself stays 0 ("Only type_id for insertion of link should be required.");
"rewrite the program to use aws go sdk v2" is 1 (changes the earlier library choice); "Can you
replace the comments so I can copy paste directly." is 0 (a one-off request for this answer).

## Skipped and why

- Gated or sign-in datasets: none were used and no terms were accepted. The datasets-server
  search index for WildChat was still loading, so sampling used random row pages instead.
- LMSYS-Chat-1M and similar sets need a sign-in and accepted terms: not used.
- DevGPT: non-English conversations, conversations that only pasted papers or long documents,
  prompt-template collections (tutor bots, persona prompts), and conversations about AI
  assistant products themselves.
- WildChat: most sampled conversations were not coding or were jailbreak or role-play prompts;
  only 52 of 385 sampled coding-flagged conversations were kept.

## Relabelled after delivery (2026-10-15, the main session)
Questions are not rules by the definition (and CARL's rules list never quotes one, except a "Can you..." request): the
"?"-ending sentences labelled 1 that do not start with can/could/would/will you were set to 0 (7 sentences: devgpt: "Can we do this as a pure bash script?..."; wildchat: "so, out of inline-grid you can make kinda fracal of emoji fl..."; wildchat: "so, out of inline-grid you can make kinda fracal of emoji fl..."; wildchat: "Is there a way to include the CSS without requiring HTML5?..."; wildchat: "So yes, Before deciding which project ideas, I want you to f..."; wildchat: "And don’t forget to be harsh, Do not layout too command and ..."; wildchat: "I am currently making a c++ priority_queue of events in my S...").

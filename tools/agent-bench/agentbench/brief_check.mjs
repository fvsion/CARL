// agent-bench: the coder briefs of a run, checked with CARL's own checker (client/shared/carl-brief.js), so the
// harness and the clients have one checker. Used by agentbench/briefs.py:
//     node brief_check.mjs [PATH/carl-brief.js] < texts.json > checks.json
// texts.json: a JSON list of task texts. checks.json: one item for each: { format, valid, problems }.
//   format: "toml" or "json" (a brief that CARL's reader finds), "kv" (the 1.12.1 form: two or more lines that
//           start with "Key:"), or "other";
//   valid:  a TOML or JSON brief that reads and has no problem (checkBrief);
//   problems: checkBrief's sentences, or the reader's error (with its line); for kv and other one sentence.
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const lib = process.argv[2] || fileURLToPath(new URL("../../../client/shared/carl-brief.js", import.meta.url));
const B = await import(pathToFileURL(lib).href);
const KV = /^[ \t]*(?:[-*][ \t]+)?\**[A-Z][A-Za-z ]{1,30}\**[ \t]*:/gm;

const texts = JSON.parse(readFileSync(0, "utf8"));
const out = (Array.isArray(texts) ? texts : []).map((t) => {
  const text = String(t ?? "");
  const r = B.parseBrief(text);
  if (r.format) {
    const problems = r.brief ? B.checkBrief(r.brief, r.format) : [r.error];
    return { format: r.format, valid: Boolean(r.brief) && problems.length === 0, problems };
  }
  const kv = (text.match(KV) ?? []).length >= 2;
  return { format: kv ? "kv" : "other", valid: false, problems: ["The text has no TOML or JSON brief."] };
});
process.stdout.write(JSON.stringify(out));

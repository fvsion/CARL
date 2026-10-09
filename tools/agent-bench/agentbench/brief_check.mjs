// agent-bench: the coder briefs of a run, checked with CARL's own brief check (client/shared/carl-brief-check.mjs, the
// command that ships with CARL's client plugins), so the harness, the clients and a user have one checker. Used by
// agentbench/briefs.py:
//     node brief_check.mjs [PATH/carl-brief-check.mjs] < texts.json > checks.json
// It runs that command's --json mode: texts.json is a JSON list of task texts; checks.json one item for each:
// { format, valid, problems } (format: toml, json, kv or other; the meanings are in carl-brief-check.mjs).
import { fileURLToPath, pathToFileURL } from "node:url";

const cli = process.argv[2] || fileURLToPath(new URL("../../../client/shared/carl-brief-check.mjs", import.meta.url));
const { main } = await import(pathToFileURL(cli).href);
process.exitCode = main(["--json"]);

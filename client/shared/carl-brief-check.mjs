#!/usr/bin/env node
// @ts-check
// CARL: check a coder brief by hand, with the check that carl-delegation runs before it gives a task to the coder
// (carl-brief.js, next to this file). Installed by client/configure.py wherever carl-brief.js is (OpenCode's
// plugins/carl-delegation, Pi's extensions/carl-delegation and extensions/subagent); agent-bench checks the briefs
// of its runs with it too (one checker).
//
//     node carl-brief-check.mjs brief.toml         a brief in a file (TOML or JSON, or a task text with one in it)
//     node carl-brief-check.mjs < brief.toml       the same from the standard input (also: FILE "-")
//     node carl-brief-check.mjs --root DIR brief.toml
//                                                  also check that each existing_tests path is in the project DIR
//                                                  (as carl-delegation does in the project folder); without
//                                                  --root that rule is not checked
//     node carl-brief-check.mjs --json < texts.json
//                                                  a JSON list of task texts; prints a JSON list of
//                                                  { format, valid, problems }, one for each
//
// The brief is revision 4's (docs/phase-plans/phase23.4.3/brief-v4-draft.md); a key of revision 1 is a problem that
// names the new key. It prints the format, then "The brief is valid." or each problem as a sentence. Exit code: 0 the
// brief is valid; 1 it has problems, or the text has no brief; 2 the command is wrong or the input cannot be read.
//   format: "toml" or "json" (a brief that CARL's reader finds), "kv" (two or more lines that start with "Key:", the
//           form before CARL 1.13), or "other";
//   valid:  a TOML or JSON brief that reads and has no problem (checkBrief);
//   problems: checkBrief's sentences, or the reader's error (with its line); for kv and other one sentence.
import { readFileSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { checkBrief, parseBrief } from "./carl-brief.js";

const KV = /^[ \t]*(?:[-*][ \t]+)?\**[A-Z][A-Za-z ]{1,30}\**[ \t]*:/gm;
const NO_BRIEF = "The text has no TOML or JSON brief.";
const USAGE = `Usage: node carl-brief-check.mjs [--root DIR] [FILE]
       node carl-brief-check.mjs [--root DIR] --json < texts.json

Checks a coder brief (TOML or JSON) as CARL checks it before the coder gets the
task. FILE: a file with the brief, or with a task text that has one ("-" or no
FILE: the standard input). It prints the format, then "The brief is valid." or
each problem. Exit code: 0 valid, 1 problems or no brief, 2 a wrong command or
an input that cannot be read.

--root DIR  The project folder: each existing_tests path must be in it (CARL
            checks this in the project). Without --root it is not checked.
--json      The standard input is a JSON list of task texts; the output is a
            JSON list of { format, valid, problems }, one for each.
`;

/**
 * CARL's check of one task text. root: the project folder for the existing_tests rule (undefined: not checked).
 * @param {string} text @param {string} [root]
 * @returns {{ format: string, valid: boolean, problems: string[] }}
 */
export function checkText(text, root) {
  const r = parseBrief(text);
  if (r.format) {
    const problems = r.brief ? checkBrief(r.brief, r.format, { root }) : [r.error];
    return { format: r.format, valid: Boolean(r.brief) && problems.length === 0, problems };
  }
  const kv = (text.match(KV) ?? []).length >= 2;
  return { format: kv ? "kv" : "other", valid: false, problems: [NO_BRIEF] };
}

/** The format in words. @param {string} f */
function formatWords(f) {
  return f === "toml" ? "TOML" : f === "json" ? "JSON" : f === "kv" ? "none (Key: value lines)" : "none";
}

/**
 * Run the command: its exit code. out / err: where it writes; input: reads the standard input.
 * @param {string[]} args
 * @param {{ out?: (s: string) => void, err?: (s: string) => void, input?: () => string }} [io]
 * @returns {number}
 */
export function main(args, io = {}) {
  const out = io.out ?? ((s) => process.stdout.write(s));
  const err = io.err ?? ((s) => process.stderr.write(s));
  const input = io.input ?? (() => readFileSync(0, "utf8"));
  if (args.includes("-h") || args.includes("--help")) {
    out(USAGE);
    return 0;
  }
  /** @type {string | undefined} */
  let root;
  const rest = [...args];
  const at = rest.indexOf("--root");
  if (at >= 0) {
    root = rest[at + 1];
    rest.splice(at, 2);
    if (!root || root.startsWith("-")) {
      err(USAGE);
      return 2;
    }
  }
  const json = rest[0] === "--json";
  const files = json ? rest.slice(1) : rest;
  if (files.length > 1 || (json && files.length) || files.some((f) => f.startsWith("-") && f !== "-")) {
    err(USAGE);
    return 2;
  }
  let text;
  try {
    text = !files.length || files[0] === "-" ? input() : readFileSync(files[0], "utf8");
  } catch (e) {
    err(`carl-brief-check: cannot read ${files[0] ?? "the standard input"}: ${e instanceof Error ? e.message : e}\n`);
    return 2;
  }
  if (json) {
    let texts;
    try {
      texts = JSON.parse(text);
    } catch {
      texts = undefined;
    }
    if (!Array.isArray(texts)) {
      err("carl-brief-check: --json needs a JSON list of texts on the standard input.\n");
      return 2;
    }
    out(JSON.stringify(texts.map((t) => checkText(String(t ?? ""), root))));
    return 0;
  }
  const c = checkText(text, root);
  out(`Format: ${formatWords(c.format)}\n`);
  if (c.valid) {
    out("The brief is valid.\n");
    return 0;
  }
  if (c.format === "toml" || c.format === "json") {
    out(c.problems.length === 1 ? "The brief has 1 problem:\n" : `The brief has ${c.problems.length} problems:\n`);
    for (const p of c.problems) out(`- ${p}\n`);
  } else {
    out(`${NO_BRIEF}\n`);
  }
  return 1;
}

/** Run as a command (not when imported). */
function isMain() {
  try {
    return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) process.exitCode = main(process.argv.slice(2));

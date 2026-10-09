// @ts-check
// CARL: the coder's brief and the coder's report, as TOML (Phase 23.4.3; revision 4 of the brief, approved by the
// user on 2026-10-09: docs/phase-plans/phase23.4.3/brief-v4-draft.md). Shared by both clients' carl-delegation (each
// carries a copy, installed by client/configure.py next to carl-delegation.js); the chain of a test session and a
// code session (item 3) uses it too. No dependency: the plugins ship without npm installs.
//   - parseToml: a small TOML reader for the subset the brief uses (strings of all four kinds, lists, inline tables,
//     [table], [[list of tables]], comments; also true / false and plain numbers). Errors name the line.
//   - parseBrief: the brief from a task text (a ```toml fence, or TOML with prose before or after it), in one fixed
//     shape (Brief); checkBrief: what is missing or wrong, as whole sentences that a small model can act on (they
//     name the brief's own keys; a key of revision 1, such as mode or goal, gets the new key's name);
//     briefRefusal: the text that sends a task back to the main agent.
//   - the same brief as JSON (the reader stays: agent-bench's brief_json variant, now obsolete): parseJson, a small
//     JSON reader whose errors name the line as the TOML reader's do; parseBrief also takes a ```json fence, or a
//     JSON object with prose around it, with the same keys, and gives the same shape; the check's sentences then
//     name the keys the JSON way ("work_mode": "code"); writeBriefJson writes it back.
//   - the chain's helpers: filesByAction, requirementIds, checkIds, testsSetting, testSessionDue, testSessionBrief,
//     writeBrief; isTestFile (what the gates call a test file), namesTests; hashFiles and changedFiles (the test-file
//     freeze); parseReport (the coder's TOML report, or the same as JSON). carl-chain.js runs the chain with them.
//   - [test_session]: CARL's own table in the code session's brief, with two- or three-word keys as the rest
//     (test_files: the test files of the test session before it; session_summary, session_notes and
//     [[test_session.failing_test]] with test_name, requirement_id, failure_reason: that session's report in short);
//     the main agent never writes it.
// Pure except hashFiles and checkBrief's existing_tests rule with a project folder (they read the file system).

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

export const BRIEF_MARK = "[CARL] Brief refused";
export const WORK_MODES = ["code", "tests-only"];
export const WORK_TYPES = ["new_feature", "follow_up", "bug_fix"];
export const ACTIONS = ["create", "change", "read"];

// ================================================================== the TOML reader

/** A TOML text that the reader cannot read: `line` is the line in the whole task text. */
export class TomlError extends Error {
  /** @param {number} line @param {string} reason */
  constructor(line, reason) {
    super(`line ${line}: ${reason}`);
    this.name = "TomlError";
    this.line = line;
    this.reason = reason;
  }
}

const KEY = String.raw`(?:[A-Za-z0-9_-]+|"[^"\n]*"|'[^'\n]*')`;
/** A line that starts a TOML statement: key = ..., [table] or [[list of tables]]. */
const STATEMENT = new RegExp(String.raw`^\s*(?:\[\[?\s*${KEY}(?:\s*\.\s*${KEY})*\s*\]\]?\s*(?:#.*)?$|${KEY}(?:\s*\.\s*${KEY})*\s*=)`);
const LINE_END_BACKSLASH = /\\[ \t]*\n\s*/y;
const SCALAR = /(true|false)(?![A-Za-z0-9_-])|[+-]?\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?(?![A-Za-z0-9_.:-])/y;
const ESCAPES = /** @type {Record<string, string>} */ ({ b: "\b", t: "\t", n: "\n", f: "\f", r: "\r", '"': '"', "\\": "\\" });

/** @param {unknown} v @returns {v is Record<string, unknown>} */
function isTable(v) {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Read a TOML text (the subset above). firstLine: the line number of the text's first line (for the errors);
 * stopAtProse: a line that is not TOML ends the text when no TOML statement follows it (a model's closing words).
 * @param {string} text @param {{ firstLine?: number, stopAtProse?: boolean }} [o]
 * @returns {Record<string, unknown>}
 */
export function parseToml(text, o = {}) {
  const s = String(text).replace(/\r\n?/g, "\n");
  const first = o.firstLine ?? 1;
  let i = 0;
  const lineAt = (/** @type {number} */ p) => {
    let n = first;
    for (let k = 0; k < p && k < s.length; k++) if (s[k] === "\n") n++;
    return n;
  };
  /** @param {string} reason @param {number} [p] @returns {never} */
  const fail = (reason, p = i) => {
    throw new TomlError(lineAt(p), reason);
  };
  /** @type {Record<string, unknown>} */
  const root = {};
  const headed = new WeakSet();                               // tables opened by a [header]
  const listed = new WeakSet();                               // lists made by [[header]]
  let cur = root;

  const ws = () => {
    while (s[i] === " " || s[i] === "\t") i++;
  };
  const comment = () => {
    if (s[i] === "#") while (i < s.length && s[i] !== "\n") i++;
  };
  const blank = () => {                                       // spaces, comments and line ends
    for (;;) {
      ws();
      comment();
      if (s[i] !== "\n") return;
      i++;
    }
  };
  const escape = () => {                                      // at a backslash
    const c = s[i + 1];
    if (c !== undefined && c in ESCAPES) {
      i += 2;
      return ESCAPES[c];
    }
    if (c === "u" || c === "U") {
      const n = c === "u" ? 4 : 8;
      const hex = s.slice(i + 2, i + 2 + n);
      const cp = /^[0-9A-Fa-f]+$/.test(hex) && hex.length === n ? parseInt(hex, 16) : -1;
      if (cp >= 0 && cp <= 0x10ffff) {
        i += 2 + n;
        return String.fromCodePoint(cp);
      }
    }
    i += 1;
    return "\\";                                              // lenient: an unknown escape keeps its backslash
  };
  const basic = () => {
    const at = i++;
    let out = "";
    for (;;) {
      if (i >= s.length || s[i] === "\n") fail('this text has no closing ": close it on the same line, or use """ for text on several lines', at);
      if (s[i] === '"') {
        i++;
        return out;
      }
      out += s[i] === "\\" ? escape() : s[i++];
    }
  };
  const literal = () => {
    const at = i++;
    const end = s.indexOf("'", i);
    const nl = s.indexOf("\n", i);
    if (end < 0 || (nl >= 0 && nl < end)) fail("this text has no closing ': close it on the same line, or use ''' for text on several lines", at);
    const out = s.slice(i, end);
    i = end + 1;
    return out;
  };
  /** @param {string} q the quotes: """ or ''' */
  const multi = (q) => {
    const at = i;
    i += 3;
    if (s[i] === "\n") i++;                                   // the line end right after the quotes is not text
    let out = "";
    for (;;) {
      if (i >= s.length) fail(`this text has no closing ${q}`, at);
      if (s.startsWith(q, i)) {
        let n = 3;
        while (s[i + n] === q[0] && n < 5) n++;               // up to two quotes may end the text itself
        out += q[0].repeat(n - 3);
        i += n;
        return out;
      }
      if (q === '"""' && s[i] === "\\") {
        LINE_END_BACKSLASH.lastIndex = i;
        if (LINE_END_BACKSLASH.test(s)) {
          i = LINE_END_BACKSLASH.lastIndex;
          continue;
        }
        out += escape();
        continue;
      }
      out += s[i++];
    }
  };
  const keyPart = () => {
    if (s[i] === '"') return basic();
    if (s[i] === "'") return literal();
    const st = i;
    while (i < s.length && /[A-Za-z0-9_-]/.test(s[i])) i++;
    if (i === st) fail("a key is missing here: write key = value");
    return s.slice(st, i);
  };
  const keyPath = () => {
    const parts = [keyPart()];
    for (;;) {
      ws();
      if (s[i] !== ".") return parts;
      i++;
      ws();
      parts.push(keyPart());
    }
  };
  /** The table at key k of t (made when it is not there; the last item of a [[list]]). */
  const descend = (/** @type {Record<string, unknown>} */ t, /** @type {string} */ k, /** @type {number} */ at) => {
    if (k === "__proto__") fail("this key name is not allowed", at);
    const v = t[k];
    if (v === undefined) return /** @type {Record<string, unknown>} */ (t[k] = {});
    if (Array.isArray(v) && listed.has(v)) return /** @type {Record<string, unknown>} */ (v[v.length - 1]);
    if (isTable(v)) return v;
    return fail(`${k} already has a value, so it cannot hold keys too`, at);
  };
  const put = (/** @type {Record<string, unknown>} */ t, /** @type {string[]} */ path, /** @type {unknown} */ v,
               /** @type {number} */ at) => {
    let tab = t;
    for (const k of path.slice(0, -1)) tab = descend(tab, k, at);
    const last = path[path.length - 1];
    if (last === "__proto__") fail("this key name is not allowed", at);
    if (Object.prototype.hasOwnProperty.call(tab, last)) fail(`the key ${path.join(".")} is there twice: give it once`, at);
    tab[last] = v;
  };
  const array = () => {
    const at = i++;
    /** @type {unknown[]} */
    const out = [];
    for (;;) {
      blank();
      if (i >= s.length) fail("this list has no closing ]", at);
      if (s[i] === "]") {
        i++;
        return out;
      }
      out.push(value());
      blank();
      if (i >= s.length) fail("this list has no closing ]", at);
      if (s[i] === ",") {
        i++;
        continue;
      }
      if (s[i] === "]") {
        i++;
        return out;
      }
      fail("a list needs a comma between its items and ] at its end");
    }
  };
  const inlineTable = () => {
    const at = i++;
    /** @type {Record<string, unknown>} */
    const out = {};
    for (;;) {
      blank();                                                // lenient: a { table } may run over several lines
      if (i >= s.length) fail("this { table } has no closing }", at);
      if (s[i] === "}") {
        i++;
        return out;
      }
      const kat = i;
      const path = keyPath();
      // lenient (1.13.2): a JSON-style "key": value inside { } as well as key = value; small models write a list of
      // tables as task_requirement = [ { "requirement_id": "R1", ... } ] (measured: the Gemma 4 E4B, 2026-10-09)
      if (s[i] !== "=" && s[i] !== ":") fail("a key needs = and a value");
      i++;
      ws();
      put(out, path, value(), kat);
      blank();
      if (s[i] === ",") {
        i++;
        continue;
      }
      if (s[i] === "}") {
        i++;
        return out;
      }
      fail("a { table } needs a comma between its keys and } at its end");
    }
  };
  /** @returns {unknown} */
  const value = () => {
    if (s.startsWith('"""', i)) return multi('"""');
    if (s.startsWith("'''", i)) return multi("'''");
    if (s[i] === '"') return basic();
    if (s[i] === "'") return literal();
    if (s[i] === "[") return array();
    if (s[i] === "{") return inlineTable();
    SCALAR.lastIndex = i;
    const m = SCALAR.exec(s);
    if (m) {
      i = SCALAR.lastIndex;
      return m[1] ? m[1] === "true" : Number(m[0].replace(/_/g, ""));
    }
    if (i >= s.length || s[i] === "\n" || s[i] === "#") fail("the value is missing after =");
    return fail('this value has no quotes: write text as "text", a list as ["a", "b"]');
  };
  const header = () => {
    const at = i;
    const many = s[i + 1] === "[";
    i += many ? 2 : 1;
    ws();
    const path = keyPath();
    const name = path.join(".");
    if (many ? !s.startsWith("]]", i) : s[i] !== "]") fail(many ? `[[${name}]] needs ]] at its end` : `[${name}] needs ] at its end`, at);
    i += many ? 2 : 1;
    let t = root;
    for (const k of path.slice(0, -1)) t = descend(t, k, at);
    const last = path[path.length - 1];
    if (last === "__proto__") fail("this key name is not allowed", at);
    const v = t[last];
    if (many) {
      if (v !== undefined && !(Array.isArray(v) && listed.has(v))) fail(`${name} already has a value, so it cannot be a [[${name}]] list too`, at);
      const list = /** @type {unknown[]} */ (v ?? (t[last] = []));
      listed.add(list);
      /** @type {Record<string, unknown>} */
      const item = {};
      list.push(item);
      cur = item;
      return;
    }
    if (v !== undefined && (!isTable(v) || headed.has(v))) fail(`[${name}] is there twice: put all its keys under one [${name}]`, at);
    const tab = /** @type {Record<string, unknown>} */ (v ?? (t[last] = {}));
    headed.add(tab);
    cur = tab;
  };

  for (;;) {
    blank();
    if (i >= s.length) break;
    const eol = s.indexOf("\n", i) < 0 ? s.length : s.indexOf("\n", i);
    if (!STATEMENT.test(s.slice(i, eol))) {
      if (o.stopAtProse && !s.slice(eol).split("\n").some((l) => STATEMENT.test(l))) break;
      fail("this line is not TOML: write key = value, a [table] or a [[list item]]");
    }
    if (s[i] === "[") header();
    else {
      const at = i;
      const path = keyPath();
      if (s[i] !== "=") fail("a key needs = and a value");
      i++;
      ws();
      put(cur, path, value(), at);
    }
    ws();
    comment();
    if (i < s.length && s[i] !== "\n") fail("there is more text after the value: write one key = value on each line");
  }
  return root;
}

// ================================================================== the JSON reader

/** A JSON text that the reader cannot read: `line` is the line in the whole task text. */
export class JsonError extends Error {
  /** @param {number} line @param {string} reason */
  constructor(line, reason) {
    super(`line ${line}: ${reason}`);
    this.name = "JsonError";
    this.line = line;
    this.reason = reason;
  }
}

const JSON_ESCAPES = /** @type {Record<string, string>} */ ({ ...ESCAPES, "/": "/" });
const JSON_NUMBER = /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![A-Za-z0-9_.])/y;

/**
 * Read one JSON object. Lenient where a small model slips: a comma before } or ], a line end inside a text, an
 * unknown escape (it keeps its backslash). A key whose value is null counts as not there (and a null list item
 * too). firstLine: the line number of the text's first line (for the errors); text after the object's closing }
 * is an error, except white space.
 * @param {string} text @param {{ firstLine?: number }} [o] @returns {Record<string, unknown>}
 */
export function parseJson(text, o = {}) {
  const s = String(text).replace(/\r\n?/g, "\n");
  const first = o.firstLine ?? 1;
  let i = 0;
  /** @param {string} reason @param {number} [p] @returns {never} */
  const fail = (reason, p = i) => {
    let n = first;
    for (let k = 0; k < p && k < s.length; k++) if (s[k] === "\n") n++;
    throw new JsonError(n, reason);
  };
  const ws = () => {
    while (i < s.length && /\s/.test(s[i])) i++;
  };
  const string = () => {
    const at = i++;
    let out = "";
    for (;;) {
      if (i >= s.length) fail('this text has no closing ": close it with "', at);
      const c = s[i];
      if (c === '"') {
        i++;
        return out;
      }
      if (c !== "\\") {
        out += c;
        i++;
        continue;
      }
      const e = s[i + 1];
      if (e !== undefined && e in JSON_ESCAPES) {
        out += JSON_ESCAPES[e];
        i += 2;
      } else if (e === "u" && /^[0-9A-Fa-f]{4}$/.test(s.slice(i + 2, i + 6))) {
        out += String.fromCharCode(parseInt(s.slice(i + 2, i + 6), 16));
        i += 6;
      } else {
        out += "\\";                                          // lenient: an unknown escape keeps its backslash
        i++;
      }
    }
  };
  /** @returns {unknown} */
  const value = () => {
    ws();
    const c = s[i];
    if (c === '"') return string();
    if (c === "{") return object();
    if (c === "[") return array();
    for (const [word, v] of /** @type {[string, unknown][]} */ ([["true", true], ["false", false], ["null", null]])) {
      if (s.startsWith(word, i) && !/[A-Za-z0-9_]/.test(s[i + word.length] ?? "")) {
        i += word.length;
        return v;
      }
    }
    JSON_NUMBER.lastIndex = i;
    const m = JSON_NUMBER.exec(s);
    if (m) {
      i = JSON_NUMBER.lastIndex;
      return Number(m[0]);
    }
    if (i >= s.length || c === "," || c === "}" || c === "]") fail("the value is missing here");
    return fail('this value has no quotes: write text as "text", a list as ["a", "b"]');
  };
  const array = () => {
    const at = i++;
    /** @type {unknown[]} */
    const out = [];
    for (;;) {
      ws();
      if (i >= s.length) fail("this list has no closing ]", at);
      if (s[i] === "]") {
        i++;
        return out;
      }
      const v = value();
      if (v !== null) out.push(v);
      ws();
      if (i >= s.length) fail("this list has no closing ]", at);
      if (s[i] === ",") {
        i++;
        continue;
      }
      if (s[i] === "]") {
        i++;
        return out;
      }
      fail("a list needs a comma between its items and ] at its end");
    }
  };
  /** @returns {Record<string, unknown>} */
  const object = () => {
    const at = i++;
    /** @type {Record<string, unknown>} */
    const out = {};
    for (;;) {
      ws();
      if (i >= s.length) fail("this { object } has no closing }", at);
      if (s[i] === "}") {
        i++;
        return out;
      }
      if (s[i] !== '"') fail('a key needs quotes: write "key": value');
      const kat = i;
      const k = string();
      ws();
      if (s[i] !== ":") fail('a key needs : and a value: write "key": value');
      i++;
      const v = value();
      if (k === "__proto__") fail("this key name is not allowed", kat);
      if (Object.prototype.hasOwnProperty.call(out, k)) fail(`the key "${k}" is there twice: give it once`, kat);
      if (v !== null) out[k] = v;
      ws();
      if (i >= s.length) fail("this { object } has no closing }", at);
      if (s[i] === ",") {
        i++;
        continue;
      }
      if (s[i] === "}") {
        i++;
        return out;
      }
      fail("an { object } needs a comma between its keys and } at its end");
    }
  };
  ws();
  if (s[i] !== "{") fail('the brief is not a JSON object: write it as { "work_mode": "code", ... }');
  const root = object();
  ws();
  if (i < s.length) fail("there is more text after the closing }: the brief is one JSON object");
  return root;
}


// ================================================================== the brief's keys

/**
 * The keys of revision 4: each top-level key, and for each table its own keys (null: a plain value). test_session is
 * CARL's own table (the code session's brief; the main agent never writes it).
 * @type {Record<string, string[] | null>}
 */
const SCHEMA = {
  work_mode: null, work_type: null, existing_tests: null,
  task_summary: null, expected_outcome: null, current_state: null, design_notes: null,
  exact_interfaces: null, scope_limits: null,
  reference_doc: ["doc_path", "doc_purpose"],
  known_file: ["file_path", "file_action"],
  task_requirement: ["requirement_id", "requirement_text"],
  acceptance_check: ["check_id", "covers_requirements", "run_command", "expected_result"],
  project_rule: ["rule_text", "rule_source"],
  input_example: ["example_source", "example_text"],
  failed_attempt: ["run_command", "error_output"],
  tried_fix: ["fix_change", "fix_result"],
  test_session: ["test_files", "session_summary", "session_notes", "failing_test"],
};
/** Keys that are not revision 4's but name one of its keys: revision 1's keys (and the plural forms a model may
 * write, of revision 1's tables and of revision 4's). The refusal names the new key. */
const RENAMED = /** @type {Record<string, string[]>} */ ({
  mode: ["work_mode"], tests: ["work_type", "existing_tests"], goal: ["task_summary"],
  scope: ["scope_limits"], scope_in: ["scope_limits"], scope_out: ["scope_limits"],
  file: ["known_file"], files: ["known_file"], requirement: ["task_requirement"], requirements: ["task_requirement"],
  check: ["acceptance_check"], checks: ["acceptance_check"], acceptance: ["acceptance_check"],
  constraint: ["project_rule"], constraints: ["project_rule"], example: ["input_example"], examples: ["input_example"],
  error: ["failed_attempt"], errors: ["failed_attempt"], tried: ["tried_fix"], tries: ["tried_fix"],
  known_files: ["known_file"], task_requirements: ["task_requirement"], acceptance_checks: ["acceptance_check"],
  project_rules: ["project_rule"], input_examples: ["input_example"], reference_docs: ["reference_doc"],
  tried_fixes: ["tried_fix"], failed_attempts: ["failed_attempt"],
});

/** The brief's own keys at the start of a line (revision 4's, and revision 1's so that its refusal names the new
 * keys): tells a TOML brief from other text. */
const BRIEF_KEYS = new RegExp(String.raw`^\s*(?:(?:work_mode|work_type|existing_tests|task_summary|expected_outcome|current_state|design_notes|exact_interfaces|scope_limits|mode|tests|goal)\s*=|\[\[?\s*(?:reference_doc|known_file|task_requirement|acceptance_check|project_rule|input_example|failed_attempt|tried_fix|scope|file|requirement|check|constraint|example|error|tried)\s*\]\]?)`, "m");
/** A key of the brief as a JSON key (revision 4's and revision 1's, and the plural forms a model may write): tells a
 * JSON brief from other text. */
const JSON_BRIEF_KEYS = /"(?:work_mode|work_type|task_summary|expected_outcome|known_files?|task_requirements?|acceptance_checks?|mode|tests|goal|scope|files?|requirements?|checks?|constraints?|examples?|error|tried)"\s*:/;
/** A fence's text starts a TOML brief: its work_mode (or revision 1's mode). */
const MODE_LINE = /^\s*(?:work_)?mode\s*=/m;

/**
 * The end of the JSON object that starts at p (a "{"): the index after its closing }, or -1 when it has none.
 * Quotes are followed (a brace in a text does not count).
 * @param {string} t @param {number} p
 */
function objectEnd(t, p) {
  let depth = 0;
  for (let k = p; k < t.length; k++) {
    const c = t[k];
    if (c === '"') {
      for (k++; k < t.length && t[k] !== '"'; k++) if (t[k] === "\\") k++;
    } else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return k + 1;
  }
  return -1;
}

/**
 * The JSON part of a text that is not fenced: the first object that starts a line and has keys that `keys`
 * matches (to its closing }; with no closing }, the rest of the text, so that the reader names the error).
 * @param {string} t @param {RegExp} keys
 * @returns {{ json: string, start: number } | null}
 */
function findJsonObject(t, keys) {
  for (const m of t.matchAll(/^[ \t]*\{/gm)) {
    const p = (m.index ?? 0) + m[0].length - 1;
    const end = objectEnd(t, p);
    const part = end < 0 ? t.slice(p) : t.slice(p, end);
    if (keys.test(part)) return { json: part, start: p };
  }
  return null;
}

/**
 * The TOML part of a task text: a ```toml fence (or a ``` fence with `work_mode =` in it), else the text from its
 * first TOML line on (prose after it is cut by the reader). Pi's subagent puts "Task: " before the text: it goes.
 * found: false when the text has no TOML in it at all.
 * @param {string} text @returns {{ toml: string, firstLine: number, found: boolean, fenced: boolean }}
 */
export function extractToml(text) {
  const t = String(text ?? "").replace(/\r\n?/g, "\n").replace(/^\s*Task:[ \t]*/, (m) => m.replace(/[^\n]/g, ""));
  const lineOf = (/** @type {number} */ p) => t.slice(0, p).split("\n").length;
  let fence = /^[ \t]*```[ \t]*toml[ \t]*\n([\s\S]*?)^[ \t]*```/im.exec(t);
  if (!fence) {
    for (const m of t.matchAll(/^[ \t]*```[^\n]*\n([\s\S]*?)^[ \t]*```/gm)) {
      if (MODE_LINE.test(m[1])) {
        fence = m;
        break;
      }
    }
  }
  if (fence) {
    const start = (fence.index ?? 0) + fence[0].indexOf("\n") + 1;
    return { toml: fence[1], firstLine: lineOf(start), found: true, fenced: true };
  }
  const lines = t.split("\n");
  const k = lines.findIndex((l) => STATEMENT.test(l));
  if (k < 0 || !BRIEF_KEYS.test(t)) return { toml: "", firstLine: 1, found: false, fenced: false };
  return { toml: lines.slice(k).join("\n"), firstLine: k + 1, found: true, fenced: false };
}

/**
 * The brief part of a task text and its format: a ```toml or ```json fence (the first one), a plain ``` fence with
 * `work_mode =` (TOML) or a JSON object with the brief's keys in it; else, with no fence, TOML from its first line on
 * (as extractToml) or the first JSON object that starts a line and has the brief's keys, whichever comes first. Pi's
 * "Task: " before the text goes. format "": the text has no brief at all.
 * @param {string} text @returns {{ format: "toml" | "json" | "", text: string, firstLine: number }}
 */
export function extractBrief(text) {
  const t = String(text ?? "").replace(/\r\n?/g, "\n").replace(/^\s*Task:[ \t]*/, (m) => m.replace(/[^\n]/g, ""));
  const lineOf = (/** @type {number} */ p) => t.slice(0, p).split("\n").length;
  /** @type {{ format: "toml" | "json", m: RegExpExecArray } | null} */
  let hit = null;
  for (const m of t.matchAll(/^[ \t]*```[ \t]*(toml|json)[ \t]*\n([\s\S]*?)^[ \t]*```/gim)) {
    hit = { format: /** @type {"toml" | "json"} */ (m[1].toLowerCase()), m };
    break;
  }
  if (!hit) {
    for (const m of t.matchAll(/^[ \t]*```[^\n]*\n([\s\S]*?)^[ \t]*```/gm)) {
      const body = m[1];
      if (MODE_LINE.test(body)) hit = { format: "toml", m };
      else if (body.trimStart().startsWith("{") && JSON_BRIEF_KEYS.test(body)) hit = { format: "json", m };
      if (hit) break;
    }
  }
  if (hit) {
    const body = hit.m[hit.m.length - 1];
    const start = (hit.m.index ?? 0) + hit.m[0].indexOf("\n") + 1;
    return { format: hit.format, text: body, firstLine: lineOf(start) };
  }
  const lines = t.split("\n");
  const k = BRIEF_KEYS.test(t) ? lines.findIndex((l) => STATEMENT.test(l)) : -1;
  const tomlAt = k < 0 ? -1 : lines.slice(0, k).reduce((n, l) => n + l.length + 1, 0);
  const js = findJsonObject(t, JSON_BRIEF_KEYS);
  if (js && (tomlAt < 0 || js.start < tomlAt)) return { format: "json", text: js.json, firstLine: lineOf(js.start) };
  if (k >= 0) return { format: "toml", text: lines.slice(k).join("\n"), firstLine: k + 1 };
  return { format: "", text: "", firstLine: 1 };
}

// ================================================================== the brief

/**
 * The brief in one fixed shape (the keys of revision 4 in camel case; files: the [[known_file]] tables, requirements:
 * [[task_requirement]], checks: [[acceptance_check]]).
 * @typedef {{ path: string, action: string }} BriefFile
 * @typedef {{ id: string, text: string }} Requirement
 * @typedef {{ id: string, covers: string[], run: string, expect: string }} Check
 * @typedef {{
 *   workMode: string, workType: string, existingTests: string[],
 *   taskSummary: string, expectedOutcome: string, currentState: string, designNotes: string,
 *   exactInterfaces: string[], scopeLimits: string,
 *   referenceDocs: { path: string, purpose: string }[],
 *   files: BriefFile[], requirements: Requirement[], checks: Check[],
 *   projectRules: { text: string, source: string }[], inputExamples: { source: string, text: string }[],
 *   failedAttempt: { run: string, output: string } | null, triedFixes: { change: string, result: string }[],
 *   testSession: TestSession | null,
 *   unknown: string[]
 * }} Brief
 * @typedef {{ test: string, requirement: string, why: string }} Failing
 * @typedef {{ files: string[], summary: string, failing: Failing[], notes: string[] }} TestSession
 */

/** @param {unknown} v */
const str = (v) => (typeof v === "string" ? v.trim() : typeof v === "number" || typeof v === "boolean" ? String(v) : "");
/** Text kept as it was written (examples, error output): only the line ends around it go. @param {unknown} v */
const text = (v) => (typeof v === "string" ? v.replace(/^\n+|\s+$/g, "") : str(v));
/** @param {unknown} v @returns {unknown[]} */
const list = (v) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
/** @param {unknown} v @returns {Record<string, unknown>} */
const tab = (v) => (isTable(v) ? v : {});
/** A path as the brief means it: relative to the project, without "./". @param {unknown} v */
const briefPath = (v) => str(v).replace(/\\/g, "/").replace(/^(\.\/)+/, "");

/**
 * The keys of a raw brief that are not revision 4's: a top-level key, or a key in one of its tables
 * ("known_file.path").
 * @param {Record<string, unknown>} raw @returns {string[]}
 */
function unknownKeys(raw) {
  /** @type {string[]} */
  const out = [];
  for (const k of Object.keys(raw)) {
    if (!Object.prototype.hasOwnProperty.call(SCHEMA, k)) {
      out.push(k);
      continue;
    }
    const keys = SCHEMA[k];
    if (!keys) continue;
    for (const item of list(raw[k])) {
      for (const sub of Object.keys(tab(item))) if (!keys.includes(sub) && !out.includes(`${k}.${sub}`)) out.push(`${k}.${sub}`);
    }
  }
  return out;
}

/**
 * The brief in one fixed shape. Lenient where a small model writes the same thing another way: a single [known_file]
 * for a list, covers_requirements as one id, existing_tests as one path, "Code" for "code".
 * @param {Record<string, unknown>} raw @returns {Brief}
 */
export function normalizeBrief(raw) {
  const fa = raw.failed_attempt;
  return {
    workMode: str(raw.work_mode).toLowerCase(),
    workType: str(raw.work_type).toLowerCase(),
    existingTests: list(raw.existing_tests).map(briefPath).filter(Boolean),
    taskSummary: str(raw.task_summary),
    expectedOutcome: str(raw.expected_outcome),
    currentState: str(raw.current_state),
    designNotes: str(raw.design_notes),
    exactInterfaces: list(raw.exact_interfaces).map(str).filter(Boolean),
    scopeLimits: str(raw.scope_limits),
    referenceDocs: list(raw.reference_doc).map((d) => ({ path: str(tab(d).doc_path), purpose: str(tab(d).doc_purpose) })),
    files: list(raw.known_file).map((f) => ({ path: briefPath(tab(f).file_path), action: str(tab(f).file_action).toLowerCase() })),
    requirements: list(raw.task_requirement).map((r) => ({ id: str(tab(r).requirement_id), text: str(tab(r).requirement_text) })),
    checks: list(raw.acceptance_check).map((c) => ({ id: str(tab(c).check_id), covers: list(tab(c).covers_requirements).map(str).filter(Boolean),
                                                     run: str(tab(c).run_command), expect: str(tab(c).expected_result) })),
    projectRules: list(raw.project_rule).map((c) => ({ text: str(tab(c).rule_text), source: str(tab(c).rule_source) })),
    inputExamples: list(raw.input_example).map((e) => ({ source: str(tab(e).example_source), text: text(tab(e).example_text) })),
    failedAttempt: fa === undefined ? null : typeof fa === "string" ? { run: "", output: text(fa) }
      : { run: str(tab(fa).run_command), output: text(tab(fa).error_output) },
    triedFixes: list(raw.tried_fix).map((t) => (typeof t === "string" ? { change: t.trim(), result: "" }
      : { change: str(tab(t).fix_change), result: str(tab(t).fix_result) })),
    testSession: raw.test_session === undefined ? null : testSession(tab(raw.test_session)),
    unknown: unknownKeys(raw),
  };
}

/**
 * CARL's [test_session] table in one fixed shape: test_files, session_summary, session_notes and
 * [[test_session.failing_test]] (test_name, requirement_id, failure_reason; the keys of the report's [[test_finding]]).
 * @param {Record<string, unknown>} t @returns {TestSession}
 */
function testSession(t) {
  return {
    files: list(t.test_files).map(briefPath).filter(Boolean),
    summary: text(t.session_summary),
    failing: list(t.failing_test).map((f) => ({ test: str(tab(f).test_name), requirement: str(tab(f).requirement_id),
                                                 why: str(tab(f).failure_reason) })),
    notes: list(t.session_notes).map(str).filter(Boolean),
  };
}

/**
 * The brief in a task text, TOML or JSON (extractBrief). format: "toml", "json", or "" when the text has no brief
 * at all; toml: the brief is TOML; error: the reader's error (with the line), "" when it read.
 * @param {string} taskText
 * @returns {{ brief: Brief | null, error: string, toml: boolean, format: "toml" | "json" | "" }}
 */
export function parseBrief(taskText) {
  const x = extractBrief(taskText);
  if (!x.format) return { brief: null, error: "", toml: false, format: "" };
  const toml = x.format === "toml";
  try {
    const raw = toml ? parseToml(x.text, { firstLine: x.firstLine, stopAtProse: true }) : parseJson(x.text, { firstLine: x.firstLine });
    return { brief: normalizeBrief(raw), error: "", toml, format: x.format };
  } catch (e) {
    if (e instanceof TomlError || e instanceof JsonError) return { brief: null, error: e.message, toml, format: x.format };
    throw e;
  }
}

/**
 * A test file, by its path in the project: a path under a folder tests/ or test/, or a file named test_*.py,
 * *_test.py, *.test.* or *.spec.*. The gates and the chain use this one rule.
 * @param {string} path a path relative to the project folder
 */
export function isTestFile(path) {
  const parts = String(path).replace(/\\/g, "/").split("/").filter((p) => p && p !== ".");
  const name = parts.pop() ?? "";
  if (parts.some((d) => d === "tests" || d === "test")) return true;
  return /^test_.+\.py$/.test(name) || /.+_test\.py$/.test(name) || /.+\.(?:test|spec)\.[^.]+$/.test(name);
}

/** A check's run names tests: a word that is a test file, the folder tests, or a path in a folder tests/ or test/
 * ("npm test" names none). @param {string} run */
export function namesTests(run) {
  return run.split(/[\s"'=]+/).some((w) => {
    const p = w.replace(/::.*$/, "").replace(/^\.\//, "");
    return p === "tests" || /^tests?\//.test(p) || /\/tests?(?:\/|$)/.test(p) || (p.includes(".") && isTestFile(p));
  });
}

/** @param {number} n */
const nth = (n) => `number ${n}`;

/**
 * How the check's sentences write the brief's keys: as TOML (key = "value", [table], [[list item]]) or as JSON
 * ("key": "value", "table", "list").
 * @typedef {{ kv: (k: string, v: string) => string, table: (n: string) => string, item: (n: string) => string,
 *             addItem: (n: string) => string }} Syntax
 */
/** @type {Record<"toml" | "json", Syntax>} */
const SYNTAX = {
  toml: {
    kv: (k, v) => `${k} = ${v}`,
    table: (n) => `[${n}]`,
    item: (n) => `[[${n}]]`,
    addItem: (n) => `add a [[${n}]]`,
  },
  json: {
    kv: (k, v) => `"${k}": ${v}`,
    table: (n) => `"${n}"`,
    item: (n) => `"${n}"`,
    addItem: (n) => `add an item to "${n}"`,
  },
};

/**
 * Is a path in the project folder and there? @param {string} p a path of the brief @param {string} root
 * @param {(p: string) => boolean} exists
 */
function inProjectThere(p, root, exists) {
  const top = resolve(root);
  const abs = resolve(top, p);
  const r = relative(top, abs);
  return r !== ".." && !r.startsWith("../") && !isAbsolute(r) && exists(abs);
}

/**
 * What is missing or wrong in a brief, as whole sentences (empty: the brief is complete). The rules of revision 4
 * (brief-v4-draft.md and the full plan's "Revision 4 of the brief"): work_mode and work_type; task_summary and
 * expected_outcome; current_state for follow_up and bug_fix; in work_mode code a [[known_file]] to create or change;
 * at least one [[task_requirement]], each in the covers_requirements of an [[acceptance_check]] with a run_command;
 * [failed_attempt] only for bug_fix, and with [[tried_fix]]. Plus: keys that are not in the schema (a key of
 * revision 1 gets the new key's name), the file actions, the ids, in tests-only a file to write that is not a test
 * file (the gate would refuse it), and, with o.root (the project folder), each existing_tests path is there.
 * format: how the sentences write the keys (the format the brief came in: parseBrief's format).
 * @param {Brief} b @param {"toml" | "json" | ""} [format]
 * @param {{ root?: string, exists?: (p: string) => boolean }} [o] o.exists: for tests (default: the file system)
 * @returns {string[]}
 */
export function checkBrief(b, format = "toml", o = {}) {
  const { kv, table, item, addItem } = SYNTAX[format === "json" ? "json" : "toml"];
  /** @type {string[]} */
  const out = [];
  for (const k of b.unknown) {
    const use = RENAMED[k];
    out.push(use ? `The brief has the key "${k}", which is not in the schema: use ${use.map((u) => `"${u}"`).join(" and ")}.`
      : `The brief has the key "${k}", which is not in the schema: remove it, or put its content in a key of the schema.`);
  }
  if (!b.workMode) {
    out.push(`work_mode is missing: write ${kv("work_mode", '"code"')} (the hand-off writes or changes program code) or ${kv("work_mode", '"tests-only"')} (it writes or changes tests only).`);
  } else if (!WORK_MODES.includes(b.workMode)) {
    out.push(`${kv("work_mode", `"${b.workMode}"`)} is not valid: write ${kv("work_mode", '"code"')} or ${kv("work_mode", '"tests-only"')}.`);
  }
  const code = b.workMode === "code";
  if (!b.workType) {
    out.push(`work_type is missing: write ${kv("work_type", '"new_feature"')} (new behaviour), "follow_up" (changes to the work the coder just did) or "bug_fix" (a fix).`);
  } else if (!WORK_TYPES.includes(b.workType)) {
    out.push(`${kv("work_type", `"${b.workType}"`)} is not valid: write ${kv("work_type", '"new_feature"')}, "follow_up" or "bug_fix".`);
  }
  if (!b.taskSummary) out.push("task_summary is empty: write the request distilled into a few sentences: what and why.");
  if (!b.expectedOutcome) out.push("expected_outcome is empty: write what the finished work looks like from the user's side.");
  if (!b.currentState && (b.workType === "follow_up" || b.workType === "bug_fix")) {
    out.push(`current_state is empty: with ${kv("work_type", `"${b.workType}"`)}, write what exists now that the coder builds on.`);
  }
  if (o.root !== undefined) {
    const exists = o.exists ?? existsSync;
    for (const p of b.existingTests) {
      if (!inProjectThere(p, o.root, exists)) out.push(`existing_tests has "${p}", which is not in the project: give the path of a test file that exists, or remove it.`);
    }
  }

  b.files.forEach((f, n) => {
    const name = f.path || `${nth(n + 1)}`;
    if (!f.path) out.push(`Known file ${nth(n + 1)} has no file_path: give it ${kv("file_path", '"the file\'s path in the project"')}.`);
    if (!ACTIONS.includes(f.action)) {
      out.push(f.action ? `Known file ${name} has ${kv("file_action", `"${f.action}"`)}: use "create", "change" or "read".`
        : `Known file ${name} has no file_action: write ${kv("file_action", '"create"')}, "change" or "read".`);
    } else if (b.workMode === "tests-only" && f.action !== "read" && f.path && !isTestFile(f.path)) {
      out.push(`Known file ${f.path} has ${kv("file_action", `"${f.action}"`)}, but with ${kv("work_mode", '"tests-only"')} the coder writes test files only: set its file_action to "read", or remove it.`);
    }
  });
  if (code && !b.files.some((f) => f.action === "create" || f.action === "change")) {
    out.push(`The brief has no file to create or change: ${addItem("known_file")} with file_path and ${kv("file_action", '"create"')} or "change" for a file you know the coder will write (the list does not have to be complete).`);
  }

  if (!b.requirements.length) {
    out.push(`The brief has no requirement: ${addItem("task_requirement")} with ${kv("requirement_id", '"R1"')} and ${kv("requirement_text", '"..."')} for each point of the request.`);
  }
  const ids = new Set();
  b.requirements.forEach((r, n) => {
    if (!r.id) out.push(`Requirement ${nth(n + 1)} has no requirement_id: give it ${kv("requirement_id", `"R${n + 1}"`)}.`);
    else if (ids.has(r.id)) out.push(`The requirement_id "${r.id}" is on more than one requirement: give each requirement its own id.`);
    ids.add(r.id);
    if (!r.text) out.push(`Requirement ${r.id || nth(n + 1)} has no requirement_text: say in one point what it must do.`);
  });

  const checkIds = new Set();
  b.checks.forEach((c, n) => {
    const name = c.id || nth(n + 1);
    if (!c.id) out.push(`Check ${nth(n + 1)} has no check_id: give it ${kv("check_id", `"C${n + 1}"`)}.`);
    else if (checkIds.has(c.id)) out.push(`The check_id "${c.id}" is on more than one check: give each check its own id.`);
    checkIds.add(c.id);
    if (!c.run) out.push(`Check ${name} has no run_command: give the command that checks it, for example ${kv("run_command", '"python -m pytest tests/test_x.py"')}.`);
    for (const id of c.covers) {
      if (!ids.has(id)) out.push(`Check ${name} covers "${id}", but no requirement has that requirement_id: fix the id, or add the requirement.`);
    }
    if (!c.covers.length) out.push(`Check ${name} covers no requirement: write ${kv("covers_requirements", '["R1"]')} with the requirement ids it checks.`);
  });
  if (b.requirements.length && !b.checks.length) {
    out.push(`The brief has no check: ${addItem("acceptance_check")} with check_id, covers_requirements, run_command and expected_result, so that every requirement is in the covers_requirements of a check.`);
  } else {
    const covered = new Set(b.checks.flatMap((c) => c.covers));
    for (const r of b.requirements) {
      if (r.id && !covered.has(r.id)) out.push(`Requirement ${r.id} is in no check's covers_requirements: add it to a check, or add a check for it.`);
    }
  }

  const fix = [b.failedAttempt ? table("failed_attempt") : "", b.triedFixes.length ? item("tried_fix") : ""].filter(Boolean);
  if (fix.length && WORK_TYPES.includes(b.workType) && b.workType !== "bug_fix") {
    out.push(`${fix.join(" and ")} ${fix.length > 1 ? "are" : "is"} only for ${kv("work_type", '"bug_fix"')} (a fix that already failed): set ${kv("work_type", '"bug_fix"')}, or remove ${fix.length > 1 ? "them" : "it"}.`);
  }
  if (b.failedAttempt) {
    if (!b.failedAttempt.run) out.push(`${table("failed_attempt")} has no run_command: give the command that fails.`);
    if (!b.failedAttempt.output) out.push(`${table("failed_attempt")} has no error_output: copy the exact output of that command.`);
  }
  if (b.triedFixes.length && !b.failedAttempt) {
    out.push(`${item("tried_fix")} is only for a fix that already failed: add the ${table("failed_attempt")} with run_command and error_output, or remove ${item("tried_fix")}.`);
  }
  b.triedFixes.forEach((t, n) => {
    if (!t.change) out.push(`Tried fix ${nth(n + 1)} has no fix_change: say what was changed.`);
    if (!t.result) out.push(`Tried fix ${nth(n + 1)} has no fix_result: say what happened.`);
  });
  return out;
}

/**
 * The text that sends a coder task back to the main agent, or "" when its brief is complete. A TOML brief's problems
 * name the keys the TOML way, a JSON brief's the JSON way. o.format: the brief's format in the main agent's
 * instructions, named when the text has no brief at all (CARL's default: TOML; agent-bench's brief_json: JSON).
 * o.root: the project folder (checkBrief: the existing_tests paths are checked there).
 * @param {string} taskText @param {{ format?: "toml" | "json", root?: string, exists?: (p: string) => boolean }} [o]
 */
export function briefRefusal(taskText, o = {}) {
  const { brief, error, format } = parseBrief(taskText);
  if (!format) {
    return `${BRIEF_MARK}: the coder takes its task only as a ${o.format === "json" ? "JSON" : "TOML"} brief, so write the task in the brief's format from your instructions and send it again.`;
  }
  const name = format === "json" ? "JSON" : "TOML";
  if (!brief) {
    const form = format === "json" ? "" : " Each item of a list is its own block, for example:\n[[task_requirement]]\n" +
      'requirement_id = "R1"\nrequirement_text = "..."\n\n[[task_requirement]]\nrequirement_id = "R2"\nrequirement_text = "..."';
    return `${BRIEF_MARK}: the brief is not valid ${name} (${error}). Fix it and send the whole brief again.${form}`;
  }
  const problems = checkBrief(brief, format, { root: o.root, exists: o.exists });
  if (!problems.length) return "";
  return `${BRIEF_MARK}: the coder did not start. Fix these points and send the whole brief again:\n` +
    problems.map((p) => `- ${p}`).join("\n");
}

// ------------------------------------------------------------------ the chain's helpers

/** The brief's known files by file_action. @param {Brief} b */
export function filesByAction(b) {
  /** @type {{ create: string[], change: string[], read: string[] }} */
  const out = { create: [], change: [], read: [] };
  for (const f of b.files) if (f.path && f.action in out) out[/** @type {"create" | "change" | "read"} */ (f.action)].push(f.path);
  return out;
}

/** @param {Brief} b */
export const requirementIds = (b) => b.requirements.map((r) => r.id).filter(Boolean);
/** @param {Brief} b */
export const checkIds = (b) => b.checks.map((c) => c.id).filter(Boolean);

/**
 * When CARL's test session runs: Phase 23.4.6's Tests setting (before code, after code, off) plugs in here. Until
 * then "before", the default: the test session before the code session.
 * @returns {"before" | "after" | "off"}
 */
export function testsSetting() {
  return "before";
}

/**
 * Is a test session due before the code session? Only for work_mode code and work_type new_feature, with no
 * [failed_attempt] or [[tried_fix]], and not a task that continues an earlier one; never for the code session of a
 * chain (its brief has CARL's [test_session]); only while the Tests setting is "before".
 * @param {Brief} b @param {boolean} [continued]
 */
export function testSessionDue(b, continued = false) {
  return !continued && testsSetting() === "before" && b.workMode === "code" && b.workType === "new_feature" &&
    !b.failedAttempt && !b.triedFixes.length && !b.testSession;
}

/**
 * The brief of the test session in the chain, in work_mode tests-only: task_summary, expected_outcome,
 * current_state, exact_interfaces, the requirements, the checks, scope_limits, reference_doc, project_rule and
 * input_example. Without design_notes and known_file (the user: tests from the behaviour, not the implementation),
 * and without existing_tests, [failed_attempt] and [[tried_fix]]. work_type stays (the brief needs it).
 * @param {Brief} b @returns {Brief}
 */
export function testSessionBrief(b) {
  return {
    ...b, workMode: "tests-only", existingTests: [], designNotes: "", files: [],
    failedAttempt: null, triedFixes: [], testSession: null, unknown: [],
  };
}

/** A TOML string: "..." on one line, """...""" for text on several lines. @param {string} v */
function q(v) {
  if (!v.includes("\n")) return JSON.stringify(v);
  return '"""\n' + v.replace(/\\/g, "\\\\").replace(/"""/g, '""\\"') + '"""';
}

/** A TOML list of strings: on one line, or one item a line when there are several. @param {string[]} xs */
function strings(xs) {
  if (xs.length < 2) return `[${xs.map((x) => JSON.stringify(x)).join(", ")}]`;
  return "[\n" + xs.map((x) => `  ${JSON.stringify(x)},\n`).join("") + "]";
}

/**
 * A brief as TOML text (the template's order); parseBrief reads it back to the same brief.
 * @param {Brief} b @returns {string}
 */
export function writeBrief(b) {
  /** @type {string[]} */
  const out = [`work_mode = ${q(b.workMode)}`, `work_type = ${q(b.workType)}`];
  if (b.existingTests.length) out.push(`existing_tests = ${strings(b.existingTests)}`);
  out.push("", `task_summary = ${q(b.taskSummary)}`, `expected_outcome = ${q(b.expectedOutcome)}`);
  if (b.currentState) out.push(`current_state = ${q(b.currentState)}`);
  if (b.designNotes) out.push(`design_notes = ${q(b.designNotes)}`);
  if (b.exactInterfaces.length) out.push(`exact_interfaces = ${strings(b.exactInterfaces)}`);
  if (b.scopeLimits) out.push(`scope_limits = ${q(b.scopeLimits)}`);
  /** @param {string} name @param {Record<string, string | string[]>} t */
  const table = (name, t) => {
    out.push("", `[[${name}]]`);
    for (const [k, v] of Object.entries(t)) {
      if (Array.isArray(v)) out.push(`${k} = [${v.map((x) => JSON.stringify(x)).join(", ")}]`);
      else if (v) out.push(`${k} = ${q(v)}`);
    }
  };
  for (const d of b.referenceDocs) table("reference_doc", { doc_path: d.path, doc_purpose: d.purpose });
  for (const f of b.files) table("known_file", { file_path: f.path, file_action: f.action });
  for (const r of b.requirements) table("task_requirement", { requirement_id: r.id, requirement_text: r.text });
  for (const c of b.checks) {
    table("acceptance_check", { check_id: c.id, covers_requirements: c.covers, run_command: c.run, expected_result: c.expect });
  }
  for (const c of b.projectRules) table("project_rule", { rule_text: c.text, rule_source: c.source });
  for (const e of b.inputExamples) table("input_example", { example_source: e.source, example_text: e.text });
  const ts = b.testSession;
  if (ts) {
    out.push("", "[test_session]", `test_files = ${strings(ts.files)}`);
    if (ts.summary) out.push(`session_summary = ${q(ts.summary)}`);
    if (ts.notes.length) out.push(`session_notes = ${strings(ts.notes)}`);
    for (const f of ts.failing) {
      out.push("", "[[test_session.failing_test]]", `test_name = ${q(f.test)}`, `requirement_id = ${q(f.requirement)}`,
               `failure_reason = ${q(f.why)}`);
    }
  }
  if (b.failedAttempt) out.push("", "[failed_attempt]", `run_command = ${q(b.failedAttempt.run)}`, `error_output = ${q(b.failedAttempt.output)}`);
  for (const t of b.triedFixes) table("tried_fix", { fix_change: t.change, fix_result: t.result });
  return out.join("\n") + "\n";
}

/**
 * A brief as JSON with the same keys and structure as writeBrief's TOML (the same order; an empty text and an
 * empty list are left out, as there); parseBrief reads it back to the same brief. The chain writes the sessions'
 * briefs in the format the main agent used.
 * @param {Brief} b @returns {string}
 */
export function writeBriefJson(b) {
  /** @param {Record<string, string | string[]>} t */
  const keep = (t) => Object.fromEntries(Object.entries(t).filter(([, v]) => Array.isArray(v) || v));
  /** @type {Record<string, unknown>} */
  const o = { work_mode: b.workMode, work_type: b.workType };
  if (b.existingTests.length) o.existing_tests = b.existingTests;
  o.task_summary = b.taskSummary;
  o.expected_outcome = b.expectedOutcome;
  if (b.currentState) o.current_state = b.currentState;
  if (b.designNotes) o.design_notes = b.designNotes;
  if (b.exactInterfaces.length) o.exact_interfaces = b.exactInterfaces;
  if (b.scopeLimits) o.scope_limits = b.scopeLimits;
  /** @param {string} name @param {Record<string, string | string[]>[]} xs */
  const tables = (name, xs) => {
    if (xs.length) o[name] = xs.map(keep);
  };
  tables("reference_doc", b.referenceDocs.map((d) => ({ doc_path: d.path, doc_purpose: d.purpose })));
  tables("known_file", b.files.map((f) => ({ file_path: f.path, file_action: f.action })));
  tables("task_requirement", b.requirements.map((r) => ({ requirement_id: r.id, requirement_text: r.text })));
  tables("acceptance_check", b.checks.map((c) => ({ check_id: c.id, covers_requirements: c.covers, run_command: c.run,
                                                    expected_result: c.expect })));
  tables("project_rule", b.projectRules.map((c) => ({ rule_text: c.text, rule_source: c.source })));
  tables("input_example", b.inputExamples.map((e) => ({ example_source: e.source, example_text: e.text })));
  const ts = b.testSession;
  if (ts) {
    /** @type {Record<string, unknown>} */
    const t = { test_files: ts.files };
    if (ts.summary) t.session_summary = ts.summary;
    if (ts.notes.length) t.session_notes = ts.notes;
    if (ts.failing.length) {
      t.failing_test = ts.failing.map((f) => ({ test_name: f.test, requirement_id: f.requirement, failure_reason: f.why }));
    }
    o.test_session = t;
  }
  if (b.failedAttempt) o.failed_attempt = { run_command: b.failedAttempt.run, error_output: b.failedAttempt.output };
  tables("tried_fix", b.triedFixes.map((t) => ({ fix_change: t.change, fix_result: t.result })));
  return JSON.stringify(o, null, 2) + "\n";
}

/**
 * The files' SHA-256 hashes (null: the file is not there), for the test-file freeze: hash the test files after the
 * test session, again at the end, and compare with changedFiles.
 * @param {string[]} paths @param {string} [root] the project folder (for relative paths)
 * @returns {Record<string, string | null>}
 */
export function hashFiles(paths, root = process.cwd()) {
  /** @type {Record<string, string | null>} */
  const out = {};
  for (const p of paths) {
    try {
      out[p] = createHash("sha256").update(readFileSync(isAbsolute(p) ? p : resolve(root, p))).digest("hex");
    } catch {
      out[p] = null;
    }
  }
  return out;
}

/**
 * The files whose hash differs between two hashFiles results (changed, made or removed), sorted.
 * @param {Record<string, string | null>} before @param {Record<string, string | null>} after
 */
export function changedFiles(before, after) {
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  return [...keys].filter((k) => (before[k] ?? null) !== (after[k] ?? null)).sort();
}

// ================================================================== the coder's report

/**
 * The coder's report (client/agents/coder.md): task_status, outcome_summary, brief_deviations,
 * [[requirement_result]] (requirement_id, requirement_status, requirement_note), [[check_result]] (check_id,
 * run_result, run_summary), [[changed_file]] (file_path, change_summary), [[test_finding]] (tests-only: test_name,
 * requirement_id, failure_reason) and [[open_issue]] (issue_text).
 * @typedef {{
 *   status: string, outcomeSummary: string, deviations: string,
 *   requirements: { id: string, status: string, note: string }[],
 *   checks: { id: string, result: string, summary: string }[],
 *   files: { path: string, what: string }[],
 *   findings: { test: string, requirement: string, why: string }[],
 *   openIssues: string[]
 * }} Report
 */

/** A line that starts the report's TOML when it has no fence: one of its top-level keys. */
const REPORT_START = /^[ \t]*(?:outcome_summary|brief_deviations|task_status)[ \t]*=/m;

/**
 * The coder's report (its TOML block, or the same as JSON; prose and a "Needs a browser check" section around it
 * are left out), or null when its final message has no report that reads (or no task_status).
 * @param {string} message @returns {Report | null}
 */
export function parseReport(message) {
  const m = String(message ?? "").replace(/\r\n?/g, "\n");
  const fence = /^[ \t]*```[ \t]*(toml|json)[ \t]*\n([\s\S]*?)^[ \t]*```/im.exec(m);
  let src = fence ? fence[2] : "";
  let json = Boolean(fence && fence[1].toLowerCase() === "json");
  if (!src) {
    const k = m.search(REPORT_START);
    const js = findJsonObject(m, /"task_status"\s*:/);
    if (js && (k < 0 || js.start < k)) {
      src = js.json;
      json = true;
    } else if (k >= 0) src = m.slice(k);
    else return null;
  }
  /** @type {Record<string, unknown>} */
  let raw;
  try {
    raw = json ? parseJson(src) : parseToml(src, { stopAtProse: true });
  } catch {
    return null;
  }
  if (!str(raw.task_status)) return null;
  return {
    status: str(raw.task_status).toLowerCase(),
    outcomeSummary: str(raw.outcome_summary),
    deviations: str(raw.brief_deviations),
    requirements: list(raw.requirement_result).map((r) => ({ id: str(tab(r).requirement_id), status: str(tab(r).requirement_status),
                                                             note: str(tab(r).requirement_note) })),
    checks: list(raw.check_result).map((c) => ({ id: str(tab(c).check_id), result: str(tab(c).run_result).toLowerCase(),
                                                 summary: str(tab(c).run_summary) })),
    files: list(raw.changed_file).map((f) => ({ path: briefPath(tab(f).file_path), what: str(tab(f).change_summary) })),
    findings: list(raw.test_finding).map((f) => ({ test: str(tab(f).test_name), requirement: str(tab(f).requirement_id),
                                                   why: str(tab(f).failure_reason) })),
    openIssues: list(raw.open_issue).map((o) => (typeof o === "string" ? o.trim() : str(tab(o).issue_text))).filter(Boolean),
  };
}

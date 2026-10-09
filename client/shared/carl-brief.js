// @ts-check
// CARL: the coder's brief and the coder's report, as TOML (Phase 23.4.3; the schema is in
// docs/phase-plans/phase23.4.3/full_plan.md, item 2). Shared by both clients' carl-delegation (each carries a copy,
// installed by client/configure.py next to carl-delegation.js); the chain of a test session and a code session
// (item 3) uses it too. No dependency: the plugins ship without npm installs.
//   - parseToml: a small TOML reader for the subset the brief uses (strings of all four kinds, lists, inline tables,
//     [table], [[list of tables]], comments; also true / false and plain numbers). Errors name the line.
//   - parseBrief: the brief from a task text (a ```toml fence, or TOML with prose before or after it), in one fixed
//     shape (Brief); checkBrief: what is missing or wrong, as whole sentences that a small model can act on;
//     briefRefusal: the text that sends a task back to the main agent.
//   - the same brief as JSON (agent-bench's brief_json variant measures the format): parseJson, a small JSON reader
//     whose errors name the line as the TOML reader's do; parseBrief also takes a ```json fence, or a JSON object
//     with prose around it, and gives the same shape, so the check, the gates and the chain work the same; the
//     check's sentences then name the keys the JSON way ("mode": "code"); writeBriefJson writes it back.
//   - the chain's helpers: filesByAction, requirementIds, checkIds, testSessionDue, testSessionBrief, writeBrief;
//     isTestFile (what the gates call a test file), namesTests; hashFiles and changedFiles (the test-file freeze);
//     parseReport (the coder's TOML report, or the same as JSON). carl-chain.js runs the chain with them.
//   - [test_session]: CARL's own table in the code session's brief (the test files of the test session before it,
//     and that session's report in short); the main agent never writes it.
// Pure except hashFiles (it reads the files).

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";

export const BRIEF_MARK = "[CARL] Brief refused";
export const MODES = ["code", "test"];
export const TESTS = ["new", "existing", "none"];
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
      if (s[i] !== "=") fail("a key needs = and a value");
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
  if (s[i] !== "{") fail('the brief is not a JSON object: write it as { "mode": "code", ... }');
  const root = object();
  ws();
  if (i < s.length) fail("there is more text after the closing }: the brief is one JSON object");
  return root;
}

/** The brief's own keys at the start of a line: tells a TOML brief from other text. */
const BRIEF_KEYS = /^\s*(?:(?:mode|tests|goal)\s*=|\[\[?\s*(?:scope|file|requirement|check|constraint|example|error|tried)\s*\]\]?)/m;
/** A key of the brief as a JSON key (and the plural forms a model may write): tells a JSON brief from other text. */
const JSON_BRIEF_KEYS = /"(?:mode|tests|goal|scope|files?|requirements?|checks?|constraints?|examples?|error|tried)"\s*:/;

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
 * The TOML part of a task text: a ```toml fence (or a ``` fence with `mode =` in it), else the text from its first
 * TOML line on (prose after it is cut by the reader). Pi's subagent puts "Task: " before the text: it goes.
 * found: false when the text has no TOML in it at all.
 * @param {string} text @returns {{ toml: string, firstLine: number, found: boolean, fenced: boolean }}
 */
export function extractToml(text) {
  const t = String(text ?? "").replace(/\r\n?/g, "\n").replace(/^\s*Task:[ \t]*/, (m) => m.replace(/[^\n]/g, ""));
  const lineOf = (/** @type {number} */ p) => t.slice(0, p).split("\n").length;
  let fence = /^[ \t]*```[ \t]*toml[ \t]*\n([\s\S]*?)^[ \t]*```/im.exec(t);
  if (!fence) {
    for (const m of t.matchAll(/^[ \t]*```[^\n]*\n([\s\S]*?)^[ \t]*```/gm)) {
      if (/^\s*mode\s*=/m.test(m[1])) {
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
 * `mode =` (TOML) or a JSON object with the brief's keys in it; else, with no fence, TOML from its first line on (as
 * extractToml) or the first JSON object that starts a line and has the brief's keys, whichever comes first. Pi's
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
      if (/^\s*mode\s*=/m.test(body)) hit = { format: "toml", m };
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
 * @typedef {{ text: string, why: string }} ScopeItem
 * @typedef {{ path: string, action: string }} BriefFile
 * @typedef {{ id: string, text: string }} Requirement
 * @typedef {{ id: string, covers: string[], run: string, expect: string }} Check
 * @typedef {{
 *   mode: string, tests: string, goal: string,
 *   scope: { in: ScopeItem[], out: ScopeItem[] },
 *   files: BriefFile[], requirements: Requirement[], checks: Check[],
 *   constraints: { text: string, source: string }[], examples: { source: string, text: string }[],
 *   error: { run: string, output: string } | null, tried: { change: string, result: string }[],
 *   testSession: TestSession | null,
 *   unknown: string[]
 * }} Brief
 * @typedef {{ test: string, requirement: string, why: string }} Failing
 * @typedef {{ files: string[], summary: string, failing: Failing[], notes: string[] }} TestSession
 */

const TOP_KEYS = new Set(["mode", "tests", "goal", "scope", "file", "requirement", "check", "constraint", "example",
                          "error", "tried", "test_session"]);
/** Keys a model may write for the schema's own (named in the problem). */
const ALIASES = /** @type {Record<string, string>} */ ({
  files: "file", requirements: "requirement", checks: "check", acceptance: "check", constraints: "constraint",
  examples: "example", errors: "error", tries: "tried", scope_in: "scope.in", scope_out: "scope.out",
});

/** @param {unknown} v */
const str = (v) => (typeof v === "string" ? v.trim() : typeof v === "number" || typeof v === "boolean" ? String(v) : "");
/** Text kept as it was written (examples, error output): only the line ends around it go. @param {unknown} v */
const text = (v) => (typeof v === "string" ? v.replace(/^\n+|\s+$/g, "") : str(v));
/** @param {unknown} v @returns {unknown[]} */
const list = (v) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
/** @param {unknown} v @returns {Record<string, unknown>} */
const tab = (v) => (isTable(v) ? v : {});
/** @param {unknown} v @returns {ScopeItem} */
const scopeItem = (v) => (typeof v === "string" ? { text: v.trim(), why: "" } : { text: str(tab(v).text), why: str(tab(v).why) });
/** A path as the brief means it: relative to the project, without "./". @param {unknown} v */
const briefPath = (v) => str(v).replace(/\\/g, "/").replace(/^(\.\/)+/, "");

/**
 * The brief in one fixed shape. Lenient where a small model writes the same thing another way: a scope entry as
 * plain text, covers as one id, a single [file] for a list, "Code" for "code".
 * @param {Record<string, unknown>} raw @returns {Brief}
 */
export function normalizeBrief(raw) {
  const scope = tab(raw.scope);
  const unknown = Object.keys(raw).filter((k) => !TOP_KEYS.has(k));
  for (const k of Object.keys(scope)) if (k !== "in" && k !== "out") unknown.push(`scope.${k}`);
  const err = raw.error;
  return {
    mode: str(raw.mode).toLowerCase(),
    tests: str(raw.tests).toLowerCase(),
    goal: str(raw.goal),
    scope: { in: list(scope.in).map(scopeItem).filter((x) => x.text), out: list(scope.out).map(scopeItem).filter((x) => x.text) },
    files: list(raw.file).map((f) => ({ path: briefPath(tab(f).path), action: str(tab(f).action).toLowerCase() })),
    requirements: list(raw.requirement).map((r) => ({ id: str(tab(r).id), text: str(tab(r).text) })),
    checks: list(raw.check).map((c) => ({ id: str(tab(c).id), covers: list(tab(c).covers).map(str).filter(Boolean),
                                          run: str(tab(c).run), expect: str(tab(c).expect) })),
    constraints: list(raw.constraint).map((c) => ({ text: str(tab(c).text), source: str(tab(c).source) })),
    examples: list(raw.example).map((e) => ({ source: str(tab(e).source), text: text(tab(e).text) })),
    error: err === undefined ? null : typeof err === "string" ? { run: "", output: text(err) }
      : { run: str(tab(err).run), output: text(tab(err).output) },
    tried: list(raw.tried).map((t) => (typeof t === "string" ? { change: t.trim(), result: "" }
      : { change: str(tab(t).change), result: str(tab(t).result) })),
    testSession: raw.test_session === undefined ? null : testSession(tab(raw.test_session)),
    unknown,
  };
}

/** CARL's [test_session] table in one fixed shape. @param {Record<string, unknown>} t @returns {TestSession} */
function testSession(t) {
  return {
    files: list(t.files).map(briefPath).filter(Boolean),
    summary: text(t.summary),
    failing: list(t.failing).map((f) => (typeof f === "string" ? { test: f.trim(), requirement: "", why: "" }
      : { test: str(tab(f).test), requirement: str(tab(f).requirement), why: str(tab(f).why) })),
    notes: list(t.notes).map((n) => (typeof n === "string" ? n.trim() : str(tab(n).text))).filter(Boolean),
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
 *             under: (n: string) => string, addItem: (n: string) => string, outExample: string }} Syntax
 */
/** @type {Record<"toml" | "json", Syntax>} */
const SYNTAX = {
  toml: {
    kv: (k, v) => `${k} = ${v}`,
    table: (n) => `[${n}]`,
    item: (n) => `[[${n}]]`,
    under: (n) => `under [${n}]`,
    addItem: (n) => `add a [[${n}]]`,
    outExample: 'out = [{ text = "...", why = "..." }]',
  },
  json: {
    kv: (k, v) => `"${k}": ${v}`,
    table: (n) => `"${n}"`,
    item: (n) => `"${n}"`,
    under: (n) => `in "${n}"`,
    addItem: (n) => `add an item to "${n}"`,
    outExample: '"out": [{ "text": "...", "why": "..." }]',
  },
};

/**
 * What is missing or wrong in a brief, as whole sentences (empty: the brief is complete). The rules are the full
 * plan's (item 2), plus: keys that are not in the schema, the file actions, the ids a check covers, and in mode test
 * a file to write that is not a test file (the gate would refuse it). format: how the sentences write the keys (the
 * format the brief came in: parseBrief's format).
 * @param {Brief} b @param {"toml" | "json" | ""} [format] @returns {string[]}
 */
export function checkBrief(b, format = "toml") {
  const { kv, table, item, under, addItem, outExample } = SYNTAX[format === "json" ? "json" : "toml"];
  /** @type {string[]} */
  const out = [];
  for (const k of b.unknown) {
    const use = ALIASES[k];
    out.push(use ? `The brief has the key "${k}", which is not in the schema: use "${use}".`
      : `The brief has the key "${k}", which is not in the schema: remove it, or put its content in a key of the schema.`);
  }
  if (!b.mode) out.push(`mode is missing: write ${kv("mode", '"code"')} (write the program code) or ${kv("mode", '"test"')} (write the tests).`);
  else if (!MODES.includes(b.mode)) out.push(`${kv("mode", `"${b.mode}"`)} is not valid: write ${kv("mode", '"code"')} or ${kv("mode", '"test"')}.`);
  const code = b.mode === "code";
  if (code && !b.tests) out.push(`tests is missing: write ${kv("tests", '"new"')}, "existing" or "none" (the rule for tests is in your instructions).`);
  else if (b.tests && !TESTS.includes(b.tests)) out.push(`${kv("tests", `"${b.tests}"`)} is not valid: write ${kv("tests", '"new"')}, "existing" or "none".`);
  if (!b.goal) out.push("goal is empty: write the goal in one or two sentences.");
  if (code && !b.scope.out.length) {
    out.push(`scope.out is empty: add at least one thing to leave alone ${under("scope")}, for example ${outExample}.`);
  }

  b.files.forEach((f, n) => {
    const name = f.path || `${nth(n + 1)}`;
    if (!f.path) out.push(`File ${nth(n + 1)} has no path: give it ${kv("path", '"the file\'s path in the project"')}.`);
    if (!ACTIONS.includes(f.action)) {
      out.push(f.action ? `File ${name} has ${kv("action", `"${f.action}"`)}: use "create", "change" or "read".`
        : `File ${name} has no action: write ${kv("action", '"create"')}, "change" or "read".`);
    } else if (b.mode === "test" && f.action !== "read" && f.path && !isTestFile(f.path)) {
      out.push(`File ${f.path} has ${kv("action", `"${f.action}"`)}, but in mode test the coder writes test files only: set its action to "read", or remove it.`);
    }
  });
  if (code && !b.files.some((f) => f.action === "create" || f.action === "change")) {
    out.push(`The brief has no file to create or change: ${addItem("file")} with path and ${kv("action", '"create"')} or "change" for each file the coder may write.`);
  }

  if (!b.requirements.length) {
    out.push(`The brief has no requirement: ${addItem("requirement")} with ${kv("id", '"R1"')} and ${kv("text", '"..."')} for each thing the code must do.`);
  }
  const ids = new Set();
  b.requirements.forEach((r, n) => {
    if (!r.id) out.push(`Requirement ${nth(n + 1)} has no id: give it ${kv("id", `"R${n + 1}"`)}.`);
    else if (ids.has(r.id)) out.push(`The id "${r.id}" is on more than one requirement: give each requirement its own id.`);
    ids.add(r.id);
    if (!r.text) out.push(`Requirement ${r.id || nth(n + 1)} has no text: say in one point what it must do.`);
  });

  const checkIds = new Set();
  b.checks.forEach((c, n) => {
    const name = c.id || nth(n + 1);
    if (!c.id) out.push(`Check ${nth(n + 1)} has no id: give it ${kv("id", `"A${n + 1}"`)}.`);
    else if (checkIds.has(c.id)) out.push(`The id "${c.id}" is on more than one check: give each check its own id.`);
    checkIds.add(c.id);
    if (!c.run) out.push(`Check ${name} has no run: give the command that checks it, for example ${kv("run", '"python -m pytest tests/test_x.py"')}.`);
    for (const id of c.covers) {
      if (!ids.has(id)) out.push(`Check ${name} covers "${id}", but no requirement has that id: fix the id, or add the requirement.`);
    }
    if (!c.covers.length) out.push(`Check ${name} covers no requirement: write ${kv("covers", '["R1"]')} with the ids it checks.`);
  });
  if (b.requirements.length && !b.checks.length) {
    out.push(`The brief has no check: ${addItem("check")} with id, covers, run and expect, so that every requirement is in the covers of a check.`);
  } else {
    const covered = new Set(b.checks.flatMap((c) => c.covers));
    for (const r of b.requirements) {
      if (r.id && !covered.has(r.id)) out.push(`Requirement ${r.id} is in no check's covers: add it to a check, or add a check for it.`);
    }
  }
  if (b.tests === "existing" && !b.checks.some((c) => namesTests(c.run))) {
    out.push(`${kv("tests", '"existing"')}, but no check names the existing tests: put the test file or folder in a check's run, for example ${kv("run", '"python -m pytest tests/test_x.py"')}.`);
  }

  if (b.error) {
    if (!b.error.run) out.push(`${table("error")} has no run: give the command that fails.`);
    if (!b.error.output) out.push(`${table("error")} has no output: copy the exact output of that command.`);
  }
  if (b.tried.length && !b.error) {
    out.push(`${item("tried")} is only for a fix that failed: add the ${table("error")} with run and output, or remove ${item("tried")}.`);
  }
  b.tried.forEach((t, n) => {
    if (!t.change) out.push(`Tried ${nth(n + 1)} has no change: say what was changed.`);
    if (!t.result) out.push(`Tried ${nth(n + 1)} has no result: say what happened.`);
  });
  return out;
}

/**
 * The text that sends a coder task back to the main agent, or "" when its brief is complete. A TOML brief's problems
 * name the keys the TOML way, a JSON brief's the JSON way. o.format: the brief's format in the main agent's
 * instructions, named when the text has no brief at all (CARL's default: TOML; agent-bench's brief_json: JSON).
 * @param {string} taskText @param {{ format?: "toml" | "json" }} [o]
 */
export function briefRefusal(taskText, o = {}) {
  const { brief, error, format } = parseBrief(taskText);
  if (!format) {
    return `${BRIEF_MARK}: the coder takes its task only as a ${o.format === "json" ? "JSON" : "TOML"} brief, so write the task in the brief's format from your instructions and send it again.`;
  }
  const name = format === "json" ? "JSON" : "TOML";
  if (!brief) return `${BRIEF_MARK}: the brief is not valid ${name} (${error}). Fix it and send the whole brief again.`;
  const problems = checkBrief(brief, format);
  if (!problems.length) return "";
  return `${BRIEF_MARK}: the coder did not start. Fix these points and send the whole brief again:\n` +
    problems.map((p) => `- ${p}`).join("\n");
}

// ------------------------------------------------------------------ the chain's helpers

/** The brief's files by action. @param {Brief} b */
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
 * Is a test session due before the code session? Only for mode code with tests = "new", no [error] or [[tried]],
 * and not a task that continues an earlier one (full plan, item 2); never for the code session of a chain (its brief
 * has CARL's [test_session]).
 * @param {Brief} b @param {boolean} [continued]
 */
export function testSessionDue(b, continued = false) {
  return !continued && b.mode === "code" && b.tests === "new" && !b.error && !b.tried.length && !b.testSession;
}

/**
 * The brief of the test session in the chain: the goal, the scope, the requirements, the checks, the constraints and
 * the examples, in mode test; of the files only the test files to create or change (none: any test file).
 * @param {Brief} b @returns {Brief}
 */
export function testSessionBrief(b) {
  return {
    ...b, mode: "test", tests: "",
    files: b.files.filter((f) => f.action !== "read" && isTestFile(f.path)),
    error: null, tried: [], testSession: null, unknown: [],
  };
}

/** A TOML string: "..." on one line, """...""" for text on several lines. @param {string} v */
function q(v) {
  if (!v.includes("\n")) return JSON.stringify(v);
  return '"""\n' + v.replace(/\\/g, "\\\\").replace(/"""/g, '""\\"') + '"""';
}

/**
 * A brief as TOML text (the schema's order); parseBrief reads it back to the same brief.
 * @param {Brief} b @returns {string}
 */
export function writeBrief(b) {
  /** @type {string[]} */
  const out = [`mode = ${q(b.mode)}`];
  if (b.tests) out.push(`tests = ${q(b.tests)}`);
  out.push(`goal = ${q(b.goal)}`);
  const items = (/** @type {ScopeItem[]} */ xs) => (xs.length ? "[\n" + xs.map((x) =>
    `  { text = ${JSON.stringify(x.text)}${x.why ? `, why = ${JSON.stringify(x.why)}` : ""} },\n`).join("") + "]" : "[]");
  out.push("", "[scope]", `in = ${items(b.scope.in)}`, `out = ${items(b.scope.out)}`);
  /** @param {string} name @param {Record<string, string | string[]>} t */
  const table = (name, t) => {
    out.push("", `[[${name}]]`);
    for (const [k, v] of Object.entries(t)) {
      if (Array.isArray(v)) out.push(`${k} = [${v.map((x) => JSON.stringify(x)).join(", ")}]`);
      else if (v) out.push(`${k} = ${q(v)}`);
    }
  };
  for (const f of b.files) table("file", { path: f.path, action: f.action });
  for (const r of b.requirements) table("requirement", { id: r.id, text: r.text });
  for (const c of b.checks) table("check", { id: c.id, covers: c.covers, run: c.run, expect: c.expect });
  for (const c of b.constraints) table("constraint", { text: c.text, source: c.source });
  for (const e of b.examples) table("example", { source: e.source, text: e.text });
  const ts = b.testSession;
  if (ts) {
    out.push("", "[test_session]", `files = [${ts.files.map((x) => JSON.stringify(x)).join(", ")}]`);
    if (ts.summary) out.push(`summary = ${q(ts.summary)}`);
    if (ts.failing.length) {
      out.push("failing = [", ...ts.failing.map((f) => `  { test = ${JSON.stringify(f.test)}, requirement = ${JSON.stringify(f.requirement)}, why = ${JSON.stringify(f.why)} },`), "]");
    }
    if (ts.notes.length) out.push("notes = [", ...ts.notes.map((n) => `  ${JSON.stringify(n)},`), "]");
  }
  if (b.error) out.push("", "[error]", `run = ${q(b.error.run)}`, `output = ${q(b.error.output)}`);
  for (const t of b.tried) table("tried", { change: t.change, result: t.result });
  return out.join("\n") + "\n";
}

/**
 * A brief as JSON with the same keys and structure as writeBrief's TOML (the same order; an empty text and an
 * empty list of tables are left out, as there); parseBrief reads it back to the same brief. The chain writes the
 * code session's brief in the format the main agent used.
 * @param {Brief} b @returns {string}
 */
export function writeBriefJson(b) {
  /** @param {Record<string, string | string[]>} t */
  const keep = (t) => Object.fromEntries(Object.entries(t).filter(([, v]) => Array.isArray(v) || v));
  const scopeItem = (/** @type {ScopeItem} */ x) => keep({ text: x.text, why: x.why });
  /** @type {Record<string, unknown>} */
  const o = { mode: b.mode };
  if (b.tests) o.tests = b.tests;
  o.goal = b.goal;
  o.scope = { in: b.scope.in.map(scopeItem), out: b.scope.out.map(scopeItem) };
  /** @param {string} name @param {Record<string, string | string[]>[]} xs */
  const tables = (name, xs) => {
    if (xs.length) o[name] = xs.map(keep);
  };
  tables("file", b.files.map((f) => ({ path: f.path, action: f.action })));
  tables("requirement", b.requirements.map((r) => ({ id: r.id, text: r.text })));
  tables("check", b.checks.map((c) => ({ id: c.id, covers: c.covers, run: c.run, expect: c.expect })));
  tables("constraint", b.constraints.map((c) => ({ text: c.text, source: c.source })));
  tables("example", b.examples.map((e) => ({ source: e.source, text: e.text })));
  const ts = b.testSession;
  if (ts) {
    /** @type {Record<string, unknown>} */
    const t = { files: ts.files };
    if (ts.summary) t.summary = ts.summary;
    if (ts.failing.length) t.failing = ts.failing.map((f) => ({ test: f.test, requirement: f.requirement, why: f.why }));
    if (ts.notes.length) t.notes = ts.notes;
    o.test_session = t;
  }
  if (b.error) o.error = { run: b.error.run, output: b.error.output };
  tables("tried", b.tried.map((t) => ({ change: t.change, result: t.result })));
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
 * @typedef {{
 *   status: string, mode: string, summary: string, rootCause: string,
 *   requirements: { id: string, status: string, note: string }[],
 *   checks: { id: string, result: string, summary: string }[],
 *   files: { path: string, what: string }[],
 *   findings: { test: string, requirement: string, why: string }[],
 *   openIssues: string[]
 * }} Report
 */

/**
 * The coder's report (its TOML block, or the same as JSON; prose and a "Needs a browser check" section around it
 * are left out), or null when its final message has no report that reads.
 * @param {string} message @returns {Report | null}
 */
export function parseReport(message) {
  const m = String(message ?? "").replace(/\r\n?/g, "\n");
  const fence = /^[ \t]*```[ \t]*(toml|json)[ \t]*\n([\s\S]*?)^[ \t]*```/im.exec(m);
  let src = fence ? fence[2] : "";
  let json = Boolean(fence && fence[1].toLowerCase() === "json");
  if (!src) {
    const k = m.search(/^[ \t]*status[ \t]*=/m);
    const js = findJsonObject(m, /"status"\s*:/);
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
  if (!str(raw.status)) return null;
  return {
    status: str(raw.status).toLowerCase(),
    mode: str(raw.mode).toLowerCase(),
    summary: str(raw.summary),
    rootCause: str(raw.root_cause),
    requirements: list(raw.requirement).map((r) => ({ id: str(tab(r).id), status: str(tab(r).status), note: str(tab(r).note) })),
    checks: list(raw.check).map((c) => ({ id: str(tab(c).id), result: str(tab(c).result).toLowerCase(), summary: str(tab(c).summary) })),
    files: list(raw.file).map((f) => ({ path: briefPath(tab(f).path), what: str(tab(f).what) })),
    findings: list(raw.finding).map((f) => ({ test: str(tab(f).test), requirement: str(tab(f).requirement), why: str(tab(f).why) })),
    openIssues: list(raw.open_issue).map((o) => (typeof o === "string" ? o.trim() : str(tab(o).text))).filter(Boolean),
  };
}

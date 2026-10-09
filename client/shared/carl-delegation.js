// @ts-check
// CARL's delegation helpers, shared by the OpenCode plugin carl-delegation and the Pi extension carl-delegation (each
// carries a copy, installed by client/configure.py). Pure, no I/O except the `exists` callback and GateSetting (it
// reads the dashboard's setting). Phase 23 (1.8.0):
//   - the delegation rule is for a session's main agent only: OpenCode gives its instructions to every agent, so the
//     rule sits between RULE_BEGIN and RULE_END and withoutRule() takes it out of a subagent's system prompt;
//   - the reminder (on by default): every user message of a main session ends with the same line about the coder;
//     the same text each time, so the history and the prompt cache stay whole (measured: it is what moves large and
//     stuck requests to the coder; reference/delegation.md);
//   - the gate (off by default; a setting deep in CARL, not recommended): the main agent's write that makes the Nth
//     new file of a turn is stopped with GATE_MARK and the reason, so it hands the work to the coder; edits of
//     existing files pass. Phase 23.4 (1.12.0): the gate is a setting of the dashboard only (config.json
//     delegation.gate, Connect > Setup); GateSetting reads it, so a change needs no setup run.
// Phase 23.4.3: the coder's brief is TOML (carl-brief.js, installed next to this file):
//   - the brief check: a call that hands a task to the coder with a brief that fails checkBrief (or no TOML brief
//     at all) is refused before the coder starts (briefCheck), so the main agent fixes it and sends it again; a
//     task that continues an earlier one (OpenCode's task_id) is not checked;
//   - the gates in the coder's own session (CoderGate): it writes only the brief's files to create or change; in
//     mode code no test file, in mode test only test files.

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { briefRefusal, filesByAction, isTestFile } from "./carl-brief.js";

export const RULE_BEGIN = "<!-- carl:main-agents-only begin -->";
export const RULE_END = "<!-- carl:main-agents-only end -->";
export const GATE_MARK = "[CARL] Blocked";
const CODERS = new Set(["coder", "carl-coder"]);         // CARL's coder: "carl-coder" next to a user's own "coder"
const WRITES = new Set(["write", "edit", "patch", "apply_patch", "multiedit"]);
const MAKERS = new Set(["tee", "touch", "mkdir"]);           // shell commands whose arguments are new files
const COPIERS = new Set(["cp", "mv", "install"]);            // ... whose last argument is

/** The text without CARL's main-agent rule (every copy of it). @param {string} text */
export function withoutRule(text) {
  let out = text;
  for (;;) {
    const i = out.indexOf(RULE_BEGIN);
    if (i < 0) return out;
    const j = out.indexOf(RULE_END, i);
    if (j < 0) return out.slice(0, i).trimEnd();              // a cut rule: drop the rest, never keep half of it
    out = (out.slice(0, i).trimEnd() + "\n\n" + out.slice(j + RULE_END.length).trimStart()).trim();
  }
}

/** The gate's number (0: off). @param {unknown} n */
export function gateNumber(n) {
  const v = Number(n);
  return Number.isInteger(v) && v >= 1 && v <= 99 ? v : 0;
}

const GATE_MS = 10_000;                                       // the dashboard's setting, read again after this

/**
 * The new-file gate from the dashboard: config.json "delegation".gate on the Mac that runs the server; on another
 * computer, the dashboard API's settings (GET /carl/cache/settings, with the key of ~/.config/carl/api-key). 0 (off)
 * when neither answers. Read again every GATE_MS.
 */
export class GateSetting {
  /** @param {{ home?: string, cacheApi?: string, fetch?: typeof fetch }} [o] */
  constructor(o = {}) {
    this.home = o.home ?? homedir();
    this.cacheApi = o.cacheApi ?? "";
    this.fetch = o.fetch ?? globalThis.fetch;
    this.value = 0;
    this.at = -GATE_MS;
  }

  /** The gate's number now (0: off). @returns {Promise<number>} */
  async get() {
    if (Date.now() - this.at < GATE_MS) return this.value;
    this.at = Date.now();
    const conf = join(this.home, ".config", "carl");
    try {                                                       // this Mac runs the server: its config.json
      const c = JSON.parse(readFileSync(join(conf, "config.json"), "utf8"));
      this.value = gateNumber(c?.delegation?.gate);
      return this.value;
    } catch { /* no config.json here: ask the dashboard */ }
    if (!this.cacheApi) return (this.value = 0);
    try {
      const key = readFileSync(join(conf, "api-key"), "utf8").trim();
      const r = await this.fetch(this.cacheApi + "/carl/cache/settings", {
        headers: key ? { Authorization: `Bearer ${key}` } : {}, signal: AbortSignal.timeout(3_000) });
      if (r.ok) this.value = gateNumber((await r.json())?.gate);
    } catch { /* the dashboard does not answer: keep the last value */ }
    return this.value;
  }
}

/** How the main agent calls the coder in this client. coder: its installed name ("coder", or "carl-coder" next to
 * a user's own "coder"). @param {"opencode" | "pi"} client @param {string} coder */
function how(client, coder) {
  return client === "opencode" ? `the task tool with subagent_type "${coder}"` : `the subagent tool with agent "${coder}"`;
}

/** @param {"opencode" | "pi"} client @param {string} path @param {string} [coder] */
export function gateText(client, path, coder = "coder") {
  return `${GATE_MARK}: ${path} is a new file, and new files mean a larger task than you should write yourself. ` +
    `Hand the whole task to ${coder} (${how(client, coder)}) with the full requirements and how to check them. ` +
    `To change an existing file yourself, edit that file.`;
}

/** The line at the end of each user message (the same text each time). @param {"opencode" | "pi"} client
 * @param {string} [coder] */
export function reminderText(client, coder = "coder") {
  return `\n\n[CARL reminder] Large coding work or a fix that already failed goes to ${coder} (${how(client, coder)}); ` +
    `questions and one small edit you do yourself.`;
}

/** A user message's text with the reminder at its end (once). @param {string} text @param {"opencode" | "pi"} client
 * @param {string} [coder] */
export function withReminder(text, client, coder = "coder") {
  const r = reminderText(client, coder);
  return text.endsWith(r) ? text : text + r;
}

/** Is this agent name CARL's coder? @param {string} name */
export function isCoderName(name) {
  return CODERS.has(String(name ?? ""));
}

/** Is this call the coder? @param {string} tool @param {Record<string, unknown>} args */
export function isCoderCall(tool, args) {
  if (tool === "task") return CODERS.has(String(args.subagent_type ?? ""));
  if (tool === "subagent") {
    const one = String(args.agent ?? "");
    const many = Array.isArray(args.tasks) ? args.tasks : Array.isArray(args.chain) ? args.chain : [];
    return CODERS.has(one) || many.some((t) => CODERS.has(String(/** @type {any} */ (t)?.agent ?? "")));
  }
  return false;
}

/**
 * The files a write call would create (none for an edit of an existing file).
 * @param {string} tool @param {Record<string, unknown>} args @param {string} cwd
 * @param {(p: string) => boolean} exists
 * @returns {string[]}
 */
export function newFiles(tool, args, cwd, exists) {
  const abs = (/** @type {string} */ p) => (isAbsolute(p) ? p : resolve(cwd, p));
  if (tool === "write") {
    const p = String(args.filePath ?? args.path ?? "");
    return p && !exists(abs(p)) ? [p] : [];
  }
  if (tool === "bash") return bashNewFiles(String(args.command ?? ""), cwd, exists, abs);
  if (tool === "patch" || tool === "apply_patch") {
    const text = String(args.patchText ?? args.patch ?? args.input ?? "");
    return [...text.matchAll(/^\*\*\* Add File: (.+)$/gm)].map((m) => m[1].trim()).filter((p) => !exists(abs(p)));
  }
  return [];
}

/** A shell path that is plainly a file of the project (not ~, a variable, a device). @param {string} p */
function plainPath(p) {
  const u = p.replace(/^(['"])(.*)\1$/, "$2");
  return u && !/^[~$&]|^\/dev\/|[`$]/.test(u) ? u : "";
}

/**
 * The files a shell command writes: redirections (> >> &> >|), tee / touch arguments, the target of cp / mv /
 * install, the files of sed -i; with dirs, mkdir arguments too. Here-document bodies are data, not shell. Only
 * plain paths (not ~, a variable, a device).
 * @param {string} command @param {boolean} dirs
 * @returns {string[]}
 */
export function shellWrites(command, dirs) {
  const lines = command.split("\n");
  const kept = [];
  for (let i = 0; i < lines.length; i++) {
    kept.push(lines[i]);
    const m = lines[i].match(/<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/);
    if (m) while (i + 1 < lines.length && lines[i + 1].trim() !== m[2]) i++;
    if (m) i++;
  }
  const text = kept.join(" ; ");
  /** @type {string[]} */
  const out = [];
  const add = (/** @type {string} */ p) => {
    const q = plainPath(p);
    if (q && !out.includes(q)) out.push(q);
  };
  for (const m of text.matchAll(/(?:^|[^<>&0-9])(?:&>>?|>>?|>\|)\s*("[^"]+"|'[^']+'|[^\s;|&()<>]+)/g)) add(m[1]);
  for (const seg of text.split(/[;|&()]+/)) {
    const words = seg.trim().split(/\s+/).filter((w) => w && !/^[<>]/.test(w));
    const cmd = (words[0] ?? "").split("/").pop() ?? "";
    const plain = words.slice(1).filter((w) => !w.startsWith("-"));
    if (MAKERS.has(cmd) && (dirs || cmd !== "mkdir")) plain.forEach(add);
    if (COPIERS.has(cmd) && plain.length >= 2) add(plain[plain.length - 1]);
    if (cmd === "sed" && words.slice(1).some((w) => /^-[A-Za-z]*i/.test(w) || w.startsWith("--in-place"))) {
      // the files: the plain words without quotes (a script in quotes can hold spaces), less the script itself
      const args = words.slice(1).filter((w, k, a) => !w.startsWith("-") && !/^-[ef]$/.test(a[k - 1] ?? "") && !/['"]/.test(w));
      const quoted = words.slice(1).some((w) => /['"]/.test(w));
      const script = quoted || words.slice(1).some((w) => /^-[ef]$/.test(w)) ? 0 : 1;
      args.slice(script).forEach(add);
    }
  }
  return out;
}

/**
 * The files a shell command would create: shellWrites (with mkdir) that do not exist yet.
 * @param {string} command @param {string} cwd @param {(p: string) => boolean} exists @param {(p: string) => string} abs
 * @returns {string[]}
 */
export function bashNewFiles(command, cwd, exists, abs) {
  void cwd;
  return shellWrites(command, true).filter((p) => !exists(abs(p)));
}

/**
 * The files a call writes (as the call names them): the file tools' path, a patch's files, a shell command's
 * shellWrites (no mkdir: a folder is not a file of the brief).
 * @param {string} tool @param {Record<string, unknown>} args
 * @returns {string[]}
 */
export function writtenFiles(tool, args) {
  if (tool === "write" || tool === "edit" || tool === "multiedit") {
    const p = String(args.filePath ?? args.path ?? "");
    return p ? [p] : [];
  }
  if (tool === "patch" || tool === "apply_patch") {
    const text = String(args.patchText ?? args.patch ?? args.input ?? "");
    return [...text.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$/gm)].map((m) => (m[1] ?? m[2]).trim());
  }
  if (tool === "bash") return shellWrites(String(args.command ?? ""), false);
  return [];
}

/**
 * The coder tasks in a call (their texts): OpenCode's task tool for the coder (not one that continues an earlier
 * task: task_id), Pi's subagent tool for the coder (single, or each coder item of tasks and chain; a chain's
 * {previous} stands for the output of the step before).
 * @param {string} tool @param {Record<string, unknown>} args @returns {string[]}
 */
export function coderTasks(tool, args) {
  if (tool === "task") return CODERS.has(String(args.subagent_type ?? "")) && !args.task_id ? [String(args.prompt ?? "")] : [];
  if (tool !== "subagent") return [];
  const out = CODERS.has(String(args.agent ?? "")) ? [String(args.task ?? "")] : [];
  for (const t of [...(Array.isArray(args.tasks) ? args.tasks : []), ...(Array.isArray(args.chain) ? args.chain : [])]) {
    const item = /** @type {Record<string, unknown>} */ (t ?? {});
    if (CODERS.has(String(item.agent ?? ""))) out.push(String(item.task ?? "").replace(/\{previous\}/g, "the output of the step before"));
  }
  return out;
}

/**
 * The brief check before a call starts the coder: the refusal (BRIEF_MARK and what to fix), or "" to let it run.
 * o.format: the brief's format in the main agent's instructions (briefRefusal).
 * @param {string} tool @param {Record<string, unknown>} args @param {{ format?: "toml" | "json" }} [o]
 */
export function briefCheck(tool, args, o = {}) {
  const tasks = coderTasks(tool, args);
  for (const [n, text] of tasks.entries()) {
    const why = briefRefusal(text, o);
    if (why) return tasks.length > 1 ? why.replace(": ", `: coder task ${n + 1}: `) : why;
  }
  return "";
}

/**
 * The gates in the coder's own session, from its brief: it writes only the files whose action is create or change;
 * in mode code no test file, in mode test only test files (isTestFile). In mode test with no test file in the
 * brief, any test file may be written. Paths outside the project folder are not the brief's: they pass.
 */
export class CoderGate {
  /** @param {import("./carl-brief.js").Brief} brief @param {string} cwd the project folder */
  constructor(brief, cwd) {
    this.cwd = cwd;
    this.mode = brief.mode === "test" ? "test" : "code";
    const f = filesByAction(brief);
    this.write = new Set([...f.create, ...f.change].map((p) => this.rel(p)));
    this.read = new Set(f.read.map((p) => this.rel(p)));
    this.anyTest = this.mode === "test" && ![...this.write].some((p) => isTestFile(p));
  }

  /** A path relative to the project folder, with "/". @param {string} p */
  rel(p) {
    return relative(this.cwd, isAbsolute(p) ? p : resolve(this.cwd, p)).split(sep).join("/");
  }

  /** Before a call of the coder: the block reason, or "" to let it run. @param {string} tool
   * @param {Record<string, unknown>} args */
  before(tool, args) {
    for (const p of writtenFiles(tool, args)) {
      const why = this.path(p);
      if (why) return why;
    }
    return "";
  }

  /** The block reason for a write of one path, or "". @param {string} p */
  path(p) {
    const r = this.rel(p);
    if (!r || r === ".." || r.startsWith("../") || isAbsolute(r)) return "";
    const test = isTestFile(r);
    if (this.mode === "code" && test) {
      return `${GATE_MARK}: ${r} is a test file, and in mode code you do not change tests; if a test looks wrong, write why under open issues in your report.`;
    }
    if (this.mode === "test" && !test) {
      return `${GATE_MARK}: ${r} is not a test file, and in mode test you write tests only; a test that fails because the code is wrong is a finding in your report.`;
    }
    if (this.write.has(r) || (this.anyTest && test)) return "";
    if (this.read.has(r)) return `${GATE_MARK}: ${r} is in your brief to read only; write the change it needs under open issues in your report.`;
    return `${GATE_MARK}: ${r} is not in your brief's files to create or change; keep to those files, and write what ${r} needs under open issues in your report.`;
  }
}

/** One main session's turn, for the gate: the new files the main agent made since the user's last message. */
export class Turn {
  constructor() {
    this.coder = false;                                       // the coder was called: the gate stands down
    this.created = 0;
  }

  reset() {
    this.coder = false;
    this.created = 0;
  }

  /** Before a call: the gate's block reason, or "" to let it run. gate: the number (0: off).
   * @param {number} gate @param {"opencode" | "pi"} client @param {string} tool
   * @param {Record<string, unknown>} args @param {string} cwd @param {(p: string) => boolean} exists
   * @param {string} [coder] */
  before(gate, client, tool, args, cwd, exists, coder = "coder") {
    if (isCoderCall(tool, args)) {
      this.coder = true;
      return "";
    }
    if (!gate || this.coder || !(WRITES.has(tool) || tool === "bash")) return "";
    const made = newFiles(tool, args, cwd, exists);
    if (!made.length) return "";
    if (this.created + made.length >= gate) return gateText(client, made[0], coder);
    this.created += made.length;
    return "";
  }
}

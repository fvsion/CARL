// @ts-check
// CARL's delegation helpers, shared by the OpenCode plugin carl-delegation and the Pi extension carl-delegation (each
// carries a copy, installed by client/configure.py). Pure, no I/O except the `exists` callback. Phase 23 (1.8.0):
//   - the delegation rule is for a session's main agent only: OpenCode gives its instructions to every agent, so the
//     rule sits between RULE_BEGIN and RULE_END and withoutRule() takes it out of a subagent's system prompt;
//   - the reminder (on by default): every user message of a main session ends with the same line about the coder;
//     the same text each time, so the history and the prompt cache stay whole (measured: it is what moves large and
//     stuck requests to the coder; reference/delegation.md);
//   - the gate (off by default; a setting deep in CARL, not recommended): the main agent's write that makes the Nth
//     new file of a turn is stopped with GATE_MARK and the reason, so it hands the work to the coder; edits of
//     existing files pass.

import { isAbsolute, resolve } from "node:path";

export const RULE_BEGIN = "<!-- carl:main-agents-only begin -->";
export const RULE_END = "<!-- carl:main-agents-only end -->";
export const GATE_MARK = "[CARL] Blocked";
const CODERS = new Set(["coder", "carl-coder"]);
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
 * The files a shell command would create: redirections (> >> &> >|), tee / touch / mkdir arguments, the target of
 * cp / mv / install; only those that do not exist yet. Here-document bodies are data, not shell.
 * @param {string} command @param {string} cwd @param {(p: string) => boolean} exists @param {(p: string) => string} abs
 * @returns {string[]}
 */
export function bashNewFiles(command, cwd, exists, abs) {
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
    if (q && !exists(abs(q)) && !out.includes(q)) out.push(q);
  };
  for (const m of text.matchAll(/(?:^|[^<>&0-9])(?:&>>?|>>?|>\|)\s*("[^"]+"|'[^']+'|[^\s;|&()<>]+)/g)) add(m[1]);
  for (const seg of text.split(/[;|&()]+/)) {
    const words = seg.trim().split(/\s+/).filter((w) => w && !/^[<>]/.test(w));
    const cmd = (words[0] ?? "").split("/").pop() ?? "";
    const plain = words.slice(1).filter((w) => !w.startsWith("-"));
    if (MAKERS.has(cmd)) plain.forEach(add);
    if (COPIERS.has(cmd) && plain.length >= 2) add(plain[plain.length - 1]);
  }
  void cwd;
  return out;
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

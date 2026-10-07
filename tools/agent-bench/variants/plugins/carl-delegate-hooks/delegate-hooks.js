// @ts-check
// CARL (Phase 23 variants V4 / V5): hooks that push the main agent to hand large work to the coder BEFORE it decides.
// Shared by the OpenCode plugin (opencode.js) and the Pi extension (pi.ts); pure, no I/O except `exists`.
//   nudge (V4): the result of the main agent's 2nd look (read, search, list) in a turn, while it has not decided
//               yet, gets one line: a large request or a fix that already failed goes to the coder now.
//   gate  (V5): the main agent's write that makes the Nth NEW file of a turn is stopped with that message
//               (GATE_MARK; "gate" = from the 1st, "gate:2" = the first new file passes); edits of existing files
//               pass, so small fixes stay with the main agent. Off by default in CARL (the user, 2026-10-06).
//   remind (V2, rough, for the v2 carl-hooks design only): every user message ends with the same reminder line
//               (the same text each time, so the history and the prompt cache stay stable).
// A turn starts with each user message; it is decided once the coder is called or a write goes through.

import { isAbsolute, resolve } from "node:path";

export const GATE_MARK = "[CARL] Blocked";
export const NUDGE_AT = 2;                                   // the look whose result gets the nudge
const CODERS = new Set(["coder", "carl-coder"]);
const LOOKS = new Set(["read", "grep", "glob", "list", "ls", "find", "bash", "codesearch"]);
const WRITES = new Set(["write", "edit", "patch", "apply_patch", "multiedit"]);
const MAKERS = new Set(["tee", "touch", "mkdir"]);           // shell commands whose arguments are new files
const COPIERS = new Set(["cp", "mv", "install"]);            // ... whose last argument is

/**
 * Is this hook on? mode: one of nudge, gate, remind, or several joined with commas ("remind,gate"); "both" is
 * nudge and gate (the first form). @param {string} mode @param {"nudge" | "gate" | "remind"} what
 */
export function on(mode, what) {
  const parts = mode.split(",").map((m) => m.trim().split(":")[0]);
  return parts.includes(what) || (parts.includes("both") && (what === "nudge" || what === "gate"));
}

/** The gate's number: it blocks the write that makes the Nth new file of a turn ("gate:2": the first new file
 * passes). 1 when not given. @param {string} mode */
export function gateFrom(mode) {
  const part = mode.split(",").map((m) => m.trim()).find((m) => m === "gate" || m.startsWith("gate:"));
  const n = part && part.includes(":") ? Number(part.split(":")[1]) : 1;
  return Number.isInteger(n) && n >= 1 ? n : 1;
}

/** @param {"opencode" | "pi"} client */
function how(client) {
  return client === "opencode" ? 'the task tool with subagent_type "coder"' : 'the subagent tool with agent "coder"';
}

/** @param {"opencode" | "pi"} client */
export function nudgeText(client) {
  return `\n\n[CARL] Before you read on: if this request is large (several files, a new module, package or CLI, ` +
    `code plus tests, a feature or a refactor) or a fix that already failed, hand the whole task to the coder now ` +
    `(${how(client)}) with the full requirements. A question or one small edit: go on yourself.`;
}

/** @param {"opencode" | "pi"} client @param {string} path */
export function gateText(client, path) {
  return `${GATE_MARK}: ${path} is a new file, and new files mean a larger task than you should write yourself. ` +
    `Hand the whole task to the coder (${how(client)}) with the full requirements and how to check them. ` +
    `To change an existing file yourself, edit that file.`;
}

/** @param {"opencode" | "pi"} client */
export function reminderText(client) {
  return `\n\n[CARL reminder] Large coding work or a fix that already failed goes to the coder (${how(client)}); ` +
    `questions and one small edit you do yourself.`;
}

/** A user message's text with the reminder at its end (once). @param {string} text @param {"opencode" | "pi"} client */
export function withReminder(text, client) {
  const r = reminderText(client);
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

/** One session's turn. */
export class Turn {
  constructor() {
    this.looks = 0;
    this.decided = false;                                     // the coder was called, or a write went through
    this.coder = false;                                       // the coder was called: no hook acts any more
    this.created = 0;                                         // new files the main agent made in this turn
    this.nudged = false;
  }

  reset() {
    this.looks = 0;
    this.decided = false;
    this.coder = false;
    this.created = 0;
    this.nudged = false;
  }

  /** The gate for a call that makes these new files: the block reason, or "" (then they count).
   * @param {string} mode @param {"opencode" | "pi"} client @param {string[]} made */
  gate(mode, client, made) {
    if (!made.length || !on(mode, "gate")) return "";
    if (this.created + made.length >= gateFrom(mode)) return gateText(client, made[0]);
    this.created += made.length;
    return "";
  }

  /** Before a call: the gate's block reason, or "" to let it run.
   * @param {string} mode @param {"opencode" | "pi"} client @param {string} tool
   * @param {Record<string, unknown>} args @param {string} cwd @param {(p: string) => boolean} exists */
  before(mode, client, tool, args, cwd, exists) {
    if (isCoderCall(tool, args)) {
      this.decided = this.coder = true;
      return "";
    }
    if (this.coder) return "";
    if (tool === "bash") {                                    // a bash write of existing files: not judged here
      return on(mode, "gate") ? this.gate(mode, client, newFiles(tool, args, cwd, exists)) : "";
    }
    if (!WRITES.has(tool)) return "";
    const why = on(mode, "gate") ? this.gate(mode, client, newFiles(tool, args, cwd, exists)) : "";
    if (!why) this.decided = true;                            // a write that goes through: the main agent does it
    return why;
  }

  /** After a call: the line to add to its result, or "".
   * @param {string} mode @param {"opencode" | "pi"} client @param {string} tool */
  after(mode, client, tool) {
    if (!LOOKS.has(tool) || this.decided) return "";
    this.looks += 1;
    if (!on(mode, "nudge") || this.nudged || this.looks !== NUDGE_AT) return "";
    this.nudged = true;
    return nudgeText(client);
  }
}

// @ts-check
// carl-compaction's core (Phase 23.5.2; client/shared/carl-compaction.js). Pure and harness-independent: it reads and
// writes the OpenAI chat format only, so the same code runs in OpenCode's and Pi's adapters now and in a gateway or
// CARL's own harness later. It never touches the disk, the network or a client API; an adapter gives it what it needs
// (the window, the project's root, a file reader, the session's record, the add-ons) and does the I/O.
//
// What it does (the design agreed with the user, docs/phase-plans/phase23.5.2/full_plan.md "The design as agreed"):
//   - the trigger (soft at idle, hard before a request would pass the window);
//   - one compaction event, never per turn: stage 1 prunes until the context is under the drain line (old thinking,
//     the reads the note replaces, old results and edit arguments, the current turn's middle, the changing files beyond
//     their budget), keeping the system prompt and tools, the current turn's user message and a raw working tail;
//     pruned results get short code-built placeholders; the CARL note (files, errors, tests, todo, a code map, the
//     working set) goes right before the current turn's user message;
//   - the record: every change, kept per session and applied byte for byte on every later request until the next
//     event, so the prefix never changes between events (the prompt cache keeps hitting);
//   - the events an add-on sees, from the request stream (what is new since the last request);
//   - the seams (the add-on hook points) and the fixed order their blocks are merged in;
//   - the stream helper for a reply CARL holds (keep-alives, the repeated prefix dropped, the final usage).
// Stage 2 (the summary) is not built yet: the record has its place.

import { createHash } from "node:crypto";

// ------------------------------------------------------------------------------------------------- the settings
// Every number is a setting: the bench (tools/compaction-bench) decides the defaults. Shares are of the window.

/** @typedef {{ soft: number, reserveMax: number, reserveShare: number, drain: number, tail: number, keepCalls: number,
 *   filesShare: number, wsFiles: number, minPrune: number, listWhole: number, facts: boolean, args: string,
 *   think: string, thinkMid: boolean, notes: boolean, files: boolean, map: boolean, overrunMax: number,
 *   regrowth: number, noteFacts: number, noteMap: number, region: number, outputMargin: number }} Settings */

/** @type {Settings} */
export const DEFAULTS = Object.freeze({
  soft: 0.62,          // the soft trigger: this share of the window, at idle (60-65% agreed)
  reserveMax: 16384,   // the hard trigger's reserve: min(reserveMax, reserveShare of the window) (16K = the p99 output)
  reserveShare: 0.2,
  drain: 0.4,          // the event works until the context is at most this share
  tail: 0.2,           // the raw working tail
  keepCalls: 8,        // ... at least this many newest tool results
  filesShare: 0.1,     // the files the current turn changes (and the note's working set)
  wsFiles: 5,          // the working set: this many files at most
  minPrune: 80,        // a result or an edit's arguments under this many tokens are never pruned
  listWhole: 300,      // a listing or search under this many tokens stays whole
  facts: true,         // the placeholders carry facts (else one plain line)
  args: "stub",        // old edit and write arguments: "off", "stub" ("[pruned: N lines]")
  think: "last",       // earlier thinking: "off", "empty", "last" (its last whole sentences)
  thinkMid: false,     // ... the current turn's middle's too (else kept)
  notes: true,         // the CARL note's facts (files, errors, tests, todo)
  files: true,         // ... its working set
  map: true,           // ... its code map
  overrunMax: 0.55,    // the excess in the current turn: an overrun up to this share is accepted
  regrowth: 0.2,       // after an event that ended above the line, the next soft event waits for this much growth
  noteFacts: 2000,     // tokens
  noteMap: 2000,
  region: 20,          // a large working file: its changed lines and this many around them
  outputMargin: 512,   // the output cap in the danger zone: the room left minus this
});

/** @param {Partial<Settings>} [over] @returns {Settings} */
export function settings(over = {}) {
  return { ...DEFAULTS, ...over };
}

// ---------------------------------------------------------------------------------------------------- the types
/** @typedef {{ [key: string]: any }} Msg  an OpenAI chat message */
/** @typedef {{ name: string, args: { [key: string]: any } }} Call */
/** @typedef {{ window: number, root?: string, extra?: number, readFile?: (path: string) => string | null,
 *   project?: { [path: string]: string }, set?: Partial<Settings>, reason?: string }} Ctx
 *   window: the context's size; root: the project's folder (paths are shown from it); extra: the tokens outside
 *   the messages (the tools) when the adapter knows them; readFile: the file's current text (live: from disk);
 *   project: the files before the session (the bench's stand-in for the disk); reason: soft, hard, manual, overflow. */

// -------------------------------------------------------------------------------------------------------- sizes
// Measured against the A3B's tokenizer (2026-10-13): a message's text, thinking and calls 3.34-3.41 chars a token,
// the tools 4.0. An adapter that knows the server's count uses it instead.
export const CHARS_PER_TOKEN = 3.35;
export const TOOLS_CHARS_PER_TOKEN = 4.0;

/** @param {any} m */
export function msgText(m) {
  const c = m?.content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((p) => (p && typeof p === "object" ? String(p.text ?? "") : "")).join("");
  return "";
}

/** A message's tokens: its text, thinking and calls. @param {Msg} m */
export function tokens(m) {
  return Math.floor((msgText(m).length + String(m.reasoning_content ?? "").length
    + JSON.stringify(m.tool_calls ?? []).length) / CHARS_PER_TOKEN);
}

/** @param {any} tools */
export function toolsTokens(tools) {
  return Math.floor(JSON.stringify(tools ?? []).length / TOOLS_CHARS_PER_TOKEN);
}

/** @param {Msg[]} messages @param {number} [extra] */
export function size(messages, extra = 0) {
  return messages.reduce((n, m) => n + tokens(m), 0) + extra;
}

// ------------------------------------------------------------------------------------- the transcript, read by code
export const READ_TOOLS = new Set(["read", "view", "cat", "read_file"]);
export const WRITE_TOOLS = new Set(["write", "edit", "multiedit", "apply_patch", "patch", "str_replace", "create"]);
export const COMMAND_TOOLS = new Set(["bash", "shell", "run", "exec", "command", "terminal"]);
export const LIST_TOOLS = new Set(["glob", "grep", "find", "ls", "list", "search", "rg"]);

/** An assistant message's tool calls as {name, args} (args parsed from their JSON text when they can be). @param {Msg} m
 * @returns {Call[]} */
export function toolCalls(m) {
  return (m?.tool_calls ?? []).map((/** @type {any} */ tc) => {
    const fn = tc?.function ?? {};
    let args = fn.arguments;
    if (typeof args === "string") {
      try {
        args = JSON.parse(args);
      } catch {
        args = { _raw: args };
      }
    }
    return { name: String(fn.name ?? ""), args: args && typeof args === "object" && !Array.isArray(args) ? args : {} };
  });
}

/** A path as the note shows it: from the project's root down. @param {string} p @param {string} [root] */
export function relPath(p, root = "") {
  let s = String(p).trim().replace(/^[`'"]+|[`'"]+$/g, "");
  const r = root.replace(/\/+$/, "");
  if (r && s.startsWith(r + "/")) s = s.slice(r.length + 1);
  return s.replace(/^\.\//, "");
}

/** What a call acts on: its file path, else its command's first word, else its pattern or URL. @param {Call} c
 * @param {string} [root] */
export function callTarget(c, root = "") {
  const a = c.args ?? {};
  for (const k of ["filePath", "file_path", "path", "file"]) if (typeof a[k] === "string" && a[k]) return relPath(a[k], root);
  const cmd = a.command ?? a.cmd;
  if (typeof cmd === "string" && cmd.trim()) return cmd.trim().split(/\s+/)[0];
  for (const k of ["pattern", "query", "url"]) if (typeof a[k] === "string") return a[k];
  return "";
}

/** Each tool result's index -> its call, from the assistant message before it. @param {Msg[]} messages
 * @returns {Map<number, Call>} */
export function callsOf(messages) {
  /** @type {Map<string, Call>} */
  const byId = new Map();
  /** @type {Map<number, Call>} */
  const out = new Map();
  messages.forEach((m, i) => {
    if (m.role === "assistant") {
      const tcs = m.tool_calls ?? [];
      toolCalls(m).forEach((c, j) => byId.set(String(tcs[j]?.id ?? ""), c));
    } else if (m.role === "tool") {
      out.set(i, byId.get(String(m.tool_call_id ?? "")) ?? { name: "", args: {} });
    }
  });
  return out;
}

/** A command as a placeholder shows it: "cd PROJECT && " dropped, at most 60 chars. @param {Call} c */
export function shortCmd(c) {
  const a = c.args ?? {};
  let s = String(a.command ?? a.cmd ?? "").trim().replace(/\n/g, " ").replace(/^cd [^&;]+(&&|;)\s*/, "");
  return s.length <= 60 ? s : s.slice(0, 57) + "...";
}

// ----------------------------------------------------------------------------------------------- errors and tests
const ERROR_RE = /(Traceback \(most recent call last\)|\w*Error\b|\w*Exception\b|\berror:|\bFAILED\b|\b\d+ failed\b|[Ee]xit code:? [1-9])/;
const NAMED_ERROR_RE = /\w+(Error|Exception)\b|\berror:/;
const DIAG_RE = /ERROR \[\d+:\d+\]/;
const DIAG_FILE_RE = /<diagnostics file="([^"]+)">([\s\S]*?)<\/diagnostics>/g;
const TEST_CMD_RE = /\b(pytest|unittest|tox|nox|npm (run )?test|yarn test|pnpm test|jest|vitest|go test|cargo test|make (test|check)|ruff|mypy|flake8|eslint|tsc)\b/;
// a command that changes the project or the system: a fixed word list, not inference
const SIDE_RE = /(^|[;&|])\s*(rm|mv|cp|mkdir|rmdir|touch|chmod|chown|ln|tee|sed\s+-i|pip3?\s+install|npm\s+(install|i|uninstall)|brew\s+install|git\s+(commit|push|checkout|switch|reset|add|rm|mv|merge|rebase|stash|tag|clean|restore|apply|cherry-pick))\b|[^<>=2&]>{1,2}\s*[\w./~-]/;
const EXIT_RE = /(?:exited with code|exit code:?)\s*(\d+)/gi;

/** The index of the last tool result that shows an error: a command's that says one, or a write tool's with a
 * diagnostic (a file read that shows `raise ValueError` is not one). @param {Msg[]} messages @returns {number} */
export function errorResult(messages) {
  const calls = callsOf(messages);
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== "tool") continue;
    const name = (calls.get(i)?.name ?? "").toLowerCase();
    const text = msgText(messages[i]);
    if ((COMMAND_TOOLS.has(name) && ERROR_RE.test(text)) || (WRITE_TOOLS.has(name) && DIAG_RE.test(text))) return i;
  }
  return -1;
}

/** The last line that names an error in a result. @param {string} text */
export function errorLine(text) {
  const diags = text.split("\n").map((l) => l.trim()).filter((l) => DIAG_RE.test(l));
  if (diags.length) return diags[diags.length - 1];
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => ERROR_RE.test(l));
  const named = lines.filter((l) => NAMED_ERROR_RE.test(l));
  return (named.length ? named : lines).at(-1) ?? "";
}

/** A write or edit result's diagnostics by file (OpenCode names each file; without a name they are the edited
 * file's). @param {string} text @param {string} path @param {string} [root] @returns {Map<string, string[]>} */
export function diagnostics(text, path, root = "") {
  /** @type {Map<string, string[]>} */
  const out = new Map();
  let found = false;
  for (const m of text.matchAll(DIAG_FILE_RE)) {
    found = true;
    const ds = m[2].split("\n").map((l) => l.trim()).filter((l) => DIAG_RE.test(l));
    if (ds.length) out.set(relPath(m[1], root), [...(out.get(relPath(m[1], root)) ?? []), ...ds]);
  }
  if (!found) {
    const ds = text.split("\n").map((l) => l.trim()).filter((l) => DIAG_RE.test(l));
    if (ds.length) out.set(path, ds);
  }
  return out;
}

/** A test run's outcome in STE100: the failed tests by name and the counts (pytest, unittest). @param {string} text */
export function testOutcome(text) {
  const failed = [...new Set([...[...text.matchAll(/FAILED \S*::(\w+)/g)].map((m) => m[1]),
    ...[...text.matchAll(/^(?:FAIL|ERROR): (\w+)/gm)].map((m) => m[1])])];
  const nf = text.match(/(\d+) failed/);
  const np = text.match(/(\d+) passed/);
  const ran = text.match(/Ran (\d+) tests?/);
  const nFailed = nf ? Number(nf[1]) : failed.length;
  const out = [];
  if (nFailed) {
    const names = failed.slice(0, 5).join(", ") + (failed.length > 5 ? ", ..." : "");
    out.push(`${nFailed} ${nFailed === 1 ? "test" : "tests"} failed${names ? `: ${names}.` : "."}`);
  }
  if (np) out.push(`${np[1]} tests passed.`);
  else if (ran && !nFailed) out.push(`${ran[1]} tests passed.`);
  return out.join(" ");
}

// ----------------------------------------------------------------------------------------------- the placeholders
const TOTAL_LINES_RE = /total (\d+) lines/;

/** @param {string} text */
function lineCount(text) {
  const m = text.slice(-200).match(TOTAL_LINES_RE);
  return m ? Number(m[1]) : text.replace(/\n+$/, "").split("\n").length;
}

/** @param {number} k @param {string} word */
function plural(k, word) {
  return `${k} ${word}${k === 1 ? "" : "s"}`;
}

/** A pruned result's placeholder, in STE100 (user, 2026-10-13: "say pruned instead of cleared"), built by code from
 * the call and its result, by kind. @param {Call} call @param {string} text @param {boolean} changedAfter
 * @param {boolean} inTask @param {string} [root] */
export function placeholder(call, text, changedAfter, inTask, root = "") {
  const name = (call.name || "tool").toLowerCase();
  const target = callTarget(call, root);
  if (READ_TOOLS.has(name)) {
    const head = `[pruned] read ${target}: ${plural(lineCount(text), "line")}.`;
    if (inTask) return `${head} You changed this file in this task. Read the file again before you change it.`;
    if (changedAfter) return `${head} The file changed after this read. This data is old. Read the file again before you use it.`;
    return `${head} Read the file again to see its contents.`;
  }
  if (COMMAND_TOOLS.has(name)) {
    const cmd = shortCmd(call);
    const ex = [...text.matchAll(EXIT_RE)].map((m) => m[1]);
    const head = `[pruned] ${name} "${cmd}":${ex.length ? ` exit ${ex[ex.length - 1]}.` : ""}`;
    if (TEST_CMD_RE.test(cmd)) {
      const o = testOutcome(text);
      if (o) return `${head} ${o} Do the tests again to see their status.`;
    }
    if (SIDE_RE.test(" " + cmd)) return `${head} This command changed the project. Do not do this command again.`;
    if (ERROR_RE.test(text)) {
      const e = errorLine(text);
      if (e) return `${head} Error: ${e.slice(0, 120)}`;
    }
    const last = text.split("\n").map((l) => l.trim()).filter(Boolean).at(-1) ?? "";
    return last ? `${head} Last line: "${last.slice(0, 100)}".` : `${head} No output.`;
  }
  if (WRITE_TOOLS.has(name)) {
    const n = (text.match(new RegExp(DIAG_RE.source, "g")) ?? []).length;
    if (/success|applied|wrote/i.test(text)) {
      return `[pruned] ${name} ${target}: The change is done.${n ? ` The tool reported ${plural(n, "error")}.` : ""}`;
    }
    const first = text.split("\n").map((l) => l.trim()).find(Boolean) ?? "";
    return `[pruned] ${name} ${target}: The change failed: "${first.slice(0, 100)}".`;
  }
  if (LIST_TOOLS.has(name)) {
    const items = text.split("\n").map((l) => relPath(l.trim(), root)).filter(Boolean);
    return `[pruned] ${name} "${target}": ${plural(items.length, "result")}. The first ${Math.min(10, items.length)}: ${items.slice(0, 10).join(", ")}.`;
  }
  return `[pruned] ${name} ${target}: ${plural(lineCount(text), "line")}.`.replace(/ {2}/g, " ");
}

// ------------------------------------------------------------------------------------------------------ thinking
const SENTENCE_END_RE = /[.!?]["')\]`*]*$/;

/** A pruned thinking block's remnant (user, 2026-10-13: "the last 50 tokens when it is a whole sentence the last 100
 * when it's a list of other format"): "[pruned: N tokens]" and the block's end, which often holds its decision.
 * @param {string} text */
export function thinkLast(text) {
  const t = text.trim();
  const n = Math.floor(t.length / CHARS_PER_TOKEN);
  let end;
  if (SENTENCE_END_RE.test(t)) {
    const room = Math.floor(50 * CHARS_PER_TOKEN);
    const sents = t.split(/(?<=[.!?])\s+/);
    const keep = [sents.pop() ?? ""];
    while (sents.length && [sents[sents.length - 1], ...keep].join(" ").length <= room) keep.unshift(sents.pop() ?? "");
    end = keep.join(" ");
    if (end.length > room) end = end.slice(-room).split(" ").slice(1).join(" ");
  } else {
    const room = Math.floor(100 * CHARS_PER_TOKEN);
    end = t.slice(-room);
    if (t.length > room) end = end.slice(0, -1).includes("\n") ? end.slice(end.indexOf("\n") + 1) : end.split(" ").slice(1).join(" ");
  }
  return `[pruned: ${n} tokens] ${end.trim()}`;
}

/** An assistant message's thinking: reasoning_content, else a <think> block in its text. @param {Msg} m */
export function thinkingOf(m) {
  if (typeof m.reasoning_content === "string" && m.reasoning_content.trim()) return m.reasoning_content;
  const c = m.content;
  if (typeof c === "string" && c.includes("</think>")) return c.split("</think>")[0].split("<think>").at(-1) ?? "";
  return "";
}

/** @param {Msg} m @param {string} next */
function setThinking(m, next) {
  if (typeof m.reasoning_content === "string" && m.reasoning_content.trim()) m.reasoning_content = next;
  else {
    const c = String(m.content ?? "");
    m.content = (next ? `<think>\n${next}\n</think>` : "") + c.slice(c.indexOf("</think>") + "</think>".length);
  }
}

// ----------------------------------------------------------------------------------------------- edit arguments
const ARG_STRINGS = new Set(["oldString", "newString", "content", "oldText", "newText", "old_string", "new_string", "patch"]);

/** An edit's or a write's strings as "[pruned: N lines]"; every other argument, and every name, as it was.
 * @param {any} args @returns {any} */
export function stubArgs(args) {
  if (Array.isArray(args)) return args.map(stubArgs);
  if (args && typeof args === "object") {
    /** @type {{ [k: string]: any }} */
    const out = {};
    for (const [k, v] of Object.entries(args)) {
      out[k] = ARG_STRINGS.has(k) && typeof v === "string" ? `[pruned: ${plural(v.split("\n").length || 1, "line")}]`
        : v && typeof v === "object" ? stubArgs(v) : v;
    }
    return out;
  }
  return args;
}

// ------------------------------------------------------------------------------------------------------- the note
const SKIP_DIRS = new Set(["node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache",
  ".mypy_cache", ".ruff_cache", ".tox", "coverage", ".next"]);
const DOC_RE = /(\.(md|txt|rst)$|(^|\/)(README|CHANGES|CHANGELOG|LICENSE)[^/]*$|(^|\/)\.gitignore$)/i;
const CODE_EXT = /\.(py|js|mjs|cjs|ts|tsx|jsx)$/;
const JS_DEF_RE = /^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>)/;
const PY_DEF_RE = /^(\s*)(?:async\s+)?(def|class)\s+(\w+)/;

/** @param {string} p */
function skipped(p) {
  return !p || p.split("/").some((part) => SKIP_DIRS.has(part));
}

/** A read result's file text when it holds the whole file (OpenCode's numbered <content> with its "End of file",
 * else the plain text); null for a part. @param {string} text @param {any} args */
export function readText(text, args) {
  if (args?.offset || args?.limit) return null;
  if (text.includes("<content>")) {
    const body = text.split("<content>")[1].split("</content>")[0];
    if (!body.includes("End of file")) return null;
    return body.replace(/^\n+|\n+$/g, "").split("\n").filter((l) => !l.startsWith("(End of file"))
      .map((l) => l.replace(/^\d+: ?/, "")).join("\n");
  }
  return text;
}

/** @param {any} args @returns {[string, string, boolean][]} */
function editPairs(args) {
  if (Array.isArray(args?.edits)) return args.edits.filter((/** @type {any} */ e) => e && typeof e === "object")
    .map((/** @type {any} */ e) => [String(e.oldText ?? ""), String(e.newText ?? ""), false]);
  const old = args?.oldString ?? args?.old_string;
  return typeof old === "string" ? [[old, String(args?.newString ?? args?.new_string ?? ""), Boolean(args?.replaceAll)]] : [];
}

/**
 * The session's files as code reads them from the transcript: their status (C created, M changed, R read, D deleted),
 * how many edits and where the last was, the files only seen in listings, and (with no reader) their contents rebuilt
 * from whole reads, writes and applied edits.
 * @param {Msg[]} messages @param {Ctx} ctx
 */
export function replay(messages, ctx) {
  const root = ctx.root ?? "";
  /** @type {Map<string, string>} */
  const state = new Map(Object.entries(ctx.project ?? {}));
  /** @type {Map<string, string>} */
  const status = new Map();
  /** @type {Map<string, number>} */
  const edits = new Map();
  /** @type {Map<string, number>} */
  const lastEdit = new Map();
  /** @type {Set<string>} */
  const unsure = new Set();
  /** @type {Set<string>} */
  const listed = new Set();
  /** @type {Map<string, string>} */
  const results = new Map();
  for (const m of messages) if (m.role === "tool") results.set(String(m.tool_call_id ?? ""), msgText(m));
  messages.forEach((m, i) => {
    if (m.role !== "assistant") return;
    const tcs = m.tool_calls ?? [];
    toolCalls(m).forEach((c, j) => {
      const name = c.name.toLowerCase();
      const path = callTarget(c, root);
      const res = results.get(String(tcs[j]?.id ?? "")) ?? "";
      if (READ_TOOLS.has(name) && path) {
        if (!res.includes("<content>") && /^\s*(Error|error:|ENOENT|File not found)/.test(res)) return;
        const t = readText(res, c.args);
        if (t !== null) {
          state.set(path, t);
          unsure.delete(path);
        }
        if (!status.has(path)) status.set(path, "R");
      } else if (WRITE_TOOLS.has(name) && path) {
        if (name === "write" || "content" in c.args) state.set(path, String(c.args.content ?? ""));
        else if (/success|applied/i.test(res)) {
          for (const [old, now, every] of editPairs(c.args)) {
            const cur = state.get(path);
            if (cur === undefined || !old || !cur.includes(old)) {
              unsure.add(path);
              continue;
            }
            state.set(path, every ? cur.split(old).join(now) : cur.replace(old, now));
          }
        } else return;                         // a failed edit changed nothing
        status.set(path, path in (ctx.project ?? {}) || ["M", "R"].includes(status.get(path) ?? "") ? "M" : "C");
        edits.set(path, (edits.get(path) ?? 0) + 1);
        lastEdit.set(path, i);
      } else if (COMMAND_TOOLS.has(name)) {
        const rm = shortCmd(c).match(/^rm\s+(?:-\w+\s+)*(.+)$/);
        if (rm) for (const p of rm[1].split(/\s+/)) {
          const q = relPath(p, root);
          state.delete(q);
          status.set(q, "D");
        }
      } else if (LIST_TOOLS.has(name)) {
        for (const l of res.split("\n")) {
          const q = relPath(l.trim().replace(/\/$/, ""), root);
          if (q && (q.split("/").at(-1) ?? "").includes(".")) listed.add(q);
        }
      }
    });
  });
  return { state, status, edits, lastEdit, unsure, listed };
}

/** The errors of the session, most recent first, each open or fixed. @param {Msg[]} messages @param {string} root
 * @param {number} [n] */
export function errorLedger(messages, root, n = 8) {
  const calls = callsOf(messages);
  /** @type {{ kind: string, key: string, cmd: string, fixed: boolean, text: string }[]} */
  const entries = [];
  for (const i of [...calls.keys()].sort((a, b) => a - b)) {
    const c = calls.get(i) ?? { name: "", args: {} };
    const text = msgText(messages[i]);
    const name = c.name.toLowerCase();
    if (COMMAND_TOOLS.has(name)) {
      const cmd = shortCmd(c);
      if (TEST_CMD_RE.test(cmd)) {
        const failed = [...new Set([...[...text.matchAll(/FAILED \S*::(\w+)/g)].map((m) => m[1]),
          ...[...text.matchAll(/^(?:FAIL|ERROR): (\w+)/gm)].map((m) => m[1])])];
        const clean = /\d+ passed|Ran \d+ tests?/.test(text) && !failed.length && !/\d+ failed|FAILED|Error/.test(text);
        for (const e of entries) {
          if (e.kind === "test" && !e.fixed && (clean || (e.cmd === cmd && !failed.includes(e.key)))) e.fixed = true;
        }
        const msg = text.match(/\w+(?:Error|Exception)(?::[^\n]{0,80})?/)?.[0] ?? "";
        for (const f of failed) entries.push({ kind: "test", key: f, cmd, fixed: false,
          text: `${f} failed${msg && failed.length === 1 ? `: ${msg}` : ""}` });
      } else {
        const bad = ERROR_RE.test(text);
        for (const e of entries) if (e.kind === "cmd" && e.key === cmd && !bad) e.fixed = true;
        if (bad) entries.push({ kind: "cmd", key: cmd, cmd, fixed: false, text: `"${cmd}": ${errorLine(text).slice(0, 100)}` });
      }
    } else if (WRITE_TOOLS.has(name)) {
      const path = callTarget(c, root);
      const byFile = diagnostics(text, path, root);
      for (const e of entries) if (e.kind === "diag" && e.key === path && !byFile.get(path)?.length) e.fixed = true;
      for (const [f, ds] of byFile) for (const d of ds.slice(0, 2)) {
        entries.push({ kind: "diag", key: f, cmd: "", fixed: false, text: `${f}: ${d.slice(0, 100)}` });
      }
    }
  }
  return entries.reverse().slice(0, n).map((e) => `- ${e.fixed ? "fixed" : "open"}: ${e.text}`);
}

/** The last test run's outcome, and the tests that failed before and pass now. @param {Msg[]} messages */
export function testsLine(messages) {
  const calls = callsOf(messages);
  const runs = [...calls.keys()].sort((a, b) => a - b)
    .filter((i) => COMMAND_TOOLS.has((calls.get(i)?.name ?? "").toLowerCase()) && TEST_CMD_RE.test(shortCmd(calls.get(i) ?? { name: "", args: {} })));
  if (!runs.length) return "";
  const last = msgText(messages[runs[runs.length - 1]]);
  const failing = new Set([...last.matchAll(/FAILED \S*::(\w+)/g)].map((m) => m[1]));
  const before = new Set(runs.slice(0, -1).flatMap((i) => [...msgText(messages[i]).matchAll(/FAILED \S*::(\w+)/g)].map((m) => m[1])));
  const fixed = [...before].filter((f) => !failing.has(f)).sort();
  const out = `Tests (the last run, "${shortCmd(calls.get(runs[runs.length - 1]) ?? { name: "", args: {} })}"): ${testOutcome(last) || "no result line."}`;
  return out + (fixed.length ? ` Fixed since an earlier failure: ${fixed.slice(0, 8).join(", ")}.` : "");
}

/** The agent's last todo list, as it wrote it. @param {Msg[]} messages */
export function todoLines(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== "assistant") continue;
    for (const c of toolCalls(messages[i]).reverse()) {
      if (c.name.toLowerCase().includes("todo") && Array.isArray(c.args.todos)) {
        return c.args.todos.filter((/** @type {any} */ t) => t && typeof t === "object")
          .map((/** @type {any} */ t) => `- [${t.status ?? ""}] ${String(t.content ?? "").slice(0, 120)}`);
      }
    }
  }
  return [];
}

/** The map: per code file, its classes and functions with their first lines (Python and JS/TS by patterns), other
 * files by name; the folders of the files in work first; cut at cap tokens. @param {Map<string, string>} files
 * @param {string[]} near @param {number} cap */
export function codeMap(files, near, cap) {
  const dirOf = (/** @type {string} */ p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
  const dirs = new Set(near.map(dirOf));
  const paths = [...files.keys()].filter((p) => !skipped(p))
    .sort((a, b) => (dirs.has(dirOf(a)) ? 0 : 1) - (dirs.has(dirOf(b)) ? 0 : 1) || a.localeCompare(b));
  const out = [];
  const others = [];
  let used = 0;
  const codeCount = paths.filter((p) => CODE_EXT.test(p)).length;
  for (const p of paths) {
    if (!CODE_EXT.test(p)) {
      others.push(p);
      continue;
    }
    const defs = [];
    const cls = [];
    (files.get(p) ?? "").split("\n").forEach((ln, k) => {
      if (p.endsWith(".py")) {
        const m = ln.match(PY_DEF_RE);
        if (!m) return;
        while (cls.length && cls[cls.length - 1][0] >= m[1].length) cls.pop();
        const name = cls.length ? `${cls[cls.length - 1][1]}.${m[3]}` : m[3];
        if (m[2] === "class") {
          defs.push(`class ${name} ${k + 1}`);
          cls.push([m[1].length, m[3]]);
        } else if (m[1].length === 0 || cls.length) defs.push(`${name} ${k + 1}`);
      } else {
        const m = ln.match(JS_DEF_RE);
        if (m) defs.push(`${m[2] ? "class " : ""}${m[1] ?? m[2] ?? m[3]} ${k + 1}`);
      }
    });
    const line = `${p}: ${defs.length ? defs.join(", ") : "(no functions)"}`;
    const cost = Math.floor(line.length / CHARS_PER_TOKEN) + 1;
    if (used + cost > cap) {
      out.push(`... ${codeCount - out.length} more code files.`);
      break;
    }
    out.push(line);
    used += cost;
  }
  if (others.length) out.push(`Other files: ${others.slice(0, 30).join(", ")}${others.length > 30 ? `, ... ${others.length - 30} more.` : "."}`);
  return out;
}

/** @param {string} text @param {number} [a] @param {number} [b] */
function numbered(text, a = 1, b) {
  const lines = text.split("\n");
  const end = b ?? lines.length;
  const out = [];
  for (let n = a; n <= end; n++) out.push(`${n}: ${lines[n - 1]}`);
  return out.join("\n");
}

/** The files whose current view is in the working tail (their last whole read is there). @param {Msg[]} messages
 * @param {number} start @param {string} root */
export function tailViews(messages, start, root) {
  const calls = callsOf(messages);
  /** @type {Map<string, number>} */
  const last = new Map();
  for (const i of [...calls.keys()].sort((a, b) => a - b)) {
    const c = calls.get(i) ?? { name: "", args: {} };
    if (READ_TOOLS.has(c.name.toLowerCase()) && readText(msgText(messages[i]), c.args) !== null) last.set(callTarget(c, root), i);
  }
  return new Set([...last].filter(([, i]) => i >= start).map(([p]) => p));
}

/**
 * The CARL note's text (STE100) and the files whose current contents it holds. scope: the working set's files, "all"
 * (the current turn's changed files, then the most recently edited, code and tests before documents) or "turn" (the
 * current turn's only). Add-on blocks (the noteSections seam, merged in their fixed order) go after the base sections,
 * before the working set.
 * @param {Msg[]} messages @param {Ctx} ctx @param {Settings} s @param {number} cur @param {number} start
 * @param {{ files: boolean, map: boolean, scope: string }} parts @param {Block[]} [extra]
 */
export function carlNote(messages, ctx, s, cur, start, parts, extra = []) {
  const root = ctx.root ?? "";
  const rep = replay(messages, ctx);
  const read = (/** @type {string} */ p) => (ctx.readFile ? ctx.readFile(p) : rep.state.get(p) ?? null);
  /** @type {string[]} */
  const changing = [];
  for (let i = messages.length - 1; i >= cur; i--) {
    if (messages[i].role !== "assistant") continue;
    for (const c of toolCalls(messages[i]).reverse()) {
      const t = callTarget(c, root);
      if (WRITE_TOOLS.has(c.name.toLowerCase()) && t && !changing.includes(t)) changing.push(t);
    }
  }
  const recent = [...rep.lastEdit.keys()].sort((a, b) => Number(DOC_RE.test(a)) - Number(DOC_RE.test(b))
    || (rep.lastEdit.get(b) ?? 0) - (rep.lastEdit.get(a) ?? 0));
  const order = [...changing, ...(parts.scope === "all" ? recent.filter((p) => !changing.includes(p)) : [])];
  const out = ["[CARL note] This note shows the state of this session when CARL made a compaction. It is not a request."];
  if (s.notes) {
    const touched = [...rep.status.keys()].filter((p) => !skipped(p)).sort();
    const words = { C: "created", M: "changed", R: "read", D: "deleted" };
    const lines = touched.map((p) => {
      const st = /** @type {"C"|"M"|"R"|"D"} */ (rep.status.get(p));
      const n = (read(p) ?? "").split("\n").length;
      return `- ${p}: ${words[st]}${st !== "D" ? `, ${plural(n, "line")}` : ""}${rep.edits.get(p) ? `, ${plural(rep.edits.get(p) ?? 0, "change")}` : ""}`;
    });
    /** @type {Map<string, number>} */
    const more = new Map();
    for (const p of [...rep.listed].filter((x) => !touched.includes(x) && !skipped(x)).sort()) {
      const d = p.includes("/") ? p.slice(0, p.lastIndexOf("/") + 1) : "./";
      more.set(d, (more.get(d) ?? 0) + 1);
    }
    for (const [d, k] of [...more].sort()) lines.push(`- ${d}: ${k} more ${k === 1 ? "file" : "files"} (seen in a listing)`);
    const facts = ["Files of this session:", ...lines];
    const errs = errorLedger(messages, root);
    if (errs.length) facts.push("Errors (the most recent first):", ...errs);
    const tl = testsLine(messages);
    if (tl) facts.push(tl);
    const todo = todoLines(messages);
    if (todo.length) facts.push("Todo (the last list):", ...todo);
    const kept = [];
    let used = 0;
    for (const ln of facts) {
      if (used + ln.length > s.noteFacts * CHARS_PER_TOKEN) {
        kept.push("(more lines were cut.)");
        break;
      }
      kept.push(ln);
      used += ln.length + 1;
    }
    out.push(kept.join("\n"));
  }
  if (parts.map) {
    /** @type {Map<string, string>} */
    const files = new Map();
    for (const p of new Set([...rep.state.keys(), ...rep.status.keys()])) {
      const t = read(p);
      if (t !== null && rep.status.get(p) !== "D") files.set(p, t);
    }
    out.push("Map of the code (use it to find code; read the file before you change it):\n" + codeMap(files, order, s.noteMap).join("\n"));
  }
  for (const b of extra) out.push(b.text);
  /** @type {string[]} */
  const held = [];
  if (parts.files) {
    const seen = tailViews(messages, start, root);
    const ws = [];
    let room = s.filesShare * ctx.window;
    for (const p of order.filter((x) => !seen.has(x)).slice(0, s.wsFiles)) {
      const text = read(p);
      if (text === null || held.includes(p) || skipped(p)) continue;
      const n = text.split("\n").length;
      const flag = !ctx.readFile && rep.unsure.has(p) ? " CARL could not apply all the changes to this file. Read the file before you change it." : "";
      let body = numbered(text);
      if (body.length / CHARS_PER_TOKEN > 0.4 * s.filesShare * ctx.window) {     // a large file: the regions around its changes
        /** @type {number[]} */
        const marks = [];
        for (const m of messages) {
          if (m.role !== "assistant") continue;
          for (const c of toolCalls(m)) {
            if (callTarget(c, root) !== p) continue;
            for (const [, now] of editPairs(c.args)) {
              const at = now.trim() ? text.indexOf(now) : -1;
              if (at >= 0) marks.push(text.slice(0, at).split("\n").length);
            }
          }
        }
        /** @type {number[][]} */
        const spans = [];
        for (const k of (marks.length ? [...new Set(marks)].sort((a, b) => a - b) : [1])) {
          const a = Math.max(1, k - s.region);
          const b = Math.min(n, k + s.region);
          if (spans.length && a <= spans[spans.length - 1][1] + 1) spans[spans.length - 1][1] = Math.max(spans[spans.length - 1][1], b);
          else spans.push([a, b]);
        }
        body = spans.map(([a, b]) => `(lines ${a}-${b} of ${n})\n${numbered(text, a, b)}`).join("\n");
      }
      const cost = body.length / CHARS_PER_TOKEN + 20;
      if (cost > room) continue;
      ws.push(`== ${p} (${plural(n, "line")}; ${plural(rep.edits.get(p) ?? 0, "change")} in this session).${flag}`, body);
      held.push(p);
      room -= cost;
    }
    if (ws.length) out.push("The current contents of the files in work:\n" + ws.join("\n"));
  }
  return { text: out.join("\n\n"), held };
}

// ---------------------------------------------------------------------------------------- the event (stage 1)
/** @typedef {{ kind: "result", id: string, text: string } | { kind: "args", at: number, call: number, args: string }
 *   | { kind: "think", at: number, text: string }} Edit
 *   an edit of the conversation: a tool result's new text (by its call id), a call's new arguments (by the message's
 *   place and the call's), a message's new thinking (by its place). */
/** @typedef {{ source: string, text: string }} Block  a block of text for the note or the summary (CARL's or an add-on's) */

/**
 * Stage 1 at one fill of the window: the edits that bring the context under the drain line, the note, and where the
 * note goes. Under the line it returns no edits.
 * @param {Msg[]} messages @param {Ctx} ctx @param {Settings} s
 * @param {{ files: boolean, map: boolean, scope: string }} parts @param {Block[]} [extra]
 */
export function planEvent(messages, ctx, s, parts, extra = []) {
  const root = ctx.root ?? "";
  const window = ctx.window;
  const work = messages.map((m) => ({ ...m }));
  const sizes = work.map(tokens);
  let total = sizes.reduce((a, b) => a + b, 0) + (ctx.extra ?? 0);
  /** @type {Edit[]} */
  const edits = [];
  if (total <= s.drain * window) return { edits, note: null, cut: messages.length, total };
  const users = work.map((m, i) => (m.role === "user" ? i : -1)).filter((i) => i >= 0);
  const cur = users.length ? users[users.length - 1] : 0;
  let start = work.length;
  let acc = 0;
  for (let i = work.length - 1; i >= 0; i--) {             // the working tail
    if (acc >= s.tail * window) break;
    acc += sizes[i];
    start = i;
  }
  const results = work.map((m, i) => (m.role === "tool" ? i : -1)).filter((i) => i >= 0);
  if (s.keepCalls > 0 && results.length >= s.keepCalls) start = Math.min(start, results[results.length - s.keepCalls]);
  while (start > 0 && work[start].role === "tool") start--;       // never a result without its call
  const calls = callsOf(work);
  const err = errorResult(work);
  /** @type {string[]} */
  const changing = [];
  for (let i = work.length - 1; i > cur; i--) {
    if (work[i].role !== "assistant") continue;
    for (const c of toolCalls(work[i]).reverse()) {
      const t = callTarget(c, root);
      if (WRITE_TOOLS.has(c.name.toLowerCase()) && t && !changing.includes(t)) changing.push(t);
    }
  }
  let note = null;
  /** @type {string[]} */
  let held = [];
  if (s.notes || parts.files || parts.map || extra.length) {
    const n = carlNote(work, ctx, s, cur, start, parts, extra);
    note = n.text;
    held = n.held;
    total += tokens({ content: note });
  }
  /** @type {Set<number>} */
  const keptFiles = new Set();
  /** @type {number[]} */
  const beyond = [];
  let room = s.filesShare * window;
  for (const path of parts.files ? [] : changing) {
    const view = [...results].reverse().find((i) => i < start && READ_TOOLS.has((calls.get(i)?.name ?? "").toLowerCase())
      && callTarget(calls.get(i) ?? { name: "", args: {} }, root) === path);
    if (view === undefined) continue;
    if (sizes[view] <= room) {
      keptFiles.add(view);
      room -= sizes[view];
    } else beyond.push(view);
  }
  const protectedIdx = new Set(keptFiles);
  if (err >= 0) protectedIdx.add(err);
  /** @type {[number, number][]} unit: [message index, -1 result | -2 thinking | j the j-th call's arguments] */
  let old = results.filter((i) => i < cur && !protectedIdx.has(i) && !beyond.includes(i)).map((i) => [i, -1]);
  let mid = results.filter((i) => i > cur && i < start && !protectedIdx.has(i) && !beyond.includes(i)).map((i) => [i, -1]);
  if (s.args !== "off") {
    /** @type {Map<string, number>} */
    const views = new Map([...keptFiles].map((v) => [callTarget(calls.get(v) ?? { name: "", args: {} }, root), v]));
    work.slice(0, start).forEach((m, i) => {
      if (m.role !== "assistant") return;
      toolCalls(m).forEach((c, j) => {
        const t = callTarget(c, root);
        if (!WRITE_TOOLS.has(c.name.toLowerCase()) || (views.has(t) && i > (views.get(t) ?? 0))) return;
        if (JSON.stringify(c.args).length / CHARS_PER_TOKEN >= s.minPrune) (i < cur ? old : mid).push([i, j]);
      });
    });
  }
  /** @type {[number, number][]} */
  const first = [];
  /** @type {Set<number>} */
  let superseded = new Set();
  if (held.length) {                                              // the reads the note's working set replaces
    superseded = new Set(results.filter((i) => i < start && READ_TOOLS.has((calls.get(i)?.name ?? "").toLowerCase())
      && held.includes(callTarget(calls.get(i) ?? { name: "", args: {} }, root)) && !protectedIdx.has(i)));
    first.push(...[...superseded].sort((a, b) => a - b).map((i) => /** @type {[number, number]} */ ([i, -1])));
    old = old.filter((u) => !superseded.has(u[0]));
    mid = mid.filter((u) => !superseded.has(u[0]));
  }
  if (s.think !== "off") {
    work.slice(0, start).forEach((m, i) => {
      if (m.role !== "assistant" || !thinkingOf(m).trim()) return;
      if (i < cur) first.push([i, -2]);
      else if (s.thinkMid) mid.push([i, -2]);
    });
  }
  const order = (/** @type {[number, number]} */ a, /** @type {[number, number]} */ b) => a[0] - b[0] || a[1] - b[1];
  old.sort(order);
  mid.sort(order);
  const units = [...first.filter((u) => u[1] === -1), ...first.filter((u) => u[1] === -2).sort(order), ...old, ...mid,
    ...beyond.map((b) => /** @type {[number, number]} */ ([b, -1]))];
  for (const [i, j] of units) {
    if (total <= s.drain * window) break;
    const m = work[i];
    if (j === -2) {                                                // a thinking block
      const was = thinkingOf(m);
      const next = s.think === "empty" ? "" : thinkLast(was);
      if (next.length >= was.length) continue;
      setThinking(m, next);
      edits.push({ kind: "think", at: i, text: next });
    } else if (j >= 0) {                                           // an edit's or a write's arguments
      const tcs = (m.tool_calls ?? []).map((/** @type {any} */ tc) => ({ ...tc, function: { ...(tc.function ?? {}) } }));
      const args = JSON.stringify(stubArgs(toolCalls(m)[j].args));
      tcs[j].function.arguments = args;
      m.tool_calls = tcs;
      edits.push({ kind: "args", at: i, call: j, args });
    } else {                                                       // a tool result
      const call = calls.get(i) ?? { name: "", args: {} };
      const text = msgText(m);
      const kind = call.name.toLowerCase();
      if (s.facts && !superseded.has(i) && (sizes[i] < s.minPrune || (LIST_TOOLS.has(kind) && sizes[i] < s.listWhole))) continue;
      let next;
      if (superseded.has(i)) next = `[pruned] read ${callTarget(call, root)}: ${plural(lineCount(text), "line")}. The current contents of this file are in the CARL note.`;
      else if (s.facts) {
        const path = callTarget(call, root);
        const later = results.some((k) => k > i && WRITE_TOOLS.has((calls.get(k)?.name ?? "").toLowerCase())
          && callTarget(calls.get(k) ?? { name: "", args: {} }, root) === path);
        next = placeholder(call, text, later, beyond.includes(i), root);
      } else next = `[pruned] the output of ${call.name} ${callTarget(call, root)}.`;
      m.content = next;
      edits.push({ kind: "result", id: String(m.tool_call_id ?? ""), text: next });
    }
    const was = sizes[i];
    sizes[i] = tokens(m);
    total -= was - sizes[i];
  }
  return { edits, note, cut: messages.length, cur, start, total, held };
}

/**
 * The event: stage 1, and when it cannot reach the drain line, by where the excess is (agreed 2026-10-13): in the old
 * part, stage 2 would summarize it (not built: the overrun stays); in the current turn, the note's working set yields
 * (to the turn's files, then none), then its map, then the overrun is accepted (up to overrunMax).
 * @param {Msg[]} messages @param {Ctx} ctx @param {Block[]} [extra]
 */
export function compactEvent(messages, ctx, extra = []) {
  const s = settings(ctx.set);
  const users = messages.map((m, i) => (m.role === "user" ? i : -1)).filter((i) => i >= 0);
  const cur = users.length ? users[users.length - 1] : 0;
  /** @type {{ files: boolean, map: boolean, scope: string }[]} */
  const steps = [{ files: s.files, map: s.map, scope: "all" }];
  if (s.files) steps.push({ files: true, map: s.map, scope: "turn" }, { files: false, map: s.map, scope: "all" });
  if (s.map) steps.push({ files: false, map: false, scope: "all" });
  /** @type {ReturnType<typeof planEvent> | null} */
  let plan = null;
  for (const [k, parts] of steps.entries()) {
    plan = planEvent(messages, ctx, s, parts, extra);
    if (plan.total <= s.drain * ctx.window) break;
    const oldPart = applyEdits(messages, plan.edits).slice(0, cur).filter((m) => m.role !== "system").reduce((a, m) => a + tokens(m), 0);
    if (k === 0 && oldPart >= plan.total - s.drain * ctx.window) break;      // the excess is the old part's: stage 2's job
  }
  const p = /** @type {ReturnType<typeof planEvent>} */ (plan);
  return { ...p, overrun: p.total > s.drain * ctx.window, accepted: p.total <= s.overrunMax * ctx.window };
}

// -------------------------------------------------------------------------------------------------------- the record
/** @typedef {{ v: 1, session: string, epoch: number, cut: number, fingerprint: string, edits: Edit[],
 *   note: { at: number, text: string } | null, attach: { id: string, text: string }[], summary: null,
 *   size: { before: number, after: number }, at: number }} Record
 *   the session's compaction record: kept by the adapter (on disk, ctx.storage, or the dashboard's API) and applied to
 *   every later request until the next event. */

/** The part of a message the fingerprint and the replay rely on (clients order the fields differently). @param {Msg} m */
function canonical(m) {
  return {
    role: m.role, content: msgText(m), reasoning: String(m.reasoning_content ?? ""), tool_call_id: m.tool_call_id ?? null,
    calls: toolCalls(m).map((c) => [c.name, JSON.stringify(c.args)]),
  };
}

/** A fingerprint of the conversation before the cut, as the client sent it (before any of CARL's edits).
 * @param {Msg[]} messages @param {number} cut */
export function fingerprint(messages, cut) {
  return createHash("sha256").update(JSON.stringify(messages.slice(0, cut).map(canonical))).digest("hex");
}

/** The messages with the edits applied (a copy). @param {Msg[]} messages @param {Edit[]} edits */
export function applyEdits(messages, edits) {
  const out = messages.map((m) => ({ ...m }));
  /** @type {Map<string, number>} */
  const byId = new Map();
  out.forEach((m, i) => {
    if (m.role === "tool") byId.set(String(m.tool_call_id ?? ""), i);
  });
  for (const e of edits) {
    if (e.kind === "result") {
      const i = byId.get(e.id);
      if (i !== undefined) out[i].content = e.text;
    } else if (e.kind === "args") {
      const m = out[e.at];
      if (!m?.tool_calls?.[e.call]) continue;
      const tcs = m.tool_calls.map((/** @type {any} */ tc) => ({ ...tc, function: { ...(tc.function ?? {}) } }));
      tcs[e.call].function.arguments = e.args;
      m.tool_calls = tcs;
    } else if (e.kind === "think") {
      if (out[e.at]) setThinking(out[e.at], e.text);
    }
  }
  return out;
}

/**
 * A record made from an event: what the next requests replay.
 * @param {string} session @param {Msg[]} messages @param {ReturnType<typeof compactEvent>} plan @param {number} epoch
 * @param {number} [now]
 */
export function makeRecord(session, messages, plan, epoch, now = Date.now()) {
  const users = messages.map((m, i) => (m.role === "user" ? i : -1)).filter((i) => i >= 0);
  const at = users.length ? users[users.length - 1] : Math.min(1, messages.length);
  /** @type {Record} */
  const rec = {
    v: 1, session, epoch, cut: plan.cut, fingerprint: fingerprint(messages, plan.cut), edits: plan.edits,
    note: plan.note ? { at, text: plan.note } : null, attach: [], summary: null,
    size: { before: size(messages), after: plan.total }, at: now,
  };
  return rec;
}

/**
 * The request's messages with the record applied, byte for byte; null when the history no longer matches (an edit, a
 * revert, a fork: the record is dropped, and the request is checked for a trigger anew). Messages after the cut pass
 * as they are, apart from the add-on blocks attached to their tool results.
 * @param {Msg[]} messages @param {Record} rec
 */
export function applyRecord(messages, rec) {
  if (messages.length < rec.cut || fingerprint(messages, rec.cut) !== rec.fingerprint) return null;
  const out = applyEdits(messages, rec.edits);
  for (const a of rec.attach) {
    const m = out.find((x) => x.role === "tool" && String(x.tool_call_id ?? "") === a.id);
    if (m) m.content = `${msgText(m)}\n\n${a.text}`;
  }
  if (rec.note) out.splice(rec.note.at, 0, { role: "user", content: rec.note.text });
  return out;
}

// ------------------------------------------------------------------------------------------------------ the trigger
/**
 * Whether an event runs now: "hard" (before this request: the prompt plus the reserve would pass the window),
 * "soft" (at idle: past the soft share, and after an event that ended above the line, grown by regrowth since), or "".
 * @param {number} prompt the context's tokens now @param {Ctx} ctx @param {boolean} idle
 * @param {Record | null} [last] the session's last record
 */
export function trigger(prompt, ctx, idle, last = null) {
  const s = settings(ctx.set);
  const reserve = Math.min(s.reserveMax, s.reserveShare * ctx.window);
  if (prompt + reserve > ctx.window) return "hard";
  if (!idle || prompt < s.soft * ctx.window) return "";
  if (last && last.size.after > s.drain * ctx.window && prompt < last.size.after + s.regrowth * ctx.window) return "";
  return "soft";
}

/** The output cap in the danger zone (the prompt plus the client's own limit would pass the window): the room left
 * minus the margin; undefined outside it. @param {number} prompt @param {number} clientMax @param {Ctx} ctx */
export function outputCap(prompt, clientMax, ctx) {
  const s = settings(ctx.set);
  return prompt + clientMax > ctx.window ? Math.max(256, ctx.window - prompt - s.outputMargin) : undefined;
}

// ---------------------------------------------------------------------------------- the events (onEvents' source)
/** @typedef {{ kind: string, at: number, [key: string]: any }} SessionEvent */

/**
 * What is new in this request since the last one, as normalized events: user turns, tool calls and their results,
 * file reads and edits, test runs, errors. Pure: the adapter passes the message count it last saw.
 * @param {Msg[]} messages @param {number} seen @param {string} [root]
 * @returns {SessionEvent[]}
 */
export function newEvents(messages, seen, root = "") {
  /** @type {SessionEvent[]} */
  const out = [];
  const calls = callsOf(messages);
  messages.forEach((m, i) => {
    if (i < seen) return;
    if (m.role === "user") out.push({ kind: "turn.user", at: i, text: msgText(m).slice(0, 4096) });
    else if (m.role === "assistant") {
      const tcs = m.tool_calls ?? [];
      toolCalls(m).forEach((c, j) => out.push({ kind: "tool.call", at: i, call_id: String(tcs[j]?.id ?? ""), tool: c.name,
        target: callTarget(c, root) }));
    } else if (m.role === "tool") {
      const c = calls.get(i) ?? { name: "", args: {} };
      const name = c.name.toLowerCase();
      const text = msgText(m);
      const target = callTarget(c, root);
      out.push({ kind: "tool.result", at: i, call_id: String(m.tool_call_id ?? ""), tool: c.name, target, bytes: text.length });
      if (READ_TOOLS.has(name)) out.push({ kind: "file.read", at: i, path: target });
      if (WRITE_TOOLS.has(name) && /success|applied|wrote/i.test(text)) out.push({ kind: "file.edited", at: i, path: target });
      if (COMMAND_TOOLS.has(name) && TEST_CMD_RE.test(shortCmd(c))) out.push({ kind: "test.result", at: i, command: shortCmd(c), outcome: testOutcome(text) });
      if (errorResult(messages.slice(0, i + 1)) === i) out.push({ kind: "error", at: i, line: errorLine(text) });
    }
  });
  return out;
}

// -------------------------------------------------------------------------------------------------------- the seams
// The add-on hook points (settled 2026-10-14, the private design's Part A revised): each is also used by CARL itself.
// The host (worker threads, deadlines, budgets, the circuit breaker) runs next to the core in the adapter; the core
// defines the seams, validates what comes back, and merges blocks in a fixed order (CARL first, then add-ons by id).
export const SEAMS = Object.freeze({
  onEvents: "observe",          // the normalized events, batched (session start, resume and end are event kinds)
  fileTree: "provide",          // additions to the session's file tree (add only)
  noteSections: "provide",      // blocks for the CARL note
  summaryInputs: "provide",     // reference points for the stage 2 summarizer (the same block shape)
  beforeToolUse: "observe",     // each tool call as the reply streams it, before the client runs it
  attach: "provide",            // a short block on the newest tool result (a pruned reference, a named tool, after beforeToolUse)
  beforeCompaction: "observe",  // the event is about to run, with its reason
  onCompaction: "observe",      // the event's result and outcome (ok, failed, aborted, skipped)
});
export const API = Object.freeze({ major: 1, minor: 0 });
const BLOCK_MAX = 4000;          // chars: a block longer is cut, with a fixed preview line

/**
 * Add-on blocks as the core takes them: typed, checked, capped, without control characters, in a fixed order (CARL's
 * own first, then add-ons by id), so the note is byte-identical run to run (the prompt cache).
 * @param {{ source: string, blocks: unknown }[]} answers
 * @returns {Block[]}
 */
export function mergeBlocks(answers) {
  /** @type {Block[]} */
  const out = [];
  const rank = (/** @type {string} */ s) => (s === "carl" ? "" : s);
  for (const a of [...answers].sort((x, y) => rank(x.source).localeCompare(rank(y.source)))) {
    if (!Array.isArray(a.blocks)) continue;
    for (const b of a.blocks) {
      if (!b || typeof b !== "object" || typeof (/** @type {any} */ (b)).text !== "string") continue;
      let text = String((/** @type {any} */ (b)).text).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
      if (text.length > BLOCK_MAX) text = text.slice(0, BLOCK_MAX) + `\n(cut: ${text.length - BLOCK_MAX} more characters)`;
      if (text.trim()) out.push({ source: a.source, text });
    }
  }
  return out;
}

// ------------------------------------------------------------------------------------- the stream (a held reply)
/** An SSE comment line that keeps a client waiting while CARL holds a reply (both clients ignore it; Pi drops a stream
 * silent for ~300 s and sends the request again: measured 2026-10-13). */
export const KEEP_ALIVE = ": keep-alive\n\n";
export const KEEP_ALIVE_MS = 10_000;

/**
 * The continued reply's stream with the part CARL sent back removed: llama.cpp streams a continued reply from its
 * start (the part given as the final assistant message, then the new text). Returns a function that takes each
 * chunk's delta (content and reasoning) and gives back what the client should still get.
 * @param {string} sentContent @param {string} sentReasoning
 */
export function dropPrefix(sentContent, sentReasoning) {
  let c = sentContent;
  let r = sentReasoning;
  return (/** @type {{ content?: string, reasoning_content?: string }} */ delta) => {
    const out = { ...delta };
    for (const [key, left] of /** @type {const} */ ([["content", "c"], ["reasoning_content", "r"]])) {
      const text = out[key];
      if (typeof text !== "string" || !text) continue;
      const rest = left === "c" ? c : r;
      const k = Math.min(rest.length, text.length);
      if (text.slice(0, k) === rest.slice(0, k)) {
        out[key] = text.slice(k);
        if (left === "c") c = rest.slice(k);
        else r = rest.slice(k);
      } else if (left === "c") c = "";
      else r = "";
    }
    return out;
  };
}

/** The A3B's (Qwen's) tool-call text for a call cut while streaming, from its partial arguments (tested 2026-10-13:
 * the call comes back whole). Other templates need their own format; an unknown one falls back to compact and redo.
 * @param {string} name @param {string} argsText @param {boolean} afterText */
export function qwenCallText(name, argsText, afterText) {
  let s = `${afterText ? "\n\n" : ""}<tool_call>\n<function=${name}>\n`;
  for (const [k, v, done] of partialArgs(argsText)) s += `<parameter=${k}>\n${v}` + (done ? "\n</parameter>\n" : "");
  return s;
}

/** A JSON object's text, maybe cut anywhere, as (key, value, complete) triples. @param {string} text
 * @returns {[string, string, boolean][]} */
export function partialArgs(text) {
  /** @type {[string, string, boolean][]} */
  const out = [];
  const n = text.length;
  let i = text.indexOf("{") + 1;
  const str = (/** @type {number} */ k) => {
    let j = k + 1;
    let buf = "";
    while (j < n) {
      const ch = text[j];
      if (ch === "\\") {
        if (j + 1 >= n) break;
        buf += text.slice(j, j + 2);
        j += 2;
        continue;
      }
      if (ch === '"') return { v: JSON.parse(`"${buf}"`), next: j + 1, done: true };
      buf += ch;
      j++;
    }
    try {
      return { v: JSON.parse(`"${buf}"`), next: n, done: false };
    } catch {
      return { v: buf.includes("\\") ? JSON.parse(`"${buf.slice(0, buf.lastIndexOf("\\"))}"`) : buf, next: n, done: false };
    }
  };
  if (i <= 0) return out;
  while (i < n) {
    while (i < n && " \n\t,".includes(text[i])) i++;
    if (i >= n || text[i] === "}") break;
    const key = str(i);
    if (!key.done) break;
    i = key.next;
    while (i < n && " \n\t:".includes(text[i])) i++;
    if (i >= n) {
      out.push([String(key.v), "", false]);
      break;
    }
    if (text[i] === '"') {
      const val = str(i);
      out.push([String(key.v), String(val.v), val.done]);
      if (!val.done) break;
      i = val.next;
    } else {
      let j = i;
      while (j < n && !",}".includes(text[j])) j++;
      out.push([String(key.v), text.slice(i, j).trim(), j < n]);
      i = j;
    }
  }
  return out;
}

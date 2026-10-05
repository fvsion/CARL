// @ts-check
// CARL: how Pi shows the result of a background subagent (index.ts registers the message renderer).
// The model reads the message's content as it is:
//   <subagent id="bg-1" agent="coder" state="done" took="42 s">
//   …the result…
//   </subagent>
// The screen shows "Coder finished (42 s)" and the result. The message's details carry the same facts
// ({ background: { id, agent, state, took } }); a message without them (an older session) is parsed.
// Pure functions: tests/js/pi-subagent.test.mjs runs them in node.

/** @typedef {"done" | "failed" | "stopped"} JobState */
/** @typedef {{ id: string, agent: string, state: JobState, took: string }} JobFacts */
/** @typedef {JobFacts & { text: string }} JobResult */

/** The lines of a collapsed result. */
export const COLLAPSED_LINES = 3;

/**
 * A time span as CARL writes it everywhere (tools/carl_core/domain/units.py): 42 s, 3 min, 1 h 12 min.
 * @param {number} ms
 * @returns {string}
 */
export function duration(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.floor(m / 60)} h`;
}

/** @param {unknown} x @returns {Record<string, unknown>} */
function obj(x) {
  return x !== null && typeof x === "object" && !Array.isArray(x) ? /** @type {Record<string, unknown>} */ (x) : {};
}

/** @param {unknown} s @returns {JobState} */
function jobState(s) {
  return s === "failed" || s === "stopped" ? s : "done";
}

/**
 * The message's text (Pi's content: a string, or text and image parts).
 * @param {unknown} content
 * @returns {string}
 */
export function contentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((p) => (obj(p).type === "text" && typeof obj(p).text === "string" ? String(obj(p).text) : "")).join("");
}

/**
 * The facts and the text of a background result: from the details when they are there, else from the
 * <subagent …> tag of the content.
 * @param {unknown} content
 * @param {unknown} details
 * @returns {JobResult}
 */
export function parseResult(content, details) {
  const raw = contentText(content);
  const m = /^\s*<subagent\b([^>]*)>\n?([\s\S]*?)\n?<\/subagent>\s*$/.exec(raw);
  /** @type {Record<string, string>} */
  const attrs = {};
  if (m) for (const a of m[1].matchAll(/(\w+)="([^"]*)"/g)) attrs[a[1]] = a[2];
  const bg = obj(obj(details).background);
  const pick = (/** @type {string} */ k) => (typeof bg[k] === "string" && bg[k] ? String(bg[k]) : attrs[k] ?? "");
  return {
    id: pick("id"),
    agent: pick("agent") || "subagent",
    state: jobState(pick("state")),
    took: pick("took"),
    text: (m ? m[2] : raw).trim(),
  };
}

/**
 * The agent's name for a title: CARL's coder is "Coder", another agent keeps its name with a capital.
 * @param {string} agent
 * @returns {string}
 */
export function agentLabel(agent) {
  if (agent === "coder" || agent === "carl-coder") return "Coder";
  return agent ? agent[0].toUpperCase() + agent.slice(1) : "Subagent";
}

/**
 * The first line: "Coder finished (42 s)", "Coder failed (3 min)", "Coder was stopped (12 s)".
 * @param {JobResult} r
 * @returns {string}
 */
export function resultTitle(r) {
  const what = r.state === "failed" ? "failed" : r.state === "stopped" ? "was stopped" : "finished";
  return `${agentLabel(r.agent)} ${what}${r.took ? ` (${r.took})` : ""}`;
}

/**
 * The result's lines to show: all of them when expanded, else the first few; `more` is how many are hidden.
 * @param {string} text
 * @param {boolean} expanded
 * @returns {{ lines: string[], more: number }}
 */
export function resultBody(text, expanded) {
  const all = text ? text.split("\n") : [];
  if (expanded || all.length <= COLLAPSED_LINES) return { lines: all, more: 0 };
  return { lines: all.slice(0, COLLAPSED_LINES), more: all.length - COLLAPSED_LINES };
}

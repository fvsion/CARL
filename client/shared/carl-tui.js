// @ts-check
// The helpers that CARL's OpenCode TUI plugins share (client/shared/carl-tui.js: the session-switcher and
// subagents-sidebar plugins each carry a copy, installed by client/configure.py): OpenTUI nodes without JSX,
// one-line text, a time span, the rows of an SDK answer, the session on the screen.
//
// This file has no runtime imports: OpenCode supplies @opentui/solid to a plugin's tui.js, which gives its
// functions to nodes(). Only the types come from the packages.

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginApi} TuiPluginApi */
/** @typedef {import("@opencode-ai/plugin/tui").TuiThemeCurrent} Theme */
/** @typedef {Theme["text"]} Color */
/** @typedef {ReturnType<typeof import("@opentui/solid").createElement>} Node */
/** @typedef {Node | string | null | undefined | false} Child */
/**
 * The OpenTUI functions that make nodes (from @opentui/solid).
 * @typedef {object} Solid
 * @property {typeof import("@opentui/solid").createElement} createElement
 * @property {typeof import("@opentui/solid").insert} insert
 * @property {typeof import("@opentui/solid").setProp} setProp
 */
/**
 * @typedef {object} Nodes
 * @property {(tag: string, props: Record<string, unknown>, children?: Child[]) => Node} el  any element
 * @property {(props: Record<string, unknown>, children: Child[]) => Node} box
 * @property {(fg: Color, value: string) => Node} text  one text node in one colour
 */

/**
 * Node makers on top of the OpenTUI functions.
 * @param {Solid} solid
 * @returns {Nodes}
 */
export function nodes({ createElement, insert, setProp }) {
  /** @type {Nodes["el"]} */
  const el = (tag, props, children = []) => {
    const node = createElement(tag);
    for (const [k, v] of Object.entries(props)) if (v !== undefined) setProp(node, k, v);
    for (const c of children) if (c !== null && c !== undefined && c !== false) insert(node, c);
    return node;
  };
  return {
    el,
    box: (props, children) => el("box", props, children),
    text: (fg, value) => el("text", { fg }, [value]),
  };
}

/**
 * Slots are typed with solid-js's DOM-based JSX.Element; OpenTUI renders its own nodes there, so this is a
 * type-level conversion only.
 * @param {Node | null} node
 * @returns {import("@opentui/solid").JSX.Element}
 */
export const asElement = (node) => /** @type {import("@opentui/solid").JSX.Element} */ (/** @type {unknown} */ (node));

/**
 * One line of at most n columns, with an ellipsis when cut.
 * @param {unknown} s
 * @param {number} n
 * @returns {string}
 */
export function cut(s, n) {
  const line = String(s ?? "").replace(/\s+/g, " ").trim();
  return line.length > n ? line.slice(0, Math.max(n - 1, 0)) + "…" : line;
}

/**
 * A time span as CARL writes it everywhere (tools/carl_core/domain/units.py): 42 s, 3 min, 1 h 12 min,
 * 2 days.
 * @param {number} ms
 * @returns {string}
 */
export function duration(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  return `${Math.floor(h / 24)} days`;
}

/**
 * The rows of an SDK response. The TUI client returns { data }; a bare array is accepted too (other client
 * configurations).
 * @template T
 * @param {{ data?: T[] } | T[] | undefined} res
 * @returns {T[]}
 */
export function rows(res) {
  return Array.isArray(res) ? res : (res?.data ?? []);
}

/**
 * The session on the screen, from the route (undefined on the home screen).
 * @param {TuiPluginApi} api
 * @returns {string | undefined}
 */
export function routeSessionID(api) {
  const route = api.route.current;
  if (route.name !== "session") return undefined;
  const id = route.params?.sessionID;
  return typeof id === "string" ? id : undefined;
}

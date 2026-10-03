// Request policy for the MTPLX provider, shared by index.js (V1 hooks) and
// server.js (V2 setup). Kept out of index.js on purpose: older OpenCode
// versions call every export of a plugin's entry module as a plugin function.
//
// The provider is "mtplx", or "llm-deploy-mtplx" when the user already had a
// provider called "mtplx" (client/configure.py picks the id).

/** @typedef {import("@opencode-ai/plugin").Hooks} Hooks */
/** @typedef {Parameters<NonNullable<Hooks["chat.headers"]>>[0]} ChatInput */
/** @typedef {Parameters<NonNullable<Hooks["chat.params"]>>[1]} ChatParams */

/** @type {ReadonlySet<string>} */
export const MTPLX_PROVIDER_IDS = new Set(["mtplx", "llm-deploy-mtplx"]);

// What OpenCode injects on its own (see stripInjectedDefaults).
const INJECTED_OUTPUT_CAP = 32000;
const INJECTED_QWEN_TEMPERATURE = 0.55;
const INJECTED_QWEN_TOP_P = 1;

// Ids are OpenCode's own ("ses_…", "msg_…"); anything else (CR/LF, spaces,
// odd lengths) is dropped rather than sent, so a header can never be split.
const SAFE_HEADER_VALUE = /^[A-Za-z0-9._:-]{1,128}$/;

/**
 * The id as a header value, or undefined when it isn't a plain token.
 * @param {unknown} value
 * @returns {string | undefined}
 */
export function headerValue(value) {
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const text = String(value);
  return SAFE_HEADER_VALUE.test(text) ? text : undefined;
}

/**
 * True unless the request names a provider other than ours; a request
 * without a provider id counts as ours.
 * @param {Partial<ChatInput> | undefined} input
 * @returns {boolean}
 */
export function isMtplxRequest(input) {
  const providerID = input?.model?.providerID || input?.provider?.info?.id;
  return !providerID || MTPLX_PROVIDER_IDS.has(providerID);
}

/**
 * The headers MTPLX uses to find its prompt cache for this session.
 * @param {unknown} sessionID
 * @param {unknown} [turnID]
 * @returns {Record<string, string>}
 */
export function sessionHeaders(sessionID, turnID) {
  /** @type {Record<string, string>} */
  const headers = { "x-mtplx-client": "opencode" };
  const session = headerValue(sessionID);
  if (session) headers["x-mtplx-session-id"] = session;
  const turn = headerValue(turnID);
  if (turn) headers["x-mtplx-client-turn-id"] = turn;
  return headers;
}

/**
 * Removes the values OpenCode injects by itself; any other value is a
 * deliberate client choice and passes through untouched.
 * @param {Partial<ChatInput> | undefined} input
 * @param {ChatParams} output
 * @returns {void}
 */
export function stripInjectedDefaults(input, output) {
  // OpenCode injects maxOutputTokens = min(limit.output, 32000) on every
  // request even when the model advertises a larger native context; without
  // it MTPLX owns the (uncapped) generation budget.
  if (output.maxOutputTokens === INJECTED_OUTPUT_CAP) output.maxOutputTokens = undefined;

  // OpenCode <= 1.18.20 (Desktop 1.18.18 included) injects a qwen-keyed sampler
  // (temperature 0.55, topP 1) for any model id containing "qwen"; 1.18.21
  // removed the rule. Without it the server's family-native sampler applies.
  // `modelID` is the older SDKs' name for `id`.
  const model = /** @type {{ id?: unknown, modelID?: unknown } | undefined} */ (input?.model);
  const modelID = String(model?.id ?? model?.modelID ?? "").toLowerCase();
  if (!modelID.includes("qwen")) return;
  // The hook's type says number, but OpenCode reads undefined as "not set":
  // that is what makes the stripping work.
  const sampler = /** @type {{ temperature: number | undefined, topP: number | undefined }} */ (output);
  if (sampler.temperature === INJECTED_QWEN_TEMPERATURE) sampler.temperature = undefined;
  if (sampler.topP === INJECTED_QWEN_TOP_P) sampler.topP = undefined;
}

// @ts-check
// What the CARL server runs against what OpenCode asks for (used by index.js). Pure, except
// serverModels(), which asks the server. Kept out of index.js: older OpenCode versions call
// every export of a plugin's entry module as a plugin function.
//
// The server's /v1/models tells the two modes apart: a single-model server lists the one
// model it runs (and answers every request with it, whatever name is sent); a router lists
// every model it offers, each with a status (loaded / loading / unloaded / ...), loads the
// one asked for, and answers HTTP 400 "not found" for a name it doesn't have.

/** @typedef {{ id: string, status?: string }} ServerModel */
/** @typedef {{ router: boolean, models: ServerModel[] }} ServerState */
/** @typedef {{ key: string, variant: "info" | "warning" | "error", message: string }} Notice */

/**
 * The server's state from a /v1/models answer (undefined when it isn't one).
 * @param {unknown} body
 * @returns {ServerState | undefined}
 */
export function parseModels(body) {
  const data = /** @type {{ data?: unknown }} */ (body ?? {}).data;
  if (!Array.isArray(data)) return undefined;
  /** @type {ServerModel[]} */
  const models = [];
  let router = false;
  for (const item of data) {
    if (!item || typeof item !== "object") continue;
    const m = /** @type {{ id?: unknown, status?: unknown }} */ (item);
    if (typeof m.id !== "string") continue;
    const st = m.status && typeof m.status === "object" ? /** @type {{ value?: unknown }} */ (m.status).value : undefined;
    if (typeof st === "string") router = true;
    models.push({ id: m.id, status: typeof st === "string" ? st : undefined });
  }
  return { router, models };
}

/**
 * What to tell the user about a request for model `wanted`, or undefined (all is well).
 * The key is the same while the situation is, so each one is said once.
 * @param {string} wanted
 * @param {ServerState} s
 * @returns {Notice | undefined}
 */
export function verdict(wanted, s) {
  if (!s.router) {
    const running = s.models[0]?.id;
    if (!running || running === wanted) return undefined;
    return {
      key: `single:${wanted}:${running}`,
      variant: "warning",
      message: `The server runs ${running}, not ${wanted}. The answers come from ${running}. ` +
        `Change the model in the CARL dashboard (Settings > Server). ` +
        `To switch models from OpenCode, turn on router mode (Settings > Router).`,
    };
  }
  const m = s.models.find((x) => x.id === wanted);
  if (!m) {
    const have = s.models.map((x) => x.id).join(", ") || "no models";
    return {
      key: `missing:${wanted}`,
      variant: "error",
      message: `The server does not have ${wanted}. It has ${have}. ` +
        `To update the model list of OpenCode, use the Connect tab of the dashboard, ` +
        `or run ./carl.sh install --config-only.`,
    };
  }
  if (m.status === "loaded" || m.status === "sleeping") return undefined;
  return {
    key: `loading:${wanted}`,
    variant: "info",
    message: `CARL loads ${wanted} for you. The other model stops first. This takes 30 s to 2 min.`,
  };
}

/**
 * GET {baseURL}/models with the key, the parsed state, undefined on any failure (no server,
 * a timeout, an unexpected answer): the check never gets in the way of a request.
 * @param {string} baseURL
 * @param {string | undefined} key
 * @param {number} [timeoutMs]
 * @returns {Promise<ServerState | undefined>}
 */
export async function serverModels(baseURL, key, timeoutMs = 1500) {
  try {
    const res = await fetch(baseURL.replace(/\/+$/, "") + "/models", {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return undefined;
    return parseModels(await res.json());
  } catch {
    return undefined;
  }
}

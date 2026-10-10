// @ts-check
// Is this address CARL's dashboard API? Asked before a client sends its API key there (Phase 23.5 item 7): another
// program can hold the port (an ssh SOCKS proxy held 8081), and the key must not go to it. carl-cache (OpenCode's
// plugin, Pi's extension) and carl-delegation each carry a copy (client/configure.py).
//
// GET /carl/ping needs no key and answers { carl: ID, version }. A client that knows the ID (its package's remote.json
// "id") accepts only that ID. A client set up before Phase 23.5 knows none: it accepts any CARL API, and an older dashboard
// (no /carl/ping: 401) when its reply carries CARL's X-Carl-Version header.

const GOOD_MS = 60_000;      // an answer that said yes is trusted this long
const BAD_MS = 10_000;       // ...one that said no, this long (then asked again: the dashboard may have started)

/** @type {Map<string, { at: number, ok: boolean }>} */
const seen = new Map();

/**
 * @param {string} base the API's address (http://HOST:PORT)
 * @param {string} [expected] the ID the client knows ("" or undefined: any CARL)
 * @param {typeof fetch} [fetchFn]
 * @returns {Promise<boolean>}
 */
export async function isCarl(base, expected, fetchFn = globalThis.fetch) {
  const k = `${base}\n${expected ?? ""}`;
  const s = seen.get(k);
  if (s && Date.now() - s.at < (s.ok ? GOOD_MS : BAD_MS)) return s.ok;
  let ok = false;
  try {
    const r = await fetchFn(String(base).replace(/\/+$/, "") + "/carl/ping", { signal: AbortSignal.timeout(2_000) });
    if (r.ok) {
      const j = /** @type {{ carl?: unknown }} */ (await r.json());
      ok = typeof j?.carl === "string" && (!expected || j.carl === expected);
    } else {
      ok = !expected && r.status === 401 && Boolean(r.headers.get("x-carl-version"));   // an older CARL dashboard
    }
  } catch {
    ok = false;
  }
  seen.set(k, { at: Date.now(), ok });
  return ok;
}

/** Forget what was asked (tests). */
export function forgetPings() {
  seen.clear();
}

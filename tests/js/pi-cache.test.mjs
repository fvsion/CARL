// client/pi/extensions/carl-cache/index.ts (Phase 23.4.5, checklist item 6): Pi's disk cache acts only on requests to
// CARL's providers (carl.json "providers"). A request of the coder on an external model (another provider): nothing
// saved, restored, claimed or marked, no key asked for, no call to CARL's server or the dashboard API. The extension is
// staged as the installer lays it out (index.ts next to carl-cache.js), with Pi's events played by hand and every
// fetch recorded. Run: node --test tests/js.
import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const HOME = mkdtempSync(join(tmpdir(), "carl-pi-cache-"));
process.env.HOME = HOME;
const agentDir = join(HOME, ".pi", "agent");
mkdirSync(agentDir, { recursive: true });
writeFileSync(join(agentDir, "carl.json"), JSON.stringify({ providers: { llamacpp: "llamacpp" }, cache_api: "http://10.0.0.1:8081" }));
process.env.PI_CODING_AGENT_DIR = agentDir;

const dir = mkdtempSync(join(tmpdir(), "carl-pi-cache-ext-"));
copyFileSync(join(REPO, "client/pi/extensions/carl-cache/index.ts"), join(dir, "index.ts"));
copyFileSync(join(REPO, "client/shared/carl-cache.js"), join(dir, "carl-cache.js"));

/** Every fetch: the URL. CARL's fake server answers the few calls a CARL request makes. */
const seen = [];
globalThis.fetch = async (input) => {
  const u = new URL(String(input));
  seen.push(u.href);
  const json = (x) => new Response(JSON.stringify(x), { headers: { "Content-Type": "application/json" } });
  if (u.pathname === "/slots") return json([{ id: 0, is_processing: false, n_prompt_tokens: 0, id_task: 1 }]);
  if (u.pathname === "/props") return json({ model_path: "/m.gguf", build_info: "b1" });
  if (u.pathname === "/apply-template") return json({ prompt: "short" });
  if (u.pathname === "/tokenize") return json({ tokens: [1, 2, 3] });
  return json({ ok: true });
};

const handlers = {};
const ext = (await import(pathToFileURL(join(dir, "index.ts")).href)).default;
ext({ on: (name, f) => { handlers[name] = f; } });

/** Pi's ctx for a request on `model`; asking for the key is recorded. */
function ctx(model, asked) {
  return {
    model,
    modelRegistry: { getApiKeyAndHeaders: async (m) => (asked.push(`${m.provider}/${m.id}`), { ok: true, apiKey: "k" }) },
    sessionManager: { getSessionId: () => "s1", getSessionFile: () => "/tmp/s1.jsonl" },
  };
}
const payload = () => ({ model: "x", stream: true, messages: [{ role: "system", content: "sys" }, { role: "user", content: "hi" }] });
const files = () => (existsSync(join(HOME, ".config", "carl")) ? readdirSync(join(HOME, ".config", "carl"), { recursive: true }).sort() : []);

test("Pi carl-cache: a request to another provider is left alone (no key asked, no call, nothing saved or claimed)", async () => {
  const asked = [];
  const before = files();
  const c = ctx({ provider: "openrouter", id: "example-coder-32b", baseUrl: "https://openrouter.ai/api/v1" }, asked);
  const p = payload();
  assert.equal(await handlers.before_provider_request({ payload: p }, c), undefined);   // the payload goes as it is
  assert.deepEqual(p, payload());
  await handlers.after_provider_response({}, c);
  await handlers.message_end({ message: { role: "assistant", stopReason: "stop" } }, c);   // the end of a turn: no save
  await handlers.agent_end({}, c);
  assert.deepEqual([seen, asked], [[], []]);
  assert.deepEqual(files(), before);
});

test("Pi carl-cache: a request to CARL's provider is still prepared (the same extension, the same run)", async () => {
  const asked = [];
  const c = ctx({ provider: "llamacpp", id: "m", baseUrl: "http://10.0.0.1:8080/v1" }, asked);
  const out = await handlers.before_provider_request({ payload: payload() }, c);
  assert.equal(out.id_slot, 0);                                                        // pinned to a slot
  assert.deepEqual(asked, ["llamacpp/m"]);
  assert.ok(seen.some((u) => u.startsWith("http://10.0.0.1:8080/")), seen.join());
  await handlers.after_provider_response({}, c);
  // after a CARL request, a request to another provider is still left alone
  seen.length = 0;
  const o = ctx({ provider: "openrouter", id: "example-coder-32b", baseUrl: "https://openrouter.ai/api/v1" }, asked);
  assert.equal(await handlers.before_provider_request({ payload: payload() }, o), undefined);
  assert.deepEqual([seen, asked], [[], ["llamacpp/m"]]);
});

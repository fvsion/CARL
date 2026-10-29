// client/pi/extensions/carl-model-check/index.ts (Phase 23.5): Pi's model check, its own extension as OpenCode's
// plugin. Staged as the installer lays it out (index.ts next to carl-model-check.js), with Pi's event played by hand.
// Run: node --test tests/js.
import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const agentDir = mkdtempSync(join(tmpdir(), "carl-pi-check-agent-"));
writeFileSync(join(agentDir, "carl.json"), JSON.stringify({ providers: { llamacpp: "llamacpp" } }));
process.env.PI_CODING_AGENT_DIR = agentDir;
const dir = mkdtempSync(join(tmpdir(), "carl-pi-check-ext-"));
copyFileSync(join(REPO, "client/pi/extensions/carl-model-check/index.ts"), join(dir, "index.ts"));
copyFileSync(join(REPO, "client/shared/carl-model-check.js"), join(dir, "carl-model-check.js"));

const asked = [];
globalThis.fetch = async (input, init) => {
  asked.push([String(input), init?.headers?.Authorization ?? ""]);
  return new Response(JSON.stringify({ data: [{ id: "the-server-model" }] }));
};

let ext;
let why = "";
try {
  ext = (await import(pathToFileURL(join(dir, "index.ts")).href)).default;
} catch (e) {
  why = String(e?.code ?? e);
  if (!/UNKNOWN_FILE_EXTENSION/.test(why) || String(e?.message ?? "").includes(dir)) throw e;
}

function setUp() {
  const handlers = {};
  ext({ on: (name, f) => { handlers[name] = f; } });
  return handlers;
}
const ctx = (model, notes) => ({ model, ui: { notify: (m, t) => notes.push([m, t]) },
                                 modelRegistry: { getApiKeyAndHeaders: async () => ({ ok: true, apiKey: "k" }) } });

test("Phase 23.5: Pi's model check says once that the server runs another model; another provider is not checked", async (t) => {
  if (!ext) return t.skip(`needs type stripping: ${why}`);
  const h = setUp();
  const notes = [];
  const ours = ctx({ provider: "llamacpp", id: "x", baseUrl: "http://10.0.0.1:9275/v1" }, notes);
  await h.before_provider_request({ payload: {} }, ours);
  await h.before_provider_request({ payload: {} }, ours);
  assert.equal(notes.length, 1);
  assert.match(notes[0][0], /^The server runs the-server-model, not x\. .*from Pi, turn on router mode/);
  assert.equal(notes[0][1], "warning");
  assert.deepEqual(asked, [["http://10.0.0.1:9275/v1/models", "Bearer k"]]);     // one ask (kept 5 s), CARL's key
  asked.length = 0;
  await h.before_provider_request({ payload: {} }, ctx({ provider: "openrouter", id: "y", baseUrl: "https://openrouter.ai/api/v1" }, notes));
  assert.deepEqual(asked, []);                                                    // an external coder: nothing asked
});

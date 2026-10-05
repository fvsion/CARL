// The OpenCode server plugins: carl-background, carl-model-check (check.js) and carl-cache (its hooks and
// its fetch wrapper, staged with the shared files as the installer lays them out, against a fake server).
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import background from "../../client/opencode/plugins/carl-background/index.js";
import { parseModels, verdict } from "../../client/opencode/plugins/carl-model-check/check.js";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const HOME = mkdtempSync(join(tmpdir(), "carl-plugins-"));
process.env.HOME = HOME;                     // carl-panel.js and carl-cache.js read ~/.config/carl: an empty one

test("carl-background: CARL's coder goes to the background unless the call says", async () => {
  const hooks = await background.server();
  const call = async (args, tool = "task") => {
    const output = { args: { ...args } };
    await hooks["tool.execute.before"]({ tool }, output);
    return output.args.background;
  };
  assert.equal(await call({ subagent_type: "coder" }), true);
  assert.equal(await call({ subagent_type: "carl-coder" }), true);
  assert.equal(await call({ subagent_type: "coder", background: false }), false);
  assert.equal(await call({ subagent_type: "coder", task_id: "t1" }), undefined);   // continues an earlier task
  assert.equal(await call({ subagent_type: "explore" }), undefined);
  assert.equal(await call({ subagent_type: "coder" }, "bash"), undefined);
});

test("carl-model-check: the warning for each situation", () => {
  const single = parseModels({ data: [{ id: "a" }] });
  assert.deepEqual(single, { router: false, models: [{ id: "a", status: undefined }] });
  assert.equal(verdict("a", single), undefined);
  assert.equal(verdict("b", single).variant, "warning");
  const router = parseModels({ data: [{ id: "a", status: { value: "loaded" } }, { id: "b", status: { value: "unloaded" } }, 7, { id: 3 }] });
  assert.equal(router.router, true);
  assert.equal(router.models.length, 2);                                   // the entries that are not models are left out
  assert.equal(verdict("a", router), undefined);
  assert.equal(verdict("b", router).variant, "info");
  assert.equal(verdict("c", router).variant, "error");
  assert.equal(parseModels({ nope: 1 }), undefined);
});

test("carl-model-check: short sentences, one path notation (Settings > Router), no arrow", () => {
  const single = parseModels({ data: [{ id: "a" }] });
  const router = parseModels({ data: [{ id: "a", status: { value: "loaded" } }, { id: "b", status: { value: "unloaded" } }] });
  const said = [verdict("b", single).message, verdict("b", router).message, verdict("c", router).message];
  assert.match(said[0], /^The server runs a, not b\. .*Settings > Router/);
  assert.match(said[2], /^The server does not have c\. It has a, b\./);
  for (const m of said) {
    assert.ok(!m.includes("→"), m);
    for (const sentence of m.split(/(?<=\.) /)) assert.ok(sentence.split(" ").length <= 20, sentence);
  }
  assert.match(verdict("c", { router: true, models: [] }).message, /It has no models\./);
});

// ------------------------------------------------------------------ carl-cache (OpenCode)

/** A fake llama-server: one idle slot; a chat reply that ends the turn. */
function fakeServer() {
  const seen = [];
  const json = (x) => new Response(JSON.stringify(x), { headers: { "Content-Type": "application/json" } });
  const fetch = async (input, init = {}) => {
    const u = new URL(String(input));
    seen.push({ path: u.pathname, headers: new Headers(init.headers), body: init.body });
    if (u.pathname === "/v1/models") return json({ data: [{ id: "m" }] });
    if (u.pathname === "/slots") return json([{ id: 0, is_processing: false, n_prompt_tokens: 0, id_task: 1 }]);
    if (u.pathname === "/props") return json({ model_path: "/m.gguf", build_info: "b1" });
    if (u.pathname === "/apply-template") return json({ prompt: "short" });
    if (u.pathname === "/tokenize") return json({ tokens: [1, 2, 3] });
    if (u.pathname.endsWith("/chat/completions")) {
      return new Response('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
                          { headers: { "Content-Type": "text/event-stream" } });
    }
    return json({ ok: true });
  };
  return { seen, fetch };
}

const srv = fakeServer();
globalThis.fetch = srv.fetch;                // the plugin wraps the fetch it finds
const dir = mkdtempSync(join(tmpdir(), "carl-cache-plugin-"));
copyFileSync(join(REPO, "client/opencode/plugins/carl-cache/index.js"), join(dir, "index.js"));
for (const f of ["carl-cache.js", "carl-panel.js"]) copyFileSync(join(REPO, "client/shared", f), join(dir, f));
const plugin = (await import(pathToFileURL(join(dir, "index.js")).href)).default;
const client = {
  app: { log: async () => ({}) },
  tui: { showToast: async () => ({}) },
  session: { get: async ({ path }) => ({ data: { id: path.id, parentID: path.id === "child" ? "main" : undefined } }) },
};
const hooks = await plugin.server({ client }, { provider: "llamacpp" });

const mark = async (sessionID, providerID = "llamacpp", agent = "build") => {
  const output = { headers: {} };
  await hooks["chat.headers"]({ sessionID, agent, model: { providerID } }, output);
  return output.headers["x-carl-cache"];
};

test("carl-cache: chat.headers marks only CARL's provider, with the session, the agent and a subagent's flag", async () => {
  assert.equal(await mark("s1", "openai"), undefined);
  assert.deepEqual(JSON.parse(await mark("s1")), { session: "s1", agent: "build", sub: false });
  assert.deepEqual(JSON.parse(await mark("child", "llamacpp", "coder")), { session: "child", agent: "coder", sub: true });
});

test("carl-cache: a marked chat request is pinned to a slot, and the mark does not reach the server", async () => {
  srv.seen.length = 0;
  const body = JSON.stringify({ model: "m", stream: true, messages: [{ role: "system", content: "sys" }, { role: "user", content: "hi" }] });
  const res = await fetch("http://10.0.0.1:8080/v1/chat/completions",
                          { method: "POST", headers: { "x-carl-cache": await mark("s1"), Authorization: "Bearer k" }, body });
  await res.text();                                                        // the stream ends: the turn's end runs
  const chat = srv.seen.find((x) => x.path.endsWith("/chat/completions"));
  assert.equal(chat.headers.get("x-carl-cache"), null);
  assert.equal(JSON.parse(chat.body).id_slot, 0);
  assert.equal(chat.headers.get("authorization"), "Bearer k");
});

test("carl-cache: other requests go as they are (a marked one that is not a chat request loses only the mark)", async () => {
  srv.seen.length = 0;
  await fetch("http://10.0.0.1:8080/v1/embeddings", { method: "POST", headers: { "x-carl-cache": await mark("s1") }, body: "{\"x\":1}" });
  await fetch("http://10.0.0.1:8080/v1/chat/completions", { method: "POST", body: "{\"y\":2}" });
  assert.deepEqual(srv.seen.map((x) => [x.path, x.body, x.headers.get("x-carl-cache")]),
                   [["/v1/embeddings", "{\"x\":1}", null], ["/v1/chat/completions", "{\"y\":2}", null]]);
});

test("carl-cache: a broken mark leaves the request as it was", async () => {
  srv.seen.length = 0;
  await fetch("http://10.0.0.1:8080/v1/chat/completions", { method: "POST", headers: { "x-carl-cache": "{not json" }, body: "{\"z\":3}" });
  assert.deepEqual(srv.seen.map((x) => [x.path, x.body]), [["/v1/chat/completions", "{\"z\":3}"]]);
});

test("carl-cache: an idle session's turn ends without an error", async () => {
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } });
  await hooks.event({ event: { type: "session.status", properties: { sessionID: "s1", status: { type: "idle" } } } });
  await hooks.event({ event: { type: "message.updated", properties: {} } });
});

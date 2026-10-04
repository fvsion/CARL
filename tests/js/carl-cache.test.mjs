// client/shared/carl-cache.js: the pure parts, and before() / after() against a fake llama-server.
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  CarlCache, chooseSlot, commonPrefix, overBudget, prefixFile, relocate, sessionFile, splitOpenCode, splitPi, thinkingOff,
} from "../../client/shared/carl-cache.js";

const OC_SYSTEM = [
  "You are opencode.\nYou are powered by the model named m.\n",
  "Here is some useful information about the environment you are running in:\n<env>\n",
  "  Working directory: /work/app\n  Workspace root folder: /work/app\n  Today's date: Sat Oct 03 2026\n</env>\n",
  "Instructions from: /home/u/.config/opencode/carl/delegation.md\nDelegate big jobs.\n",
  "Instructions from: /work/app/AGENTS.md\nUse tabs.\n",
  "\n\nSkills provide specialized instructions and workflows.\n<available_skills></available_skills>",
].join("");

test("OpenCode: the env block and the project's instructions move, the rest stays as it was", () => {
  const [kept, moved] = splitOpenCode(OC_SYSTEM);
  assert.equal(kept, "You are opencode.\nYou are powered by the model named m.\n" +
    "Instructions from: /home/u/.config/opencode/carl/delegation.md\nDelegate big jobs.\n" +
    "\n\nSkills provide specialized instructions and workflows.\n<available_skills></available_skills>");
  assert.match(moved, /^Here is some useful information[\s\S]*<\/env>\n\nInstructions from: \/work\/app\/AGENTS.md\nUse tabs\.$/);
  assert.deepEqual(splitOpenCode("no env here"), ["no env here", ""]);
});

test("Pi: the project context and the folder move", () => {
  const sys = "<rules>r</rules>\n\n<project_context>\nP\n</project_context>\n\n<cwd>\n/w\n</cwd>\n\n<mcp_servers>m</mcp_servers>";
  assert.deepEqual(splitPi(sys), ["<rules>r</rules>\n\n<mcp_servers>m</mcp_servers>",
                                  "<project_context>\nP\n</project_context>\n\n<cwd>\n/w\n</cwd>"]);
});

test("relocate puts the moved text in front of the first user message (text or parts)", () => {
  const p = { model: "m", messages: [{ role: "system", content: OC_SYSTEM }, { role: "user", content: "hi" }] };
  const out = relocate(p, splitOpenCode);
  assert.ok(!out.messages[0].content.includes("<env>"));
  assert.match(out.messages[1].content, /^Here is[\s\S]*Use tabs\.\n\nhi$/);
  assert.equal(p.messages[1].content, "hi");                                      // the original is untouched
  const parts = relocate({ messages: [{ role: "system", content: OC_SYSTEM }, { role: "user", content: [{ type: "text", text: "hi" }] }] },
                         splitOpenCode);
  assert.equal(parts.messages[1].content.length, 2);
  assert.equal(relocate({ messages: [{ role: "user", content: "x" }] }, splitOpenCode).messages[0].content, "x");
});

test("small helpers", () => {
  assert.equal(commonPrefix([1, 2, 3], [1, 2, 4]), 2);
  assert.deepEqual(chooseSlot([{ id: 0, busy: false, n: 50 }, { id: 1, busy: false, n: 0 }, { id: 2, busy: true, n: 0 }]),
                   { id: 1, busy: false, n: 0 });
  assert.equal(chooseSlot([{ id: 0, busy: true, n: 0 }]), undefined);
  assert.ok(thinkingOff({ reasoning_effort: "none" }) && thinkingOff({ chat_template_kwargs: { enable_thinking: false } }));
  assert.ok(!thinkingOff({ chat_template_kwargs: { enable_thinking: true } }));
  assert.equal(sessionFile("q/m", "k", "ses 1"), "carl-session+q_m+k+ses_1.bin");
  assert.equal(prefixFile("m", "pi-coder", "ab"), "carl-prefix+m+pi-coder+ab.bin");
});

test("the disk limit removes the oldest conversations first, then prompts, and the new file last", () => {
  const f = (name, mtime, bytes = 3) => ({ name, bytes, mtime });
  const files = [f("carl-prefix+a.bin", 1), f("carl-session+a.bin", 2), f("carl-session+b.bin", 3), f("carl-prefix+b.bin", 4)];
  assert.deepEqual(overBudget(files, 12), []);
  assert.deepEqual(overBudget(files, 6), ["carl-session+a.bin", "carl-session+b.bin"]);
  assert.deepEqual(overBudget(files, 6, "carl-session+a.bin"), ["carl-session+b.bin", "carl-prefix+a.bin"]);
  assert.deepEqual(overBudget([f("carl-session+a.bin", 1, 9)], 5, "carl-session+a.bin"), ["carl-session+a.bin"]);
});

// ------------------------------------------------------------------ a fake llama-server

/** Tokens are character codes; a template is "S:<system>|T:<tools>|U:<user>|A:<assistant>…|G:" */
function fakeServer({ router = false, loaded = true, slots = 2 } = {}) {
  const st = {
    slots: Array.from({ length: slots }, (_, id) => ({ id, busy: false, n: 0, task: -1 })),
    files: new Map(), calls: [], task: 0, loaded,
  };
  const render = (b) => b.messages.map((m) => `${m.role[0].toUpperCase()}:${typeof m.content === "string" ? m.content : JSON.stringify(m.content)}` +
    (m.reasoning_content ? `~${m.reasoning_content}` : "")).join("|") + (b.tools ? `|T:${JSON.stringify(b.tools)}` : "") + "|G:";
  const json = (x, status = 200) => new Response(JSON.stringify(x), { status, headers: { "Content-Type": "application/json" } });
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : undefined;
    const path = u.pathname + (u.searchParams.get("action") ? `?${u.searchParams.get("action")}` : "");
    st.calls.push({ path, body });
    if (path === "/v1/models") return json({ data: [{ id: "m", ...(router ? { status: { value: st.loaded ? "loaded" : "unloaded" } } : {}) }] });
    if (path === "/models/load") { st.loaded = true; return json({ success: true }); }
    if (path === "/slots") return json(st.slots.map((s) => ({ id: s.id, is_processing: s.busy, n_prompt_tokens: s.n, id_task: s.task })));
    if (path === "/props") return json({ model_path: "/models/m.gguf", build_info: "b1" });
    if (path === "/apply-template") return json({ prompt: render(body) });
    if (path === "/tokenize") return json({ tokens: [...body.content].map((c) => c.charCodeAt(0)) });
    if (path === "/completion") {
      const s = st.slots[body.id_slot];
      s.n = body.prompt.length; s.task = st.task++;
      return json({ content: "" });
    }
    const m = u.pathname.match(/^\/slots\/(\d+)$/);
    if (m) {
      const s = st.slots[Number(m[1])];
      if (path.endsWith("?save")) { st.files.set(body.filename, s.n); return json({ n_saved: s.n }); }
      if (path.endsWith("?restore")) {
        if (!st.files.has(body.filename)) return json({ error: { message: "no file" } }, 400);
        s.n = st.files.get(body.filename);
        return json({ n_restored: s.n });
      }
    }
    return json({ error: "unexpected " + path }, 404);
  };
  return { st, fetch };
}

// a request ran in the slot: its tokens, and the server's next task id (ids only go up, as in llama.cpp)
const ran = (srv, slot, n) => {
  srv.st.slots[slot].n = n;
  srv.st.slots[slot].task = srv.st.task++;
};
const LONG = "You are opencode. " + "Follow the rules. ".repeat(400);
const request = (user, extra = {}) => ({ model: "m", messages: [{ role: "system", content: LONG + OC_SYSTEM }, { role: "user", content: user }],
                                          tools: [{ type: "function", function: { name: "read" } }], ...extra });
const home = () => {
  const h = mkdtempSync(join(tmpdir(), "carl-cache-"));
  mkdirSync(join(h, ".config", "carl", "slots"), { recursive: true });
  return h;
};
// a request: prepared, then sent (the slot is released for this process's next request)
const send = async (c, p, meta) => {
  const r = await c.before(p, meta);
  r.release();
  return r;
};
// the server's files are kept in the fake; "remote" makes the cache try them instead of looking on disk
const cache = (fetch, h = home()) => new CarlCache({ baseURL: "http://10.0.0.1:8080/v1", fetch, split: splitOpenCode, home: h });

test("a new agent prompt is read once and saved; the request is pinned to that slot", async () => {
  const srv = fakeServer();
  const c = cache(srv.fetch);
  const { payload } = await send(c, request("hello"), { session: "s1", agent: "build" });
  assert.equal(payload.id_slot, 0);
  assert.ok(!payload.messages[0].content.includes("<env>"));
  const saved = [...srv.st.files.keys()];
  assert.equal(saved.length, 1);
  assert.match(saved[0], /^carl-prefix\+m\+build\+[0-9a-f]{12}\.bin$/);
  // the prefix is exactly the system text and tools up to the user's message
  const fill = String.fromCharCode(...srv.st.calls.find((x) => x.path === "/completion").body.prompt);
  assert.ok(fill.startsWith("S:You are opencode.") && fill.endsWith("|U:"));
});

test("the next request of the session stays in its slot without a restore", async () => {
  const srv = fakeServer();
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 6000);                        // the request ran there
  srv.st.calls.length = 0;
  const { payload } = await send(c, request("again"), { session: "s1", agent: "build" });
  assert.equal(payload.id_slot, 0);
  assert.ok(!srv.st.calls.some((x) => x.path.includes("restore")));
});

test("after a turn the session is saved; a new client process restores it before the next request", async () => {
  const srv = fakeServer();
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 6000);
  await c.after({ session: "s1", agent: "*" });
  const file = [...srv.st.files.keys()].find((n) => n.startsWith("carl-session+"));
  assert.match(file, /^carl-session\+m\+[0-9a-f]{10}\+s1\.bin$/);
  await c.after({ session: "s1", agent: "*" });                               // nothing new: no second save
  assert.equal(srv.st.calls.filter((x) => x.path.endsWith("?save") && x.body.filename === file).length, 1);
  srv.st.slots[0].n = 0; srv.st.slots[0].task = -1;                          // the server restarted
  const d = cache(srv.fetch);
  srv.st.calls.length = 0;
  const { payload } = await send(d, request("next"), { session: "s1", agent: "build" });
  const restored = srv.st.calls.filter((x) => x.path.endsWith("?restore")).map((x) => x.body.filename);
  assert.deepEqual(restored, [file]);                                          // the session's own state, not the prompt's
  assert.equal(payload.id_slot, 0);
});

test("a session whose slot another session took comes back from its file", async () => {
  const srv = fakeServer({ slots: 1 });
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 6000);
  await c.after({ session: "s1", agent: "build" });
  await send(c, request("other"), { session: "s2", agent: "build" });       // takes the only slot
  ran(srv, 0, 7000);
  srv.st.calls.length = 0;
  await send(c, request("back"), { session: "s1", agent: "build" });
  const restored = srv.st.calls.filter((x) => x.path.endsWith("?restore")).map((x) => x.body.filename);
  assert.equal(restored.length, 1);
  assert.match(restored[0], /^carl-session\+m\+[0-9a-f]{10}\+s1\.bin$/);
});

test("a subagent that needs the main session's slot saves the main session first", async () => {
  const srv = fakeServer({ slots: 1 });
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "main", agent: "build" });
  ran(srv, 0, 9000);                                                         // mid-turn: a tool call, no save yet
  srv.st.calls.length = 0;
  await send(c, request("sub task"), { session: "child", agent: "coder", sub: true });
  const paths = srv.st.calls.map((x) => `${x.path}${x.body?.filename ? ` ${x.body.filename.split("+")[0]}` : ""}`);
  const save = paths.indexOf("/slots/0?save carl-session");
  assert.ok(save > 0 && paths.indexOf("/completion") < save, paths.join(" "));   // set back to its last prompt, saved
  ran(srv, 0, 7000);
  srv.st.calls.length = 0;
  await send(c, request("tool result"), { session: "main", agent: "build" });
  assert.ok(srv.st.calls.some((x) => x.path.endsWith("?restore") && x.body.filename.startsWith("carl-session+")));
});

test("subagents and short conversations are not saved; title requests are not pinned", async () => {
  const srv = fakeServer();
  const c = cache(srv.fetch);
  const t = await send(c, request("title please"), { session: "s1", agent: "title" });
  assert.equal(t.payload.id_slot, undefined);
  assert.equal(t.payload.reasoning_effort, "none");                             // a title doesn't think
  assert.equal(t.payload.chat_template_kwargs.enable_thinking, false);
  await send(c, request("hello"), { session: "s2", agent: "coder", sub: true });
  srv.st.slots[0].n = 9000;
  await c.after({ session: "s2", agent: "coder", sub: true });
  await send(c, request("hello"), { session: "s3", agent: "build" });
  srv.st.slots[1].n = 100;
  await c.after({ session: "s3", agent: "*" });
  assert.ok(![...srv.st.files.keys()].some((n) => n.startsWith("carl-session+")));
});

test("a server restart (task ids start again) brings the session back from the disk", async () => {
  const srv = fakeServer();
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 6000);
  await send(c, request("x"), { session: "s1", agent: "build" });            // sees task 40
  await c.after({ session: "s1", agent: "*" });
  srv.st.slots.forEach((s) => { s.n = 0; s.task = -1; });                    // restarted
  srv.st.calls.length = 0;
  await send(c, request("again"), { session: "s1", agent: "build" });
  assert.ok(srv.st.calls.some((x) => x.path.endsWith("?restore") && x.body.filename.startsWith("carl-session+")));
});

test("router mode: the model is loaded first, and the slot calls name it", async () => {
  const srv = fakeServer({ router: true, loaded: false });
  const c = cache(srv.fetch);
  const { payload } = await send(c, request("hello"), { session: "s1", agent: "build" });
  assert.ok(srv.st.calls.some((x) => x.path === "/models/load" && x.body.model === "m"));
  assert.equal(payload.id_slot, 0);
  const save = srv.st.calls.find((x) => x.path.endsWith("?save"));
  assert.equal(save.body.model, "m");
});

test("router mode: a title request for an unloaded model goes to the loaded one", async () => {
  const srv = fakeServer({ router: true });
  const inner = srv.fetch;
  srv.fetch = async (url, init) => String(url).endsWith("/v1/models")
    ? new Response(JSON.stringify({ data: [{ id: "m", status: { value: "loaded" } }, { id: "big", status: { value: "unloaded" } }] }))
    : inner(url, init);
  const c = cache(srv.fetch);
  const { payload } = await send(c, { ...request("title"), model: "big" }, { session: "s1", agent: "title" });
  assert.equal(payload.model, "m");
  const other = await send(c, { ...request("title"), model: "nope" }, { session: "s1", agent: "title" });
  assert.equal(other.payload.model, "nope");                                   // not installed: as it was
});

test("models whose template drops the reply's reasoning are set back to the last prompt before the save", async () => {
  const srv = fakeServer();
  const inner = srv.fetch;
  // a template that drops reasoning_content from earlier replies
  srv.fetch = async (url, init) => {
    if (String(url).endsWith("/apply-template")) {
      const b = JSON.parse(init.body);
      b.messages = b.messages.map((m) => ({ ...m, reasoning_content: undefined }));
      return inner(url, { ...init, body: JSON.stringify(b) });
    }
    return inner(url, init);
  };
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 9000);
  srv.st.calls.length = 0;
  await c.after({ session: "s1", agent: "*" });
  const paths = srv.st.calls.map((x) => x.path);
  assert.ok(paths.indexOf("/completion") >= 0 && paths.indexOf("/completion") < paths.findIndex((p) => p.endsWith("?save")));
});

test("on this Mac the settings switch it off, and saves keep the disk limit", async () => {
  const h = home();
  writeFileSync(join(h, ".config", "carl", "config.json"), JSON.stringify({ cache: { prefix: false, sessions: false } }));
  const srv = fakeServer();
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const { payload } = await send(c, request("hello"), { session: "s1", agent: "build" });
  assert.equal(payload.id_slot, undefined);
  assert.equal(srv.st.calls.length, 0);
  const dir = join(h, ".config", "carl", "slots");
  writeFileSync(join(dir, "carl-session+m+0123456789+old.bin"), Buffer.alloc(10));
  writeFileSync(join(dir, "carl-session+m+0123456789+new.bin"), Buffer.alloc(10));
  writeFileSync(join(h, ".config", "carl", "config.json"), JSON.stringify({ cache: { disk_gb: 1 } }));
  const d = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  d.wrote("carl-session+m+0123456789+new.bin", "");
  assert.equal(readdirSync(dir).length, 2);                                   // 20 bytes: within 1 GB
});

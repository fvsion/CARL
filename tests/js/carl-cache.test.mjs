// client/shared/carl-cache.js: the pure parts, and before() / after() against a fake llama-server.
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  CarlCache, rankSlots, commonPrefix, isStateName, overBudget, prefixFile, relocate, sessionFile, splitOpenCode, splitPi,
  thinkingOff,
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

test("relocate: system2 adds a second system message; none leaves the request as it was", () => {
  const p = { messages: [{ role: "system", content: OC_SYSTEM }, { role: "user", content: "hi" }] };
  const two = relocate(p, splitOpenCode, "system2");
  assert.deepEqual(two.messages.map((m) => m.role), ["system", "system", "user"]);
  assert.ok(two.messages[1].content.includes("<env>") && two.messages[2].content === "hi");
  assert.equal(relocate(p, splitOpenCode, "none"), p);
  const already = { messages: [{ role: "system", content: OC_SYSTEM }, { role: "system", content: "x" }, { role: "user", content: "hi" }] };
  assert.equal(relocate(already, splitOpenCode, "system2").messages[2].content.includes("<env>"), true);  // the user message then
});

test("small helpers", () => {
  assert.equal(commonPrefix([1, 2, 3], [1, 2, 4]), 2);
  assert.deepEqual(rankSlots([{ id: 0, busy: false, n: 50 }, { id: 1, busy: false, n: 0 }, { id: 2, busy: true, n: 0 }])
                     .map((x) => x.id), [1, 0]);
  assert.deepEqual(rankSlots([{ id: 0, busy: true, n: 0 }]), []);
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

/** Tokens are character codes; a template is "S:<system>|U:<user>|G:<assistant>…|T:<tools>|G:" */
function fakeServer({ router = false, loaded = true, slots = 2, toolsFirst = false, modelPath = "/models/m.gguf" } = {}) {
  const st = {
    slots: Array.from({ length: slots }, (_, id) => ({ id, busy: false, n: 0, task: -1 })),
    files: new Map(), calls: [], task: 0, loaded,
  };
  // the generation prompt "G:" is how a reply starts, so a reply re-renders as generated (a stable template)
  // toolsFirst: a Qwen-like template (the tools first, then each system message: a second one comes after them)
  const tools = (b) => (b.tools ? `T:${JSON.stringify(b.tools)}` : "");
  const render = (b) => (toolsFirst && b.tools ? tools(b) + "|" : "") + b.messages.map((m) => `${m.role === "assistant" ? "G" : m.role[0].toUpperCase()}:${typeof m.content === "string" ? m.content : JSON.stringify(m.content)}` +
    (m.reasoning_content ? `~${m.reasoning_content}` : "")).join("|") + (!toolsFirst && b.tools ? `|${tools(b)}` : "") + "|G:";
  const json = (x, status = 200) => new Response(JSON.stringify(x), { status, headers: { "Content-Type": "application/json" } });
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : undefined;
    const path = u.pathname + (u.searchParams.get("action") ? `?${u.searchParams.get("action")}` : "");
    st.calls.push({ path, body });
    if (path === "/v1/models") return json({ data: [{ id: "m", ...(router ? { status: { value: st.loaded ? "loaded" : "unloaded" } } : {}) }] });
    if (path === "/models/load") { st.loaded = true; return json({ success: true }); }
    if (path === "/slots") return json(st.slots.map((s) => ({ id: s.id, is_processing: s.busy, n_prompt_tokens: s.n, id_task: s.task })));
    if (path === "/props") return json({ model_path: modelPath, build_info: "b1" });
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
process.env.CARL_CACHE_SAVE = "turn";      // most tests: a save after every turn (the policy tests set their own)

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
  assert.ok(payload.messages[0].content.includes("<env>"));              // a template CARL does not know: in place
  const saved = [...srv.st.files.keys()];
  assert.equal(saved.length, 1);
  assert.match(saved[0], /^carl-prefix\+m\+build\+[0-9a-f]{12}\.bin$/);
  // the prefix is exactly the system text and tools up to the user's message
  const fill = String.fromCharCode(...srv.st.calls.find((x) => x.path === "/completion").body.prompt);
  assert.ok(fill.startsWith("S:You are opencode.") && fill.endsWith("|U:"));
});

test("Phase 23.1: a Qwen-like template gets the project part as a second system message; the saved prompt ends where it starts", async () => {
  const srv = fakeServer({ toolsFirst: true });
  const c = cache(srv.fetch);
  const { payload } = await send(c, request("hello"), { session: "s1", agent: "build" });
  assert.deepEqual(payload.messages.map((m) => m.role), ["system", "system", "user"]);
  assert.ok(!payload.messages[0].content.includes("<env>"));
  assert.ok(payload.messages[1].content.includes("<env>"));                 // system text again, not the user's
  assert.equal(payload.messages[2].content, "hello");
  const fill = String.fromCharCode(...srv.st.calls.find((x) => x.path === "/completion").body.prompt);
  assert.ok(fill.startsWith("T:") && fill.endsWith("|S:"), fill.slice(-40));  // the tools, the shared text, up to the 2nd
  // the same shared start for another project: the saved prompt is restored, not read again
  const d = cache(srv.fetch);
  const other = request("hi", { messages: [{ role: "system", content: (LONG + OC_SYSTEM).replace(/Working directory: \S+/, "Working directory: /other") }, { role: "user", content: "hi" }] });
  srv.st.calls.length = 0;
  await send(d, other, { session: "s2", agent: "build" });
  assert.ok(!srv.st.calls.some((x) => x.path === "/completion"), "the shared prompt was read again");
});

test("Phase 23.1: Gemma 4 keeps the project part where the client put it", async () => {
  const srv = fakeServer({ toolsFirst: true, modelPath: "/models/gemma-4-12B-it-Q4_0.gguf" });
  const { payload } = await send(cache(srv.fetch), request("hello"), { session: "s1", agent: "build" });
  assert.equal(payload.messages.length, 2);
  assert.ok(payload.messages[0].content.includes("<env>"));
  assert.equal(payload.messages[1].content, "hello");
});

test("Phase 23.1: by default (cache.move off) an unknown template's project part stays in place; Qwen keeps the second system message", async () => {
  process.env.CARL_CACHE_MOVE = "off";
  try {
    const other = await send(cache(fakeServer().fetch), request("hello"), { session: "s1", agent: "build" });
    assert.equal(other.payload.messages.length, 2);
    assert.ok(other.payload.messages[0].content.includes("<env>"));
    assert.equal(other.payload.messages[1].content, "hello");
    const qwen = await send(cache(fakeServer({ toolsFirst: true }).fetch), request("hello"), { session: "s1", agent: "build" });
    assert.deepEqual(qwen.payload.messages.map((m) => m.role), ["system", "system", "user"]);
  } finally {
    delete process.env.CARL_CACHE_MOVE;
  }
});

test("Phase 23.1: cache.move auto (CARL_CACHE_MOVE=auto) moves an unknown template's project part to the first user message", async () => {
  process.env.CARL_CACHE_MOVE = "auto";
  try {
    const srv = fakeServer();                                                // the tools after the messages
    const { payload } = await send(cache(srv.fetch), request("hello"), { session: "s1", agent: "build" });
    assert.deepEqual(payload.messages.map((m) => m.role), ["system", "user"]);
    assert.ok(payload.messages[1].content.includes("<env>") && payload.messages[1].content.endsWith("hello"));
  } finally {
    delete process.env.CARL_CACHE_MOVE;
  }
});

test("Phase 23.1: the default leaves an unknown template's project part in place", async () => {
  const { payload } = await send(cache(fakeServer().fetch), request("hello"), { session: "s1", agent: "build" });
  assert.equal(payload.messages.length, 2);
  assert.ok(payload.messages[0].content.includes("<env>"));
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

// ------------------------------------------------------------------ the save policy and the slot claims

const withSave = async (mode, fn) => {
  const was = process.env.CARL_CACHE_SAVE;
  process.env.CARL_CACHE_SAVE = mode;
  try {
    await fn();
  } finally {
    process.env.CARL_CACHE_SAVE = was;
  }
};
const sessionSaves = (srv) => srv.st.calls.filter((x) => x.path.endsWith("?save") && x.body.filename.startsWith("carl-session+"));

test("auto saves a session once its unsaved part would take ~2 min to read again", () => withSave("auto", async () => {
  const srv = fakeServer();
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "s1", agent: "build" });     // the build prompt: ~7.2K tokens put in
  c.models.get("m").speed = 1000 / 120;                                   // 2 min = 1,000 tokens
  ran(srv, 0, 7700);
  await c.after({ session: "s1", agent: "build" });
  assert.equal(sessionSaves(srv).length, 0);                              // ~500 new tokens: not yet
  ran(srv, 0, 9000);
  await c.after({ session: "s1", agent: "build" });
  assert.equal(sessionSaves(srv).length, 1);
  ran(srv, 0, 9300);
  await c.after({ session: "s1", agent: "build" });
  assert.equal(sessionSaves(srv).length, 1);                              // 300 since the save
}));

test("save = stop: a session isn't saved when its slot is needed (it stays in the RAM cache)", () => withSave("stop", async () => {
  const srv = fakeServer({ slots: 1 });
  const c = cache(srv.fetch);
  await send(c, request("hello"), { session: "main", agent: "build" });
  ran(srv, 0, 9000);
  await send(c, request("sub"), { session: "child", agent: "coder", sub: true });
  assert.equal(sessionSaves(srv).length, 0);
}));

test("on this Mac a turn leaves a record of the slot's session; a router switch saves it first", () => withSave("switch", async () => {
  const h = home();
  const srv = fakeServer({ router: true });
  const inner = srv.fetch;
  let loaded = "m";
  srv.fetch = async (url, init) => {
    const u = String(url);
    if (u.endsWith("/v1/models")) {
      return new Response(JSON.stringify({ data: ["m", "big"].map((id) => ({ id, status: { value: id === loaded ? "loaded" : "unloaded" } })) }));
    }
    if (u.endsWith("/models/load")) loaded = JSON.parse(init.body).model;
    return inner(url, init);
  };
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  await send(c, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 9000);
  await c.after({ session: "s1", agent: "build" });
  assert.equal(sessionSaves(srv).length, 0);                              // switch: not after the turn
  const dir = join(h, ".config", "carl", "slots");
  const rec = readdirSync(dir).filter((n) => n.startsWith(".resident+"));
  assert.deepEqual(rec, [".resident+m+0.json"]);
  await send(c, { ...request("x"), model: "big" }, { session: "s2", agent: "build" });
  assert.equal(sessionSaves(srv).length, 1);                              // saved before "m" stopped
  assert.equal(sessionSaves(srv)[0].body.model, "m");
  assert.deepEqual(readdirSync(dir).filter((n) => n.startsWith(".resident+")), []);
}));

test("a new process finds a session still in its slot through the record", () => withSave("auto", async () => {
  const h = home();
  const srv = fakeServer();
  const opts = { baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h };
  const a = new CarlCache(opts);
  await send(a, request("hello"), { session: "s1", agent: "build" });
  ran(srv, 0, 7300);
  await a.after({ session: "s1", agent: "build" });                        // small: recorded, not saved
  const b = new CarlCache(opts);                                           // the next `opencode run`
  srv.st.calls.length = 0;
  const { payload } = await send(b, request("again"), { session: "s1", agent: "build" });
  assert.equal(payload.id_slot, 0);
  assert.ok(!srv.st.calls.some((x) => x.path.endsWith("?restore")));    // used where it is
  b.models.get("m").speed = 1000 / 120;                                   // 2 min = 1,000 tokens
  ran(srv, 0, 7600);
  await b.after({ session: "s1", agent: "build" });
  assert.equal(sessionSaves(srv).length, 0);                              // the record's base: ~400 unsaved
}));

test("a turn is marked in its slot from the first request to its save or record (subagents too)", () => withSave("turn", async () => {
  const h = home();
  const srv = fakeServer();
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const dir = join(h, ".config", "carl", "slots");
  const marks = () => readdirSync(dir).filter((n) => n.startsWith(".turn+"));
  await send(c, request("hello"), { session: "s1", agent: "build" });
  assert.deepEqual(marks(), [".turn+m+0"]);                               // a tool call or the reply: still running
  await send(c, request("sub"), { session: "k1", agent: "coder", sub: true });
  assert.deepEqual(marks().sort(), [".turn+m+0", ".turn+m+1"]);
  ran(srv, 0, 7300);
  await c.after({ session: "s1", agent: "build" });                        // saved (save = turn), then the mark goes
  assert.equal(sessionSaves(srv).length, 1);
  assert.deepEqual(marks(), [".turn+m+1"]);
  await c.turnsDone("k1");                                                 // stopped with Esc: the idle event
  assert.deepEqual(marks(), []);
}));

test("a new session takes a slot where no other turn runs, though that slot holds more", async () => {
  const h = home();
  const srv = fakeServer();
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const first = await send(c, request("main"), { session: "s1", agent: "build" });   // its turn runs (a tool now)
  assert.equal(first.payload.id_slot, 0);
  ran(srv, 0, 900);
  ran(srv, 1, 5000);                                                     // slot 1: an old, larger conversation
  const sub = await send(c, request("sub"), { session: "k1", agent: "coder", sub: true });
  assert.equal(sub.payload.id_slot, 1);                                  // not slot 0: s1's turn still runs there
});

test("two processes on this Mac don't pin the same slot", async () => {
  const h = home();
  const srv = fakeServer();
  const a = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const b = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const first = await a.before(request("one"), { session: "s1", agent: "build" });   // not released yet: in flight
  const second = await b.before(request("two"), { session: "s2", agent: "build" });
  assert.notEqual(first.payload.id_slot, second.payload.id_slot);
  const third = await b.before(request("three"), { session: "s3", agent: "build" });
  assert.equal(third.payload.id_slot, undefined);                         // both taken: llama.cpp decides
  first.release();
  second.release();
  assert.deepEqual(readdirSync(join(h, ".config", "carl", "slots")).filter((n) => n.startsWith(".claim+")), []);
});

test("a client on another computer uses the dashboard's cache API: settings, claims, records", async () => {
  const srv = fakeServer();
  const api = { claims: new Set(), records: new Map(), calls: [], turns: [] };
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    if (u.port !== "8081") return srv.fetch(url, init);
    const body = init.body ? JSON.parse(init.body) : {};
    api.calls.push(u.pathname);
    const json = (x, status = 200) => new Response(JSON.stringify(x), { status });
    if (u.pathname === "/carl/cache/settings") return json({ prefix: true, sessions: true, save: "switch", disk_gb: 10 });
    if (u.pathname === "/carl/cache/claim") {
      const k = `${body.model}:${body.slot}`;
      if (api.claims.has(k)) return json({ error: "taken" }, 409);
      api.claims.add(k);
      return json({ ok: true });
    }
    if (u.pathname === "/carl/cache/release") { api.claims.delete(`${body.model}:${body.slot}`); return json({ ok: true }); }
    if (u.pathname === "/carl/cache/turn") { api.turns.push([body.slot, body.session, body.running]); return json({ ok: true }); }
    if (u.pathname === "/carl/cache/record" && init.method === "POST") { api.records.set(body.file, body); return json({ ok: true }); }
    if (u.pathname === "/carl/cache/record") {
      const r = api.records.get(u.searchParams.get("file"));
      return r ? json({ slot: r.slot, task: r.task, base: r.base }) : json({ error: "none" }, 404);
    }
    return json({ error: "?" }, 404);
  };
  const was = process.env.CARL_CACHE_SAVE;
  delete process.env.CARL_CACHE_SAVE;
  try {
    const opts = { baseURL: "http://10.0.0.1:8080/v1", fetch, split: splitOpenCode, home: "/nonexistent",
                   cacheApi: "http://10.0.0.1:8081" };
    const a = new CarlCache(opts);
    const first = await a.before(request("hello"), { session: "s1", agent: "build" });
    assert.ok(api.claims.has("m:0"));                                        // claimed through the API
    first.release();
    await new Promise((ok) => setTimeout(ok, 10));
    assert.equal(api.claims.size, 0);
    ran(srv, 0, 7500);
    await a.after({ session: "s1", agent: "build" });                       // save = switch (the API's): a record
    assert.equal(sessionSaves(srv).length, 0);
    assert.equal(api.records.size, 1);
    assert.deepEqual(api.turns, [[0, "s1", true], [0, "s1", false]]);      // the turn ran, then ended
    const b = new CarlCache(opts);                                          // the next process
    srv.st.calls.length = 0;
    const { payload } = await send(b, request("again"), { session: "s1", agent: "build" });
    assert.equal(payload.id_slot, 0);
    assert.ok(!srv.st.calls.some((x) => x.path.endsWith("?restore")));
  } finally {
    process.env.CARL_CACHE_SAVE = was;
  }
});

test("a conversation stored as a patch is made whole before a restore (on this Mac, with zstd)", async (t) => {
  const { spawnSync } = await import("node:child_process");
  if (spawnSync("zstd", ["--version"]).status !== 0) return t.skip("zstd is not installed");
  const h = home();
  const dir = join(h, ".config", "carl", "slots");
  const shared = Buffer.alloc(400_000, 7);
  writeFileSync(join(dir, "carl-prefix+m+build+abc.bin"), shared);
  const whole = Buffer.concat([shared, Buffer.alloc(50_000, 3)]);
  const name = "carl-session+m+0123456789+s1.bin";
  writeFileSync(join(dir, name), whole);
  assert.equal(spawnSync("zstd", ["-q", "--long=31", `--patch-from=${join(dir, "carl-prefix+m+build+abc.bin")}`,
                                  join(dir, name), "-o", join(dir, `${name}.zst`)]).status, 0);
  writeFileSync(join(dir, `${name}.json`), JSON.stringify({ base: "carl-prefix+m+build+abc.bin", size: whole.length, packed_at: 1000 }));
  (await import("node:fs")).unlinkSync(join(dir, name));
  const srv = fakeServer();
  srv.st.files.set(name, 9000);
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  assert.equal(await c.restore("m", false, 0, name), 9000);
  assert.deepEqual((await import("node:fs")).readFileSync(join(dir, name)), whole);      // byte for byte
  // the budget sees it in all its forms, and a prompt that goes takes its patch with it
  assert.deepEqual(overBudget([{ name: "carl-prefix+m+build+abc.bin", bytes: 10, mtime: 1 },
                               { name, bytes: 5, mtime: 2, base: "carl-prefix+m+build+abc.bin" }], 1, "zzz").sort(),
                   ["carl-prefix+m+build+abc.bin", name].sort());
});

// ------------------------------------------------------------------ state files, names and the debug log

const mode = (path) => statSync(path).mode & 0o777;

test("the claim, turn and record files are for this user only (0600)", () => withSave("switch", async () => {
  const h = home();
  const dir = join(h, ".config", "carl", "slots");
  const srv = fakeServer();
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const r = await c.before(request("hello"), { session: "s1", agent: "build" });    // in flight: the claim is there
  assert.equal(mode(join(dir, ".claim+m+0")), 0o600);
  assert.equal(mode(join(dir, ".turn+m+0")), 0o600);
  r.release();
  ran(srv, 0, 9000);
  await c.after({ session: "s1", agent: "build" });                                   // switch: a record
  assert.equal(mode(join(dir, ".resident+m+0.json")), 0o600);
}));

test("a saved state's name is never a path", () => {
  assert.ok(isStateName(sessionFile("q/m", "k", "../../x")));
  assert.ok(isStateName("carl-prefix+m+build+abc.bin"));
  for (const bad of ["../carl-session+m+k+s.bin", "carl-session+m+k+s.bin/../../x", "/tmp/carl-session+a.bin", "x.bin", 7, undefined]) {
    assert.ok(!isStateName(bad), String(bad));
  }
});

test("a turn mark being written (.tmp) or one that goes meanwhile does not hide the others", async () => {
  const h = home();
  const dir = join(h, ".config", "carl", "slots");
  writeFileSync(join(dir, ".turn+m+0.tmp"), "{\"model\": \"m\", \"sl");             // half written (read first)
  writeFileSync(join(dir, ".turn+m+1"), JSON.stringify({ model: "m", slot: 1, session: "other" }));
  const srv = fakeServer();
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  const order = await c.rankFree("m", [{ id: 0, busy: false, n: 10, task: -1 }, { id: 1, busy: false, n: 0, task: -1 }], "mine");
  assert.deepEqual(order.map((s) => s.id), [0, 1]);                       // slot 1: another session's turn runs there
});

test("a record whose file is not a saved state's name is not saved from", () => withSave("switch", async () => {
  const h = home();
  const dir = join(h, ".config", "carl", "slots");
  const srv = fakeServer({ router: true });
  srv.st.slots[0].task = 5;
  writeFileSync(join(dir, ".resident+m+0.json"),
                JSON.stringify({ model: "m", slot: 0, file: "carl-session+../../../tmp/x.bin", task: 5, base: 0 }));
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: srv.fetch, split: splitOpenCode, home: h });
  await c.saveRecorded("m");
  assert.ok(!srv.st.calls.some((x) => x.path.endsWith("?save")));
  assert.ok(readdirSync(dir).includes(".resident+m+0.json"));            // left for the dashboard to look at
}));

test("a patch whose base is a path (outside the slots folder) is not unpacked", async (t) => {
  const { spawnSync } = await import("node:child_process");
  if (spawnSync("zstd", ["--version"]).status !== 0) return t.skip("zstd is not installed");
  const h = home();
  const dir = join(h, ".config", "carl", "slots");
  const outside = join(h, ".config", "carl", "outside.bin");             // slots/../outside.bin
  writeFileSync(outside, Buffer.alloc(1000, 7));
  const name = "carl-session+m+0123456789+s1.bin";
  writeFileSync(join(h, "whole.bin"), Buffer.alloc(1200, 7));
  assert.equal(spawnSync("zstd", ["-q", "--long=31", `--patch-from=${outside}`, join(h, "whole.bin"),
                                  "-o", join(dir, `${name}.zst`)]).status, 0);
  writeFileSync(join(dir, `${name}.json`), JSON.stringify({ base: "../outside.bin", size: 1200, packed_at: 1000 }));
  const c = new CarlCache({ baseURL: "http://127.0.0.1:8080/v1", fetch: fakeServer().fetch, home: h });
  assert.equal(await c.whole(name), false);
  assert.ok(!readdirSync(dir).includes(name));
});

test("CARL_CACHE_LOG: what the cache let go is written there (this user only), never the key", async () => {
  const h = home();
  const log = join(h, "cache.log");
  const was = process.env.CARL_CACHE_LOG;
  process.env.CARL_CACHE_LOG = log;
  try {
    const down = async () => { throw new TypeError("fetch failed"); };
    const c = new CarlCache({ baseURL: "http://10.0.0.1:8080/v1", apiKey: "sk-secret-123", fetch: down, home: "/nonexistent",
                              cacheApi: "http://10.0.0.1:8081" });
    const r = await c.api("POST", "/carl/cache/claim", { model: "m", slot: 0 });
    assert.equal(r.status, 0);                                           // the request goes on
    const { payload } = await c.before(request("hello"), { session: "s1", agent: "build" });
    assert.equal(payload.id_slot, undefined);
    const text = readFileSync(log, "utf8");
    assert.match(text, /cache API POST \/carl\/cache\/claim: fetch failed/);
    assert.match(text, /before: fetch failed/);
    assert.ok(!text.includes("sk-secret-123"));
    assert.equal(mode(log), 0o600);
  } finally {
    if (was === undefined) delete process.env.CARL_CACHE_LOG;
    else process.env.CARL_CACHE_LOG = was;
  }
});

#!/usr/bin/env python3
"""Phase 23.5's cache matrix: what carl-cache saves, under which name, what it restores and what that saves,
in each setup CARL runs, with the real OpenCode and Pi and a real llama.cpp server.

    cachematrix.py prepare --home H --repo R --models-dir M --model gemma-4-e4b
    cachematrix.py run --home H --repo R --models-dir M --out results.jsonl [--models ...] [--modes ...] ...
    cachematrix.py report results.jsonl [--md]

A cell is one setup: the model, single or router mode, the full or the window cache (cache.swa), the client's
model name the same as the server's or another (single mode: an alias the server answers anyway; router mode:
a switch to a second model and back), a main session or a subagent, OpenCode or Pi. Each cell starts with an
empty cache (a new server, no saved files) and runs a fixed script of short turns that force each path of
carl-cache: a long first turn, the slot taken by other sessions, the client restarted, a rename, a fork, the
server restarted. Each turn is one request (the prompt asks for one word and no tools), so the server log says
what was read: per step the tokens read against the tokens in the context (the reuse), what carl-cache did
(CARL_CACHE_LOG), and the files saved.

The server and the clients share one settings folder (the harness HOME's ~/.config/carl), as on a Mac that runs
both: the clients find the slots folder the server saves to. The dashboard does not run; before a server stop the
harness saves the recorded sessions the way the dashboard's Stop does (diskcache.residents under the server's
model name). The harness never uses port 8080 or your HOME (agent-bench's rules).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(TOOLS, "agent-bench"))
sys.path.insert(0, TOOLS)              # monitor and carl_core

from agentbench import clients as cl, home as hm  # noqa: E402
from agentbench.proc import Child, run  # noqa: E402
from agentbench.server import CarlServer, ServerError, ServerSetup, check_port, ensure_key  # noqa: E402
from monitor import diskcache  # noqa: E402
from monitor.logbook import LogBook, RequestRecord  # noqa: E402

AXES = ("model", "mode", "swa", "match", "agent", "client")
STATE_PREFIXES = ("carl-prefix+", "carl-session+", ".turn+", ".resident+", ".claim+")
HYBRID = ("qwen3.6-35b",)           # no sliding window: the window cache does not apply
TURN_LIMIT = 900.0


def say(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def csv(v: str) -> List[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


# ------------------------------------------------------------------------------------------------ the cells
@dataclass(frozen=True)
class Cell:
    model: str
    mode: str           # single | router
    swa: str            # full | window | none (a model with no sliding window)
    match: str          # same | other
    agent: str          # main | sub
    client: str         # opencode | pi

    def key(self) -> str:
        return "/".join(getattr(self, a) for a in AXES)

    def group(self) -> Tuple[str, str, str]:
        """Cells that run on the same server settings."""
        return (self.model, self.mode, self.swa)


def cells(models: Sequence[str], modes: Sequence[str], swas: Sequence[str], matches: Sequence[str],
          agents: Sequence[str], clients: Sequence[str]) -> List[Cell]:
    out = []
    for model in models:
        hybrid = any(model.startswith(h) for h in HYBRID)
        for mode in modes:
            for swa in (["none"] if hybrid else swas):
                for match in matches:
                    for agent in agents:
                        for client in clients:
                            out.append(Cell(model, mode, swa, match, agent, client))
    return out


# ------------------------------------------------------------------------------------------------ the texts
_WORDS = ("cache slot token prompt session server client model window state file restore save read write "
          "agent tool plan test check build run fix note list table row value field key name path time disk "
          "memory speed batch queue thread lock event hook patch merge branch fork rename restart switch").split()


def document(name: str, words: int = 4500) -> str:
    """A long text, the same for the same name (a seeded mix of words, numbered lines)."""
    rnd = random.Random(name)
    lines, n, i = [], 0, 0
    while n < words:
        k = rnd.randint(8, 16)
        lines.append(f"{name} {i:04d}: " + " ".join(rnd.choice(_WORDS) for _ in range(k)) + ".")
        n += k
        i += 1
    return "\n".join(lines)


WORDS = {"gemma-4-e4b": 4500}      # the long turn's notes per model (~1.4 tokens a word); --words; others: BIG_WORDS
BIG_WORDS = 15000                  # the models CARL is tuned for (the user, 2026-10-10: the A3B IQ3 first): ~20K tokens


def long_prompt(name: str, words: int = 4500) -> str:
    return ("Read the notes below. Reply with only the word OK. Do not use any tools.\n\n<notes>\n"
            + document(name, words) + "\n</notes>")


SHORT = "Reply with only the word OK. Do not use any tools."


# ------------------------------------------------------------------------------------------------ the server
@dataclass
class MatrixSetup(ServerSetup):
    """agent-bench's server with the HOME's ~/.config/carl as its settings folder (so its slots folder is the
    clients'), router mode on request."""
    conf: str = ""
    mode: str = "single"

    @property
    def conf_dir(self) -> str:      # type: ignore[override]
        return self.conf

    @property
    def home_dir(self) -> str:      # type: ignore[override]
        """The harness HOME (the settings folder's home), as on a Mac with one HOME: router mode in CARL 1.16.0 takes
        its save folder from HOME, not from CARL_CONF_DIR (fixed in 23.5)."""
        return os.path.dirname(os.path.dirname(self.conf))

    def argv(self) -> List[str]:
        if self.mode == "router":
            return ["./carl.sh", "--router", "--local"]
        return super().argv()


def carl_config(repo: str, conf: str, key: str, value: str) -> None:
    env = dict(os.environ, CARL_CONF_DIR=conf)
    code, out = run(["python3", "tools/carl.py", "config", "set", key, value], repo, env, 60)
    if code != 0:
        raise ServerError(f"carl.py config set {key} {value}: {out.strip()[-300:]}")


def http(base: str, key: str, method: str, path: str, body: Optional[Mapping[str, Any]] = None,
         timeout: float = 30.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, method=method, data=data,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def server_alias(base: str, key: str) -> str:
    try:
        return str((http(base, key, "GET", "/props") or {}).get("model_alias") or "")
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def dashboard_stop_save(base: str, key: str, slots_dir: str, router: bool) -> List[str]:
    """What the dashboard's Stop does first (jobs.save_before_stop -> save_recorded): save each session a client
    recorded in a slot of the server's model (its /props model_alias), while the slot still holds it."""
    alias = server_alias(base, key)
    if not alias:
        return []
    q = f"?model={urllib.parse.quote(alias)}" if router else ""
    try:
        slots = {int(s.get("id")): s for s in http(base, key, "GET", "/slots" + q) or []}
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return []
    saved = []
    for r in diskcache.residents(slots_dir, alias):
        s = slots.get(r.slot)
        if not s or s.get("is_processing") or int(s.get("id_task", -1)) != r.task:
            continue
        try:
            http(base, key, "POST", f"/slots/{r.slot}?action=save", {"filename": r.file, **({"model": alias} if router else {})},
                 timeout=600)
            os.remove(r.path)
            saved.append(r.file)
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return saved



# ------------------------------------------------------------------------------------------------ what a step did
@dataclass
class Probe:
    """Offsets into the server log and the cache log, and the slots folder's files, at the start of a step."""
    server_log: str
    cache_log: str
    slots_dir: str
    _s: int = 0
    _c: int = 0
    _files: Dict[str, int] = field(default_factory=dict)

    def files(self) -> Dict[str, int]:
        out = {}
        try:
            for n in os.listdir(self.slots_dir):
                if n.startswith("carl-"):
                    try:
                        out[n] = os.path.getsize(os.path.join(self.slots_dir, n))
                    except OSError:
                        pass
        except OSError:
            pass
        return out

    def mark(self) -> None:
        self._s = _size(self.server_log)
        self._c = _size(self.cache_log)
        self._files = self.files()

    def take(self, server_log: str = "") -> Dict[str, Any]:
        """What happened since mark(); server_log: the server's log now (a restart in the step starts a new one)."""
        texts = [_read_from(self.server_log, self._s)]
        if server_log and server_log != self.server_log:
            texts.append(_read_from(server_log, 0))            # a new server: its own task ids
        reqs = []
        for t in texts:
            book = LogBook()
            for line in t.splitlines():
                book.add(line)
            reqs += [request_doc(r) for r in book.requests]
        text = "\n".join(texts)
        cache = [ln.split(" ", 1)[1] if " " in ln else ln for ln in _read_from(self.cache_log, self._c).splitlines()]
        now = self.files()
        added = {n: s for n, s in now.items() if n not in self._files or self._files[n] != s}
        gone = sorted(n for n in self._files if n not in now)
        errors = [ln for ln in text.splitlines() if " E " in ln[:20]]
        return {"requests": reqs, "cache_log": cache, "files_written": added, "files_removed": gone,
                "server_errors": errors[:10]}


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _read_from(path: str, at: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(at)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def request_doc(r: RequestRecord) -> Dict[str, Any]:
    """One request from the log: CARL's own (a fill or settle: nothing generated) or a turn's."""
    read_s = (r.new / r.pp) if r.pp else None
    return {"slot": r.slot, "task": r.task, "ctx": int(r.ctx), "read": int(r.new), "gen": int(r.gen),
            "read_s": round(read_s, 2) if read_s is not None else None,
            "kind": "carl" if r.gen <= 1 else "turn", "error": r.error}


def summary(step: Mapping[str, Any]) -> Dict[str, Any]:
    """The step's turn request (the longest context among those that generated text): its reuse, and the tokens
    CARL's own requests read."""
    turns = [q for q in step["requests"] if q["kind"] == "turn"]
    t = max(turns, key=lambda q: q["ctx"]) if turns else None
    carl_read = sum(q["read"] for q in step["requests"] if q["kind"] == "carl")
    hows = [ln.split("(", 1)[1].rstrip(")") for ln in step["cache_log"] if ln.startswith("request: ") and "(" in ln]
    return {"ctx": t["ctx"] if t else None, "read": t["read"] if t else None,
            "reuse": round(1 - t["read"] / t["ctx"], 3) if t and t["ctx"] else None,
            "read_s": t["read_s"] if t else None, "carl_read": carl_read, "how": hows[-1] if hows else "",
            "saved": sorted(step["files_written"]), "restored": [ln for ln in step["cache_log"] if "restored" in ln]}


# ------------------------------------------------------------------------------------------------ the clients
class Driver:
    """One client process and its sessions."""
    name = ""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def new(self) -> str: ...
    def say(self, sid: str, text: str) -> str: ...
    def rename(self, sid: str, title: str) -> None: ...
    def fork(self, sid: str) -> str: ...

    def restart(self) -> None:
        self.stop()
        self.start()


class OpenCodeDriver(Driver):
    name = "opencode"

    def __init__(self, home: str, cwd: str, env: Mapping[str, str], model: str, sub: bool, sub_agent: str) -> None:
        self.home, self.cwd, self.env, self.model, self.sub, self.sub_agent = home, cwd, env, model, sub, sub_agent
        self.child: Optional[Child] = None
        self.api: Optional[cl.OpenCodeApi] = None
        self.parent = ""

    def start(self) -> None:
        port = cl.free_port()
        cl.clear_stale_locks(self.home)
        self.child = Child(["opencode", "serve", "--port", str(port), "--hostname", "127.0.0.1"], self.cwd, self.env)
        self.api = cl.OpenCodeApi(port, timeout=TURN_LIMIT)
        t0 = time.time()
        while not self.api.ready():
            if not self.child.running() or time.time() - t0 > 180:
                raise RuntimeError("opencode serve did not start: " + self.child.err_text(5))
            time.sleep(0.5)

    def stop(self) -> None:
        if self.child is not None:
            self.child.stop()
            self.child = None

    def _call(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None) -> Any:
        assert self.api is not None
        return self.api.call(method, path, body)

    def new(self) -> str:
        body: Dict[str, Any] = {"title": "cache-matrix"}
        if self.sub:
            if not self.parent:
                self.parent = str(self._call("POST", "/session", {"title": "cache-matrix parent"})["id"])
            body["parentID"] = self.parent
        return str(self._call("POST", "/session", body)["id"])

    def say(self, sid: str, text: str) -> str:
        body = {"parts": [{"type": "text", "text": text}], "model": {"providerID": cl.PROVIDER, "modelID": self.model},
                "agent": self.sub_agent if self.sub else "build"}
        variant = cl.opencode_variant("off")
        if variant:
            body["variant"] = variant
        msg = self._call("POST", f"/session/{sid}/message", body) or {}
        err = (msg.get("info") or {}).get("error")
        if err:
            return "error: " + json.dumps(err)[:300]
        return "".join(str(p.get("text", "")) for p in msg.get("parts") or [] if p.get("type") == "text")[:200]

    def rename(self, sid: str, title: str) -> None:
        self._call("PATCH", f"/session/{sid}", {"title": title})

    def fork(self, sid: str) -> str:
        return str(self._call("POST", f"/session/{sid}/fork", {})["id"])


class PiDriver(Driver):
    """Pi in RPC mode. A main session's process holds one session at a time (switch_session between them); a
    subagent (CARL's subagent tool: --no-session and CARL_AGENT) is a process of its own."""
    name = "pi"

    def __init__(self, home: str, cwd: str, env: Mapping[str, str], model: str, sub: bool) -> None:
        self.home, self.cwd, self.env, self.model, self.sub = home, cwd, dict(env), model, sub
        if sub:
            self.env["CARL_AGENT"] = "coder"
        self.child: Optional[Child] = None
        self.files: Dict[str, str] = {}
        self.current = ""
        self.n = 0

    def start(self) -> None:
        argv = ["pi", "--mode", "rpc", "--model", f"{cl.PROVIDER}/{self.model}:{cl.pi_level('off', self.home)}"]
        if self.sub:
            argv.append("--no-session")
        self.child = Child(argv, self.cwd, self.env, stdin=True)
        self.current = ""
        if self.sub:
            self.current = self._state().get("sessionId", "")

    def stop(self) -> None:
        if self.child is not None:
            self.child.stop()
            self.child = None

    def _cmd(self, doc: Dict[str, Any], wait_turn: bool = False) -> Dict[str, Any]:
        assert self.child is not None
        self.n += 1
        rid = f"m{self.n}"
        self.child.send(json.dumps({"id": rid, **doc}))
        resp: Optional[Dict[str, Any]] = None
        text = ""
        t0 = time.time()
        while True:
            item = self.child.next_line(0.5)
            if time.time() - t0 > TURN_LIMIT:
                raise RuntimeError(f"pi: no answer to {doc.get('type')} in {TURN_LIMIT:.0f} s")
            if item is None:
                if not self.child.running():
                    raise RuntimeError("pi stopped: " + self.child.err_text(5))
                continue
            if item[0] != "out":
                continue
            try:
                ev = json.loads(item[1])
            except ValueError:
                continue
            if ev.get("type") == "response" and ev.get("id") == rid:
                if not ev.get("success", True):
                    raise RuntimeError(f"pi {doc.get('type')}: {json.dumps(ev)[:300]}")
                resp = ev
                if not wait_turn:
                    return resp
            elif wait_turn and ev.get("type") == "message_end":
                m = ev.get("message") or {}
                if m.get("role") == "assistant":
                    text = "".join(str(c.get("text", "")) for c in m.get("content") or [] if c.get("type") == "text")
                    if m.get("stopReason") == "error":
                        text = "error: " + str(m.get("errorMessage", ""))[:300]
            elif wait_turn and ev.get("type") in ("agent_settled", "agent_end") and resp is not None:
                if ev.get("type") == "agent_end":
                    self._wait_settled()
                return {"text": text[:200]}

    def _wait_settled(self) -> bool:
        """After agent_end, an agent_settled may follow (Pi 1.x): wait a little for it."""
        assert self.child is not None
        t0 = time.time()
        while time.time() - t0 < 3:
            item = self.child.next_line(0.2)
            if item and item[0] == "out" and '"agent_settled"' in item[1]:
                return True
        return False

    def _state(self) -> Dict[str, Any]:
        return dict(self._cmd({"type": "get_state"}).get("data") or {})

    def _switch(self, sid: str) -> None:
        if self.sub or sid == self.current:
            return
        self._cmd({"type": "switch_session", "sessionPath": self.files[sid]})
        self.current = sid

    def new(self) -> str:
        if self.sub:
            self.restart()             # a subagent is a process of its own
            return self.current
        self._cmd({"type": "new_session"})
        st = self._state()
        sid = str(st.get("sessionId"))
        self.files[sid] = str(st.get("sessionFile"))
        self.current = sid
        return sid

    def say(self, sid: str, text: str) -> str:
        self._switch(sid)
        return str(self._cmd({"type": "prompt", "message": text}, wait_turn=True).get("text", ""))

    def rename(self, sid: str, title: str) -> None:
        self._switch(sid)
        self._cmd({"type": "set_session_name", "name": title})

    def fork(self, sid: str) -> str:
        self._switch(sid)
        self._cmd({"type": "clone"})
        st = self._state()
        new = str(st.get("sessionId"))
        self.files[new] = str(st.get("sessionFile"))
        self.current = new
        return new


# ------------------------------------------------------------------------------------------------ the client configs
def add_alias(home: str, model: str, alias: str) -> List[str]:
    """A second name for the server's model in the clients' model lists (single mode answers any name)."""
    changed = []
    oc = os.path.join(home, ".config", "opencode", "opencode.json")
    try:
        with open(oc, encoding="utf-8") as f:
            doc = json.load(f)
        models = doc["provider"][cl.PROVIDER]["models"]
        if model in models and alias not in models:
            models[alias] = {**models[model], "name": alias}
            with open(oc, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
            changed.append(oc)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    pm = os.path.join(home, ".pi", "agent", "models.json")
    try:
        with open(pm, encoding="utf-8") as f:
            doc = json.load(f)
        prov = doc["providers"][cl.PROVIDER]
        ms = prov["models"]
        src = next((m for m in ms if m.get("id") == model), None)
        if src and not any(m.get("id") == alias for m in ms):
            ms.append({**src, "id": alias, "name": alias})
            with open(pm, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
            changed.append(pm)
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        pass
    return changed


def sub_agent_name(home: str) -> str:
    """OpenCode's coder agent as CARL's setup named it."""
    try:
        with open(os.path.join(home, ".config", "opencode", "opencode.json"), encoding="utf-8") as f:
            agents = json.load(f).get("agent") or {}
        for name in ("coder", "carl-coder"):
            if name in agents:
                return name
    except (OSError, ValueError):
        pass
    return "general"


# ------------------------------------------------------------------------------------------------ one cell
@dataclass
class Env:
    a: argparse.Namespace
    home: str
    work: str
    slots_dir: str
    server: Optional[CarlServer] = None
    key: str = ""

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.a.port}"


def clear_cache(slots_dir: str) -> int:
    n = 0
    try:
        for name in os.listdir(slots_dir):
            if name.startswith(STATE_PREFIXES):
                try:
                    os.remove(os.path.join(slots_dir, name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def start_server(e: Env, cell: Cell) -> None:
    a = e.a
    conf = os.path.join(e.home, ".config", "carl")
    if cell.swa != "none":
        carl_config(a.repo, conf, "cache.swa", cell.swa)
    setup = MatrixSetup(repo=a.repo, model=cell.model, work=os.path.join(e.work, "server"), port=a.port,
                        models_dir=a.models_dir, slots=a.slots, conf=conf, mode=cell.mode,
                        extra_env={"SWA_MODE": cell.swa} if cell.swa != "none" else {})
    e.server = CarlServer(setup)
    e.server.start()
    e.server.wait_ready()
    e.key = ensure_key(setup.key_file)
    if cell.mode == "router":
        wait_loaded(e, cell.model)


def wait_loaded(e: Env, model: str, limit: float = 300) -> None:
    """Router mode: the model loaded (a first request would load it anyway; this keeps the load out of a step)."""
    t0 = time.time()
    asked = False
    while time.time() - t0 < limit:
        try:
            for m in (http(e.base, e.key, "GET", "/v1/models") or {}).get("data") or []:
                if m.get("id") == model and ((m.get("status") or {}).get("value") == "loaded"):
                    return
            if not asked:
                http(e.base, e.key, "POST", "/models/load", {"model": model}, timeout=30)
                asked = True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(2)
    raise ServerError(f"router: {model} did not load in {limit:.0f} s")


def stop_server(e: Env) -> None:
    """Stop the server and wait until no llama-server of the harness's port is left (a router's model server stops
    after the router; the launcher refuses a second model while one is in memory)."""
    if e.server is not None:
        e.server.stop()
        e.server = None
    for _ in range(60):
        code, out = run(["pgrep", "-f", f"llama-server.*--port {e.a.port}|llama-server.*{e.work}"], e.work, os.environ, 10)
        if code != 0 or not out.strip():
            return
        time.sleep(1)


def run_cell(e: Env, cell: Cell, env_base: Mapping[str, str], other: str) -> Dict[str, Any]:
    """The cell's script; one result document."""
    a = e.a
    clear_cache(e.slots_dir)
    stop_server(e)
    start_server(e, cell)
    cache_log = os.path.join(e.work, "logs", f"cache-{cell.key().replace('/', '_')}.log")
    os.makedirs(os.path.dirname(cache_log), exist_ok=True)
    if os.path.exists(cache_log):
        os.remove(cache_log)
    env = dict(env_base, CARL_CACHE_LOG=cache_log)
    model = (a.alias if cell.match == "other" and cell.mode == "single" else cell.model)
    words = WORDS.get(cell.model, BIG_WORDS)

    def long_prompt(name: str) -> str:            # this model's size of the long turn
        return globals()["long_prompt"](name, words)
    cwd = os.path.join(e.work, "project")
    os.makedirs(cwd, exist_ok=True)
    sub = cell.agent == "sub"
    drv: Driver = (OpenCodeDriver(e.home, cwd, env, model, sub, sub_agent_name(e.home)) if cell.client == "opencode"
                   else PiDriver(e.home, cwd, env, model, sub))
    assert e.server is not None
    probe = Probe(e.server.log_file, cache_log, e.slots_dir)
    steps: List[Dict[str, Any]] = []
    names: Dict[str, str] = {}
    t_cell = time.time()

    def step(name: str, ideal: str, fn: Callable[[], str]) -> None:
        assert e.server is not None
        probe.server_log = e.server.log_file
        probe.mark()
        t0 = time.time()
        err = ""
        reply = ""
        try:
            reply = fn()
        except Exception as ex:     # a failed step is a result too; the cell goes on
            err = f"{type(ex).__name__}: {ex}"[:400]
        time.sleep(1.0)              # the save after the reply, the log's last lines
        doc = {"step": name, "ideal": ideal, "seconds": round(time.time() - t0, 1), "reply": reply[:80],
               "error": err, **probe.take(e.server.log_file if e.server else "")}
        doc["summary"] = summary(doc)
        steps.append(doc)
        s = doc["summary"]
        say(f"   {name}: {s['how'] or '-'}; read {s['read']} of {s['ctx']} (reuse {s['reuse']}); "
            f"CARL read {s['carl_read']}; saved {len(s['saved'])}{'; ERROR ' + err if err else ''}")

    def restart_server() -> str:
        saved = dashboard_stop_save(e.base, e.key, e.slots_dir, cell.mode == "router")
        stop_server(e)
        start_server(e, cell)
        return f"dashboard Stop saved {len(saved)}"

    try:
        drv.start()
        if not sub:
            step("a1", "the agent's prompt read once and saved; A in a slot", lambda: _new_say(drv, names, "A", long_prompt("A")))
            step("a2", "A in its own slot: only the new message read", lambda: drv.say(names["A"], SHORT))
            step("b1", "B in the other slot (the prompt from its file or the RAM)", lambda: _new_say(drv, names, "B", long_prompt("B")))
            step("c1", "C takes a slot; the session there saved first", lambda: _new_say(drv, names, "C", long_prompt("C")))
            out = _evicted(steps[-1], names)
            step("back", f"{out} (its slot was taken) restored from its file: only the new message read",
                 lambda: drv.say(names[out], SHORT))
            step("client-restart", "A after a client restart: its slot (record) or its file",
                 lambda: (drv.restart(), drv.say(names["A"], SHORT))[1])
            step("rename", "A renamed: the same session (ID), its own slot",
                 lambda: (drv.rename(names["A"], "renamed by cache-matrix"), drv.say(names["A"], SHORT))[1])
            step("fork", "a fork of A from its end starts from A's saved state; the session in the slot it takes saved first",
                 lambda: _fork_say(drv, names, "A", "F"))
            note = {"text": ""}

            def srv() -> str:
                note["text"] = restart_server()
                return note["text"] + "; " + drv.say(names["A"], SHORT)
            step("server-restart", "A after a server restart (the dashboard's Stop saved it): restored from its file", srv)
            step("b-later", "B, whose slot the fork took after the client restart: restored from its file (saved when "
                 "its slot was taken)", lambda: drv.say(names["B"], SHORT))
            if cell.mode == "router" and cell.match == "other":
                step("switch", f"a session on {other} (the router switches; the sessions are saved first)",
                     lambda: _switch_say(e, drv, names, other, cell))
                step("switch-back", "A on the first model again: restored from its file",
                     lambda: _switch_back(e, drv, names, cell))
        else:
            step("s1", "the subagent's prompt read once and saved; nothing saved for the session",
                 lambda: _new_say(drv, names, "S1", long_prompt("A")))
            if cell.client == "opencode":
                step("s1-again", "the same subagent session: its own slot", lambda: drv.say(names["S1"], SHORT))
            step("s2", "a second subagent: the prompt from its file", lambda: _new_say(drv, names, "S2", long_prompt("B")))
            step("s3", "a third subagent while two slots are used: the prompt from its file",
                 lambda: _new_say(drv, names, "S3", long_prompt("C")))

            def srv_sub() -> str:
                msg = restart_server()
                return msg + "; " + _new_say(drv, names, "S4", long_prompt("D"))
            step("server-restart", "a subagent after a server restart: the prompt from its file", srv_sub)
    finally:
        drv.stop()
    files = Probe(e.server.log_file if e.server else "", cache_log, e.slots_dir).files()
    return {"cell": dataclasses.asdict(cell), "key": cell.key(), "client_model": model, "words": words, "steps": steps,
            "files_at_end": files, "seconds": round(time.time() - t_cell, 1), "date": time.strftime("%Y-%m-%dT%H:%M:%S")}


def _new_say(drv: Driver, names: Dict[str, str], label: str, text: str) -> str:
    names[label] = drv.new()
    return drv.say(names[label], text)


def _fork_say(drv: Driver, names: Dict[str, str], src: str, label: str) -> str:
    names[label] = drv.fork(names[src])
    return drv.say(names[label], SHORT)


def _evicted(c1: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """The session C pushed out: the one carl-cache saved "(its slot is needed)", else A."""
    for ln in c1["cache_log"]:
        if "its slot is needed" in ln:
            for label in ("A", "B"):
                if names.get(label) and names[label] in ln:
                    return label
    return "A"


def _switch_say(e: Env, drv: Driver, names: Dict[str, str], other: str, cell: Cell) -> str:
    saved = dashboard_stop_save(e.base, e.key, e.slots_dir, True)      # a switch from the dashboard saves first
    if isinstance(drv, OpenCodeDriver):
        drv.model = other
    elif isinstance(drv, PiDriver):
        drv._cmd({"type": "set_model", "provider": cl.PROVIDER, "modelId": other})
    r = _new_say(drv, names, "D", long_prompt("D"))
    return f"dashboard saved {len(saved)}; {r}"


def _switch_back(e: Env, drv: Driver, names: Dict[str, str], cell: Cell) -> str:
    if isinstance(drv, OpenCodeDriver):
        drv.model = cell.model
    elif isinstance(drv, PiDriver):
        drv._cmd({"type": "set_model", "provider": cl.PROVIDER, "modelId": cell.model})
    return drv.say(names["A"], SHORT)


# ------------------------------------------------------------------------------------------------ results
def done_keys(path: str) -> set:
    keys = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    keys.add(json.loads(line)["key"])
                except (ValueError, KeyError):
                    pass
    except OSError:
        pass
    return keys


def append(path: str, doc: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(doc) + "\n")


def load(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def report(docs: Iterable[Mapping[str, Any]], md: bool = False) -> str:
    """One row per step: the cell, what carl-cache did, the reuse, CARL's own reading, the files written."""
    rows = [("cell", "step", "how", "read/ctx", "reuse", "CARL read", "saved", "error")]
    for d in docs:
        for s in d["steps"]:
            m = s["summary"]
            rows.append((d["key"], s["step"], m["how"] or "-", f"{m['read']}/{m['ctx']}",
                         "" if m["reuse"] is None else f"{m['reuse']:.0%}", str(m["carl_read"]),
                         ", ".join(f.split("+")[0].replace("carl-", "") for f in m["saved"]) or "-",
                         (s["error"] or "")[:60]))
    if md:
        out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(out)
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


# ------------------------------------------------------------------------------------------------ commands
def work_of(a: argparse.Namespace) -> str:
    w = os.path.abspath(os.path.expanduser(a.work or (a.home.rstrip("/") + "-work")))
    os.makedirs(w, exist_ok=True)
    return w


def cmd_prepare(a: argparse.Namespace) -> int:
    home = hm.check_home_path(a.home)
    if os.path.isdir(home) and os.listdir(home) and not hm.is_harness_home(home):
        raise SystemExit(f"error: {home} is not empty and is not a harness HOME")
    os.makedirs(home, exist_ok=True)
    hm.write_marker(home, {"created_by": "tools/cache-matrix/cachematrix.py prepare", "repo": a.repo})
    check_port(a.port)
    work = work_of(a)
    conf = os.path.join(home, ".config", "carl")
    os.makedirs(conf, exist_ok=True)
    setup = MatrixSetup(repo=a.repo, model=a.model, work=os.path.join(work, "server"), port=a.port,
                        models_dir=os.path.abspath(a.models_dir), slots=a.slots, conf=conf)
    srv = CarlServer(setup)
    say(f"server for {a.model} on port {a.port} (log {srv.log_file})")
    srv.start()
    try:
        say(f"ready after {srv.wait_ready():.0f} s; the client package and ./setup in {home} (some minutes)")
        src = hm.PackageSource(a.repo, setup.client_dir, conf, os.path.join(work, "dist"), setup.models_dir)
        hm.unpack_and_setup(hm.make_package(src), home, install=True, coder="on")
        found = hm.pin_versions(home, hm.DEFAULT_OPENCODE, hm.DEFAULT_PI)
        hm.write_marker(home, {"versions": found})
    finally:
        srv.stop()
    say(f"ready: {home} ({', '.join(f'{k} {v}' for k, v in found.items())})")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    home = hm.require_harness_home(a.home)
    check_port(a.port)
    a.models_dir = os.path.abspath(os.path.expanduser(a.models_dir))
    for item in csv(a.words):
        m, _, n = item.partition("=")
        WORDS[m] = int(n)
    work = work_of(a)
    todo_all = cells(csv(a.models), csv(a.modes), csv(a.swa), csv(a.match), csv(a.agents), csv(a.clients))
    done = done_keys(a.out)
    todo = [c for c in todo_all if c.key() not in done]
    say(f"{len(todo_all)} cells, {len(todo_all) - len(todo)} done, {len(todo)} to run; results {os.path.abspath(a.out)}")
    e = Env(a, home, work, os.path.join(home, ".config", "carl", "slots"))
    os.makedirs(e.slots_dir, exist_ok=True)
    awake = None
    try:
        import subprocess
        awake = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    except OSError:
        pass
    try:
        refreshed: Tuple[str, str] = ("", "")
        for n, cell in enumerate(todo, 1):
            if refreshed != (cell.model, cell.mode):
                # the clients' model list for this model and mode (./setup --no-install from a package of its server:
                # single mode lists its one model, router mode every model it offers)
                stop_server(e)
                start_server(e, cell)
                assert e.server is not None
                src = hm.PackageSource(a.repo, e.server.setup.client_dir, os.path.join(home, ".config", "carl"),
                                       os.path.join(work, "dist"), a.models_dir)
                hm.unpack_and_setup(hm.make_package(src), home, install=False, coder="on")
                changed = add_alias(home, cell.model, a.alias)
                say(f"clients: set up for {cell.model}; alias {a.alias} in {len(changed)} config(s)")
                refreshed = (cell.model, cell.mode)
            say(f"[{n}/{len(todo)}] {cell.key()}")
            other = a.other_model
            try:
                doc = run_cell(e, cell, cl.client_env(home), other)
            except (ServerError, RuntimeError, OSError) as ex:
                say(f"   cell failed: {ex}")
                doc = {"cell": dataclasses.asdict(cell), "key": cell.key(), "error": str(ex)[:500], "steps": []}
                if not a.keep_going:
                    append(a.out, doc)
                    return 1
            append(a.out, doc)
    finally:
        stop_server(e)
        if awake is not None:
            awake.terminate()
    say(f"done: cachematrix.py report {a.out}")
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    print(report(load(a.results), a.md))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Phase 23.5's cache matrix (carl-cache with the real clients).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", required=True, help="the harness HOME (never yours)")
    common.add_argument("--work", default=None, help="the work folder (default: HOME-work)")
    common.add_argument("--repo", default=os.path.dirname(TOOLS), help="the CARL checkout to test")
    common.add_argument("--models-dir", default=os.path.expanduser("~/models/gguf"))
    common.add_argument("--port", type=int, default=8097)
    common.add_argument("--slots", type=int, default=2)
    p = sub.add_parser("prepare", parents=[common], help="a new harness HOME with OpenCode and Pi")
    p.add_argument("--model", default="gemma-4-e4b")
    p.set_defaults(fn=cmd_prepare)
    r = sub.add_parser("run", parents=[common], help="run the cells not done yet")
    r.add_argument("--out", required=True)
    r.add_argument("--models", default="gemma-4-e4b")
    r.add_argument("--modes", default="single,router")
    r.add_argument("--swa", default="full,window")
    r.add_argument("--match", default="same,other")
    r.add_argument("--agents", default="main,sub")
    r.add_argument("--clients", default="opencode,pi")
    r.add_argument("--alias", default="cache-matrix-alias", help="the other name a client uses (single mode)")
    r.add_argument("--other-model", default="qwen3.8-9b", help="router mode: the model of the switch")
    r.add_argument("--keep-going", action="store_true")
    r.add_argument("--words", default="", help="MODEL=N,...: the long turn's words per model (default: the E4B "
                   f"4500, any other {BIG_WORDS})")
    r.set_defaults(fn=cmd_run)
    q = sub.add_parser("report", help="the results as a table")
    q.add_argument("results")
    q.add_argument("--md", action="store_true")
    q.set_defaults(fn=cmd_report)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())

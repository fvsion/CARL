#!/usr/bin/env python3
"""Does OpenCode's own compaction keep the user's directives? (Phase 23.5.2.2; user, 2026-10-14: "did you compare basic
open code compaction for how well it keeps user directives?"; "48K, with the control, 2 runs each, OpenCode only".)

Real OpenCode (a harness HOME made by compbench.py prepare; never yours) against CARL's server with the A3B, driving
the pyrules session the compaction bench collected (its 25 user turns: the four rules-* tasks, then the follow-ups),
with the project's three rules said by the user in the first message and no AGENTS.md in the project:
  1. every function added or changed gets a docstring whose first line starts with `Does:`;
  2. every new test method is named `test_rule_...`;
  3. after every code change, the tests run (`python3 -m unittest discover -s tests`).
Arms (OpenCode's opencode.json in the harness HOME, as CARL writes it for that window: output = min(32000, ctx / 2)):
- rules48 (the hook test, Phase 23.5.2.2.4): compact48 + carl-rules-hook (the rules list, rules.py with setfit2, given to
  OpenCode's summary prompt through experimental.session.compacting; what it gave: hook.jsonl);
- compact48: a 48K window, OpenCode's compaction on (its defaults: it compacts at context - output, ~24.5K);
- control96: a 96K window, OpenCode's compaction off (the session, ~76K, never compacts).
Graded by code on what OpenCode did: the final project against the fixture (every function added or changed: a Does:
docstring; every new test method: test_rule_; agent-bench's hidden rule tests), and OpenCode's own session messages
(each turn with a code change: did the tests run after its last change; where the compactions were).

    python3 oc_directives.py --home HARNESS_HOME --out DIR --arm compact48|control96 --run N
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Mapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import compbench as cb  # noqa: E402  (also puts agent-bench and cache-matrix on the path)

RULES = ("Every function that you add or change gets a docstring. Its first line starts with `Does:`, for example "
         "`\"\"\"Does: load the notes file.\"\"\"`.",
         "Name every new test method `test_rule_` and then what it checks, for example `test_rule_empty_file`.",
         "After every code change, run the tests: `python3 -m unittest discover -s tests`.")
SAID = "A few rules for this project, please follow them the whole time:\n" + "\n".join(f"- {r}" for r in RULES)
ITEMS = ["rules-func", "rules-rename", "rules-flag", "rules-test"] + cb.FOLLOW_UPS + cb.GROW + cb.GROW
ITEMS = ITEMS[:25]                         # the collected session's 25 user turns, in its order
ARMS = {"compact48": (49152, {"auto": True, "prune": False}),
        "control96": (98304, {"auto": False, "prune": False}),
        # the hook test (Phase 23.5.2.2.4): compact48 + the rules list given to OpenCode's summary (carl-rules-hook)
        "rules48": (49152, {"auto": True, "prune": False})}
HOOK = os.path.join(HERE, "carl-rules-hook")
RULES_PY = os.path.join(cb.REPO, "tools", "monitor", "rules.py")
HOOK_TIER = "spacy+B:setfit2"
HIDDEN = ["_rules.py", "test_hidden_rules_func.py", "test_hidden_rules_rename.py", "test_hidden_rules_flag.py",
          "test_hidden_rules_test.py"]
WRITE = {"edit", "write", "multiedit", "apply_patch", "patch"}


def configure(home: str, model: str, arm: str, log: str = "") -> Dict[str, Any]:
    """The harness HOME's opencode.json for the arm: the model's window and output as CARL writes them, compaction,
    and the hook test's plugin (rules48 only)."""
    ctx, compaction = ARMS[arm]
    path = os.path.join(home, ".config", "opencode", "opencode.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    m = doc["provider"][next(iter(doc["provider"]))]["models"][model]
    m["limit"] = {"context": ctx, "output": min(32000, ctx // 2)}                 # client/carl_models.py
    doc["compaction"] = compaction
    plugins = [p for p in doc.get("plugin") or [] if not (isinstance(p, list) and "carl-rules-hook" in str(p[0]))]
    if arm == "rules48":
        # OpenCode runs with the harness HOME: the models' folder and the NLP environment given by path (run 1 of
        # 2026-10-15 fell back to the patterns: "~/models/gguf" was read in the harness HOME)
        plugins.append([f"file:{HOOK}", {"rulesPy": RULES_PY, "tier": HOOK_TIER, "log": log,
                                         "env": {"CARL_NLP_MODELS": os.path.expanduser("~/models/gguf"),
                                                 "CARL_NLP_DIR": os.path.join(cb.REPO, ".nlp")}}])
    doc["plugin"] = plugins
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return {"limit": m["limit"], "compaction": compaction, "hook": HOOK_TIER if arm == "rules48" else None}


def functions(text: str) -> Dict[str, str]:
    """Every function and method of a Python file: name (Class.name for methods) -> its source."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: Dict[str, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for n in ast.iter_child_nodes(node):
            if isinstance(n, ast.ClassDef):
                walk(n, prefix + n.name + ".")
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[prefix + n.name] = ast.get_source_segment(text, n) or ""
                walk(n, prefix + n.name + ".")
    walk(tree, "")
    return out


def does_doc(src: str) -> bool:
    try:
        fn = ast.parse(src.lstrip() if src.startswith((" ", "\t")) else src).body[0]
    except (SyntaxError, IndexError):
        import textwrap
        try:
            fn = ast.parse(textwrap.dedent(src)).body[0]
        except (SyntaxError, IndexError):
            return False
    doc = ast.get_docstring(fn) or ""                                        # type: ignore[arg-type]
    return bool(doc) and doc.strip().splitlines()[0].startswith("Does:")


def grade_project(cwd: str, fixture_dir: str) -> Dict[str, Any]:
    """Rules 1 and 2 on the final project against the fixture."""
    defs, defs_ok, tests, tests_ok, bad = 0, 0, 0, 0, []
    for d, _dirs, names in os.walk(cwd):
        if "/." in d or "__pycache__" in d:
            continue
        for n in names:
            if not n.endswith(".py") or n.startswith("test_hidden") or n == "_rules.py":
                continue
            path = os.path.join(d, n)
            rel = os.path.relpath(path, cwd)
            with open(path, encoding="utf-8", errors="replace") as f:
                now = functions(f.read())
            old_path = os.path.join(fixture_dir, rel)
            old = {}
            if os.path.exists(old_path):
                with open(old_path, encoding="utf-8", errors="replace") as f:
                    old = functions(f.read())
            is_test = rel.startswith("tests" + os.sep) or n.startswith("test_")
            for name, src in now.items():
                if name in old and old[name] == src:
                    continue                                                  # unchanged
                defs += 1
                ok = does_doc(src)
                defs_ok += ok
                short = name.rsplit(".", 1)[-1]
                if not ok:
                    bad.append(f"{rel}:{name} no Does:")
                if is_test and short.startswith("test") and name not in old:
                    tests += 1
                    tests_ok += short.startswith("test_rule_")
                    if not short.startswith("test_rule_"):
                        bad.append(f"{rel}:{name} not test_rule_")
    return {"functions_added_or_changed": defs, "with_does": defs_ok, "new_tests": tests, "named_test_rule": tests_ok,
            "misses": bad[:60]}


DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(\w+)[ \t]*\((?:[^()]|\([^()]*\))*\)[^:\n]*:[ \t]*\n"
                    r"(?:[ \t]*\n)*[ \t]*(\S[^\n]*)?", re.M)


def turn_edits(parts: List[Mapping[str, Any]]) -> Dict[str, int]:
    """Rules 1 and 2 in one turn's own edit and write calls: the functions in their new text (a Does: docstring?) and
    the test methods in test files (named test_rule_?)."""
    v = {"defs": 0, "defs_ok": 0, "tests": 0, "tests_ok": 0}
    for p in parts:
        if p.get("type") != "tool" or str(p.get("tool", "")).lower() not in WRITE:
            continue
        inp = (p.get("state") or {}).get("input") or {}
        path = str(inp.get("filePath") or inp.get("path") or "")
        if not path.endswith(".py"):
            continue
        texts = [inp[k] for k in ("newString", "content") if isinstance(inp.get(k), str)]
        is_test = "/tests/" in path or os.path.basename(path).startswith("test_")
        for text in texts:
            for m in DEF_RE.finditer(text):
                name, first = m.group(1), m.group(2) or ""
                v["defs"] += 1
                v["defs_ok"] += bool(re.match(r"[rRuU]?(\"\"\"|\'\'\')\s*Does:", first))
                if is_test and name.startswith("test"):
                    v["tests"] += 1
                    v["tests_ok"] += name.startswith("test_rule_")
    return v


def grade_session(messages: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Rule 3 and the compactions, from OpenCode's own messages (the full history; OpenCode keeps it)."""
    turns: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    compactions = []
    for i, m in enumerate(messages):
        info, parts = m.get("info") or {}, m.get("parts") or []
        if info.get("role") == "user":
            if any(p.get("type") == "compaction" for p in parts):
                compactions.append(len(turns))                               # before user turn N+1 ...
                continue
            text = " ".join(str(p.get("text", "")) for p in parts if p.get("type") == "text" and not p.get("synthetic"))
            if not text.strip() or text.startswith("Continue if you have next steps"):
                continue
            cur = {"turn": len(turns) + 1, "changes": 0, "tests_after_last_change": False, "text": text[:80],
                   "after_compaction": bool(compactions), "edits": {"defs": 0, "defs_ok": 0, "tests": 0, "tests_ok": 0}}
            turns.append(cur)
            continue
        if info.get("summary") or info.get("mode") == "compaction" or info.get("agent") == "compaction":
            continue
        if cur is None:
            continue
        for k, x in turn_edits(parts).items():
            cur["edits"][k] += x
        for p in parts:
            if p.get("type") != "tool":
                continue
            tool = str(p.get("tool", "")).lower()
            inp = (p.get("state") or {}).get("input") or {}
            if tool in WRITE and str(inp.get("filePath") or inp.get("path") or "").endswith(".py"):
                cur["changes"] += 1
                cur["tests_after_last_change"] = False
            elif tool == "bash" and re.search(r"\bunittest\b|\bpytest\b", str(inp.get("command", ""))):
                if cur["changes"]:
                    cur["tests_after_last_change"] = True
    changed = [t for t in turns if t["changes"]]

    def total(ts: List[Dict[str, Any]]) -> Dict[str, int]:
        out = {"turns": len(ts), "defs": 0, "defs_ok": 0, "tests": 0, "tests_ok": 0,
               "turns_with_changes": sum(1 for t in ts if t["changes"]),
               "tested_after_change": sum(1 for t in ts if t["changes"] and t["tests_after_last_change"])}
        for t in ts:
            for k, x in t["edits"].items():
                out[k] += x
        return out
    return {"before_compaction": total([t for t in turns if not t["after_compaction"]]),
            "after_compaction": total([t for t in turns if t["after_compaction"]]),
            "turns": len(turns), "turns_with_changes": len(changed),
            "tested_after_change": sum(t["tests_after_last_change"] for t in changed),
            "compactions_before_turn": [c + 1 for c in compactions],
            "per_turn": turns}


def main() -> int:
    import cachematrix as cm
    from agentbench import clients as cl, fixtures as fx, home as hm
    from agentbench.server import CarlServer
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--model", default="qwen3.6-35b-a3b-iq3")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--models-dir", default=os.path.expanduser("~/models/gguf"))
    a = ap.parse_args()
    home = hm.require_harness_home(a.home)
    name = f"{a.arm}-run{a.run}"
    folder = os.path.join(a.out, name)
    if os.path.exists(os.path.join(folder, "grade.json")):
        cb.say(f"{name}: done already")
        return 0
    os.makedirs(folder, exist_ok=True)
    conf = configure(home, a.model, a.arm, os.path.join(os.path.abspath(folder), "hook.jsonl"))
    cb.say(f"{name}: opencode.json {conf}")
    work = a.home.rstrip("/") + "-work"
    setup = cm.MatrixSetup(repo=cb.REPO, model=a.model, work=os.path.join(work, "server"), port=a.port + cb.PROXY_GAP,
                           models_dir=os.path.abspath(a.models_dir), slots=1, conf=os.path.join(home, ".config", "carl"))
    srv = CarlServer(setup)
    srv.start()
    srv.wait_ready()
    proxy = drv = None
    tasks: List[Dict[str, Any]] = []
    answered: List[Dict[str, Any]] = []
    try:
        proxy = subprocess.Popen([sys.executable, os.path.join(cb.TOOLS, "req-capture-proxy.py"), f"127.0.0.1:{a.port}",
                                  f"127.0.0.1:{a.port + cb.PROXY_GAP}", os.path.join(folder, "requests.jsonl"),
                                  "--bodies", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cwd = fx.make_fresh("pyrules", os.path.join(work, "projects", name))
        if os.path.exists(os.path.join(cwd, "AGENTS.md")):                  # the rules only in the user's words
            subprocess.run(["git", "rm", "-q", "AGENTS.md"], cwd=cwd, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "no AGENTS.md"], cwd=cwd, check=True)
        env = cl.client_env(home)
        drv = cb._drivers("opencode", home, cwd, env, a.model)
        time.sleep(1)
        drv.start()
        sid = drv.new()
        answered: List[Dict[str, Any]] = []
        stop = threading.Event()

        def answer_questions() -> None:
            """A stand-in user for OpenCode's question tool (hook run 1, 2026-10-15: the model asked "Which edge cases
            should I fix?" and the turn waited for a person): every pending question of this session is answered with
            all its options, as the turn's text had asked for all of them."""
            while not stop.wait(10):
                try:
                    for q in drv._call("GET", "/question") or []:
                        if q.get("sessionID") != sid:
                            continue
                        ans = [[o.get("label") for o in (x.get("options") or [])] for x in q.get("questions") or []]
                        drv._call("POST", f"/question/{q['id']}/reply", {"answers": ans})
                        answered.append({"turn": len(tasks) + 1, "questions": [x.get("question") for x in
                                                                               q.get("questions") or []], "answers": ans})
                except Exception:  # noqa: BLE001  (the next poll tries again)
                    pass
        threading.Thread(target=answer_questions, daemon=True).start()
        prompts = {p.id: p for p in __import__("agentbench.prompts", fromlist=["load_prompts"]).load_prompts()}
        for k, item in enumerate(ITEMS):
            text = cb.item_text(item, prompts)
            if k == 0:
                text = SAID + "\n\n" + text
            t0 = time.time()
            try:
                reply = drv.say(sid, text)
            except Exception as e:  # noqa: BLE001  (one turn's failure is data; the session goes on)
                reply = f"error: {e}"
            tasks.append({"turn": k + 1, "item": item[:60], "seconds": round(time.time() - t0, 1), "reply": reply[:200]})
            cb.say(f"{name}: turn {k + 1}/{len(ITEMS)} {item[:40]} in {time.time() - t0:.0f} s")
        stop.set()
        messages = drv._call("GET", f"/session/{sid}/message") or []
        with open(os.path.join(folder, "messages.json"), "w", encoding="utf-8") as f:
            json.dump(messages, f)
    finally:
        if drv is not None:
            drv.stop()
        if proxy is not None:
            proxy.terminate()
            proxy.wait(10)
        srv.stop()
    fixture_dir = os.path.join(fx.FIXTURES_DIR, "pyrules")
    grade = {"arm": a.arm, "run": a.run, "opencode": conf, "tasks": tasks, "questions_answered": answered,
             "project": grade_project(cwd, fixture_dir), "session": grade_session(messages)}
    hidden = fx.check_hidden(cwd, "pyrules", HIDDEN)
    grade["hidden"] = {k: {"passed": v.passed, "output": str(v.output)[-1500:]} for k, v in hidden.items()}
    with open(os.path.join(folder, "grade.json"), "w", encoding="utf-8") as f:
        json.dump(grade, f, indent=1)
    p, s = grade["project"], grade["session"]
    cb.say(f"{name}: Does: {p['with_does']}/{p['functions_added_or_changed']}, test_rule_ {p['named_test_rule']}/"
           f"{p['new_tests']}, tests after a change {s['tested_after_change']}/{s['turns_with_changes']}, "
           f"compactions before turns {s['compactions_before_turn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

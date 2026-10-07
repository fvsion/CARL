"""Helpers for the variants (tools/agent-bench/variants/): change the delegation rule and the coder agent in a harness
HOME the way CARL's client setup writes them, so a variant only supplies its texts.

The texts use the markers of client/agents/delegation.md (<!-- carl:background opencode --> ...), and the rule for
each client is cut from them with client/configure.py's own delegation_for(), with the same background and browser
switches as the installed rule. Every change is idempotent: apply() runs again after each config refresh, and with
--no-server on its own output.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Dict, List, Optional

from . import BENCH_DIR, REPO

TEXTS_DIR = os.path.join(BENCH_DIR, "variants", "texts")
_configure: Optional[ModuleType] = None


def configure() -> ModuleType:
    """client/configure.py, loaded once (its rule, agent and APPEND_SYSTEM helpers)."""
    global _configure
    if _configure is None:
        path = os.path.join(REPO, "client", "configure.py")
        spec = importlib.util.spec_from_file_location("carl_client_configure", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod                  # its dataclasses look the module up
        spec.loader.exec_module(mod)
        _configure = mod
    return _configure


def text(name: str) -> str:
    """A variant text from variants/texts/."""
    with open(os.path.join(TEXTS_DIR, name), encoding="utf-8") as f:
        return f.read()


@dataclass(frozen=True)
class Paths:
    """The files of the delegation rule and the coder in a harness HOME (as CARL's setup writes them)."""
    oc_json: str
    oc_rule: str
    oc_coder: str
    pi_append: str
    pi_agents: str

    @staticmethod
    def of(home: str) -> "Paths":
        oc = os.path.join(home, ".config", "opencode")
        pi = os.path.join(home, ".pi", "agent")
        return Paths(os.path.join(oc, "opencode.json"), os.path.join(oc, "carl", "delegation.md"),
                     os.path.join(oc, "carl", "coder.md"), os.path.join(pi, "APPEND_SYSTEM.md"),
                     os.path.join(pi, "agents"))


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path: str, body: str) -> bool:
    """Write when the text differs; True when it changed."""
    if _read(path) == body:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)
    return True


@dataclass(frozen=True)
class Switches:
    """The rule's switches as the installed rule shows them (CARL's setup decided them)."""
    background: bool
    browser: bool


def switches(installed_rule: str) -> Switches:
    """From CARL's rule or a variant's rule (every rule text says these the same way, so a variant reads its own
    output the same)."""
    return Switches(background="background: true" in installed_rule,
                    browser="check by hand" not in installed_rule)


def coder_name(home: str) -> str:
    """The installed coder's name: "coder", or "carl-coder" next to a user's own coder."""
    cfg = _json(Paths.of(home).oc_json)
    agents = cfg.get("agent", {}) if isinstance(cfg.get("agent"), dict) else {}
    for name in ("coder", "carl-coder"):
        a = agents.get(name)
        if isinstance(a, dict) and "carl" in str(a.get("prompt", "")):
            return name
    return "coder"


def _json(path: str) -> Dict[str, Any]:
    raw = _read(path)
    if raw is None:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def set_rule(home: str, rule_text: str, last: bool = False) -> List[str]:
    """Put a delegation rule (with client markers) in place of CARL's, in both clients. last: OpenCode's rule moves
    to the end of the instructions (Pi's is the last block of APPEND_SYSTEM.md already)."""
    cf, p, changed = configure(), Paths.of(home), []
    cur_oc = _read(p.oc_rule)
    if cur_oc is None:
        raise RuntimeError(f"no CARL delegation rule in {p.oc_rule}: run ./setup first")
    sw, name = switches(cur_oc), coder_name(home)
    oc_rule = cf.oc_rule_text(cf.name_agent(cf.delegation_for(rule_text, "opencode", sw.background, sw.browser), name))
    if _write(p.oc_rule, oc_rule):
        changed.append(p.oc_rule)
    if last:
        cfg = _json(p.oc_json)
        ins = [x for x in cfg.get("instructions", []) if x != p.oc_rule] + [p.oc_rule]
        if cfg.get("instructions") != ins:
            cfg["instructions"] = ins
            _write(p.oc_json, json.dumps(cfg, indent=2) + "\n")
            changed.append(p.oc_json)
    pi_rule = cf.name_agent(cf.delegation_for(rule_text, "pi", sw.background, sw.browser).strip(), name)
    cur_pi = _read(p.pi_append) or ""
    if _write(p.pi_append, cf.append_system_text(cur_pi, pi_rule)):
        changed.append(p.pi_append)
    return changed


def set_coder(home: str, agent_text: str) -> List[str]:
    """Put a coder agent file (front matter with the description, then the prompt) in place of CARL's coder, in
    both clients: OpenCode's prompt file and description, Pi's agent file."""
    cf, p, changed = configure(), Paths.of(home), []
    name = coder_name(home)
    named = cf.name_agent(agent_text, name)
    body, desc = cf.split_agent(named)
    if _write(p.oc_coder, body):
        changed.append(p.oc_coder)
    cfg = _json(p.oc_json)
    agent = cfg.get("agent", {}).get(name)
    if isinstance(agent, dict) and agent.get("description") != desc:
        agent["description"] = desc
        _write(p.oc_json, json.dumps(cfg, indent=2) + "\n")
        changed.append(p.oc_json)
    if _write(os.path.join(p.pi_agents, f"{name}.md"), named):
        changed.append(os.path.join(p.pi_agents, f"{name}.md"))
    return changed


def without_markers(rule_text: str) -> str:
    """A rule's text with every client block (for tests and reading)."""
    return re.sub(r"<!-- carl:[a-z]+ [a-z]+ -->\n?", "", rule_text)


HOOKS = "carl-delegate-hooks"
HOOKS_SRC = os.path.join(BENCH_DIR, "variants", "plugins", HOOKS)


def install_hooks(home: str, mode: str) -> List[str]:
    """CARL's delegation hooks (variants/plugins/carl-delegate-hooks) in both clients, in one mode: nudge (V4), gate
    (V5), both, or remind (V2, rough). OpenCode: a server plugin in plugins/ and an entry in opencode.json's plugin
    list; Pi: an extension in extensions/."""
    if not mode or any(not re.fullmatch(r"nudge|gate(:[1-9][0-9]?)?|both|remind", m) for m in mode.split(",")):
        raise ValueError(f"bad hook mode {mode!r} (nudge, gate or gate:N, both, remind, or several joined with commas)")
    p, changed = Paths.of(home), []
    shared = _read(os.path.join(HOOKS_SRC, "delegate-hooks.js")) or ""

    def put(path: str, body: str) -> None:
        if _write(path, body):
            changed.append(path)

    oc_dir = os.path.join(os.path.dirname(p.oc_json), "plugins", HOOKS)
    put(os.path.join(oc_dir, "delegate-hooks.js"), shared)
    oc_js = _read(os.path.join(HOOKS_SRC, "opencode.js")) or ""
    put(os.path.join(oc_dir, "index.js"), oc_js.replace("__MODE__", mode))
    put(os.path.join(oc_dir, "package.json"), json.dumps({
        "name": HOOKS, "version": "0.0.0", "type": "module", "private": True,
        "exports": {".": {"import": "./index.js"}, "./server": {"import": "./index.js"}},
        "peerDependencies": {"@opencode-ai/plugin": ">=1.14.0"}}, indent=2) + "\n")
    cfg = _json(p.oc_json)
    entry = "file:" + oc_dir
    plugins = [x for x in cfg.get("plugin", []) if not (x == entry or (isinstance(x, list) and x and x[0] == entry))]
    plugins.append([entry, {}])
    if cfg.get("plugin") != plugins:
        cfg["plugin"] = plugins
        _write(p.oc_json, json.dumps(cfg, indent=2) + "\n")
        changed.append(p.oc_json)
    pi_dir = os.path.join(os.path.dirname(p.pi_append), "extensions", HOOKS)
    put(os.path.join(pi_dir, "delegate-hooks.js"), shared)
    put(os.path.join(pi_dir, "index.ts"), (_read(os.path.join(HOOKS_SRC, "pi.ts")) or "").replace("__MODE__", mode))
    return changed


def remove_hooks(home: str) -> List[str]:
    """Take CARL's delegation hooks out of both clients. CARL's setup keeps plugins it does not own, so a hook
    variant's plugin stays after a config refresh: every variant without hooks (baseline too) calls this first, so
    no variant runs with the hooks of the one before (found 2026-10-06)."""
    p, changed = Paths.of(home), []
    oc_dir = os.path.join(os.path.dirname(p.oc_json), "plugins", HOOKS)
    pi_dir = os.path.join(os.path.dirname(p.pi_append), "extensions", HOOKS)
    for d in (oc_dir, pi_dir):
        if os.path.isdir(d):
            shutil.rmtree(d)
            changed.append(d)
    cfg = _json(p.oc_json)
    entry = "file:" + oc_dir
    plugins = cfg.get("plugin", [])
    kept = [x for x in plugins if not (x == entry or (isinstance(x, list) and x and x[0] == entry))]
    if kept != plugins:
        cfg["plugin"] = kept
        _write(p.oc_json, json.dumps(cfg, indent=2) + "\n")
        changed.append(p.oc_json)
    return changed

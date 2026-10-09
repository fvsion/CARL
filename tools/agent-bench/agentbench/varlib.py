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
from typing import Any, Dict, List, Optional, Tuple

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


def carl_config(home: str) -> str:
    return os.path.join(home, ".config", "carl", "config.json")


def set_move(home: str, on: bool) -> List[str]:
    """carl-cache's move of the project part (OpenCode's <env> and AGENTS.md, Pi's project context) to the user
    message: on is CARL's default (no setting); off writes "cache": {"prefix": false} to the HOME's
    ~/.config/carl/config.json (the harness's own file, marked), which also turns off the saved prompts. Every
    variant sets it (variant.py), so no variant runs with the setting of the one before."""
    path = carl_config(home)
    cur = _json(path)
    if on:
        if cur.get("_agent_bench") and os.path.exists(path):
            os.remove(path)
            return [path]
        return []
    want = {"_agent_bench": "Phase 23.1: carl-cache's move off (variant project_in_system)", "cache": {"prefix": False}}
    if cur and not cur.get("_agent_bench"):
        raise RuntimeError(f"{path} is not the harness's: will not change it")
    return [path] if _write(path, json.dumps(want, indent=2) + "\n") else []


def read_text(path: str) -> Optional[str]:
    """A file's text, or None when it does not exist."""
    return _read(path)


# ------------------------------------------------------------------------------------------------ the brief (23.4.3)
DELEGATION = "carl-delegation"
BRIEF_FORMATS = ("toml", "json", "kv")


def pi_carl_json(home: str) -> str:
    """Pi's carl.json (CARL's state file in Pi's agent folder: carl-delegation and the subagent tool read it)."""
    return os.path.join(os.path.dirname(Paths.of(home).pi_append), "carl.json")


def pi_subagent_ts(home: str) -> str:
    """CARL's subagent extension for Pi (./setup installs it whole at every refresh)."""
    return os.path.join(os.path.dirname(Paths.of(home).pi_append), "extensions", "subagent", "index.ts")


def _is_delegation_entry(x: Any) -> bool:
    entry = x[0] if isinstance(x, list) and x else x
    return isinstance(entry, str) and entry.rstrip("/").split("/")[-1] == DELEGATION


def _with(opts: Dict[str, Any], want: Dict[str, Any]) -> Dict[str, Any]:
    """opts with the keys of want set (None: taken out)."""
    out = dict(opts)
    for k, v in want.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def set_brief(home: str, check: bool = True, chain: bool = True, fmt: str = "toml", strict: bool = False) -> List[str]:
    """CARL's brief check, the chain (the test session before the code session) and the format that a refusal of a
    task with no brief names: OpenCode's carl-delegation options (brief, chain, briefFormat in opencode.json's plugin
    entry) and Pi's carl.json "delegation" (brief, chain, brief_format). CARL's defaults (on, on, TOML) take the keys
    out: CARL's setup writes none. strict: carl-delegation must be in both clients (a variant that turns the check
    off must never run with it on)."""
    if fmt not in ("toml", "json"):
        raise ValueError(f"bad brief format {fmt!r} (toml or json)")
    p, changed = Paths.of(home), []
    cfg = _json(p.oc_json)
    raw = cfg.get("plugin")
    plugins: List[Any] = raw if isinstance(raw, list) else []
    idx = [i for i, x in enumerate(plugins) if _is_delegation_entry(x)]
    if strict and not idx:
        raise RuntimeError(f"no {DELEGATION} plugin in {p.oc_json}: run ./setup with the coder first")
    want = {"brief": None if check else False, "chain": None if chain else False,
            "briefFormat": None if fmt == "toml" else fmt}
    edit = False
    for i in idx:
        x = plugins[i]
        entry = x[0] if isinstance(x, list) else x
        opts = x[1] if isinstance(x, list) and len(x) > 1 and isinstance(x[1], dict) else {}
        new = _with(opts, want)
        if not isinstance(x, list) or new != opts:
            plugins[i] = [entry, new]
            edit = True
    if edit:
        cfg["plugin"] = plugins
        _write(p.oc_json, json.dumps(cfg, indent=2) + "\n")
        changed.append(p.oc_json)
    path = pi_carl_json(home)
    st = _json(path)
    if strict and not st:
        raise RuntimeError(f"no {path}: run ./setup with the coder first")
    d = st.get("delegation") if isinstance(st.get("delegation"), dict) else None
    if d is not None or not (check and chain and fmt == "toml"):
        new_d = _with(d or {}, {"brief": want["brief"], "chain": want["chain"], "brief_format": want["briefFormat"]})
        if new_d != (d or {}) and st:
            st["delegation"] = new_d
            if _write(path, json.dumps(st, indent=2) + "\n"):
                changed.append(path)
    return changed


# The coder lines of the guidelines of Pi's subagent tool (client/pi/extensions/subagent/index.ts), for each format
# of the brief: CARL's (TOML), the same for JSON, and 1.12.1's (kv: "a self-contained task").
_LARGE = ("Large request (3+ files, ~150+ lines, a new module/package/tool/CLI, implementation plus tests, a multi-step "
          "feature or refactor): ")
_STUCK = ("Stuck: if a fix for the same code has already failed twice (your attempts, or ones the user says failed), "
          'delegate to agent "${coder}" with ')
PI_GUIDELINES: Dict[str, Tuple[str, str]] = {
    "toml": (_LARGE + "do not write or change any file yourself. Read only what the brief needs (the files to change, "
             "the test command, the project's rules), then call the subagent tool with agent \"${coder}\" and a TOML "
             "brief as its task (the format is in your instructions).",
             _STUCK + "a brief that has the exact error ([error]) and what was tried ([[tried]]), instead of a third "
             "attempt."),
    "json": (_LARGE + "do not write or change any file yourself. Read only what the brief needs (the files to change, "
             "the test command, the project's rules), then call the subagent tool with agent \"${coder}\" and a JSON "
             "brief as its task (the format is in your instructions).",
             _STUCK + 'a brief that has the exact error ("error") and what was tried ("tried"), instead of a third '
             "attempt."),
    "kv": (_LARGE + 'your FIRST action is the subagent tool with agent "${coder}" and a self-contained task. Do not '
           "start writing it yourself.",
           _STUCK + "the code, the exact error and what was tried, instead of a third attempt."),
}


def set_pi_guidelines(home: str, fmt: str, strict: bool = False) -> List[str]:
    """The coder lines of Pi's subagent tool guidelines for one brief format (CARL's names TOML). strict: the
    installed extension must have them (in one of the three forms)."""
    if fmt not in PI_GUIDELINES:
        raise ValueError(f"bad brief format {fmt!r} ({', '.join(PI_GUIDELINES)})")
    path = pi_subagent_ts(home)
    cur = _read(path)
    if cur is None:
        if strict:
            raise RuntimeError(f"no {path}: run ./setup with the coder first")
        return []
    new = cur
    for n in (0, 1):
        for lines in PI_GUIDELINES.values():
            new = new.replace(lines[n], PI_GUIDELINES[fmt][n])
    if strict and not all(PI_GUIDELINES[fmt][n] in new for n in (0, 1)):
        raise RuntimeError(f"{path}: the coder lines of the guidelines are not there (a newer extension?)")
    return [path] if _write(path, new) else []


def set_brief_format(home: str, fmt: str) -> List[str]:
    """One brief format in both clients, with the texts of variants/texts/brief_{fmt}_*.md (kv: 1.12.1's rule and
    coder; json: CARL's, with the brief and the report as JSON), the check and the chain (kv: both off; json: on, a
    refusal names JSON) and Pi's tool guidelines. "toml" is CARL's own setup: nothing but the switches and the
    guidelines back to their defaults."""
    if fmt == "toml":
        return set_brief(home) + set_pi_guidelines(home, "toml")
    changed = (remove_hooks(home) + set_rule(home, text(f"brief_{fmt}_delegation.md"))
               + set_coder(home, text(f"brief_{fmt}_coder.md")))
    if fmt == "kv":
        changed += set_brief(home, check=False, chain=False, strict=True)
    else:
        changed += set_brief(home, fmt=fmt, strict=True)
    return changed + set_pi_guidelines(home, fmt, strict=True)

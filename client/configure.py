#!/usr/bin/env python3
"""Merge the CARL client configs into OpenCode and Pi without clobbering
anything the user owns. Called by install.sh (it resolves the key, server
address, context window and whether the coder is on).

What "ours" means: recorded in a state file next to each config
(~/.config/opencode/llm-deploy.json, ~/.pi/agent/llm-deploy.json). Installs
from before the state file existed are recognised by a signature (our
provider names / API-key file path, our prompt text).

Rules
- Providers: ours replace only our own earlier version. If the user has their
  own provider called "llamacpp" / "mtplx", ours is installed alongside as
  "llm-deploy" / "llm-deploy-mtplx" and every reference uses that id.
- Default model (OpenCode model/small_model, Pi defaultProvider/defaultModel/
  defaultThinkingLevel): set only when unset or still the value we set before.
- Agents, prompts, extensions: never overwrite a user's file or agent with the
  same name; ours gets an "llm-deploy-" name or is skipped with a note.
- Lists (plugin, instructions): ours are appended / removed, others kept.
- Every changed file is backed up first (<file>.bak.<timestamp>).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

JsonObj = dict[str, Any]
"""A parsed JSON object (OpenCode / Pi configs are open-ended JSON)."""

ORIGINAL = ".before-carl"      # one copy of each config as it was before CARL first changed it
ALT: dict[str, str] = {"llamacpp": "llm-deploy", "mtplx": "llm-deploy-mtplx"}
OUR_NAMES: frozenset[str] = frozenset({"llama.cpp (Mac host)", "MTPLX (Mac host, ≤48K)", "MTPLX (Mac host)"})
OUR_KEY = "/.config/mtplx/api-key"
# defaults earlier versions set (treated as ours when no state file exists yet)
OLD_DEFAULTS: frozenset[str] = frozenset({
    "llamacpp/qwen3.8-27b-abliterated-llama", "llamacpp/qwen3.8-27b", "llamacpp/qwen3.6-35b-a3b",
    "mtplx/qwen3.8-27b-abliterated-grant", "mtplx/qwen3.8-27b-abliterated"})
DEFAULT_MODEL = "qwen3.6-35b-a3b"
REPORT_KINDS = ("backed up", "added", "updated", "kept", "removed")
# The host is substituted into JSON text and URLs: a hostname, IPv4 or IPv6 literal only.
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]*")


# ------------------------------------------------------------------ arguments
@dataclass(frozen=True)
class Options:
    """What install.sh resolved: where the bundle and home are, the server, the switches."""
    bundle: str          # the client/ directory
    home: str
    host: str
    port: str            # MTPLX
    llama_port: str
    ctx: int
    coder: bool
    sidebar: bool
    switcher: bool

    @property
    def oc_dir(self) -> str:
        return os.path.join(self.home, ".config/opencode")

    @property
    def pi_dir(self) -> str:
        return os.path.join(self.home, ".pi/agent")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.llama_port}/v1"


def host_arg(v: str) -> str:
    if not HOST_RE.fullmatch(v):
        raise argparse.ArgumentTypeError(f"not a host name or address: {v!r}")
    return v


def port_arg(v: str) -> str:
    if not v.isdigit() or not 1 <= int(v) <= 65535:
        raise argparse.ArgumentTypeError(f"not a TCP port: {v!r}")
    return str(int(v))


def ctx_arg(v: str) -> int:
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a token count: {v!r}") from None
    if n < 1024:
        raise argparse.ArgumentTypeError(f"context window too small: {n}")
    return n


def switch_arg(v: str) -> bool:
    if v not in ("0", "1"):
        raise argparse.ArgumentTypeError(f"takes 0 or 1, got {v!r}")
    return v == "1"


def parse_args(argv: list[str]) -> Options:
    ap = argparse.ArgumentParser(description="Merge the CARL client configs into OpenCode and Pi (run by install.sh).")
    ap.add_argument("--bundle", required=True, help="the client/ directory")
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--host", required=True, type=host_arg)
    ap.add_argument("--port", required=True, type=port_arg, help="MTPLX port")
    ap.add_argument("--llama-port", required=True, type=port_arg)
    ap.add_argument("--ctx", type=ctx_arg, required=True)
    ap.add_argument("--coder", type=switch_arg, default=True)
    ap.add_argument("--sidebar", type=switch_arg, default=True)
    ap.add_argument("--switcher", type=switch_arg, default=True)
    a = ap.parse_args(argv)
    return Options(bundle=a.bundle, home=a.home, host=a.host, port=a.port, llama_port=a.llama_port,
                   ctx=a.ctx, coder=a.coder, sidebar=a.sidebar, switcher=a.switcher)


# ------------------------------------------------------------------ pure helpers
def name_agent(text: str, name: str) -> str:
    """Point the coder / delegation texts at the installed agent name."""
    if name == "coder":
        return text
    text = re.sub(r"^name: coder$", f"name: {name}", text, flags=re.M)
    return (text.replace("`coder`", f"`{name}`").replace('"coder"', f'"{name}"')
                .replace("If you ARE the coder subagent", f"If you ARE the {name} subagent")
                .replace("You are **coder**", f"You are **{name}**"))


def split_agent(text: str) -> tuple[str, str]:
    """(body, description) of an agent file with YAML front matter."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError("agent file has no front matter")
    front, body = m.group(1), m.group(2).lstrip()
    d = re.search(r'^description:\s*"(.*)"\s*$', front, re.M)
    if not d:
        raise ValueError("agent front matter has no description")
    return body, d.group(1)


def set_ctx_oc(provider: JsonObj, ctx: int) -> None:
    for m in provider.get("models", {}).values():
        m["limit"]["context"] = ctx
        m["limit"]["output"] = min(m["limit"]["output"], ctx // 2)
        m["name"] = m["name"].replace("128K", f"{ctx // 1024}K")


def set_ctx_pi(provider: JsonObj, ctx: int) -> None:
    for m in provider.get("models", []):
        m["contextWindow"] = ctx
        m["maxTokens"] = min(m["maxTokens"], ctx // 2)
        m["name"] = m["name"].replace("128K", f"{ctx // 1024}K")


def pick_ids(existing: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool]) -> dict[str, str]:
    """Provider ids to install under: ours by default, ALT when the user owns the name."""
    ids: dict[str, str] = {}
    for pid in ("llamacpp", "mtplx"):
        chosen = state.get("providers", {}).get(pid)
        if not chosen:
            cur = existing.get(pid)
            chosen = pid if cur is None or is_ours(cur) else ALT[pid]
        ids[pid] = chosen
    return ids


def ours_oc(p: JsonObj) -> bool:
    return p.get("name") in OUR_NAMES or OUR_KEY in str(p.get("options", {}).get("apiKey", ""))


def ours_pi(p: JsonObj) -> bool:
    return OUR_KEY in str(p.get("apiKey", ""))


def ours_agent(a: object) -> bool:
    return isinstance(a, dict) and ("llm-deploy" in str(a.get("prompt", "")) or "prompts/coder.md" in str(a.get("prompt", "")))


# ------------------------------------------------------------------ files
def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_or_none(path: str) -> str | None:
    return read_text(path) if os.path.exists(path) else None


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load(path: str, default: JsonObj) -> JsonObj:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        sys.exit(f"error: {path} is not plain JSON (comments?). Fix or move it, then re-run; nothing was changed.")
    if not isinstance(data, dict):
        sys.exit(f"error: {path} is not a JSON object. Fix or move it, then re-run; nothing was changed.")
    return data


class Report:
    """What changed, grouped by kind, printed at the end."""

    def __init__(self) -> None:
        self.entries: dict[str, list[str]] = {k: [] for k in REPORT_KINDS}

    def add(self, kind: str, line: str) -> None:
        self.entries[kind].append(line)

    def lines(self) -> list[str]:
        return [f"  {k:9s} {line}" for k in REPORT_KINDS for line in self.entries[k]]


class Installer:
    """Writes the OpenCode and Pi configs for one set of Options."""

    def __init__(self, opts: Options, stamp: str) -> None:
        self.o = opts
        self.stamp = stamp
        self.report = Report()

    # -- file helpers bound to this run (home for display, stamp for backups)
    def short(self, path: str) -> str:
        return path.replace(self.o.home, "~")

    def save(self, path: str, data: JsonObj, mode: int = 0o600, backup: bool = True) -> None:
        self.save_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode, backup)

    def save_text(self, path: str, text: str, mode: int = 0o600, backup: bool = True) -> None:
        """Write only on a change. Before a change to a file that exists: keep the
        original once (FILE.before-carl, never overwritten) and a copy of the
        current version (FILE.bak.<time>)."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            if read_text(path) == text:
                return
            if backup:
                if not os.path.exists(path + ORIGINAL):
                    shutil.copy2(path, path + ORIGINAL)
                    self.report.add("backed up", f"{self.short(path)} -> {os.path.basename(path)}{ORIGINAL} (your original)")
                shutil.copy2(path, f"{path}.bak.{self.stamp}")
        write_text(path, text)
        os.chmod(path, mode)

    def bundle_path(self, rel: str) -> str:
        return os.path.join(self.o.bundle, rel)

    def render(self, text: str) -> str:
        for a, b in (("__MTPLX_HOST__", self.o.host), ("__MTPLX_PORT__", self.o.port),
                     ("__LLAMA_PORT__", self.o.llama_port), ("__HOME__", self.o.home)):
            text = text.replace(a, b)
        return text

    def bundle_json(self, rel: str) -> JsonObj:
        data = json.loads(self.render(read_text(self.bundle_path(rel))))
        if not isinstance(data, dict):
            raise ValueError(f"{rel}: not a JSON object")
        return data

    def coder_text(self) -> str:
        return read_text(self.bundle_path("agents/coder.md"))

    def delegation_text(self) -> str:
        return read_text(self.bundle_path("agents/delegation.md"))

    # ========================================================== OpenCode
    def opencode(self) -> dict[str, str]:
        oc, rep = self.o.oc_dir, self.report
        path = os.path.join(oc, "opencode.json")
        cfg = load(path, {})
        st_path = os.path.join(oc, "llm-deploy.json")
        st = load(st_path, {})
        first_install = not st
        bundle = self.bundle_json("opencode/opencode.json")

        providers = cfg.setdefault("provider", {})
        had_ours_before = any(isinstance(v, dict) and ours_oc(v) for v in providers.values())
        ids = pick_ids(providers, st, ours_oc)
        for pid, new_id in ids.items():
            prov = bundle["provider"][pid]
            if pid == "llamacpp":
                set_ctx_oc(prov, self.o.ctx)
            if new_id != pid:
                prov["name"] = prov["name"] + " [llm-deploy]"
                rep.add("kept", f"OpenCode provider '{pid}' (yours); ours installed as '{new_id}'")
            if providers.get(new_id) != prov:
                rep.add("updated" if new_id in providers else "added", f"OpenCode provider '{new_id}'")
            providers[new_id] = prov

        self._oc_default_model(cfg, st, ids, first_install, had_ours_before)

        # title agent: we no longer disable it; undo our earlier setting
        agent = cfg.setdefault("agent", {})
        if (st.get("title_disabled") or (first_install and had_ours_before)) and agent.get("title") == {"disable": True}:
            agent.pop("title")
            rep.add("removed", "OpenCode agent.title.disable (titles are back on)")
        st.pop("title_disabled", None)

        # plugins (server side): the MTPLX session headers
        plug = os.path.join(oc, "plugins/mtplx-session-headers")
        shutil.rmtree(plug, ignore_errors=True)
        shutil.copytree(self.bundle_path("opencode/plugins/mtplx-session-headers"), plug)
        plist = cfg.setdefault("plugin", [])
        if plug not in plist:
            plist.append(plug)

        self._oc_coder(cfg, st, agent)
        for k in ("instructions", "agent", "plugin"):
            if not cfg.get(k):
                cfg.pop(k, None)
        cfg.setdefault("$schema", "https://opencode.ai/config.json")

        self._oc_tui()

        st.update({"providers": ids, "base_url": self.o.base_url, "updated": self.stamp})
        self.save(path, cfg)
        self.save(st_path, st, backup=False)
        return ids

    def _oc_default_model(self, cfg: JsonObj, st: JsonObj, ids: dict[str, str],
                          first_install: bool, had_ours_before: bool) -> None:
        """model / small_model: only if unset or still what we set before."""
        rep = self.report
        ours_default = f"{ids['llamacpp']}/{DEFAULT_MODEL}"
        prev = {st.get("model"), st.get("small_model")} | (OLD_DEFAULTS if first_install and had_ours_before else set())
        for key in ("model", "small_model"):
            cur = cfg.get(key)
            # small_model follows model: if your default model is your own, your
            # small tasks (titles, summaries) aren't moved to our server either.
            if key == "small_model" and "model" not in st and cur is None:
                continue
            if cur is None or cur in prev:
                if cur != ours_default:
                    cfg[key] = ours_default
                    rep.add("updated" if cur else "added", f"OpenCode {key} = {ours_default}")
                st[key] = ours_default
            else:
                rep.add("kept", f"OpenCode {key} = {cur} (yours)")
                st.pop(key, None)

        if "model" not in st and cfg.get("small_model") in prev and cfg.get("small_model"):
            rep.add("removed", f"OpenCode small_model = {cfg.pop('small_model')} (follows your own default model)")
            st.pop("small_model", None)

    def _oc_coder(self, cfg: JsonObj, st: JsonObj, agent: JsonObj) -> None:
        """The coder agent and its delegation rule (on with --coder 1, removed with 0)."""
        oc, rep = self.o.oc_dir, self.report
        ddir = os.path.join(oc, "llm-deploy")
        for p in (os.path.join(oc, "prompts", n) for n in ("coder.md", "delegation.md")):
            # earlier versions kept them in prompts/
            old = read_or_none(p)
            if old is not None and old.lstrip().startswith(("You are **coder**", "## Delegating to the coder")):
                os.remove(p)
                cfg["instructions"] = [x for x in cfg.get("instructions", []) if x != p]
        name = st.get("coder_agent") or ("coder" if "coder" not in agent or ours_agent(agent["coder"]) else "llm-deploy-coder")
        if name != "coder" and "coder" in agent and not st.get("coder_agent"):
            rep.add("kept", "OpenCode agent 'coder' (yours); ours is 'llm-deploy-coder'")
        rule_path = os.path.join(ddir, "delegation.md")
        if not self.o.coder:
            if name in agent and (st.get("coder_agent") == name or ours_agent(agent[name])):
                agent.pop(name)
                rep.add("removed", f"OpenCode agent '{name}' (server has 1 slot, or NO_CODER=1)")
            cfg["instructions"] = [x for x in cfg.get("instructions", []) if x != rule_path]
            shutil.rmtree(ddir, ignore_errors=True)
            st.pop("coder_agent", None)
            return
        body, desc = split_agent(self.coder_text())
        prompt_path = os.path.join(ddir, "coder.md")
        os.makedirs(ddir, exist_ok=True)
        for p, t, what in ((prompt_path, name_agent(body, name), "coder prompt"),
                           (rule_path, name_agent(self.delegation_text(), name), "delegation rule")):
            old = read_or_none(p)
            if old is not None and old != t:
                rep.add("updated", f"OpenCode {what} ({self.short(p)})")
            write_text(p, t)
        new_agent = {
            "description": desc,
            "mode": "subagent",
            "prompt": "{file:" + prompt_path + "}",
            "options": {"reasoningEffort": "medium"},
            # thinking on + temperature 0.6 (Qwen's coding value): the best of 9 coder
            # runs on the 35B (2026-10-02: all functions typed, more tests, 26% faster
            # than 1.0); the 27B uses the same value without its own test.
            "temperature": 0.6,
            "permission": {"task": "deny"},
            "steps": 80,
            "color": "secondary",
        }
        if agent.get(name) != new_agent:
            rep.add("updated" if name in agent else "added", f"OpenCode agent '{name}' + delegation rule")
        agent[name] = new_agent
        ins = cfg.setdefault("instructions", [])
        if rule_path not in ins:
            ins.append(rule_path)
        st["coder_agent"] = name

    def _oc_tui(self) -> None:
        """TUI plugins: subagents sidebar, session switcher (in the prompt box)."""
        oc, rep = self.o.oc_dir, self.report
        tui_path = os.path.join(oc, "tui.json")
        tui = load(tui_path, {})
        tplug = tui.setdefault("plugin", [])
        for name, label, want in (("subagents-sidebar", "OpenCode subagents sidebar", self.o.sidebar),
                                  ("session-switcher", "OpenCode session switcher", self.o.switcher)):
            dest = os.path.join(oc, "plugins", name)
            entry = "file:" + dest
            if want:
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(self.bundle_path(os.path.join("opencode/plugins", name)), dest)
                if entry not in tplug:
                    tplug.append(entry)
                    rep.add("added", f"{label} (tui.json)")
            elif entry in tplug:
                tplug.remove(entry)
                shutil.rmtree(dest, ignore_errors=True)
                rep.add("removed", label)
        if not tplug:
            tui.pop("plugin")
        if tui or os.path.exists(tui_path):
            self.save(tui_path, tui)

    # ========================================================== Pi
    def pi(self) -> dict[str, str]:
        pi, rep = self.o.pi_dir, self.report
        path = os.path.join(pi, "models.json")
        cfg = load(path, {})
        st_path = os.path.join(pi, "llm-deploy.json")
        st = load(st_path, {})
        first_install = not st
        bundle = self.bundle_json("pi/models.json")

        providers = cfg.setdefault("providers", {})
        had_ours_before = any(isinstance(v, dict) and ours_pi(v) for v in providers.values())
        ids = pick_ids(providers, st, ours_pi)
        for pid, new_id in ids.items():
            prov = bundle["providers"][pid]
            if pid == "llamacpp":
                set_ctx_pi(prov, self.o.ctx)
            if new_id != pid:
                rep.add("kept", f"Pi provider '{pid}' (yours); ours installed as '{new_id}'")
            if providers.get(new_id) != prov:
                rep.add("updated" if new_id in providers else "added", f"Pi provider '{new_id}'")
            providers[new_id] = prov
        self.save(path, cfg)

        self._pi_settings(st, ids, first_install, had_ours_before)
        self._pi_extensions_and_coder(st)

        st.update({"providers": ids, "base_url": self.o.base_url, "updated": self.stamp})
        self.save(st_path, st, backup=False)
        return ids

    def _pi_settings(self, st: JsonObj, ids: dict[str, str], first_install: bool, had_ours_before: bool) -> None:
        """settings.json defaults: only when unset or still ours."""
        rep = self.report
        sp = os.path.join(self.o.pi_dir, "settings.json")
        sett = load(sp, {})
        want = {"defaultProvider": ids["llamacpp"], "defaultModel": DEFAULT_MODEL, "defaultThinkingLevel": "low"}
        prev = st.get("settings", {})
        legacy = first_install and had_ours_before and sett.get("defaultProvider") == "llamacpp"
        if all(sett.get(k) is None or sett.get(k) == prev.get(k) or legacy for k in want):
            changed = {k: v for k, v in want.items() if sett.get(k) != v}
            sett.update(want)
            if changed:
                rep.add("updated", "Pi defaults " + ", ".join(f"{k}={v}" for k, v in changed.items()))
            st["settings"] = want
            self.save(sp, sett)
        else:
            rep.add("kept", f"Pi defaults (yours: {sett.get('defaultProvider')}/{sett.get('defaultModel')})")
            st.pop("settings", None)

    def _pi_extensions_and_coder(self, st: JsonObj) -> None:
        """The request-policy extension, the subagent extension, the coder agent and
        the delegation rule in APPEND_SYSTEM.md."""
        pi, rep = self.o.pi_dir, self.report
        ext = os.path.join(pi, "extensions")
        os.makedirs(ext, exist_ok=True)
        pol = os.path.join(ext, "mtplx-request-policy.ts")
        cur_pol = read_or_none(pol)
        if cur_pol is None or "Pi <-> MTPLX request bridge" in cur_pol:
            write_text(pol, read_text(self.bundle_path("pi/extensions/mtplx-request-policy.ts")))
        else:
            rep.add("kept", "Pi extensions/mtplx-request-policy.ts (yours)")

        sub = os.path.join(ext, "subagent")
        sub_index = read_or_none(os.path.join(sub, "index.ts"))
        sub_ours = (os.path.isdir(sub) and "LLM-Deploy" in sub_index) if sub_index is not None else not os.path.exists(sub)
        agents_dir = os.path.join(pi, "agents")
        name = st.get("coder_agent") or "coder"
        cpath = os.path.join(agents_dir, f"{name}.md")
        existing = read_or_none(cpath)
        if name == "coder" and existing is not None and "You are **coder**" not in existing:
            name, cpath = "llm-deploy-coder", os.path.join(agents_dir, "llm-deploy-coder.md")
            rep.add("kept", "Pi agents/coder.md (yours); ours is 'llm-deploy-coder'")
        asp = os.path.join(pi, "APPEND_SYSTEM.md")
        cur = read_or_none(asp) or ""
        begin, end = "<!-- llm-deploy:delegation begin -->", "<!-- llm-deploy:delegation end -->"
        cur = re.sub(r"\n*" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", cur, flags=re.S).strip()
        if self.o.coder:
            if sub_ours:
                shutil.rmtree(sub, ignore_errors=True)
                shutil.copytree(self.bundle_path("pi/extensions/subagent"), sub)
                st["subagent_ext"] = True
            else:
                rep.add("kept", "Pi extensions/subagent (yours; it provides the subagent tool ours would)")
            os.makedirs(agents_dir, exist_ok=True)
            new_text = name_agent(self.coder_text(), name)
            old_text = read_or_none(cpath)
            if old_text != new_text:
                rep.add("updated" if old_text is not None else "added", f"Pi agent '{name}' + delegation rule")
            write_text(cpath, new_text)
            rule = name_agent(self.delegation_text().strip(), name)
            cur = (cur + "\n\n" if cur else "") + f"{begin}\n{rule}\n{end}"
            st["coder_agent"] = name
        else:
            if sub_ours and os.path.isdir(sub):
                shutil.rmtree(sub)
            if "specialist software engineer" in (read_or_none(cpath) or ""):
                os.remove(cpath)
                rep.add("removed", f"Pi agent '{name}' (server has 1 slot, or NO_CODER=1)")
            st.pop("coder_agent", None)
            st.pop("subagent_ext", None)
        if cur or os.path.exists(asp):
            self.save_text(asp, cur + "\n" if cur else "", mode=0o644)


def main(argv: list[str]) -> int:
    opts = parse_args(argv)
    inst = Installer(opts, time.strftime("%Y%m%d-%H%M%S"))
    oc_ids = inst.opencode()
    pi_ids = inst.pi()
    for line in inst.report.lines():
        print(line)
    print(f"OpenCode: {opts.oc_dir}/opencode.json (providers: {', '.join(oc_ids.values())})")
    print(f"Pi:       {opts.pi_dir}/models.json (providers: {', '.join(pi_ids.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Merge the CARL client configs into OpenCode and Pi without clobbering
anything the user owns. Called by install.sh (it resolves the key, server
address, context window and whether the coder is on).

What "ours" means: recorded in a state file next to each config
(~/.config/opencode/carl.json, ~/.pi/agent/carl.json). Installs from before
the state file existed are recognised by a signature (our provider names /
API-key file path, our prompt text).

Rules
- Models: one entry per model installed on the server (carl_models.py, from
  installed-models.json or the server's /v1/models), each served under its own
  name with its family's thinking options; the block is rewritten each run, so
  a deleted model's entry goes and a new one appears.
- Providers: ours replace only our own earlier version. If the user has their
  own provider called "llamacpp", ours is installed alongside as "carl" and
  every reference uses that id.
- Renamed in 1.2.0 (CARL was called LLM-Deploy): an install with the old names
  (OLD_NAMES: state files, prompt folder, provider id, markers) is moved to the
  new ones; only CARL's own pieces, recognised as above.
- Removed in 1.2.0 (CARL is llama.cpp only): our MTPLX provider ("mtplx" /
  "llm-deploy-mtplx"), the OpenCode plugin mtplx-session-headers and the Pi
  extension mtplx-request-policy.ts are taken out of an earlier install; a
  provider, plugin or extension of the user's own with those names stays.
- Default model (OpenCode model/small_model, Pi defaultProvider/defaultModel/
  defaultThinkingLevel): set only when unset or still the value we set before.
- Agents, prompts, extensions: never overwrite a user's file or agent with the
  same name. CARL's coder subagent is "coder", or "carl-coder" when the user has
  an agent "coder" of their own (before 1.2.0 that fallback was "llm-deploy-coder":
  ours under the other names is taken out); a user's own "carl-coder" too: ours
  is skipped with a note.
- Lists (plugin, instructions): ours are appended / removed, others kept.
- Tools (Phase 8, measured: OpenCode's system prompt + tools stay under 11K tokens):
  OpenCode's web search (Exa, or Parallel; the queries leave the Mac) and background
  subagents are switched on by environment variables in ~/.config/carl/opencode.env, which
  one marked block in the shell profile (~/.zshrc, ~/.bashrc) sources; the LSP tool with
  "lsp": true (OpenCode then downloads and runs language servers; --lsp 0 leaves both out).
  Each model entry asks for parallel tool calls (carl_models.py). Pi: grep, find
  and ls on (settings.json defaultTools) and the same web search as an MCP server
  (mcp.json "carl-web-search"). Ours only: a user's own defaultTools, MCP server or "lsp"
  stays.
- Browser (Playwright MCP, pinned, an isolated Chrome profile): in OpenCode its tools are off
  for the main agents and on for a "browser" subagent ("carl-browser" next to a user's own
  "browser"), so the main prompt stays under 10.5K tokens (measured: the 26 browser tools
  are ~4.8K); in Pi a deferred MCP server (tool_search loads its tools when needed).
- The prompt cache (client/shared/carl-cache.js): the OpenCode plugin carl-cache (the same kind
  of entry; it replaces carl-prefix-cache) and the Pi extension carl-cache save each session's
  conversation and each agent's prompt on the server's disk and restore them before a request.
  NO_CACHE=1 leaves both out.
- OpenCode plugin carl-model-check (opencode.json "plugin", with our provider id as its
  option): warns when the model picked isn't the one the server runs, isn't installed, or
  is being loaded (router mode). NO_MODEL_CHECK=1 leaves it out.
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

sys.dont_write_bytecode = True                    # no __pycache__ in the client bundle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carl_models  # noqa: E402  (next to this file, in the client bundle)
from carl_models import ModelList  # noqa: E402

JsonObj = dict[str, Any]
"""A parsed JSON object (OpenCode / Pi configs are open-ended JSON)."""

ORIGINAL = ".before-carl"      # one copy of each config as it was before CARL first changed it
PROVIDER = "llamacpp"


@dataclass(frozen=True)
class Names:
    """What CARL's own pieces are called in the clients' folders."""
    alt: str            # our provider's id when the user owns one called PROVIDER
    state: str          # the state file next to each config
    prompt_dir: str     # OpenCode: the folder of the coder prompt and the delegation rule
    block: str          # Pi APPEND_SYSTEM.md: <!-- {block}:delegation begin --> ... end -->
    ext_marker: str     # marks CARL's changes in the vendored Pi subagent extension
    key_file: str       # the API-key file our providers read (under the home folder)


NAMES = Names(alt="carl", state="carl.json", prompt_dir="carl", block="carl", ext_marker="CARL:",
              key_file=".config/carl/api-key")
# Before the rename in 1.2.0 (CARL was called LLM-Deploy): recognised as ours and moved to NAMES.
OLD_NAMES = Names(alt="llm-deploy", state="llm-deploy.json", prompt_dir="llm-deploy", block="llm-deploy",
                  ext_marker="LLM-Deploy:", key_file=".config/llm-deploy/api-key")
ALT = NAMES.alt
OUR_NAMES: frozenset[str] = frozenset({"llama.cpp (Mac host)", "MTPLX (Mac host, ≤48K)", "MTPLX (Mac host)"})
# The API-key files our providers read: now, before the rename, and before 1.2.0 (all recognised as ours).
OUR_KEYS = ("/" + NAMES.key_file, "/" + OLD_NAMES.key_file, "/.config/mtplx/api-key")
# Pieces of the MTPLX support removed in 1.2.0 (taken out of an earlier install).
OLD_MTPLX_IDS = ("mtplx", "llm-deploy-mtplx")
OLD_OC_PLUGIN = "plugins/mtplx-session-headers"
OLD_OC_PLUGIN_SIG = "MTPLXSessionHeaders"
OLD_PI_EXT = "extensions/mtplx-request-policy.ts"
OLD_PI_EXT_SIG = "Pi <-> MTPLX request bridge"
WEB_SEARCH = ("exa", "parallel", "off")
SEARCH_MCP = {"exa": "https://mcp.exa.ai/mcp", "parallel": "https://search.parallel.ai/mcp"}
SEARCH_NAME = "carl-web-search"                         # Pi's MCP server entry
PI_TOOLS = ["+grep", "+find", "+ls"]                    # Pi's built-in tools that are off by default
ENV_FILE = "opencode.env"                               # in ~/.config/carl: OpenCode's tool switches
PROFILE_BEGIN, PROFILE_END = "# >>> CARL: OpenCode tool switches >>>", "# <<< CARL <<<"
BROWSER_MCP = "carl-browser"                            # the MCP server (OpenCode tools: carl-browser_*)
BROWSER_PKG = "@playwright/mcp@0.0.83"                  # pinned: measured 2026-10-03 (26 tools, ~4.8K tokens)
BROWSER_AGENT, BROWSER_AGENT_ALT = "browser", "carl-browser"
BROWSER_OFF = ("bash", "edit", "write", "lsp", "task", "todowrite", "question", "skill")   # not for the browser agent
CHROME_APP = "/Applications/Google Chrome.app"
MODEL_CHECK = "carl-model-check"
CACHE = "carl-cache"                                    # the prompt cache: OpenCode plugin and Pi extension
OLD_CACHE = "carl-prefix-cache"                         # its OpenCode plugin before 11.5 (removed)
CACHE_CORE = "shared/carl-cache.js"                     # the code both carry
CODER = "coder"                                         # the coder subagent's name in both clients
CODER_ALT = "carl-coder"                                # ... when the user has their own "coder"
CODER_NAMES = (CODER, CODER_ALT, "llm-deploy-coder")    # every name CARL used (the last before 1.2.0)
CODER_FILES = ("coder.md", "delegation.md")             # OpenCode: in the prompt folder
CODER_SIG = "specialist software engineer"              # in our coder prompt (the Pi agent file)
# defaults earlier versions set (treated as ours when no state file exists yet)
OLD_DEFAULTS: frozenset[str] = frozenset(
    {f"{p}/{m}" for p in (PROVIDER, OLD_NAMES.alt)
     for m in ("qwen3.8-27b-abliterated-llama", "qwen3.8-27b", "qwen3.6-35b-a3b")}
    | {"mtplx/qwen3.8-27b-abliterated-grant", "mtplx/qwen3.8-27b-abliterated"})
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
    llama_port: str
    ctx: int             # the running model's window per slot (the server's /props)
    models: ModelList    # the installed models (installed-models.json)
    running: str | None  # the model the server runs now (it gets ctx)
    coder: bool
    sidebar: bool
    switcher: bool
    model_check: bool
    web_search: str = "exa"   # exa | parallel | off
    lsp: bool = True
    background: bool = True
    browser: bool = True
    browser_headed: bool = False
    cache: bool = True
    profile: bool = True      # append the pointer to ~/.zshrc / ~/.bashrc (NO_PROFILE=1: print it instead)

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
    ap.add_argument("--llama-port", required=True, type=port_arg)
    ap.add_argument("--ctx", type=ctx_arg, required=True)
    ap.add_argument("--models", required=True, help="installed-models.json (tools/carl.py client-models)")
    ap.add_argument("--running", default=None, help="the model id the server runs now")
    ap.add_argument("--coder", type=switch_arg, default=True)
    ap.add_argument("--sidebar", type=switch_arg, default=True)
    ap.add_argument("--switcher", type=switch_arg, default=True)
    ap.add_argument("--model-check", type=switch_arg, default=True)
    ap.add_argument("--web-search", choices=WEB_SEARCH, default="exa")
    ap.add_argument("--lsp", type=switch_arg, default=True)
    ap.add_argument("--background", type=switch_arg, default=True)
    ap.add_argument("--browser", type=switch_arg, default=True)
    ap.add_argument("--browser-headed", type=switch_arg, default=False)
    ap.add_argument("--profile", type=switch_arg, default=True)
    ap.add_argument("--cache", "--prefix-cache", dest="cache", type=switch_arg, default=True)
    a = ap.parse_args(argv)
    try:
        models = carl_models.load_list(a.models)
    except (OSError, ValueError) as e:
        ap.error(f"--models: {e}")
    return Options(bundle=a.bundle, home=a.home, host=a.host, llama_port=a.llama_port, ctx=a.ctx, models=models,
                   running=a.running, coder=a.coder, sidebar=a.sidebar, switcher=a.switcher,
                   model_check=a.model_check, web_search=a.web_search, lsp=a.lsp, background=a.background,
                   browser=a.browser, browser_headed=a.browser_headed, profile=a.profile,
                   cache=a.cache)


# ------------------------------------------------------------------ pure helpers
def name_agent(text: str, name: str) -> str:
    """Point the coder / delegation texts at the installed agent name."""
    if name == CODER:
        return text
    text = re.sub(r"^name: coder$", f"name: {name}", text, flags=re.M)
    return (text.replace("`coder`", f"`{name}`").replace('"coder"', f'"{name}"')
                .replace("If you ARE the coder subagent", f"If you ARE the {name} subagent")
                .replace("You are **coder**", f"You are **{name}**"))


@dataclass(frozen=True)
class CoderPlan:
    """What to do with the coder agent in one client."""
    name: str                   # the name ours has (or would have)
    install: bool
    remove: tuple[str, ...]     # our entries to take out
    why: str                    # the reason shown for the removals
    kept: tuple[str, ...]       # report lines about the user's own agents


def coder_plan(mine: set[str], has_coder: bool, has_alt: bool, wanted: bool) -> CoderPlan:
    """Name and steps for the coder: "coder", or CODER_ALT when the user owns "coder"; skipped
    when the user also owns that name or the coder is off. mine: the names whose agent is ours."""
    user_coder = has_coder and CODER not in mine
    name = CODER_ALT if user_coder else CODER
    theirs = (has_alt if name == CODER_ALT else has_coder) and name not in mine
    install = wanted and not theirs
    kept: list[str] = []
    if wanted and user_coder:
        kept.append(f"'{CODER}' (yours); ours is '{CODER_ALT}'")
    if wanted and theirs:
        kept.append(f"'{name}' (yours): CARL's coder is not installed")
    why = (f"(ours is now '{name}')" if install else "(server has 1 slot, or NO_CODER=1)" if not wanted
           else f"(your own '{name}' takes its place)")
    return CoderPlan(name, install, tuple(sorted(mine - ({name} if install else set()))), why, tuple(kept))


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


def default_model(ml: ModelList) -> str | None:
    """The model to make the clients' default: the one a server start loads, else the first."""
    return ml.default or (ml.ids[0] if ml.ids else None)


def pick_ids(existing: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool]) -> dict[str, str]:
    """Provider id to install under: ours by default, ALT when the user owns the name
    (also when the state file recorded ALT's name from before the rename)."""
    chosen = state.get("providers", {}).get(PROVIDER)
    if chosen == OLD_NAMES.alt:
        chosen = ALT
    if not chosen:
        cur = existing.get(PROVIDER)
        chosen = PROVIDER if cur is None or is_ours(cur) else ALT
    return {PROVIDER: chosen}


def has_our_key(ref: object) -> bool:
    """An apiKey reference that reads our key file (the current or the pre-1.2.0 path)."""
    return any(k in str(ref) for k in OUR_KEYS)


def ours_oc(p: JsonObj) -> bool:
    return p.get("name") in OUR_NAMES or has_our_key(p.get("options", {}).get("apiKey", ""))


def ours_pi(p: JsonObj) -> bool:
    return has_our_key(p.get("apiKey", ""))


def old_mtplx_ids(providers: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool]) -> list[str]:
    """Our MTPLX provider ids still in a config (the one the state file recorded, else the
    ids earlier versions used), when the entry there is ours."""
    recorded = state.get("providers", {}).get("mtplx")
    ids = [recorded] if isinstance(recorded, str) else list(OLD_MTPLX_IDS)
    return [i for i in ids if isinstance(providers.get(i), dict) and is_ours(providers[i])]


def old_alt_ids(providers: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool]) -> list[str]:
    """Our provider under its id from before the rename (OLD_NAMES.alt), when it is ours:
    the state file recorded it, or it reads our key file."""
    old = providers.get(OLD_NAMES.alt)
    recorded = state.get("providers", {}).get(PROVIDER) == OLD_NAMES.alt
    return [OLD_NAMES.alt] if isinstance(old, dict) and (recorded or is_ours(old)) else []


OUR_PROMPTS = tuple(f"/opencode/{n.prompt_dir}/" for n in (NAMES, OLD_NAMES)) + ("prompts/coder.md",)


def ours_agent(a: object) -> bool:
    """An OpenCode agent entry CARL wrote: its prompt is a file in CARL's folder (either name)."""
    return isinstance(a, dict) and any(p in str(a.get("prompt", "")) for p in OUR_PROMPTS)


def gone_note(ref: object, renamed: dict[str, str]) -> str:
    """The note after a default model of the user's own whose provider CARL took out
    (renamed: old id -> the id ours has now), "" when the provider is still there."""
    prov, _, model = str(ref).partition("/")
    if prov in renamed:
        return f"; that provider is now '{renamed[prov]}': pick {renamed[prov]}/{model}"
    return "; that provider no longer exists: pick another" if prov in OLD_MTPLX_IDS else ""


def delegation_markers(names: Names) -> tuple[str, str]:
    """The lines around CARL's delegation rule in Pi's APPEND_SYSTEM.md."""
    return f"<!-- {names.block}:delegation begin -->", f"<!-- {names.block}:delegation end -->"


def strip_block(text: str, markers: tuple[str, str]) -> str:
    """text without the block between the markers (and the blank lines before it)."""
    begin, end = markers
    return re.sub(r"\n*" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", text, flags=re.S)


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
        self.written: set[str] = set()             # files written this run (backed up once)

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
            if backup and path not in self.written:     # one backup per file per run
                if not os.path.exists(path + ORIGINAL):
                    shutil.copy2(path, path + ORIGINAL)
                    self.report.add("backed up", f"{self.short(path)} -> {os.path.basename(path)}{ORIGINAL} (your original)")
                shutil.copy2(path, f"{path}.bak.{self.stamp}")
        link = os.path.islink(path)
        write_text(path, text)                     # through a symlink: the link stays, its target changes
        if not link:
            os.chmod(path, mode)
        self.written.add(path)

    def remove_file(self, path: str) -> None:
        """Remove a file CARL installed, keeping a copy (FILE.bak.<time>, which the clients don't load)."""
        shutil.copy2(path, f"{path}.bak.{self.stamp}")
        os.remove(path)

    # -- the state file (what is ours), and its name before the rename
    def load_state(self, folder: str) -> JsonObj:
        """NAMES.state; while it doesn't exist yet, the one from before the rename."""
        new = os.path.join(folder, NAMES.state)
        return load(new if os.path.exists(new) else os.path.join(folder, OLD_NAMES.state), {})

    def save_state(self, folder: str, client: str, st: JsonObj) -> None:
        """Write NAMES.state; the old-named one goes (a copy stays as FILE.bak.<time>)."""
        self.save(os.path.join(folder, NAMES.state), st, backup=False)
        old = os.path.join(folder, OLD_NAMES.state)
        if os.path.isfile(old):
            self.remove_file(old)
            self.report.add("updated", f"{client} state file {self.short(old)} -> {NAMES.state} (CARL's new name)")

    def renamed_provider(self, providers: JsonObj, st: JsonObj, is_ours: Callable[[JsonObj], bool],
                         new_id: str, client: str) -> dict[str, str]:
        """Take our provider under its old id out ({old id: the id ours has now}, or {})."""
        out: dict[str, str] = {}
        for old in old_alt_ids(providers, st, is_ours):
            if old != new_id:
                providers.pop(old)
                self.report.add("removed", f"{client} provider '{old}' (CARL's provider is now '{new_id}')")
                out[old] = new_id
        return out

    def bundle_path(self, rel: str) -> str:
        return os.path.join(self.o.bundle, rel)

    def render(self, text: str) -> str:
        for a, b in (("__HOST__", self.o.host), ("__LLAMA_PORT__", self.o.llama_port), ("__HOME__", self.o.home)):
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
        st = self.load_state(oc)
        first_install = not st
        bundle = self.bundle_json("opencode/opencode.json")

        providers = cfg.setdefault("provider", {})
        had_ours_before = any(isinstance(v, dict) and ours_oc(v) for v in providers.values())
        ids = pick_ids(providers, st, ours_oc)
        new_id = ids[PROVIDER]
        prov = bundle["provider"][PROVIDER]
        prov["models"] = carl_models.opencode_models(self.o.models, self.o.running, self.o.ctx)
        if new_id != PROVIDER:
            prov["name"] = prov["name"] + f" [{ALT}]"
            rep.add("kept", f"OpenCode provider '{PROVIDER}' (yours); ours installed as '{new_id}'")
        if providers.get(new_id) != prov:
            rep.add("updated" if new_id in providers else "added", f"OpenCode provider '{new_id}'")
        providers[new_id] = prov
        renamed = self.renamed_provider(providers, st, ours_oc, new_id, "OpenCode")
        for old in old_mtplx_ids(providers, st, ours_oc):
            providers.pop(old)
            rep.add("removed", f"OpenCode provider '{old}' (MTPLX support was removed in CARL 1.2.0)")

        self._oc_default_model(cfg, st, ids, first_install, had_ours_before, renamed)

        # title agent: we no longer disable it; undo our earlier setting
        agent = cfg.setdefault("agent", {})
        if (st.get("title_disabled") or (first_install and had_ours_before)) and agent.get("title") == {"disable": True}:
            agent.pop("title")
            rep.add("removed", "OpenCode agent.title.disable (titles are back on)")
        st.pop("title_disabled", None)

        self._oc_remove_old_plugin(cfg)
        self._oc_server_plugin(cfg, MODEL_CHECK, self.o.model_check, new_id, "model warnings")
        self._oc_server_plugin(cfg, OLD_CACHE, False, new_id, "")
        self._oc_server_plugin(cfg, CACHE, self.o.cache, new_id, "the prompt cache")
        old_specs = os.path.join(self.o.home, ".config", "carl", "prefix")    # what carl-prefix-cache recorded
        if os.path.isdir(old_specs):
            shutil.rmtree(old_specs, ignore_errors=True)

        self._oc_coder(cfg, st, agent)
        self._oc_tools(cfg, st)
        self._oc_browser(cfg, st, agent)
        for k in ("instructions", "agent", "plugin"):
            if not cfg.get(k):
                cfg.pop(k, None)
        cfg.setdefault("$schema", "https://opencode.ai/config.json")

        self._oc_tui()

        st.update({"providers": ids, "base_url": self.o.base_url, "updated": self.stamp})
        self.save(path, cfg)
        self.save_state(oc, "OpenCode", st)
        return ids

    def _oc_remove_old_plugin(self, cfg: JsonObj) -> None:
        """The server plugin earlier versions installed for MTPLX: out of the plugin list and
        off the disk (when the folder is ours)."""
        plug = os.path.join(self.o.oc_dir, OLD_OC_PLUGIN)
        plist = cfg.get("plugin")
        if isinstance(plist, list) and any(x in (plug, "file:" + plug) for x in plist):
            cfg["plugin"] = [x for x in plist if x not in (plug, "file:" + plug)]
            self.report.add("removed", "OpenCode plugin entry mtplx-session-headers (MTPLX support was removed)")
        if os.path.isdir(plug):
            if OLD_OC_PLUGIN_SIG in (read_or_none(os.path.join(plug, "index.js")) or ""):
                shutil.rmtree(plug)
                self.report.add("removed", f"{self.short(plug)} (MTPLX support was removed)")
            else:
                self.report.add("kept", f"{self.short(plug)} (not ours)")

    def _oc_tools(self, cfg: JsonObj, st: JsonObj) -> None:
        """OpenCode's tool switches: the env file and its profile block, and "lsp" with --lsp 1."""
        rep = self.report
        env = os.path.join(self.o.home, ".config/carl", ENV_FILE)
        lines = []
        if self.o.web_search != "off":
            flag = "OPENCODE_ENABLE_EXA" if self.o.web_search == "exa" else "OPENCODE_ENABLE_PARALLEL"
            lines += [f"export {flag}=1                 # web search: the queries go to {self.o.web_search} "
                      f"({SEARCH_MCP[self.o.web_search]})",
                      f"export OPENCODE_WEBSEARCH_PROVIDER={self.o.web_search}"]
        if self.o.background:
            lines.append("export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1   # the task tool can run a subagent "
                         "in the background")
        if self.o.lsp:
            lines.append("export OPENCODE_EXPERIMENTAL_LSP_TOOL=1             # the lsp tool (with \"lsp\": true)")
        if lines:
            text = ("# CARL: OpenCode's tool switches, written by client/install.sh (WEB_SEARCH=exa|parallel|off,\n"
                    "# NO_LSP=1, NO_BACKGROUND_SUBAGENTS=1 change them). Your shell profile sources this file.\n"
                    + "\n".join(lines) + "\n")
            if read_or_none(env) != text:
                rep.add("updated" if os.path.exists(env) else "added",
                        f"OpenCode tools ({self.short(env)}): web search {self.o.web_search}, background subagents "
                        f"{'on' if self.o.background else 'off'}, lsp {'on' if self.o.lsp else 'off'}")
            self.save_text(env, text, mode=0o644, backup=False)
        elif os.path.exists(env):
            os.remove(env)
            rep.add("removed", f"OpenCode tools ({self.short(env)}): all off")
        self._profiles(bool(lines))
        if self.o.lsp and "lsp" not in cfg:
            cfg["lsp"] = True
            st["lsp"] = True
            rep.add("added", 'OpenCode "lsp": true (OpenCode downloads and runs language servers)')
        elif not self.o.lsp and st.pop("lsp", None) and cfg.get("lsp") is True:
            cfg.pop("lsp")
            rep.add("removed", 'OpenCode "lsp": true (without NO_LSP=1 it comes back)')

    def browser_command(self) -> list[str]:
        """The browser MCP server: npx runs the pinned Playwright MCP with a temporary profile;
        on a Mac with Google Chrome it drives that Chrome (no browser download), elsewhere
        Playwright's own Chromium (npx playwright install chromium)."""
        cmd = ["npx", "-y", BROWSER_PKG, "--isolated"]
        if sys.platform == "darwin" and os.path.isdir(CHROME_APP):
            cmd += ["--browser", "chrome"]
        if not self.o.browser_headed:
            cmd.append("--headless")
        return cmd

    def _oc_browser(self, cfg: JsonObj, st: JsonObj, agent: JsonObj) -> None:
        """The browser MCP server (its tools off globally), and the subagent that has them."""
        rep = self.report
        mcp = cfg.setdefault("mcp", {})
        tools = cfg.setdefault("tools", {})
        pattern = f"{BROWSER_MCP}_*"
        old_name = st.get("browser_agent")
        if not self.o.browser:
            if BROWSER_MCP in mcp and mcp[BROWSER_MCP] == st.get("browser_mcp"):
                mcp.pop(BROWSER_MCP)
                tools.pop(pattern, None)
                rep.add("removed", "OpenCode browser (MCP server carl-browser)")
            if old_name and ours_agent(agent.get(old_name)):
                agent.pop(old_name)
                rep.add("removed", f"OpenCode agent '{old_name}'")
            for k in ("browser_mcp", "browser_agent"):
                st.pop(k, None)
            for k in ("mcp", "tools"):
                if not cfg.get(k):
                    cfg.pop(k, None)
            return
        want = {"type": "local", "command": self.browser_command(), "enabled": True}
        cur = mcp.get(BROWSER_MCP)
        if cur is not None and cur != st.get("browser_mcp"):
            rep.add("kept", f"OpenCode MCP server '{BROWSER_MCP}' (yours): CARL's browser is not installed")
            return
        if cur != want:
            rep.add("updated" if cur else "added", f"OpenCode browser ({BROWSER_PKG}, its tools for the browser agent only)")
        mcp[BROWSER_MCP] = want
        tools[pattern] = False
        st["browser_mcp"] = want
        name = BROWSER_AGENT if BROWSER_AGENT not in agent or ours_agent(agent[BROWSER_AGENT]) else BROWSER_AGENT_ALT
        if name in agent and not ours_agent(agent[name]):
            rep.add("kept", f"OpenCode agent '{name}' (yours): CARL's browser agent is not installed")
            return
        if old_name and old_name != name and ours_agent(agent.get(old_name)):
            agent.pop(old_name)
        body, desc = split_agent(read_text(self.bundle_path("agents/browser.md")))
        ddir = os.path.join(self.o.oc_dir, NAMES.prompt_dir)
        os.makedirs(ddir, exist_ok=True)
        prompt_path = os.path.join(ddir, "browser.md")
        write_text(prompt_path, body if name == BROWSER_AGENT else body.replace("You are **browser**", f"You are **{name}**"))
        new = {"description": desc, "mode": "subagent", "prompt": "{file:" + prompt_path + "}",
               "tools": {pattern: True, **{t: False for t in BROWSER_OFF}}, "permission": {"task": "deny"},
               "color": "info"}
        if agent.get(name) != new:
            rep.add("updated" if name in agent else "added", f"OpenCode agent '{name}' (the browser tools)")
        agent[name] = new
        st["browser_agent"] = name

    def _profiles(self, want: bool) -> None:
        """A pointer to the env file: a marked 3-line block appended to ~/.zshrc and ~/.bashrc when
        they exist (never created, nothing else in them changes, a backup first), and taken out
        again when nothing is switched on. OpenCode reads these switches only from the environment.
        With neither file, the report says which line to add."""
        block = (f"{PROFILE_BEGIN}\n[ -f \"$HOME/.config/carl/{ENV_FILE}\" ] && . \"$HOME/.config/carl/{ENV_FILE}\"\n"
                 f"{PROFILE_END}\n")
        found = False
        line = f'[ -f "$HOME/.config/carl/{ENV_FILE}" ] && . "$HOME/.config/carl/{ENV_FILE}"'
        if not self.o.profile:
            if want:
                self.report.add("kept", f"your shell profile (NO_PROFILE=1): add this line to it for OpenCode's tool "
                                        f"switches: {line}")
            return
        for name in (".zshrc", ".bashrc"):
            path = os.path.join(self.o.home, name)
            cur = read_or_none(path)
            if cur is None:
                continue
            found = True
            body = strip_block(cur or "", (PROFILE_BEGIN, PROFILE_END)).rstrip("\n")
            new = (body + "\n\n" + block if body else block) if want else (body + "\n" if body else "")
            if new != (cur or ""):
                self.save_text(path, new, mode=0o644)
                where = f" (in {self.short(os.path.realpath(path))}, its link target)" if os.path.islink(path) else ""
                self.report.add("updated" if want else "removed",
                                f"~/{name}{where}: {'sources' if want else 'no longer sources'} "
                                f"~/.config/carl/{ENV_FILE} (open a new terminal)")
        if want and not found:
            self.report.add("kept", f"no ~/.zshrc or ~/.bashrc: add this line to your shell profile for OpenCode's "
                                    f'tool switches: [ -f "$HOME/.config/carl/{ENV_FILE}" ] && . '
                                    f'"$HOME/.config/carl/{ENV_FILE}"')

    def _oc_server_plugin(self, cfg: JsonObj, name: str, want_it: bool, provider_id: str, what: str) -> None:
        """One of CARL's OpenCode server plugins: copied into plugins/, one entry in the plugin list
        with our provider id (an entry of ours is replaced, other entries stay); out when not wanted."""
        dest = os.path.join(self.o.oc_dir, "plugins", name)
        entry = "file:" + dest

        def is_ours(x: object) -> bool:
            return x == entry or (isinstance(x, list) and len(x) >= 1 and x[0] == entry)
        plist = cfg.get("plugin")
        plist = plist if isinstance(plist, list) else []
        had = [x for x in plist if is_ours(x)]
        rest = [x for x in plist if not is_ours(x)]
        if not want_it:
            if had or os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
                cfg["plugin"] = rest
                self.report.add("removed", f"OpenCode plugin {name}")
            return
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(self.bundle_path(os.path.join("opencode/plugins", name)), dest)
        if name == CACHE:
            shutil.copy(self.bundle_path(CACHE_CORE), dest)
        want = [entry, {"provider": provider_id}]
        if had != [want]:
            self.report.add("updated" if had else "added", f"OpenCode plugin {name} ({what})")
        cfg["plugin"] = rest + [want]

    def _oc_default_model(self, cfg: JsonObj, st: JsonObj, ids: dict[str, str],
                          first_install: bool, had_ours_before: bool, renamed: dict[str, str]) -> None:
        """model / small_model: only if unset or still what we set before. renamed: our
        provider ids taken out this run -> the id ours has now (named in the note)."""
        rep = self.report
        model = default_model(self.o.models)
        if model is None:
            rep.add("kept", "OpenCode model (no model is installed on the server yet)")
            return
        ours_default = f"{ids['llamacpp']}/{model}"
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
                rep.add("kept", f"OpenCode {key} = {cur} (yours{gone_note(cur, renamed)})")
                st.pop(key, None)

        if "model" not in st and cfg.get("small_model") in prev and cfg.get("small_model"):
            rep.add("removed", f"OpenCode small_model = {cfg.pop('small_model')} (follows your own default model)")
            st.pop("small_model", None)

    def _oc_coder(self, cfg: JsonObj, st: JsonObj, agent: JsonObj) -> None:
        """The coder agent and its delegation rule (on with --coder 1, removed with 0): "coder",
        or "carl-coder" next to a user's own "coder". Ours under another name goes."""
        oc, rep = self.o.oc_dir, self.report
        ddir = os.path.join(oc, NAMES.prompt_dir)
        self._oc_remove_old_prompt_dir(cfg)
        for p in (os.path.join(oc, "prompts", n) for n in ("coder.md", "delegation.md")):
            # earlier versions kept them in prompts/
            old = read_or_none(p)
            if old is not None and old.lstrip().startswith(("You are **coder**", "## Delegating to the coder")):
                os.remove(p)
                cfg["instructions"] = [x for x in cfg.get("instructions", []) if x != p]
        mine = {n for n in CODER_NAMES if n in agent and ours_agent(agent[n])}
        plan = coder_plan(mine, CODER in agent, CODER_ALT in agent, self.o.coder)
        for line in plan.kept:
            rep.add("kept", f"OpenCode agent {line}")
        for old_name in plan.remove:
            agent.pop(old_name)
            rep.add("removed", f"OpenCode agent '{old_name}' {plan.why}")
        rule_path = os.path.join(ddir, "delegation.md")
        name = plan.name
        if not plan.install:
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

    def _oc_remove_old_prompt_dir(self, cfg: JsonObj) -> None:
        """The prompt folder from before the rename: its delegation rule out of the
        instructions, our two files deleted (they are rewritten under NAMES.prompt_dir),
        the folder too once empty. Anything else in it stays."""
        old_dir = os.path.join(self.o.oc_dir, OLD_NAMES.prompt_dir)
        if os.path.islink(old_dir) or not os.path.isdir(old_dir):
            return
        old_rule = os.path.join(old_dir, "delegation.md")
        cfg["instructions"] = [x for x in cfg.get("instructions", []) if x != old_rule]
        for f in CODER_FILES:
            if os.path.isfile(os.path.join(old_dir, f)):
                os.remove(os.path.join(old_dir, f))
        if os.listdir(old_dir):
            self.report.add("kept", f"{self.short(old_dir)} (not ours: CARL's prompts are in {NAMES.prompt_dir}/ now)")
        else:
            os.rmdir(old_dir)
            self.report.add("removed", f"{self.short(old_dir)} (CARL's prompts are in {NAMES.prompt_dir}/ now)")

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
        st = self.load_state(pi)
        first_install = not st
        bundle = self.bundle_json("pi/models.json")

        providers = cfg.setdefault("providers", {})
        had_ours_before = any(isinstance(v, dict) and ours_pi(v) for v in providers.values())
        ids = pick_ids(providers, st, ours_pi)
        new_id = ids[PROVIDER]
        prov = bundle["providers"][PROVIDER]
        prov["models"] = carl_models.pi_models(self.o.models, self.o.running, self.o.ctx)
        if new_id != PROVIDER:
            rep.add("kept", f"Pi provider '{PROVIDER}' (yours); ours installed as '{new_id}'")
        if providers.get(new_id) != prov:
            rep.add("updated" if new_id in providers else "added", f"Pi provider '{new_id}'")
        providers[new_id] = prov
        renamed = self.renamed_provider(providers, st, ours_pi, new_id, "Pi")
        for old in old_mtplx_ids(providers, st, ours_pi):
            providers.pop(old)
            rep.add("removed", f"Pi provider '{old}' (MTPLX support was removed in CARL 1.2.0)")
        self.save(path, cfg)

        self._pi_settings(st, ids, first_install, had_ours_before, renamed)
        self._pi_tools(st)
        self._pi_extensions_and_coder(st)
        self._pi_cache(st)

        st.update({"providers": ids, "base_url": self.o.base_url, "updated": self.stamp})
        self.save_state(pi, "Pi", st)
        return ids

    def _pi_settings(self, st: JsonObj, ids: dict[str, str], first_install: bool, had_ours_before: bool,
                     renamed: dict[str, str]) -> None:
        """settings.json defaults: only when unset or still ours."""
        rep = self.report
        sp = os.path.join(self.o.pi_dir, "settings.json")
        sett = load(sp, {})
        model = default_model(self.o.models)
        if model is None:
            rep.add("kept", "Pi defaults (no model is installed on the server yet)")
            return
        want = {"defaultProvider": ids[PROVIDER], "defaultModel": model, "defaultThinkingLevel": "low"}
        prev = st.get("settings", {})
        legacy = first_install and had_ours_before and sett.get("defaultProvider") in (PROVIDER, OLD_NAMES.alt)
        if all(sett.get(k) is None or sett.get(k) == prev.get(k) or legacy for k in want):
            changed = {k: v for k, v in want.items() if sett.get(k) != v}
            sett.update(want)
            if changed:
                rep.add("updated", "Pi defaults " + ", ".join(f"{k}={v}" for k, v in changed.items()))
            st["settings"] = want
            self.save(sp, sett)
        else:
            mine = f"{sett.get('defaultProvider')}/{sett.get('defaultModel')}"
            rep.add("kept", f"Pi defaults (yours: {mine}{gone_note(mine, renamed)})")
            st.pop("settings", None)

    def _pi_tools(self, st: JsonObj) -> None:
        """Pi's tools: grep, find and ls on (defaultTools, unless yours), and web search as an MCP
        server (ours; your own server named carl-web-search stays)."""
        pi, rep = self.o.pi_dir, self.report
        sp = os.path.join(pi, "settings.json")
        sett = load(sp, {})
        cur = sett.get("defaultTools")
        if cur is None or cur == st.get("default_tools"):
            if cur != PI_TOOLS:
                sett["defaultTools"] = list(PI_TOOLS)
                self.save(sp, sett)
                rep.add("updated" if cur else "added", "Pi tools grep, find, ls (settings.json defaultTools)")
            st["default_tools"] = list(PI_TOOLS)
        else:
            rep.add("kept", "Pi defaultTools (yours)")
            st.pop("default_tools", None)
        mp = os.path.join(pi, "mcp.json")
        mcp = load(mp, {})
        servers = mcp.setdefault("mcpServers", {})
        mine = servers.get(SEARCH_NAME)
        ours = mine is None or mine == st.get("web_search")
        want = None if self.o.web_search == "off" else {
            "url": SEARCH_MCP[self.o.web_search], "exposure": "direct",
            "description": f"Web search ({self.o.web_search}): the search queries leave this computer"}
        if not ours:
            rep.add("kept", f"Pi MCP server {SEARCH_NAME} (yours)")
            st.pop("web_search", None)
        elif want and mine != want:
            servers[SEARCH_NAME] = want
            self.save(mp, mcp)
            rep.add("updated" if mine else "added", f"Pi web search ({self.o.web_search}, mcp.json {SEARCH_NAME})")
            st["web_search"] = want
        elif not want and mine is not None:
            servers.pop(SEARCH_NAME)
            self.save(mp, mcp)
            rep.add("removed", f"Pi web search (mcp.json {SEARCH_NAME})")
            st.pop("web_search", None)
        elif want:
            st["web_search"] = want
        self._pi_browser(mp, st)

    def _pi_browser(self, mp: str, st: JsonObj) -> None:
        """The browser MCP server for Pi, deferred: tool_search loads its tools when a task needs them."""
        rep = self.report
        mcp = load(mp, {})
        servers = mcp.setdefault("mcpServers", {})
        cur = servers.get(BROWSER_MCP)
        cmd = self.browser_command()
        want = {"command": cmd[0], "args": cmd[1:], "exposure": "deferred",
                "description": "A real Chrome (Playwright): open pages, click, type, fill forms, screenshots, "
                               "console and network"} if self.o.browser else None
        if cur is not None and cur != st.get("browser_mcp"):
            rep.add("kept", f"Pi MCP server {BROWSER_MCP} (yours)")
            st.pop("browser_mcp", None)
            return
        if want and cur != want:
            servers[BROWSER_MCP] = want
            self.save(mp, mcp)
            rep.add("updated" if cur else "added", f"Pi browser (mcp.json {BROWSER_MCP}, loaded on demand)")
        elif not want and cur is not None:
            servers.pop(BROWSER_MCP)
            self.save(mp, mcp)
            rep.add("removed", f"Pi browser (mcp.json {BROWSER_MCP})")
        if want:
            st["browser_mcp"] = want
        else:
            st.pop("browser_mcp", None)

    def _pi_extensions_and_coder(self, st: JsonObj) -> None:
        """The subagent extension, the coder agent and the delegation rule in
        APPEND_SYSTEM.md; the MTPLX request-policy extension of earlier versions goes."""
        pi, rep = self.o.pi_dir, self.report
        ext = os.path.join(pi, "extensions")
        os.makedirs(ext, exist_ok=True)
        pol = os.path.join(pi, OLD_PI_EXT)
        cur_pol = read_or_none(pol)
        if cur_pol is not None:
            if OLD_PI_EXT_SIG in cur_pol:
                self.remove_file(pol)
                rep.add("removed", f"Pi {OLD_PI_EXT} (MTPLX support was removed in CARL 1.2.0)")
            else:
                rep.add("kept", f"Pi {OLD_PI_EXT} (yours)")

        sub = os.path.join(ext, "subagent")
        sub_index = read_or_none(os.path.join(sub, "index.ts"))
        sub_ours = ((os.path.isdir(sub) and any(n.ext_marker in sub_index for n in (NAMES, OLD_NAMES)))
                    if sub_index is not None else not os.path.exists(sub))
        agents_dir = os.path.join(pi, "agents")
        texts = {n: read_or_none(os.path.join(agents_dir, f"{n}.md")) for n in CODER_NAMES}
        mine = {n for n, t in texts.items() if t is not None and CODER_SIG in t}
        plan = coder_plan(mine, texts[CODER] is not None, texts[CODER_ALT] is not None, self.o.coder)
        for line in plan.kept:
            rep.add("kept", f"Pi agent {line}")
        for old_name in plan.remove:
            self.remove_file(os.path.join(agents_dir, f"{old_name}.md"))
            rep.add("removed", f"Pi agent '{old_name}' {plan.why}")
        name = plan.name
        cpath = os.path.join(agents_dir, f"{name}.md")
        existing = texts[name]
        asp = os.path.join(pi, "APPEND_SYSTEM.md")
        cur = read_or_none(asp) or ""
        for names in (OLD_NAMES, NAMES):
            cur = strip_block(cur, delegation_markers(names))
        cur = cur.strip()
        begin, end = delegation_markers(NAMES)
        if plan.install:
            if sub_ours:
                shutil.rmtree(sub, ignore_errors=True)
                shutil.copytree(self.bundle_path("pi/extensions/subagent"), sub)
                st["subagent_ext"] = True
            else:
                rep.add("kept", "Pi extensions/subagent (yours; it provides the subagent tool ours would)")
            os.makedirs(agents_dir, exist_ok=True)
            new_text = name_agent(self.coder_text(), name)
            if existing != new_text:
                rep.add("updated" if existing is not None else "added", f"Pi agent '{name}' + delegation rule")
            write_text(cpath, new_text)
            rule = name_agent(self.delegation_text().strip(), name)
            cur = (cur + "\n\n" if cur else "") + f"{begin}\n{rule}\n{end}"
            st["coder_agent"] = name
        else:
            if sub_ours and os.path.isdir(sub):
                shutil.rmtree(sub)
            st.pop("coder_agent", None)
            st.pop("subagent_ext", None)
        if cur or os.path.exists(asp):
            self.save_text(asp, cur + "\n" if cur else "", mode=0o644)


    def _pi_cache(self, st: JsonObj) -> None:
        """The prompt cache extension (extensions/carl-cache, with the shared carl-cache.js); a folder
        of that name that isn't ours stays."""
        dest = os.path.join(self.o.pi_dir, "extensions", CACHE)
        index = read_or_none(os.path.join(dest, "index.ts"))
        ours = index is None and not os.path.exists(dest) or index is not None and NAMES.ext_marker in index
        if not ours:
            self.report.add("kept", f"Pi extensions/{CACHE} (yours)")
            return
        if not self.o.cache:
            if os.path.isdir(dest):
                shutil.rmtree(dest)
                self.report.add("removed", f"Pi extension {CACHE}")
            st.pop("cache_ext", None)
            return
        new = read_or_none(self.bundle_path(f"pi/extensions/{CACHE}/index.ts"))
        changed = index != new or read_or_none(os.path.join(dest, "carl-cache.js")) != read_or_none(self.bundle_path(CACHE_CORE))
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(self.bundle_path(f"pi/extensions/{CACHE}"), dest)
        shutil.copy(self.bundle_path(CACHE_CORE), dest)
        if changed:
            self.report.add("updated" if index is not None else "added", f"Pi extension {CACHE} (the prompt cache)")
        st["cache_ext"] = True


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

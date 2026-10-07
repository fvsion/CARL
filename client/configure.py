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
  defaultThinkingLevel): the model the server runs now (else the one a start loads); set only
  when unset or still the value we set before.
- Agents, prompts, extensions: never overwrite a user's file or agent with the
  same name. CARL's coder subagent is "coder", or "carl-coder" when the user has
  an agent "coder" of their own (before 1.2.0 that fallback was "llm-deploy-coder":
  ours under the other names is taken out); a user's own "carl-coder" too: ours
  is skipped with a note.
- Lists (plugin, instructions): ours are appended / removed, others kept.
- Tools (Phase 8, measured: OpenCode's system prompt + tools stay under 10.5K tokens):
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
- The disk cache (client/shared/carl-cache.js): the OpenCode plugin carl-cache (the same kind
  of entry; it replaces carl-prefix-cache) and the Pi extension carl-cache save each session
  and each agent's prompt on the server's disk and restore them before a request.
  NO_CACHE=1 leaves both out.
- OpenCode plugin carl-model-check (opencode.json "plugin", with our provider id as its
  option): warns when the model picked isn't the one the server runs, isn't installed, or
  is being loaded (router mode). NO_MODEL_CHECK=1 leaves it out.
- Clients (--clients both|opencode|pi, CLIENTS= in install.sh): the configs of the clients chosen; the
  other client's files are not read or changed.
- Every changed file is backed up first (<file>.bak.<timestamp>). A config that is not plain
  JSON stops the run before anything is written.

Layout: the merge rules are plain functions on the parsed configs (no I/O: "merge rules" below);
the Installer applies them and does every file operation through the Files port, whose adapter
LocalFiles is wired in main().
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

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
CLIENTS = ("both", "opencode", "pi")
SEARCH_MCP = {"exa": "https://mcp.exa.ai/mcp", "parallel": "https://search.parallel.ai/mcp"}
SEARCH_NAME = "carl-web-search"                         # Pi's MCP server entry
PI_TOOLS = ["+grep", "+find", "+ls"]                    # Pi's built-in tools that are off by default
ENV_FILE = "opencode.env"                               # in ~/.config/carl: OpenCode's tool switches
PROFILE_BEGIN, PROFILE_END = "# >>> CARL: OpenCode tool switches >>>", "# <<< CARL <<<"
PROFILE_LINE = f'[ -f "$HOME/.config/carl/{ENV_FILE}" ] && . "$HOME/.config/carl/{ENV_FILE}"'
PROFILES = (".zshrc", ".bashrc")                        # appended to when they exist; never created
BROWSER_MCP = "carl-browser"                            # the MCP server (OpenCode tools: carl-browser_*)
BROWSER_TOOLS = f"{BROWSER_MCP}_*"
BROWSER_PKG = "@playwright/mcp@0.0.83"                  # pinned: measured 2026-10-03 (26 tools, ~4.8K tokens)
BROWSER_AGENT, BROWSER_AGENT_ALT = "browser", "carl-browser"
BROWSER_OFF = ("bash", "edit", "write", "lsp", "task", "todowrite", "question", "skill")   # not for the browser agent
CHROME_APP = "/Applications/Google Chrome.app"
MODEL_CHECK = "carl-model-check"
BACKGROUND = "carl-background"                          # the coder in the background (OpenCode plugin)
DELEGATION = "carl-delegation"                          # the hand-off: rule for main agents, reminder, gate (both)
# Around CARL's delegation rule in OpenCode's instructions file: carl-delegation takes it out of subagents' prompts
RULE_BEGIN, RULE_END = "<!-- carl:main-agents-only begin -->", "<!-- carl:main-agents-only end -->"
CACHE = "carl-cache"                                    # the disk cache: OpenCode plugin and Pi extension
OLD_CACHE = "carl-prefix-cache"                         # its OpenCode plugin before 11.5 (removed)
CACHE_CORE = "shared/carl-cache.js"                     # the code both carry
PANEL = "carl-panel"                                    # the /carl panel: OpenCode TUI plugin and Pi extension
PANEL_CORE = "shared/carl-panel.js"
DELEGATION_CORE = "shared/carl-delegation.js"           # the hand-off rules both clients' carl-delegation carry
TUI_CORE = "shared/carl-tui.js"                         # the helpers the sidebar and the switcher carry
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
    reminder: bool = True     # the per-turn reminder about the coder (carl-delegation)
    gate: int = 0             # the new-file gate: stop the main agent at its Nth new file in a turn (0: off)
    profile: bool = True      # append the pointer to ~/.zshrc / ~/.bashrc (NO_PROFILE=1: print it instead)
    clients: str = "both"     # both | opencode | pi: the configs to write (the other client's stay as they are)

    @property
    def oc_dir(self) -> str:
        return os.path.join(self.home, ".config/opencode")

    @property
    def pi_dir(self) -> str:
        return os.path.join(self.home, ".pi/agent")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.llama_port}/v1"

    @property
    def cache_api(self) -> str:
        """The dashboard's cache API (tools/monitor/cacheapi.py): next to the server, port + 1."""
        return f"http://{self.host}:{int(self.llama_port) + 1}"


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
        raise argparse.ArgumentTypeError(f"context too small: {n} tokens")
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
    ap.add_argument("--clients", choices=CLIENTS, default="both")
    ap.add_argument("--reminder", type=switch_arg, default=True)
    ap.add_argument("--gate", type=int, default=0, choices=range(0, 100), metavar="0-99")
    a = ap.parse_args(argv)
    try:
        models = carl_models.load_list(a.models)
    except (OSError, ValueError) as e:
        ap.error(f"--models: {e}")
    return Options(bundle=a.bundle, home=a.home, host=a.host, llama_port=a.llama_port, ctx=a.ctx, models=models,
                   running=a.running, coder=a.coder, sidebar=a.sidebar, switcher=a.switcher,
                   model_check=a.model_check, web_search=a.web_search, lsp=a.lsp, background=a.background,
                   browser=a.browser, browser_headed=a.browser_headed, profile=a.profile,
                   cache=a.cache, clients=a.clients, reminder=a.reminder, gate=a.gate)


# ================================================================== merge rules (no I/O)
class Report:
    """What changed, grouped by kind, printed at the end."""

    def __init__(self) -> None:
        self.entries: dict[str, list[str]] = {k: [] for k in REPORT_KINDS}

    def add(self, kind: str, line: str) -> None:
        self.entries[kind].append(line)

    def lines(self, width: int = 0) -> list[str]:
        """Each entry as "  kind      text", wrapped to `width` (default: the terminal's, else 80); a line
        after a newline in an entry (a command to copy) is indented, not wrapped."""
        width = width or shutil.get_terminal_size((80, 24)).columns
        out: list[str] = []
        for k in REPORT_KINDS:
            for entry in self.entries[k]:
                first, *literal = entry.split("\n")
                out += textwrap.wrap(first, max(width, 50), initial_indent=f"  {k:9s} ", subsequent_indent=" " * 12,
                                     break_long_words=False, break_on_hyphens=False) or [f"  {k:9s}"]
                out += [" " * 12 + line for line in literal]
        return out


def name_agent(text: str, name: str) -> str:
    """Point the coder / delegation texts at the installed agent name."""
    if name == CODER:
        return text
    text = re.sub(r"^(name|agent): coder$", rf"\1: {name}", text, flags=re.M)
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


CLIENT_BLOCK = re.compile(r"<!-- carl:(\w+) (\w+) -->\n(.*?)(?=<!-- carl:)", re.S)
CLIENT_END = re.compile(r"<!-- carl:\w+ end -->\n\n?")


def oc_rule_text(rule: str) -> str:
    """OpenCode's delegation rule file: the rule between RULE_BEGIN and RULE_END. OpenCode gives its instructions to
    every agent; carl-delegation takes the marked rule out of subagents' prompts, so only a main agent has it."""
    return f"{RULE_BEGIN}\n{rule.strip()}\n{RULE_END}\n"


def delegation_for(text: str, client: str, background: bool, browser: bool = True) -> str:
    """The delegation rule for one client: the paragraphs marked for it whose switch is on ("background"
    when background subagents are on, "browser" with the browser, "nobrowser" without it; "any" = every
    client), the others and the markers out."""
    on = {"background": background, "browser": browser, "nobrowser": not browser}

    def keep(m: re.Match[str]) -> str:
        return m.group(3).rstrip("\n") + "\n\n" if on.get(m.group(1), False) and m.group(2) in (client, "any") else ""
    out = CLIENT_END.sub("\n", CLIENT_BLOCK.sub(keep, text))
    return re.sub(r"\n{3,}", "\n\n", out)


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


def report_coder_plan(plan: CoderPlan, client: str, rep: Report) -> None:
    """The report lines of a coder plan: the user's own agents kept, ours taken out."""
    for line in plan.kept:
        rep.add("kept", f"{client} agent {line}")
    for old_name in plan.remove:
        rep.add("removed", f"{client} agent '{old_name}' {plan.why}")


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


def default_model(ml: ModelList, running: str | None = None) -> str | None:
    """The model to make the clients' default: the one the server runs now (single model: install.sh
    passes no running model in router mode), else the one a server start loads, else the first. The
    dashboard saves the model it applies, so a restart runs it again; only a one-off ./carl.sh --model
    differs until the next plain start."""
    if running and running in ml.ids:
        return running
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


def merge_provider(providers: JsonObj, st: JsonObj, prov: JsonObj, new_id: str,
                   is_ours: Callable[[JsonObj], bool], client: str, rep: Report) -> dict[str, str]:
    """Our provider in, under new_id (pick_ids); ours under its pre-rename id and our MTPLX provider
    out. Returns the renamed ids: {old id: the id ours has now}."""
    if new_id != PROVIDER:
        rep.add("kept", f"{client} provider '{PROVIDER}' (yours); ours installed as '{new_id}'")
    if providers.get(new_id) != prov:
        rep.add("updated" if new_id in providers else "added", f"{client} provider '{new_id}'")
    providers[new_id] = prov
    renamed: dict[str, str] = {}
    for old in old_alt_ids(providers, st, is_ours):
        if old != new_id:
            providers.pop(old)
            rep.add("removed", f"{client} provider '{old}' (CARL's provider is now '{new_id}')")
            renamed[old] = new_id
    for old in old_mtplx_ids(providers, st, is_ours):
        providers.pop(old)
        rep.add("removed", f"{client} provider '{old}' (MTPLX support was removed in CARL 1.2.0)")
    return renamed


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


def drop_from(cfg: JsonObj, key: str, values: tuple[object, ...]) -> None:
    """cfg[key] without the values (the key is set, also when it was missing)."""
    cfg[key] = [x for x in cfg.get(key, []) if x not in values]


def prune_empty(cfg: JsonObj, keys: tuple[str, ...]) -> None:
    """Take out the keys whose value is empty (or missing)."""
    for k in keys:
        if not cfg.get(k):
            cfg.pop(k, None)


# ---------------------------------------------------- OpenCode
def merge_oc_default_model(cfg: JsonObj, st: JsonObj, model: str | None, provider_id: str, legacy: bool,
                           renamed: dict[str, str], rep: Report) -> None:
    """model / small_model: only if unset or still what we set before. legacy: an install from
    before the state file (the defaults earlier versions set count as ours). renamed: our
    provider ids taken out this run -> the id ours has now (named in the note)."""
    if model is None:
        rep.add("kept", "OpenCode model (no model is installed on the server yet)")
        return
    ours_default = f"{provider_id}/{model}"
    prev = {st.get("model"), st.get("small_model")} | (OLD_DEFAULTS if legacy else set())
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


def undo_title_disable(agent: JsonObj, st: JsonObj, legacy: bool, rep: Report) -> None:
    """The title agent: earlier versions disabled it; undo our setting."""
    if (st.get("title_disabled") or legacy) and agent.get("title") == {"disable": True}:
        agent.pop("title")
        rep.add("removed", "OpenCode agent.title.disable (titles are back on)")
    st.pop("title_disabled", None)


def merge_plugin_entry(cfg: JsonObj, entry: str, want: list[Any] | None, on_disk: bool,
                       label: str, rep: Report) -> bool:
    """One of CARL's server plugins in opencode.json "plugin": `want` ([entry, options]) replaces
    an entry of ours, other entries stay; want None takes ours out. True when the plugin's folder
    is to be removed."""
    def is_ours(x: object) -> bool:
        return x == entry or (isinstance(x, list) and len(x) >= 1 and x[0] == entry)
    plist = cfg.get("plugin")
    plist = plist if isinstance(plist, list) else []
    had = [x for x in plist if is_ours(x)]
    rest = [x for x in plist if not is_ours(x)]
    if want is None:
        if had or on_disk:
            cfg["plugin"] = rest
            rep.add("removed", f"OpenCode plugin {label}")
            return True
        return False
    if had != [want]:
        rep.add("updated" if had else "added", f"OpenCode plugin {label}")
    cfg["plugin"] = rest + [want]
    return False


def env_file_text(web_search: str, background: bool, lsp: bool) -> str | None:
    """~/.config/carl/opencode.env: OpenCode's tool switches (None: all off)."""
    lines = []
    if web_search != "off":
        flag = "OPENCODE_ENABLE_EXA" if web_search == "exa" else "OPENCODE_ENABLE_PARALLEL"
        lines += [f"export {flag}=1                 # web search: the queries go to {web_search} "
                  f"({SEARCH_MCP[web_search]})",
                  f"export OPENCODE_WEBSEARCH_PROVIDER={web_search}"]
    if background:
        lines.append("export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1   # the task tool can run a subagent "
                     "in the background")
    if lsp:
        lines.append("export OPENCODE_EXPERIMENTAL_LSP_TOOL=1             # the lsp tool (with \"lsp\": true)")
    if not lines:
        return None
    return ("# CARL: OpenCode's tool switches, written by client/install.sh (WEB_SEARCH=exa|parallel|off,\n"
            "# NO_LSP=1, NO_BACKGROUND_SUBAGENTS=1 change them). Your shell profile sources this file.\n"
            + "\n".join(lines) + "\n")


def profile_text(cur: str, want: bool) -> str:
    """A shell profile with CARL's marked 3-line block at its end (want), or without it.
    Nothing else in it changes."""
    block = f"{PROFILE_BEGIN}\n{PROFILE_LINE}\n{PROFILE_END}\n"
    body = strip_block(cur, (PROFILE_BEGIN, PROFILE_END)).rstrip("\n")
    if want:
        return body + "\n\n" + block if body else block
    return body + "\n" if body else ""


def merge_lsp(cfg: JsonObj, st: JsonObj, want: bool, rep: Report) -> None:
    """OpenCode "lsp": true while the LSP tool is on; only ours is taken out again."""
    if want and "lsp" not in cfg:
        cfg["lsp"] = True
        st["lsp"] = True
        rep.add("added", 'OpenCode "lsp": true (OpenCode downloads and runs language servers)')
    elif not want and st.pop("lsp", None) and cfg.get("lsp") is True:
        cfg.pop("lsp")
        rep.add("removed", 'OpenCode "lsp": true (without NO_LSP=1 it comes back)')


def browser_command(headed: bool, chrome: bool) -> list[str]:
    """The browser MCP server: npx runs the pinned Playwright MCP with a temporary profile;
    on a Mac with Google Chrome it drives that Chrome (no browser download), elsewhere
    Playwright's own Chromium (npx playwright install chromium)."""
    cmd = ["npx", "-y", BROWSER_PKG, "--isolated"]
    if chrome:
        cmd += ["--browser", "chrome"]
    if not headed:
        cmd.append("--headless")
    return cmd


@dataclass(frozen=True)
class AgentText:
    """An agent's description (from the bundle's agents/*.md) and where its prompt is installed."""
    desc: str
    prompt_path: str


def merge_oc_browser(cfg: JsonObj, st: JsonObj, agent: JsonObj, server: JsonObj | None,
                     text: AgentText | None, rep: Report) -> str | None:
    """The browser MCP server (its tools off globally) and the subagent that has them; server None:
    ours out. Returns the agent name whose prompt to write (None: no agent of ours)."""
    mcp = cfg.setdefault("mcp", {})
    tools = cfg.setdefault("tools", {})
    old_name = st.get("browser_agent")
    if server is None or text is None:
        if BROWSER_MCP in mcp and mcp[BROWSER_MCP] == st.get("browser_mcp"):
            mcp.pop(BROWSER_MCP)
            tools.pop(BROWSER_TOOLS, None)
            rep.add("removed", "OpenCode browser (MCP server carl-browser)")
        if old_name and ours_agent(agent.get(old_name)):
            agent.pop(old_name)
            rep.add("removed", f"OpenCode agent '{old_name}'")
        for k in ("browser_mcp", "browser_agent"):
            st.pop(k, None)
        prune_empty(cfg, ("mcp", "tools"))
        return None
    cur = mcp.get(BROWSER_MCP)
    if cur is not None and cur != st.get("browser_mcp"):
        rep.add("kept", f"OpenCode MCP server '{BROWSER_MCP}' (yours): CARL's browser is not installed")
        return None
    if cur != server:
        rep.add("updated" if cur else "added", f"OpenCode browser ({BROWSER_PKG}, its tools for the browser agent only)")
    mcp[BROWSER_MCP] = server
    tools[BROWSER_TOOLS] = False
    st["browser_mcp"] = server
    name = BROWSER_AGENT if BROWSER_AGENT not in agent or ours_agent(agent[BROWSER_AGENT]) else BROWSER_AGENT_ALT
    if name in agent and not ours_agent(agent[name]):
        rep.add("kept", f"OpenCode agent '{name}' (yours): CARL's browser agent is not installed")
        return None
    if old_name and old_name != name and ours_agent(agent.get(old_name)):
        agent.pop(old_name)
    new = {"description": text.desc, "mode": "subagent", "prompt": "{file:" + text.prompt_path + "}",
           "tools": {BROWSER_TOOLS: True, **{t: False for t in BROWSER_OFF}}, "permission": {"task": "deny"},
           "color": "info"}
    if agent.get(name) != new:
        rep.add("updated" if name in agent else "added", f"OpenCode agent '{name}' (the browser tools)")
    agent[name] = new
    st["browser_agent"] = name
    return name


def merge_oc_coder(cfg: JsonObj, st: JsonObj, agent: JsonObj, name: str, text: AgentText | None,
                   rule_path: str, rep: Report) -> None:
    """The coder agent and its delegation rule in the instructions (text None: the rule out)."""
    if text is None:
        drop_from(cfg, "instructions", (rule_path,))
        st.pop("coder_agent", None)
        return
    new_agent = {
        "description": text.desc,
        "mode": "subagent",
        "prompt": "{file:" + text.prompt_path + "}",
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


def merge_tui_plugin(tplug: list[Any], entry: str, want: bool, label: str, rep: Report) -> bool:
    """One TUI plugin in tui.json "plugin". True when its folder is to be removed."""
    if want:
        if entry not in tplug:
            tplug.append(entry)
            rep.add("added", f"{label} (tui.json)")
        return False
    if entry in tplug:
        tplug.remove(entry)
        rep.add("removed", label)
        return True
    return False


# ---------------------------------------------------- Pi
def merge_pi_defaults(sett: JsonObj, st: JsonObj, provider_id: str, model: str | None, legacy: bool,
                      renamed: dict[str, str], rep: Report) -> bool:
    """settings.json defaultProvider / defaultModel / defaultThinkingLevel: only when unset or still
    ours. legacy: an install from before the state file. True when settings.json is to be written."""
    if model is None:
        rep.add("kept", "Pi defaults (no model is installed on the server yet)")
        return False
    want = {"defaultProvider": provider_id, "defaultModel": model, "defaultThinkingLevel": "low"}
    prev = st.get("settings", {})
    ours_before = legacy and sett.get("defaultProvider") in (PROVIDER, OLD_NAMES.alt)
    if all(sett.get(k) is None or sett.get(k) == prev.get(k) or ours_before for k in want):
        changed = {k: v for k, v in want.items() if sett.get(k) != v}
        sett.update(want)
        if changed:
            rep.add("updated", "Pi defaults " + ", ".join(f"{k}={v}" for k, v in changed.items()))
        st["settings"] = want
        return True
    mine = f"{sett.get('defaultProvider')}/{sett.get('defaultModel')}"
    rep.add("kept", f"Pi defaults (yours: {mine}{gone_note(mine, renamed)})")
    st.pop("settings", None)
    return False


def merge_pi_default_tools(sett: JsonObj, st: JsonObj, rep: Report) -> bool:
    """Pi's grep, find and ls on (settings.json defaultTools), unless the list is the user's own.
    True when settings.json changed."""
    cur = sett.get("defaultTools")
    if cur is None or cur == st.get("default_tools"):
        st["default_tools"] = list(PI_TOOLS)
        if cur != PI_TOOLS:
            sett["defaultTools"] = list(PI_TOOLS)
            rep.add("updated" if cur else "added", "Pi tools grep, find, ls (settings.json defaultTools)")
            return True
        return False
    rep.add("kept", "Pi defaultTools (yours)")
    st.pop("default_tools", None)
    return False


def merge_pi_web_search(servers: JsonObj, st: JsonObj, web_search: str, rep: Report) -> bool:
    """Web search as an MCP server (ours; a server of the user's own with that name stays).
    True when mcp.json changed."""
    mine = servers.get(SEARCH_NAME)
    want = None if web_search == "off" else {
        "url": SEARCH_MCP[web_search], "exposure": "direct",
        "description": f"Web search ({web_search}): the search queries leave this computer"}
    if mine is not None and mine != st.get("web_search"):
        rep.add("kept", f"Pi MCP server {SEARCH_NAME} (yours)")
        st.pop("web_search", None)
        return False
    if want and mine != want:
        servers[SEARCH_NAME] = want
        rep.add("updated" if mine else "added", f"Pi web search ({web_search}, mcp.json {SEARCH_NAME})")
        st["web_search"] = want
        return True
    if not want and mine is not None:
        servers.pop(SEARCH_NAME)
        rep.add("removed", f"Pi web search (mcp.json {SEARCH_NAME})")
        st.pop("web_search", None)
        return True
    if want:
        st["web_search"] = want
    return False


def merge_pi_browser(servers: JsonObj, st: JsonObj, command: list[str] | None, rep: Report) -> bool:
    """The browser MCP server for Pi, deferred: tool_search loads its tools when a task needs them
    (command None: ours out). True when mcp.json changed."""
    cur = servers.get(BROWSER_MCP)
    want = {"command": command[0], "args": command[1:], "exposure": "deferred",
            "description": "A real Chrome (Playwright): open pages, click, type, fill forms, screenshots, "
                           "console and network"} if command else None
    if cur is not None and cur != st.get("browser_mcp"):
        rep.add("kept", f"Pi MCP server {BROWSER_MCP} (yours)")
        st.pop("browser_mcp", None)
        return False
    changed = False
    if want and cur != want:
        servers[BROWSER_MCP] = want
        rep.add("updated" if cur else "added", f"Pi browser (mcp.json {BROWSER_MCP}, loaded on demand)")
        changed = True
    elif not want and cur is not None:
        servers.pop(BROWSER_MCP)
        rep.add("removed", f"Pi browser (mcp.json {BROWSER_MCP})")
        changed = True
    if want:
        st["browser_mcp"] = want
    else:
        st.pop("browser_mcp", None)
    return changed


def append_system_text(cur: str, rule: str | None) -> str:
    """Pi's APPEND_SYSTEM.md with CARL's delegation rule as the last block (rule None: without it).
    The rule's earlier versions (either name) go; the user's text stays."""
    for names in (OLD_NAMES, NAMES):
        cur = strip_block(cur, delegation_markers(names))
    cur = cur.strip()
    if rule is not None:
        begin, end = delegation_markers(NAMES)
        cur = (cur + "\n\n" if cur else "") + f"{begin}\n{rule}\n{end}"
    return cur + "\n" if cur else ""


# ================================================================== the filesystem (port and adapter)
class Files(Protocol):
    """The filesystem as the installer uses it."""

    def read(self, path: str) -> str | None:
        """The text of a file; None when it doesn't exist."""

    def exists(self, path: str) -> bool: ...

    def isfile(self, path: str) -> bool: ...

    def isdir(self, path: str) -> bool: ...

    def islink(self, path: str) -> bool: ...

    def realpath(self, path: str) -> str: ...

    def listdir(self, path: str) -> list[str]: ...

    def makedirs(self, path: str) -> None:
        """The folder and its parents (no error when it exists)."""

    def write(self, path: str, text: str) -> None:
        """Through a symlink: the link stays, its target changes."""

    def chmod(self, path: str, mode: int) -> None: ...

    def copy(self, src: str, dest: str) -> None:
        """A copy of a file with its mode and times (a backup)."""

    def copy_into(self, src: str, folder: str) -> None:
        """A copy of a file into a folder, with its mode."""

    def copytree(self, src: str, dest: str) -> None:
        """A copy of a folder (dest must not exist)."""

    def remove(self, path: str) -> None: ...

    def rmdir(self, path: str) -> None: ...

    def rmtree(self, path: str, quiet: bool = True) -> None:
        """Remove a folder; quiet: errors (also a missing folder) are ignored."""


class LocalFiles:
    """Files on this computer's disk."""

    def read(self, path: str) -> str | None:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def isfile(self, path: str) -> bool:
        return os.path.isfile(path)

    def isdir(self, path: str) -> bool:
        return os.path.isdir(path)

    def islink(self, path: str) -> bool:
        return os.path.islink(path)

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def listdir(self, path: str) -> list[str]:
        return os.listdir(path)

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def write(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def chmod(self, path: str, mode: int) -> None:
        os.chmod(path, mode)

    def copy(self, src: str, dest: str) -> None:
        shutil.copy2(src, dest)

    def copy_into(self, src: str, folder: str) -> None:
        shutil.copy(src, folder)

    def copytree(self, src: str, dest: str) -> None:
        shutil.copytree(src, dest)

    def remove(self, path: str) -> None:
        os.remove(path)

    def rmdir(self, path: str) -> None:
        os.rmdir(path)

    def rmtree(self, path: str, quiet: bool = True) -> None:
        shutil.rmtree(path, ignore_errors=quiet)


class ConfigError(Exception):
    """A user's config that CARL can't merge into (not plain JSON, not an object)."""


class ConfigFiles:
    """Reads and writes the clients' configs: JSON checked on load, a backup before every change."""

    def __init__(self, fs: Files, home: str, stamp: str, report: Report) -> None:
        self.fs = fs
        self.home = home
        self.stamp = stamp
        self.report = report
        self.written: set[str] = set()             # files written this run (backed up once)

    def short(self, path: str) -> str:
        """The path for display (the home folder as ~)."""
        return path.replace(self.home, "~")

    def load(self, path: str) -> JsonObj:
        """A JSON object ({} when the file doesn't exist); ConfigError when it isn't one."""
        text = self.fs.read(path)
        if text is None:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ConfigError(f"{path} is not plain JSON (it can have comments). CARL changed nothing. "
                              "Correct the file or move it, then run the setup again.") from None
        if not isinstance(data, dict):
            raise ConfigError(f"{path} is not a JSON object. CARL changed nothing. Correct the file or move it, "
                              "then run the setup again.")
        return data

    def save(self, path: str, data: JsonObj, mode: int = 0o600, backup: bool = True) -> None:
        self.save_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode, backup)

    def save_text(self, path: str, text: str, mode: int = 0o600, backup: bool = True) -> None:
        """Write only on a change. Before a change to a file that exists: keep the
        original once (FILE.before-carl, never overwritten) and a copy of the
        current version (FILE.bak.<time>)."""
        fs = self.fs
        fs.makedirs(os.path.dirname(path))
        if fs.exists(path):
            if fs.read(path) == text:
                return
            if backup and path not in self.written:     # one backup per file per run
                if not fs.exists(path + ORIGINAL):
                    fs.copy(path, path + ORIGINAL)
                    self.report.add("backed up",
                                    f"{self.short(path)} -> {os.path.basename(path)}{ORIGINAL} (your original)")
                fs.copy(path, f"{path}.bak.{self.stamp}")
        link = fs.islink(path)
        fs.write(path, text)                       # through a symlink: the link stays, its target changes
        if not link:
            fs.chmod(path, mode)
        self.written.add(path)

    def remove_file(self, path: str) -> None:
        """Remove a file CARL installed, keeping a copy (FILE.bak.<time>, which the clients don't load)."""
        self.fs.copy(path, f"{path}.bak.{self.stamp}")
        self.fs.remove(path)


# ================================================================== the installer
class Installer:
    """Writes the OpenCode and Pi configs for one set of Options."""

    def __init__(self, opts: Options, stamp: str, fs: Files, chrome: bool) -> None:
        self.o = opts
        self.fs = fs
        self.chrome = chrome                       # Google Chrome is installed (the browser agent drives it)
        self.stamp = stamp
        self.report = Report()
        self.cf = ConfigFiles(fs, opts.home, stamp, self.report)

    def short(self, path: str) -> str:
        return self.cf.short(path)

    # -- the state file (what is ours), and its name before the rename
    def state_path(self, folder: str) -> str:
        """NAMES.state; while it doesn't exist yet, the one from before the rename."""
        new = os.path.join(folder, NAMES.state)
        return new if self.fs.exists(new) else os.path.join(folder, OLD_NAMES.state)

    def check_configs(self) -> None:
        """Load every config this run reads, so one that isn't plain JSON stops it before a write."""
        oc, pi = self.o.oc_dir, self.o.pi_dir
        paths = []
        if self.o.clients != "pi":
            paths += [os.path.join(oc, "opencode.json"), self.state_path(oc), os.path.join(oc, "tui.json")]
        if self.o.clients != "opencode":
            paths += [os.path.join(pi, "models.json"), self.state_path(pi), os.path.join(pi, "settings.json"),
                      os.path.join(pi, "mcp.json")]
        for path in paths:
            self.cf.load(path)

    def save_state(self, folder: str, client: str, st: JsonObj) -> None:
        """Write NAMES.state; the old-named one goes (a copy stays as FILE.bak.<time>)."""
        self.cf.save(os.path.join(folder, NAMES.state), st, backup=False)
        old = os.path.join(folder, OLD_NAMES.state)
        if self.fs.isfile(old):
            self.cf.remove_file(old)
            self.report.add("updated", f"{client} state file {self.short(old)} -> {NAMES.state} (CARL's new name)")

    # -- the bundle (client/)
    def bundle_path(self, rel: str) -> str:
        return os.path.join(self.o.bundle, rel)

    def bundle_text(self, rel: str) -> str:
        text = self.fs.read(self.bundle_path(rel))
        if text is None:
            raise FileNotFoundError(f"{self.bundle_path(rel)}: not in the client folder (copy it again)")
        return text

    def render(self, text: str) -> str:
        for a, b in (("__HOST__", self.o.host), ("__LLAMA_PORT__", self.o.llama_port), ("__HOME__", self.o.home)):
            text = text.replace(a, b)
        return text

    def bundle_json(self, rel: str) -> JsonObj:
        data = json.loads(self.render(self.bundle_text(rel)))
        if not isinstance(data, dict):
            raise ValueError(f"{rel}: not a JSON object")
        return data

    def install_folder(self, src: str, dest: str, shared: tuple[str, ...] = ()) -> None:
        """A plugin or extension folder from the bundle, with the shared files it imports (replaced whole)."""
        self.fs.rmtree(dest)
        self.fs.copytree(self.bundle_path(src), dest)
        for f in shared:
            self.fs.copy_into(self.bundle_path(f), dest)

    def browser_command(self) -> list[str]:
        return browser_command(self.o.browser_headed, self.chrome)

    # ========================================================== OpenCode
    def opencode(self) -> dict[str, str]:
        oc, rep, o = self.o.oc_dir, self.report, self.o
        path = os.path.join(oc, "opencode.json")
        cfg = self.cf.load(path)
        st = self.cf.load(self.state_path(oc))
        first_install = not st
        bundle = self.bundle_json("opencode/opencode.json")

        providers = cfg.setdefault("provider", {})
        had_ours_before = any(isinstance(v, dict) and ours_oc(v) for v in providers.values())
        legacy = first_install and had_ours_before
        ids = pick_ids(providers, st, ours_oc)
        new_id = ids[PROVIDER]
        prov = bundle["provider"][PROVIDER]
        prov["models"] = carl_models.opencode_models(o.models, o.running, o.ctx)
        if new_id != PROVIDER:
            prov["name"] = prov["name"] + f" [{ALT}]"
        renamed = merge_provider(providers, st, prov, new_id, ours_oc, "OpenCode", rep)
        merge_oc_default_model(cfg, st, default_model(o.models, o.running), new_id, legacy, renamed, rep)
        agent = cfg.setdefault("agent", {})
        undo_title_disable(agent, st, legacy, rep)

        self._oc_remove_old_plugin(cfg)
        self._oc_server_plugin(cfg, MODEL_CHECK, o.model_check, new_id, "model warnings")
        self._oc_server_plugin(cfg, OLD_CACHE, False, new_id, "")
        self._oc_server_plugin(cfg, CACHE, o.cache, new_id, "the disk cache")
        self._oc_server_plugin(cfg, BACKGROUND, o.background and o.coder, new_id, "the coder in the background")
        old_specs = os.path.join(o.home, ".config", "carl", "prefix")    # what carl-prefix-cache recorded
        if self.fs.isdir(old_specs):
            self.fs.rmtree(old_specs)

        self._oc_coder(cfg, st, agent, new_id)
        self._oc_tools(cfg, st)
        self._oc_browser(cfg, st, agent)
        prune_empty(cfg, ("instructions", "agent", "plugin"))
        cfg.setdefault("$schema", "https://opencode.ai/config.json")

        self._oc_tui()

        st.update({"providers": ids, "base_url": o.base_url, "updated": self.stamp})
        self.cf.save(path, cfg)
        self.save_state(oc, "OpenCode", st)
        return ids

    def _oc_remove_old_plugin(self, cfg: JsonObj) -> None:
        """The server plugin earlier versions installed for MTPLX: out of the plugin list and
        off the disk (when the folder is ours)."""
        plug = os.path.join(self.o.oc_dir, OLD_OC_PLUGIN)
        plist = cfg.get("plugin")
        if isinstance(plist, list) and any(x in (plug, "file:" + plug) for x in plist):
            drop_from(cfg, "plugin", (plug, "file:" + plug))
            self.report.add("removed", "OpenCode plugin entry mtplx-session-headers (MTPLX support was removed)")
        if self.fs.isdir(plug):
            if OLD_OC_PLUGIN_SIG in (self.fs.read(os.path.join(plug, "index.js")) or ""):
                self.fs.rmtree(plug, quiet=False)
                self.report.add("removed", f"{self.short(plug)} (MTPLX support was removed)")
            else:
                self.report.add("kept", f"{self.short(plug)} (not ours)")

    def _oc_server_plugin(self, cfg: JsonObj, name: str, want_it: bool, provider_id: str, what: str,
                          extra: JsonObj | None = None) -> None:
        """One of CARL's OpenCode server plugins: copied into plugins/, one entry in the plugin list
        with our provider id and its other options (an entry of ours is replaced, other entries stay); out when
        not wanted."""
        dest = os.path.join(self.o.oc_dir, "plugins", name)
        entry = "file:" + dest
        if not want_it:
            if merge_plugin_entry(cfg, entry, None, self.fs.isdir(dest), name, self.report):
                self.fs.rmtree(dest)
            return
        shared = {CACHE: (CACHE_CORE, PANEL_CORE), DELEGATION: (DELEGATION_CORE,)}.get(name, ())
        self.install_folder(os.path.join("opencode/plugins", name), dest, shared)
        want = [entry, {"provider": provider_id, **({"cacheApi": self.o.cache_api} if name == CACHE else {}),
                        **(extra or {})}]
        merge_plugin_entry(cfg, entry, want, True, f"{name} ({what})", self.report)

    def _oc_coder(self, cfg: JsonObj, st: JsonObj, agent: JsonObj, provider_id: str) -> None:
        """The coder agent, its delegation rule and carl-delegation (on with --coder 1, removed with 0): "coder",
        or "carl-coder" next to a user's own "coder". Ours under another name goes."""
        oc, rep = self.o.oc_dir, self.report
        ddir = os.path.join(oc, NAMES.prompt_dir)
        self._oc_remove_old_prompt_dir(cfg)
        for p in (os.path.join(oc, "prompts", n) for n in CODER_FILES):
            # earlier versions kept them in prompts/
            old = self.fs.read(p)
            if old is not None and old.lstrip().startswith(("You are **coder**", "## Delegating to the coder")):
                self.fs.remove(p)
                drop_from(cfg, "instructions", (p,))
        mine = {n for n in CODER_NAMES if n in agent and ours_agent(agent[n])}
        plan = coder_plan(mine, CODER in agent, CODER_ALT in agent, self.o.coder)
        report_coder_plan(plan, "OpenCode", rep)
        for old_name in plan.remove:
            agent.pop(old_name)
        rule_path = os.path.join(ddir, "delegation.md")
        name = plan.name
        if not plan.install:
            merge_oc_coder(cfg, st, agent, name, None, rule_path, rep)
            self.fs.rmtree(ddir)
            self._oc_server_plugin(cfg, DELEGATION, False, provider_id, "")
            self._code_command(os.path.join(oc, "command", "code.md"), "", name, "OpenCode")
            return
        body, desc = split_agent(self.bundle_text("agents/coder.md"))
        prompt_path = os.path.join(ddir, "coder.md")
        self.fs.makedirs(ddir)
        rule = delegation_for(self.bundle_text("agents/delegation.md"), "opencode", self.o.background, self.o.browser)
        for p, t, what in ((prompt_path, name_agent(body, name), "coder prompt"),
                           (rule_path, oc_rule_text(name_agent(rule, name)), "delegation rule")):
            old = self.fs.read(p)
            if old is not None and old != t:
                rep.add("updated", f"OpenCode {what} ({self.short(p)})")
            self.fs.write(p, t)
        merge_oc_coder(cfg, st, agent, name, AgentText(desc, prompt_path), rule_path, rep)
        self._oc_server_plugin(cfg, DELEGATION, True, provider_id, "the hand-off to the coder",
                               {"reminder": self.o.reminder, "gate": self.o.gate, "coder": name})
        self._code_command(os.path.join(oc, "command", "code.md"), "opencode/commands/code.md", name, "OpenCode")

    def _code_command(self, dest: str, src: str, name: str, client: str) -> None:
        """/code: the user's own switch to give a task straight to the coder (Phase 23). src "": take ours out. A
        code.md of the user's (no CARL mark in its description) stays."""
        cur = self.fs.read(dest)
        if cur is not None and '"CARL: ' not in cur:
            self.report.add("kept", f"{client} /code ({self.short(dest)} is yours)")
            return
        if not src:
            if cur is not None:
                self.fs.remove(dest)
                self.report.add("removed", f"{client} /code")
            return
        text = name_agent(self.bundle_text(src), name)
        if cur != text:
            self.fs.makedirs(os.path.dirname(dest))
            self.fs.write(dest, text)
            self.report.add("updated" if cur is not None else "added", f"{client} /code (the task straight to the coder)")

    def _oc_remove_old_prompt_dir(self, cfg: JsonObj) -> None:
        """The prompt folder from before the rename: its delegation rule out of the
        instructions, our two files deleted (they are rewritten under NAMES.prompt_dir),
        the folder too once empty. Anything else in it stays."""
        fs = self.fs
        old_dir = os.path.join(self.o.oc_dir, OLD_NAMES.prompt_dir)
        if fs.islink(old_dir) or not fs.isdir(old_dir):
            return
        drop_from(cfg, "instructions", (os.path.join(old_dir, "delegation.md"),))
        for f in CODER_FILES:
            if fs.isfile(os.path.join(old_dir, f)):
                fs.remove(os.path.join(old_dir, f))
        if fs.listdir(old_dir):
            self.report.add("kept", f"{self.short(old_dir)} (not ours: CARL's prompts are in {NAMES.prompt_dir}/ now)")
        else:
            fs.rmdir(old_dir)
            self.report.add("removed", f"{self.short(old_dir)} (CARL's prompts are in {NAMES.prompt_dir}/ now)")

    def _oc_tools(self, cfg: JsonObj, st: JsonObj) -> None:
        """OpenCode's tool switches: the env file and its profile block, and "lsp" with --lsp 1."""
        o = self.o
        env = os.path.join(o.home, ".config/carl", ENV_FILE)
        text = env_file_text(o.web_search, o.background, o.lsp)
        if text is not None:
            if self.fs.read(env) != text:
                self.report.add("updated" if self.fs.exists(env) else "added",
                                f"OpenCode tools ({self.short(env)}): web search {o.web_search}, background "
                                f"subagents {'on' if o.background else 'off'}, lsp {'on' if o.lsp else 'off'}")
            self.cf.save_text(env, text, mode=0o644, backup=False)
        elif self.fs.exists(env):
            self.fs.remove(env)
            self.report.add("removed", f"OpenCode tools ({self.short(env)}): all off")
        self._profiles(text is not None)
        merge_lsp(cfg, st, o.lsp, self.report)

    def _profiles(self, want: bool) -> None:
        """A pointer to the env file: a marked 3-line block appended to ~/.zshrc and ~/.bashrc when
        they exist (never created, nothing else in them changes, a backup first), and taken out
        again when nothing is switched on. OpenCode reads these switches only from the environment.
        With neither file, the report says which line to add."""
        if not self.o.profile:
            if want:
                self.report.add("kept", f"your shell profile (NO_PROFILE=1). For the tool switches of OpenCode, "
                                        f"add this line to it:\n{PROFILE_LINE}")
            return
        found = False
        for name in PROFILES:
            path = os.path.join(self.o.home, name)
            cur = self.fs.read(path)
            if cur is None:
                continue
            found = True
            new = profile_text(cur, want)
            if new != cur:
                self.cf.save_text(path, new, mode=0o644)
                where = (f" (in {self.short(self.fs.realpath(path))}, its link target)" if self.fs.islink(path)
                         else "")
                self.report.add("updated" if want else "removed",
                                f"~/{name}{where}: {'sources' if want else 'no longer sources'} "
                                f"~/.config/carl/{ENV_FILE} (open a new terminal)")
        if want and not found:
            self.report.add("kept", f"no ~/.zshrc or ~/.bashrc. For the tool switches of OpenCode, add this line "
                                    f"to your shell profile:\n{PROFILE_LINE}")

    def _oc_browser(self, cfg: JsonObj, st: JsonObj, agent: JsonObj) -> None:
        """The browser MCP server (its tools off globally), and the subagent that has them."""
        server: JsonObj | None = None
        text: AgentText | None = None
        body = ""
        ddir = os.path.join(self.o.oc_dir, NAMES.prompt_dir)
        prompt_path = os.path.join(ddir, "browser.md")
        if self.o.browser:
            server = {"type": "local", "command": self.browser_command(), "enabled": True}
            body, desc = split_agent(self.bundle_text("agents/browser.md"))
            text = AgentText(desc, prompt_path)
        name = merge_oc_browser(cfg, st, agent, server, text, self.report)
        if name is not None:
            self.fs.makedirs(ddir)
            self.fs.write(prompt_path,
                          body if name == BROWSER_AGENT else body.replace("You are **browser**", f"You are **{name}**"))

    def _oc_tui(self) -> None:
        """TUI plugins: subagents sidebar, session switcher (in the prompt box), the /carl panel;
        each with the shared file it imports."""
        oc = self.o.oc_dir
        tui_path = os.path.join(oc, "tui.json")
        tui = self.cf.load(tui_path)
        tplug = tui.setdefault("plugin", [])
        for name, label, want, shared in (
                ("subagents-sidebar", "OpenCode subagents sidebar", self.o.sidebar, TUI_CORE),
                ("session-switcher", "OpenCode session switcher", self.o.switcher, TUI_CORE),
                (PANEL, "OpenCode /carl panel", True, PANEL_CORE)):
            dest = os.path.join(oc, "plugins", name)
            if want:
                self.install_folder(os.path.join("opencode/plugins", name), dest, (shared,))
            if merge_tui_plugin(tplug, "file:" + dest, want, label, self.report):
                self.fs.rmtree(dest)
        if not tplug:
            tui.pop("plugin")
        if tui or self.fs.exists(tui_path):
            self.cf.save(tui_path, tui)

    # ========================================================== Pi
    def pi(self) -> dict[str, str]:
        pi, rep, o = self.o.pi_dir, self.report, self.o
        path = os.path.join(pi, "models.json")
        cfg = self.cf.load(path)
        st = self.cf.load(self.state_path(pi))
        first_install = not st
        bundle = self.bundle_json("pi/models.json")

        providers = cfg.setdefault("providers", {})
        had_ours_before = any(isinstance(v, dict) and ours_pi(v) for v in providers.values())
        ids = pick_ids(providers, st, ours_pi)
        new_id = ids[PROVIDER]
        prov = bundle["providers"][PROVIDER]
        prov["models"] = carl_models.pi_models(o.models, o.running, o.ctx)
        renamed = merge_provider(providers, st, prov, new_id, ours_pi, "Pi", rep)
        self.cf.save(path, cfg)

        sp = os.path.join(pi, "settings.json")
        sett = self.cf.load(sp)
        write = merge_pi_defaults(sett, st, new_id, default_model(o.models, o.running), first_install and had_ours_before,
                                  renamed, rep)
        if merge_pi_default_tools(sett, st, rep) or write:
            self.cf.save(sp, sett)
        mp = os.path.join(pi, "mcp.json")
        mcp = self.cf.load(mp)
        servers = mcp.setdefault("mcpServers", {})
        write = merge_pi_web_search(servers, st, o.web_search, rep)
        if merge_pi_browser(servers, st, self.browser_command() if o.browser else None, rep) or write:
            self.cf.save(mp, mcp)

        self._pi_extensions_and_coder(st)
        self._pi_ext(CACHE, o.cache, (CACHE_CORE, PANEL_CORE), "the disk cache", st, "cache_ext")
        self._pi_ext(PANEL, True, (PANEL_CORE,), "the /carl panel", st, "panel_ext")
        coder_on = "coder_agent" in st
        self._code_command(os.path.join(o.pi_dir, "prompts", "code.md"), "pi/prompts/code.md" if coder_on else "",
                           str(st.get("coder_agent") or CODER), "Pi")
        self._pi_ext(DELEGATION, coder_on, (DELEGATION_CORE,), "the hand-off to the coder", st, "delegation_ext")
        if coder_on:
            st["delegation"] = {"reminder": o.reminder, "gate": o.gate}
        else:
            st.pop("delegation", None)
        if o.cache:
            st["cache_api"] = o.cache_api
        else:
            st.pop("cache_api", None)

        st.update({"providers": ids, "base_url": o.base_url, "updated": self.stamp})
        self.save_state(pi, "Pi", st)
        return ids

    def _pi_extensions_and_coder(self, st: JsonObj) -> None:
        """The subagent extension, the coder agent and the delegation rule in
        APPEND_SYSTEM.md; the MTPLX request-policy extension of earlier versions goes."""
        fs, pi, rep = self.fs, self.o.pi_dir, self.report
        ext = os.path.join(pi, "extensions")
        fs.makedirs(ext)
        pol = os.path.join(pi, OLD_PI_EXT)
        cur_pol = fs.read(pol)
        if cur_pol is not None:
            if OLD_PI_EXT_SIG in cur_pol:
                self.cf.remove_file(pol)
                rep.add("removed", f"Pi {OLD_PI_EXT} (MTPLX support was removed in CARL 1.2.0)")
            else:
                rep.add("kept", f"Pi {OLD_PI_EXT} (yours)")

        sub = os.path.join(ext, "subagent")
        sub_index = fs.read(os.path.join(sub, "index.ts"))
        sub_ours = ((fs.isdir(sub) and any(n.ext_marker in sub_index for n in (NAMES, OLD_NAMES)))
                    if sub_index is not None else not fs.exists(sub))
        agents_dir = os.path.join(pi, "agents")
        texts = {n: fs.read(os.path.join(agents_dir, f"{n}.md")) for n in CODER_NAMES}
        mine = {n for n, t in texts.items() if t is not None and CODER_SIG in t}
        plan = coder_plan(mine, texts[CODER] is not None, texts[CODER_ALT] is not None, self.o.coder)
        report_coder_plan(plan, "Pi", rep)
        for old_name in plan.remove:
            self.cf.remove_file(os.path.join(agents_dir, f"{old_name}.md"))
        name = plan.name
        rule: str | None = None
        if plan.install:
            if sub_ours:
                self.install_folder("pi/extensions/subagent", sub)
                st["subagent_ext"] = True
            else:
                rep.add("kept", "Pi extensions/subagent (yours; it provides the subagent tool ours would)")
            fs.makedirs(agents_dir)
            existing = texts[name]
            new_text = name_agent(self.bundle_text("agents/coder.md"), name)
            if existing != new_text:
                rep.add("updated" if existing is not None else "added", f"Pi agent '{name}' + delegation rule")
            fs.write(os.path.join(agents_dir, f"{name}.md"), new_text)
            rule = name_agent(delegation_for(self.bundle_text("agents/delegation.md"), "pi", self.o.background,
                                             self.o.browser).strip(), name)
            st["coder_agent"] = name
            st["background_subagents"] = self.o.background     # the subagent extension reads it
        else:
            if sub_ours and fs.isdir(sub):
                fs.rmtree(sub, quiet=False)
            for k in ("coder_agent", "subagent_ext", "background_subagents"):
                st.pop(k, None)
        asp = os.path.join(pi, "APPEND_SYSTEM.md")
        text = append_system_text(fs.read(asp) or "", rule)
        if text or fs.exists(asp):
            self.cf.save_text(asp, text, mode=0o644)

    def _pi_ext(self, name: str, want: bool, shared: tuple[str, ...], what: str, st: JsonObj, key: str) -> None:
        """One of CARL's Pi extensions (extensions/NAME, with the shared files it imports); a folder of that
        name that isn't ours stays."""
        fs = self.fs
        dest = os.path.join(self.o.pi_dir, "extensions", name)
        index = fs.read(os.path.join(dest, "index.ts"))
        ours = index is None and not fs.exists(dest) or index is not None and NAMES.ext_marker in index
        if not ours:
            self.report.add("kept", f"Pi extensions/{name} (yours)")
            return
        if not want:
            if fs.isdir(dest):
                fs.rmtree(dest, quiet=False)
                self.report.add("removed", f"Pi extension {name}")
            st.pop(key, None)
            return
        files = {"index.ts": f"pi/extensions/{name}/index.ts", **{os.path.basename(f): f for f in shared}}
        changed = any(fs.read(os.path.join(dest, n)) != fs.read(self.bundle_path(src)) for n, src in files.items())
        self.install_folder(f"pi/extensions/{name}", dest, shared)
        if changed:
            self.report.add("updated" if index is not None else "added", f"Pi extension {name} ({what})")
        st[key] = True


def main(argv: list[str]) -> int:
    opts = parse_args(argv)
    chrome = sys.platform == "darwin" and os.path.isdir(CHROME_APP)
    inst = Installer(opts, time.strftime("%Y%m%d-%H%M%S"), LocalFiles(), chrome)
    try:
        inst.check_configs()
        oc_ids = inst.opencode() if opts.clients != "pi" else None
        pi_ids = inst.pi() if opts.clients != "opencode" else None
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for line in inst.report.lines():
        print(line)
    if oc_ids is not None:
        print(f"OpenCode: {inst.short(opts.oc_dir)}/opencode.json, CARL provider {', '.join(oc_ids.values())}")
    if pi_ids is not None:
        print(f"Pi:       {inst.short(opts.pi_dir)}/models.json, CARL provider {', '.join(pi_ids.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

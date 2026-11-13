"""OpenCode's merge rules (no I/O): CARL's pieces in opencode.json and tui.json next to the user's own, and the texts
of OpenCode's tool switches (~/.config/carl/opencode.env and the block in the shell profile that sources it).

- Default model (model / small_model): the model the server runs now (else the one a start loads); set only when
  unset or still the value we set before.
- Plugins: CARL's server plugins in carl-plugins/ (not plugins/, which OpenCode 2 loads by itself), each one entry
  in "plugin" with our provider id and its options; the TUI plugins in tui.json "plugin". Ours are replaced or
  taken out, the others stay.
- Tools (Phase 8, measured: OpenCode's system prompt + tools stay under 10.5K tokens): web search (Exa, or
  Parallel; the queries leave this computer) and background subagents are switched on by environment variables in
  ~/.config/carl/opencode.env, which one marked block in the shell profile (~/.zshrc, ~/.bashrc) sources; the LSP
  tool with "lsp": true.
- Browser (Playwright MCP, pinned, an isolated Chrome profile): its tools are off for the main agents and on for a
  "browser" subagent ("carl-browser" next to a user's own "browser"), so the main prompt stays under 10.5K tokens
  (measured: the 26 browser tools are ~4.8K).
- The coder agent and its delegation rule (the instructions file): carl-delegation sets the coder's thinking per
  request; an external coder model (Phase 23.4.5) gets "model" and no temperature.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from .merge import JsonObj, Report, drop_from, gone_note, prune_empty, strip_block

# CARL's OpenCode plugin folders (Phase 23.5.1.11): not in plugins/, which OpenCode 2 loads by itself (every folder
# there, beside the "plugin" lists: a second, failing copy); opencode.json and tui.json list them by path. A CARL
# folder from before, in plugins/, is moved here (the installer's _oc_old_plugin).
OC_PLUGIN_DIR = "carl-plugins"
SEARCH_MCP = {"exa": "https://mcp.exa.ai/mcp", "parallel": "https://search.parallel.ai/mcp"}
ENV_FILE = "opencode.env"                               # in ~/.config/carl: OpenCode's tool switches
PROFILE_BEGIN, PROFILE_END = "# >>> CARL: OpenCode tool switches >>>", "# <<< CARL <<<"
PROFILE_LINE = f'[ -f "$HOME/.config/carl/{ENV_FILE}" ] && . "$HOME/.config/carl/{ENV_FILE}"'
PROFILES = (".zshrc", ".bashrc")                        # appended to when they exist; never created
BROWSER_MCP = "carl-browser"                            # the MCP server (OpenCode tools: carl-browser_*)
BROWSER_TOOLS = f"{BROWSER_MCP}_*"
BROWSER_PKG = "@playwright/mcp@0.0.83"                  # pinned: measured 2026-10-03 (26 tools, ~4.8K tokens)
BROWSER_AGENT, BROWSER_AGENT_ALT = "browser", "carl-browser"
BROWSER_OFF = ("bash", "edit", "write", "lsp", "task", "todowrite", "question", "skill")   # not for the browser agent
MODEL_CHECK = "carl-model-check"
BACKGROUND = "carl-background"                          # the coder in the background (OpenCode plugin)
DELEGATION = "carl-delegation"                          # the hand-off: rule for main agents, reminder, gate (both)
# Around CARL's delegation rule in OpenCode's instructions file: carl-delegation takes it out of subagents' prompts
RULE_BEGIN, RULE_END = "<!-- carl:main-agents-only begin -->", "<!-- carl:main-agents-only end -->"
CACHE = "carl-cache"                                    # the disk cache: OpenCode plugin and Pi extension
OLD_CACHE = "carl-prefix-cache"                         # its OpenCode plugin before 11.5 (removed)
PANEL = "carl-panel"                                    # the /carl panel: OpenCode TUI plugin and Pi extension
SIDEBAR, SWITCHER = "subagents-sidebar", "session-switcher"   # OpenCode's TUI plugins


def oc_rule_text(rule: str) -> str:
    """OpenCode's delegation rule file: the rule between RULE_BEGIN and RULE_END. OpenCode gives its instructions to
    every agent; carl-delegation takes the marked rule out of subagents' prompts, so only a main agent has it."""
    return f"{RULE_BEGIN}\n{rule.strip()}\n{RULE_END}\n"


def ours_agent(a: object, prompt_dirs: List[str]) -> bool:
    """An OpenCode agent entry CARL wrote: its prompt is a file in CARL's folder (any of its names: prompt_dirs) or
    the prompts/coder.md of earlier versions. Either separator (a Windows path has "\\")."""
    ours = tuple(f"/opencode/{d}/" for d in prompt_dirs) + ("prompts/coder.md",)
    return isinstance(a, dict) and any(p in str(a.get("prompt", "")).replace("\\", "/") for p in ours)


def merge_oc_default_model(cfg: JsonObj, st: JsonObj, model: Optional[str], provider_id: str, legacy: bool,
                           renamed: Dict[str, str], rep: Report, old_defaults: FrozenSet[str] = frozenset()) -> None:
    """model / small_model: only if unset or still what we set before. legacy: an install from
    before the state file (the defaults earlier versions set, old_defaults, count as ours). renamed: our
    provider ids taken out this run -> the id ours has now (named in the note)."""
    if model is None:
        rep.add("kept", "OpenCode model (no model is installed on the server yet)")
        return
    ours_default = f"{provider_id}/{model}"
    prev = {st.get("model"), st.get("small_model")} | (set(old_defaults) if legacy else set())
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


def same_path(a: object, b: str, real: Optional[Callable[[str], str]] = None) -> bool:
    """a and b name the same file or folder ("file:" prefix allowed): equal, or equal with the symlinks resolved
    (real: the resolver; an entry written before 1.11.0 through a link is still CARL's)."""
    if not isinstance(a, str):
        return False
    if a == b:
        return True
    if real is None:
        return False
    pa, pb = a[5:] if a.startswith("file:") else a, b[5:] if b.startswith("file:") else b
    return a.startswith("file:") == b.startswith("file:") and real(pa) == real(pb)


def plural_files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def merge_plugin_entry(cfg: JsonObj, entry: str, want: Optional[List[Any]], on_disk: bool,
                       label: str, rep: Report, real: Optional[Callable[[str], str]] = None) -> bool:
    """One of CARL's server plugins in opencode.json "plugin": `want` ([entry, options]) replaces
    an entry of ours, other entries stay; want None takes ours out. True when the plugin's folder
    is to be removed."""
    def is_ours(x: object) -> bool:
        return same_path(x, entry, real) or (isinstance(x, list) and len(x) >= 1 and same_path(x[0], entry, real))
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


def env_file_text(web_search: str, background: bool, lsp: bool) -> Optional[str]:
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
    return ("# CARL: OpenCode's tool switches, written by CARL's setup (WEB_SEARCH=exa|parallel|off,\n"
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


def browser_command(headed: bool, chrome: bool, windows: bool = False) -> List[str]:
    """The browser MCP server: npx runs the pinned Playwright MCP with a temporary profile;
    on a computer with Google Chrome it drives that Chrome (no browser download), elsewhere
    Playwright's own Chromium (npx playwright install chromium). On Windows npx is npx.cmd: the
    clients start the command without a shell, and only the file's full name finds it there."""
    cmd = ["npx.cmd" if windows else "npx", "-y", BROWSER_PKG, "--isolated"]
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


def merge_oc_browser(cfg: JsonObj, st: JsonObj, agent: JsonObj, server: Optional[JsonObj],
                     text: Optional[AgentText], rep: Report, prompt_dirs: List[str]) -> Optional[str]:
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
        if old_name and ours_agent(agent.get(old_name), prompt_dirs):
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
    name = (BROWSER_AGENT if BROWSER_AGENT not in agent or ours_agent(agent[BROWSER_AGENT], prompt_dirs)
            else BROWSER_AGENT_ALT)
    if name in agent and not ours_agent(agent[name], prompt_dirs):
        rep.add("kept", f"OpenCode agent '{name}' (yours): CARL's browser agent is not installed")
        return None
    if old_name and old_name != name and ours_agent(agent.get(old_name), prompt_dirs):
        agent.pop(old_name)
    new = {"description": text.desc, "mode": "subagent", "prompt": "{file:" + text.prompt_path + "}",
           "tools": {BROWSER_TOOLS: True, **{t: False for t in BROWSER_OFF}}, "permission": {"task": "deny"},
           "color": "info"}
    if agent.get(name) != new:
        rep.add("updated" if name in agent else "added", f"OpenCode agent '{name}' (the browser tools)")
    agent[name] = new
    st["browser_agent"] = name
    return name


def merge_oc_coder(cfg: JsonObj, st: JsonObj, agent: JsonObj, name: str, text: Optional[AgentText],
                   rule_path: str, rep: Report, model: str = "", real: Optional[Callable[[str], str]] = None) -> None:
    """The coder agent and its delegation rule in the instructions (text None: the rule out). The agent has no
    reasoningEffort: carl-delegation sets the coder's thinking per request, for the model it runs on. model: an
    external model of OpenCode for the coder (Phase 23.4.5): the agent's "model", and no temperature (CARL's sampling
    is for CARL's models)."""
    if text is None:
        drop_from(cfg, "instructions", (rule_path,))
        st.pop("coder_agent", None)
        return
    new_agent = {
        "description": text.desc,
        "mode": "subagent",
        "prompt": "{file:" + text.prompt_path + "}",
        **({"model": model} if model else {
            # temperature 0.6 (Qwen's coding value; with thinking at medium the best of 9 coder
            # runs on the 35B, 2026-10-02: all functions typed, more tests, 26% faster than 1.0);
            # the 27B uses the same value without its own test. The thinking: no value here
            # (Phase 23.4.4): carl-delegation's chat.params hook sets it per model (coderThinking).
            "temperature": 0.6}),
        "permission": {"task": "deny"},
        "steps": 80,
        "color": "secondary",
    }
    if agent.get(name) != new_agent:
        rep.add("updated" if name in agent else "added", f"OpenCode agent '{name}' + delegation rule")
    agent[name] = new_agent
    ins = cfg.setdefault("instructions", [])
    ins[:] = [x for x in ins if x == rule_path or not same_path(x, rule_path, real)]   # one written through a link goes
    if rule_path not in ins:
        ins.append(rule_path)
    st["coder_agent"] = name


def merge_tui_plugin(tplug: List[Any], entry: str, want: bool, label: str, rep: Report,
                     real: Optional[Callable[[str], str]] = None) -> bool:
    """One TUI plugin in tui.json "plugin". True when its folder is to be removed."""
    old = [x for x in tplug if same_path(x, entry, real) and x != entry]     # the same plugin through a link
    for x in old:
        tplug.remove(x)
    if want:
        if entry not in tplug:
            tplug.append(entry)
            if not old:
                rep.add("added", f"{label} (tui.json)")
        return False
    if entry in tplug or old:
        if entry in tplug:
            tplug.remove(entry)
        rep.add("removed", label)
        return True
    return False

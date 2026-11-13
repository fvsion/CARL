"""Pi's merge rules (no I/O): CARL's pieces in models.json, settings.json, mcp.json and APPEND_SYSTEM.md next to the
user's own.

- Default model (settings.json defaultProvider / defaultModel / defaultThinkingLevel): the model the server runs now
  (else the one a start loads); set only when unset or still the value we set before.
- Thinking per role (Phase 23.4.4): the main session's is a modelThinkingLevels entry when it is not the default of
  its kind (only ours: an entry the user changed stays); the coder's is a map in carl.json "thinking" that the
  subagent extension reads per model when it starts the coder.
- Tools: grep, find and ls on (settings.json defaultTools) and web search as an MCP server (mcp.json
  "carl-web-search"); the browser as a deferred MCP server (tool_search loads its tools when needed). Ours only.
- The delegation rule: the last block of APPEND_SYSTEM.md, between CARL's markers.
- The key (models.json apiKey): Pi runs a "!command" and takes what it prints. On macOS and Linux it is the bundle's
  `!cat ~/.config/carl/api-key`. On Windows Pi runs the command with the bash of Git for Windows when there is one,
  else with cmd.exe, which has no cat (HTTP 401): there it is findstr with full paths in double quotes, which both
  shells read the same way (checked against Pi 1.1.0).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .legacy import NAMES, PROVIDER, Names
from .merge import JsonObj, Report, delegation_markers, gone_note, strip_block
from .merge_opencode import BROWSER_MCP, SEARCH_MCP
from .models import ModelList

SEARCH_NAME = "carl-web-search"                         # Pi's MCP server entry
PI_TOOLS = ["+grep", "+find", "+ls"]                    # Pi's built-in tools that are off by default


def windows_key_ref(home: str, system_root: str) -> str:
    """Pi's apiKey on Windows: findstr "^" prints each line of the key file. bash -c and cmd /d /s /c read the same
    words from it: full paths in double quotes, with no variable and no "~" (home and system_root are Windows paths:
    C:\\Users\\me, C:\\Windows)."""
    def win(p: str) -> str:
        return p.replace("/", "\\").rstrip("\\")
    findstr = win(system_root or "C:\\Windows") + "\\System32\\findstr.exe"
    return f'!"{findstr}" "^" "{win(home)}\\{win(NAMES.key_file)}"'


def merge_pi_defaults(sett: JsonObj, st: JsonObj, provider_id: str, model: Optional[str], legacy: bool,
                      renamed: Dict[str, str], rep: Report, old: Names, level: str) -> bool:
    """settings.json defaultProvider / defaultModel / defaultThinkingLevel (level): only when unset or still
    ours. legacy: an install from before the state file (old: CARL's names then). True when settings.json is to be
    written."""
    if model is None:
        rep.add("kept", "Pi defaults (no model is installed on the server yet)")
        return False
    want = {"defaultProvider": provider_id, "defaultModel": model, "defaultThinkingLevel": level}
    prev = st.get("settings", {})
    ours_before = legacy and sett.get("defaultProvider") in (PROVIDER, old.alt)
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


def merge_pi_model_thinking(sett: JsonObj, st: JsonObj, provider_id: str, ml: ModelList, rep: Report) -> bool:
    """settings.json modelThinkingLevels: the main session's thinking of each of our models whose Main thinking is
    not the default of its kind (the default comes from defaultThinkingLevel, as before). Only ours: an entry that
    the user changed (Pi's /settings) stays. True when settings.json changed."""
    cur_levels = sett.get("modelThinkingLevels")
    levels: JsonObj = dict(cur_levels) if isinstance(cur_levels, dict) else {}
    raw = st.get("model_thinking")
    prev: JsonObj = raw if isinstance(raw, dict) else {}
    r = ml.rules
    want = {f"{provider_id}/{m.id}": str(m.pi_level("main", r)) for m in ml.models
            if m.role("main", r) != r.thinking.default(m.thinking, "main")}
    mine: Dict[str, str] = {}
    for key in sorted(set(want) | set(prev)):
        cur = levels.get(key)
        if cur is not None and cur != prev.get(key):
            if key in want and cur != want[key]:
                rep.add("kept", f"Pi thinking of {key} (yours: {cur})")
            continue
        if key in want:
            levels[key] = mine[key] = want[key]
        else:
            levels.pop(key, None)
    st["model_thinking"] = mine
    if not mine:
        st.pop("model_thinking")
    changed = levels != (sett.get("modelThinkingLevels") or {})
    if changed:
        rep.add("updated", "Pi thinking per model (settings.json modelThinkingLevels): "
                + (", ".join(f"{k} {v}" for k, v in mine.items()) or "the defaults"))
        if levels:
            sett["modelThinkingLevels"] = levels
        else:
            sett.pop("modelThinkingLevels", None)
    return changed


def pi_coder_thinking(provider_id: str, ml: ModelList) -> JsonObj:
    """carl.json "thinking": the coder's Pi thinking level per model ("provider/model"), which the subagent extension
    passes when it starts the coder. A model whose coder thinks as the main session ("main") has no entry: the
    extension then passes the session's level."""
    levels = {f"{provider_id}/{m.id}": m.pi_level("coder", ml.rules) for m in ml.models}
    return {"coder": {k: v for k, v in levels.items() if v is not None}}


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


def merge_pi_browser(servers: JsonObj, st: JsonObj, command: Optional[List[str]], rep: Report) -> bool:
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


def append_system_text(cur: str, rule: Optional[str], old: Names) -> str:
    """Pi's APPEND_SYSTEM.md with CARL's delegation rule as the last block (rule None: without it).
    The rule's earlier versions (either name: old is CARL's names before the rename) go; the user's text stays."""
    for names in (old, NAMES):
        cur = strip_block(cur, delegation_markers(names))
    cur = cur.strip()
    if rule is not None:
        begin, end = delegation_markers(NAMES)
        cur = (cur + "\n\n" if cur else "") + f"{begin}\n{rule}\n{end}"
    return cur + "\n" if cur else ""

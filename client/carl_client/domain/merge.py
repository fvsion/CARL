"""The merge rules both clients share (no I/O): what changed (Report), the coder agent's name and the texts that name
it, the delegation rule per client, our provider in a config next to the user's own, and the clients' default model.
merge_opencode.py and merge_pi.py hold each client's own rules.

What "ours" means: recorded in a state file next to each config (~/.config/opencode/carl.json,
~/.pi/agent/carl.json). Installs from before the state file existed are recognised by a signature (our provider
names / API-key file path, our prompt text).
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .legacy import NAMES, OLD_MTPLX_IDS, OUR_NAMES, PROVIDER, Legacy, Names
from .models import ModelList

JsonObj = Dict[str, Any]
"""A parsed JSON object (OpenCode / Pi configs are open-ended JSON)."""

REPORT_KINDS = ("backed up", "added", "updated", "kept", "removed")
ALT = NAMES.alt
CODER = "coder"                                         # the coder subagent's name in both clients
CODER_ALT = "carl-coder"                                # ... when the user has their own "coder"
CODER_NAMES = (CODER, CODER_ALT, "llm-deploy-coder")    # every name CARL used (the last before 1.2.0)
CODER_FILES = ("coder.md", "delegation.md")             # OpenCode: in the prompt folder
CODER_SIG = "specialist software engineer"              # in our coder prompt (the Pi agent file)
CLIENT_BLOCK = re.compile(r"<!-- carl:(\w+) (\w+) -->\n(.*?)(?=<!-- carl:)", re.S)
CLIENT_END = re.compile(r"<!-- carl:\w+ end -->\n\n?")


class Report:
    """What changed, grouped by kind, printed at the end."""

    def __init__(self) -> None:
        self.entries: Dict[str, List[str]] = {k: [] for k in REPORT_KINDS}

    def add(self, kind: str, line: str) -> None:
        self.entries[kind].append(line)

    def lines(self, width: int = 0) -> List[str]:
        """Each entry as "  kind      text", wrapped to `width` (default: 80; the setup passes the terminal's); a
        line after a newline in an entry (a command to copy) is indented, not wrapped."""
        width = width or 80
        out: List[str] = []
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
    remove: Tuple[str, ...]     # our entries to take out
    why: str                    # the reason shown for the removals
    kept: Tuple[str, ...]       # report lines about the user's own agents


@dataclass(frozen=True)
class OsLine:
    """A line of an agent prompt (file: "agents/coder.md") that reads otherwise on Windows (data/prompts.json)."""
    file: str
    posix: str
    windows: str


def parse_os_lines(doc: Any) -> Tuple[OsLine, ...]:
    """data/prompts.json, checked; ValueError naming the problem."""
    rows = doc.get("windows") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError("prompts.json: \"windows\" must be a list")
    out = []
    for r in rows:
        if not (isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k] and "\n" not in r[k]
                                            for k in ("file", "posix", "windows"))):
            raise ValueError("prompts.json: each line needs file, posix and windows as one-line texts")
        out.append(OsLine(r["file"], r["posix"], r["windows"]))
    return tuple(out)


def for_os(text: str, rel: str, lines: Tuple[OsLine, ...], windows: bool) -> str:
    """The agent prompt `rel` (agents/coder.md) for this OS: on Windows its lines in `lines` read as their Windows
    form; on macOS and Linux the text is as it is."""
    if not windows:
        return text
    swap = {x.posix: x.windows for x in lines if x.file == rel}
    return "\n".join(swap.get(line, line) for line in text.split("\n"))


def delegation_for(text: str, client: str, background: bool, browser: bool = True) -> str:
    """The delegation rule for one client: the paragraphs marked for it whose switch is on ("background"
    when background subagents are on, "browser" with the browser, "nobrowser" without it; "any" = every
    client), the others and the markers out."""
    on = {"background": background, "browser": browser, "nobrowser": not browser}

    def keep(m: "re.Match[str]") -> str:
        return m.group(3).rstrip("\n") + "\n\n" if on.get(m.group(1), False) and m.group(2) in (client, "any") else ""
    out = CLIENT_END.sub("\n", CLIENT_BLOCK.sub(keep, text))
    return re.sub(r"\n{3,}", "\n\n", out)


def coder_plan(mine: Set[str], has_coder: bool, has_alt: bool, wanted: bool) -> CoderPlan:
    """Name and steps for the coder: "coder", or CODER_ALT when the user owns "coder"; skipped
    when the user also owns that name or the coder is off. mine: the names whose agent is ours."""
    user_coder = has_coder and CODER not in mine
    name = CODER_ALT if user_coder else CODER
    theirs = (has_alt if name == CODER_ALT else has_coder) and name not in mine
    install = wanted and not theirs
    kept: List[str] = []
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


def split_agent(text: str) -> Tuple[str, str]:
    """(body, description) of an agent file with YAML front matter."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError("agent file has no front matter")
    front, body = m.group(1), m.group(2).lstrip()
    d = re.search(r'^description:\s*"(.*)"\s*$', front, re.M)
    if not d:
        raise ValueError("agent front matter has no description")
    return body, d.group(1)


def default_model(ml: ModelList, running: Optional[str] = None) -> Optional[str]:
    """The model to make the clients' default: the one the server runs now (single model: the setup
    passes no running model in router mode), else the one a server start loads, else the first. The
    dashboard saves the model it applies, so a restart runs it again; only a one-off start with --model
    differs until the next plain start."""
    if running and running in ml.ids:
        return running
    return ml.default or (ml.ids[0] if ml.ids else None)


def pick_ids(existing: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool], legacy: Legacy) -> Dict[str, str]:
    """Provider id to install under: ours by default, ALT when the user owns the name
    (also when the state file recorded ALT's name from before the rename)."""
    chosen = state.get("providers", {}).get(PROVIDER)
    if chosen == legacy.old.alt:
        chosen = ALT
    if not chosen:
        cur = existing.get(PROVIDER)
        chosen = PROVIDER if cur is None or is_ours(cur) else ALT
    return {PROVIDER: chosen}


def ours_oc(p: JsonObj, legacy: Legacy) -> bool:
    return p.get("name") in OUR_NAMES or legacy.has_our_key(p.get("options", {}).get("apiKey", ""))


def ours_pi(p: JsonObj, legacy: Legacy) -> bool:
    return legacy.has_our_key(p.get("apiKey", ""))


def old_mtplx_ids(providers: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool]) -> List[str]:
    """Our MTPLX provider ids still in a config (the one the state file recorded, else the
    ids earlier versions used), when the entry there is ours."""
    recorded = state.get("providers", {}).get("mtplx")
    ids = [recorded] if isinstance(recorded, str) else list(OLD_MTPLX_IDS)
    return [i for i in ids if isinstance(providers.get(i), dict) and is_ours(providers[i])]


def old_alt_ids(providers: JsonObj, state: JsonObj, is_ours: Callable[[JsonObj], bool], legacy: Legacy) -> List[str]:
    """Our provider under its id from before the rename (the old names' alt), when it is ours:
    the state file recorded it, or it reads our key file."""
    old_alt = legacy.old.alt
    old = providers.get(old_alt)
    recorded = state.get("providers", {}).get(PROVIDER) == old_alt
    return [old_alt] if isinstance(old, dict) and (recorded or is_ours(old)) else []


def merge_provider(providers: JsonObj, st: JsonObj, prov: JsonObj, new_id: str,
                   is_ours: Callable[[JsonObj], bool], client: str, rep: Report, legacy: Legacy) -> Dict[str, str]:
    """Our provider in, under new_id (pick_ids); ours under its pre-rename id and our MTPLX provider
    out. Returns the renamed ids: {old id: the id ours has now}."""
    if new_id != PROVIDER:
        rep.add("kept", f"{client} provider '{PROVIDER}' (yours); ours installed as '{new_id}'")
    if providers.get(new_id) != prov:
        rep.add("updated" if new_id in providers else "added", f"{client} provider '{new_id}'")
    providers[new_id] = prov
    renamed: Dict[str, str] = {}
    for old in old_alt_ids(providers, st, is_ours, legacy):
        if old != new_id:
            providers.pop(old)
            rep.add("removed", f"{client} provider '{old}' (CARL's provider is now '{new_id}')")
            renamed[old] = new_id
    for old in old_mtplx_ids(providers, st, is_ours):
        providers.pop(old)
        rep.add("removed", f"{client} provider '{old}' (MTPLX support was removed in CARL 1.2.0)")
    return renamed


def gone_note(ref: object, renamed: Dict[str, str]) -> str:
    """The note after a default model of the user's own whose provider CARL took out
    (renamed: old id -> the id ours has now), "" when the provider is still there."""
    prov, _, model = str(ref).partition("/")
    if prov in renamed:
        return f"; that provider is now '{renamed[prov]}': pick {renamed[prov]}/{model}"
    return "; that provider no longer exists: pick another" if prov in OLD_MTPLX_IDS else ""


def strip_block(text: str, markers: Tuple[str, str]) -> str:
    """text without the block between the markers (and the blank lines before it)."""
    begin, end = markers
    return re.sub(r"\n*" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", text, flags=re.S)


def drop_from(cfg: JsonObj, key: str, values: Tuple[object, ...]) -> None:
    """cfg[key] without the values (the key is set, also when it was missing)."""
    cfg[key] = [x for x in cfg.get(key, []) if x not in values]


def prune_empty(cfg: JsonObj, keys: Tuple[str, ...]) -> None:
    """Take out the keys whose value is empty (or missing)."""
    for k in keys:
        if not cfg.get(k):
            cfg.pop(k, None)


def models_state(ml: ModelList) -> JsonObj:
    """The state file's "models" (Phase 23.4.4): each model's thinking kind, the values written for the main session
    and the coder (/carl's override of this computer merged), and the dashboard's coder value (what /carl's
    "dashboard default" gives), for the /carl panel."""
    r = ml.rules
    return {m.id: {"thinking": m.thinking, "main": m.role("main", r), "coder": m.role("coder", r),
                   "dashboard": m.dashboard_coder(r), **({"free_form": True} if m.free_form else {})} for m in ml.models}


def delegation_markers(names: Names) -> Tuple[str, str]:
    """The lines around CARL's delegation rule in Pi's APPEND_SYSTEM.md."""
    return f"<!-- {names.block}:delegation begin -->", f"<!-- {names.block}:delegation end -->"

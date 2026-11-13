"""The client switch vocabulary (THE one: rule R14): the switches of the setup, which ./setup records in
~/.config/carl/client-install.env (KEY=value lines), a sync applies again and `carl-sync.py set` (the /carl panel)
changes; their values, their defaults, and which client reads a change without a restart. Pure. export() gives the
same vocabulary as JSON for carl-panel.js (client/shared/carl-switches.json).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

# the install switches a sync applies again (the setup records them)
SWITCHES = ("CLIENTS", "CODER", "NO_CODER", "WEB_SEARCH", "NO_LSP", "LSP", "NO_BROWSER", "BROWSER_HEADED",
            "NO_SIDEBAR", "NO_SWITCHER", "NO_MODEL_CHECK", "NO_BACKGROUND_SUBAGENTS", "NO_CACHE", "LLAMA_CTX",
            "NO_REMINDER", "CODER_THINKING", "CODER_MODEL", "CODER_MODEL_THINKING", "CODER_TESTS", "CODER_REQUEST_CHECK",
            "CODER_RUN_GATE", "CODER_FIX_ROUNDS", "CODER_FREE_FORM")
# the names the code uses (each one of SWITCHES)
CLIENTS = "CLIENTS"
CODER = "CODER"
NO_CODER = "NO_CODER"
WEB_SEARCH = "WEB_SEARCH"
NO_LSP = "NO_LSP"
LSP = "LSP"
NO_BROWSER = "NO_BROWSER"
BROWSER_HEADED = "BROWSER_HEADED"
NO_SIDEBAR = "NO_SIDEBAR"
NO_SWITCHER = "NO_SWITCHER"
NO_MODEL_CHECK = "NO_MODEL_CHECK"
NO_BACKGROUND_SUBAGENTS = "NO_BACKGROUND_SUBAGENTS"
NO_CACHE = "NO_CACHE"
LLAMA_CTX = "LLAMA_CTX"
NO_REMINDER = "NO_REMINDER"
CODER_THINKING = "CODER_THINKING"
CODER_MODEL = "CODER_MODEL"
CODER_MODEL_THINKING = "CODER_MODEL_THINKING"
CODER_TESTS = "CODER_TESTS"
CODER_REQUEST_CHECK = "CODER_REQUEST_CHECK"
CODER_RUN_GATE = "CODER_RUN_GATE"
CODER_FIX_ROUNDS = "CODER_FIX_ROUNDS"
CODER_FREE_FORM = "CODER_FREE_FORM"

# the values of the switches that take a word (configure.py's options take the same)
CLIENT_CHOICES = ("both", "opencode", "pi")
WEB_SEARCH_VALUES = ("exa", "parallel", "off")
TESTS_VALUES = ("after", "before", "off")              # the Tests setting (23.4.6; after by default since 23.5.0)
REQUEST_CHECK_VALUES = ("reminder", "on", "off")       # the Request Check (23.4.3 addendum)
FREE_FORM_VALUES = ("model", "on", "off")              # the free-form brief (23.4.3 addendum)
FIX_ROUNDS_VALUES = ("0", "1", "2", "3")               # the Coder Loop's fix rounds (23.4.3 addendum)

# the switches that "set" (the /carl panel) changes, and their values: 1 = off, "on" = the line goes (the default)
OFF_SWITCHES = (NO_CODER, NO_BACKGROUND_SUBAGENTS, NO_REMINDER, NO_BROWSER, NO_LSP, NO_SIDEBAR, NO_SWITCHER, NO_CACHE,
                NO_MODEL_CHECK)
SETTABLE: Dict[str, Tuple[str, ...]] = {
    **{k: ("1", "on") for k in OFF_SWITCHES}, WEB_SEARCH: WEB_SEARCH_VALUES,
    CODER: ("1", "auto"),          # /carl turns the coder on with CODER=1: also on a server with 1 slot
    CODER_THINKING: (),            # MODEL:VALUE (the coder's thinking values), per model: a map in one line
    CODER_MODEL: (),               # PROVIDER/MODEL (CODER_MODEL_RE) or main (Phase 23.4.5)
    CODER_MODEL_THINKING: (),      # a level or variant name (LEVEL_RE) or default
    CODER_TESTS: TESTS_VALUES,
    CODER_REQUEST_CHECK: REQUEST_CHECK_VALUES,
    CODER_RUN_GATE: ("on", "off"),                         # the Coder Loop's run gate (23.4.3 addendum)
    CODER_FIX_ROUNDS: FIX_ROUNDS_VALUES,
    CODER_FREE_FORM: FREE_FORM_VALUES}
THINKING_DEFAULT = "default"    # "set": the model's entry goes (the dashboard's Coder thinking applies again)
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")   # a model name (carl_client.domain.models)
# an external coder model (Phase 23.4.5): the client's PROVIDER/MODEL (openrouter's ids have / and :); its thinking
CODER_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/@+-]*")
CODER_MODEL_MAIN = "main"       # the coder on the main session's model (the default: no line)
LEVEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
OPENCODE_ONLY = (NO_LSP, NO_SIDEBAR, NO_SWITCHER)    # Pi has no such part (NO_MODEL_CHECK: both, Phase 23.5)
# Pi reads the coder's thinking and model (carl.json) each time it starts the coder: no restart
PI_LIVE = (*OPENCODE_ONLY, CODER_THINKING, CODER_MODEL, CODER_MODEL_THINKING, CODER_TESTS, CODER_REQUEST_CHECK,
           CODER_RUN_GATE, CODER_FIX_ROUNDS, CODER_FREE_FORM)
# OpenCode's carl-delegation reads the Tests setting (carl.json) at each coder task: no restart (Phase 23.4.6)
OPENCODE_LIVE = (CODER_TESTS, CODER_REQUEST_CHECK, CODER_RUN_GATE, CODER_FIX_ROUNDS, CODER_FREE_FORM)
OPENCODE_ENV = (WEB_SEARCH, NO_LSP, NO_BACKGROUND_SUBAGENTS)   # opencode.env: read when the shell starts
DEFAULTS = {WEB_SEARCH: "exa", CODER_THINKING: "", CODER_MODEL: CODER_MODEL_MAIN,
            CODER_MODEL_THINKING: THINKING_DEFAULT, CODER_TESTS: "after", CODER_REQUEST_CHECK: "reminder",
            CODER_RUN_GATE: "on", CODER_FIX_ROUNDS: "1", CODER_FREE_FORM: "model"}
# the values "set" writes as no line (the setup's default)
NO_LINE = ((CODER_MODEL, CODER_MODEL_MAIN), (CODER_MODEL_THINKING, THINKING_DEFAULT), *(
    (k, DEFAULTS[k]) for k in (CODER_TESTS, CODER_REQUEST_CHECK, CODER_RUN_GATE, CODER_FIX_ROUNDS, CODER_FREE_FORM)))
LIVE = {"opencode": OPENCODE_LIVE, "pi": PI_LIVE}


def parse_env(lines: Sequence[str]) -> Dict[str, str]:
    """client-install.env's lines as {KEY: value}: only the known keys."""
    out = {}
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k in SWITCHES:
            out[k] = v
    return out


def parse_set(args: List[str], coder_values: Sequence[str]) -> Dict[str, str]:
    """KEY=VALUE arguments of "set" as {KEY: VALUE}; ValueError (the message for the user) for anything else.
    coder_values: the coder's thinking values (carl_client.domain.thinking)."""
    if not args:
        raise ValueError("set needs at least one KEY=VALUE, for example: set NO_SIDEBAR=1")
    out: Dict[str, str] = {}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep or k not in SETTABLE:
            raise ValueError(f"set does not change '{k if sep else a}'. The keys: {', '.join(SETTABLE)}.")
        if k == CODER_THINKING:
            model, colon, value = v.partition(":")
            if not colon or not MODEL_ID_RE.fullmatch(model) or value not in (*coder_values, THINKING_DEFAULT):
                raise ValueError(f"{CODER_THINKING} takes MODEL:VALUE (VALUE: {', '.join(coder_values)} or "
                                 f"{THINKING_DEFAULT}), not '{v}'.")
            out[k] = thinking_text({**thinking_map(out.get(k, ""), coder_values, keep_default=True), model: value},
                                   keep_default=True)
            continue
        if k == CODER_MODEL:
            if v != CODER_MODEL_MAIN and not (CODER_MODEL_RE.fullmatch(v) and len(v) <= 200):
                raise ValueError(f"{CODER_MODEL} takes PROVIDER/MODEL or {CODER_MODEL_MAIN}, not '{v}'.")
            out[k] = v
            continue
        if k == CODER_MODEL_THINKING:
            if not LEVEL_RE.fullmatch(v):
                raise ValueError(f"{CODER_MODEL_THINKING} takes a thinking level or {THINKING_DEFAULT}, not '{v}'.")
            out[k] = v
            continue
        if v not in SETTABLE[k]:
            raise ValueError(f"{k} takes {' or '.join(SETTABLE[k])}, not '{v}'.")
        out[k] = v
    return out


def thinking_map(text: str, coder_values: Sequence[str], keep_default: bool = False) -> Dict[str, str]:
    """CODER_THINKING's value ("MODEL:VALUE,MODEL:VALUE") as {model: value}; an entry that is not one is left out
    (keep_default: also MODEL:default, the request of "set" to remove the model's entry)."""
    ok = (*coder_values, THINKING_DEFAULT) if keep_default else tuple(coder_values)
    out: Dict[str, str] = {}
    for part in (text or "").split(","):
        model, colon, value = part.strip().partition(":")
        if colon and MODEL_ID_RE.fullmatch(model) and value in ok:
            out[model] = value
    return out


def thinking_text(m: Dict[str, str], keep_default: bool = False) -> str:
    """The map as CODER_THINKING's value; a model whose value is "default" is left out (keep_default: kept, the
    request of "set" before it is merged)."""
    return ",".join(f"{k}:{m[k]}" for k in sorted(m) if keep_default or m[k] != THINKING_DEFAULT)


def shown(key: str, env: Mapping[str, str]) -> str:
    """A switch as "set" takes it: its value, or the setup's default when the file has no line for it (CODER_THINKING:
    "", the dashboard's values; CODER_MODEL: main; CODER_MODEL_THINKING: default)."""
    return env.get(key) or DEFAULTS.get(key, "on")


def drops_line(key: str, value: str) -> bool:
    """A value that "set" writes as no line: "" (the default), or "on" for a switch whose line turns it off (NO_...=1);
    "on" of CODER_REQUEST_CHECK is a value of its own."""
    return value == "" or (value == "on" and key in OFF_SWITCHES)


def planned(want: Mapping[str, str], before: Mapping[str, str], coder_values: Sequence[str]) -> Dict[str, str]:
    """What "set" writes, from what it was asked (parse_set) and the file before: CODER_THINKING merged over the other
    models' values (one model's value; default: none), the setup's defaults as no line (main, default, after ...),
    and a new coder model starting at its own thinking (CODER_MODEL_THINKING's line goes)."""
    out = dict(want)
    if CODER_THINKING in out:
        out[CODER_THINKING] = thinking_text({**thinking_map(before.get(CODER_THINKING, ""), coder_values),
                                             **thinking_map(out[CODER_THINKING], coder_values, keep_default=True)})
    out = {k: ("" if (k, v) in NO_LINE else v) for k, v in out.items()}
    if CODER_MODEL in out and CODER_MODEL_THINKING not in out \
            and shown(CODER_MODEL, before) != shown(CODER_MODEL, {CODER_MODEL: out[CODER_MODEL]}):
        out[CODER_MODEL_THINKING] = ""
    return out


def env_text(lines: Sequence[str], want: Mapping[str, str]) -> str:
    """client-install.env with the lines of `want` changed (a value that drops_line: the line goes); every other line
    stays."""
    keep = [ln for ln in lines if ln.strip().partition("=")[0] not in want]
    return "".join(f"{ln}\n" for ln in list(keep) + [f"{k}={v}" for k, v in want.items() if not drops_line(k, v)])


def apps_of(env: Mapping[str, str]) -> List[str]:
    """The clients the setup configures (CLIENTS: both by default)."""
    clients = env.get(CLIENTS, CLIENT_CHOICES[0])
    return [c for c in CLIENT_CHOICES[1:] if clients in (CLIENT_CHOICES[0], c)]


def restart_after(want: Mapping[str, str], changed: Mapping[str, Any], env: Mapping[str, str]) -> List[str]:
    """The clients that must restart after "set": those that read one of the keys whose value changed (else of every
    key asked) only when they start."""
    moved = [k for k in want if k in changed] or list(want)
    return [c for c in apps_of(env) if any(k not in LIVE[c] for k in moved)]


def needs_new_terminal(restart: Sequence[str], want: Mapping[str, str]) -> bool:
    """OpenCode reads opencode.env's switches only when the shell starts."""
    return "opencode" in restart and any(k in OPENCODE_ENV for k in want)


EXPORT_FILE = "shared/carl-switches.json"     # in the client folder


def export() -> Dict[str, Any]:
    """The vocabulary as JSON (EXPORT_FILE, for carl-panel.js)."""
    return {"_comment": "The client switch vocabulary for carl-panel.js, written from carl_client/domain/switches.py "
                        "(a test checks that the two agree).",
            "switches": list(SWITCHES), "off_switches": list(OFF_SWITCHES),
            "settable": {k: list(v) for k, v in SETTABLE.items()}, "defaults": dict(DEFAULTS),
            "thinking_default": THINKING_DEFAULT, "coder_model_main": CODER_MODEL_MAIN,
            "live": {c: list(v) for c, v in LIVE.items()}, "opencode_env": list(OPENCODE_ENV)}


def export_text() -> str:
    """EXPORT_FILE's text."""
    return json.dumps(export(), indent=2) + "\n"

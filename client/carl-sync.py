#!/usr/bin/env python3
"""CARL client config sync. The dashboard on the server sends the client config
(its Connect tab, or ./carl.sh push on the server). This tool applies it to
OpenCode and Pi on this computer: it runs the installer (install.sh next to this
file) again, with its backups.

Usage: carl-sync.py COMMAND

  watch           The sync service. It keeps one connection to the dashboard API
                  and applies each new config. No port opens on this computer.
                  launchd or systemd --user keeps it running (install.sh sets
                  that up).
  once            Check one time and apply a new config. Without the service,
                  OpenCode and Pi run this when they start.
  apply           Apply the config that waits.
  auto on|off     on (the default): apply new configs at once. off: new configs
                  wait until you apply them.
  status          Show the state as JSON. The /carl panel in OpenCode and Pi
                  reads it.
  register on|off For install.sh: the sync service is installed (on) or not
                  (off). Without the service, OpenCode and Pi check for a new
                  config when they start.
  set KEY=VALUE [KEY=VALUE ...]
                  Change switches of the setup and write the configs again with
                  the models that this computer has now (no new config from the
                  dashboard). The /carl panel uses it. The keys:
                    NO_CODER, NO_BACKGROUND_SUBAGENTS, NO_REMINDER, NO_BROWSER,
                    NO_LSP, NO_SIDEBAR, NO_SWITCHER, NO_CACHE, NO_MODEL_CHECK:
                      1 turns the part off, on turns it on again.
                    WEB_SEARCH: exa, parallel or off.
                    CODER: 1 (the coder also with 1 slot) or auto (with 2
                      slots or more). /carl sets CODER=1 when you turn the
                      coder on.
                    CODER_THINKING: MODEL:VALUE, the coder's thinking with
                      that model on this computer (over the dashboard's
                      Coder thinking). VALUE: main (same as main), on, off,
                      low, medium or xhigh; default removes the model's
                      value (the dashboard's applies again). The other
                      models keep theirs.
                    CODER_MODEL: PROVIDER/MODEL, an external model of
                      the client for the coder (it then uses no slot of
                      the server), or main (the main session's model,
                      the default).
                    CODER_MODEL_THINKING: the thinking level (Pi) or
                      variant (OpenCode) of that external model; default
                      removes it (the model's own default). A new
                      CODER_MODEL removes it too.
                    CODER_TESTS: before (the default), after or off: when
                      CARL's test session runs for new code. OpenCode and
                      Pi read it at each coder task (no restart).
                    CODER_REQUEST_CHECK: reminder (the default), on or off:
                      the check that the brief carries the literals of
                      your request (no restart).
                    CODER_RUN_GATE: on (the default) or off: CARL checks
                      the coder's work after each code session.
                    CODER_FIX_ROUNDS: 0, 1 (the default), 2 or 3: the fix
                      rounds that the run gate starts by itself.
                  It shows the result as JSON: what changed, which client
                  (OpenCode, Pi) must restart, and the server's slots (as the
                  installer read them; null when the server does not answer).
                  Exit code 2: a key or a value that it does not know (it then
                  changes nothing).

The server's address comes from remote.json next to this file. The server writes
it at every start. The API key comes from api-key next to this file, or from
~/.config/carl/api-key. A sync applies again the switches of the last install
(the clients, the coder, web search, LSP, browser and the others, in
~/.config/carl/client-install.env: ./setup and "set" write them).
So a sync changes only what the server decides: the installed models.
State: ~/.config/carl/client-sync.json. The installer's output:
~/.config/carl/client-sync.log.
"""
from __future__ import annotations

import fcntl
import getpass
import http.client
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(os.path.expanduser("~"), ".config", "carl")
STATE = os.path.join(CONF, "client-sync.json")
LOG = os.path.join(CONF, "client-sync.log")
LOCK = os.path.join(CONF, "client-sync.lock")
STATE_LOCK = os.path.join(CONF, "client-sync.state.lock")
INSTALL_ENV = os.path.join(CONF, "client-install.env")
# the install switches a sync applies again (install.sh records them)
SWITCHES = ("CLIENTS", "CODER", "NO_CODER", "WEB_SEARCH", "NO_LSP", "LSP", "NO_BROWSER", "BROWSER_HEADED",
            "NO_SIDEBAR", "NO_SWITCHER", "NO_MODEL_CHECK", "NO_BACKGROUND_SUBAGENTS", "NO_CACHE", "LLAMA_CTX",
            "NO_REMINDER", "CODER_THINKING", "CODER_MODEL", "CODER_MODEL_THINKING", "CODER_TESTS", "CODER_REQUEST_CHECK",
            "CODER_RUN_GATE", "CODER_FIX_ROUNDS", "CODER_FREE_FORM")
# the switches that "set" (the /carl panel) changes, and their values: 1 = off, "on" = the line goes (the default)
OFF_SWITCHES = ("NO_CODER", "NO_BACKGROUND_SUBAGENTS", "NO_REMINDER", "NO_BROWSER", "NO_LSP", "NO_SIDEBAR",
                "NO_SWITCHER", "NO_CACHE", "NO_MODEL_CHECK")
SETTABLE = {**{k: ("1", "on") for k in OFF_SWITCHES}, "WEB_SEARCH": ("exa", "parallel", "off"),
            "CODER": ("1", "auto"),    # /carl turns the coder on with CODER=1: also on a server with 1 slot
            "CODER_THINKING": (),      # MODEL:VALUE (THINKING_VALUES), per model: a map in one line
            "CODER_MODEL": (),         # PROVIDER/MODEL (CODER_MODEL_RE) or main (Phase 23.4.5)
            "CODER_MODEL_THINKING": (),    # a level or variant name (LEVEL_RE) or default
            "CODER_TESTS": ("before", "after", "off"),    # the Tests setting (Phase 23.4.6)
            "CODER_REQUEST_CHECK": ("reminder", "on", "off"),    # the Request Check (23.4.3 addendum)
            "CODER_RUN_GATE": ("on", "off"),                       # the Coder Loop's run gate (23.4.3 addendum)
            "CODER_FIX_ROUNDS": ("0", "1", "2", "3"),              # its fix rounds
            "CODER_FREE_FORM": ("model", "on", "off")}             # the free-form brief (23.4.3 addendum)
THINKING_VALUES = ("main", "on", "off", "low", "medium", "xhigh")
THINKING_DEFAULT = "default"    # "set": the model's entry goes (the dashboard's Coder thinking applies again)
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")   # a model name (client/carl_models.py)
# an external coder model (Phase 23.4.5): the client's PROVIDER/MODEL (openrouter's ids have / and :); its thinking
CODER_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/@+-]*")
CODER_MODEL_MAIN = "main"       # the coder on the main session's model (the default: no line)
LEVEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
OPENCODE_ONLY = ("NO_LSP", "NO_SIDEBAR", "NO_SWITCHER", "NO_MODEL_CHECK")    # Pi has no such part
# Pi reads the coder's thinking and model (carl.json) each time it starts the coder: no restart
PI_LIVE = (*OPENCODE_ONLY, "CODER_THINKING", "CODER_MODEL", "CODER_MODEL_THINKING", "CODER_TESTS", "CODER_REQUEST_CHECK",
           "CODER_RUN_GATE", "CODER_FIX_ROUNDS", "CODER_FREE_FORM")
# OpenCode's carl-delegation reads the Tests setting (carl.json) at each coder task: no restart (Phase 23.4.6)
OPENCODE_LIVE = ("CODER_TESTS", "CODER_REQUEST_CHECK", "CODER_RUN_GATE", "CODER_FIX_ROUNDS", "CODER_FREE_FORM")
OPENCODE_ENV = ("WEB_SEARCH", "NO_LSP", "NO_BACKGROUND_SUBAGENTS")   # opencode.env: read when the shell starts
READ_TIMEOUT = 75            # the dashboard sends a comment every 25 s: silence this long = reconnect
BACKOFF = (5, 10, 30, 60)
MAX_CONFIG = 1 << 20         # the published config (the installed models) is a few KB
MAX_LINE = 1 << 16           # one line of the event stream
VERSION_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")    # the config's version: written to the state file and the log
VERSION_TEXT_RE = re.compile(r"[0-9A-Za-z.+-]{1,40}")   # a CARL version (1.7.0), as /carl shows it
# What can go wrong when the dashboard is away or answers badly (the service then tries again).
NET_ERRORS = (OSError, ValueError, http.client.HTTPException)

Json = Dict[str, Any]


def load(path: str) -> Json:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def state() -> Json:
    return {"auto_apply": True, **load(STATE)}


def save_state(doc: Json) -> None:
    os.makedirs(CONF, exist_ok=True)
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.chmod(STATE + ".tmp", 0o600)
    os.replace(STATE + ".tmp", STATE)


def update(**kv: Any) -> Json:
    os.makedirs(CONF, exist_ok=True)
    with open(STATE_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)            # the service, install.sh and the plugins all write it
        doc = {**state(), **kv}
        save_state(doc)
    return doc


def server() -> Tuple[str, str]:
    """(the dashboard's API, the key); ValueError when this folder has no remote.json."""
    r = load(os.path.join(HERE, "remote.json"))
    api = r.get("cache_api")
    if not isinstance(api, str) or (url := urllib.parse.urlsplit(api)).scheme not in ("http", "https") \
            or not url.netloc:
        raise ValueError("There is no remote.json (the server's address) in the client folder. Make a new client "
                         "package on the server Mac (./carl.sh package), unzip it over this folder and run ./setup.")
    for f in (os.path.join(HERE, "api-key"), os.path.join(CONF, "api-key")):
        try:
            with open(f, encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return api.rstrip("/"), key
        except OSError:
            continue
    raise ValueError("There is no API key next to carl-sync.py or in ~/.config/carl/api-key. Make a new client "
                     "package on the server Mac (./carl.sh package), unzip it over this folder and run ./setup.")


def describe(e: BaseException) -> str:
    """An error for the state file (the /carl panel shows it): what went wrong and what to do."""
    if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
        return ("The server refused the API key. Make a new client package on the server Mac (./carl.sh package), "
                "unzip it over this folder and run ./setup. The package has the key.")
    if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
        return f"The dashboard does not answer: {e.reason}. Make sure that the dashboard runs on the server."[:200]
    return str(e)[:200] or type(e).__name__


MODE = "check"               # how this run reaches the dashboard: "service" (watch) or "check" (once)


def who() -> str:
    """This client, for the dashboard's Clients list (X-Carl-Client): a stable id, the host name, the user,
    the system, the config it has, auto-apply, and whether the service runs."""
    st = state()
    if not st.get("id"):
        st = update(id=uuid.uuid4().hex[:12])
    return json.dumps({"id": st["id"], "host": socket.gethostname()[:60], "user": getpass.getuser()[:40],
                       "os": f"{platform.system()} {platform.machine()}"[:40], "applied": st.get("applied") or "",
                       "auto_apply": st.get("auto_apply", True), "mode": MODE, "coder_model": coder_model()},
                      separators=(",", ":"))


def coder_model() -> str:
    """The coder's model on this computer (Phase 23.4.5; the dashboard's Clients list): "main" (the main session's
    model, on CARL's server) or the external PROVIDER/MODEL of client-install.env's CODER_MODEL."""
    v = install_env().get("CODER_MODEL", "")
    return v if CODER_MODEL_RE.fullmatch(v) and len(v) <= 200 else CODER_MODEL_MAIN


def request(url: str, key: str, etag: str = "") -> urllib.request.Request:
    headers = {"Authorization": f"Bearer {key}", "X-Carl-Client": who()}
    if etag:
        headers["If-None-Match"] = f'"{etag}"'
    return urllib.request.Request(url, headers=headers)


SERVER_VERSION: Dict[str, str] = {}        # the server's CARL version from the last reply (X-Carl-Version)


def _note_version(headers: Any) -> None:
    v = (headers.get("X-Carl-Version") or "").strip() if headers is not None else ""
    if VERSION_TEXT_RE.fullmatch(v):
        SERVER_VERSION["v"] = v


def fetch_config(api: str, key: str, etag: str) -> Optional[Json]:
    """The published config when it isn't `etag`; None when it is (304). Notes the server's CARL version."""
    try:
        with urllib.request.urlopen(request(api + "/carl/client/config", key, etag), timeout=10) as r:
            _note_version(r.headers)
            body = r.read(MAX_CONFIG + 1)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            _note_version(e.headers)
            return None
        raise
    if len(body) > MAX_CONFIG:
        raise ValueError("The client config from the server is too large.")
    doc = json.loads(body)
    if not (isinstance(doc, dict) and isinstance(doc.get("models"), dict)
            and isinstance(doc.get("version"), str) and VERSION_RE.fullmatch(doc["version"])):
        raise ValueError("The server sent data that is not a client config.")
    return doc


def install_env() -> Dict[str, str]:
    """The switches of the last install (KEY=value lines; only the known keys)."""
    out = {}
    try:
        with open(INSTALL_ENV, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k in SWITCHES:
                    out[k] = v
    except OSError:
        pass
    return out


def apply(doc: Json) -> None:
    """Write the installed models and run the installer again (its backups, its merge rules)."""
    path = os.path.join(HERE, "installed-models.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc["models"], f, indent=1)
    os.replace(path + ".tmp", path)
    env = {**os.environ, **install_env(), "NO_PROFILE": "1", "CARL_SYNC": "1"}
    os.makedirs(CONF, exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as log:
        log.write(f"== {time.strftime('%Y-%m-%d %H:%M:%S')}: client config {doc['version']}\n")
        log.flush()
        rc = subprocess.run(["bash", os.path.join(HERE, "install.sh")], env=env, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
    if rc != 0:
        raise RuntimeError(f"The installer stopped with an error (exit code {rc}). Its output is in {LOG}.")


def parse_set(args: List[str]) -> Dict[str, str]:
    """KEY=VALUE arguments of "set" as {KEY: VALUE}; ValueError (the message for the user) for anything else."""
    if not args:
        raise ValueError("set needs at least one KEY=VALUE, for example: set NO_SIDEBAR=1")
    out: Dict[str, str] = {}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep or k not in SETTABLE:
            raise ValueError(f"set does not change '{k if sep else a}'. The keys: {', '.join(SETTABLE)}.")
        if k == "CODER_THINKING":
            model, colon, value = v.partition(":")
            if not colon or not MODEL_ID_RE.fullmatch(model) or value not in (*THINKING_VALUES, THINKING_DEFAULT):
                raise ValueError(f"CODER_THINKING takes MODEL:VALUE (VALUE: {', '.join(THINKING_VALUES)} or "
                                 f"{THINKING_DEFAULT}), not '{v}'.")
            out[k] = thinking_text({**thinking_map(out.get(k, ""), keep_default=True), model: value}, keep_default=True)
            continue
        if k == "CODER_MODEL":
            if v != CODER_MODEL_MAIN and not (CODER_MODEL_RE.fullmatch(v) and len(v) <= 200):
                raise ValueError(f"CODER_MODEL takes PROVIDER/MODEL or {CODER_MODEL_MAIN}, not '{v}'.")
            out[k] = v
            continue
        if k == "CODER_MODEL_THINKING":
            if not LEVEL_RE.fullmatch(v):
                raise ValueError(f"CODER_MODEL_THINKING takes a thinking level or {THINKING_DEFAULT}, not '{v}'.")
            out[k] = v
            continue
        if v not in SETTABLE[k]:
            raise ValueError(f"{k} takes {' or '.join(SETTABLE[k])}, not '{v}'.")
        out[k] = v
    return out


def thinking_map(text: str, keep_default: bool = False) -> Dict[str, str]:
    """CODER_THINKING's value ("MODEL:VALUE,MODEL:VALUE") as {model: value}; an entry that is not one is left out
    (keep_default: also MODEL:default, the request of "set" to remove the model's entry)."""
    ok = (*THINKING_VALUES, THINKING_DEFAULT) if keep_default else THINKING_VALUES
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


DEFAULTS = {"WEB_SEARCH": "exa", "CODER_THINKING": "", "CODER_MODEL": CODER_MODEL_MAIN,
            "CODER_MODEL_THINKING": THINKING_DEFAULT, "CODER_TESTS": "before", "CODER_REQUEST_CHECK": "reminder",
            "CODER_RUN_GATE": "on", "CODER_FIX_ROUNDS": "1", "CODER_FREE_FORM": "model"}


def shown(key: str, env: Dict[str, str]) -> str:
    """A switch as "set" takes it: its value, or the setup's default when the file has no line for it (CODER_THINKING:
    "", the dashboard's values; CODER_MODEL: main; CODER_MODEL_THINKING: default)."""
    return env.get(key) or DEFAULTS.get(key, "on")


def drops_line(key: str, value: str) -> bool:
    """A value that "set" writes as no line: "" (the default), or "on" for a switch whose line turns it off (NO_...=1);
    "on" of CODER_REQUEST_CHECK is a value of its own."""
    return value == "" or (value == "on" and key in OFF_SWITCHES)


def write_install_env(want: Dict[str, str]) -> None:
    """client-install.env with the lines of `want` changed (a value that drops_line: the line goes); every other line
    stays."""
    try:
        with open(INSTALL_ENV, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    keep = [ln for ln in lines if ln.strip().partition("=")[0] not in want]
    text = "".join(f"{ln}\n" for ln in keep + [f"{k}={v}" for k, v in want.items() if not drops_line(k, v)])
    os.makedirs(CONF, exist_ok=True)
    fd = os.open(INSTALL_ENV + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(INSTALL_ENV + ".tmp", 0o600)
    os.replace(INSTALL_ENV + ".tmp", INSTALL_ENV)


def set_switches(want: Dict[str, str]) -> Json:
    """Change the switches in client-install.env, then run the installer again with the models this folder has
    (installed-models.json: no new config). One at a time with a sync (the same lock). The result: what changed,
    ok or the error, and which client must restart."""
    os.makedirs(CONF, exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = install_env()
        if "CODER_THINKING" in want:              # one model's value (default: none): the other models keep theirs
            merged = thinking_text({**thinking_map(before.get("CODER_THINKING", "")),
                                    **thinking_map(want["CODER_THINKING"], keep_default=True)})
            want = {**want, "CODER_THINKING": merged}     # "": no model left, the line goes
        # the defaults have no line (main, default, before); a new coder model starts at its own thinking (model
        # default)
        want = {k: ("" if (k, v) in (("CODER_MODEL", CODER_MODEL_MAIN), ("CODER_MODEL_THINKING", THINKING_DEFAULT),
                                     ("CODER_TESTS", DEFAULTS["CODER_TESTS"]),
                                     ("CODER_REQUEST_CHECK", DEFAULTS["CODER_REQUEST_CHECK"]),
                                     ("CODER_RUN_GATE", DEFAULTS["CODER_RUN_GATE"]),
                                     ("CODER_FIX_ROUNDS", DEFAULTS["CODER_FIX_ROUNDS"]),
                                     ("CODER_FREE_FORM", DEFAULTS["CODER_FREE_FORM"]))
                    else v) for k, v in want.items()}
        if "CODER_MODEL" in want and "CODER_MODEL_THINKING" not in want \
                and shown("CODER_MODEL", before) != shown("CODER_MODEL", {"CODER_MODEL": want["CODER_MODEL"]}):
            want["CODER_MODEL_THINKING"] = ""
        write_install_env(want)
        after = install_env()
        changed = {k: {"from": shown(k, before), "to": shown(k, after)} for k in want
                   if shown(k, before) != shown(k, after)}
        # not NO_PROFILE: a switch may need the shell profile's line for OpenCode's tool switches, as ./setup adds it
        env = {**os.environ, **after, "CARL_SYNC": "1"}
        for k, v in want.items():
            if drops_line(k, v):
                env.pop(k, None)                     # a switch from the environment must not undo the change
        clients = after.get("CLIENTS", "both")
        apps = [c for c in ("opencode", "pi") if clients in ("both", c)]
        moved = [k for k in want if k in changed] or list(want)    # the keys whose value changed (else all)
        live = {"opencode": OPENCODE_LIVE, "pi": PI_LIVE}
        restart = [c for c in apps if any(k not in live[c] for k in moved)]
        result: Json = {"set": want, "changed": changed, "ok": True, "error": None, "restart": restart,
                        "new_terminal": "opencode" in restart and any(k in OPENCODE_ENV for k in want), "slots": None}
        # the slot count the installer reads for its coder rule (/props total_slots): /carl warns when it turns the
        # coder on and the server runs 1 slot
        slots_out = os.path.join(CONF, f".set-slots-{os.getpid()}")
        env["CARL_SLOTS_OUT"] = slots_out
        try:
            with open(LOG, "w", encoding="utf-8") as log:
                log.write(f"== {time.strftime('%Y-%m-%d %H:%M:%S')}: set "
                          f"{' '.join(f'{k}={v}' for k, v in want.items())}\n")
                log.flush()
                rc = subprocess.run(["bash", os.path.join(HERE, "install.sh")], env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
            if rc != 0:
                raise RuntimeError(f"The installer stopped with an error (exit code {rc}). Its output is in {LOG}.")
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            result.update(ok=False, error=describe(e), restart=[], new_terminal=False)
        result["slots"] = read_slots(slots_out)
        return result


def read_slots(path: str) -> Optional[int]:
    """The slot count the installer wrote to `path` (the file goes); None when it wrote none."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(32).strip()
        os.remove(path)
    except OSError:
        return None
    return int(text) if text.isdigit() and 0 < int(text) < 1000 else None


def once(apply_waiting: bool = False) -> Json:
    """Check the published config; apply a new one (auto-apply), or keep it waiting."""
    os.makedirs(CONF, exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)            # one sync at a time (the service, a plugin, the panel)
        st = state()
        try:
            api, key = server()
            doc = fetch_config(api, key, "" if apply_waiting else str(st.get("applied") or ""))
        except NET_ERRORS as e:
            return update(checked=time.time(), error=describe(e))
        if SERVER_VERSION.get("v"):                  # /carl compares the client package with it
            st = update(server_version=SERVER_VERSION["v"])
        if doc is None or doc["version"] == st.get("applied"):
            return update(checked=time.time(), error=None, pending=None)
        if not (st.get("auto_apply", True) or apply_waiting):
            return update(checked=time.time(), error=None, pending=doc["version"])
        try:
            apply(doc)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            return update(checked=time.time(), error=describe(e), pending=doc["version"])
        done = update(checked=time.time(), error=None, pending=None, applied=doc["version"],
                      applied_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        try:                                         # tell the dashboard at once (its Clients list)
            fetch_config(api, key, doc["version"])
        except NET_ERRORS:
            pass
        return done


def report(api: str, key: str) -> None:
    """Tell the dashboard this client's state (who(): its config, the coder's model) with a check of the config it
    has (a 304 as a rule); a new config is not applied here (the dashboard sends its event for that)."""
    try:
        fetch_config(api, key, str(state().get("applied") or ""))
    except NET_ERRORS:
        pass


def events(api: str, key: str) -> Iterator[str]:
    """The event names the dashboard sends (a blank name for its keep-alive comments)."""
    with urllib.request.urlopen(request(api + "/carl/client/events", key), timeout=READ_TIMEOUT) as r:
        name = ""
        while raw := r.readline(MAX_LINE):
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith(":"):
                yield ""
            elif not line and name:
                yield name
                name = ""


def watch() -> None:
    """The service: listen, apply on a push, reconnect (5 s, 10 s, 30 s, then every minute)."""
    global MODE
    MODE = "service"
    update(service=True, bundle=HERE)
    fails = 0
    while True:
        try:
            api, key = server()
            told = who()                             # what the stream's request told the dashboard
            for name in events(api, key):
                update(alive=time.time(), connected=True)
                fails = 0
                if name == "config":
                    once()
                elif who() != told:                  # a /carl change (the coder's model): tell the dashboard
                    report(api, key)
                told = who()
            update(connected=False)                  # the dashboard closed the stream (it stopped)
        except NET_ERRORS as e:
            update(connected=False, error=describe(e))
        time.sleep(BACKOFF[min(fails, len(BACKOFF) - 1)])
        fails += 1


def main(argv: List[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
    elif cmd == "watch":
        watch()
    elif cmd == "once":
        print(json.dumps(once(), indent=1))
    elif cmd == "apply":
        print(json.dumps(once(apply_waiting=True), indent=1))
    elif cmd == "auto" and len(argv) == 2 and argv[1] in ("on", "off"):
        print(json.dumps(update(auto_apply=argv[1] == "on"), indent=1))
    elif cmd == "register" and len(argv) == 2 and argv[1] in ("on", "off"):
        update(service=argv[1] == "on", bundle=HERE)
    elif cmd == "set":
        try:
            want = parse_set(argv[1:])
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        result = set_switches(want)
        print(json.dumps(result, indent=1))
        return 0 if result["ok"] else 1
    elif cmd == "status":
        print(json.dumps({**state(), "bundle": HERE}, indent=1))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)

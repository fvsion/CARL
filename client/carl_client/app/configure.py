"""Merge the CARL client configs into OpenCode and Pi without clobbering
anything the user owns. Called by the setup's config step (carl_client.app.install; it
resolves the key, server address, context window and whether the coder is on).

What "ours" means: recorded in a state file next to each config
(~/.config/opencode/carl.json, ~/.pi/agent/carl.json). Installs from before
the state file existed are recognised by a signature (our provider names /
API-key file path, our prompt text).

Rules (the merge rules are in carl_client.domain: merge, merge_opencode, merge_pi, legacy)
- Models: one entry per model installed on the server (carl_client.domain.models, from
  installed-models.json or the server's /v1/models), each served under its own
  name with its family's thinking options; the block is rewritten each run, so
  a deleted model's entry goes and a new one appears.
- Providers: ours replace only our own earlier version. If the user has their
  own provider called "llamacpp", ours is installed alongside as "carl" and
  every reference uses that id.
- Renamed in 1.2.0 and removed in 1.2.0: carl_client.domain.legacy (the names before the
  rename and the pieces of the MTPLX support are taken out of an earlier install; ours only).
- Default model (OpenCode model/small_model, Pi defaultProvider/defaultModel/
  defaultThinkingLevel): the model the server runs now (else the one a start loads); set only
  when unset or still the value we set before.
- Thinking per role (Phase 23.4.4; the dashboard's Main thinking and Coder thinking, per model, carried
  in installed-models.json): the main session's is each OpenCode model entry's reasoningEffort and,
  when it is not the default of its kind, a Pi modelThinkingLevels entry (only ours: an entry the user
  changed stays). The coder's follows the model the coder runs on: OpenCode's coder agent has no
  reasoningEffort; carl-delegation's option coderThinking ({"provider/model": effort}) is set on the
  coder's requests by its chat.params hook. Pi: a map in carl.json "thinking" that the subagent
  extension reads per model when it starts the coder. Both: also for the chain's test session; a model
  whose Coder thinking is "main" (the default) has no entry, so the coder thinks as the main session.
  /carl's Coder thinking (--coder-thinking MODEL:VALUE,...: CODER_THINKING in ~/.config/carl/client-install.env)
  overrides the dashboard's value on this computer, per model. Both state files (carl.json) keep "models":
  each model's thinking kind, main and coder values as written and the dashboard's coder value (what
  /carl's "dashboard default" gives), for the /carl panel.
- The coder on an external model (Phase 23.4.5; --coder-model PROVIDER/MODEL: CODER_MODEL, /carl's Coder model):
  OpenCode's coder agent gets "model" and no "temperature" (no CARL sampling for a model that is not CARL's);
  carl-delegation gets "coderModel" and, with --coder-model-thinking, "coderVariant" (its chat.params hook applies that
  variant of the model to the coder's requests). Pi: carl.json "coder_model" (the subagent extension starts the coder
  with --model) and, with a thinking level, "thinking": {"coder": {"PROVIDER/MODEL": LEVEL}}. Both state files keep
  "coder_model" ("main" when the coder runs on the main session's model). The provider's keys stay the client's:
  CARL writes only the model's name.
- The Request Check (the 23.4.3 addendum; --request-check reminder|on|off: CODER_REQUEST_CHECK): both state files keep
  "request_check", which carl-delegation (OpenCode, through stateFile; Pi) reads at each coder task.
- The Tests setting (Phase 23.4.6; --coder-tests after|before|off: CODER_TESTS, /carl's Tests row; after by default
  since Phase 23.5.0): when the chain's test session runs. Both state files keep "coder_tests"; OpenCode's
  carl-delegation gets the option "stateFile" (its carl.json) and Pi's subagent extension reads its own carl.json,
  each at every coder task (no restart).
- The shared files: each plugin or extension folder gets the shared files client/shared/manifest.json lists for it
  (the brief check as a command, client/shared/carl-brief-check.mjs, goes next to carl-brief.js).
- The models (Phase 23.4.4 item 13): in single-model mode only the model the server runs (--running,
  when the list has it); in router mode (no --running) every model of the list.
- Agents, prompts, extensions: never overwrite a user's file or agent with the
  same name. CARL's coder subagent is "coder", or "carl-coder" when the user has
  an agent "coder" of their own (before 1.2.0 that fallback was "llm-deploy-coder":
  ours under the other names is taken out); a user's own "carl-coder" too: ours
  is skipped with a note.
- Lists (plugin, instructions): ours are appended / removed, others kept.
- Tools: carl_client.domain.merge_opencode and merge_pi; OpenCode's switches reach the shell through the
  UserEnvironment port (adapters/userenv_profile.py; on Windows adapters/userenv_hkcu.py).
- The disk cache (client/shared/carl-cache.js): the OpenCode plugin carl-cache (the same kind
  of entry; it replaces carl-prefix-cache) and the Pi extension carl-cache save each session
  and each agent's prompt on the server's disk and restore them before a request.
  NO_CACHE=1 leaves both out.
- OpenCode plugin carl-model-check (opencode.json "plugin", with our provider id as its
  option): warns when the model picked isn't the one the server runs, isn't installed, or
  is being loaded (router mode). NO_MODEL_CHECK=1 leaves it out.
- Clients (--clients both|opencode|pi, CLIENTS= in the setup): the configs of the clients chosen; the
  other client's files are not read or changed.
- Every changed file is backed up first: <file>.before-carl (the original, once) and <file>.bak (the
  version before CARL's last change, replaced each time). The old <file>.bak.<timestamp> copies of
  CARL's files are removed once. A config that is not plain JSON stops the run before anything is
  written.

Layout: the merge rules are plain functions on the parsed configs (no I/O, carl_client.domain); the
Installer applies them and does every file operation through the Files port, whose adapter LocalFiles is
wired in main().
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

from ..domain import switches as sw
from ..domain.legacy import (NAMES, OLD_OC_PLUGIN, OLD_OC_PLUGIN_SIG, OLD_PI_EXT, OLD_PI_EXT_SIG, PROVIDER, Legacy,
                             Names)
from ..domain.merge import (ALT, CODER, CODER_ALT, CODER_FILES, CODER_NAMES, CODER_SIG, JsonObj, Report,
                            coder_plan, default_model, delegation_for, drop_from, for_os, merge_provider, models_state,
                            name_agent, ours_oc, ours_pi, pick_ids, prune_empty, report_coder_plan, split_agent)
from ..domain.merge_opencode import (BACKGROUND, BROWSER_AGENT, CACHE, DELEGATION, MODEL_CHECK, OC_PLUGIN_DIR,
                                     OLD_CACHE, PANEL, PROFILE_BEGIN, PROFILE_END, PROFILE_LINE, RULE_BEGIN, RULE_END,
                                     SIDEBAR, SWITCHER, AgentText, browser_command, env_file_text, merge_lsp,
                                     merge_oc_browser, merge_oc_coder, merge_oc_default_model, merge_plugin_entry,
                                     merge_tui_plugin, oc_rule_text, ours_agent, plural_files, profile_text,
                                     same_path, undo_title_disable)
from ..domain import merge_pi
from ..domain.merge_pi import (SEARCH_NAME, merge_pi_browser, merge_pi_default_tools, merge_pi_defaults,
                               merge_pi_model_thinking, merge_pi_web_search, pi_coder_thinking, windows_key_ref)
from ..domain.models import (ModelList, coder_efforts, coder_overrides, only_running, opencode_models,
                             parse_overrides, pi_models)
from ..ports import Files, UserEnvironment
from . import model_list

ORIGINAL = ".before-carl"      # one copy of each config as it was before CARL first changed it
BACKUP = ".bak"                # one copy of the version before CARL's last change (replaced each time)
# Before 1.11.0 each change left FILE.bak.<YYYYMMDD-HHMMSS>; the setup removes those of CARL's files once.
STAMPED = re.compile(r"^(?P<base>.+)\.bak\.(?P<stamp>\d{8}-\d{6})$")
MANAGED = frozenset({"opencode.json", "tui.json", "models.json", "settings.json", "mcp.json", "APPEND_SYSTEM.md",
                     NAMES.state, "mtplx-request-policy.ts", ".zshrc", ".bashrc"})   # the files CARL writes or removes
MANIFEST = "shared/manifest.json"   # the shared files each plugin or extension folder gets (client/shared)
CLIENTS = sw.CLIENT_CHOICES
WEB_SEARCH = sw.WEB_SEARCH_VALUES
CODER_TESTS = sw.TESTS_VALUES        # the Tests setting (23.4.6; after by default since 23.5.0)
REQUEST_CHECK = sw.REQUEST_CHECK_VALUES   # the Request Check (23.4.3 addendum)
FREE_FORM = sw.FREE_FORM_VALUES      # the free-form brief (23.4.3 addendum)
CHROME_APP = "/Applications/Google Chrome.app"
# Google Chrome on Windows: chrome.exe under one of these folders (for all users, or this user only)
CHROME_WIN = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
CHROME_WIN_EXE = os.path.join("Google", "Chrome", "Application", "chrome.exe")
PLACEHOLDERS = ("__HOST__", "__LLAMA_PORT__", "__HOME__", "__HOST_LABEL__")   # in the bundle's opencode.json and models.json
# The host is substituted into JSON text and URLs: a hostname, IPv4 or IPv6 literal only.
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]*")
CODER_MODEL_RE = sw.CODER_MODEL_RE   # carl-sync.py's
LEVEL_RE = sw.LEVEL_RE


# ------------------------------------------------------------------ arguments
@dataclass(frozen=True)
class Options:
    """What the config step resolved: where the bundle and home are, the server, the switches."""
    bundle: str          # the client/ directory
    home: str
    host: str
    llama_port: str
    ctx: int             # the running model's window per slot (the server's /props)
    models: ModelList    # the installed models (installed-models.json)
    running: Optional[str]   # the model the server runs now (it gets ctx)
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
    oc_real: str = ""         # OpenCode's folder with its symlinks resolved (the Installer sets it)
    profile: bool = True      # append the pointer to ~/.zshrc / ~/.bashrc (NO_PROFILE=1: print it instead)
    clients: str = "both"     # both | opencode | pi: the configs to write (the other client's stay as they are)
    coder_model: str = ""     # an external model for the coder: PROVIDER/MODEL of the client ("": the main session's)
    coder_model_thinking: str = ""   # its variant (OpenCode) or thinking level (Pi); "": the model's own default
    coder_tests: str = "after"    # the Tests setting (23.4.6): after | before | off (the chain's test session)
    request_check: str = "reminder"   # the Request Check (23.4.3 addendum): reminder | on | off
    run_gate: bool = True     # the Coder Loop's run gate (23.4.3 addendum)
    fix_rounds: int = 1       # its fix rounds, 0-3
    free_form: str = "model"  # the free-form brief (23.4.3 addendum): model | on | off
    cache_api_url: str = ""      # the dashboard API as remote.json says (its real port); "" = port + 1
    carl_id: str = ""            # CARL's ID (remote.json "id"): the API must answer it before the key goes there

    @property
    def oc_dir(self) -> str:
        """OpenCode's folder; with oc_real set (the Installer: the folder with its symlinks resolved) that one.
        OpenCode resolves its own folder but not the "file:" paths in opencode.json, so a path through a link would
        point one folder off and load no plugin."""
        return self.oc_real or os.path.join(self.home, ".config/opencode")

    @property
    def pi_dir(self) -> str:
        return os.path.join(self.home, ".pi/agent")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.llama_port}/v1"

    @property
    def cache_api(self) -> str:
        """The dashboard's cache API (src/carl/adapters/http/dashboard_api_server.py): where remote.json says it runs, else the server's
        port + 1."""
        return self.cache_api_url or f"http://{self.host}:{int(self.llama_port) + 1}"


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


def coder_model_arg(v: str) -> str:
    """--coder-model: PROVIDER/MODEL, or "" / main (the coder on the main session's model)."""
    if v in ("", sw.CODER_MODEL_MAIN):
        return ""
    if not CODER_MODEL_RE.fullmatch(v) or len(v) > 200:
        raise argparse.ArgumentTypeError(f"not PROVIDER/MODEL: {v!r}")
    return v


def level_arg(v: str) -> str:
    if v in ("", sw.THINKING_DEFAULT):
        return ""
    if not LEVEL_RE.fullmatch(v):
        raise argparse.ArgumentTypeError(f"not a thinking level: {v!r}")
    return v


def switch_arg(v: str) -> bool:
    if v not in ("0", "1"):
        raise argparse.ArgumentTypeError(f"takes 0 or 1, got {v!r}")
    return v == "1"


def api_url_arg(v: str) -> str:
    """http://HOST:PORT (the dashboard API), or "" (port + 1)."""
    if v and not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]-]+:[0-9]{1,5}", v):
        raise argparse.ArgumentTypeError(f"takes http://HOST:PORT, got {v!r}")
    return v


def carl_id_arg(v: str) -> str:
    if v and not re.fullmatch(r"[0-9a-f]{32}", v):
        raise argparse.ArgumentTypeError(f"takes 32 hex digits, got {v!r}")
    return v


def parse_args(argv: List[str]) -> Options:
    ap = argparse.ArgumentParser(description="Merge the CARL client configs into OpenCode and Pi (run by the setup).")
    ap.add_argument("--bundle", required=True, help="the client/ directory")
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--host", required=True, type=host_arg)
    ap.add_argument("--llama-port", required=True, type=port_arg)
    ap.add_argument("--cache-api", default="", type=api_url_arg, help="the dashboard API (remote.json cache_api)")
    ap.add_argument("--carl-id", default="", type=carl_id_arg, help="CARL's ID (remote.json id)")
    ap.add_argument("--ctx", type=ctx_arg, required=True)
    ap.add_argument("--models", required=True, help="installed-models.json (carl.py client-models)")
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
    ap.add_argument("--coder-thinking", default="", help="/carl's Coder thinking on this computer: MODEL:VALUE,...")
    ap.add_argument("--coder-model", type=coder_model_arg, default="",
                    help="/carl's Coder model: an external PROVIDER/MODEL of the client for the coder (main: none)")
    ap.add_argument("--coder-model-thinking", type=level_arg, default="",
                    help="the thinking of that model: a variant (OpenCode) or a thinking level (Pi)")
    ap.add_argument("--coder-tests", choices=CODER_TESTS, default="after",
                    help="/carl's Tests: the coder's test session after the code session (the default), before it "
                         "(test-first: needs a full spec), or off")
    ap.add_argument("--request-check", choices=REQUEST_CHECK, default="reminder",
                    help="/carl's Request Check: the brief must carry the literals of the user's request")
    ap.add_argument("--run-gate", type=switch_arg, default=True, help="/carl's Coder Loop: the run gate")
    ap.add_argument("--fix-rounds", type=int, choices=(0, 1, 2, 3), default=1, help="/carl's Coder Loop: its fix rounds")
    ap.add_argument("--free-form", choices=FREE_FORM, default="model", help="/carl's Free-Form Brief")
    a = ap.parse_args(argv)
    try:
        models = model_list.load_list(a.models)
    except (OSError, ValueError) as e:
        ap.error(f"--models: {e}")
    models = only_running_with_overrides(models, a.running, a.coder_thinking)
    return Options(bundle=a.bundle, home=a.home, host=a.host, llama_port=a.llama_port, ctx=a.ctx, models=models,
                   running=a.running, coder=a.coder, sidebar=a.sidebar, switcher=a.switcher,
                   model_check=a.model_check, web_search=a.web_search, lsp=a.lsp, background=a.background,
                   browser=a.browser, browser_headed=a.browser_headed, profile=a.profile,
                   cache=a.cache, clients=a.clients, reminder=a.reminder, coder_model=a.coder_model,
                   coder_model_thinking=a.coder_model_thinking if a.coder_model else "", coder_tests=a.coder_tests,
                   request_check=a.request_check, run_gate=a.run_gate, fix_rounds=a.fix_rounds, free_form=a.free_form,
                   cache_api_url=a.cache_api, carl_id=a.carl_id)


def only_running_with_overrides(ml: ModelList, running: Optional[str], coder_thinking: str) -> ModelList:
    """Single-model mode (23.4.4): only the running model; then /carl's Coder thinking of this computer over the
    dashboard's (23.4.4)."""
    return coder_overrides(only_running(ml, running), parse_overrides(coder_thinking, ml.rules))


# ------------------------------------------------------------------ the helpers the benches call (plan 6.3)
def legacy() -> Legacy:
    from ..adapters import package_data
    return package_data.legacy()


def append_system_text(cur: str, rule: Optional[str]) -> str:
    """Pi's APPEND_SYSTEM.md with CARL's delegation rule as the last block (rule None: without it); the rule's
    earlier versions (either name) go, the user's text stays (merge_pi.append_system_text)."""
    return merge_pi.append_system_text(cur, rule, legacy().old)


def fill(doc: Any, values: Dict[str, str]) -> Any:
    """A parsed JSON document with the placeholders filled in its strings (after json.loads: a value with a
    backslash or a quote, a Windows home, stays a valid string)."""
    if isinstance(doc, str):
        for a, b in values.items():
            doc = doc.replace(a, b)
        return doc
    if isinstance(doc, dict):
        return {k: fill(v, values) for k, v in doc.items()}
    if isinstance(doc, list):
        return [fill(v, values) for v in doc]
    return doc


# ================================================================== the files
class ConfigError(Exception):
    """A user's config that CARL can't merge into (not plain JSON, not an object)."""


class ConfigFiles:
    """Reads and writes the clients' configs: JSON checked on load, a backup before every change."""

    def __init__(self, fs: Files, home: str, stamp: str, report: Report) -> None:
        self.fs = fs
        self.home = home
        self.stamp = stamp
        self.report = report
        self.written: Set[str] = set()             # files written this run (backed up once)

    def short(self, path: str) -> str:
        """The path for display (the home folder as ~, also with its links resolved)."""
        real = self.fs.realpath(self.home)
        return path.replace(real, "~").replace(self.home, "~") if real != self.home else path.replace(self.home, "~")

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
        current version (FILE.bak, replaced each time)."""
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
                fs.copy(path, path + BACKUP)
        link = fs.islink(path)
        fs.write(path, text)                       # through a symlink: the link stays, its target changes
        if not link:
            fs.chmod(path, mode)
        self.written.add(path)

    def remove_file(self, path: str) -> None:
        """Remove a file CARL installed, keeping a copy (FILE.bak, which the clients don't load)."""
        self.fs.copy(path, path + BACKUP)
        self.fs.remove(path)

    def clean_old_backups(self, folders: List[str], managed: FrozenSet[str] = MANAGED) -> int:
        """Once: CARL's old FILE.bak.<YYYYMMDD-HHMMSS> files (only of the files CARL manages, in its folders) go; the
        newest becomes FILE.bak when there is none. A file of the user's own (another name, another pattern) stays.
        The number removed."""
        fs, n = self.fs, 0
        for folder in folders:
            if not fs.isdir(folder):
                continue
            groups: Dict[str, List[Tuple[str, str]]] = {}
            for name in fs.listdir(folder):
                m = STAMPED.match(name)
                if m and m.group("base") in managed and fs.isfile(os.path.join(folder, name)):
                    groups.setdefault(m.group("base"), []).append((m.group("stamp"), name))
            for base, found in groups.items():
                found.sort()
                keep = os.path.join(folder, base + BACKUP)
                if not fs.exists(keep):
                    fs.copy(os.path.join(folder, found[-1][1]), keep)
                for _, name in found:
                    fs.remove(os.path.join(folder, name))
                    n += 1
        if n:
            self.report.add("removed", f"{plural_files(n)} of old backups (FILE.bak.<time>); CARL now keeps "
                                       f"FILE.before-carl and one FILE.bak")
        return n


# ================================================================== the installer
def chrome_installed(platform: str, environ: Mapping[str, str]) -> bool:
    """Google Chrome is on this computer: the app on macOS; chrome.exe in Program Files or the user's own programs on
    Windows. Linux: no (Playwright's Chromium, as before)."""
    if platform == "darwin":
        return os.path.isdir(CHROME_APP)
    if platform == "win32":
        env = {k.upper(): v for k, v in environ.items()}          # a copy of os.environ has its names in upper case
        return any(os.path.isfile(os.path.join(env[k.upper()], CHROME_WIN_EXE))
                   for k in CHROME_WIN if env.get(k.upper()))
    return False


def os_userenv(cf: ConfigFiles, fs: Files, home: str, platform: str = sys.platform) -> UserEnvironment:
    """The UserEnvironment adapter for this OS: HKCU\\Environment on Windows, the env file and the shell profiles on
    macOS and Linux."""
    if platform == "win32":
        from ..adapters.userenv_hkcu import HkcuEnvironment
        return HkcuEnvironment(cf, fs, home)
    from ..adapters.userenv_profile import ProfileEnvironment
    return ProfileEnvironment(cf, fs, home)


class Installer:
    """Writes the OpenCode and Pi configs for one set of Options. fs: the Files port; userenv: the UserEnvironment
    port (default: the env file and the shell profiles through fs); old: what earlier versions left (default: the
    package's data/legacy.json)."""

    def __init__(self, opts: Options, stamp: str, fs: Files, chrome: bool, userenv: Any = None,
                 old: Optional[Legacy] = None, platform: str = sys.platform, system_root: str = "") -> None:
        oc = os.path.join(opts.home, ".config/opencode")
        real = fs.realpath(oc)
        self.o = dataclasses.replace(opts, oc_real=real) if real != oc else opts      # "file:" paths without links
        opts = self.o
        self.fs = fs
        self.chrome = chrome                       # Google Chrome is installed (the browser agent drives it)
        self.windows = platform == "win32"         # the Windows forms: Pi's key, npx.cmd, the prompts' Windows lines
        self.system_root = system_root             # Windows' folder (SystemRoot): findstr for Pi's key
        self.stamp = stamp
        self.report = Report()
        self.cf = ConfigFiles(fs, opts.home, stamp, self.report)
        self.legacy = old or legacy()
        self.names: Names = self.legacy.old
        if userenv is None:
            userenv = os_userenv(self.cf, fs, opts.home, platform)
        self.userenv = userenv
        self._manifest: Optional[Dict[str, Tuple[str, ...]]] = None

    def short(self, path: str) -> str:
        return self.cf.short(path)

    def ours_oc(self, p: JsonObj) -> bool:
        return ours_oc(p, self.legacy)

    def ours_pi(self, p: JsonObj) -> bool:
        return ours_pi(p, self.legacy)

    @property
    def prompt_dirs(self) -> List[str]:
        """CARL's prompt folder in OpenCode under each of its names (now, before the rename)."""
        return [NAMES.prompt_dir, self.names.prompt_dir]

    def ours_agent(self, a: object) -> bool:
        return ours_agent(a, self.prompt_dirs)

    # -- the state file (what is ours), and its name before the rename
    def state_path(self, folder: str) -> str:
        """NAMES.state; while it doesn't exist yet, the one from before the rename."""
        new = os.path.join(folder, NAMES.state)
        return new if self.fs.exists(new) else os.path.join(folder, self.names.state)

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

    def clean_old_backups(self, folders: List[str]) -> int:
        return self.cf.clean_old_backups(folders, MANAGED | {self.names.state})

    def save_state(self, folder: str, client: str, st: JsonObj) -> None:
        """Write NAMES.state; the old-named one goes (a copy stays as FILE.bak)."""
        self.cf.save(os.path.join(folder, NAMES.state), st, backup=False)
        old = os.path.join(folder, self.names.state)
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

    def agent_text(self, rel: str) -> str:
        """An agent prompt of the bundle (agents/) with the lines for this OS."""
        from ..adapters import package_data
        return for_os(self.bundle_text(rel), rel, package_data.prompt_lines(), self.windows)

    def host_label(self) -> str:
        """The server's computer in the provider's name (data/words.json): Mac host."""
        from ..adapters import package_data
        return package_data.server_words()["host"]

    def bundle_json(self, rel: str) -> JsonObj:
        data = fill(json.loads(self.bundle_text(rel)),
                    dict(zip(PLACEHOLDERS, (self.o.host, self.o.llama_port, self.o.home, self.host_label()))))
        if not isinstance(data, dict):
            raise ValueError(f"{rel}: not a JSON object")
        return data

    def shared(self, folder: str) -> Tuple[str, ...]:
        """The shared files a plugin or extension folder of the bundle gets (shared/manifest.json)."""
        if self._manifest is None:
            doc = json.loads(self.bundle_text(MANIFEST))
            folders = doc.get("folders") if isinstance(doc, dict) else None
            if not isinstance(folders, dict):
                raise ValueError(f"{MANIFEST}: no \"folders\"")
            self._manifest = {k: tuple(v) for k, v in folders.items() if isinstance(v, list)}
        if folder not in self._manifest:
            raise ValueError(f"{MANIFEST}: {folder} is not listed")
        return self._manifest[folder]

    def install_folder(self, src: str, dest: str) -> None:
        """A plugin or extension folder from the bundle, with the shared files it imports (replaced whole)."""
        self.fs.rmtree(dest)
        self.fs.copytree(self.bundle_path(src), dest)
        for f in self.shared(src):
            self.fs.copy_into(self.bundle_path(f), dest)

    def browser_command(self) -> List[str]:
        return browser_command(self.o.browser_headed, self.chrome, self.windows)

    # ========================================================== OpenCode
    def opencode(self) -> Dict[str, str]:
        oc, rep, o = self.o.oc_dir, self.report, self.o
        path = os.path.join(oc, "opencode.json")
        cfg = self.cf.load(path)
        st = self.cf.load(self.state_path(oc))
        first_install = not st
        bundle = self.bundle_json("opencode/opencode.json")

        providers = cfg.setdefault("provider", {})
        had_ours_before = any(isinstance(v, dict) and self.ours_oc(v) for v in providers.values())
        legacy_install = first_install and had_ours_before
        ids = pick_ids(providers, st, self.ours_oc, self.legacy)
        new_id = ids[PROVIDER]
        prov = bundle["provider"][PROVIDER]
        prov["models"] = opencode_models(o.models, o.running, o.ctx)
        if new_id != PROVIDER:
            prov["name"] = prov["name"] + f" [{ALT}]"
        renamed = merge_provider(providers, st, prov, new_id, self.ours_oc, "OpenCode", rep, self.legacy)
        merge_oc_default_model(cfg, st, default_model(o.models, o.running), new_id, legacy_install, renamed, rep,
                               self.legacy.old_defaults)
        agent = cfg.setdefault("agent", {})
        undo_title_disable(agent, st, legacy_install, rep)

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

        st.update({"providers": ids, "base_url": o.base_url, "updated": self.stamp, "models": models_state(o.models),
                   "coder_model": o.coder_model or "main", "coder_tests": o.coder_tests,
                   "request_check": o.request_check,
                   "run_gate": o.run_gate, "fix_rounds": o.fix_rounds, "free_form": o.free_form,
                   "cache_api": o.cache_api})          # the /carl panel's speeds (Phase 23.5 item 9)
        if o.carl_id:
            st["carl_id"] = o.carl_id                  # the API must answer it before the key goes there
        else:
            st.pop("carl_id", None)
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

    def _oc_old_plugin(self, plist: List[Any], name: str, wanted: bool) -> None:
        """A CARL plugin folder of an install from before 1.20.2 in ~/.config/opencode/plugins/ (OpenCode 2 loads every
        folder there): its entries out of the list, the folder removed when it is CARL's (listed, or its id), else kept."""
        old = os.path.join(self.o.oc_dir, "plugins", name)
        entry = "file:" + old
        real = self.fs.realpath
        gone = [x for x in plist if same_path(x, entry, real)
                or (isinstance(x, list) and x and same_path(x[0], entry, real))]
        for x in gone:
            plist.remove(x)
        if self.fs.isdir(old):
            # CARL's: listed as ours, or its id (a folder of that name that is not ours stays)
            if gone or f'id: "{name}"' in (self.fs.read(os.path.join(old, "index.js")) or ""):
                self.fs.rmtree(old)
                if wanted:
                    self.report.add("updated", f"OpenCode plugin {name} (moved to {OC_PLUGIN_DIR}/)")
                else:
                    self.report.add("removed", f"OpenCode plugin {name}")
            else:
                self.report.add("kept", f"{self.short(old)} (not ours)")

    def _oc_server_plugin(self, cfg: JsonObj, name: str, want_it: bool, provider_id: str, what: str,
                          extra: Optional[JsonObj] = None) -> None:
        """One of CARL's OpenCode server plugins: copied into carl-plugins/ (OC_PLUGIN_DIR), one entry in the plugin
        list with our provider id and its other options (an entry of ours is replaced, other entries stay); out when
        not wanted."""
        plist = cfg.get("plugin")
        if isinstance(plist, list):
            self._oc_old_plugin(plist, name, want_it)
        dest = os.path.join(self.o.oc_dir, OC_PLUGIN_DIR, name)
        entry = "file:" + dest
        if not want_it:
            if merge_plugin_entry(cfg, entry, None, self.fs.isdir(dest), name, self.report, self.fs.realpath):
                self.fs.rmtree(dest)
            return
        self.install_folder(f"opencode/plugins/{name}", dest)        # a manifest key: "/" on every OS
        want = [entry, {"provider": provider_id,
                        **({"cacheApi": self.o.cache_api} if name == CACHE else {}),
                        **({"carlId": self.o.carl_id} if name == CACHE and self.o.carl_id else {}),
                        **(extra or {})}]
        merge_plugin_entry(cfg, entry, want, True, f"{name} ({what})", self.report, self.fs.realpath)

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
        mine = {n for n in CODER_NAMES if n in agent and self.ours_agent(agent[n])}
        plan = coder_plan(mine, CODER in agent, CODER_ALT in agent, self.o.coder)
        report_coder_plan(plan, "OpenCode", rep)
        for old_name in plan.remove:
            agent.pop(old_name)
        rule_path = os.path.join(ddir, "delegation.md")
        name = plan.name
        if not plan.install:
            merge_oc_coder(cfg, st, agent, name, None, rule_path, rep, real=self.fs.realpath)
            self.fs.rmtree(ddir)
            self._oc_server_plugin(cfg, DELEGATION, False, provider_id, "")
            self._code_command(os.path.join(oc, "command", "code.md"), "", name, "OpenCode")
            return
        body, desc = split_agent(self.agent_text("agents/coder.md"))
        prompt_path = os.path.join(ddir, "coder.md")
        self.fs.makedirs(ddir)
        rule = delegation_for(self.agent_text("agents/delegation.md"), "opencode", self.o.background, self.o.browser)
        for p, t, what in ((prompt_path, name_agent(body, name), "coder prompt"),
                           (rule_path, oc_rule_text(name_agent(rule, name)), "delegation rule")):
            old = self.fs.read(p)
            if old is not None and old != t:
                rep.add("updated", f"OpenCode {what} ({self.short(p)})")
            self.fs.write(p, t)
        ext = self.o.coder_model
        merge_oc_coder(cfg, st, agent, name, AgentText(desc, prompt_path), rule_path, rep, ext, real=self.fs.realpath)
        # the coder's thinking per model (23.4.4): set on the coder's requests by the plugin's chat.params hook (none
        # when every model's coder thinks as the main session); an external coder model (23.4.5): its variant
        thinking = coder_efforts(self.o.models, provider_id)
        self._oc_server_plugin(cfg, DELEGATION, True, provider_id, "the hand-off to the coder",
                               {"reminder": self.o.reminder, "cacheApi": self.o.cache_api, "coder": name,
                                **({"carlId": self.o.carl_id} if self.o.carl_id else {}),
                                "stateFile": os.path.join(oc, NAMES.state),
                                **({"coderThinking": thinking} if thinking else {}),
                                **({"coderModel": ext} if ext else {}),
                                **({"coderVariant": self.o.coder_model_thinking} if ext and self.o.coder_model_thinking
                                   else {})})
        # (the new-file gate is a setting of the dashboard: carl-delegation reads it through cacheApi, Phase 23.4)
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
        old_dir = os.path.join(self.o.oc_dir, self.names.prompt_dir)
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
        """OpenCode's tool switches: the env file and its profile block (the UserEnvironment port), and "lsp" with
        --lsp 1."""
        o = self.o
        self.userenv.apply(env_file_text(o.web_search, o.background, o.lsp),
                           f"web search {o.web_search}, background subagents {'on' if o.background else 'off'}, "
                           f"lsp {'on' if o.lsp else 'off'}", o.profile, self.report)
        merge_lsp(cfg, st, o.lsp, self.report)

    def _oc_browser(self, cfg: JsonObj, st: JsonObj, agent: JsonObj) -> None:
        """The browser MCP server (its tools off globally), and the subagent that has them."""
        server: Optional[JsonObj] = None
        text: Optional[AgentText] = None
        body = ""
        ddir = os.path.join(self.o.oc_dir, NAMES.prompt_dir)
        prompt_path = os.path.join(ddir, "browser.md")
        if self.o.browser:
            server = {"type": "local", "command": self.browser_command(), "enabled": True}
            body, desc = split_agent(self.agent_text("agents/browser.md"))
            text = AgentText(desc, prompt_path)
        name = merge_oc_browser(cfg, st, agent, server, text, self.report, self.prompt_dirs)
        if name is not None:
            self.fs.makedirs(ddir)
            self.fs.write(prompt_path,
                          body if name == BROWSER_AGENT else body.replace("You are **browser**", f"You are **{name}**"))

    def _oc_tui(self) -> None:
        """TUI plugins: subagents sidebar, session switcher (in the prompt box), the /carl panel;
        each with the shared files the manifest lists for it."""
        oc = self.o.oc_dir
        tui_path = os.path.join(oc, "tui.json")
        tui = self.cf.load(tui_path)
        tplug = tui.setdefault("plugin", [])
        for name, label, want in ((SIDEBAR, "OpenCode subagents sidebar", self.o.sidebar),
                                  (SWITCHER, "OpenCode session switcher", self.o.switcher),
                                  (PANEL, "OpenCode /carl panel", True)):
            self._oc_old_plugin(tplug, name, want)
            dest = os.path.join(oc, OC_PLUGIN_DIR, name)
            if want:
                self.install_folder(f"opencode/plugins/{name}", dest)        # a manifest key: "/" on every OS
            if merge_tui_plugin(tplug, "file:" + dest, want, label, self.report, self.fs.realpath):
                self.fs.rmtree(dest)
        if not tplug:
            tui.pop("plugin")
        if tui or self.fs.exists(tui_path):
            self.cf.save(tui_path, tui)

    # ========================================================== Pi
    def pi(self) -> Dict[str, str]:
        pi, rep, o = self.o.pi_dir, self.report, self.o
        path = os.path.join(pi, "models.json")
        cfg = self.cf.load(path)
        st = self.cf.load(self.state_path(pi))
        first_install = not st
        bundle = self.bundle_json("pi/models.json")

        providers = cfg.setdefault("providers", {})
        had_ours_before = any(isinstance(v, dict) and self.ours_pi(v) for v in providers.values())
        ids = pick_ids(providers, st, self.ours_pi, self.legacy)
        new_id = ids[PROVIDER]
        prov = bundle["providers"][PROVIDER]
        if self.windows:                                   # cmd.exe has no cat (merge_pi)
            prov["apiKey"] = windows_key_ref(o.home, self.system_root)
        prov["models"] = pi_models(o.models, o.running, o.ctx)
        renamed = merge_provider(providers, st, prov, new_id, self.ours_pi, "Pi", rep, self.legacy)
        self.cf.save(path, cfg)

        sp = os.path.join(pi, "settings.json")
        sett = self.cf.load(sp)
        write = merge_pi_defaults(sett, st, new_id, default_model(o.models, o.running), first_install and had_ours_before,
                                  renamed, rep, self.names, o.models.rules.pi_default_level)
        write = merge_pi_model_thinking(sett, st, new_id, o.models, rep) or write
        if merge_pi_default_tools(sett, st, rep) or write:
            self.cf.save(sp, sett)
        mp = os.path.join(pi, "mcp.json")
        mcp = self.cf.load(mp)
        servers = mcp.setdefault("mcpServers", {})
        write = merge_pi_web_search(servers, st, o.web_search, rep)
        if merge_pi_browser(servers, st, self.browser_command() if o.browser else None, rep) or write:
            self.cf.save(mp, mcp)

        self._pi_extensions_and_coder(st)
        self._pi_ext(CACHE, o.cache, "the disk cache", st, "cache_ext")
        self._pi_ext(MODEL_CHECK, o.model_check, "model warnings", st, "model_check_ext")
        self._pi_ext(PANEL, True, "the /carl panel", st, "panel_ext")
        coder_on = "coder_agent" in st
        self._code_command(os.path.join(o.pi_dir, "prompts", "code.md"), "pi/prompts/code.md" if coder_on else "",
                           str(st.get("coder_agent") or CODER), "Pi")
        self._pi_ext(DELEGATION, coder_on, "the hand-off to the coder", st, "delegation_ext")
        if coder_on:
            st["delegation"] = {"reminder": o.reminder}            # the gate: the dashboard's setting (23.4)
            coder_thinking = pi_coder_thinking(new_id, o.models)   # the coder's thinking per model (23.4.4)
            if o.coder_model and o.coder_model_thinking:            # an external coder model's level (23.4.5)
                coder_thinking["coder"][o.coder_model] = o.coder_model_thinking
            if coder_thinking["coder"]:
                st["thinking"] = coder_thinking
            else:                                               # "main" everywhere: the session's level
                st.pop("thinking", None)
        else:
            st.pop("delegation", None)
            st.pop("thinking", None)
        st["cache_api"] = o.cache_api      # the cache, the delegation gate and the /carl panel's speeds (Phase 23.5)

        # the coder's model (23.4.5): an external one the subagent extension passes as --model, else "main"
        st.update({"providers": ids, "base_url": o.base_url, "updated": self.stamp, "models": models_state(o.models),
                   "coder_model": o.coder_model or "main", "coder_tests": o.coder_tests,
                   "request_check": o.request_check,
                   "run_gate": o.run_gate, "fix_rounds": o.fix_rounds, "free_form": o.free_form})
        st.pop("model_check", None)                    # 23.5 before its own extension: the extension says it now
        if o.carl_id:
            st["carl_id"] = o.carl_id                  # the API must answer it before the key goes there
        else:
            st.pop("carl_id", None)
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
        sub_ours = ((fs.isdir(sub) and any(n.ext_marker in sub_index for n in (NAMES, self.names)))
                    if sub_index is not None else not fs.exists(sub))
        agents_dir = os.path.join(pi, "agents")
        texts = {n: fs.read(os.path.join(agents_dir, f"{n}.md")) for n in CODER_NAMES}
        mine = {n for n, t in texts.items() if t is not None and CODER_SIG in t}
        plan = coder_plan(mine, texts[CODER] is not None, texts[CODER_ALT] is not None, self.o.coder)
        report_coder_plan(plan, "Pi", rep)
        for old_name in plan.remove:
            self.cf.remove_file(os.path.join(agents_dir, f"{old_name}.md"))
        name = plan.name
        rule: Optional[str] = None
        if plan.install:
            if sub_ours:
                self.install_folder("pi/extensions/subagent", sub)   # the chain (Phase 23.4.3)
                st["subagent_ext"] = True
            else:
                rep.add("kept", "Pi extensions/subagent (yours; it provides the subagent tool ours would)")
            fs.makedirs(agents_dir)
            existing = texts[name]
            new_text = name_agent(self.agent_text("agents/coder.md"), name)
            if existing != new_text:
                rep.add("updated" if existing is not None else "added", f"Pi agent '{name}' + delegation rule")
            fs.write(os.path.join(agents_dir, f"{name}.md"), new_text)
            rule = name_agent(delegation_for(self.agent_text("agents/delegation.md"), "pi", self.o.background,
                                             self.o.browser).strip(), name)
            st["coder_agent"] = name
            st["background_subagents"] = self.o.background     # the subagent extension reads it
        else:
            if sub_ours and fs.isdir(sub):
                fs.rmtree(sub, quiet=False)
            for k in ("coder_agent", "subagent_ext", "background_subagents"):
                st.pop(k, None)
        asp = os.path.join(pi, "APPEND_SYSTEM.md")
        text = merge_pi.append_system_text(fs.read(asp) or "", rule, self.names)
        if text or fs.exists(asp):
            self.cf.save_text(asp, text, mode=0o644)

    def _pi_ext(self, name: str, want: bool, what: str, st: JsonObj, key: str) -> None:
        """One of CARL's Pi extensions (extensions/NAME, with the shared files the manifest lists for it); a folder of
        that name that isn't ours stays."""
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
        src = f"pi/extensions/{name}"
        files = {"index.ts": f"{src}/index.ts", **{os.path.basename(f): f for f in self.shared(src)}}
        changed = any(fs.read(os.path.join(dest, n)) != fs.read(self.bundle_path(s)) for n, s in files.items())
        self.install_folder(src, dest)
        if changed:
            self.report.add("updated" if index is not None else "added", f"Pi extension {name} ({what})")
        st[key] = True


def main(argv: List[str]) -> int:
    import shutil                                  # here, not at import: shutil needs the OS's posix or nt module

    from ..adapters.files_local import LocalFiles
    opts = parse_args(argv)
    inst = Installer(opts, time.strftime("%Y%m%d-%H%M%S"), LocalFiles(), chrome_installed(sys.platform, os.environ),
                     platform=sys.platform, system_root=os.environ.get("SystemRoot", ""))
    try:
        inst.check_configs()
        inst.clean_old_backups([inst.o.oc_dir, opts.pi_dir, os.path.join(opts.pi_dir, "extensions"), opts.home])
        oc_ids = inst.opencode() if opts.clients != "pi" else None
        pi_ids = inst.pi() if opts.clients != "opencode" else None
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for line in inst.report.lines(shutil.get_terminal_size((80, 24)).columns):
        print(line)
    if oc_ids is not None:
        print(f"OpenCode: {inst.short(opts.oc_dir)}/opencode.json, CARL provider {', '.join(oc_ids.values())}")
    if pi_ids is not None:
        print(f"Pi:       {inst.short(opts.pi_dir)}/models.json, CARL provider {', '.join(pi_ids.values())}")
    return 0

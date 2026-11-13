"""CARL client setup: the one entry point on every computer (no sudo). ./setup, setup.command and setup.cmd run it
through carl_setup.py; on the server's computer `carl install` runs ./setup. It was the bash client/setup (with
client/install-clients.sh) until Phase W; its decisions are the same, and its texts too, except that they name the
setup's command of the OS (`./setup install`, `./setup clients`; setup.cmd on Windows) where they named the two scripts
(W9.4, A65):

- It asks one time which clients (OpenCode, Pi) and which options (the coder, the browser, web search, LSP). The
  choices: an option > the environment > the last setup (~/.config/carl/client-install.env, read, never run) >
  CARL's defaults. --yes, or no terminal, takes them without a question.
- It installs or updates OpenCode and Pi when they are missing or older than the versions CARL is tested with
  (client-versions.env), from npm into ~/.local; a newer one of the same major version stays, and OpenCode 2 (another
  plugin system) is named and left alone. Pi needs Node 22.19 or newer: without one, Node 22 LTS comes from
  nodejs.org (its SHA-256 checked) into ~/.local/lib/nodejs.
- The programs' folder is ~/.local/bin, and ~/.local itself on Windows (npm's Windows layout: npm puts opencode.cmd
  and pi.cmd in its prefix). The shell profiles get it on PATH on macOS and Linux; on Windows the user's Path
  (HKCU\\Environment) does.
- Then the config step (carl_client.app.install, in this process) writes the configs, and the setup says what it did
  and whether the server answers.

Running it again is safe: an update is a new package unzipped over this folder, then ./setup.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import IO, Callable, Dict, List, Mapping, MutableMapping, Optional, Tuple

from ..domain import switches as sw
from ..domain.remote import KEY_FILE, REMOTE_FILE
from ..domain.words import COMMAND
from ..ports import NodeDownload, Programs
from . import install
from .install import Stop, say_text, wrap_help

NPM_PKG_OPENCODE = "opencode-ai"
NPM_PKG_PI = "@earendil-works/pi-coding-agent"
VERSIONS_FILE = "client-versions.env"
NODE_MIN = "22.19.0"
NODE_LINE = "latest-v22.x"
# The package's files: the private ones get 0600, the scripts u+x, first (a copy can lose the modes).
PRIVATE = (KEY_FILE, REMOTE_FILE)
SCRIPTS = ("setup", "setup.command", "carl-sync.py", "carl_setup.py")
PATH_LINE = 'export PATH="$HOME/.local/bin:$PATH"  # carl-vm-client'
PATH_MARK = re.compile(r"# (carl|mtplx)-vm-client")
# The switches the setup does not ask for: from the environment, else as in the last setup.
CARRIED = (sw.BROWSER_HEADED, sw.NO_SIDEBAR, sw.NO_SWITCHER, sw.NO_MODEL_CHECK, sw.NO_BACKGROUND_SUBAGENTS, sw.NO_CACHE,
           sw.LLAMA_CTX, sw.NO_REMINDER, sw.CODER_THINKING, sw.CODER_MODEL, sw.CODER_MODEL_THINKING)
SMOKE = re.compile(r"^(OK: the server answers|Note: no server runs at)")
BACKUP = re.compile(r"^  backed up ")


# ------------------------------------------------------------------ the versions and the answers (pure)
def _order(c: str) -> int:
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return -1 if c == "~" else ord(c) + 256


def version_cmp(a: str, b: str) -> int:
    """`sort -V`'s order of two versions: < 0, 0 or > 0 (digit runs as numbers)."""
    i = j = 0
    while i < len(a) or j < len(b):
        diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) else 0
            bc = _order(b[j]) if j < len(b) else 0
            if ac != bc:
                return ac - bc
            i, j = i + 1, j + 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            diff = diff or ord(a[i]) - ord(b[j])
            i, j = i + 1, j + 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if diff:
            return diff
    return (a > b) - (a < b)


def older(a: str, b: str) -> bool:
    """a is an older version than b."""
    return a != b and version_cmp(a, b) <= 0


def at_least(a: str, b: str) -> bool:
    """a is version b or newer."""
    return version_cmp(b, a) <= 0


def action(installing: bool, found: bool, have: str, tested: str, major: str) -> str:
    """What the setup does with a client: skip (--no-install), install (missing), unknown (no version), major (another
    major version: OpenCode 2 is another plugin system), update (older than tested), newer, or keep."""
    if not installing:
        return "skip"
    if not found:
        return "install"
    if not have or not tested:
        return "unknown"
    if major and have.split(".", 1)[0] != major:
        return "major"
    if older(have, tested):
        return "update"
    return "newer" if have != tested else "keep"


KINDS: Dict[str, Tuple[Tuple[Tuple[str, ...], str], ...]] = {
    "clients": ((("both", "b", "all"), "both"), (("opencode", "o", "oc"), "opencode"), (("pi", "p"), "pi")),
    "coder": ((("auto", "a"), "auto"), (("on", "y", "yes", "1"), "on"), (("off", "n", "no", "0"), "off")),
    "onoff": ((("on", "y", "yes", "1"), "on"), (("off", "n", "no", "0"), "off")),
    "web": ((("exa", "e"), "exa"), (("parallel", "p"), "parallel"), (("off", "n", "no", "0"), "off")),
}


def normalize(word: str, kind: str) -> str:
    """The answer as one of the values of KIND, or "" when it is not one (case and spaces do not count)."""
    a = "".join(word.lower().split())
    for words, value in KINDS[kind]:
        if a in words:
            return value
    return ""


def label(which: str) -> str:
    return {"pi": "Pi", "opencode": "OpenCode"}.get(which, "OpenCode and Pi")


def read_versions(path: str) -> Dict[str, str]:
    """client-versions.env's KEY="VALUE" lines (read, never run)."""
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.fullmatch(r'\s*([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|([^\s#]*))\s*(?:#.*)?', line)
        if m:
            out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


# ------------------------------------------------------------------ the help
def help_text(port: int, vm_host: str, setup: str, server: str) -> str:
    return f"""CARL client setup: install OpenCode and Pi, and connect them to the CARL server.

Usage: {setup} [OPTION...]

On another computer, unzip the client package from {server} ({COMMAND} package), then run {setup} in its folder. On a Mac, a double click on setup.command does the same. On {server}, {COMMAND} install runs this setup.

The setup asks one time which clients and which options you want. Press Enter to keep the value in brackets. Then it installs OpenCode and Pi when they are missing or older than the versions CARL is tested with (client-versions.env; into ~/.local, no sudo), and writes their configs. It keeps your own settings and makes a backup of each file before it changes it. At the end, it tells you what it did and if the server answers.

Options:
  -y, --yes\\tTake the defaults. Ask no questions. Without a terminal, the setup does this too.
  --clients WHICH\\tboth (the default), opencode or pi. "{setup} pi" is the same as "--clients pi".
  --coder auto|on|off\\tThe coder subagent: the main agent gives large tasks to it. auto (the default): on when the server runs 2 or more slots, or when the coder runs on an external model (/carl > Coder subagent > Coder model).
  --browser on|off\\tThe browser tools: an agent opens and checks web pages. The default is on.
  --web-search WHICH\\texa (the default), parallel or off. The search queries leave this computer: they go to Exa or Parallel.
  --lsp on|off\\tLSP in OpenCode: OpenCode downloads and runs language servers for your code. The default is on.
  --no-install\\tDo not install or update OpenCode and Pi. Write only the configs. --config-only does the same.
  --no-config\\tInstall or update OpenCode and Pi. Do not write the configs. --clients-only does the same.
  -h, --help\\tShow this help.

The defaults are the choices of your last setup on this computer (~/.config/carl/client-install.env). The sync service uses the same choices when it applies a new config from the dashboard. The environment variables of the config step also work, for example WEB_SEARCH=off.

Where the server is (only when you need it):
  (no option)\\tThe server in remote.json in this folder. The package has it, and the server writes it at each start.
  --local\\tThe server runs on this computer.
  --vm [HOST]\\tThe server runs on the host of this VM. The default HOST is {vm_host}.
  --host ADDRESS\\tThe server is at ADDRESS.
  --port N\\tThe port of the server. The default is {port}.
  --key-file FILE\\tRead the API key from FILE. The package has the key in api-key.

To update: make a new package on {server}, unzip it over this folder (unzip -o), and run {setup} again. To turn a part off, run {setup} again and answer off. {setup} install --help lists more switches.

Internals: setup runs {setup} clients (OpenCode and Pi) and {setup} install (the configs) in this folder. You do not need to run them yourself.
"""


def clients_help(setup: str, server: str) -> str:
    return f"""CARL: install the coding agents OpenCode and Pi for this user.

Run {setup} instead. It installs OpenCode and Pi when they are missing or older (with this script), then writes their configs. This script is an internal part of the setup.

Usage: {setup} clients [both | opencode | pi]

  both\\tInstall OpenCode and Pi. This is the default.
  opencode\\tInstall only OpenCode.
  pi\\tInstall only Pi.
  -h, --help\\tShow this help.

The script works on a Mac and in a Linux VM. It needs no sudo: everything goes into ~/.local.

Both agents come from npm, at the versions CARL is tested with (client-versions.env). Pi needs Node 22.19 or newer. If Node is missing or older, the script downloads Node 22 LTS from nodejs.org into ~/.local/lib/nodejs. It checks the SHA-256 sum of the download.

The script adds ~/.local/bin to PATH in ~/.zshrc and ~/.bashrc, if these files exist. It makes a backup of each file first.

Then the setup writes the configs ({setup} install). It connects OpenCode and Pi to the CARL server. On {server}, {COMMAND} install runs the setup.
"""


# ------------------------------------------------------------------ the parts
@dataclass
class Parts:
    """What the setup uses of the computer: its programs (adapters/npm.py), Node's download (adapters/node_dist.py)
    and the config step (app/install.py). Tests pass fakes."""
    here: str
    programs: Callable[[Mapping[str, str], IO[str], IO[str]], Programs]
    node: Callable[[], NodeDownload]
    step: Callable[..., int]
    platform: str = sys.platform
    # Windows: the folder onto the user's Path (adapters/userenv_hkcu.py); True when it was added, False when it was
    # there already
    user_path: Optional[Callable[[str], bool]] = None


def build(here: str, environ: Mapping[str, str]) -> Parts:
    """This computer's parts for the client folder `here`."""
    from ..adapters.node_dist import NodeDist
    from ..adapters.npm import Programs
    user_path = None
    if sys.platform == "win32":
        from ..adapters.userenv_hkcu import add_to_user_path
        user_path = add_to_user_path
    return Parts(here, Programs, lambda: NodeDist(NODE_LINE), install.step, sys.platform, user_path)


def bin_dir(prefix: str, platform: str) -> str:
    """Where npm -g --prefix PREFIX puts the programs: PREFIX/bin, and PREFIX itself on Windows."""
    return prefix if platform == "win32" else os.path.join(prefix, "bin")


class Setup:
    """One run of the setup: the environment it changes for its parts, the client's folder, the streams."""

    def __init__(self, environ: MutableMapping[str, str], here: str, parts: Parts, out: IO[str], err: IO[str],
                 stdin: IO[str]) -> None:
        self.env: Dict[str, str] = dict(environ)
        self.here = here
        self.parts = parts
        self.out = out
        self.err = err
        self.stdin = stdin
        self.home = self.env.get("HOME") or os.path.expanduser("~")
        self.last_file = os.path.join(self.home, ".config", "carl", "client-install.env")
        self.versions = read_versions(os.path.join(here, VERSIONS_FILE))
        self.width = install.out_width(self.env, out)

    def say(self, text: str, err: bool = False) -> None:
        (self.err if err else self.out).write(say_text(text, self.width))

    def echo(self, text: str = "") -> None:
        self.out.write(text + "\n")

    def fail(self, text: str, code: int = 2) -> Stop:
        self.err.write(text + "\n")
        return Stop(code)

    # -- the last setup's switches
    def last(self, key: str) -> str:
        """KEY's value in the last setup's switches ("" when none); the file is read, never run."""
        try:
            with open(self.last_file, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return ""
        vals = [ln[len(key) + 1:] for ln in lines if ln.startswith(key + "=")]
        return vals[-1] if vals else ""

    def source(self, *keys: str) -> Callable[[str], str]:
        """The environment's values when it sets one of KEYS, else the last setup's."""
        if any(self.env.get(k) for k in keys):
            return lambda k: self.env.get(k, "")
        return self.last

    # -- the run
    def run(self, argv: List[str]) -> int:
        try:
            return self._run(argv)
        except Stop as s:
            return s.code

    def _run(self, argv: List[str]) -> int:
        self.options(argv)
        self.fix_modes()
        self.title()
        self.choices()
        self.questions()
        c = self.c
        self.echo()
        lsp = f", LSP {c['lsp']}" if c["clients"] != "pi" else ""
        self.say(f"Clients: {c['clients']}. Options: coder {c['coder']}, browser {c['browser']}, web search "
                 f"{c['web']}{lsp}.")
        if c["web"] != "off":
            self.say(f"Web search sends the search queries to {c['web']}, outside this computer. Everything else stays "
                     f"between this computer and the server.")
        self.env["PATH"] = (bin_dir(os.path.join(self.home, ".local"), self.parts.platform) + os.pathsep
                            + self.env.get("PATH", ""))
        report = self.clients()
        smoke, backups = "", 0
        if self.do_config:
            smoke, backups = self.configs()
        self.echo()
        self.say("CARL setup is done.")
        for r in report:
            self.say(f"  {r}")
        if self.do_config:
            more = (". Backups of the changed files: FILE.bak next to each file (the original: FILE.before-carl)"
                    if backups else "")
            self.say(f"  Configs: written{more}.")
            self.say(f"  Smoke test: {smoke or 'no result (see the output above)'}")
        else:
            self.say("  Configs: not changed (--no-config).")
        if self.do_config and not smoke.startswith("OK:"):
            self.say(f"Start the server on {install.defaults().server} ({COMMAND}), then try again: the clients work "
                     f"when the server runs.")
        nxt = {"pi": "pi", "both": "opencode (or: pi)"}.get(c["clients"], "opencode")
        self.say(f"Next: open a new terminal. Go to your project folder and run: {nxt}")
        return 0

    def options(self, argv: List[str]) -> None:
        self.yes, self.do_install, self.do_config = False, True, True
        self.net: List[str] = []
        self.o: Dict[str, str] = {}
        i = 0
        while i < len(argv):
            a = argv[i]
            nxt = argv[i + 1] if i + 1 < len(argv) else ""
            if a in ("-h", "--help", "help"):
                d = install.defaults()
                setup = install.setup_command(self.parts.platform)
                self.out.write(wrap_help(help_text(d.port, d.vm_host, setup, d.server), self.width))
                raise Stop(0)
            if a in ("-y", "--yes"):
                self.yes = True
            elif a in sw.CLIENT_CHOICES:
                self.o["clients"] = a
            elif a in ("--clients", "--coder", "--browser", "--web-search", "--lsp"):
                self.need(a, nxt)
                self.o[{"--web-search": "web"}.get(a, a[2:])] = nxt
                i += 1
            elif a in ("--no-install", "--config-only"):
                self.do_install = False
            elif a in ("--no-config", "--clients-only"):
                self.do_config = False
            elif a == "--local":
                self.net.append(a)
            elif a == "--vm":
                self.net.append(a)
                if install.VM_ARG_RE.match(nxt):
                    self.net.append(nxt)
                    i += 1
            elif a in ("--host", "--port", "--key-file", "--key"):
                self.need(a, nxt)
                self.net += [a, nxt]
                i += 1
            else:
                raise self.fail(f"error: setup does not know '{a}'. Help: ./setup --help")
            i += 1
        if not (self.do_install or self.do_config):
            raise self.fail("error: --no-install and --no-config together leave nothing to do.")

    def need(self, opt: str, value: str) -> None:
        if not value:
            raise self.fail(f"error: {opt} needs a value. Help: ./setup --help")

    def fix_modes(self) -> None:
        """The package's files: their modes first (a copy can lose them)."""
        for f, private in [(f, True) for f in PRIVATE] + [(f, False) for f in SCRIPTS]:
            p = os.path.join(self.here, f)
            if not os.path.isfile(p):
                continue
            try:
                os.chmod(p, 0o600 if private else os.stat(p).st_mode & 0o7777 | 0o100)
            except OSError as e:
                raise self.fail(f"chmod: {p}: {e.strerror or e}", 1) from None

    def title(self) -> None:
        version = ""
        vfile = os.path.join(self.here, "VERSION")
        if os.path.isfile(vfile):
            version = install.read_squeezed(vfile)
        server = server_version = ""
        remote = os.path.join(self.here, REMOTE_FILE)
        if install.nonempty(remote):
            server = f"{json_field(remote, 'host')}:{json_field(remote, 'port')}"
            server_version = json_field(remote, "version")
        self.say(f"CARL client setup{f' (client package {version})' if version else ''}"
                 f"{f'. The server: {server}' if server else ''}.")
        if version and server_version and version != server_version:
            self.say(f"Note: this package is version {version}, but the server ran CARL {server_version} when it wrote "
                     f"remote.json.")

    def choices(self) -> None:
        """options > environment > the last setup > CARL's defaults."""
        get = self.source(sw.CLIENTS)
        clients = get(sw.CLIENTS) or sw.CLIENT_CHOICES[0]
        get = self.source(sw.CODER, sw.NO_CODER)
        if get(sw.NO_CODER) == "1" or get(sw.CODER) == "0":
            coder = "off"
        else:
            coder = "on" if get(sw.CODER) == "1" else "auto"
        browser = "off" if self.source(sw.NO_BROWSER)(sw.NO_BROWSER) == "1" else "on"
        web = self.source(sw.WEB_SEARCH)(sw.WEB_SEARCH) or sw.DEFAULTS[sw.WEB_SEARCH]
        get = self.source(sw.NO_LSP, sw.LSP)
        lsp = "off" if get(sw.NO_LSP) == "1" or get(sw.LSP) == "0" else "on"
        for value, kind, name in ((clients, "clients", sw.CLIENTS), (web, "web", sw.WEB_SEARCH)):  # a bad environment
            if not normalize(value, kind):
                raise self.fail(f"error: {name} does not take '{value}'.")
        self.c = {"clients": normalize(clients, "clients"), "coder": coder, "browser": browser,
                  "web": normalize(web, "web"), "lsp": lsp}
        for key, kind, opt in (("clients", "clients", "--clients"), ("coder", "coder", "--coder"),
                               ("browser", "onoff", "--browser"), ("web", "web", "--web-search"),
                               ("lsp", "onoff", "--lsp")):
            if self.o.get(key):
                v = normalize(self.o[key], kind)
                if not v:
                    raise self.fail(f"error: {opt} does not take '{self.o[key]}'. Help: ./setup --help")
                self.c[key] = v

    def questions(self) -> None:
        if self.yes or not install._isatty(self.stdin) or not (self.do_config or not self.o.get("clients")):
            return
        self.echo()
        self.say("CARL asks some questions one time. Press Enter to keep the value in brackets.")
        if not self.o.get("clients"):
            self.ask("1. Which clients: both, opencode or pi?", "clients", "clients")
        if self.do_config:
            if not self.o.get("coder"):
                self.ask("2. The coder subagent: the main agent gives large tasks to it. auto (on when the server runs "
                         "2 or more slots), on or off?", "coder", "coder")
            if not self.o.get("browser"):
                self.ask("3. The browser tools: an agent opens and checks web pages. on or off?", "onoff", "browser")
            if not self.o.get("web"):
                self.ask("4. Web search: the search queries leave this computer (they go to Exa or Parallel). exa, "
                         "parallel or off?", "web", "web")
            if self.c["clients"] != "pi" and not self.o.get("lsp"):
                self.ask("5. LSP in OpenCode: OpenCode downloads and runs language servers for your code. on or off?",
                         "onoff", "lsp")

    def ask(self, text: str, kind: str, key: str) -> None:
        """One question; Enter keeps the default; a bad answer asks again."""
        while True:
            self.say(text)
            self.out.flush()
            self.err.write(f"  [{self.c[key]}] ")
            self.err.flush()
            a = self.stdin.readline().rstrip("\n").strip(" \t")
            if not a:
                return
            v = normalize(a, kind)
            if v:
                self.c[key] = v
                return
            self.say("Type one of the words in the question, or press Enter.")

    # -- OpenCode and Pi
    def clients(self) -> List[str]:
        """Install or update them when they are missing or older than tested; the report's lines."""
        progs = self.parts.programs(self.env, self.out, self.err)
        chosen = [x for x, skip in (("opencode", "pi"), ("pi", "opencode")) if self.c["clients"] != skip]
        rows = []
        for name in chosen:
            pkg, key = (NPM_PKG_PI, "PI") if name == "pi" else (NPM_PKG_OPENCODE, "OPENCODE")
            tested, major = self.versions.get(f"{key}_TESTED", ""), self.versions.get(f"{key}_MAJOR", "")
            found = progs.which(name) is not None
            have = progs.version_of(name) if found else ""
            rows.append((name, pkg, action(self.do_install, found, have, tested, major), have or "unknown", tested))
        todo = [r[0] for r in rows if r[2] in ("install", "update")]
        if todo:
            which = todo[0] if len(todo) == 1 else "both"
            self.echo()
            self.say(f"CARL installs {label(which)} (from npm, into ~/.local).")
            rc = self.install_clients(which, {**self.env, "COLUMNS": str(self.width)})
            if rc != 0:
                self.say(f"error: the install of {label(which)} stopped (exit code {rc}). Its output is above. Correct "
                         f"the cause, then run ./setup again.", err=True)
                raise Stop(rc)
        report = []
        for name, pkg, act, was, tested in rows:
            now = (progs.version_of(name) if progs.which(name) else "") or "unknown"
            n = label(name)
            report.append({
                "install": f"{n}: installed (version {now}).",
                "update": f"{n}: updated from version {was} to {now}.",
                "keep": f"{n}: version {was}, the version CARL is tested with.",
                "newer": f"{n}: version {was}, newer than the version CARL is tested with ({tested}). It is kept. If a "
                         f"CARL part does not work, install the tested one: npm install -g --prefix ~/.local "
                         f"{pkg}@{tested}",
                "major": f"{n}: version {was}. CARL's parts for {n} need version {tested.split('.', 1)[0]} (CARL is "
                         f"tested with {tested}); with this version they do nothing. CARL did not change it. To use "
                         f"CARL, install the tested one: npm install -g --prefix ~/.local {pkg}@{tested}",
                "unknown": f"{n}: version {was}. CARL cannot read its version or the tested one; it is kept.",
                "skip": f"{n}: not changed (--no-install).",
            }[act])
        return report

    def install_clients(self, which: str, environ: Mapping[str, str]) -> int:
        return clients_main(which, environ, self.here, self.parts, self.out, self.err)

    # -- the configs
    def configs(self) -> Tuple[str, int]:
        """The config step with the choices; (the smoke test's line, the count of backups)."""
        self.echo()
        self.say("CARL writes the configs.")
        env = dict(self.env)
        c = self.c
        env[sw.CLIENTS], env[sw.WEB_SEARCH] = c["clients"], c["web"]
        for k in (sw.CODER, sw.NO_CODER, sw.LSP):
            env.pop(k, None)
        if c["coder"] == "on":
            env[sw.CODER] = "1"
        elif c["coder"] == "off":
            env[sw.NO_CODER] = "1"
        env[sw.NO_BROWSER] = "1" if c["browser"] == "off" else "0"
        env[sw.NO_LSP] = "1" if c["lsp"] == "off" else "0"
        for k in CARRIED:
            if not env.get(k):
                v = self.last(k)
                if v:
                    env[k] = v
        env["COLUMNS"] = str(self.width)
        tee = install.Tee(self.out)
        rc = self.parts.step(env, self.here, tee, tee, self.net, self.stdin)
        if rc != 0:
            self.say(f"error: the config step stopped (exit code {rc}). Its output is above. Your files have backups "
                     f"(FILE.bak). Correct the cause, then run ./setup again.", err=True)
            raise Stop(rc)
        lines = tee.text().split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        start = next((n for n, x in enumerate(lines) if SMOKE.match(x)), None)
        smoke = re.sub(r" +", " ", " ".join(lines[start:])).rstrip(" ") if start is not None else ""
        return smoke, sum(1 for x in lines if BACKUP.match(x))


def json_field(path: str, key: str) -> str:
    """A string or number field of a JSON object ("" when none)."""
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f).get(key, "")
    except Exception:                                           # noqa: BLE001 (the bash version's `except Exception`)
        v = ""
    return str(v) if isinstance(v, (str, int)) and not isinstance(v, bool) else ""


# ------------------------------------------------------------------ the clients step (OpenCode and Pi from npm)
def clients_main(which: str, environ: Mapping[str, str], here: str, parts: Parts, out: IO[str], err: IO[str]) -> int:
    """OpenCode and Pi for this user, from npm into ~/.local (no sudo), with Node 22 LTS from nodejs.org when the
    computer's Node is missing or older than Pi needs. The exit code."""
    width = install.out_width(environ, out)
    which = which or "both"
    if which in ("-h", "--help", "help"):
        out.write(wrap_help(clients_help(install.setup_command(parts.platform), install.defaults().server), width))
        return 0
    if which not in sw.CLIENT_CHOICES:
        err.write(f"error: unknown argument '{which}'. Use both, opencode or pi. "
                  f"Help: {install.setup_command(parts.platform)} clients --help\n")
        return 2
    home = environ.get("HOME") or os.path.expanduser("~")
    prefix = os.path.join(home, ".local")
    windows = parts.platform == "win32"
    bindir = bin_dir(prefix, parts.platform)
    os.makedirs(bindir, exist_ok=True)
    env = {**environ, "PATH": bindir + os.pathsep + environ.get("PATH", "")}
    progs = parts.programs(env, out, err)
    have = progs.node_version() if progs.which("node") and progs.which("npm") else ""
    if have and at_least(have, NODE_MIN):
        out.write(f"Node {have} found ({progs.which('node')})\n")
    else:
        if have:
            out.write(f"Node {have} is older than {NODE_MIN}. The script installs Node 22 LTS for this user only.\n")
        from ..adapters.node_dist import NodeError
        try:
            dest = parts.node().install(NODE_MIN, prefix, out)
        except NodeError as e:
            out.flush()
            err.write(f"{e}\n")
            return 1
        except OSError as e:
            out.flush()
            err.write(f"error: the Node download stopped: {e}\n")
            return 1
        v = progs.node_version()
        if not v:
            err.write("error: installed Node does not run on this system\n")
            return 1
        out.write(f"Node {v} installed at {dest}\n")
    versions = read_versions(os.path.join(here, VERSIONS_FILE))
    pkgs = []
    if which in ("both", "opencode"):
        pkgs.append(f"{NPM_PKG_OPENCODE}@{versions.get('OPENCODE_TESTED') or 'latest'}")
    if which in ("both", "pi"):
        pkgs.append(f"{NPM_PKG_PI}@{versions.get('PI_TESTED') or 'latest'}")
    out.write(f"npm install -g --prefix {prefix} {' '.join(pkgs)}\n")
    rc = progs.npm_install(prefix, pkgs)
    if rc != 0:
        return rc
    if windows and parts.user_path is not None:          # the user's Path, not a profile (Git Bash reads it too)
        try:
            if parts.user_path(bindir):
                out.write(f"Added {bindir} to your Path (HKCU\\Environment)\n")
        except OSError as e:
            err.write(f"Note: CARL could not add {bindir} to your Path ({e.strerror or e}). Add it yourself.\n")
    # One marked line, appended to a profile that exists (never created), after a backup.
    for rc_name in () if windows else (".zshrc", ".bashrc"):
        path = os.path.join(home, rc_name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            if PATH_MARK.search(f.read()):                  # "# mtplx-vm-client" marked the same line before 1.2.0
                continue
        bak = path + ".bak"                        # one copy (the setup keeps FILE.before-carl and one FILE.bak)
        shutil.copy2(path, bak)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"\n{PATH_LINE}\n")
        out.write(f"Added ~/.local/bin to PATH in ~/{rc_name} (marked # carl-vm-client; backup: {rc_name}.bak)\n")
    out.write("\n")
    for name, pad in (("opencode", "opencode"), ("pi", "pi      ")):
        if which in ("both", name):
            p = progs.output([name, "--version"]) if progs.which(name) else None
            text = (p.stdout if p is not None else "") + ("is not on PATH\n" if p is None or p.returncode else "")
            out.write(f"{pad} {text.rstrip(chr(10))}  -> {progs.which(name) or ''}\n")
    out.write("\n")
    out.write("Open a new terminal. Then run opencode or pi.\n" if windows
              else "Open a new terminal, or run: source ~/.zshrc. Then run opencode or pi.\n")
    conf = os.path.join(home, ".config")
    if not any(os.path.isfile(os.path.join(conf, d, KEY_FILE)) for d in key_dirs()):
        out.write("There is no CARL config yet. Run ./setup to connect OpenCode and Pi to the server.\n")
    out.flush()
    return 0


def key_dirs() -> List[str]:
    """The folders under ~/.config that hold a key of CARL's (now and before 1.2.0: data/legacy.json)."""
    from ..adapters import package_data
    doc = package_data.PackageFiles().load("legacy")
    paths = [".config/carl/api-key", package_data.legacy().old.key_file, *doc.get("key_files", [])]
    return [p.split("/")[1] for p in paths if p.startswith(".config/") and p.count("/") == 2]


# ------------------------------------------------------------------ the entries
def main(argv: List[str], parts: Parts, environ: Optional[MutableMapping[str, str]] = None) -> int:
    """carl_setup.py: the setup (./setup), or one of its parts (install: the config step; clients [WHICH]:
    OpenCode and Pi from npm)."""
    environ = os.environ if environ is None else environ
    here = parts.here
    if argv[:1] == ["install"]:
        return install.main(argv[1:], environ, here)
    if argv[:1] == ["clients"]:
        return clients_main(argv[1] if len(argv) > 1 else "both", environ, here, parts, sys.stdout, sys.stderr)
    return Setup(environ, here, parts, sys.stdout, sys.stderr, sys.stdin).run(argv)

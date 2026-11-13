"""The setup's config step: it wires OpenCode and Pi to the model server (llama.cpp). ./setup runs it, the sync
service runs it again with the last switches (adapters/install_sh.py), and `./setup install` (`setup.cmd install` on
Windows) runs it alone. It was client/install.sh until Phase W; its decisions are the same, and its texts too, except
that they name the setup's command of the OS (`./setup install`) where they named the script (W9.4, A65):

- Where the server is: an option (--local, --vm [HOST], --host ADDRESS, ADDRESS, --port N), else remote.json next
  to the client's files (the server writes it at every start; its own address makes local mode), else local mode
  on macOS and vm mode elsewhere. Local mode takes the address a local server listens on (adapters/local_addrs.py).
- The API key, first hit wins: --key or --key-file, CARL_API_KEY, api-key next to the client's files,
  ~/.config/carl/api-key, the key files of the names before 1.2.0 (data/legacy.json), the prompt. It is kept in
  ~/.config/carl/api-key (0600; on the server's computer that is the server's own file, so a different key keeps
  the old one in api-key.bak).
- The context for the clients: LLAMA_CTX (N or Nk), else the running server's /props n_ctx, else the default
  (data/server.json). It only decides when the clients compact; it must not exceed the server's -c.
- The coder subagent: NO_CODER=1 off, CODER=1 on, an external CODER_MODEL on; else on when the server runs 2 or more
  slots (/props total_slots: with 1 slot a subagent would evict the main session); with no server, the earlier
  choice (OpenCode's carl.json) or on.
- The models: on the server's own clone, carl.py client-models writes installed-models.json; else the file
  the package carries; else the ids /v1/models gives, as generic entries.
- The configs: carl_client.app.configure, in this process (its merge rules, its backups).
- The sync: the switches go into ~/.config/carl/client-install.env (the sync applies them again); on a computer
  whose server is elsewhere, the sync service (launchd or systemd --user) keeps one connection to the dashboard.
  The setup records the Python the plugins run carl-sync.py with (client-sync.json "python").
- The smoke test: /v1/models with the key.

The step reads its environment from the mapping it gets, never from os.environ, and writes to the streams it gets
(the setup passes one stream for both, as the bash setup read the config script's 2>&1). Programs it starts write
there too.
The text helpers at the top (the width, the help layout, the wrapped messages) are the bash scripts' awk, fold and
sed, ported for the setup and this step; the output is byte for byte the bash scripts'.
"""
from __future__ import annotations

import contextlib
import getpass
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Dict, Iterator, List, Mapping, MutableMapping, Optional, Tuple

from ..adapters import package_data
from ..domain import merge_opencode
from ..domain import switches as sw
from ..domain.legacy import NAMES
from ..domain.models import LIST_FILE
from ..domain.remote import KEY_FILE, REMOTE_FILE
from ..domain.words import COMMAND
from ..ports import Interpreter, LocalNetwork, ServiceManager

HELP_COLUMN = 24                 # the help pages' column of terms
MIN_WIDTH = 50
DEFAULT_WIDTH = 80
SYNC_SCRIPT = "carl-sync.py"
CLONE_CARL = os.path.join("..", "carl.py")     # the server's own clone: its model list
CARL_ID_RE = re.compile(r"^[0-9a-f]{32}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
API_RE = re.compile(r"https?://[A-Za-z0-9.:\[\]-]+:[0-9]{1,5}")  # as --cache-api takes it
PORT_RE = re.compile(r"^[0-9]{1,5}$")
VM_ARG_RE = re.compile(r"^[0-9.]+$")
CTX_K_RE = re.compile(r"^([0-9]{1,6})k$")
CTX_N_RE = re.compile(r"^[0-9]{1,9}$")
EMPTY_LIST = '{"schema": 1, "default": null, "models": []}\n'


# ------------------------------------------------------------------ the text helpers (the bash scripts' awk and fold)
def out_width(environ: Mapping[str, str], stream: IO[str]) -> int:
    """The width of the help page and the messages: COLUMNS, else the terminal's, else 80; at least 50."""
    w = environ.get("COLUMNS", "")
    if not re.fullmatch(r"[0-9]+", w):
        w = ""
        if _isatty(stream):
            try:
                w = str(os.get_terminal_size(stream.fileno()).columns)
            except (AttributeError, OSError, ValueError):
                w = ""
        if not re.fullmatch(r"[0-9]+", w):
            w = str(DEFAULT_WIDTH)
    return max(int(w), MIN_WIDTH)


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _emit(text: str, first: str, rest: str, width: int, out: List[str]) -> None:
    line, started = first, False
    for word in re.split(r" +", text):
        if word == "":
            continue
        if started and len(line) + 1 + len(word) > width:
            out.append(line)
            line = rest + word
        elif started:
            line = line + " " + word
        else:
            line, started = line + word, True
    out.append(line)


def wrap_help(text: str, width: int, column: int = HELP_COLUMN) -> str:
    """The help text wrapped to the width. A line "TERM\\tTEXT" (a backslash and a t) is a term with its text in a
    column; the other lines wrap with their own indent; an empty line stays."""
    out: List[str] = []
    for line in text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"):
        if line == "":
            out.append("")
            continue
        k = line.find("\\t")
        if k >= 0:
            term, desc, pad = line[:k], line[k + 2:], " " * column
            if len(term) + 2 > column:
                out.append(term)
                _emit(desc, pad, pad, width, out)
            else:
                _emit(desc, term.ljust(column), pad, width, out)
            continue
        ind = line[:len(line) - len(line.lstrip(" "))]
        _emit(line[len(ind):], ind, ind, width, out)
    return "".join(x + "\n" for x in out)


def _column(col: int, c: str) -> int:
    if c == "\b":
        return col - 1 if col > 0 else 0
    if c == "\r":
        return 0
    if c == "\t":
        return col + 8 - col % 8
    return col + 1


def fold(text: str, width: int) -> List[str]:
    """`fold -s -w WIDTH` of one line of text: lines broken after the last blank that fits, else at the width."""
    out: List[str] = []
    buf, col, i = "", 0, 0
    while i < len(text):
        c = text[i]
        col = _column(col, c)
        if col > width:
            blank = max(buf.rfind(" "), buf.rfind("\t"))
            if blank >= 0:
                out.append(buf[:blank + 1])
                buf = buf[blank + 1:]
                col = 0
                for x in buf:
                    col = _column(col, x)
                continue                        # the same character again, on the new line
            if buf == "":
                buf, col, i = c, _column(0, c), i + 1
                continue
            out.append(buf)
            buf, col = "", 0
            continue
        buf += c
        i += 1
    if buf or not out:
        out.append(buf)
    return out


def say_text(text: str, width: int) -> str:
    """One message, wrapped to the width less 2; its next lines are indented by 2 (the scripts' say)."""
    lines: List[str] = []
    for part in text.split("\n"):
        lines += [x.rstrip(" ") for x in fold(part, width - 2)]
    return "".join((x if n == 0 else "  " + x) + "\n" for n, x in enumerate(lines))


def tilde(path: str, home: str) -> str:
    """A path as ~/... when it is under the home folder."""
    return "~" + path[len(home):] if home and path.startswith(home) else path


def strip_spaces(text: str) -> str:
    """The text without any whitespace (`tr -d '[:space:]'`)."""
    return "".join(text.split())


def read_squeezed(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return strip_spaces(f.read())


def nonempty(path: str) -> bool:
    """A file that exists and is not empty (`[[ -s ]]`)."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


# ------------------------------------------------------------------ the package's values
@dataclass(frozen=True)
class Defaults:
    """The values the step falls back to (data/server.json): CARL's port, the VM host's address, the context; and
    the platform words of its texts (data/words.json: the server's computer in a sentence, and with "server": the server
    Mac)."""
    port: int
    vm_host: str
    ctx: int
    machine: str
    server: str

    @property
    def the_machine(self) -> str:
        """The server's computer as a thing: the Mac (the server Mac without "server")."""
        return self.server.replace("the server ", "the ", 1)


def defaults() -> Defaults:
    files = package_data.PackageFiles()
    doc, words = files.load("server"), package_data.server_words()
    port, vm, ctx = (doc.get("default_port"), doc.get("vm_host"), doc.get("default_ctx")) if isinstance(doc, dict) \
        else (None, None, None)
    if not (isinstance(port, int) and isinstance(vm, str) and HOST_RE.match(vm) and isinstance(ctx, int)
            and ctx >= 1024):
        raise ValueError("server.json: default_port, vm_host and default_ctx must be a port, an address and a context")
    return Defaults(port, vm, ctx, words["machine"], words["server"])


@dataclass(frozen=True)
class OldKeys:
    """The key files of the names before 1.2.0 (data/legacy.json), under the home folder: the folder the settings
    were in (moved once to ~/.config/carl), the clients' copy, and the server's own key on its computer."""
    old_dir: str
    client_key: str
    server_key: str


def old_keys() -> OldKeys:
    doc = package_data.PackageFiles().load("legacy")
    old = package_data.legacy().old.key_file
    keys, server = doc.get("key_files"), doc.get("server_key_file")
    if not (isinstance(keys, list) and keys and isinstance(keys[0], str) and isinstance(server, str) and server):
        raise ValueError("legacy.json: key_files and server_key_file must name the key files before 1.2.0")
    return OldKeys(os.path.dirname(old), keys[0], server)


def setup_command(platform: str) -> str:
    """How the user starts the setup on this OS (data/words.json: ./setup, or setup.cmd on Windows). The setup's parts
    run by the same command: `./setup install`, `./setup clients`."""
    return package_data.sync_words(platform)["setup_command"]


def help_text(d: Defaults, setup: str) -> str:
    return f"""CARL client installer: connect OpenCode and Pi to the CARL server.

Run {setup} instead. It asks for your choices, installs OpenCode and Pi, and runs this script. This script is an internal part of the setup. The sync service runs it too.

Usage: {setup} install [OPTION...]

Where the server is:
  (no option)\\tThe server in remote.json next to this script. The server writes this file at every start. Without remote.json: --local on a Mac, --vm on Linux.
  --local\\tThe server runs on {d.machine}. The script uses the address that the server listens on, else 127.0.0.1.
  --vm [HOST]\\tThe server runs on the host of this VM. The default HOST is {d.vm_host}.
  --host ADDRESS\\tThe server is at ADDRESS, for example the LAN address of {d.the_machine}.
  ADDRESS\\tThe same as --host ADDRESS.
  --port N\\tThe port of the server. The default is {d.port}.
  -h, --help\\tShow this help.

The API key:
  --key-file FILE\\tRead the API key from FILE. The dashboard on {d.server} shows the key on its Connect tab.
  --key KEY\\tGive the API key on the command line. Then your shell history and the process list show the key. Use --key-file or the prompt if you can.
  The script looks for the key in this order: --key or --key-file, CARL_API_KEY, the file api-key next to this script, ~/.config/carl/api-key, a key from an earlier install. Then it asks you. It keeps the key in ~/.config/carl/api-key, with mode 0600.

Switches (environment variables, for example NO_CACHE=1):
  CLIENTS=WHICH\tThe configs to write: both (the default), opencode or pi.
  NO_CACHE=1\\tDo not install the disk cache.
  NO_MODEL_CHECK=1\\tDo not install the model check (OpenCode and Pi).
  NO_SWITCHER=1\\tDo not install the session switcher of OpenCode.
  NO_SIDEBAR=1\\tDo not install the subagents sidebar of OpenCode.
  CODER=1, NO_CODER=1\\tAlways install the coder subagent, or never. Without them, the script installs the coder when the server runs 2 or more slots, or when the coder runs on an external model (CODER_MODEL).
  NO_BACKGROUND_SUBAGENTS=1\\tThe main session waits for the coder.
  NO_REMINDER=1\\tNo reminder about the coder at the end of each message of the main session (it is on by default: it is what moves large and stuck tasks to the coder).
  CODER_THINKING=MODEL:VALUE,...\\tThe coder's thinking on this computer, per model, over the dashboard's Coder thinking. VALUE: main (same as main), on, off, low, medium or xhigh. /carl sets it.
  CODER_MODEL=PROVIDER/MODEL\\tThe coder runs on this model of OpenCode or Pi, not on the CARL server. The coder's work goes to that provider. main (the default): the model of the main session. /carl sets it.
  CODER_MODEL_THINKING=LEVEL\\tThe thinking of that external model: a variant (OpenCode) or a thinking level (Pi). Without it: the model's own default. /carl sets it.
  CODER_TESTS=WHEN\\tWhen CARL's test session runs for new code: after (the default: the code, then the tests), before (the tests, then the code: test-first, needs a full spec such as spec-kit) or off (no test session). /carl sets it.
  CODER_REQUEST_CHECK=MODE\\tThe check that the coder's brief carries the literals of your request (commands, signatures, formats, files): reminder (the default: one reminder), on (until each one is in the brief) or off. /carl sets it.
  CODER_RUN_GATE=on|off\\tCARL checks the coder's work after each code session (compile, language checks, the project's tests): on (the default) or off. /carl sets it.
  CODER_FIX_ROUNDS=N\\tHow many times the run gate sends failing work back to the coder by itself: 0 to 3 (the default 1). /carl sets it.
  CODER_FREE_FORM=MODE\\tThe free-form part of the coder's brief: model (the default: the model decides), on or off. /carl sets it.
  NO_BROWSER=1\\tDo not install the browser tools.
  BROWSER_HEADED=1\\tShow the browser on the screen.
  WEB_SEARCH=PROVIDER\\texa (the default), parallel or off.
  NO_LSP=1\\tDo not turn on the LSP tool of OpenCode.
  LLAMA_CTX=N\\tThe context for the clients, in tokens or as Nk, for example 96k. The default is the context of the running server.
  NO_PROFILE=1\\tDo not change ~/.zshrc or ~/.bashrc.
  NO_SYNC_SERVICE=1\\tDo not install the sync service.

Run the script again after you change the context of the server, or after you download or delete a model. The script keeps your own settings. It makes a backup of each file before it changes it.
"""


# ------------------------------------------------------------------ the server's HTTP API (curl -fsS -m 5)
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **kw: Any) -> None:       # curl without -L: a redirect is the answer
        return None


def http_get(url: str, key: str, timeout: float = 5.0) -> Optional[bytes]:
    """GET with the key as Bearer header: the body, or None for an error, a 4xx or 5xx, or no answer. Proxies only
    from the environment (as curl), never the system's."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies_environment()),
                                         _NoRedirect())
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code < 400:
            try:
                return e.read()
            except OSError:
                return b""
        return None
    except (OSError, ValueError, Exception):           # no answer, a bad URL, a broken reply: curl fails alike
        return None


# ------------------------------------------------------------------ the step
@dataclass
class Ports:
    """What the step uses of the computer (build_ports; tests pass fakes): the server's HTTP API, this computer's
    addresses (C-050), the interpreter (C-051), the sync service's managers (C-047: this OS's first, launchd,
    systemd --user or Task Scheduler; then the others whose files an older setup may have left here), and the
    configure step (None: carl_client.app.configure in this process)."""
    addrs: LocalNetwork
    interpreter: Interpreter
    services: Tuple[ServiceManager, ...]
    get: Callable[[str, str], Optional[bytes]] = http_get
    configure: Optional[Callable[[List[str]], int]] = None
    platform: str = field(default_factory=lambda: sys.platform)


class Stop(Exception):
    """The step ends with this exit code (its message is written already)."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class Step:
    """One run of the config step: argv (its options), the environment, the client's folder, the streams."""

    def __init__(self, argv: List[str], environ: Mapping[str, str], here: str, out: IO[str], err: IO[str],
                 stdin: Optional[IO[str]] = None, ports: Optional[Ports] = None) -> None:
        self.argv = list(argv)
        self.v: Dict[str, str] = dict(environ)            # the shell's variables: CODER and CODER_MODEL change
        self.env = dict(environ)                          # what the programs it starts get
        self.here = here
        self.out = out
        self.err = err
        self.stdin = stdin if stdin is not None else sys.stdin
        self.ports = ports or build_ports(environ)
        self.home = self.v.get("HOME") or os.path.expanduser("~")
        self.width = out_width(self.v, out)
        self.d = defaults()
        self.setup = setup_command(self.ports.platform)
        self._props: Optional[Any] = None
        self._props_read = False

    # -- output
    def echo(self, text: str = "", err: bool = False) -> None:
        (self.err if err else self.out).write(text + "\n")

    def say(self, text: str, err: bool = False) -> None:
        (self.err if err else self.out).write(say_text(text, self.width))

    def fail(self, text: str, code: int, wrap: bool = False) -> Stop:
        if wrap:
            self.say(text, err=True)
        else:
            self.echo(text, err=True)
        return Stop(code)

    def flag(self, name: str, value: str = "1") -> bool:
        return self.v.get(name, "") == value

    def short(self, path: str) -> str:
        return tilde(path, self.home)

    # -- the server
    def api(self, path: str) -> Optional[bytes]:
        return self.ports.get(f"http://{self.host}:{self.port}{path}", self.key)

    def props(self) -> Optional[Any]:
        """The running server's /props, parsed (read once); None when no server answers."""
        if not self._props_read:
            self._props_read = True
            body = self.api("/props")
            try:
                self._props = json.loads(body) if body is not None else None
            except ValueError:
                self._props = None
        return self._props

    def run(self) -> int:
        try:
            return self._run()
        except Stop as s:
            return s.code

    def _run(self) -> int:
        self.options()
        self.remote_file()
        self.where()
        self.migrate_key_dir()
        self.api_key()
        ctx, ctx_src = self.context()
        self.say(f"Context for the clients: {ktok(ctx)} tokens per slot{ctx_src}")
        coder = self.coder()
        models_arg, running, cleanup = self.models(ctx)
        try:
            web, clients = self.tools()
            self.check_remote_ids()
            rc = self.configure(clients, ctx, models_arg, running, coder, web)
            if rc != 0:
                return rc
        finally:
            cleanup()
        if not self.flag("CARL_SYNC"):
            self.sync()
        self.smoke()
        return 0

    # -- the options
    def options(self) -> None:
        self.mode, self.host, self.port = "", "", ""
        self.arg_key, self.key_in_args = "", False
        pos: List[str] = []
        a = self.argv
        i = 0

        def value(what: str) -> str:
            if i + 1 >= len(a) or a[i + 1] == "":
                raise self.fail(f"error: {a[i]} needs {what}. Help: {self.setup} install --help", 1)
            return a[i + 1]

        while i < len(a):
            x = a[i]
            if x == "--local":
                self.mode = "local"
            elif x == "--vm":
                self.mode = "vm"
                if i + 1 < len(a) and VM_ARG_RE.match(a[i + 1]):
                    self.host = a[i + 1]
                    i += 1
            elif x == "--host":
                self.mode, self.host = "host", value("an address")
                i += 1
            elif x == "--key":
                self.arg_key, self.key_in_args = value("the key"), True
                i += 1
            elif x == "--port":
                self.port = value("a port")
                i += 1
            elif x == "--key-file":
                f = value("a file")
                if not os.access(f, os.R_OK) or os.path.isdir(f):
                    raise self.fail(f"error: cannot read the key file {f} (check the path and that you can read it)", 1)
                self.arg_key, self.key_in_args = read_squeezed(f), False
                i += 1
            elif x in ("-h", "--help"):
                self.out.write(wrap_help(help_text(self.d, self.setup), self.width))
                raise Stop(0)
            else:
                pos.append(x)
            i += 1
        if len(pos) > 3:
            raise self.fail(f"error: too many arguments: {' '.join(pos)}. Help: {self.setup} install --help", 2)
        if pos:
            self.host = pos[0]
            self.mode = self.mode or "host"
        if len(pos) >= 2:
            # The pre-1.2.0 form HOST MTPLX_PORT [LLAMA_PORT]: the MTPLX port has no use any more.
            self.say(f"Note: the form {self.setup} install HOST MTPLX_PORT LLAMA_PORT is old. MTPLX support was "
                     f"removed, so the script ignores '{pos[1]}'.", err=True)
            self.say(f"Use this form: {self.setup} install --host HOST --port PORT", err=True)
            if len(pos) >= 3 and not self.port:
                self.port = pos[2]

    def remote_file(self) -> None:
        """remote.json's CARL ID (Phase 23.5) and dashboard API, for any mode. The configs take that API, where the
        server really runs it (decision A25; the bash config script always wrote the port + 1)."""
        self.carl_id = ""
        self.cache_api = ""
        self.remote = os.path.join(self.here, REMOTE_FILE)
        if not nonempty(self.remote):
            return
        doc = self.remote_doc()
        if doc is not None:
            cid = doc.get("id") or ""
            self.carl_id = str(cid) if CARL_ID_RE.match(str(cid)) else ""
            api = doc.get("cache_api")
            api = api.rstrip("/") if isinstance(api, str) else ""
            self.cache_api = api if API_RE.fullmatch(api) else ""

    def remote_doc(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.remote, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def where(self) -> None:
        if not self.mode and nonempty(self.remote):
            doc = self.remote_doc()
            try:
                line = f"{doc['host']} {int(doc['port'])}" if doc is not None else None
            except (KeyError, TypeError, ValueError):
                line = None
            if line is not None:
                self.host = line.rsplit(" ", 1)[0]
                self.port = self.port or line.split(" ", 1)[1]
                # the server's own computer (its address is one of ours): local, no sync service
                self.mode = "local" if self.ports.addrs.is_ours(self.host) else "host"
                self.echo(f"Server address from remote.json: {self.host}:{self.port}")
        self.port = self.port or str(self.d.port)
        if not self.mode:
            self.mode = "local" if self.ports.platform == "darwin" else "vm"
        if not (PORT_RE.match(self.port) and 1 <= int(self.port) <= 65535):
            raise self.fail(f"error: not a TCP port: '{self.port}'", 2)
        if self.mode == "local":
            self.host = self.ports.addrs.listen_addr(self.port) or "127.0.0.1"
        elif self.mode == "vm":
            self.host = self.host or self.d.vm_host
        # The address goes into URLs and the client configs: a host name or an IP address only.
        if not HOST_RE.match(self.host):
            raise self.fail(f"error: not a host name or address: '{self.host}'", 2)
        self.echo(f"Server: {self.host}, port {self.port} ({self.mode} mode)")

    # -- the key
    def migrate_key_dir(self) -> None:
        """Before the rename in 1.2.0 the folder was ~/.config/llm-deploy: moved once (a link stays at the old path)."""
        self.key_dir = os.path.join(self.home, ".config", "carl")
        self.key_file = os.path.join(self.key_dir, KEY_FILE)
        self.old = old_keys()
        self.old_dir = os.path.join(self.home, *self.old.old_dir.split("/"))
        if (os.path.isdir(self.old_dir) and not os.path.islink(self.old_dir) and not os.path.lexists(self.key_dir)):
            try:
                os.rename(self.old_dir, self.key_dir)
            except OSError as e:
                self.echo(f"mv: {e.strerror or e}", err=True)
                return
            os.chmod(self.key_dir, 0o700)
            for n in os.listdir(self.key_dir):
                p = os.path.join(self.key_dir, n)
                if os.path.isfile(p) and not os.path.islink(p):
                    os.chmod(p, os.stat(p).st_mode & 0o7700)
            with contextlib.suppress(OSError):
                os.symlink(os.path.basename(self.key_dir), self.old_dir)
            self.echo(f"Moved {self.short(self.old_dir)} to {self.short(self.key_dir)}, CARL's new name. The old path "
                      f"links to it.")

    def api_key(self) -> None:
        here_key = os.path.join(self.here, KEY_FILE)
        old_dir_key = os.path.join(self.old_dir, KEY_FILE)
        old_server = os.path.join(self.home, *self.old.server_key.split("/"))
        old_client = os.path.join(self.home, *self.old.client_key.split("/"))
        if self.arg_key:
            key = self.arg_key
            if self.key_in_args:
                self.say("Note: --key puts the key in your shell history and in the process list. Next time, use "
                         "--key-file FILE or the prompt.", err=True)
        elif self.v.get("CARL_API_KEY"):
            key = self.v["CARL_API_KEY"]
        elif os.path.isfile(here_key):
            with contextlib.suppress(OSError):
                os.chmod(here_key, 0o600)                 # a copied client folder can lose the mode
            key = read_squeezed(here_key)
        elif nonempty(self.key_file):
            key = read_squeezed(self.key_file)
            self.echo(f"Using the API key in {self.short(self.key_file)}")
        elif nonempty(old_dir_key):
            key = read_squeezed(old_dir_key)
            self.echo(f"Using the API key from {self.short(old_dir_key)}. It is now in {self.short(self.key_file)}.")
        elif self.mode == "local" and nonempty(old_server):
            key = read_squeezed(old_server)
            self.echo(f"Using the API key of the local server from before 1.2.0: {self.short(old_server)}")
        elif nonempty(old_client):
            key = read_squeezed(old_client)
            self.echo(f"Using the API key from before 1.2.0 in {self.short(old_client)}. It is now in "
                      f"{self.short(self.key_file)}.")
        else:
            key = self.prompt(f"API key (the dashboard on {self.d.server} shows it on its Connect tab): ")
        key = key.strip(" \t\n\r\f\v")
        if not key:
            raise self.fail(f"error: the API key is empty. Copy it from the Connect tab of the dashboard on "
                            f"{self.d.server}. Give it with --key-file FILE or at the prompt.", 1, wrap=True)
        # It goes into an HTTP header: printable characters, no spaces (never echoed back).
        if not all(c.isprintable() and not c.isspace() for c in key):
            raise self.fail(f"error: the API key contains spaces or control characters. Copy it again from the "
                            f"Connect tab of the dashboard on {self.d.server}.", 1, wrap=True)
        os.makedirs(self.key_dir, exist_ok=True)
        os.chmod(self.key_dir, 0o700)
        # Rewritten only when it changes. On the server's computer this is the server's own key file: a different key
        # replaces it for the next server start, so the old one is kept.
        old_key = read_squeezed(self.key_file) if os.path.isfile(self.key_file) else ""
        if old_key != key:
            if old_key:
                bak = self.key_file + ".bak"              # one copy of the key before (replaced each time)
                copy_private(self.key_file, bak)
                self.say(f"Note: the script replaced the API key in {self.short(self.key_file)}. The old key is in "
                         f"{self.short(bak)}. If {self.d.machine} runs the CARL server, the server uses the new key "
                         f"after its next start.")
            write_private(self.key_file, key)
        os.chmod(self.key_file, 0o600)
        self.key = key

    def prompt(self, text: str) -> str:
        """The key from the terminal, not shown; from stdin without a prompt otherwise. No answer stops the step."""
        if _isatty(self.stdin):
            try:
                return getpass.getpass(text, stream=self.err)
            except EOFError:
                raise Stop(1) from None
        line = self.stdin.readline()
        if not line:
            raise Stop(1)
        self.echo()
        return line.rstrip("\n")

    # -- the context and the coder
    def context(self) -> Tuple[int, str]:
        """The clients' context limit (OpenCode limit.context, Pi contextWindow): client-side only, it decides when
        they compact, and must not exceed the server's -c. LLAMA_CTX > the running server's n_ctx > the default; for
        the running model only (carl_client.domain.models): the others use their own settings."""
        raw = self.v.get(sw.LLAMA_CTX, "")
        if raw:
            v = raw.lower()
            m = CTX_K_RE.match(v)
            if m:
                return int(m.group(1)) * 1024, ", from LLAMA_CTX."
            if CTX_N_RE.match(v):
                return int(v), ", from LLAMA_CTX."
            raise self.fail(f"error: LLAMA_CTX takes tokens or Nk (e.g. 96k), got '{raw}'", 1)
        try:
            n = int(self.props()["default_generation_settings"]["n_ctx"])         # type: ignore[index]
            if n >= 1024:
                return n, ", from the running server."
        except (TypeError, KeyError, ValueError, AttributeError):
            pass
        return self.d.ctx, ". No server answers, so this is the default."

    def slots(self) -> str:
        try:
            return str(int(self.props().get("total_slots", 1)))                  # type: ignore[union-attr]
        except (TypeError, ValueError, AttributeError):
            return ""

    def coder(self) -> str:
        """The coder subagent, only with a 2-slot server (a subagent is a second conversation: with one slot every
        delegation evicts the main session); CODER=1 forces it on, NO_CODER=1 off; a coder on an external model
        (CODER_MODEL=PROVIDER/MODEL, Phase 23.4.5) uses no slot, so it is on also with 1 slot."""
        slots = self.slots()
        if self.v.get(sw.CODER_MODEL, "") == sw.CODER_MODEL_MAIN:
            self.v[sw.CODER_MODEL] = ""
        if self.flag(sw.NO_CODER):
            coder, why = "0", "NO_CODER=1 turns it off."
        elif self.flag(sw.CODER):
            coder, why = "1", "CODER=1 turns it on."
        elif self.v.get(sw.CODER_MODEL):
            coder, why = "1", f"The coder runs on {self.v[sw.CODER_MODEL]}, not on the server."
        elif slots:
            if int(slots) >= 2:
                coder, why = "1", f"The server runs {slots} slots."
            else:
                coder, why = "0", "The server runs 1 slot. A subagent would take the slot of the main session."
        else:
            # the state file configure keeps (llm-deploy.json before the rename; it moves it)
            state = os.path.join(self.home, ".config", "opencode", NAMES.state)
            if not os.path.isfile(state):
                state = os.path.join(self.home, ".config", "opencode", self.old_state())
            have = os.path.isfile(state)
            coder = "1" if not have or _has(state, b'"coder_agent"') else "0"
            why = ("No server answers, so the script keeps the earlier choice." if have
                   else "No server answers, so the script uses the default.")
        self.v[sw.CODER] = coder
        self.say(f"Coder subagent: {'on' if coder == '1' else 'off'}. {why}")
        # carl-sync.py set (the /carl panel) asks for the slot count it read: /carl warns when it turns the coder on
        # with 1 slot
        out = self.v.get("CARL_SLOTS_OUT", "")
        if out and slots:
            with contextlib.suppress(OSError):
                with open(out, "w", encoding="utf-8", newline="\n") as f:
                    f.write(slots + "\n")
        return coder

    @staticmethod
    def old_state() -> str:
        return package_data.legacy().old.state

    # -- the models
    def models(self, ctx: int) -> Tuple[str, str, Callable[[], None]]:
        """(the model list file, the model the server runs, the cleanup). On the server's clone, carl.py lists
        them into installed-models.json next to the client's files (single-model mode: only the model the server runs;
        router mode: every installed model); else the package's list; else /v1/models, as generic entries."""
        list_file = os.path.join(self.here, LIST_FILE)
        p = self.props()
        served = ""
        if isinstance(p, dict):
            served = "router" if p.get("role") == "router" else "model " + str(p.get("model_alias") or "")
        running = served[len("model "):] if served.startswith("model ") else ""
        tool = os.path.join(self.here, CLONE_CARL)
        if os.path.isfile(tool):
            argv = [self.ports.interpreter.command(), tool, "client-models", "--running", running]
            if served == "router":
                argv.append("--router")
            if self.write_from(argv, list_file + ".tmp"):
                os.replace(list_file + ".tmp", list_file)
            else:
                with contextlib.suppress(OSError):
                    os.remove(list_file + ".tmp")
                self.say(f"Warning: the script could not list the models of {self.d.machine} with carl.py "
                         f"client-models.", err=True)
        models_arg, cleanup = list_file, (lambda: None)
        if not nonempty(list_file):
            fd, models_arg = tempfile.mkstemp()
            os.close(fd)
            cleanup = (lambda: _remove(models_arg))
            text = self.server_list(ctx)
            with open(models_arg, "w", encoding="utf-8", newline="\n") as f:
                f.write(text if text is not None else EMPTY_LIST)
            self.say(f"Note: there is no installed-models.json here, so the model list comes from the server "
                     f"(/v1/models). For every installed model, make a new client package on {self.d.server} "
                     f"({COMMAND} package), unzip it over this folder and run ./setup.")
        try:
            with open(models_arg, encoding="utf-8") as f:
                ids = ", ".join(m["id"] for m in json.load(f)["models"]) or "none yet"
        except (OSError, ValueError, KeyError, TypeError):
            ids = ""
        self.say(f"Models for the clients: {ids}.")
        return models_arg, running, cleanup

    def server_list(self, ctx: int) -> Optional[str]:
        from .model_list import from_server_ids
        body = self.api("/v1/models")
        try:
            ids = [m.get("id") for m in json.loads(body).get("data", [])]     # type: ignore[arg-type]
            ml = from_server_ids(ids, ctx)
            return json.dumps({"schema": 1, "default": ml.default, "models": [m.__dict__ for m in ml.models]})
        except (TypeError, ValueError, AttributeError):
            return None

    def write_from(self, argv: List[str], dest: str) -> bool:
        """A program's stdout into dest (its errors on the step's stream); True when it exits 0."""
        try:
            with open(dest, "w", encoding="utf-8", newline="\n") as f:
                p = subprocess.run(argv, env=self.env, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.PIPE,
                                   encoding="utf-8", errors="replace")
        except OSError as e:
            self.echo(f"{argv[0]}: {e.strerror or e}", err=True)
            return False
        self.err.write(p.stderr)
        return p.returncode == 0

    # -- the tools, the profile, the browser
    def tools(self) -> Tuple[str, str]:
        web = self.v.get(sw.WEB_SEARCH) or sw.DEFAULTS[sw.WEB_SEARCH]
        if web not in sw.WEB_SEARCH_VALUES:
            raise self.fail(f"error: WEB_SEARCH takes exa, parallel or off, got '{web}'", 2)
        clients = self.v.get(sw.CLIENTS) or sw.CLIENT_CHOICES[0]
        if clients not in sw.CLIENT_CHOICES:
            raise self.fail(f"error: CLIENTS takes both, opencode or pi, got '{clients}'", 2)
        if web != "off":
            self.say(f"Web search: on ({web}). OpenCode and Pi send the search queries to {web}, outside this "
                     f"computer. Everything else stays on this computer. To turn it off, use WEB_SEARCH=off.")
        self.profiles_note()
        if not self.flag(sw.NO_BROWSER):
            from .configure import chrome_installed
            if chrome_installed(self.ports.platform, self.env):
                self.say("Browser: OpenCode (its browser agent) and Pi use Google Chrome, with a temporary profile. To "
                         "turn it off, use NO_BROWSER=1.")
            else:
                self.say("Browser: OpenCode (its browser agent) and Pi use the Chromium of Playwright. Install it one "
                         "time with npx playwright install chromium. It is about 150 MB. To turn it off, use "
                         "NO_BROWSER=1.")
        return web, clients

    def profiles_note(self) -> None:
        """OpenCode reads its tool switches only from the environment: say plainly which shell profile files get the
        3-line pointer to ~/.config/carl/opencode.env, before writing. On Windows no profile changes: the switches go
        into the user's environment (HKCU\\Environment, userenv_hkcu.py)."""
        if self.ports.platform == "win32":
            if self.flag("NO_PROFILE"):
                self.say("User environment: not changed (NO_PROFILE=1). For the tool switches of OpenCode, run "
                         "the setx commands that the list below gives.")
            else:
                self.say("User environment: OpenCode reads its tool switches only from the environment, so the setup "
                         "sets them in HKCU\\Environment (programs that start after it get them). To skip this, use "
                         "NO_PROFILE=1.")
            return
        profiles: List[str] = []
        have_block = False
        for name in merge_opencode.PROFILES:
            f = os.path.join(self.home, name)
            if not os.path.exists(f):
                continue
            if _has(f, merge_opencode.PROFILE_BEGIN.encode("utf-8")):
                have_block = True
                continue
            if os.path.islink(f):
                profiles.append(f"{self.short(f)} (a link to {self.short(os.path.realpath(f))})")
            else:
                profiles.append(self.short(f))
        if self.flag("NO_PROFILE"):
            self.say("Shell profile: not changed (NO_PROFILE=1). For the tool switches of OpenCode, add this line to "
                     "it yourself:")
            self.echo("  " + merge_opencode.PROFILE_LINE)
        elif not profiles and have_block:
            self.echo(f"Shell profile: it loads ~/.config/carl/{merge_opencode.ENV_FILE} already.")
        elif profiles:
            text = profiles[0] if len(profiles) == 1 else f"{profiles[0]} and {profiles[1]}"
            self.say(f"Shell profile: the script adds 3 marked lines to {text}. They load "
                     f"~/.config/carl/{merge_opencode.ENV_FILE}, because OpenCode reads its tool switches only from "
                     f"the environment. The script makes a backup and changes nothing else in the file. To skip this, "
                     f"use NO_PROFILE=1.")

    def check_remote_ids(self) -> None:
        """remote.json's ID and dashboard API belong to its server only (another --host or --port: neither)."""
        if not (self.carl_id or self.cache_api):
            return
        doc = self.remote_doc()
        try:
            same = doc is not None and (doc.get("host"), int(doc.get("port", 0))) == (self.host, int(self.port))
        except (TypeError, ValueError):
            same = False
        if not same:
            self.carl_id = self.cache_api = ""

    # -- the configs
    def configure(self, clients: str, ctx: int, models_arg: str, running: str, coder: str, web: str) -> int:
        """Merges our providers, defaults, coder agent, sidebar and extensions into the existing configs without
        overwriting anything the user owns (carl_client.app.configure). Backups: FILE.before-carl and FILE.bak."""
        def on(off: bool) -> str:
            return "0" if off else "1"

        v = self.v
        argv = ["--bundle", self.here, "--home", self.home, "--host", self.host, "--clients", clients,
                "--llama-port", self.port]
        if self.cache_api:
            argv += ["--cache-api", self.cache_api]
        if self.carl_id:
            argv += ["--carl-id", self.carl_id]
        argv += ["--ctx", str(ctx), "--models", models_arg]
        if running:
            argv += ["--running", running]
        argv += ["--coder", coder, "--sidebar", on(self.flag(sw.NO_SIDEBAR)),
                 "--switcher", on(self.flag(sw.NO_SWITCHER)),
                 "--model-check", on(self.flag(sw.NO_MODEL_CHECK)),
                 "--web-search", web, "--lsp", on(self.flag(sw.NO_LSP) or (v.get(sw.LSP) or "1") == "0"),
                 "--background", on(self.flag(sw.NO_BACKGROUND_SUBAGENTS)),
                 "--reminder", on(self.flag(sw.NO_REMINDER)),
                 "--coder-thinking", v.get(sw.CODER_THINKING, ""),
                 "--coder-model", v.get(sw.CODER_MODEL, ""),
                 "--coder-model-thinking", v.get(sw.CODER_MODEL_THINKING, ""),
                 "--coder-tests", v.get(sw.CODER_TESTS) or sw.DEFAULTS[sw.CODER_TESTS],
                 "--request-check", v.get(sw.CODER_REQUEST_CHECK) or sw.DEFAULTS[sw.CODER_REQUEST_CHECK],
                 "--run-gate", on((v.get(sw.CODER_RUN_GATE) or sw.DEFAULTS[sw.CODER_RUN_GATE]) == "off"),
                 "--fix-rounds", v.get(sw.CODER_FIX_ROUNDS) or sw.DEFAULTS[sw.CODER_FIX_ROUNDS],
                 "--free-form", v.get(sw.CODER_FREE_FORM) or sw.DEFAULTS[sw.CODER_FREE_FORM],
                 "--profile", on(self.flag("NO_PROFILE")),
                 "--cache", on(self.flag(sw.NO_CACHE) or self.flag("NO_PREFIX_CACHE")),
                 "--browser", on(self.flag(sw.NO_BROWSER)),
                 "--browser-headed", "1" if self.flag(sw.BROWSER_HEADED) else "0"]
        if self.ports.configure is not None:
            return self.ports.configure(argv)
        from . import configure
        return run_main(configure.main, argv, self.out, self.err, configure_columns(self.v, self.out),
                        os.path.join(self.here, "configure.py"))

    # -- the sync
    def sync(self) -> None:
        """This install's switches, so a sync (a config that the dashboard sends) applies them again; and on a
        computer whose server is elsewhere (remote.json), the sync service: one outgoing connection to the dashboard
        API (no port opens here) that applies each new config with this step. launchd (macOS), systemd --user
        (Linux) or Task Scheduler (Windows) keeps it running. Off: NO_SYNC_SERVICE=1."""
        conf = os.path.join(self.home, ".config", "carl")
        try:
            os.makedirs(conf, mode=0o700, exist_ok=True)
            write_private(os.path.join(conf, "client-install.env"),
                          "".join(f"{k}={self.v[k]}\n" for k in sw.SWITCHES if self.v.get(k)))
        except OSError:
            pass
        services = self.ports.services
        service = services[0]                             # this OS's
        no_service = self.flag("NO_SYNC_SERVICE")
        if self.mode == "local" or not nonempty(self.remote) or no_service:
            for svc in services:
                if svc.remove():
                    self.echo("  removed   the client sync service")
            self.register(False)
            if self.mode != "local" and no_service:
                self.say("Client sync: no sync service (NO_SYNC_SERVICE=1). OpenCode and Pi check for a new config "
                         "when they start.")
            return
        script = os.path.join(self.here, SYNC_SCRIPT)
        py = self.ports.interpreter.command()
        ok = service.available() and service.install(py, script)
        if ok:
            self.register(True)
            self.say(f"Client sync: a background service ({service.title}) keeps one connection to the dashboard on "
                     f"the server. It applies each new config that the dashboard sends. It opens no port on this "
                     f"computer. /carl in OpenCode or Pi shows its state. To remove it, use NO_SYNC_SERVICE=1.")
            if self.ports.platform.startswith("linux") and self.linger_off():     # systemd --user lingers only there
                self.say("Note: the service runs only while you are logged in. To keep it running, for example in a VM "
                         "that you reach with SSH, run this command:")
                self.echo(f"  loginctl enable-linger {self.user()}")
        else:
            self.register(False)
            self.say("Client sync: no background service. This computer has no service manager, or the service did "
                     "not start. OpenCode and Pi check for a new config when they start.")

    def register(self, on: bool) -> None:
        """client-sync.json: the sync service is installed or not, the client's folder, and the Python that the
        plugins run carl-sync.py with (carl-sync.py register did this before). Errors are ignored, as before."""
        try:
            from ..adapters.sync_files import SyncFiles
            from .sync import os_lock
            store = SyncFiles(os.path.join(self.home, ".config", "carl"), self.here, os_lock())
            store.update(service=on, bundle=self.here, python=self.ports.interpreter.command())
        except Exception:                                   # noqa: BLE001 (`carl-sync.py register 2>/dev/null || true`)
            pass

    def user(self) -> str:
        p = _output(["id", "-un"], self.env)
        return p.strip() if p is not None else ""

    def linger_off(self) -> bool:
        if not shutil.which("loginctl", path=self.env.get("PATH", "")):
            return False
        out = _output(["loginctl", "show-user", self.user(), "-p", "Linger", "--value"], self.env)
        return out is not None and out.strip() == "no"

    # -- the smoke test
    def smoke(self) -> None:
        self.echo()
        body = self.api("/v1/models")
        if body is not None:
            try:
                first = str(json.loads(body)["data"][0]["id"])
            except (TypeError, ValueError, KeyError, IndexError):
                first = ""
            has = f". It has {first}" if first else ""
            self.say(f"OK: the server answers at http://{self.host}:{self.port}/v1{has}.")
        else:
            self.say(f"Note: no server runs at http://{self.host}:{self.port}. Start it on {self.d.server} with "
                     f"{COMMAND}.")


def ktok(n: int) -> str:
    """Tokens as K (1024 tokens)."""
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n / 1024:.1f}K"


def _has(path: str, needle: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            return needle in f.read()
    except OSError:
        return False


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def _output(argv: List[str], env: Mapping[str, str]) -> Optional[str]:
    try:
        p = subprocess.run(argv, env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout


def write_private(path: str, text: str) -> None:
    """A file that only this user can read when it is new (umask 077); an existing file keeps its mode."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def copy_private(src: str, dest: str) -> None:
    """A copy of a file, byte for byte, made as write_private makes a file."""
    with open(src, "rb") as f:
        data = f.read()
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def configure_columns(environ: Mapping[str, str], out: IO[str]) -> int:
    """The width configure's report wraps to, as a child of the bash script saw it: COLUMNS, else its stdout's
    terminal, else 80."""
    try:
        n = int(environ.get("COLUMNS", ""))
    except ValueError:
        n = 0
    if n > 0:
        return n
    if _isatty(out):
        try:
            return os.get_terminal_size(out.fileno()).columns
        except (AttributeError, OSError, ValueError):
            pass
    return DEFAULT_WIDTH


@contextlib.contextmanager
def _columns(n: int) -> Iterator[None]:
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = str(n)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = old


def run_main(main: Callable[[List[str]], int], argv: List[str], out: IO[str], err: IO[str], columns: int,
             prog: str) -> int:
    """A main() of this package in this process, as the script PROG (its usage line names it), with its output on
    out and err; its exit code (also from SystemExit: argparse's errors)."""
    saved = sys.argv
    sys.argv = [prog, *argv]
    try:
        with _columns(columns), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = main(argv)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
                if not isinstance(e.code, (int, type(None))):
                    err.write(f"{e.code}\n")
    finally:
        sys.argv = saved
    out.flush()
    return int(rc or 0)


def build_ports(environ: Mapping[str, str], platform: str = sys.platform) -> Ports:
    """This computer's adapters for the step: the sync service's manager is the OS's (on macOS and Linux the other
    one's file is taken out too, as before)."""
    from ..adapters.interpreter import Interpreter
    from ..adapters.launchd import Launchd
    from ..adapters.local_addrs import LocalAddrs
    from ..adapters.systemd import SystemdUser
    from ..adapters.task_scheduler import TaskScheduler
    home = environ.get("HOME") or os.path.expanduser("~")
    services: Tuple[ServiceManager, ...]
    if platform == "win32":
        user = environ.get("USERNAME", "")
        # the account's domain; a PC in a workgroup names its local accounts by the computer (an ssh session, W7.5 on
        # the Windows test PC, says WORKGROUP there, which Task Scheduler cannot map to an account)
        domain = environ.get("USERDOMAIN", "")
        if domain.upper() == "WORKGROUP":
            domain = environ.get("COMPUTERNAME", "")
        services = (TaskScheduler(home, f"{domain}\\{user}" if domain and user else user),)
    elif platform == "darwin":
        services = (Launchd(home), SystemdUser(home))
    else:
        services = (SystemdUser(home), Launchd(home))
    return Ports(addrs=LocalAddrs(), interpreter=Interpreter(environ), services=services, platform=platform)


def step(environ: Mapping[str, str], here: str, out: IO[str], err: IO[str], argv: Optional[List[str]] = None,
         stdin: Optional[IO[str]] = None, ports: Optional[Ports] = None) -> int:
    """One run of the config step; its exit code."""
    s = Step(argv or [], environ, here, out, err, stdin, ports or build_ports(environ))
    try:
        return s.run()
    finally:
        out.flush()
        err.flush()


def main(argv: List[str], environ: MutableMapping[str, str], here: str) -> int:
    """carl_setup.py install."""
    return step(environ, here, sys.stdout, sys.stderr, argv)


class Tee(io.TextIOBase):
    """A stream that writes to another one and keeps what it wrote (the bash setup's tee)."""

    def __init__(self, to: IO[str]) -> None:
        super().__init__()
        self.to = to
        self.kept = io.StringIO()

    def write(self, s: str) -> int:                       # type: ignore[override]
        self.to.write(s)
        self.kept.write(s)
        return len(s)

    def flush(self) -> None:
        self.to.flush()

    def isatty(self) -> bool:
        return False

    def text(self) -> str:
        return self.kept.getvalue()

"""The client package (carl package, and the dashboard's Connect > Setup): one zip of the client folder.
A new computer installs OpenCode and Pi from it with ./setup. Pure: the CARL version, the name of the zip,
the files that go in and their modes, the check that other computers can use the server, and the text that
the CLI and the dashboard show. The use case is application/package.py; its I/O (the file list, the zip) is in
adapters/common/."""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ..shared.platform_words import BY_OS, MACOS, PlatformWords

FOLDER = "carl-client"                      # the one folder in the zip: unzip gives carl-client/setup
# The platform words of the client's texts. Written from PlatformWords, never by hand: into each package, and into a
# clone's client folder by a development tool (a test checks both).
WORDS_FILE = "carl_client/data/words.json"
# The files the package makes itself (the server's, and the words), never from the file list.
SERVER_FILES = ("remote.json", "api-key", "installed-models.json")      # the server's own files in client/
GENERATED = SERVER_FILES + ("VERSION", WORDS_FILE)
# always in the package (also before they are in git): the user's entries (macOS and Linux, a double click on a Mac,
# Windows) and the Python setup they run
ENTRY_POINTS = ("setup", "setup.command", "setup.cmd", "carl_setup.py")
EXECUTABLE = frozenset(ENTRY_POINTS + ("carl-sync.py",))
PRIVATE = frozenset(("api-key", "remote.json"))                               # 0600 in the zip and after unzip
LOCAL_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))
# Never in a package, even when git tracks it: caches, backups, Finder and editor files.
_SKIP = re.compile(r"(^|/)(__pycache__|node_modules|\.mypy_cache|\.pytest_cache)(/|$)|(^|/)\.DS_Store$|\.pyc$"
                   r"|\.bak\.|\.before-carl$|\.tmp$|~$")
_RELEASE = re.compile(r"^##\s+v?(\d+\.\d+\.\d+)\b", re.M)
_DESCRIBE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?)$")


@dataclass(frozen=True)
class Remote:
    """client/remote.json: what the server wrote at its last start."""
    host: str
    port: int
    cache_api: str
    version: str = ""


def parse_remote(doc: object) -> Optional[Remote]:
    """remote.json's content, or None when it is not a valid connection file."""
    if not isinstance(doc, dict):
        return None
    host, port, api = doc.get("host"), doc.get("port"), doc.get("cache_api")
    if not (isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", host)
            and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
        return None
    version = doc.get("version")
    return Remote(host, port, api if isinstance(api, str) else f"http://{host}:{port + 1}",
                  version if isinstance(version, str) else "")


def version_from_changelog(text: str) -> Optional[str]:
    """The newest released version: the first "## X.Y.Z" heading (not "## Unreleased")."""
    m = _RELEASE.search(text)
    return m.group(1) if m else None


def version_from_describe(out: str) -> Optional[str]:
    """`git describe --tags` output ("v1.6.0", "v1.6.0-3-gabc1234") as a version, or None."""
    m = _DESCRIBE.match(out.strip())
    return m.group(1) if m else None


def safe_host(name: str) -> str:
    """A host name or an address as a part of a file name: letters, digits, dots and hyphens only."""
    name = name.strip().strip("[]")
    if not re.fullmatch(r"[0-9.]+|[0-9A-Fa-f:]+", name):
        name = name.split(".", 1)[0]                 # a host name: its first label ("mac" of "mac.local")
    return re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip(".-")[:63] or "server"


def zip_name(version: str, host: str) -> str:
    return f"carl-client-{re.sub(r'[^A-Za-z0-9.+-]+', '-', version) or 'unknown'}-{safe_host(host)}.zip"


def is_local(host: str) -> bool:
    """Only this computer can reach a server on this address."""
    return host.strip("[]").lower() in LOCAL_HOSTS


def keep(rel: str) -> bool:
    """A file of the client folder (path relative to it) that goes into the package from the file list."""
    return bool(rel) and not rel.startswith(("/", "../")) and not _SKIP.search(rel) and rel not in GENERATED


def share_keep(rel: str) -> bool:
    """A tracked file of the whole repository (path relative to its root) that goes into the share zip
    (a development tool): the package's skip rule (no cache, backup or editor file), and no server file of any folder
    (the key, the connection file, the model list), even when the repository tracks it by mistake."""
    return (bool(rel) and not rel.startswith(("/", "../")) and not _SKIP.search(rel)
            and rel.rsplit("/", 1)[-1] not in SERVER_FILES)


def mode_of(rel: str, executable: bool) -> int:
    """The file's mode in the zip: the key and the connection file 0600, the scripts 0755, others 0644."""
    if rel in PRIVATE:
        return 0o600
    return 0o755 if executable or rel in EXECUTABLE else 0o644


def select(tracked: Sequence[str]) -> List[str]:
    """The files of the package from the file list (paths relative to client/): sorted, no duplicate, the entry
    points always in, the generated files never (the package adds them from the server)."""
    return sorted(set(r for r in tracked if keep(r)) | set(ENTRY_POINTS))


# The note of the words file (data/words.json): PlatformWords writes it (a development tool in a clone), and its
# text names no file of CARL's own (the user never opens them)
_GENERATED = "Written by CARL: do not edit. Each client package gets this file when it is built."


def _sync_words() -> Dict[str, object]:
    return {name: {"installer": w.installer, "service_managers": w.service_managers, "setup_command": w.setup_command}
            for name, w in BY_OS.items()}


def client_words(words: PlatformWords) -> Dict[str, object]:
    """The platform words the client takes (data/words.json), from PlatformWords: machine, the server's computer in a
    sentence of the config step (words: the server's own); server, the same in a sentence of the client's own texts
    (the server Mac); host, it in the provider's name (Mac host); and sync, per OS, the words of carl-sync.py's help."""
    return {"_generated": _GENERATED, "machine": words.machine, "server": words.server_machine(), "host": words.host_label,
            "sync": _sync_words()}


def clone_words() -> Dict[str, object]:
    """The words file of a clone's client folder: no server is named, as the clone's setup is the one of the computer
    that it runs on (carl install, the dashboard's Install key), so the file holds the three of every OS (by_os) and the
    client takes the ones of its own. A package never has it: it names its server."""
    return {"_generated": _GENERATED,
            "by_os": {name: {"machine": w.machine, "server": w.server_machine(), "host": w.host_label}
                      for name, w in BY_OS.items()},
            "sync": _sync_words()}


def client_words_text(words: Optional[PlatformWords] = None) -> str:
    """client_words() as the file's text; clone_words() when no server's words are given."""
    return json.dumps(clone_words() if words is None else client_words(words), indent=2) + "\n"


# ---------------------------------------------------------------- what the user reads (ASD-STE100)
@dataclass(frozen=True)
class Outcome:
    """The result of `package`: the zip ("" when CARL wrote none), the error that stopped it ("" when none),
    and the paragraphs to show after it (how to correct the error, or the key warning and the next steps). In a
    paragraph, the lines after a newline are commands: show them as they are, indented, not wrapped."""
    path: str
    error: str
    notes: Tuple[str, ...]
    files: int = 0
    size: int = 0


def net_setting(cfg: Mapping[str, object]) -> Tuple[str, str]:
    """Your settings llama.net and llama.host ("" when not set)."""
    llama = cfg.get("llama")
    if not isinstance(llama, dict):
        return "", ""
    net, host = llama.get("net"), llama.get("host")
    return (net if isinstance(net, str) else ""), (host if isinstance(host, str) else "")


def local_refusal(remote: Remote, cfg: Mapping[str, object], words: PlatformWords = MACOS) -> Outcome:
    """The server serves only this Mac: no package (other computers cannot reach it), and how to change it."""
    cmd, here = words.command, words.machine
    net, host = net_setting(cfg)
    if net == "vm" or (host and not is_local(host)):
        how = (f"Your settings already use another network ({'llama.host = ' + host if host else 'llama.net = vm'}), "
               f"but the server started with only {here}. Start the server again, then run {cmd} package again.")
    else:
        how = (f"To serve a VM or another computer, change the network: in the dashboard, Settings > Server > "
               f"Network (vm), or run {cmd} config set llama.net vm. Then start the server again ({cmd}, or "
               f"{cmd} --vm one time), so that remote.json has an address that the other computer can reach. "
               f"Then run {cmd} package again.")
    return Outcome("", f"error: the server serves only {here} ({remote.host}). Other computers cannot reach it, "
                       f"so CARL did not make the package. CARL changed nothing.",
                   (how, f"To make the package for {here} only (for example for a test), add --anyway."))


def missing_refusal(missing: Sequence[str], client_dir: str, words: PlatformWords = MACOS) -> Outcome:
    """remote.json or api-key is not in the client folder: the server never started from this folder."""
    cmd = words.command
    names = " and ".join(missing)
    return Outcome("", f"error: the client folder ({client_dir}) has no {names}. The server writes "
                       f"{'them' if len(missing) > 1 else 'it'} at each start.",
                   (f"Start the server one time ({cmd}), then run {cmd} package again.",))


def done_notes(path: str, remote: Remote, cfg: Mapping[str, object],
               words: PlatformWords = MACOS) -> Tuple[str, ...]:
    """After a package is written: who can use it, the key warning (how to delete the zip), how to use it. The
    commands are for the server's shell (rm, or del on Windows); the other computer can run any of the three OSes."""
    cmd, here = words.command, words.machine
    name = re.split(r"[\\/]", path)[-1]
    notes = [f"The package uses the server at {remote.host}, port {remote.port}. "
             + (f"Only {here} can reach this address." if is_local(remote.host)
                else "Computers that can reach this address can use the server.")]
    net, host = net_setting(cfg)
    if not is_local(remote.host) and net in ("", "local") and not host:
        notes.append(f"Note: your setting llama.net is local, so the next start of the server serves only {here}. "
                     f"To keep this network, set it in Settings > Server > Network, or run "
                     f"{cmd} config set llama.net vm.")
    if words.shell == "cmd":
        notes += [
            f"CAUTION: the package holds the API key of the server. A person with the key can use the server. Keep "
            f"the zip secret. On the other computer, delete the zip after you unzip it: the folder keeps the key in "
            f"api-key. After you copy the zip, delete it on {here}:\ndel \"{path}\"",
            f"To use it on another Windows computer, copy the zip to it (for example with a network share or a USB "
            f"disk), extract it (File Explorer: Extract All), and double-click setup.cmd in the {FOLDER} folder. In a "
            f"terminal:\ntar -xf \"{name}\"\ncd {FOLDER} && setup.cmd",
            f"On macOS or Linux, run:\nunzip {shlex.quote(name)}\ncd {FOLDER} && ./setup",
            "On a Mac, you can also double-click setup.command in the folder. To update the other computer later, "
            "make a new package, extract it over the old folder and run the setup again. Your settings stay, and CARL "
            "makes backups.",
        ]
        return tuple(notes)
    notes += [
        f"CAUTION: the package holds the API key of the server. A person with the key can use the server. Keep the "
        f"zip secret. On the other computer, delete the zip after you unzip it: the folder keeps the key in api-key "
        f"(mode 0600). After you copy the zip, delete it on {here}:\nrm {shlex.quote(path)}",
        f"To use it on the other computer (macOS or Linux), copy the zip to it (for example with scp, AirDrop or a "
        f"USB disk). Then run:\nunzip {shlex.quote(name)}\ncd {FOLDER} && ./setup",
        "On a Mac, you can also double-click setup.command in the folder. To update the other computer later, make "
        "a new package, unzip it over the old folder (unzip -o) and run ./setup again. Your settings stay, and CARL "
        "makes backups.",
    ]
    return tuple(notes)

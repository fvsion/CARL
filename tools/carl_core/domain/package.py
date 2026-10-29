"""The client package (./carl.sh package, and the dashboard's Connect > Setup): one zip of the client folder.
A new computer installs OpenCode and Pi from it with ./setup. Pure: the CARL version, the name of the zip,
the files that go in and their modes, the check that other computers can use the server, and the text that
the CLI and the dashboard show. The I/O (git, the zip) is in carl_core/adapters/client_package.py."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

FOLDER = "carl-client"                      # the one folder in the zip: unzip gives carl-client/setup
GENERATED = ("remote.json", "api-key", "installed-models.json", "VERSION")   # from the server, not from git
ENTRY_POINTS = ("setup", "setup.command")   # always in the package (also before they are in git)
EXECUTABLE = frozenset(ENTRY_POINTS + ("install.sh", "install-clients.sh", "configure.py", "carl-sync.py"))
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


def mode_of(rel: str, executable: bool) -> int:
    """The file's mode in the zip: the key and the connection file 0600, the scripts 0755, others 0644."""
    if rel in PRIVATE:
        return 0o600
    return 0o755 if executable or rel in EXECUTABLE else 0o644


def select(tracked: Sequence[str]) -> List[str]:
    """The files of the package from the file list (paths relative to client/): sorted, no duplicate, the entry
    points always in, the generated files never (the package adds them from the server)."""
    return sorted(set(r for r in tracked if keep(r)) | set(ENTRY_POINTS))


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


def local_refusal(remote: Remote, cfg: Mapping[str, object], cmd: str) -> Outcome:
    """The server serves only this Mac: no package (other computers cannot reach it), and how to change it."""
    net, host = net_setting(cfg)
    if net == "vm" or (host and not is_local(host)):
        how = (f"Your settings already use another network ({'llama.host = ' + host if host else 'llama.net = vm'}), "
               f"but the server started with only this Mac. Start the server again, then run {cmd} package again.")
    else:
        how = (f"To serve a VM or another computer, change the network: in the dashboard, Settings > Server > "
               f"Network (vm), or run {cmd} config set llama.net vm. Then start the server again ({cmd}, or "
               f"{cmd} --vm one time), so that remote.json has an address that the other computer can reach. "
               f"Then run {cmd} package again.")
    return Outcome("", f"error: the server serves only this Mac ({remote.host}). Other computers cannot reach it, "
                       f"so CARL did not make the package. CARL changed nothing.",
                   (how, f"To make the package for this Mac only (for example for a test), add --anyway."))


def missing_refusal(missing: Sequence[str], client_dir: str, cmd: str) -> Outcome:
    """remote.json or api-key is not in the client folder: the server never started from this folder."""
    names = " and ".join(missing)
    return Outcome("", f"error: the client folder ({client_dir}) has no {names}. The server writes "
                       f"{'them' if len(missing) > 1 else 'it'} at each start.",
                   (f"Start the server one time ({cmd}), then run {cmd} package again.",))


def done_notes(path: str, remote: Remote, cfg: Mapping[str, object], cmd: str) -> Tuple[str, ...]:
    """After a package is written: who can use it, the key warning (how to delete the zip), how to use it."""
    name = path.rsplit("/", 1)[-1]
    notes = [f"The package uses the server at {remote.host}, port {remote.port}. "
             + ("Only this Mac can reach this address." if is_local(remote.host)
                else "Computers that can reach this address can use the server.")]
    net, host = net_setting(cfg)
    if not is_local(remote.host) and net in ("", "local") and not host:
        notes.append(f"Note: your setting llama.net is local, so the next start of the server serves only this Mac. "
                     f"To keep this network, set it in Settings > Server > Network, or run "
                     f"{cmd} config set llama.net vm.")
    notes += [
        f"CAUTION: the package holds the API key of the server. A person with the key can use the server. Keep the "
        f"zip secret. On the other computer, delete the zip after you unzip it: the folder keeps the key in api-key "
        f"(mode 0600). After you copy the zip, delete it on this Mac:\nrm {shlex.quote(path)}",
        f"To use it on the other computer (macOS or Linux), copy the zip to it (for example with scp, AirDrop or a "
        f"USB disk). Then run:\nunzip {shlex.quote(name)}\ncd {FOLDER} && ./setup",
        "On a Mac, you can also double-click setup.command in the folder. To update the other computer later, make "
        "a new package, unzip it over the old folder (unzip -o) and run ./setup again. Your settings stay, and CARL "
        "makes backups.",
    ]
    return tuple(notes)

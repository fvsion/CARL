"""The client's ports: what carl_client.app needs from the computer it runs on. One adapter each in
carl_client/adapters (the entries wire them: configure.py, carl-sync.py), and a fake in the tests.

  Files            the filesystem as the installer uses it          adapters/files_local.py
  PackageData      the package's own values (data/*.json)            adapters/package_data.py
  UserEnvironment  OpenCode's tool switches in the user's shell      adapters/userenv_profile.py,
                                                                     adapters/userenv_hkcu.py
  DashboardApi     the dashboard API on the server (HTTP)            adapters/dashboard_http.py
  SyncStore        the sync's files: state, switches, remote.json    adapters/sync_files.py
  Installer        the setup's config step, run again                adapters/install_sh.py
  Lock             one sync or switch change at a time               adapters/lock_fcntl.py, adapters/lock_msvcrt.py
  ServiceManager   the sync service at login                         adapters/launchd.py, adapters/systemd.py,
                                                                     adapters/task_scheduler.py
  Programs         node, npm, OpenCode and Pi on PATH (the setup)    adapters/npm.py
  NodeDownload     Node from nodejs.org for this user                adapters/node_dist.py
  LocalNetwork     this computer's addresses and listeners           adapters/local_addrs.py
  Interpreter      the Python of the children and the sync service   adapters/interpreter.py
"""
from __future__ import annotations

from typing import IO, Any, ContextManager, Dict, Iterator, List, Mapping, Optional, Protocol


class Files(Protocol):
    """The filesystem as the installer uses it. Contract C-040."""

    def read(self, path: str) -> Optional[str]:
        """The text of a file; None when it doesn't exist."""

    def exists(self, path: str) -> bool: ...

    def isfile(self, path: str) -> bool: ...

    def isdir(self, path: str) -> bool: ...

    def islink(self, path: str) -> bool: ...

    def realpath(self, path: str) -> str: ...

    def listdir(self, path: str) -> List[str]: ...

    def makedirs(self, path: str) -> None:
        """The folder and its parents (no error when it exists)."""

    def write(self, path: str, text: str) -> None:
        """Through a symlink: the link stays, its target changes."""

    def chmod(self, path: str, mode: int) -> None: ...

    def copy(self, src: str, dest: str) -> None:
        """A copy of a file with its mode and times (a backup)."""

    def copy_into(self, src: str, folder: str) -> None:
        """A copy of a file into a folder, with its mode."""

    def copytree(self, src: str, dest: str) -> None:
        """A copy of a folder (dest must not exist)."""

    def remove(self, path: str) -> None: ...

    def rmdir(self, path: str) -> None: ...

    def rmtree(self, path: str, quiet: bool = True) -> None:
        """Remove a folder; quiet: errors (also a missing folder) are ignored."""


class PackageData(Protocol):
    """The values that ship with the package (client/carl_client/data): parsed JSON by name. Contract C-041."""

    def load(self, name: str) -> Any:
        """data/<name>.json, parsed; ValueError naming the file when it is missing or not JSON."""


class UserEnvironment(Protocol):
    """OpenCode's tool switches where a shell finds them: the env file and the shell profile's pointer to it.
    Contract C-042."""

    def apply(self, text: Optional[str], summary: str, profile: bool, report: Any) -> None:
        """Write the env file (text; None: take it out) and the profiles' block (profile False: only say which line
        to add), each change in the report."""


class DashboardApi(Protocol):
    """The dashboard API on the server, as one client folder reaches it. Contract C-043."""

    def ping(self, api: str, timeout: float = 2.0) -> Optional[str]:
        """What answers /carl/ping (no key): CARL's ID; "" for an older CARL dashboard; None for anything else."""

    def server(self, api: str, key: str, who: str) -> Optional[Any]:
        """/carl/client/server (the server's port now), parsed; None when the dashboard is older."""

    def config(self, api: str, key: str, who: str, etag: str) -> Optional[Mapping[str, Any]]:
        """The published config when it isn't `etag`; None when it is (304)."""

    def server_version(self) -> str:
        """The server's CARL version from the last reply ("" before one)."""

    def events(self, api: str, key: str, who: str) -> Iterator[str]:
        """The event names the dashboard sends (a blank name for its keep-alive comments)."""

    def describe(self, e: BaseException) -> str:
        """An error (of these calls, or of the installer) as the /carl panel shows it: what went wrong, what to do."""

    def no_api(self, api: str) -> Exception:
        """The error when no CARL dashboard answers at api or the next ports."""


class SyncStore(Protocol):
    """The sync's files: its state (client-sync.json), the setup's switches (client-install.env), the server's place
    (remote.json) and key, and the model list the installer reads. Contract C-044."""

    sync_lock: str          # the lock file of one sync or switch change at a time

    def state(self) -> Dict[str, Any]: ...

    def update(self, **kv: Any) -> Dict[str, Any]:
        """The state with kv merged in, written under its own lock (the service, the setup and the plugins write it)."""

    def install_lines(self) -> List[str]:
        """client-install.env's lines ([] when there is none)."""

    def write_install(self, text: str) -> None: ...

    def remote(self) -> Dict[str, Any]: ...

    def write_remote(self, **kv: Any) -> None: ...

    def key(self) -> str:
        """The API key next to the client's files, else in the settings folder; "" when there is none."""

    def write_models(self, doc: Any) -> None:
        """installed-models.json next to the client's files."""

    def slots_file(self) -> str:
        """Where the installer writes the slot count it read (CARL_SLOTS_OUT)."""

    def read_slots(self, path: str) -> Optional[int]:
        """The slot count the installer wrote there (the file goes); None when it wrote none."""


class Installer(Protocol):
    """The setup's config step, run again with these switches; its output goes to the sync's log.
    Contract C-045."""

    def run(self, env: Mapping[str, str], title: str) -> int:
        """The exit code (OSError and subprocess errors pass through)."""

    @property
    def log(self) -> str: ...


class Lock(Protocol):
    """One sync or switch change at a time. Contract C-046."""

    def hold(self, path: str) -> ContextManager[None]:
        """Held while the block runs (another process waits)."""


class ServiceManager(Protocol):
    """The sync service: carl-sync.py watch, started at login and kept running. Contract C-047."""

    @property
    def title(self) -> str:
        """The service as the setup names it ("launchd dev.carl.sync")."""

    def available(self) -> bool:
        """This computer runs the service manager for this user."""

    def install(self, python: str, script: str) -> bool:
        """Write the service's file and start it; False when the service manager refused it."""

    def remove(self) -> bool:
        """True when there was one to take out."""

    def installed(self) -> bool: ...

    def unit_text(self, python: str, script: str) -> str:
        """The service's file as it is written."""


class Programs(Protocol):
    """The programs the setup finds and runs by name on PATH: node, npm, OpenCode and Pi. Contract C-048."""

    def which(self, name: str) -> Optional[str]:
        """The program's path on PATH; None when it is not there."""

    def version_of(self, name: str) -> str:
        """The first X.Y.Z in `NAME --version` ("" when there is none)."""

    def node_version(self) -> str:
        """Node's version ("" when node does not run)."""

    def output(self, argv: List[str], stderr: bool = False) -> Optional[Any]:
        """A program's run with its output read (a CompletedProcess); None when it does not start."""

    def run(self, argv: List[str]) -> int:
        """A program's run with its output on the setup's streams; its exit code."""

    def npm_install(self, prefix: str, pkgs: List[str]) -> int:
        """npm install -g --prefix PREFIX PKGS; npm's exit code."""


class NodeDownload(Protocol):
    """Node from nodejs.org for this user. Contract C-049."""

    def install(self, need: str, prefix: str, out: IO[str]) -> str:
        """Node into PREFIX/lib/nodejs, its programs linked into PREFIX/bin; the folder. NodeError (the line for the
        user) when this computer has no archive or the download's SHA-256 is not the listed one."""


class LocalNetwork(Protocol):
    """This computer's own addresses, as the config step reads them. Contract C-050."""

    def is_ours(self, host: str) -> bool:
        """The address is one of this computer's (a loopback name always is)."""

    def listen_addr(self, port: str) -> str:
        """The address a server on this computer listens on for the port ("" when none)."""


class Interpreter(Protocol):
    """The Python of the client's Python children and of the sync service. Contract C-051."""

    def command(self) -> str:
        """The interpreter's path (python3 on PATH on macOS and Linux, else this Python)."""

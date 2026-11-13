"""One builder for each entry of CARL: what that process runs with.

  build_cli         the command line's entry: its settings folder, words and services
  build_commands    the command line's context (cli/commands: composition/cli.py), for the dispatch and the
                    programs that run one command (the dashboard's jobs call carl.py)
  build_serve       the start: the bootstrap (_boot.py)
  build_start       the start in Python (macOS and Linux; start.py wires it)
  build_dispatch    the command line's dispatch (cli/main.py; dispatch.py wires it)
  build_dashboard   the dashboard (dashboard/__main__.py; its models through Entry.api)
  build_tune        Auto-tune
  build_log_filter  the log filter (the hidden command log-filter)

A builder reads the environment and the platform (rule R6: only the composition does), picks the profile and gives
the entry its platform words, its paths and its services. It builds nothing that does I/O, except that it installs the
domain's thinking rules from the client package's data file (install_thinking): the Carl service is built on its first
use (Entry.app), over the adapters the process shares (wiring.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Mapping, MutableMapping, Optional, Sequence

from ..application.api import Api
from ..application.app import Carl
from ..application.server_file import ServerFileService
from ..application.start_server import StartServer
from ..domain.shared.platform_words import PlatformWords
from . import wiring
from .profiles import Machine, Profile

if TYPE_CHECKING:
    from ..cli.commands.base import Cli
    from ..cli.main import Dispatch


@dataclass
class Entry:
    """What one entry runs with. name: cli, serve, dashboard or tune. Contract C-118."""
    name: str
    profile: Profile
    paths: wiring.CarlPaths
    machine: Machine
    env: Mapping[str, str]
    _app: List[Carl] = field(default_factory=list, repr=False)

    @property
    def words(self) -> PlatformWords:
        return self.profile.words

    def app(self) -> Carl:
        """The Carl service of this entry's settings folder (built on first use)."""
        if not self._app:
            self._app.append(wiring.build_carl(self.paths, self.env, self.words))
        return self._app[0]

    def api(self) -> Api:
        """The typed API of this entry's settings folder (application/api.py), over its Carl service."""
        return Api(self.app(), self.paths.config, self.paths.local)

    def server_file(self) -> ServerFileService:
        return wiring.build_server_file(self.paths.conf_dir)

    def client_dir(self, repo: str = wiring.REPO) -> str:
        """CARL_CLIENT_DIR, else client/ in the clone (repo)."""
        return wiring.client_dir(self.env, repo)

    def llama_program(self, name: str = "llama-server") -> Optional[str]:
        """The path of a llama.cpp program (the machine's llama.cpp folder, else PATH); None when there is none."""
        from ..adapters.gpu.inventory import llama_program
        from .llama import program_ending
        return llama_program(name, self.machine.llama_dir, program_ending())


@dataclass(frozen=True)
class LogFilterEntry:
    """What the log filter runs with: the home folder of its pointer file (~/models/logs/.log-PORT.json)."""
    home: str
    words: PlatformWords

    def run(self, argv: Sequence[str]) -> int:
        """The filter's command line (adapters/common/log_filter.py): it ends when its input ends."""
        from ..adapters.common.log_filter import main
        from ..domain.shared.ansi import strip_ansi
        return main(list(argv), self.home, strip_ansi)


@dataclass
class StartEntry:
    """The start in Python: its StartServer, built for this computer's profile (start.py). Contract C-116."""
    service: StartServer

    def run(self, argv: Sequence[str]) -> int:
        return self.service.run(argv)


def install_thinking() -> None:
    """Give the domain the thinking rules of this process: client/carl_client/data/thinking.json, read through the
    bridge to the client package (rule R18: the values live in data, the domain takes them as input)."""
    from ..adapters import client_bundle
    from ..domain.catalog import thinking
    thinking.install(client_bundle.model_rules().thinking)


def _entry(name: str, env: Optional[Mapping[str, str]], platform: Optional[str]) -> Entry:
    install_thinking()
    env = os.environ if env is None else env
    home = os.path.expanduser("~")
    prof = wiring.profile(env, platform)
    return Entry(name, prof, wiring.CarlPaths.from_env(env, home), wiring.machine(env, home, prof.os), env)


def build_cli(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Entry:
    return _entry("cli", env, platform)


def build_serve(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Entry:
    return _entry("serve", env, platform)


def build_dashboard(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Entry:
    return _entry("dashboard", env, platform)


def build_tune(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Entry:
    return _entry("tune", env, platform)


def build_commands(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Cli:
    """The command line's context for this computer: the commands of cli/commands run with it."""
    from . import cli
    e = _entry("cli", env, platform)
    return cli.build(e.env, e.profile, e.machine, os.path.expanduser("~"))


def build_log_filter(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> LogFilterEntry:
    env = os.environ if env is None else env
    return LogFilterEntry(os.path.expanduser("~"), wiring.words(env, platform))


def build_start(env: Optional[MutableMapping[str, str]] = None, platform: Optional[str] = None) -> StartEntry:
    """The start of llama-server in this process (macOS and Linux); env: its environment (default os.environ,
    which the start changes as the launcher did: Homebrew's folder on PATH)."""
    from . import start
    env = os.environ if env is None else env
    e = _entry("serve", env, platform)
    return StartEntry(start.build(env, e.profile, e.machine, os.path.expanduser("~")))



def build_dispatch(env: Optional[MutableMapping[str, str]] = None, platform: Optional[str] = None) -> Dispatch:
    """The dispatch of the command line for this computer (the bootstrap's step 7). The start and the log filter
    it runs are built when a command asks for them."""
    from . import dispatch
    env = os.environ if env is None else env
    e = _entry("serve", env, platform)
    return dispatch.build(env, e.profile, e.machine, os.path.expanduser("~"),
                          lambda argv: build_start(env, platform).run(argv),
                          lambda argv: build_log_filter(env, platform).run(argv))

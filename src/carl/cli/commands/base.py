"""What every command runs with (Cli), what the composition gives it that no port covers (CliTools), and the shared
helpers of the commands: the error of a command (ConfigError: one `error:` line, exit 1), the usage error, and paths
written with ~.

The composition builds the Cli of this computer (composition/cli.py): the platform words, the profile's OS (which
commands the help shows), COLUMNS, the settings folder's files, and the API and the services, each built on its first
use. The commands print their own text; they read no environment and no platform (rule R6) and import no adapter
(rule R7).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

from ...application.api import Api
from ...application.client_sync import ClientSync
from ...application.disk_cache import DiskCache
from ...application.llama_install import LlamaInstall
from ...application.server_file import ServerFileService
from ...domain.clients.package import Outcome
from ...domain.shared.constants import DEFAULT_PORT
from ...domain.shared.errors import ConfigError
from ...domain.shared.platform_words import PlatformWords
from ...ports import EngineAdapter, PluginHost
from ...ui.layout import width

if TYPE_CHECKING:
    from .gpu import GpuFacts
    from .tune import TuneTools

EXIT_ERROR = 1                                   # a refused or failed command: one "error:" line on stderr


@dataclass
class CliTools:
    """What the commands need that no port covers yet, from the composition:
      carl_id          this CARL's ID (made once in the settings folder)
      engine           the engine adapter (WCLI): its version and its own help (engine version, engine help)
      server_file      the server file service of the settings folder (server-PORT.json)
      disk_cache       the disk cache of the settings folder
      client_sync      the client config the sync of other computers reads
      make_package     the client package (out_dir: None for dist/ in the clone; anyway) -> its outcome
      log_file         the log of the server on PORT (its filter's pointer), else the latest log: a path
      gpu              the gpu command's report (cli/commands/gpu.py GpuFacts), offline or not
      tune             what Auto-tune runs with (cli/commands/tune.py TuneTools)
      paths            the product's paths for `carl paths --json`
      llama_install    the install of the pinned llama.cpp build (Windows and Linux)
      read_secret      a secret from standard input when it is piped, else asked for with nothing shown as it is
                       typed (`carl hf token set`); the argument is the question"""
    carl_id: Callable[[], str]
    engine: Callable[[], EngineAdapter]
    server_file: Callable[[], ServerFileService]
    disk_cache: Callable[[], DiskCache]
    client_sync: Callable[[], ClientSync]
    make_package: Callable[[Optional[str], bool], Outcome]
    log_file: Callable[[], str]
    gpu: Callable[[bool], GpuFacts]
    tune: Callable[[], TuneTools]
    paths: Callable[[], Dict[str, str]]
    llama_install: Callable[[], LlamaInstall]
    read_secret: Callable[[str], str] = lambda question: ""


@dataclass
class Cli:
    """One command line's context. words: the platform words of its texts; os: the profile's OS (macos, windows or
    linux: which commands its help shows); columns: COLUMNS ("" when it is not set); home: the user's home;
    config_file and local_file: config.json and models.json of the settings folder; api: the API, built on first
    use; tools: CliTools; port: the port a start uses now (the help names it, A46)."""
    words: PlatformWords
    os: str
    columns: str
    home: str
    config_file: str
    local_file: str
    build_api: Callable[[], Api]
    tools: CliTools
    port: int = DEFAULT_PORT            # the port a start uses now (the variable PORT, else the default): the help's
    _api: List[Api] = field(default_factory=list, repr=False)

    def api(self) -> Api:
        if not self._api:
            self._api.append(self.build_api())
        return self._api[0]

    def plugins(self) -> Optional[PluginHost]:
        """The plugins that are installed (None: this Cli has no plugin seam, as the Carl of a test)."""
        return self.api().carl.plugins

    def width(self) -> int:
        return width(self.columns)

    def tilde(self, path: str) -> str:
        """path with the home folder written ~."""
        home = self.home
        return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def arg(argv: Sequence[str], i: int, usage: str) -> str:
    """The i-th argument, else the usage error (ConfigError: "usage: carl.py USAGE")."""
    if len(argv) <= i:
        raise ConfigError(f"usage: carl.py {usage}")
    return argv[i]


def unknown_model(name: str, words: PlatformWords) -> str:
    return f"unknown model '{name}'. {words.command} models lists the models."


def count(n: int, one: str, many: str = "") -> str:
    """1 file, 2 files."""
    return f"{n} {one if n == 1 else (many or one + 's')}"

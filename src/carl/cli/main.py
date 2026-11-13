"""The command line's dispatch (W3.4): host/serve.sh's, in Python. The bootstrap (_boot.py) calls main(); the
composition (composition/dispatch.py) builds the Dispatch for this computer's profile.

The order is the bash dispatch's, and so are the texts and the exit codes:

  1. `log-filter` (hidden) is the log filter. `--version` first, or in a start's arguments, is CARL's version: one
     line, on every OS, and nothing is read or written in the settings folder (WFIN). On Windows the commands of the table that run there run here too (W4.1),
     and the help; every other command goes to the profile's Windows path (the profile gives the Dispatch a `preview`
     callable: it runs the start here and answers what does not run on Windows yet with an error, W4.2; the
     table says where each command runs; the cli code has no platform check).
  2. The settings folder's old name is moved before anything reads it: not for help, nor for a command that does not
     exist (ConfDirMigration; only the macOS profile has one).
  3. No command: attach to a server that runs, else the first run (application/first_run.py), then a start.
  4. `help [TOPIC]`, `-h`, `--help`; the removed commands `grant` and `pocket`, and the words that named an engine
     (`llama`, `llama-install` off the Mac, `llama-version`, `--help-adv`: decision A39), each an error line that names
     the command in its place, exit 2.
  5. The help hijack: `COMMAND -h` or `COMMAND --help` is that command's help (for a start, the start topic).
  6. The command: a command of the table (cli/commands/__init__.py: fit, models, engine, ... and the hidden ones) runs
     in this process (W4.1); install runs the client setup and the dashboard its program, each in place of this
     process; `start` or a leading flag is the start (in this process). `--help-adv` in a start's arguments is the
     removed word for `engine help`. Anything else: an error and exit 2.

Contract C-401.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Sequence, Tuple, cast

from ..application.first_run import DASHBOARD, EXIT, FirstRun
from ..domain.server import dispatch_text as dt
from ..domain.shared.constants import DEFAULT_PORT
from ..domain.shared.contracts import require
from ..domain.shared.errors import FolderAccessError
from ..domain.shared.platform_words import PlatformWords
from ..ports import ConfDirMigration
from . import commands

LOG_FILTER = "log-filter"
START = "start"
HELP = ("help", "-h", "--help")
REMOVED = ("grant", "pocket")                       # the MTPLX presets, removed in CARL 1.2.0
HELP_FLAGS = ("-h", "--help")
HELP_ADV = dt.HELP_ADV                              # removed (A39): the error names `engine help`
VERSION = "--version"                               # CARL's version (WFIN): one line, nothing else runs
NO_START = "--no-start"
DASHBOARD_WORDS = ("dashboard", NO_START)           # the dashboard on the server's port
MONITOR = "monitor"
INSTALL = "install"
# The commands that no migration comes before: help, and the commands that do not exist.
NO_MIGRATION = HELP + (HELP_ADV, VERSION) + REMOVED + tuple(dt.REMOVED_ENGINE_WORDS)

SETUP = "client/carl_setup.py"                      # the client setup (client/setup is its shell shim)

# The commands of the table, run in this process (cli/commands/__init__.py), shown and hidden.
TABLE: Tuple[str, ...] = tuple(c.name for c in commands.COMMANDS + commands.HIDDEN)
# Every word the dispatch compares the first argument with (the contract test reads this list).
COMMANDS: Tuple[str, ...] = (tuple(HELP) + (HELP_ADV, VERSION) + REMOVED + DASHBOARD_WORDS + (MONITOR, INSTALL, START)
                             + TABLE)
MIGRATING = (("",) + DASHBOARD_WORDS + (MONITOR, INSTALL, START) + TABLE)


@dataclass
class DispatchTools:
    """What the dispatch needs that no port covers yet (the composition fills it in; W5 moves the dashboard in):
      err             a line on stderr (flushed);
      exec_tool       a program of CARL's (a path in the clone: the client setup) with its arguments, in place of
                      this process; it returns only when the program cannot run (127);
      help_topic      the help of a topic (in this process); the exit code (2: no such topic);
      dashboard       the dashboard with its arguments, in place of this process (127 when it cannot run);
      command         a command of the table with its arguments, in this process; its exit code;
      runs_here       whether a word is a command of the table that runs here (on this computer's OS);
      plugin_command  whether a word is a hidden command of a plugin (asked last, of a word nothing else takes), and
                      run_plugin_command runs it with its arguments (WP; None: no plugin seam)."""
    err: Callable[[str], None]
    exec_tool: Callable[[str, Sequence[str]], int]
    help_topic: Callable[[str], int]
    dashboard: Callable[[Sequence[str]], int]
    command: Callable[[str, Sequence[str]], int]
    runs_here: Callable[[str], bool]
    plugin_command: Optional[Callable[[str], bool]] = None
    run_plugin_command: Optional[Callable[[str, Sequence[str]], int]] = None


class Dispatch:
    """One command line. start, log_filter: the commands that run in this process (argv: the options after the
    command word); preview: the Windows path, which gets the start and the commands that do not run there (it runs
    the start, or says that a command is not available);
    first_run: None where there is a preview; os_name: the profile's OS (llama-install is removed off the Mac only);
    version: CARL's version (the VersionSource, C-029), asked only by `--version`."""

    def __init__(self, words: PlatformWords, env: Mapping[str, str], migrations: Sequence[ConfDirMigration],
                 first_run: Optional[FirstRun], tools: Optional[DispatchTools],
                 start: Callable[[Sequence[str]], int], log_filter: Callable[[Sequence[str]], int],
                 preview: Optional[Callable[[Sequence[str]], int]] = None, os_name: str = "macos",
                 version: Optional[Callable[[], str]] = None) -> None:
        self.words, self.env, self.migrations = words, env, migrations
        self.first_run, self.tools, self.start, self.log_filter, self.preview = (
            first_run, tools, start, log_filter, preview)
        self.os_name, self.version = os_name, version

    def run(self, argv: Sequence[str]) -> int:
        """The exit code of the command line. A command that replaces this process does not return. Ctrl-C while
        the first run works (Auto fit's pick, a download) ends it with 130, as the shell's did."""
        try:
            return self._run(list(argv))
        except KeyboardInterrupt:
            return 130
        except FolderAccessError as e:              # Windows refuses one of CARL's folders: the message says what to run
            print(f"error: {e.strerror}", file=sys.stderr, flush=True)
            return 1

    def _run(self, args: List[str]) -> int:
        if args[:1] == [LOG_FILTER]:
            return self.log_filter(args[1:])
        if self._asks_version(args):
            return self._print_version()
        if self.preview is not None:
            return self._preview(args, self.preview)
        require(self.tools is not None and self.first_run is not None, "C-401",
                "a dispatch without a preview has its tools and its first run")
        t, first_run = cast(DispatchTools, self.tools), cast(FirstRun, self.first_run)
        if self._migrates(args):
            for m in self.migrations:
                if m.pending():
                    for line in m.migrate():
                        t.err(line)
        port = self.env.get("PORT") or str(DEFAULT_PORT)
        if not args:
            step = first_run.run(port)
            if step.kind == DASHBOARD:
                return t.dashboard(["--port", port])
            if step.kind == EXIT:
                return step.code
            args = [START]
        word = args[0]
        if word in HELP:
            return t.help_topic(self._topic(args[1] if len(args) > 1 else ""))
        if word in REMOVED:
            t.err(dt.removed_command(word, self.words))
            return 2
        if dt.is_removed_engine_word(word, self.os_name):
            t.err(dt.removed_engine_command(word, self.words))
            return 2
        for a in args[1:]:
            if a in HELP_FLAGS:
                flag = word.startswith("-") and word != NO_START
                return t.help_topic(self._topic(START if flag else word))
        if word in DASHBOARD_WORDS:
            return t.dashboard(["--port", port] + args[1:])
        if word == INSTALL:
            return t.exec_tool(SETUP, args[1:])
        if word == MONITOR:
            return t.dashboard(args[1:])
        if t.runs_here(word):
            return t.command(word, args[1:])
        if word == START:
            options = args[1:]
        elif word.startswith("-"):
            options = args                                  # a leading flag means llama
        elif t.plugin_command is not None and t.run_plugin_command is not None and t.plugin_command(word):
            return t.run_plugin_command(word, args[1:])    # a hidden command of a plugin (WP): after every other word
        else:
            t.err(dt.unknown_command(word, self.words))
            return 2
        if HELP_ADV in options:
            t.err(dt.removed_engine_command(HELP_ADV, self.words))
            return 2
        return self.start(options)

    def _preview(self, args: List[str], preview: Callable[[Sequence[str]], int]) -> int:
        """Windows: the help and the commands of the table that run there, here; the rest (the start, no command, a
        plugin's command, the dashboard, Auto-tune, the client setup) goes to the profile's Windows path, which runs
        the start in this process (W6.4). Nothing moves (no migration)."""
        t = self.tools
        word = args[0] if args else ""
        if t is None:
            return preview(args)
        if word in HELP:
            return t.help_topic(self._topic(args[1] if len(args) > 1 else ""))
        if dt.is_removed_engine_word(word, self.os_name) or (HELP_ADV in args and self._starts(word)):
            t.err(dt.removed_engine_command(word if word in dt.REMOVED_ENGINE_WORDS else HELP_ADV, self.words))
            return 2
        if t.runs_here(word):
            if any(a in HELP_FLAGS for a in args[1:]):
                return t.help_topic(word)
            return t.command(word, args[1:])
        return preview(args)

    def _print_version(self) -> int:
        """CARL's version, one line on stdout (WFIN): before the Windows path and the move, so nothing else runs."""
        require(self.version is not None, "C-401", "a dispatch has CARL's version")
        print(dt.version_line(cast(Callable[[], str], self.version)()), flush=True)
        return 0

    @classmethod
    def _asks_version(cls, args: Sequence[str]) -> bool:
        """`--version` first, or in a start's arguments (a start would give it to llama-server, which prints its own
        version only after the start wrote its files)."""
        word = args[0] if args else ""
        start = cls._starts(word) and word not in HELP + (HELP_ADV,)
        return word == VERSION or (start and VERSION in args[1:])

    @staticmethod
    def _starts(word: str) -> bool:
        """The word is a start: `start`, or a leading flag."""
        return word == START or (word.startswith("-") and word != NO_START)

    @staticmethod
    def _migrates(args: Sequence[str]) -> bool:
        """The settings folder's old name moves before a command reads it: any command and any flag, not help."""
        word = args[0] if args else ""
        if word in NO_MIGRATION:
            return False
        return word in MIGRATING or word.startswith("-")

    @staticmethod
    def _topic(topic: str) -> str:
        """The help topic of a word: none is the main one, and --no-start is the dashboard's."""
        return "dashboard" if topic == NO_START else (topic or "main")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The command line of this process (the bootstrap's step 7): the dispatch of this computer."""
    from ..composition import entries
    return entries.build_dispatch().run(sys.argv[1:] if argv is None else list(argv))

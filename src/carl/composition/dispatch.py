"""The dispatch's wiring (W3.4): the Dispatch of this computer's profile (cli/main.py), over its adapters.

  every OS      the commands of the table (cli/commands/__init__.py) and the help run in this process, over the
                Cli of this computer (composition/cli.py, W4.1)
  macos, linux  the client setup and the dashboard run as programs of CARL's own (python3 from PATH, as the bash
                dispatch ran them) in place of this process; the start and the log filter run in this process. The
                first run asks the system's adapters (the port, the processes, the dependency check:
                composition/start.py `host`); its pick and its download run `carl.py fit --pick-default` and
                `carl.py download NAME` as children, as the bash dispatch ran its tools.
  windows       the start (no command, `start` or a leading flag) runs in this process too (W6.4: the Windows
                adapters, composition/start.py; it offers to install llama.cpp when none is found), the dashboard
                (`dashboard`, `monitor`, `--no-start`, and no command while a llama-server runs on the port: W6.8, in
                this process, as Windows has no exec) and a plugin's hidden command; `install` runs the client setup
                (client/carl_setup.py) with this Python as a child, in this console, and gives its exit code (W1.6:
                the Connect tab's i and u, the Router panel's u and the install after an Apply run it)

Nothing is built here until build() is called (rule R6); llama-server's own help is found by the locator
(CARL_LLAMA_DIR, else llama.path off the Mac, else PATH).
"""
from __future__ import annotations

import sys
from typing import Callable, MutableMapping, Optional, Sequence

from ..application.first_run import FirstRun, FirstRunTools
from ..application.start_server import LLAMA_SERVER
from ..cli import commands
from ..cli.main import DASHBOARD_WORDS, INSTALL, MONITOR, NO_START, SETUP, Dispatch, DispatchTools
from ..domain.server import dispatch_text as dt
from ..domain.server.launch_plan import is_port
from ..domain.shared.constants import DEFAULT_PORT
from ..domain.shared.errors import ConfigError
from . import cli as cli_wiring
from . import llama as llama_wiring, start, wiring
from .profiles import Machine, Profile

CARL_PY = "carl.py"                                 # the entry, as a program of the clone (CarlPrograms)


def build(env: MutableMapping[str, str], profile: Profile, machine: Machine, home: str,
          run_start: Callable[[Sequence[str]], int], run_filter: Callable[[Sequence[str]], int]) -> Dispatch:
    """The Dispatch of this computer. run_start, run_filter: the commands that run in this process."""
    words = profile.words
    cli = cli_wiring.build(env, profile, machine, home)

    def command(name: str, argv: Sequence[str]) -> int:
        return commands.run_command(cli, name, argv)

    def help_topic(topic: str) -> int:
        return commands.run_help(cli, topic)

    def runs_here(name: str) -> bool:
        return commands.runs_here(name, profile.os)

    def plugin_command(name: str) -> bool:
        return commands.is_plugin_command(cli, name)

    def run_plugin_command(name: str, argv: Sequence[str]) -> int:
        return commands.run_plugin_command(cli, name, argv)

    def version() -> str:
        """CARL's version (`carl --version`): the clone's CHANGELOG, else git describe (VersionSource, C-029)."""
        from ..adapters.common.version import ChangelogVersion
        return ChangelogVersion(wiring.REPO).version()

    if profile.os == "windows":
        from ..adapters.windows import host as windows_host
        from .dashboard import run_here as dashboard_here
        windows_host.vt_output()                    # the texts' colours in a console (the bootstrap leaves it here)

        def preview_run(argv: Sequence[str]) -> int:
            """The start in this process (its own llama.cpp offer first), the dashboard in this process, the client
            setup as a child (`install`), a plugin's hidden command, else the error: what does not run on Windows yet,
            or an unknown command. A start, the dashboard or install with a help flag shows its help, as the dispatch
            does elsewhere (cli/main.py)."""
            word = argv[0] if argv else ""
            asks_help = any(a in ("-h", "--help") for a in argv[1:])
            if (word == "start" or (word.startswith("-") and word != NO_START)) and asks_help:
                return help_topic("start")
            port = env.get("PORT") or str(DEFAULT_PORT)
            if not argv and server_on(port):            # the first run's attach (C-120): a llama-server on the port
                return dashboard_here(["--port", port])
            if word in DASHBOARD_WORDS or word == MONITOR:
                if asks_help:
                    return help_topic(MONITOR if word == MONITOR else "dashboard")
                if word == MONITOR:
                    return dashboard_here(list(argv[1:]))
                return dashboard_here(["--port", port, *argv[1:]])
            if word == INSTALL:
                return help_topic(INSTALL) if asks_help else setup_here(list(argv[1:]))
            if llama_wiring.starts_server(tuple(argv)):
                return run_start(list(argv[1:]) if argv[:1] == ["start"] else list(argv))
            if argv and not argv[0].startswith("-") and plugin_command(argv[0]):
                return run_plugin_command(argv[0], argv[1:])
            _err(dt.not_on_windows(word, words) if word in dt.NOT_ON_WINDOWS else dt.unknown_command(word, words))
            return 2
        def setup_here(args: Sequence[str]) -> int:
            """The client setup with this Python, as a child that shares this console (Windows has no exec): its
            questions work in a terminal, a child of the dashboard gets the defaults; its exit code (127: it cannot
            run)."""
            from ..adapters.posix.start_tools import CarlPrograms
            return CarlPrograms(wiring.REPO, sys.executable).run(SETUP, args)

        def server_on(port: str) -> bool:
            """A llama-server listens on port (another program there is not one), as the first run asks (C-120)."""
            if not is_port(port):
                return False
            system = start.host(env, profile, home, start.place(env, home, profile, machine).key_file)
            pid = system.probe.owner(int(port))
            return pid is not None and any(LLAMA_SERVER in p.command for p in system.table.processes() if p.pid == pid)
        tools = DispatchTools(err=_err, exec_tool=lambda tool, args: 127, help_topic=help_topic,
                              dashboard=dashboard_here, command=command, runs_here=runs_here)
        return Dispatch(words, env, [], None, tools, run_start, run_filter, preview_run, profile.os, version)
    from ..adapters.posix import start_tools
    where = start.place(env, home, profile, machine)
    programs = start_tools.CarlPrograms(wiring.REPO)
    system = start.host(env, profile, home, where.key_file)

    def pick_default() -> Optional[str]:
        """Auto fit's pick (carl.py fit --pick-default): None when it failed or gave nothing."""
        code, name = programs.capture(CARL_PY, ["fit", "--pick-default"])
        return name if code == 0 and name else None

    def model_bytes(name: str) -> str:
        """A model's size in bytes as text ("" when unknown; an unknown model says so on stderr, as the old tool's
        `get NAME bytes` did)."""
        try:
            m = cli.api().find(name)
        except ConfigError as e:
            _err(f"error: {e}")
            return ""
        if not m:
            _err(f"error: unknown model '{name}'. {words.command} models lists the models.")
            return ""
        size = m.get("bytes")
        return "" if size is None else str(size)

    first = FirstRunTools(
        out=start_tools.out, ask=start_tools.ask, is_tty=start_tools.is_tty,
        downloaded=lambda: start.downloaded_names(env, home, words), pick_default=pick_default,
        model_bytes=model_bytes, download=lambda name: programs.run(CARL_PY, ["download", name]))
    tools = DispatchTools(
        err=start_tools.err, exec_tool=programs.exec_tool, help_topic=help_topic, dashboard=programs.dashboard,
        command=command, runs_here=runs_here, plugin_command=plugin_command, run_plugin_command=run_plugin_command)
    return Dispatch(words, env, system.migrations, FirstRun(words, env, system.probe, system.table, system.deps, first),
                    tools, run_start, run_filter, os_name=profile.os, version=version)


def _err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)

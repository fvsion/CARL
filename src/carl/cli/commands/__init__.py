"""The commands of `carl`, one module each, and the table of them: what each runs, where its help shows it and where it
runs in this process. The dispatch (cli/main.py) runs a command of the table in its own process (W4.1: no command
runs a program of its own any more, except the client setup and the dashboard).

  COMMANDS  the commands of the help, in its order. A command's `shown` says on which OS the help lists it (gpu: not
            on a Mac), its `runs` where it runs here (every command runs on every OS since W6.9).
  HIDDEN    the commands that the start, the dashboard, the client setup and the benches call (no help).

A command's error (ConfigError) is one "error:" line on stderr and exit 1; Ctrl-C is exit 130. Contracts C-401,
C-404 to C-413, C-415, C-419 to C-422.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

from ...domain.shared.errors import ConfigError
from .. import help_topics
from . import (cache, card, carl_id, chat_template, client_models, config, delete, download, engine, fit, gpu,
               hf, launch_env, log, models, package, paths, push, router_preset, server_file, tune, verify)
from .base import EXIT_ERROR, Cli

ALL_OS = ("macos", "windows", "linux")
OFF_MAC = ("windows", "linux")

Run = Callable[[Cli, List[str]], int]


@dataclass(frozen=True)
class Command:
    """One command. shown: the OSes whose help lists it (() for a hidden command); runs: the OSes where it runs in
    this process; rest: it gets the arguments after its word (push takes none)."""
    name: str
    run: Run
    shown: Tuple[str, ...] = ALL_OS
    runs: Tuple[str, ...] = ALL_OS
    rest: bool = True


COMMANDS: Tuple[Command, ...] = (
    Command("fit", fit.run),
    Command("models", models.run),
    Command("download", download.run),
    Command("hf", hf.run),
    Command("verify", verify.run),
    Command("delete", delete.run),
    Command("config", config.run),
    Command("cache", cache.run),
    Command("card", card.run),
    Command("package", package.run),
    Command("push", push.run, rest=False),
    Command("tune", tune.run),
    Command("gpu", gpu.run, shown=OFF_MAC),
    Command("engine", engine.run),
    Command("log", log.run),
)
HIDDEN: Tuple[Command, ...] = tuple(Command(name, run, shown=()) for name, run in (
    ("plan", fit.plan),
    ("check", fit.check),
    ("launch-env", launch_env.run),
    ("router-preset", router_preset.run),
    ("server-file", server_file.run),
    ("client-models", client_models.run),
    ("carl-id", carl_id.run),
    ("chat-template", chat_template.run),
    ("paths", paths.run),
))
BY_NAME: Dict[str, Command] = {c.name: c for c in COMMANDS + HIDDEN}

def runs_here(name: str, os_name: str) -> bool:
    """A command of the table that runs in this process on this OS."""
    c = BY_NAME.get(name)
    return c is not None and os_name in c.runs


def shown(os_name: str) -> List[str]:
    """The commands only some OSes' help lists, that this OS's lists (the main topic's)."""
    return [c.name for c in COMMANDS if c.name in help_topics.OFF_MAC and os_name in c.shown]


def plugin_help(cli: Cli) -> List[Tuple[str, str]]:
    """The commands of the plugins that are on that `carl -h` lists (PluginCommand.listed), with what each does; none
    when there is no plugin seam or it cannot be read."""
    try:
        host = cli.plugins()
        return [(c.name, c.help) for c in host.commands() if c.listed] if host is not None else []
    except Exception:  # noqa: BLE001  (a plugin never stops the help)
        return []


def help_text(cli: Cli, topic: str) -> str:
    """A help topic in this computer's words and width (KeyError: no such topic). The main topic lists the plugins'
    commands that ask for it (only that topic builds the plugins)."""
    listed = plugin_help(cli) if help_topics.is_main(topic) else []
    return help_topics.render(topic, cli.width(), cli.words, shown(cli.os), cli.port, listed)


def run_help(cli: Cli, topic: str) -> int:
    """`carl help TOPIC`: the topic, or one error line and exit 2."""
    try:
        print(help_text(cli, topic))
    except KeyError:
        print(help_topics.unknown_topic(topic, cli.words), file=sys.stderr)
        return 2
    return 0


def guarded(run: Run, cli: Cli, argv: Sequence[str]) -> int:
    """A command's run: its error is one "error:" line and exit 1, Ctrl-C exit 130."""
    try:
        return run(cli, list(argv))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


def run_command(cli: Cli, name: str, argv: Sequence[str]) -> int:
    """One command of the table with the arguments after its word."""
    c = BY_NAME[name]
    return guarded(c.run, cli, argv if c.rest else [])


def is_plugin_command(cli: Cli, name: str) -> bool:
    """A hidden command that an installed plugin that is on adds (WP). Asked only of a word that is no command of the
    table, so a start or a command of the table never builds the plugins."""
    host = cli.plugins()
    return host is not None and any(c.name == name for c in host.commands())


def run_plugin_command(cli: Cli, name: str, argv: Sequence[str]) -> int:
    """One command of a plugin with the arguments after its word: its exit code. A plugin's error is one `error:` line
    and exit 1 (the registry catches it); Ctrl-C is exit 130."""
    host = cli.plugins()
    if host is None:
        return EXIT_ERROR
    try:
        return host.run_command(name, list(argv), print)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130

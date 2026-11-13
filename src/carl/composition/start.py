"""The start's wiring (W3.2): the StartServer of this computer's profile, over its adapters.

  macos   ifconfig, netstat, ps, caffeinate, Homebrew's check, and the migrations of earlier versions (the old
          settings folder and its key, MTPLX's key: decision 13, wired here only)
  linux   /proc and ss, systemd-inhibit (or a note), the llama.cpp offer (W6.2), no migration
  windows GetExtendedTcpTable and an exclusive bind, Toolhelp (the guard by name only), the socket's addresses, the
          llama.cpp offer, keep-awake by a child that holds SetThreadExecutionState, the key's access list, no
          migration; the server through the Windows runner (a hidden console of its own, the console stop) and, in a
          terminal, the dashboard in this process (W6.8: Windows has no exec)

The start is the default path on every OS (the bash launcher is gone, W4.2). Its environment is
the process's own (os.environ): the dependency check may put Homebrew's folder on its PATH, which the server then
gets. What reads the clock, the settings and the files is built when the start asks for it, after the migrations;
router mode's lists and presets are read and written in this process, from the same settings.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Mapping, MutableMapping, Optional, Sequence

from ..adapters.common import key_store, private_dirs, runtime_files
from ..adapters.common.template_files import TemplateFiles
from ..adapters.common.version import ChangelogVersion
from ..adapters.llama.dry_run import LlamaDryRun
from ..adapters.llama.server_fit import LlamaServerFit
from ..application.server_file import ServerFileService
from ..application.start_server import StartPlace, StartPorts, StartServer, StartTools
from ..application.version_check import start_note
from ..domain.config.key_paths import carl_key_file
from ..domain.config.paths import CONF_DIR, conf_dir_override, home_path
from ..domain.llama.version import tested_for
from ..domain.server.launch_plan import THREAD_PIN, process_lines, shell_lines
from ..domain.server.router_presets import Preset, ini_safe, preset_ini
from ..domain.server.ub_order import UbAsk
from ..domain.shared.constants import VM_ADDR
from ..domain.shared.errors import ConfigError
from ..domain.shared.platform_words import PlatformWords
from ..domain.shared.types import ModelInfo, SettingValue
from ..ports import (ConfDirMigration, Dependencies, KeepAwake, KeyStore, NetworkInterfaces, PluginHost, PortProbe,
                     ProcessTable, ServerRunner)
from . import engines as engine_wiring, llama as llama_wiring, wiring
from .profiles import Machine, Profile

WRITTEN = "%Y-%m-%dT%H:%M:%S"                     # remote.json's "written", as the launcher wrote it
CARL_PY = os.path.join(wiring.REPO, "carl.py")


def place(env: Mapping[str, str], home: str, profile: Profile, machine: Machine) -> StartPlace:
    conf = conf_dir_override(home, env) or home_path(home, CONF_DIR)
    tool = {"macos": "ifconfig", "windows": "ipconfig"}.get(profile.os, "ip addr")
    return StartPlace(home, conf, carl_key_file(home, env), os.path.join(home, "models", "templates"), tool,
                      profile.os == "windows")


@dataclass
class Host:
    """What the start and the first run ask of this computer's system, by profile: the listening ports, the
    processes and the addresses; the settings and the key an earlier version kept elsewhere; the dependency check
    (Homebrew's on a Mac, the llama.cpp offer on Windows and Linux); keep-awake."""
    probe: PortProbe
    table: ProcessTable
    nets: NetworkInterfaces
    migrations: List[ConfDirMigration]
    key_migrations: List[ConfDirMigration]
    deps: Optional[Dependencies]
    awake: KeepAwake


def host(env: MutableMapping[str, str], profile: Profile, home: str, key_file: str) -> Host:
    """The system adapters of this computer (key_file: CARL's own key file, which the key's earlier places move to
    on a Mac; with CARL_CONF_DIR set it is that folder's, and nothing moves to it)."""
    from ..adapters.posix import start_tools
    if profile.os == "windows":
        from ..adapters.common.socket_addresses import SocketAddresses
        from ..adapters.windows.keep_awake import WindowsKeepAwake
        from ..adapters.windows.processes import WindowsProcessTable
        from ..adapters.windows.tcp_table import WindowsPorts
        conf = conf_dir_override(home, env) or home_path(home, CONF_DIR)
        return Host(WindowsPorts(), WindowsProcessTable(conf, resident=False), SocketAddresses(), [], [],
                    llama_wiring.offer(env, profile, home),
                    WindowsKeepAwake(wiring.logs_dir(home), sys.executable, start_tools.err))
    if profile.os == "macos":
        from ..adapters.macos import llm_deploy_migration, mtplx_migration
        from ..adapters.macos.bsd_netstat import NetstatPorts
        from ..adapters.macos.caffeinate import Caffeinate
        from ..adapters.macos.homebrew_deps import HomebrewDeps
        from ..adapters.macos.ifaces import Ifconfig
        from ..adapters.macos.ps import PsTable
        return Host(NetstatPorts(), PsTable(), Ifconfig(env.get("VM_HOST") or VM_ADDR),
                    [llm_deploy_migration.FolderMigration(home, bool(env.get("CARL_CONF_DIR")))],
                    [] if conf_dir_override(home, env) else
                    [llm_deploy_migration.key_migration(home, key_file), mtplx_migration.key_migration(home, key_file)],
                    HomebrewDeps(env, start_tools.out, start_tools.err, start_tools.ask), Caffeinate())
    from ..adapters.common.socket_addresses import SocketAddresses
    from ..adapters.linux.inhibit import SystemdInhibit
    from ..adapters.linux.ss_proc import ProcPorts, ProcTable
    return Host(ProcPorts(), ProcTable(), SocketAddresses(), [], [], llama_wiring.offer(env, profile, home),
                SystemdInhibit(start_tools.err))


def server_fit(env: Mapping[str, str], profile: Profile, machine: Machine, conf_dir: str) -> LlamaServerFit:
    """llama-server's own fit of a start (A67), through the profile's ProcessControl; Windows makes its stubs sparse
    its own way. Its answers are kept by the GPUs too (their names and sizes; a Mac's chip)."""
    from ..adapters.llama import gguf_stub
    from ..ports import ProcessControl
    procs: ProcessControl
    extend: gguf_stub.Extend = gguf_stub.truncate
    if profile.os == "windows":
        from ..adapters.posix import start_tools
        from ..adapters.windows.process import WindowsProcesses
        from ..adapters.windows.sparse_file import extend_sparse
        procs, extend = WindowsProcesses(note=start_tools.err), extend_sparse
    else:
        from ..adapters.posix.process import PosixProcesses
        procs = PosixProcesses()

    def gpus() -> str:                           # the answers' key names the GPUs (a Mac: its chip)
        if profile.os == "macos":
            from ..adapters.macos.sysctl_host import MacHostInfo
            return MacHostInfo().cpu_name()
        devices = llama_wiring.gpu_state(profile, machine).inventory.devices()
        return ", ".join(f"{g.name} {g.total}" for g in devices)
    return LlamaServerFit(llama_wiring.llama_cpp(profile, machine), conf_dir, procs, env, extend=extend, gpus=gpus)


def build(env: MutableMapping[str, str], profile: Profile, machine: Machine, home: str) -> StartServer:
    """The start of this computer."""
    from ..adapters.posix import start_tools
    from ..adapters.posix.process import date_text
    words = profile.words
    where = place(env, home, profile, machine)
    h = host(env, profile, home, where.key_file)
    engines = engine_wiring.adapters(profile, machine)
    keys: Callable[[str], KeyStore] = key_store.KeyFile
    stamp, prepare_log, lines_of = date_text, start_tools.prepare_log, shell_lines
    ini_path: Callable[[str], str] = lambda path: path  # noqa: E731
    if profile.os == "windows":
        from ..adapters.windows import host as windows_host
        from ..adapters.windows.secrets import PrivateKeyFile
        from ..adapters.windows.server_runner import WindowsServerRunner
        windows_host.vt_output()
        programs = start_tools.CarlPrograms(wiring.REPO, sys.executable)
        runner = WindowsServerRunner(CARL_PY, where.conf_dir, sys.executable, out=start_tools.out,
                                     note=start_tools.err)
        keys = lambda path: PrivateKeyFile(key_store.KeyFile(path), start_tools.err)  # noqa: E731
        stamp, prepare_log, lines_of = _local_time, _log_folder, process_lines
        ini_path = _short_path
        from .dashboard import run_here
        dashboard: Callable[[Sequence[str]], int] = run_here
        server_runner: ServerRunner = runner
    else:
        from ..adapters.posix.server_runner import PosixServerRunner
        programs = start_tools.CarlPrograms(wiring.REPO)
        dashboard, server_runner = programs.dashboard, PosixServerRunner(CARL_PY)
    ports = StartPorts(
        h.probe, h.table, h.nets, keys, h.migrations, h.key_migrations, h.deps,
        TemplateFiles(where.templates_dir, start_tools.err, lambda: models_folder(env, home, words)),
        server_runner, h.awake,
        ServerFileService(runtime_files.RuntimeFiles(where.conf_dir), runtime_files.now_text), engines,
        plugins=lambda: plugin_host(env, home, words), cpu=profile.cpu() if profile.cpu else None,
        dry_run=LlamaDryRun(llama_wiring.llama_cpp(profile, machine), where.conf_dir),
        server_fit=server_fit(env, profile, machine, where.conf_dir))
    tools = StartTools(
        out=start_tools.out, err=start_tools.err, ask=start_tools.ask, is_tty=start_tools.is_tty,
        is_file=os.path.isfile, pid=os.getpid, make_dir=start_tools.make_dir, prepare_log=prepare_log,
        stamp=stamp, launch_env=lambda model, use_config: launch_env(env, home, words, model, use_config, lines_of),
        outbox=lambda host, port, key: outbox(env, home, host, port, key, profile.os), cache_trim=programs.cache_trim,
        engine_name=lambda model: engine_of(env, home, words, model),
        llama_note=lambda: start_notes(llama_wiring.server_program(machine), profile, machine), fit=programs.fit,
        downloaded=lambda: downloaded_files(env, home, words),
        write_presets=lambda file, templates: write_presets(env, home, words, file, templates, ini_path),
        help=lambda arg: _help(env, profile, machine, home, "start"), dashboard=dashboard,
        ub_answer=lambda model, ask, choice: answer_ub(env, home, words, model, ask, choice))
    return StartServer(words, env, where, ports, tools)


def _short_path(path: str) -> str:
    """The 8.3 short form of a path that exists (Windows): it has no ';' or '#', which the presets file cannot hold."""
    from ..adapters.windows.process import short_path
    return short_path(path) if os.path.exists(path) else path


def _local_time(fmt: str) -> str:
    """The time in fmt (Windows: no date program to ask)."""
    return time.strftime(fmt, time.localtime())


def _log_folder(log: str) -> None:
    """The log's folder (Windows: no llama-server-latest.log link; links need a privilege there)."""
    private_dirs.make_dirs(os.path.dirname(log) or ".")


def _help(env: Mapping[str, str], profile: Profile, machine: Machine, home: str, topic: str) -> int:
    """A help topic in this process (the dispatch's help: cli/commands/__init__.py run_help)."""
    from ..cli import commands
    from . import cli
    return commands.run_help(cli.build(env, profile, machine, home), topic)


def plugin_host(env: Mapping[str, str], home: str, words: PlatformWords) -> PluginHost:
    """The plugins of the settings folder (the Carl of the same folder holds them; nothing is read yet)."""
    host = wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words).plugins
    assert host is not None
    return host


def models_folder(env: Mapping[str, str], home: str, words: PlatformWords) -> str:
    """The models folder of this settings folder (MODELS_DIR, else paths.models_dir of config.json, else the default)."""
    app = wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words)
    return app.models_dir(app.load_config())


def launch_env(env: Mapping[str, str], home: str, words: PlatformWords, model: str, use_config: bool,
               lines_of: Callable[[Mapping[str, SettingValue]], str] = shell_lines) -> Optional[str]:
    """The model and its settings as KEY=value lines (carl launch-env, in this process): its note on
    stderr; an error as "error: ..." and None. The settings folder is read now, after the migrations. lines_of: the
    lines' rule (Windows: process_lines, as its paths have backslashes and no shell reads them)."""
    try:
        app = wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words)
        lines, note = app.launch_env(model or None, use_config)
        if note:
            _err(note)
        return lines_of(lines)
    except ConfigError as e:
        _err(f"error: {e}")
        return None


def answer_ub(env: Mapping[str, str], home: str, words: PlatformWords, model: str, ask: UbAsk, choice: str) -> None:
    """The answer to the start's -ub question, saved in config.json (A51); a settings file that cannot be written
    leaves the start going with the answer in force for this start only."""
    try:
        wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words).answer_ub(model, ask, choice)
    except (ConfigError, OSError) as e:
        _err(f"warning: CARL could not save your answer in config.json: {e}")


def engine_of(env: Mapping[str, str], home: str, words: PlatformWords, model: str) -> Optional[str]:
    """The engine for a model (composition/engines.py engine_name): the environment, the setting engine.name, the
    model's card. model: "" for none (router mode). None, with "error: ..." on stderr, when the settings cannot be read or
    name an engine that CARL does not have."""
    try:
        app = wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words)
        cfg = app.load_config()
        found = app.find(model, app.all_models(cfg)) if model else None
        return engine_wiring.engine_name(env, cfg.engine.get("name"), str(found.get("engine") or "") if found else "")
    except ConfigError as e:
        _err(f"error: {e}")
        return None


def _downloaded(env: Mapping[str, str], home: str, words: PlatformWords) -> List[ModelInfo]:
    """The downloaded models, in the models list's order; none, with "error: ..." on stderr, when the settings or
    the models cannot be read."""
    try:
        app = wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words)
        return [m for m in app.all_models(app.load_config()) if m.get("status") == "downloaded"]
    except ConfigError as e:
        _err(f"error: {e}")
        return []


def downloaded_files(env: Mapping[str, str], home: str, words: PlatformWords) -> List[str]:
    """The file of every downloaded model, in the models list's order (carl.py's old downloaded, then path)."""
    return [str(m.get("path", "")) for m in _downloaded(env, home, words)]


def downloaded_names(env: Mapping[str, str], home: str, words: PlatformWords) -> List[str]:
    """The name of every downloaded model, in the models list's order (carl.py's old downloaded): the first run
    asks whether there is one."""
    return [str(m.get("name", "")) for m in _downloaded(env, home, words)]


def write_presets(env: Mapping[str, str], home: str, words: PlatformWords, file: str,
                  templates_dir: str, to_ini: Callable[[str], str] = lambda path: path) -> Optional[Preset]:
    """Router mode's presets (carl router-preset, in this process): the presets INI written to file; the
    presets, or None with "error: ..." on stderr when the settings or the models cannot be read. to_ini: another name
    for a path the file cannot hold (ini_path)."""
    try:
        app = wiring.build_carl(wiring.CarlPaths.from_env(env, home), env, words)
        preset, common = ini_safe(*app.router_preset(app.load_config(), templates_dir, env.get(THREAD_PIN, "")), to_ini)
        with open(file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(preset_ini(preset, common, words))
        return preset
    except ConfigError as e:
        _err(f"error: {e}")
        return None


def outbox(env: Mapping[str, str], home: str, host: str, port: int, key_file: str, os_name: str = "") -> None:
    """The client folder's connection files (CARL_CLIENT_DIR, else client/ in the clone): nothing when the folder or
    the key is missing; CARL's ID is made then (carl-id in the settings folder). On Windows the copies of the key are
    left to the user as the key is (fix1.6), with one warning each when that fails."""
    folder = wiring.client_dir(env)
    if not os.path.isdir(folder) or not key_store.has_key(key_file):
        return
    conf = wiring.CarlPaths.from_env(env, home).conf_dir
    from ..adapters.posix.process import date_text
    from .package import private_files
    runtime_files.write_client_package(folder, host, port, key_file, runtime_files.carl_id(conf),
                                       ChangelogVersion(wiring.REPO).version(), date_text(WRITTEN),
                                       private_files(os_name), _err)


def llama_note(server: str, profile: Profile) -> str:
    """The start line when the installed llama.cpp is older than the tested one ("" when it is not, or the check
    cannot run)."""
    try:
        from ..adapters.macos.sysctl_host import llama_server_version
        return start_note(llama_server_version(server), profile.words.update_hint(), tested_for(profile.os))
    except Exception:                                   # a check that cannot run says nothing (the launcher's)
        return ""


def start_notes(server: str, profile: Profile, machine: Machine) -> str:
    """The start's note lines about llama.cpp (the start line's llama_note): the version note, and off the Mac the
    warning when this computer has a GPU that this llama-server does not see (a build for another GPU). The two end
    in one string, a line each. W4.2: the warning was the Windows preview's, in tools/carl-serve.py."""
    notes = [llama_note(server, profile)]
    if profile.os != "macos":
        try:
            from ..adapters.gpu.llama_devices import listed_devices
            from ..domain.server import start_text
            if llama_wiring.gpu_state(profile, machine).inventory.devices() and not listed_devices(server):
                notes.append(start_text.gpu_blind_note(profile.words))
        except Exception:                               # a check that cannot run says nothing
            pass
    return "\n".join(n for n in notes if n)


def _err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


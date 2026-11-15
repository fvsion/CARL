"""The start of llama-server in Python (W3.2; the default path from W3.3; the bash launcher is deleted, W4.2): the
bash launcher's steps over the ports. The decisions are the launch plan's (domain/server: launch_plan, network,
argv, start_text; C-119); this service does the steps around them, in the launcher's order:

  1. the options (-h and --help go to the help);
  2. the settings that earlier versions kept elsewhere (ConfDirMigration: on macOS the old settings folder), then
     the tools CARL needs (Dependencies; SKIP_DEPS=1 skips);
  3. the model and its settings (launch-env), then the plan: the mode, the model file, the address, the port and the
     rest, and the engine for the model (its card, the setting engine.name; WCLI); a server option that the engine's
     own help does not name is refused (WFIN: before anything is written);
  4. the port: a llama-server there is refused (one model at a time); another program there gets the offer of the
     next free port on a terminal, else the start ends and names it (PortProbe, ProcessTable);
  5. the second-model guard: no start while another large process or a known model server is in memory
     (ALLOW_SECOND_MODEL=1 skips; BIG_GB sets the size);
  6. the key (KeyStore; for CARL's own key file, on macOS, a copy from an earlier place first), the client
     folder's connection files, the slot folder and the disk cache's trim, the llama.cpp version note, the log;
  7. router mode (W3.3, all in this process): the templates of every downloaded model, the presets file and
     server-PORT.json, the start lines; a single model: the memory plan and the check, the threads (one per physical
     core, pinned, when experts stay in the RAM: A54), the thinking toggle's template, the start lines,
     server-PORT.json;
  8. the run (ServerRunner, KeepAwake): with MONITOR=0 or off a terminal the start becomes the engine's server
     (llama-server; its PID is the caller's); in a terminal the server starts in the background and the dashboard takes
     the terminal. The engine's program and its argv come from its EngineAdapter.

Every text, exit code, file and the server's argv and environment are the bash launcher's of 1.20.10, byte for byte,
held by the CLI goldens.
OS-free (R5): what no port covers yet comes in as StartTools from the composition. Contract C-116.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..domain.catalog.quants import join_spec
from ..domain.hardware.cpu_topology import checked
from ..domain.hardware.processes import is_model_server
from ..domain.memory import server_fit as sf
from ..domain.server import launch_plan as lp
from ..domain.server import start_text as st
from ..domain.server.router_presets import Preset, preset_answer
from ..domain.server.ub_order import UbAsk, choice_from
from ..domain.shared.contracts import require
from ..domain.shared.errors import ConfigError
from ..domain.shared.paths import policy_for
from ..domain.shared.platform_words import PlatformWords
from ..ports import (ConfDirMigration, CpuLayout, Dependencies, EngineAdapter, KeepAwake, KeyStore, MemoryDryRun,
                     NetworkInterfaces, PluginHost, PortProbe, ProcessTable, ServerFit, ServerRunner, TemplateStore)
from .server_file import ServerFileService
from .templates import ensure_templates

LLAMA_SERVER = "llama-server"
LAST_FREE = 100                         # the start offers a free port at most this far after the one asked for
BIG_GB = "8"                            # the second-model guard: a process over this many GiB resident
NO = ("N", "n")
STAMP = "%Y%m%d-%H%M%S"                 # the log file's time
PRESETS = "router-presets.ini"
ROUNDS = 3                              # plans made again when llama-server's fit needs more than CARL's estimate (A67)


@dataclass(frozen=True)
class StartPlace:
    """Where a start's files are, from the composition. conf_dir: CARL_CONF_DIR, else ~/.config/carl (the slots,
    the router's presets); key_file: CARL's key file (~/.config/carl/api-key, also with CARL_CONF_DIR);
    templates_dir: ~/models/templates; addr_tool: the program that lists this computer's addresses (named in a
    message); numeric_temp: TEMP counts only as a number (Windows)."""
    home: str
    conf_dir: str
    key_file: str
    templates_dir: str
    addr_tool: str = "ifconfig"
    numeric_temp: bool = False


@dataclass
class StartPorts:
    """The ports of a start. keys: the key store of a key file; migrations: the settings folder's (before
    anything); key_migrations: the key's earlier places, newest first (for CARL's own key file only); deps: None
    where the composition has no check yet; engines: the EngineAdapter of each engine name."""
    probe: PortProbe
    table: ProcessTable
    nets: NetworkInterfaces
    keys: Callable[[str], KeyStore]
    migrations: Sequence[ConfDirMigration]
    key_migrations: Sequence[ConfDirMigration]
    deps: Optional[Dependencies]
    templates: TemplateStore
    runner: ServerRunner
    awake: KeepAwake
    server_file: ServerFileService
    engines: Mapping[str, EngineAdapter]
    plugins: Optional[Callable[[], PluginHost]] = None   # the plugins, built when a start asks (WP; None: no plugin seam)
    cpu: Optional[CpuLayout] = None          # the CPU's cores, for the threads of a start with experts in the RAM (A54);
                                             # None on a Mac, which has none
    dry_run: Optional[MemoryDryRun] = None   # llama.cpp's no-alloc dry run, D's check against the fit (A67); None: none
    server_fit: Optional[ServerFit] = None   # llama-server's own fit, the source of the GPU need (A67); None: B


@dataclass
class StartTools:
    """What a start needs that no port covers yet (the composition fills it in; W4.1 and W5 move the commands
    in-process):
      out, err        a line on stdout, on stderr (flushed);
      ask             a prompt on stderr and the line typed, None at the end of the input;
      is_tty, is_file, pid   the terminal (fd 0 or 1), a file that exists, this process's pid;
      make_dir        a folder and its parents (mkdir -p);
      prepare_log     the log's folder and the llama-server-latest.log link to the log;
      stamp           the time as `date +FORMAT` gives it;
      launch_env      the model and its settings as KEY=value lines (MODEL, use the config); None when it failed
                      (it said why);
      outbox          the client folder's connection files (host, port, key file);
      cache_trim      the disk cache trimmed to its limit (silent);
      engine_name     the engine for a model (a name: its card, the setting engine.name, CARL_ENGINE; "" for router
                      mode, which has no one model); None when it failed (it said why);
      llama_note      the start line when the installed llama.cpp is older than the tested one ("" else);
      fit             the memory plan or check (llama-fit's ARGS; quiet: stdout captured, stderr dropped) -> (exit
                      code, stdout);
      downloaded      the file of every downloaded model (router mode's templates);
      write_presets   router mode's presets file (FILE, the templates folder) written, and the presets; None when
                      it failed (it said why);
      help            the help of the start (-h or --help); exits;
      dashboard       the dashboard with its arguments, in this terminal; exits;
      ub_answer       the answer to the -ub question saved in config.json (the model's name, the question, the
                      choice: ub_order.USE_TUNED or KEEP); None: the start cannot save one and does not ask."""
    out: Callable[[str], None]
    err: Callable[[str], None]
    ask: Callable[[str], Optional[str]]
    is_tty: Callable[[int], bool]
    is_file: Callable[[str], bool]
    pid: Callable[[], int]
    make_dir: Callable[[str], None]
    prepare_log: Callable[[str], None]
    stamp: Callable[[str], str]
    launch_env: Callable[[str, bool], Optional[str]]
    outbox: Callable[[str, int, str], None]
    cache_trim: Callable[[], None]
    engine_name: Callable[[str], Optional[str]]
    llama_note: Callable[[], str]
    fit: Callable[[Sequence[str], bool], Tuple[int, str]]
    downloaded: Callable[[], List[str]]
    write_presets: Callable[[str, str], Optional[Preset]]
    help: Callable[[str], int]
    dashboard: Callable[[Sequence[str]], int]
    ub_answer: Optional[Callable[[str, UbAsk, str], None]] = None


class StartServer:
    """One start. env: the start's environment (the dependency check may put Homebrew's folder on its PATH; the
    server's environment is made from it)."""

    def __init__(self, words: PlatformWords, env: MutableMapping[str, str], place: StartPlace, ports: StartPorts,
                 tools: StartTools) -> None:
        self.words, self.env, self.place, self.p, self.t = words, env, place, ports, tools

    def run(self, argv: Sequence[str]) -> int:
        """The start's exit code (a start that runs llama-server in this process, or the dashboard, does not
        return where the OS allows it)."""
        try:
            return self._start(list(argv))
        except lp.LaunchError as e:
            if str(e):
                self.t.err(str(e))
            return e.code
        except OSError as e:
            self.t.err(f"error: {e}")
            return 1
        except KeyboardInterrupt:                # Ctrl-C before the server runs: the start ends, as the shell's did
            return 130

    # ------------------------------------------------------------ the steps
    def _start(self, argv: List[str]) -> int:
        o = lp.parse_options(argv)
        if o.help:
            return self.t.help(o.help)
        for m in self.p.migrations:
            if m.pending():
                self._err_lines(m.migrate())
        env, t = self.env, self.t
        if env.get("SKIP_DEPS", "0") != "1" and self.p.deps is not None:
            if not self.p.deps.offer(t.is_tty(0) and t.is_tty(1)):
                return 1
        if t.is_tty(1):
            t.out(st.READING)
        model = join_spec(o.model, o.quant) if o.model else env.get(lp.MODEL, "")
        text = t.launch_env(model, env.get("SETTINGS_FILE") != "none")
        if text is None:
            return 1
        plan = lp.begin(o, env, lp.launch_lines(text), self.place.numeric_temp)
        self._err_lines(plan.take_stderr())
        stamp = "" if env.get(lp.LOG_FILE) else t.stamp(STAMP)
        lp.resolve(plan, self.words, self.place.home, self.place.conf_dir, self.place.key_file, stamp, t.is_file,
                   self._has_addr, self.place.addr_tool)
        self._err_lines(plan.take_stderr())
        engine = self._engine(plan)
        self._options(plan, engine)
        self._check_port(plan)
        self._guard()
        self._key(plan.get(lp.API_KEY_FILE))
        t.outbox(plan.get(lp.HOST), int(plan.get(lp.PORT)), plan.get(lp.API_KEY_FILE))
        t.make_dir(plan.slot_dir)
        t.cache_trim()
        note = t.llama_note()
        if lp.logs(plan):
            t.prepare_log(plan.get(lp.LOG_FILE))
        if plan.mode == "router":
            return self._router(plan, note, engine)
        return self._single(plan, note, engine)

    def _engine(self, plan: lp.LaunchPlan) -> EngineAdapter:
        """The engine for the start: the model's (router mode has no one model: the override alone). Raises LaunchError
        when the pick failed (it said why)."""
        name = self.t.engine_name("" if plan.mode == "router" else plan.get(lp.MODEL_NAME))
        engine = None if name is None else self.p.engines.get(name)
        if engine is None:
            raise lp.LaunchError("" if name is None else f"error: CARL has no adapter for the engine {name}.")
        require(engine.engine_name() == name, "C-057", "an engine adapter has the name it was registered with")
        return engine

    def _options(self, plan: lp.LaunchPlan, engine: EngineAdapter) -> None:
        """A server option the engine does not take is refused here, before the start writes a file (the key, the
        client folder's files, server-PORT.json): the engine would refuse it only after them (WFIN). The engine's
        help is read only when the options hold a word of an option's form; a help that lists nothing refuses
        nothing. Raises LaunchError (exit 2) for the first such word."""
        if not lp.option_words(plan.extra):
            return
        bad = lp.unknown_options(plan.extra, engine.option_names())
        if bad:
            typed = plan.options.rest
            raise lp.unknown_option_error(bad[0], engine.program(), bad[0] not in typed, self.words)

    def _has_addr(self, addr: str) -> bool:
        return addr in self.p.nets.local_addresses()

    def _check_port(self, plan: lp.LaunchPlan) -> None:
        """One server per port, and in practice one model at a time: a llama-server on the port is refused. Another
        program there: the next free port is offered on a terminal (other computers reach the server by its port,
        so it never changes silently), else the start ends and names it."""
        port, cmd = int(plan.get(lp.PORT)), self.words.command
        pid = self.p.probe.owner(port)
        if pid is None:
            return
        comm = next((p.command for p in self.p.table.processes() if p.pid == pid), "")
        if LLAMA_SERVER in comm:
            raise lp.LaunchError(f"error: a server runs on port {port} already (process {pid}). One model at a time: "
                                 f"stop it first (press Ctrl-C in its terminal, or run kill {pid}).\n"
                                 f"       {cmd} monitor opens its dashboard.")
        what = comm.rsplit("/", 1)[-1][:30]
        free = next((q for q in range(port + 1, min(port + LAST_FREE, 65535) + 1) if self.p.probe.owner(q) is None),
                    None)
        if free is None:
            raise lp.LaunchError(f"error: port {port} is in use by another program ({what}, process {pid}), and no "
                                 "port after it is free.")
        if not (self.t.is_tty(0) and self.t.is_tty(1)):
            raise lp.LaunchError(f"error: port {port} is in use by another program ({what}, process {pid}). Port "
                                 f"{free} is free: PORT={free} {cmd} start starts the server there.")
        answer = self.t.ask(f"Port {port} is in use by another program ({what}, process {pid}). Start the server "
                            f"on port {free}? [Y/n] ")
        if answer is None:
            raise lp.LaunchError("")
        if answer.startswith(NO):
            self.t.out(f"The server did not start. PORT=N {cmd} start starts it on port N.")
            raise lp.LaunchError("")
        plan.vars[lp.PORT] = str(free)

    def _guard(self) -> None:
        """No second model: two do not fit in the GPU memory, and on a Mac the second one can stop it. Any process
        over BIG_GB resident counts (a server started some other way), and every known model server by its name.
        The name is the first word of ps's command, as the launcher's awk read it."""
        env = self.env
        if env.get("ALLOW_SECOND_MODEL", "0") == "1":
            return
        big_gb = env.get("BIG_GB") or BIG_GB
        lp.require_int("BIG_GB", big_gb, 1)
        big, me = int(big_gb) * 1024 * 1024, self.t.pid()
        found = []
        for p in self.p.table.processes():
            name = (p.command.split() or [""])[0]
            if p.pid != me and (p.rss_kib > big or is_model_server(name)):
                found.append(f"  pid {p.pid}, {p.rss_kib / 1048576:.1f} GiB: {name}")
        if not found:
            return
        cmd = self.words.command
        raise lp.LaunchError("\n".join(
            ["error: another large process (possibly a model) is in memory. Two models do not fit, and the second "
             f"one can stop {self.words.the_machine}."] + found +
            [f"       Stop that process first. If it is a CARL server, open its dashboard ({cmd}), press q, then s.",
             "       If it is not a model, set ALLOW_SECOND_MODEL=1."]))

    def _key(self, key_file: str) -> None:
        """The server's key: a key file keeps its key; CARL's own key file without one takes it from an earlier
        place first; else a new one, which the clients need."""
        store = self.p.keys(key_file)
        if store.read():
            store.ensure()
            return
        if key_file == self.place.key_file:
            for m in self.p.key_migrations:
                if m.pending():
                    self._err_lines(m.migrate())
                    return
        store.ensure()
        cmd = self.words.command
        self.t.err(f"CARL made the API key {st.tilde(key_file, self.place.home)}. The clients need it: {cmd} install "
                   f"and the client package ({cmd} package) copy it, and the dashboard shows it (Connect, then k).")

    def _router(self, plan: lp.LaunchPlan, note: str, engine: EngineAdapter) -> int:
        """Router mode: every downloaded model that fits, one loaded at a time, each with the settings a single
        start of it would use. The presets file and server-PORT.json are written here, in this process."""
        t = self.t
        if lp.thinking_toggle(plan):
            ensure_templates(self.p.templates, t.downloaded(), t.is_file)
        file = policy_for(self.place.conf_dir).join(self.place.conf_dir, PRESETS)
        preset = t.write_presets(file, self.place.templates_dir)
        if preset is None:
            return 1
        self.p.server_file.write_router(int(plan.get(lp.PORT)), preset)
        if any(m.devices.startswith("CUDA") for m in preset.models):     # the children get CARL's CUDA numbers too
            plan.env_add[lp.CUDA_ORDER[0]] = lp.CUDA_ORDER[1]
        for line in st.router_lines(plan, preset_answer(preset), file, self.words, self.place.home, note):
            t.out(line)
        return self._run(plan, engine, engine.router_argv(plan, file), None)

    def _ub_question(self, plan: lp.LaunchPlan) -> None:
        """A config -ub against the model's tuned one (A51): at a terminal, one prompt (Use tuned, or keep the config
        value for this model), the answer saved; with nobody to ask the tuned -ub stands and the start's lines say so
        (the plan keeps the question for them)."""
        ask, t = plan.ub_ask, self.t
        if ask is None or t.ub_answer is None or not (t.is_tty(0) and t.is_tty(1)):
            return
        name = plan.get(lp.MODEL_NAME) or plan.get(lp.ALIAS)
        choice = choice_from(t.ask(ask.question(name)))
        if not choice:                          # no input (the end of the input): as if nobody could be asked
            return
        t.ub_answer(name, ask, choice)
        lp.answer_ub(plan, choice)

    def _single(self, plan: lp.LaunchPlan, note: str, engine: EngineAdapter) -> int:
        t = self.t
        self._ub_question(plan)
        base = copy.deepcopy(plan) if lp.checks_memory(plan) and engine.engine_name() == "llama.cpp" else None
        self._plan(plan)
        m: Optional[lp.Measured] = None
        if base is not None:
            plan, m = self._measured(base, plan, engine)
        if lp.checks_memory(plan):
            code, _ = t.fit(lp.check_args(plan), False)
            lp.after_check(plan, code)
            self._err_lines(plan.take_stderr())
        if m is not None:
            self._dry_check(plan, engine, m)
        if lp.thinking_toggle(plan):
            plan.template = self.p.templates.ensure(plan.get(lp.MODEL))
        for line in st.start_lines(plan, self.words, self.place.home, note):
            t.out(line)
        try:                                     # what this computer's clients need to know; never in the way
            self.p.server_file.write_single(int(plan.get(lp.PORT)), plan.get(lp.ALIAS), plan.swa, plan.get(lp.KV_K),
                                            int(plan.get(lp.CTX)), int(plan.get(lp.SLOTS)), lp.start_record(plan))
        except (OSError, ValueError):
            pass
        return self._run(plan, engine, engine.server_argv(plan), lp.logs(plan))

    def _plan(self, plan: lp.LaunchPlan) -> None:
        """The memory plan (llama-fit.py --plan) into the plan, and the threads of a start with experts in the RAM."""
        code, answer = self.t.fit(lp.plan_args(plan), True)
        lp.apply_plan(plan, code == 0, answer, self.words)
        lp.apply_pin(plan, checked(self.p.cpu.topology()) if self.p.cpu is not None and plan.n_cpu_moe > 0 else None)
        self._err_lines(plan.take_stderr())      # a backend's warning (FIT_CHECK=0 on Windows with CUDA)

    def _measured(self, base: lp.LaunchPlan, plan: lp.LaunchPlan, engine: EngineAdapter
                  ) -> Tuple[lp.LaunchPlan, Optional[lp.Measured]]:
        """The start's GPU need from llama-server's own fit (A67: never a number that can be under). The fit runs with
        the start's argv; when it needs more than CARL's estimate of the same start, the plan is made again (from base,
        the plan before the memory plan) with that much less room, at most ROUNDS times, and the check that follows
        refuses a start whose fit does not fit (--gpu-extra is the gap at the plan that starts). A fit that cannot run (no
        llama-server with --fit): the need is B, CARL's upper bound (carl fit --need --bound), the same way. No fit port, or
        a start over several GPUs (--need answers nothing): the estimate alone, as before, and no line."""
        fit = self.p.server_fit
        if fit is None:
            return plan, None
        m: Optional[lp.Measured] = None
        measures = True                          # a fit that could not run is not asked again in this start
        for round_ in range(ROUNDS + 1):
            argv = engine.server_argv(plan)
            got = sf.parse(fit.measure(argv) or "") if measures else None
            measures = got is not None
            m = self._need(plan, argv, got, fit)
            if m is None:
                return plan, None
            if m.gap <= plan.gpu_extra:
                break
            if round_ == ROUNDS:                 # the check takes the gap at the plan that starts
                plan.gpu_extra = m.gap
                break
            again = copy.deepcopy(base)
            again.gpu_extra = max(m.gap, plan.gpu_extra)
            self._plan(again)
            plan = again
        assert m is not None
        lp.apply_measured(plan, m)
        if m.source == sf.E_BOUND and lp.logs(plan):
            fit.watch_draft(engine.server_argv(plan), plan.get(lp.LOG_FILE))   # the next start takes its record
        return plan, m

    def _need(self, plan: lp.LaunchPlan, argv: Sequence[str], got: Optional[sf.Breakdown], fit: ServerFit
              ) -> Optional[lp.Measured]:
        """The need of the plan: the fit's (got) against CARL's estimate, or without a fit B's bound against it; None
        when carl fit answers nothing (several GPUs) or fails."""
        code, answer = self.t.fit(lp.need_args(plan), True)
        ans = lp.need_answer(answer) if code == 0 else None
        if ans is None:
            return None
        if got is not None:
            unmeasured = bool(plan.draft) and not got.extra_measured()     # a drafter the fit did not measure
            return lp.measured(plan, got, ans, fit.draft_record(argv) if unmeasured else None)
        code, answer = self.t.fit(lp.need_args(plan) + ["--bound"], True)
        b = lp.need_answer(answer) if code == 0 else None
        return lp.Measured(b.need, sf.B, b.need - ans.need, 0, ans.output) if b is not None else None

    def _dry_check(self, plan: lp.LaunchPlan, engine: EngineAdapter, m: lp.Measured) -> None:
        """D's check (A67): on a start whole on the GPU llama.cpp's dry run with the output off the GPU has the fit's
        compute buffer; a gap over 1 MiB is a line (a changed build), never the need. DRY_RUN=0 turns it off; a dry run
        that cannot run says nothing."""
        dry = self.p.dry_run
        if dry is None or self.env.get("DRY_RUN") == "0" or plan.n_cpu_moe > 0 or m.source != sf.E:
            return
        lp.apply_dry(plan, m, dry.run(lp.dry_args(engine.server_argv(plan), m.output)))

    def _run(self, plan: lp.LaunchPlan, engine: EngineAdapter, args: List[str], log_filter: Optional[bool]) -> int:
        """The engine's server with its output through the log filter (a single model with a log: log_filter; None, a
        router start, keeps CARL_LOG_FILTER as the caller set it). In a terminal (MONITOR is not 0): in the
        background, then the dashboard; else in this process. KEEP_AWAKE=0: no keep-awake."""
        env, t = self.env, self.t
        log, port = plan.get(lp.LOG_FILE), int(plan.get(lp.PORT))
        filtered = (env.get(lp.LOG_FILTER) == "1" if log_filter is None else log_filter) and lp.logs(plan)
        server_env = lp.server_environment(env, plan, self.place.home, log_filter)
        program, args, server_env = self._with_plugins(plan, engine.program(), args, server_env)
        argv = [program] + args
        require(argv[1:] == args, "C-020", "the argv names the program first")
        keep = env.get("KEEP_AWAKE", "1") != "0"
        if env.get("MONITOR", "1") != "0" and t.is_tty(0) and t.is_tty(1):
            home = self.place.home
            logs = policy_for(home).join(home, "models", "logs")
            console = policy_for(logs).join(logs, f".console-{port}.out")
            t.make_dir(logs)
            spid = self.p.runner.start(argv, server_env, log if filtered else "", port, console)
            if keep:
                self.p.awake.hold(spid)
            margs = ["--host", plan.get(lp.HOST), "--port", str(port), "--server-pid", str(spid), "--owner",
                     "--console", console] + (["--log", log] if lp.logs(plan) else [])
            return t.dashboard(margs)
        if keep:
            self.p.awake.hold(t.pid())
        return self.p.runner.run(argv, server_env, log if filtered else "", port)

    def _with_plugins(self, plan: lp.LaunchPlan, server: str, args: List[str], server_env: MutableMapping[str, str]
                      ) -> Tuple[str, List[str], MutableMapping[str, str]]:
        """The program, its arguments and its environment with the launch steps of the plugins that are on (the
        domain applies them: launch_plan.with_steps); a step that cannot be applied is left out with a warning. Nothing
        changes when no plugin adds a step."""
        if self.p.plugins is None:
            return server, args, server_env
        steps = list(self.p.plugins().launch_steps(lp.launch_facts(plan, server, args)))
        if not steps:
            return server, args, server_env
        try:
            program, new_args, new_env, notes = lp.with_steps(server, args, server_env, steps, plan.extra)
        except ConfigError as e:
            self.t.err(f"warning: a plugin's launch step was left out: {e}")
            return server, args, server_env
        for note in notes:
            self.t.out(note)
        return program, new_args, new_env

    def _err_lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.t.err(line)

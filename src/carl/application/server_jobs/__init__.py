"""Background work started from the Settings and Connect tabs: restarting the server with new settings (and rolling
back when it fails), Auto-tune (carl tune), model downloads and checks (carl download, carl verify), Hugging Face
lookups, the client installer and the client package. Children run from argument lists in their own sessions
through the ProcessControl port (contract C-019), a server stops through StopServer (C-117); their output goes to
files under ~/models/logs.

The jobs reach this computer only through JobsHost (host.py, C-132) and tell the dashboard what happens through the
JobNotifier (notifier.py, C-131): they never write the dashboard's state from their threads. Contract C-130."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Protocol

from ...domain.catalog.layout import locate
from ...domain.clients.package import Outcome
from ...domain.server.cmdline import CLEAN_ENV, SETTINGS_FILE, env_from_cmd
from ...domain.server.snapshot import ServerData, SlotInfo, clean, draft_bytes, jlist
from ...domain.shared.types import JsonObject, JsonValue
from ...domain.tune.progress import Kind
from ...ports import ServerEndpoint
from ..collector import Collector
from ..model_list import ModelList
from ..settings_service import REINSTALL, Pending, SettingsService
from ..stop_server import StopServer
from .host import CacheSettings, JobsHost
from .notifier import (AskTuneStop, Found, Job, JobNotifier, PackageDone, RevealFailed, Settled, StartFailed, Status,
                       Toast, failure, toast)
from .records import TUNE_ALL, Child, Download, Drain, HFLookup, InstallRun, PackageRun, TuneRun

HF_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")   # OWNER/REPO: nothing else goes into the API path
FETCH_ERRORS = (OSError, ValueError)        # what the server's API raises (ServerEndpoint, C-052)
STOP_TIMEOUT = 30.0                         # seconds for a server to exit before it is ended
JOB_STOP_TIMEOUT = 10.0                     # a download's or the installer's


@dataclass(frozen=True)
class Paths:
    """Where the launchers, logs and settings are."""
    repo: str
    logs: str           # ~/models/logs
    config_file: str

    @property
    def slots(self) -> str:
        """~/.config/carl/slots: the disk cache of prompt states (the server's --slot-save-path)."""
        return os.path.join(os.path.dirname(self.config_file), "slots")

    def console(self, port: int) -> str:
        """Where a server started for port writes its output."""
        return os.path.join(self.logs, f".console-{port}.out")


class SyncedClients(Protocol):
    """The computers that synced (the dashboard API's registry): forgotten after a while."""

    def list(self) -> List[object]: ...
    def forget(self, cid: str) -> None: ...


def server_env(base: Mapping[str, str], port: int, extra: Mapping[str, str]) -> Dict[str, str]:
    """The environment for the start (carl.py start): no monitor, this port, extra on top; without
    CLEAN_ENV and SETTINGS_FILE unless extra sets them ("none": the rollback ignores config.json)."""
    env = dict(base, MONITOR="0", PORT=str(port), **extra)
    for k in CLEAN_ENV + (SETTINGS_FILE,):
        if k not in extra:
            env.pop(k, None)
    return env


def start_server(host: JobsHost, paths: Paths, port: int, extra_env: Mapping[str, str], console: str,
                 append: bool = False) -> Child:
    """Start the server (carl.py start, in this Python) in the background, output to console (appended for the
    rollback: the failed start's output stays). With MONITOR=0 the process becomes llama-server, so its PID and its
    session are the server's."""
    if not append and host.exists(console):
        host.remove(console)
    cmd = [host.python, os.path.join(paths.repo, "carl.py"), "start"]
    return Child(host.procs, host.procs.spawn(cmd, server_env(host.env(), port, extra_env), console, True))


def start_tool(host: JobsHost, argv: List[str], log: str, env: Optional[Mapping[str, str]] = None) -> Child:
    """Run one of CARL's programs in its own session, output to log (a new file)."""
    if host.exists(log):
        host.remove(log)
    return Child(host.procs, host.procs.spawn(argv, dict(env if env is not None else host.env()), log, True))


def wait_up(host: JobsHost, proc: Child, port: int, key: Callable[[], str], vm_addr: str,
            where: Optional[str] = None, secs: float = 900) -> bool:
    """True once the new server answers /health (on where, the VM address or localhost:
    it may use another address); False if it exits or takes longer than secs. key() is
    asked each time: a first start creates the key file."""
    t_end = time.time() + secs
    while time.time() < t_end:
        if proc.poll() is not None:
            return False
        for h in ([where] if where else []) + [vm_addr, "127.0.0.1"]:
            if host.healthy(h, port, key()):
                return True
        time.sleep(2)
    return False


FIT_TIMEOUT = 900.0                 # measure_fit: a model not on disk reads its 24 MB header first; two context types, a few runs each


def last_lines(host: JobsHost, path: str, n: int) -> List[str]:
    """The last n non-blank-ended lines of a job's output file ([] if there is none)."""
    return (host.read_text(path) or "").strip().splitlines()[-n:]


class EndpointApi:
    """The server's API (LlamaServerApi, C-031) over the dashboard's ServerEndpoint (C-052), for StopServer."""

    def __init__(self, endpoint: ServerEndpoint) -> None:
        self.endpoint = endpoint

    def get(self, path: str, timeout: float) -> JsonValue:
        return json.loads(self.endpoint.get(path, timeout) or "null")

    def post(self, path: str, body: JsonObject, timeout: float) -> JsonValue:
        return json.loads(self.endpoint.post(path, body, timeout) or "null")

    def healthy(self) -> bool:
        try:
            self.endpoint.get("/health", 2)
            return True
        except FETCH_ERRORS:
            return False


class ServerJobs:
    """Runs the Settings and Connect tabs' background work; the dashboard learns of it through the notifier. The
    jobs' records (dl, tune, install, package, hf) are the jobs' own: the notifier hands each new one to the
    dashboard, and only the dashboard's thread changes them after that (poll)."""

    def __init__(self, notifier: JobNotifier, collector: Collector, svc: SettingsService, paths: Paths, vm_addr: str,
                 host: JobsHost) -> None:
        self.notify = notifier
        self.collector = collector
        self.svc = svc
        self.models: ModelList = svc.models
        self.paths = paths
        self.vm_addr = vm_addr
        self.host = host
        self.stopper = StopServer(EndpointApi(collector.endpoint), host.procs)
        self.install_columns: Callable[[], int] = lambda: 120      # the installer's width (the dashboard sets it)
        self.registry: Optional[SyncedClients] = None              # the API's clients (app.cache_api sets it)
        self._lock = threading.Lock()                              # the records and the cached settings below
        self._conf: CacheSettings = host.cache_settings({})
        self._conf_at = 0.0
        self._trim_at = 0.0
        self.dl: Optional[Download] = None
        self.tune: Optional[TuneRun] = None
        self.install: Optional[InstallRun] = None
        self.package: Optional[PackageRun] = None
        self.hf: Optional[HFLookup] = None

    def _job(self, slot: str, record: object) -> None:
        """A job's new record: kept here, and handed to the dashboard."""
        with self._lock:
            setattr(self, slot, record)
        self.notify.post(Job(slot, record))      # type: ignore[arg-type]

    def _stop(self, pid: Optional[int]) -> None:
        """Stop a server CARL started or watches (nothing when there is none)."""
        if pid and pid > 1 and self.host.alive(pid):
            self.stopper.stop(pid, timeout=STOP_TIMEOUT)

    def cache_conf(self, fresh: bool = False) -> CacheSettings:
        """The Caching settings (config.json "cache"), read again every few seconds."""
        with self._lock:
            if not fresh and time.time() - self._conf_at <= 5:
                return self._conf
        try:
            conf = self.host.cache_settings(self.svc.store.load_config())
        except Exception:       # config.json unreadable: the defaults (the Server panel says why)
            conf = self.host.cache_settings({})
        with self._lock:
            self._conf, self._conf_at = conf, time.time()
        return conf

    def trim_cache(self, keep: str = "") -> bool:
        """Remove the oldest saved states over the disk limit (slot_folder.py); False when `keep`
        itself had to go (alone over the limit)."""
        folder, conf = self.paths.slots, self.cache_conf()
        # earlier versions' files, what clients that went away left, the counters' events older than two days; with
        # share, new saves as patches against their prompt
        self.host.tidy_cache(folder, os.path.dirname(self.paths.config_file), time.time(), conf.share)
        gone = self.host.over_limit(folder, conf.limit, keep)
        if not gone:
            return True
        self.host.remove_states(folder, gone)
        return keep not in gone

    def cache_files(self) -> List[object]:
        """The saved states in the disk cache, oldest first."""
        return list(self.host.cache_files(self.paths.slots))

    def clear_cache(self) -> None:
        """Remove every saved state of the disk cache."""
        self.host.remove_states(self.paths.slots, [f.name for f in self.host.cache_files(self.paths.slots)])

    def llama_backends(self) -> Optional[List[str]]:
        """The backends of the installed llama.cpp builds (JobsHost.llama_backends); None: a folder is named."""
        return self.host.llama_backends(os.path.dirname(self.paths.config_file))

    def _wait_up(self, proc: Child, port: int, where: Optional[str] = None) -> bool:
        return wait_up(self.host, proc, port, lambda: self.collector.endpoint.key, self.vm_addr, where)

    # ------------------------------------------------------------ settings: restart
    def restart(self, p: Pending, d: ServerData, port: Optional[int] = None, ran: Optional[Pending] = None,
                then_install: bool = False) -> None:
        """Save the settings, stop the server, start it with them (in a thread); while a reply is
        being written, ask first (when_idle). port: start on this port instead (the server's port is
        another program's: that program is left alone). ran: what ran when the values were first shown (the
        clients' configs need an update when the context or the slots changed); then_install: update this
        computer's client configs once the server is up."""
        def go() -> None:
            self.notify.post(Status("CARL is saving the settings…", time.time()))
            self.notify.post(StartFailed())
            threading.Thread(target=self._restart, args=(dict(p), d, port, dict(ran or {}), then_install),
                             daemon=True).start()
        self.when_idle(d, "apply the settings (the server restarts)", go)

    def _restart(self, p: Pending, d: ServerData, new_port: Optional[int] = None, ran: Optional[Pending] = None,
                 then_install: bool = False) -> None:
        """Save, stop the old server, start the new one; on failure, put config.json back
        and start the old server again. new_port: another program has the port: start there, stop nothing."""
        say, host, ep = self.notify.post, self.host, self.collector.endpoint
        path = self.paths.config_file
        old_cmd, old_pid, port = d.cmd, d.target_pid or d.pid, ep.port
        if new_port and new_port != port:
            old_cmd, old_pid, port = "", None, new_port
        console = self.paths.console(port)
        started: List[Child] = []       # the servers this restart started: one that does not come up is stopped, never left
        try:
            old_text = host.read_text(path) if host.exists(path) else None     # the rollback puts it back
            if old_text is None and host.exists(path):
                raise OSError(f"cannot read {path}")
            host.make_folder(os.path.dirname(console))
            self.svc.save(p)
            say(Status("CARL is saving the sessions in the slots…"))
            self.save_before_stop(d)
            say(Status("CARL is stopping the server…"))
            self.collector.server_pid = None
            self._stop(old_pid)
            say(Status("The server is starting with the new settings: the model is loading…"))
            proc = start_server(host, self.paths, port, {}, console)
            started.append(proc)
            self.collector.follow(port, proc.pid, console)
            chosen = str(p.get("net"))
            if self._wait_up(proc, port, where=chosen if chosen.count(".") == 3 else None):
                started.clear()
                changed = [r.label.lower() for r in self.svc.rows(p)
                           if r.key in REINSTALL and str(p[r.key]) != str((ran or {}).get(r.key))]
                say(toast("The server restarted with the new settings."
                          + (f" The {' and the '.join(changed)} changed: update OpenCode and Pi (the Connect tab, u)."
                             if changed else ""), 12))
                say(Status(None))
                say(Settled())
                if then_install:
                    self.start_install(config_only=True)
                return
            failed = host.read_text(console) or ""         # the launcher's output (the rollback's goes after it)
            self._stop(proc.pid)
            if old_text is None:
                host.remove(path)
            else:
                host.write_private(path, old_text)
            back = False
            if old_pid and "llama-server" in old_cmd:
                say(Status("The new settings failed. The old server is starting again…"))
                proc = start_server(host, self.paths, port, env_from_cmd(old_cmd), console, append=True)
                started.append(proc)
                self.collector.follow(port, proc.pid, console)
                back = self._wait_up(proc, port)
                if back:
                    started.clear()
                else:
                    self._stop(proc.pid)            # it did not come up (still loading): "No server is running now" is true
            say(StartFailed(failed))
            say(failure("The new settings failed.", " " + ("The old server is running again." if back else
                                                           "No server is running now.")
                        + " The Server panel shows why.", 20))
            say(Status(None))
            say(Settled())              # config.json is back: the rows show its values, not the ones that failed
            return
        except Exception as e:      # anything (disk, config, process): the restart stops here and says why
            for child in started:   # a server this restart started and did not see up is not left running
                try:
                    self._stop(child.pid)
                except Exception:
                    pass
            say(failure(f"The restart failed: {e}", "", 20))
        say(Status(None))
        say(Settled(clear_pending=False))

    # ------------------------------------------------------------ Auto-tune
    def run_tune(self, d: ServerData, model: str, depth: str, confirmed: bool = False) -> None:
        """Tune model (TUNE_ALL: every downloaded model) at depth (quick, default, long); asks first when it has to
        stop a running server."""
        tune = self.tune
        if tune and tune.proc.poll() is None:
            self.notify.post(toast("Auto-tune is already running.", 5))
            return
        pid = d.target_pid if not d.exited else None
        n = len(self.models.downloaded())
        what = (f"all downloaded models, one after the other ({n} × about 10-20 min)" if model == TUNE_ALL
                else f"{model} (about 10-20 min)")
        running = bool(pid and self.host.alive(pid))
        if running and pid and not confirmed:
            self.notify.post(AskTuneStop(what, pid))
            return

        def work() -> None:
            if running:
                self.notify.post(Status("CARL is saving the sessions in the slots…", time.time()))
                self.save_before_stop(d)
                self.notify.post(Status("CARL is stopping the server for Auto-tune…"))
                self.collector.server_pid = None
                self._stop(pid)
                self.notify.post(Status(None))
            log = os.path.join(self.paths.logs, ".tune.out")
            self.host.make_folder(self.paths.logs)
            flag = {"quick": ["--quick"], "long": ["--long"]}.get(depth, [])
            proc = start_tool(self.host, [self.host.python, os.path.join(self.paths.repo, "carl.py"), "tune",
                                          "all" if model == TUNE_ALL else model] + flag, log)
            self._job("tune", TuneRun(model=model, proc=proc, log=log, restart=running))
        if running:
            self.when_idle(d, "auto-tune (the server stops)", lambda: threading.Thread(target=work, daemon=True).start())
        else:
            threading.Thread(target=work, daemon=True).start()

    def cancel_tune(self) -> None:
        """Stop Auto-tune (as Ctrl-C does: it cleans up its test server)."""
        if self.tune:
            self.host.interrupt(self.tune.proc.pid)

    def _restart_after_tune(self) -> None:
        """Start the server again after a tune stopped it: llama.cpp with the saved settings."""
        def work() -> None:
            port = self.collector.endpoint.port          # where the server ran before the tune stopped it
            console = self.paths.console(port)
            self.notify.post(Status("The server is starting again with the saved settings…", time.time()))
            proc = start_server(self.host, self.paths, port, {}, console)
            self.collector.follow(port, proc.pid, console)
            ok = self._wait_up(proc, port)
            if not ok:
                self._stop(proc.pid)            # it did not come up: no server is left half started
            self.notify.post(Status(None))
            self.notify.post(Settled())
            self.notify.post(toast("The server is running again.", 12) if ok else
                             failure("The server did not start again.", " The Log tab tells why.", 12))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ models
    def start_download(self, spec: str) -> None:
        """Download a catalogue model by name (with its MTP drafter, when it has one), or
        hf:REPO/FILE.gguf (resumable)."""
        if self.dl and self.dl.proc.poll() is None:
            self.notify.post(toast("A download is already running. To cancel it: c in the Models panel.", 6))
            return
        more: List[str] = []
        if spec.startswith("hf:"):
            _, file, _ = self.svc.store.parse_hf(spec)
            base = os.path.basename(file or "")
            info = next((x for x in (self.hf.files if self.hf else []) if x[0] == file), None)
            name = re.sub(r"[^a-z0-9._-]+", "-", base[:-5].lower())
            # where the CLI puts the file: in the model's own folder, or the older place of a partial file it resumes
            path: Optional[str] = locate(self.svc.store.models_dir(), base, self.host)
            total = (info[1] or 0) if info else 0
        else:
            m = self.models.by_name(spec)
            if not m:
                self.notify.post(failure(f"{spec} is not a known model."))
                return
            name, path, total = spec, m["path"], m["bytes"]
            # the drafters of other kinds that are not there (DFlash, DSpark, EAGLE-3: their own folders; WCATX)
            others = [d for d in jlist(m.get("drafts")) if d.get("kind") != "mtp-file" and d.get("status") != "downloaded"]
            if m.get("draft") and m["status"] == "downloaded":     # carl.py fetches only the missing drafter
                name, path, total = f"{spec} MTP drafter", m.get("draft_path"), draft_bytes(m)
                more, total = [str(d.get("path", "")) for d in others], total + sum(int(d.get("bytes", 0)) for d in others)
            elif m["status"] == "downloaded" and others:
                name, path = f"{spec} drafter", str(others[0].get("path", ""))
                more, total = [str(d.get("path", "")) for d in others[1:]], sum(int(d.get("bytes", 0)) for d in others)
            elif m.get("draft") or others:                          # the model, then its drafters
                more = ([str(m.get("draft_path", ""))] if m.get("draft") else []) + [str(d.get("path", "")) for d in others]
                total += (draft_bytes(m) if m.get("draft") else 0) + sum(int(d.get("bytes", 0)) for d in others)
        log = os.path.join(self.paths.logs, ".download.out")
        self.host.make_folder(os.path.dirname(log))
        proc = start_tool(self.host, [self.host.python, os.path.join(self.paths.repo, "carl.py"), "download",
                                      spec], log)
        self._job("dl", Download(name=name, path=path, total=total, proc=proc, log=log, more=more))
        self.notify.post(toast(f"The download of {name} started. If you cancel it, the downloaded part stays on the "
                               f"disk: d continues it.", 6))

    def cancel_download(self) -> None:
        """Stop the download; the partial file stays for a resume."""
        if self.dl:
            self._stop_job(self.dl.proc.pid)
            self.notify.post(toast("You cancelled the download. The downloaded part stays on the disk: press d to "
                                   "continue.", 6))

    def _stop_job(self, pid: int) -> None:
        """Stop a job's process (and its group) in the background: the dashboard does not wait for it."""
        threading.Thread(target=self.host.procs.stop, args=(pid, JOB_STOP_TIMEOUT), daemon=True).start()

    def verify(self, name: str) -> None:
        """Check a model's SHA-256 in the background (~1 min)."""
        def work() -> None:
            argv = [self.host.python, os.path.join(self.paths.repo, "carl.py"), "verify", name]
            proc = Child(self.host.procs, self.host.procs.spawn(argv, dict(self.host.env()), os.devnull, True))
            ok = proc.wait(3600) == 0
            self.notify.post(toast(f"{name}: the file is correct (the checksum agrees).", 10) if ok else
                             failure(f"{name}: the file is damaged (the checksum is wrong).", " Download it again (d).",
                                     10))
        threading.Thread(target=work, daemon=True).start()
        self.notify.post(toast(f"CARL is checking the file of {name} (SHA-256, about 1 min)…", 60))

    def measure_fit(self, name: str) -> None:
        """Measure a model with llama-server's own fit in the background (the command fit NAME --measured-only, A72): it runs
        the fit (on a stub of the header when the file is not there) and keeps the answer for the fit table; the
        sentence it prints is the toast."""
        def work() -> None:
            log = os.path.join(self.paths.logs, ".fit.out")
            self.host.make_folder(self.paths.logs)
            proc = start_tool(self.host, [self.host.python, os.path.join(self.paths.repo, "carl.py"), "fit", name,
                                          "--measured-only"], log)
            code = proc.wait(FIT_TIMEOUT)
            said = " ".join(last_lines(self.host, log, 6))
            if code == 0:
                self.notify.post(toast(said or f"{name}: nothing to say.", 20))
            else:
                if code is None:
                    self._stop_job(proc.pid)
                self.notify.post(failure(f"{name}: the fit could not measure it.", " " + said[-160:], 15))
        threading.Thread(target=work, daemon=True).start()
        self.notify.post(toast(f"CARL is measuring {name} with llama-server's own fit (a few seconds)…", 30))

    def hf_lookup(self, repo: str) -> None:
        """Look up the GGUF files of a Hugging Face repo (in a thread) -> a picker."""
        threading.Thread(target=self._hf_lookup, args=(repo,), daemon=True).start()

    def _hf_lookup(self, repo: str) -> None:
        self._job("hf", HFLookup(repo, f"CARL looks for {repo} on Hugging Face…"))
        try:
            r, file, _ = self.svc.store.parse_hf(repo if repo.startswith(("hf:", "http")) else "hf:" + repo)
            if not HF_REPO.fullmatch(r):
                self._job("hf", HFLookup(repo, f"This is not a Hugging Face repo: {clean(repo)}. Use OWNER/REPO."))
                return
            files = [f for f in self.svc.store.hf_files(r) if clean(f[0]) == f[0]]   # names shown on screen
            if file:
                files = [f for f in files if f[0] == file] or files
            if not files:
                self._job("hf", HFLookup(r, f"{r} has no .gguf files."))
                return
            self._job("hf", HFLookup(r, "", files))
            self.notify.post(Found(r, tuple(files)))
        except Exception as e:      # bad name, no network, rate limit, unexpected answer: show it
            self._job("hf", HFLookup(repo, f"The Hugging Face search failed: {e}"))

    # ------------------------------------------------------------ router mode
    def router_load(self, name: str, d: ServerData, unload: bool = False) -> None:
        """Load (the loaded model stops first: --models-max 1) or unload one of a router's models
        (the sessions in the loaded model's slots are saved first)."""
        def work() -> None:
            what = "unload" if unload else "load"
            self.save_before_stop(d)            # the loaded model stops
            try:
                self.collector.endpoint.post(f"/models/{what}", {"model": name}, timeout=600)
                self.notify.post(toast(f"{name}: " + ("unloaded." if unload else "the load started (30 s to 2 min). "
                                                                                 "The Router panel shows when it is "
                                                                                 "loaded."), 10))
            except FETCH_ERRORS as e:
                self.notify.post(failure(f"{name}: CARL cannot {what} it: {e}", "", 10))

        def go() -> None:
            self.notify.post(toast(f"{name} {'unloads' if unload else 'loads'}…", 120 if not unload else 10))
            threading.Thread(target=work, daemon=True).start()
        self.when_idle(d, f"{'unload' if unload else 'load'} {name} (the loaded model stops)", go)

    # ------------------------------------------------------------ a turn is running
    def busy_slots(self, d: ServerData) -> List[int]:
        """The slots writing a reply now (a fresh /slots; the loaded model's in router mode); none
        when the server doesn't answer."""
        if not d.up or (d.router is not None and not d.alias):
            return []
        q = f"?model={urllib.parse.quote(d.alias)}" if d.router is not None else ""
        try:
            slots = [SlotInfo.from_json(x) for x in jlist(json.loads(self.collector.endpoint.get("/slots" + q, timeout=3)))]
        except FETCH_ERRORS:
            return []
        return [s.id for s in slots if s.busy and s.id is not None]

    def turn_slots(self, d: ServerData) -> List[int]:
        """The slots where a client's turn runs (OpenCode and Pi with CARL mark each request of an agent
        loop, tool calls too, until the turn's save or record is on disk)."""
        return self.host.turns(self.paths.slots, d.alias) if d.up and d.alias else []

    def saving(self) -> bool:
        """A saved state was written in the last 2 s (a save may still be going on)."""
        return self.host.saving(self.paths.slots, 2)

    def when_idle(self, d: ServerData, what: str, go: Callable[[], None]) -> None:
        """Run `go` (it stops the model) now when no turn runs; else ask first: wait for the end of the
        turn (its session saved), or stop now (the reply is cut, the session goes back to its last save)."""
        busy, turns = self.busy_slots(d), self.turn_slots(d)
        if not busy and not turns:
            go()
            return
        self.notify.post(Job("drain", Drain(what=what, go=go, busy=busy, turns=turns)))

    def drain_tick(self, d: ServerData) -> None:
        """While waiting (on the dashboard's thread): go on at the second look in a row (0.5 s apart) with no slot
        writing, no turn marked and no save file being written; then save_before_stop saves the recorded sessions."""
        dr = self.notify.waiting()
        if dr is None or not dr.waiting or time.time() - dr.checked < 0.5:
            return
        dr.checked = time.time()
        dr.busy, dr.turns, dr.saving = self.busy_slots(d), self.turn_slots(d), self.saving()
        dr.idle = 0 if dr.busy or dr.turns or dr.saving else dr.idle + 1
        if dr.idle >= 2 or not d.up:
            self.notify.post(Job("drain", None))
            dr.go()

    def save_before_stop(self, d: ServerData) -> None:
        """Before CARL stops the server or its model (Stop, Apply, Auto-tune, a router load): the
        sessions OpenCode and Pi left in the slots, saved to their files (save_recorded)."""
        if d.up and d.alias:
            self.save_recorded(d.alias, d.router is not None)

    def save_recorded(self, model: str, router: bool = True) -> int:
        """Save the sessions the clients recorded in a model's slots (slot_folder.py residents), each only
        while its slot still holds that state (the task id); the number saved. With save = turn every
        session is saved already. Also the cache API's save-recorded (a remote client's router switch)."""
        conf = self.cache_conf(fresh=True)
        if not conf.sessions or conf.save == "turn" or not self.host.free_enough(self.paths.slots):
            return 0
        recs = self.host.residents(self.paths.slots, model)
        if not recs:
            return 0
        ep = self.collector.endpoint
        try:
            slots = {s.id: s for s in (SlotInfo.from_json(x) for x in jlist(json.loads(
                ep.get("/slots" + (f"?model={urllib.parse.quote(model)}" if router else ""), timeout=5))))}
        except FETCH_ERRORS:
            return 0
        n = 0
        for r in recs:
            s = slots.get(r.slot)
            if s is None or s.busy or s.task != r.task:
                continue
            try:
                ep.post(f"/slots/{r.slot}?action=save", {"filename": r.file, **({"model": model} if router else {})},
                        timeout=600)
                self.host.drop_resident(r)
                n += 1
            except FETCH_ERRORS:
                continue
        return n

    # ------------------------------------------------------------ Connect: the client config push
    def push_client_config(self, running: Optional[str] = None, router: Optional[bool] = None,
                           full: bool = False) -> None:
        """Publish the client config (client_config_store.py: the models the configs get; single-model mode: running,
        the model the server runs) for the clients' sync service. full: the message names the version."""
        try:
            version = self.host.publish(os.path.dirname(self.paths.config_file),
                                        self.svc.store.client_models(running, router))
        except (OSError, ValueError) as e:
            self.notify.post(failure(f"CARL cannot send the config: {e}", "", 10))
            return
        self.notify.post(toast(f"Sent the config{f' (version {version})' if full else ''}. The sync service of each "
                               f"computer applies it in a few seconds. OpenCode and Pi use it at their next start.", 12))

    def forget_clients(self, older: float) -> int:
        """Drop the clients the API hasn't seen for `older` seconds (and not connected now)."""
        reg = self.registry
        if reg is None:
            return 0
        gone = [c.id for c in reg.list()                                   # type: ignore[attr-defined]
                if c.connected <= 0 and time.time() - c.last_seen > older]  # type: ignore[attr-defined]
        for cid in gone:
            reg.forget(cid)
        return len(gone)

    def pushed(self) -> str:
        """The last push, as "VERSION at TIME" ("" when nothing is pushed)."""
        doc = self.host.published(os.path.dirname(self.paths.config_file))
        return f"{doc['version']} at {str(doc.get('published', '?')).replace('T', ' ')}" if doc else ""

    # ------------------------------------------------------------ Connect: the client installer
    def start_install(self, config_only: bool) -> None:
        """carl install for this computer (OpenCode and Pi into ~/.local when missing, then their
        configs pointed at this server), or only the configs; output in the Connect tab."""
        if self.install and not self.install.done:
            self.notify.post(toast("The installer is already running. Its output is in the Connect tab.", 6))
            return
        port = self.collector.endpoint.port
        log = os.path.join(self.paths.logs, ".install.out")
        self.host.make_folder(self.paths.logs)
        argv = [self.host.python, os.path.join(self.paths.repo, "carl.py"), "install", "--local", "--port", str(port)]
        argv += ["--config-only"] if config_only else []
        # the installer wraps its output to the card (Phase 23.2)
        env = dict(self.host.env(), CARL_CMD=self.host.words.command, COLUMNS=str(self.install_columns()))
        proc = start_tool(self.host, argv, log, env)
        self._job("install", InstallRun("configs" if config_only else "clients and configs", proc, log))

    # ------------------------------------------------------------ Connect: the client package
    def make_package(self, running: Optional[str] = None, router: Optional[bool] = None) -> None:
        """The client package for another computer (z): the code of carl package, in a thread. Its result
        (the zip, the key warning, or why CARL made none) shows in the Connect tab. running, router: the server's
        model and mode (clients.serving) for the model list."""
        if self.package and not self.package.done:
            self.notify.post(toast("CARL is already making the client package.", 5))
            return
        run = PackageRun(time.time())
        self._job("package", run)
        repo = self.paths.repo

        def work() -> None:
            try:
                out = self.host.make_package(repo, self.svc.store.client_models(running, router),
                                             self.svc.store.load_config())
            except (OSError, ValueError) as e:
                out = Outcome("", f"error: CARL cannot make the client package: {e}", ())
            self.notify.post(PackageDone(run, out))
            self.notify.post(failure("CARL made no client package.", " The Connect tab says why.", 10) if out.error
                             else toast("CARL made the client package. It holds the API key: keep it secret.", 10))
        threading.Thread(target=work, daemon=True).start()

    def show_package(self) -> None:
        """Show the zip in the file manager."""
        run = self.package
        path = run.outcome.path if run and run.outcome else ""
        if not path or not self.host.exists(path):
            self.notify.post(toast("There is no client package to show. Press z to make one.", 6))
            return
        if not self.host.desktop.reveal(path):
            self.notify.post(RevealFailed(path))

    def cancel_install(self) -> None:
        """Stop the installer (its backups stay; re-running it is safe)."""
        if self.install and not self.install.done:
            self._stop_job(self.install.proc.pid)

    # ------------------------------------------------------------ progress (every refresh, on the dashboard's thread)
    def poll(self) -> None:
        """Progress of the download and Auto-tune; their end is announced, and after a tune
        that stopped the server, the server is started again. Every minute: the disk cache within
        its limit."""
        if time.time() - self._trim_at > 60:
            self._trim_at = time.time()
            threading.Thread(target=self.trim_cache, daemon=True).start()
        dl, tn, ins = self.dl, self.tune, self.install
        if dl and not dl.done:
            self._poll_download(dl)
        if tn and not tn.done:
            self._poll_tune(tn)
        if ins and not ins.done:
            self._poll_install(ins)

    def _poll_download(self, dl: Download) -> None:
        now = time.time()
        dl.have = (self.host.file_size(dl.path) if dl.path else 0) + sum(self.host.file_size(p) for p in dl.more)
        dl.hist.append((now, dl.have))
        del dl.hist[:-20]
        h = dl.hist
        dl.rate = (h[-1][1] - h[0][1]) / max(h[-1][0] - h[0][0], 1e-6) if len(h) > 1 else 0
        finished = dl.proc.poll() is not None      # before reading: a finished job's output is complete
        dl.tail = last_lines(self.host, dl.log, 1)
        if not finished:
            return
        dl.done = True
        ok = dl.proc.returncode == 0
        self.models.get(refresh=True)
        self.notify.post(toast(f"{dl.name} is downloaded and checked. OpenCode and Pi do not list it yet: press u in "
                               f"the Connect tab.", 12) if ok else
                         failure(f"The download of {dl.name} failed:", " " + " ".join(dl.tail)[-120:], 12))

    def _poll_tune(self, tn: TuneRun) -> None:
        finished = tn.proc.poll() is not None
        tn.lines = self.host.read_lines(tn.log)
        if not finished:
            return
        tn.done = True
        ok = tn.proc.returncode == 0
        last = tn.events[-1] if tn.lines else None
        self.models.get(refresh=True)
        self.notify.post(toast("Auto-tune is done. " + (last.detail if last and last.kind is Kind.DONE else ""),
                               15) if ok else
                         failure("Auto-tune stopped with an error:", " " + (last.text if last else ""), 15))
        if tn.restart:
            self._restart_after_tune()

    def _poll_install(self, ins: InstallRun) -> None:
        finished = ins.proc.poll() is not None
        ins.lines = self.host.read_lines(ins.log)
        if not finished:
            return
        ins.done = True
        if ins.proc.returncode == 0:
            self.notify.post(toast("OpenCode and Pi now use this server. Open a new terminal, then run opencode or pi.",
                                   15))
        else:
            self.notify.post(failure("The installer stopped with an error.", " Its output is in the Connect tab.", 15))


__all__ = ["CLEAN_ENV", "FETCH_ERRORS", "Paths", "ServerJobs", "TUNE_ALL", "Toast", "server_env", "start_server",
           "start_tool"]

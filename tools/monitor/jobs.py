"""Background work started from the Settings tab: restarting the server with new settings
(and rolling back when it fails), Auto-tune (tools/carl-tune.py), model downloads and
checks (tools/carl.py) and Hugging Face lookups. Children run from argument lists in their
own sessions; their output goes to files under ~/models/logs."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional

from . import api, clientsync, diskcache, fsio, slotpack, system
from .cacheapi import CacheState, Registry
from .collector import Collector
from carl_core.domain.units import file_size

from .fmt import ANSI, DIM, R, RED
from .model import ServerData, SlotInfo, clean, draft_bytes, jlist
from .settings import REINSTALL, Pending, SettingsService, env_from_cmd
from .state import TUNE_ALL, Confirm, Download, Drain, HFLookup, InstallRun, Picker, TuneRun, UIState
from .store import ModelList

# Settings the launchers read from the environment: removed, so config.json (or the
# rollback's own environment) decides and not the environment this monitor started in.
CLEAN_ENV = ("CTX", "SLOTS", "KV", "KV_K", "KV_V", "MODEL", "MODEL_NAME", "ALIAS", "NET", "HOST", "TEMP", "TOP_P",
             "TOP_K", "MIN_P", "PRESENCE", "SPEC", "SPEC_N", "CACHE_RAM", "LLAMA_MODE")
HF_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")   # OWNER/REPO: nothing else goes into the API path


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


def server_env(base: Mapping[str, str], port: int, extra: Mapping[str, str]) -> Dict[str, str]:
    """The environment for host/serve-llama.sh: no monitor, this port, extra on top; without
    CLEAN_ENV and SETTINGS_FILE unless extra sets them ("none": the rollback ignores config.json)."""
    env = dict(base, MONITOR="0", PORT=str(port), **extra)
    for k in CLEAN_ENV + ("SETTINGS_FILE",):
        if k not in extra:
            env.pop(k, None)
    return env


def start_server(paths: Paths, port: int, extra_env: Mapping[str, str], console: str,
                 append: bool = False) -> "subprocess.Popen[bytes]":
    """Start host/serve-llama.sh in the background, output to console (appended for the
    rollback: the failed start's output stays)."""
    cmd = [os.path.join(paths.repo, "host", "serve-llama.sh")]
    with open(console, "a" if append else "w") as out:
        return subprocess.Popen(cmd, env=server_env(os.environ, port, extra_env), stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)


def start_tool(argv: List[str], log: str) -> "subprocess.Popen[bytes]":
    """Run one of CARL's Python tools in its own session, output to log."""
    with open(log, "w") as out:
        return subprocess.Popen([sys.executable, *argv], stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)


def wait_up(proc: "subprocess.Popen[bytes]", port: int, key: Callable[[], str], vm_addr: str,
            host: Optional[str] = None, secs: float = 900) -> bool:
    """True once the new server answers /health (on host, the VM address or localhost:
    it may use another address); False if it exits or takes longer than secs. key() is
    asked each time: a first start creates the key file."""
    t_end = time.time() + secs
    while time.time() < t_end:
        if proc.poll() is not None:
            return False
        for h in ([host] if host else []) + [vm_addr, "127.0.0.1"]:
            if api.health_ok(h, port, key()):
                return True
        time.sleep(2)
    return False


def last_lines(path: str, n: int) -> List[str]:
    """The last n non-blank-ended lines of a job's output file ([] if there is none)."""
    return (fsio.read_text(path, errors="replace") or "").strip().splitlines()[-n:]


_REFUSAL = re.compile(r"^(error|refused)\b", re.I)


def launcher_errors(path: str, most: int = 8) -> List[str]:
    """What the launcher said when it refused or failed a start: its first "error:" (or "Refused") line and
    the indented lines after it (the reason comes first, the advice after), at most `most` lines; else
    its last 3 lines. [] when there is no output."""
    lines = [clean(ANSI.sub("", x)).rstrip() for x in (fsio.read_text(path, errors="replace") or "").splitlines()]
    for i, x in enumerate(lines):
        if _REFUSAL.match(x.strip()):
            out = [x.strip()]
            for y in lines[i + 1:]:
                if not y.strip() or not y[:1].isspace():
                    break
                out.append(y.strip())
            return out[:most]
    return [x for x in lines if x.strip()][-3:]


class ServerJobs:
    """Runs the Settings tab's background work and reports it in the UI state."""

    def __init__(self, ui: UIState, collector: Collector, svc: SettingsService, paths: Paths, vm_addr: str) -> None:
        self.ui = ui
        self.collector = collector
        self.svc = svc
        self.models: ModelList = svc.models
        self.paths = paths
        self.vm_addr = vm_addr
        self._conf, self._conf_at = diskcache.CacheConfig(), 0.0
        self.registry: Optional[Registry] = None     # the API's clients (app.cache_api sets it)
        self._trim_at = 0.0

    def cache_conf(self, fresh: bool = False) -> diskcache.CacheConfig:
        """The Caching settings (config.json "cache"), read again every few seconds."""
        if fresh or time.time() - self._conf_at > 5:
            try:
                self._conf = diskcache.config_of(self.svc.store.load_config())
            except Exception:       # config.json unreadable: the defaults (the Server panel says why)
                self._conf = diskcache.CacheConfig()
            self._conf_at = time.time()
        return self._conf

    def trim_cache(self, keep: str = "") -> bool:
        """Remove the oldest saved states over the disk limit (diskcache.py); False when `keep`
        itself had to go (alone over the limit)."""
        folder = self.paths.slots
        diskcache.remove(folder, diskcache.legacy(folder))
        if self.cache_conf().share:
            slotpack.tidy(folder)                   # new saves as patches against their prompt, copies gone
        gone = diskcache.over_budget(diskcache.listing(folder), self.cache_conf().limit, keep)
        if not gone:
            return True
        diskcache.remove(folder, gone)
        return keep not in gone

    def _wait_up(self, proc: "subprocess.Popen[bytes]", port: int, host: Optional[str] = None) -> bool:
        return wait_up(proc, port, lambda: self.collector.endpoint.key, self.vm_addr, host)

    # ------------------------------------------------------------ settings: restart
    def restart(self, p: Pending, d: ServerData) -> None:
        """Save the settings, stop the server, start it with them (in a thread); while a reply is
        being written, ask first (when_idle)."""
        def go() -> None:
            self.ui.restart, self.ui.restart_t = "CARL saves the settings…", time.time()
            self.ui.start_error = []
            threading.Thread(target=self._restart, args=(dict(p), d), daemon=True).start()
        self.when_idle(d, "apply the settings (the server restarts)", go)

    def _restart(self, p: Pending, d: ServerData) -> None:
        """Save, stop the old server, start the new one; on failure, put config.json back
        and start the old server again."""
        ui, ep = self.ui, self.collector.endpoint
        path = self.paths.config_file
        old_cmd, old_pid, port = d.cmd, d.target_pid or d.pid, ep.port
        console = self.paths.console(port)
        try:
            old_text = fsio.read_text(path) if os.path.exists(path) else None   # the rollback puts it back
            if old_text is None and os.path.exists(path):
                raise OSError(f"cannot read {path}")
            os.makedirs(os.path.dirname(console), exist_ok=True)
            self.svc.save(p)
            ui.restart = "CARL saves the sessions in the slots…"
            self.save_before_stop(d)
            ui.restart = "CARL stops the server…"
            self.collector.server_pid = None
            system.stop_pid(old_pid)
            ui.restart = "The server starts with the new settings: the model loads…"
            proc = start_server(self.paths, port, {}, console)
            self.collector.follow(port, proc.pid, console)
            chosen = str(p.get("net"))
            if self._wait_up(proc, port, host=chosen if chosen.count(".") == 3 else None):
                changed = [r.label.lower() for r in self.svc.rows(p)
                           if r.key in REINSTALL and str(p[r.key]) != str(ui.set_run.get(r.key))]
                ui.toast("The server restarted with the new settings."
                         + (f" The {' and the '.join(changed)} changed: update OpenCode and Pi (the Connect tab, u)."
                            if changed else ""), 12)
                ui.restart = None
                ui.pending = None
                if ui.install_after_restart:
                    ui.install_after_restart = False
                    self.start_install(config_only=True)
                return
            errors = launcher_errors(console)
            system.stop_pid(proc.pid)
            if old_text is None:
                os.remove(path)
            else:
                fsio.write_private(path, old_text)
            back = False
            if old_pid and "llama-server" in old_cmd:
                ui.restart = "The new settings failed. The old server starts again…"
                proc = start_server(self.paths, port, env_from_cmd(old_cmd), console, append=True)
                self.collector.follow(port, proc.pid, console)
                back = self._wait_up(proc, port)
            ui.start_error = errors
            ui.toast(f"{RED}The new settings failed.{R} " + ("The old server runs again." if back else "No server runs now.")
                     + " The Server panel shows why.", 20)
        except Exception as e:      # anything (disk, config, process): the restart stops here and says why
            ui.toast(f"{RED}The restart failed: {e}{R}", 20)
        ui.restart = None
        ui.install_after_restart = False

    # ------------------------------------------------------------ Auto-tune
    def run_tune(self, d: ServerData, confirmed: bool = False) -> None:
        """Tune ui.tune_model; asks first when it has to stop a running server."""
        ui = self.ui
        if ui.tune and ui.tune.proc.poll() is None:
            ui.toast("Auto-tune runs already.", 5)
            return
        pid = d.target_pid if not d.exited else None
        model = ui.tune_model or ""
        n = len(self.models.downloaded())
        what = (f"all downloaded models, one after the other ({n} × about 5-10 min)" if model == TUNE_ALL
                else f"{model} (about 5-10 min)")
        if pid and system.pid_alive(pid) and not confirmed:
            ui.confirm2 = Confirm("STOP THE SERVER FOR AUTO-TUNE?", [
                "Auto-tune needs the GPU for itself. It stops the server now." + (f" (pid {pid})" if ui.full else ""),
                f"Then it measures {what}, and it starts the server again with the saved settings.",
                "Requests in progress stop."], "tyes", yes_label="Stop the server and tune")
            return
        restart = bool(pid and system.pid_alive(pid))
        depth = ui.tune_depth

        def work() -> None:
            if restart:
                ui.restart, ui.restart_t = "CARL saves the sessions in the slots…", time.time()
                self.save_before_stop(d)
                ui.restart = "CARL stops the server for Auto-tune…"
                self.collector.server_pid = None
                system.stop_pid(pid)
                ui.restart = None
            log = os.path.join(self.paths.logs, ".tune.out")
            os.makedirs(self.paths.logs, exist_ok=True)
            flag = {"quick": ["--quick"], "long": ["--long"]}.get(depth, [])
            proc = start_tool([os.path.join(self.paths.repo, "tools", "carl-tune.py"), "all" if model == TUNE_ALL else model]
                              + flag, log)
            ui.tune = TuneRun(model=model, proc=proc, log=log, restart=restart)
        if restart:
            self.when_idle(d, "auto-tune (the server stops)", lambda: threading.Thread(target=work, daemon=True).start())
        else:
            threading.Thread(target=work, daemon=True).start()

    def cancel_tune(self) -> None:
        """Stop Auto-tune (SIGINT: it cleans up its test server)."""
        if self.ui.tune:
            system.kill_group(self.ui.tune.proc.pid, signal.SIGINT)

    def _restart_after_tune(self) -> None:
        """Start the server again after a tune stopped it: llama.cpp with the saved settings."""
        def work() -> None:
            port = self.collector.endpoint.port          # where the server ran before the tune stopped it
            console = self.paths.console(port)
            self.ui.restart, self.ui.restart_t = "The server starts again with the saved settings…", time.time()
            proc = start_server(self.paths, port, {}, console)
            self.collector.follow(port, proc.pid, console)
            ok = self._wait_up(proc, port)
            self.ui.restart = None
            self.ui.pending = None
            self.ui.toast("The server runs again." if ok else f"{RED}The server did not start again.{R} The Log tab tells why.",
                          12)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ models
    def start_download(self, spec: str) -> None:
        """Download a catalogue model by name (with its MTP drafter, when it has one), or
        hf:REPO/FILE.gguf (resumable)."""
        ui = self.ui
        if ui.dl and ui.dl.proc.poll() is None:
            ui.toast("A download runs already. To cancel it: c in the Models panel.", 6)
            return
        more: List[str] = []
        if spec.startswith("hf:"):
            _, file, _ = self.svc.store.parse_hf(spec)
            base = os.path.basename(file or "")
            info = next((x for x in (ui.hf.files if ui.hf else []) if x[0] == file), None)
            name = re.sub(r"[^a-z0-9._-]+", "-", base[:-5].lower())
            path: Optional[str] = os.path.join(self.svc.store.models_dir(), base)
            total = (info[1] or 0) if info else 0
        else:
            m = self.models.by_name(spec)
            if not m:
                ui.toast(f"{RED}{spec} is not a known model.{R}", 6)
                return
            name, path, total = spec, m["path"], m["bytes"]
            if m.get("draft") and m["status"] == "downloaded":     # carl.py fetches only the missing drafter
                name, path, total = f"{spec} MTP drafter", m.get("draft_path"), draft_bytes(m)
            elif m.get("draft"):                                    # the model, then its drafter
                more, total = [str(m.get("draft_path", ""))], total + draft_bytes(m)
        log = os.path.join(self.paths.logs, ".download.out")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        proc = start_tool([os.path.join(self.paths.repo, "tools", "carl.py"), "download", spec], log)
        ui.dl = Download(name=name, path=path, total=total, proc=proc, log=log, more=more)
        ui.toast(f"The download of {name} started. If you cancel it, the downloaded part stays on the disk: d continues "
                 f"it.", 6)

    def cancel_download(self) -> None:
        """Stop the download; the partial file stays for a resume."""
        if self.ui.dl:
            system.kill_group(self.ui.dl.proc.pid, signal.SIGTERM)
            self.ui.toast("You cancelled the download. The downloaded part stays on the disk: press d to continue.", 6)

    def verify(self, name: str) -> None:
        """Check a model's SHA-256 in the background (~1 min)."""
        def work() -> None:
            ok = subprocess.run([sys.executable, os.path.join(self.paths.repo, "tools", "carl.py"), "verify", name],
                                capture_output=True).returncode == 0
            self.ui.toast(f"{name}: the file is correct (the checksum agrees)." if ok else
                          f"{RED}{name}: the file is damaged (the checksum is wrong).{R} Download it again (d).", 10)
        threading.Thread(target=work, daemon=True).start()
        self.ui.toast(f"CARL checks the file of {name} (SHA-256, about 1 min)…", 60)

    def hf_lookup(self, repo: str) -> None:
        """Look up the GGUF files of a Hugging Face repo (in a thread) -> a picker."""
        threading.Thread(target=self._hf_lookup, args=(repo,), daemon=True).start()

    def _hf_lookup(self, repo: str) -> None:
        ui = self.ui
        ui.hf = HFLookup(repo, f"CARL looks for {repo} on Hugging Face…")
        try:
            r, file, _ = self.svc.store.parse_hf(repo if repo.startswith(("hf:", "http")) else "hf:" + repo)
            if not HF_REPO.fullmatch(r):
                ui.hf = HFLookup(repo, f"This is not a Hugging Face repo: {clean(repo)}. Use OWNER/REPO.")
                return
            files = [f for f in self.svc.store.hf_files(r) if clean(f[0]) == f[0]]   # names shown on screen
            if file:
                files = [f for f in files if f[0] == file] or files
            if not files:
                ui.hf = HFLookup(r, f"{r} has no .gguf files.")
                return
            ui.hf = HFLookup(r, "", files)
            ui.picker = Picker(title=f"HUGGING FACE · {r}", on_pick="pickhf", noun="GGUF files",
                               header=f"{DIM}{'':2}{'file':<60} {'download':>9}  checksum{R}",
                               foot="Enter downloads it to the models folder. You can stop and continue the download. "
                                    "CARL checks the file when it has a checksum.",
                               items=[(f, f"{f:<60} {file_size(b):>9}  "
                                          f"{DIM}{'known' if sha else 'none: CARL cannot check this file'}{R}")
                                      for f, b, sha in files])
        except Exception as e:      # bad name, no network, rate limit, unexpected answer: show it
            ui.hf = HFLookup(repo, f"The Hugging Face search failed: {e}")

    # ------------------------------------------------------------ router mode
    def router_load(self, name: str, d: ServerData, unload: bool = False) -> None:
        """Load (the loaded model stops first: --models-max 1) or unload one of a router's models
        (the sessions in the loaded model's slots are saved first)."""
        def work() -> None:
            what = "unload" if unload else "load"
            self.save_before_stop(d)            # the loaded model stops
            try:
                self.collector.endpoint.post(f"/models/{what}", {"model": name}, timeout=600)
                self.ui.toast(f"{name}: " + ("unloaded." if unload else "the load started (30 s to 2 min). The Router "
                                                                        "panel shows when it is loaded."), 10)
            except api.FETCH_ERRORS as e:
                self.ui.toast(f"{RED}{name}: CARL cannot {what} it: {e}{R}", 10)
        def go() -> None:
            self.ui.toast(f"{name} {'unloads' if unload else 'loads'}…", 120 if not unload else 10)
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
        except (api.FETCH_ERRORS + (ValueError,)):
            return []
        return [s.id for s in slots if s.busy and s.id is not None]

    def turn_slots(self, d: ServerData) -> List[int]:
        """The slots where a client's turn runs (OpenCode and Pi with CARL mark each request of an agent
        loop, tool calls too, until the turn's save or record is on disk)."""
        return CacheState(self.paths.slots).turns(d.alias) if d.up and d.alias else []

    def saving(self) -> bool:
        """A saved state was written in the last 2 s (a save may still be going on)."""
        try:
            return any(n.startswith("carl-") and n.endswith(".bin")
                       and time.time() - os.path.getmtime(os.path.join(self.paths.slots, n)) < 2
                       for n in os.listdir(self.paths.slots))
        except OSError:
            return False

    def when_idle(self, d: ServerData, what: str, go: Callable[[], None]) -> None:
        """Run `go` (it stops the model) now when no turn runs; else ask first: wait for the end of the
        turn (its session saved), or stop now (the reply is cut, the session goes back to its last save)."""
        busy, turns = self.busy_slots(d), self.turn_slots(d)
        if not busy and not turns:
            go()
            return
        self.ui.drain = Drain(what=what, go=go, busy=busy, turns=turns)

    def drain_tick(self, d: ServerData) -> None:
        """While waiting: go on at the second look in a row (0.5 s apart) with no slot writing, no turn
        marked and no save file being written; then save_before_stop saves the recorded sessions."""
        dr = self.ui.drain
        if dr is None or not dr.waiting or time.time() - dr.checked < 0.5:
            return
        dr.checked = time.time()
        dr.busy, dr.turns, dr.saving = self.busy_slots(d), self.turn_slots(d), self.saving()
        dr.idle = 0 if dr.busy or dr.turns or dr.saving else dr.idle + 1
        if dr.idle >= 2 or not d.up:
            self.ui.drain = None
            dr.go()

    def save_before_stop(self, d: ServerData) -> None:
        """Before CARL stops the server or its model (Stop, Apply, Auto-tune, a router load): the
        sessions OpenCode and Pi left in the slots, saved to their files (save_recorded)."""
        if d.up and d.alias:
            self.save_recorded(d.alias, d.router is not None)

    def save_recorded(self, model: str, router: bool = True) -> int:
        """Save the sessions the clients recorded in a model's slots (diskcache.residents), each only
        while its slot still holds that state (the task id); the number saved. With save = turn every
        session is saved already. Also the cache API's save-recorded (a remote client's router switch)."""
        conf = self.cache_conf(fresh=True)
        if not conf.sessions or conf.save == "turn" or not diskcache.free_enough(self.paths.slots):
            return 0
        recs = diskcache.residents(self.paths.slots, model)
        if not recs:
            return 0
        ep = self.collector.endpoint
        try:
            slots = {s.id: s for s in (SlotInfo.from_json(x) for x in jlist(json.loads(
                ep.get("/slots" + (f"?model={urllib.parse.quote(model)}" if router else ""), timeout=5))))}
        except (api.FETCH_ERRORS + (ValueError,)):
            return 0
        n = 0
        for r in recs:
            s = slots.get(r.slot)
            if s is None or s.busy or s.task != r.task:
                continue
            try:
                ep.post(f"/slots/{r.slot}?action=save", {"filename": r.file, **({"model": model} if router else {})},
                        timeout=600)
                diskcache.drop_resident(r)
                n += 1
            except api.FETCH_ERRORS:
                continue
        return n

    # ------------------------------------------------------------ Connect: the client config push
    def push_client_config(self) -> None:
        """Publish the client config (clientsync.py: the installed models) for the clients' sync service."""
        try:
            version = clientsync.publish(os.path.dirname(self.paths.config_file), self.svc.store.client_models())
        except (OSError, ValueError) as e:
            self.ui.toast(f"{RED}CARL cannot send the config: {e}{R}", 10)
            return
        self.ui.toast(f"Sent the config{f' (version {version})' if self.ui.full else ''}. The sync service of each "
                      f"computer applies it in a few seconds. OpenCode and Pi use it at their next start.", 12)

    def forget_clients(self, older: float) -> int:
        """Drop the clients the API hasn't seen for `older` seconds (and not connected now)."""
        reg = self.registry
        if reg is None:
            return 0
        gone = [c.id for c in reg.list() if c.connected <= 0 and time.time() - c.last_seen > older]
        for cid in gone:
            reg.forget(cid)
        return len(gone)

    def pushed(self) -> str:
        """The last push, as "VERSION at TIME" ("" when nothing is pushed)."""
        doc = clientsync.published(os.path.dirname(self.paths.config_file))
        return f"{doc['version']} at {str(doc.get('published', '?')).replace('T', ' ')}" if doc else ""

    # ------------------------------------------------------------ Connect: the client installer
    def start_install(self, config_only: bool) -> None:
        """./carl.sh install for this Mac (OpenCode and Pi into ~/.local when missing, then their
        configs pointed at this server), or only the configs; output in the Connect tab."""
        ui = self.ui
        if ui.install and not ui.install.done:
            ui.toast("The installer runs already. Its output is in the Connect tab.", 6)
            return
        port = self.collector.endpoint.port
        log = os.path.join(self.paths.logs, ".install.out")
        os.makedirs(self.paths.logs, exist_ok=True)
        argv = [os.path.join(self.paths.repo, "host", "serve.sh"), "install", "--local", "--port", str(port)]
        argv += ["--config-only"] if config_only else []
        with open(log, "w") as out:
            proc = subprocess.Popen(argv, env=dict(os.environ, CARL_CMD="./carl.sh"), stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        ui.install = InstallRun("configs" if config_only else "clients and configs", proc, log)
        ui.install_shown = True

    def cancel_install(self) -> None:
        """Stop the installer (its backups stay; re-running it is safe)."""
        if self.ui.install and not self.ui.install.done:
            system.kill_group(self.ui.install.proc.pid, signal.SIGTERM)

    # ------------------------------------------------------------ progress (every refresh)
    def poll(self) -> None:
        """Progress of the download and Auto-tune; their end is announced, and after a tune
        that stopped the server, the server is started again. Every minute: the disk cache within
        its limit."""
        if time.time() - self._trim_at > 60:
            self._trim_at = time.time()
            threading.Thread(target=self.trim_cache, daemon=True).start()
        dl, tn, ins = self.ui.dl, self.ui.tune, self.ui.install
        if dl and not dl.done:
            self._poll_download(dl)
        if tn and not tn.done:
            self._poll_tune(tn)
        if ins and not ins.done:
            self._poll_install(ins)

    def _poll_download(self, dl: Download) -> None:
        now = time.time()
        dl.have = (fsio.file_size(dl.path) if dl.path else 0) + sum(fsio.file_size(p) for p in dl.more)
        dl.hist.append((now, dl.have))
        del dl.hist[:-20]
        h = dl.hist
        dl.rate = (h[-1][1] - h[0][1]) / max(h[-1][0] - h[0][0], 1e-6) if len(h) > 1 else 0
        finished = dl.proc.poll() is not None      # before reading: a finished job's output is complete
        dl.tail = last_lines(dl.log, 1)
        if not finished:
            return
        dl.done = True
        ok = dl.proc.returncode == 0
        self.models.get(refresh=True)
        self.ui.toast(f"{dl.name} is downloaded and checked. OpenCode and Pi do not list it yet: press u in the Connect "
                      f"tab." if ok else f"{RED}The download of {dl.name} failed:{R} " + " ".join(dl.tail)[-120:], 12)

    def _poll_tune(self, tn: TuneRun) -> None:
        finished = tn.proc.poll() is not None
        tn.lines = fsio.read_lines(tn.log)
        if not finished:
            return
        tn.done = True
        ok = tn.proc.returncode == 0
        lines = tn.lines
        self.models.get(refresh=True)
        self.ui.toast(("Auto-tune is done. " + (lines[-1][5:] if lines and lines[-1].startswith("DONE") else "") if ok
                       else f"{RED}Auto-tune stopped with an error:{R} " + (lines[-1] if lines else "")), 15)
        if tn.restart:
            self._restart_after_tune()

    def _poll_install(self, ins: InstallRun) -> None:
        finished = ins.proc.poll() is not None
        ins.lines = fsio.read_lines(ins.log)
        if not finished:
            return
        ins.done = True
        if ins.proc.returncode == 0:
            self.ui.toast("OpenCode and Pi now use this server. Open a new terminal, then run opencode or pi.", 15)
        else:
            self.ui.toast(f"{RED}The installer stopped with an error.{R} Its output is in the Connect tab.", 15)

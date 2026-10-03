"""Background work started from the Settings tab: restarting the server with new settings
(and rolling back when it fails), Auto-tune (tools/carl-tune.py), model downloads and
checks (tools/carl.py) and Hugging Face lookups. Children run from argument lists in their
own sessions; their output goes to files under ~/models/logs."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional

from . import api, fsio, system
from .collector import Collector
from .fmt import DIM, R, RED, size
from .model import ServerData, clean
from .settings import REINSTALL, Pending, SettingsService, env_from_cmd
from .state import Confirm, Download, HFLookup, InstallRun, Picker, TuneRun, UIState
from .store import ModelList

# Settings the launchers read from the environment: removed, so config.json (or the
# rollback's own environment) decides and not the environment this monitor started in.
CLEAN_ENV = ("CTX", "SLOTS", "KV", "KV_K", "KV_V", "MODEL", "MODEL_NAME", "ALIAS", "NET", "HOST", "TEMP", "TOP_P",
             "TOP_K", "MIN_P", "PRESENCE", "SPEC", "SPEC_N", "CACHE_RAM")
HF_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")   # OWNER/REPO: nothing else goes into the API path


@dataclass(frozen=True)
class Paths:
    """Where the launchers, logs and settings are."""
    repo: str
    logs: str           # ~/models/logs
    config_file: str

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


class ServerJobs:
    """Runs the Settings tab's background work and reports it in the UI state."""

    def __init__(self, ui: UIState, collector: Collector, svc: SettingsService, paths: Paths, vm_addr: str) -> None:
        self.ui = ui
        self.collector = collector
        self.svc = svc
        self.models: ModelList = svc.models
        self.paths = paths
        self.vm_addr = vm_addr

    def _wait_up(self, proc: "subprocess.Popen[bytes]", port: int, host: Optional[str] = None) -> bool:
        return wait_up(proc, port, lambda: self.collector.endpoint.key, self.vm_addr, host)

    # ------------------------------------------------------------ settings: restart
    def restart(self, p: Pending, d: ServerData) -> None:
        """Save the settings, stop the server, start it with them (in a thread)."""
        self.ui.restart = "saving the settings…"
        threading.Thread(target=self._restart, args=(dict(p), d), daemon=True).start()

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
                raise OSError(f"can't read {path}")
            os.makedirs(os.path.dirname(console), exist_ok=True)
            self.svc.save(p)
            ui.restart = f"stopping the server (pid {old_pid})…"
            self.collector.server_pid = None
            system.stop_pid(old_pid)
            ui.restart = "starting llama.cpp with the new settings (the model loads)…"
            proc = start_server(self.paths, port, {}, console)
            self.collector.follow(port, proc.pid, console)
            chosen = str(p.get("net"))
            if self._wait_up(proc, port, host=chosen if chosen.count(".") == 3 else None):
                changed = [r.label for r in self.svc.rows(p)
                           if r.key in REINSTALL and str(p[r.key]) != str(ui.set_run.get(r.key))]
                ui.toast("restarted with the new settings"
                         + (f"; {' and '.join(changed)} changed: run install.sh again on each client" if changed else ""), 12)
                ui.restart = None
                ui.pending = None
                return
            tail = last_lines(console, 3)
            system.stop_pid(proc.pid)
            if old_text is None:
                os.remove(path)
            else:
                fsio.write_private(path, old_text)
            back = False
            if old_pid and "llama-server" in old_cmd:
                ui.restart = "the new settings failed: starting the old server again…"
                proc = start_server(self.paths, port, env_from_cmd(old_cmd), console, append=True)
                self.collector.follow(port, proc.pid, console)
                back = self._wait_up(proc, port)
            ui.toast(f"{RED}new settings failed{R}: " + ("the old server runs again" if back else "no server runs now")
                     + f" · {' | '.join(tail)[-150:]}", 20)
        except Exception as e:      # anything (disk, config, process): the restart stops here and says why
            ui.toast(f"{RED}restart failed: {e}{R}", 20)
        ui.restart = None

    # ------------------------------------------------------------ Auto-tune
    def run_tune(self, d: ServerData, confirmed: bool = False) -> None:
        """Tune ui.tune_model; asks first when it has to stop a running server."""
        ui = self.ui
        if ui.tune and ui.tune.proc.poll() is None:
            ui.toast("auto-tune is already running", 5)
            return
        pid = d.target_pid if not d.exited else None
        model = ui.tune_model or ""
        if pid and system.pid_alive(pid) and not confirmed:
            ui.confirm2 = Confirm("AUTO-TUNE?", [
                f"Auto-tune needs the GPU to itself: it stops the running server (pid {pid}),",
                f"tunes {model} (~5-10 min), then starts the server again with the saved settings.",
                "Requests in progress stop."], "tyes")
            return
        restart = bool(pid and system.pid_alive(pid))
        quick = ui.tune_quick

        def work() -> None:
            if restart:
                ui.restart = f"stopping the server (pid {pid}) for auto-tune…"
                self.collector.server_pid = None
                system.stop_pid(pid)
                ui.restart = None
            log = os.path.join(self.paths.logs, ".tune.out")
            os.makedirs(self.paths.logs, exist_ok=True)
            proc = start_tool([os.path.join(self.paths.repo, "tools", "carl-tune.py"), model] + (["--quick"] if quick else []), log)
            ui.tune = TuneRun(model=model, proc=proc, log=log, restart=restart)
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
            self.ui.restart = "starting the server again with the saved (and newly tuned) settings…"
            proc = start_server(self.paths, port, {}, console)
            self.collector.follow(port, proc.pid, console)
            ok = self._wait_up(proc, port)
            self.ui.restart = None
            self.ui.pending = None
            self.ui.toast("server running again" if ok else f"{RED}the server did not start again{R}: see the Log tab", 12)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ models
    def start_download(self, spec: str) -> None:
        """Download a catalogue model by name, or hf:REPO/FILE.gguf (resumable)."""
        ui = self.ui
        if ui.dl and ui.dl.proc.poll() is None:
            ui.toast("a download is already running (press c in the Models panel to cancel it)", 6)
            return
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
                ui.toast(f"{RED}unknown model {spec}{R}", 6)
                return
            name, path, total = spec, m["path"], m["bytes"]
        log = os.path.join(self.paths.logs, ".download.out")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        proc = start_tool([os.path.join(self.paths.repo, "tools", "carl.py"), "download", spec], log)
        ui.dl = Download(name=name, path=path, total=total, proc=proc, log=log)
        ui.toast(f"downloading {name} (resumable: cancelling keeps the part)", 6)

    def cancel_download(self) -> None:
        """Stop the download; the partial file stays for a resume."""
        if self.ui.dl:
            system.kill_group(self.ui.dl.proc.pid, signal.SIGTERM)
            self.ui.toast("download cancelled (the part stays: press d to resume it)", 6)

    def verify(self, name: str) -> None:
        """Check a model's SHA-256 in the background (~1 min)."""
        def work() -> None:
            ok = subprocess.run([sys.executable, os.path.join(self.paths.repo, "tools", "carl.py"), "verify", name],
                                capture_output=True).returncode == 0
            self.ui.toast(f"{name}: checksum OK" if ok else f"{RED}{name}: checksum mismatch{R}", 10)
        threading.Thread(target=work, daemon=True).start()
        self.ui.toast(f"verifying {name} (sha256, ~1 min)…", 60)

    def hf_lookup(self, repo: str) -> None:
        """Look up the GGUF files of a Hugging Face repo (in a thread) -> a picker."""
        threading.Thread(target=self._hf_lookup, args=(repo,), daemon=True).start()

    def _hf_lookup(self, repo: str) -> None:
        ui = self.ui
        ui.hf = HFLookup(repo, f"looking up {repo} on Hugging Face…")
        try:
            r, file, _ = self.svc.store.parse_hf(repo if repo.startswith(("hf:", "http")) else "hf:" + repo)
            if not HF_REPO.fullmatch(r):
                ui.hf = HFLookup(repo, f"not a Hugging Face repo: {clean(repo)} (OWNER/REPO)")
                return
            files = [f for f in self.svc.store.hf_files(r) if clean(f[0]) == f[0]]   # names shown on screen
            if file:
                files = [f for f in files if f[0] == file] or files
            if not files:
                ui.hf = HFLookup(r, f"{r} has no .gguf files")
                return
            ui.hf = HFLookup(r, "", files)
            ui.picker = Picker(title=f"HUGGING FACE · {r}", on_pick="pickhf", noun="GGUF files",
                               header=f"{DIM}{'':2}{'file':<60} {'size':>9}{R}",
                               foot="↑↓ select · Enter downloads it to the models folder (resumable, checked against its SHA-256)",
                               items=[(f, f"{f:<60} {size(b):>9}  {DIM}sha256 {clean(sha[:12]) if sha else '?'}{R}")
                                      for f, b, sha in files])
        except Exception as e:      # bad name, no network, rate limit, unexpected answer: show it
            ui.hf = HFLookup(repo, f"Hugging Face lookup failed: {e}")

    # ------------------------------------------------------------ Connect: the client installer
    def start_install(self, config_only: bool) -> None:
        """./carl.sh install for this Mac (OpenCode and Pi into ~/.local when missing, then their
        configs pointed at this server), or only the configs; output in the Connect tab."""
        ui = self.ui
        if ui.install and not ui.install.done:
            ui.toast("the installer is already running: its output is in the Connect tab", 6)
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
        that stopped the server, the server is started again."""
        dl, tn, ins = self.ui.dl, self.ui.tune, self.ui.install
        if dl and not dl.done:
            self._poll_download(dl)
        if tn and not tn.done:
            self._poll_tune(tn)
        if ins and not ins.done:
            self._poll_install(ins)

    def _poll_download(self, dl: Download) -> None:
        now = time.time()
        dl.have = fsio.file_size(dl.path) if dl.path else 0
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
        self.ui.toast(f"{dl.name}: " + ("downloaded and verified" if ok else f"{RED}download failed{R}: " + " ".join(dl.tail)[-120:]), 12)

    def _poll_tune(self, tn: TuneRun) -> None:
        finished = tn.proc.poll() is not None
        tn.lines = fsio.read_lines(tn.log)
        if not finished:
            return
        tn.done = True
        ok = tn.proc.returncode == 0
        lines = tn.lines
        self.models.get(refresh=True)
        self.ui.toast(("auto-tune finished: " + (lines[-1][5:] if lines and lines[-1].startswith("DONE") else "") if ok
                     else f"{RED}auto-tune failed{R}: " + (lines[-1] if lines else "")), 15)
        if tn.restart:
            self._restart_after_tune()

    def _poll_install(self, ins: InstallRun) -> None:
        finished = ins.proc.poll() is not None
        ins.lines = fsio.read_lines(ins.log)
        if not finished:
            return
        ins.done = True
        if ins.proc.returncode == 0:
            self.ui.toast("OpenCode and Pi point at this server: open a new terminal, then run opencode or pi", 15)
        else:
            self.ui.toast(f"{RED}the installer failed{R}: see its output in the Connect tab", 15)

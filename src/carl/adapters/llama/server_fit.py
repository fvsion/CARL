"""llama-server's own fit of a start (implements ServerFit, contract C-060; decision A67): the server started with the
start's argv and --fit on -lv 4 on a local port of its own (domain/memory/server_fit.argv_of), through ProcessControl,
stopped by its pid as soon as its log says the fit is done (about 1 s; no weight is loaded). The answer is kept in the
settings folder by the build, the GPUs, the model files (size and a hash of the header) and the flags, so the same start
asks the server once; what is kept is the breakdown's lines (server_fit.Breakdown.text), not the whole log.
A fit that cannot run (no program, a file that is not there, a run that never reaches the fit, no free port) gives None:
the start then says the need is CARL's estimate (B).

A model that is not on disk (Auto fit's pick before its download: application/fit.py, FitService.measured_fit) is fitted
on a sparse stub (adapters/llama/gguf_stub.py) of its header, CARL's 24 MB range read, in the cache folder, removed
after; that answer is kept by the file's name on Hugging Face at its revision, so the header is read once.
A drafter in its own file is recorded after its first real start: the start names the log (watch_draft), and the next
fit reads the draft context's buffers from it (domain/memory/server_fit.draft_record).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from ...domain.hardware.gpu import CUDA_ORDER
from ...domain.memory import server_fit as sf
from ...ports import LlamaCpp, ProcessControl
from ..common.json_files import write_private
from . import gguf_stub

TIMEOUT = 60.0                     # a first CUDA run loads the driver; the fit itself is 0.4 to 1.9 s
POLL = 0.1
STOP = 10.0
KEPT = 200                         # answers kept in the file; the oldest go first
FILE = "server-fits.json"          # {"fits": {key: breakdown lines}, "drafts": {key: bytes}, "watch": {key: log path}}
Stat = Callable[[str], Optional[Tuple[int, str]]]
HEAD = 1 << 20                     # the bytes of a model file whose hash names it in the key (its header's first MiB)


def free_port() -> Optional[int]:
    """A free port on 127.0.0.1, as the system hands one out (None when none can be bound)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((sf.LOCAL, 0))
            return int(s.getsockname()[1])
    except OSError:
        return None


class LlamaServerFit:
    """The ServerFit of llama.cpp. llama: where llama-server is; conf_dir: the settings folder (the answers file); procs:
    starts and stops the fit; env: the environment it runs with (the start's, from the composition); port: a free local
    port for it (None: none free); extend: how a stub is made sparse (Windows has its own); stat: (size, header hash) of a file;
    gpus: the GPUs' names and sizes in words (the key's; asked once, when the first key is made); clock and sleep: the
    time (the tests give theirs)."""

    def __init__(self, llama: LlamaCpp, conf_dir: str, procs: ProcessControl, env: Optional[Mapping[str, str]] = None,
                 port: Callable[[], Optional[int]] = free_port, extend: gguf_stub.Extend = gguf_stub.truncate,
                 stat: Optional[Stat] = None, gpus: Callable[[], str] = lambda: "",
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = TIMEOUT) -> None:
        self.llama, self.conf_dir, self.procs, self.port, self.extend = llama, conf_dir, procs, port, extend
        self.env, self.gpus = dict(env or {}), gpus
        self._gpu_text: Optional[str] = None
        self._version: Optional[str] = None
        self.stat, self.clock, self.sleep, self.timeout = stat or _stat, clock, sleep, timeout

    # ------------------------------------------------------------ the port
    def measure(self, server_argv: Sequence[str]) -> Optional[str]:
        key = self._key(server_argv)
        if key is None:
            return None
        doc = self._read()
        if self._take_watched(doc):
            self._write(doc)
        if key in doc["fits"]:
            return doc["fits"][key]
        return self._keep(key, self._run(server_argv))

    def measure_stub(self, name: str, size: int, head: Callable[[], bytes],
                     server_argv: Sequence[str]) -> Optional[str]:
        """measure for a model not on disk: the stub of its header (size: the real file's) in the cache, removed after.
        The answer is kept by name (a stub's own size and time say nothing), so head is read for a new one only."""
        model = sf.value(server_argv, "-m", "--model")
        if not model or not name or size <= 0 or not self.llama.server():
            return None                              # no llama-server: no header is read for nothing
        key = self._stub_key(name, size, server_argv)
        doc = self._read()
        if key in doc["fits"]:
            return doc["fits"][key]
        try:
            data = head()
        except (OSError, ValueError):
            return None                              # offline, or no file on Hugging Face
        folder = os.path.join(self.conf_dir, "cache")
        try:
            os.makedirs(folder, exist_ok=True)
            fd, stub = tempfile.mkstemp(suffix=".gguf", prefix="stub-", dir=folder)
            os.close(fd)
        except OSError:
            return None
        try:
            gguf_stub.write_stub(data, sf.stub_size(len(data), size), stub, self.extend)
            args = list(server_argv)
            args[args.index(model)] = stub
            log = self._run(args)
        except (OSError, ValueError):
            return None
        finally:
            try:
                os.remove(stub)
            except OSError:
                pass
        return self._keep(key, log)

    def kept(self, server_argv: Sequence[str]) -> Optional[str]:
        key = self._key(server_argv)
        return self._read()["fits"].get(key) if key is not None else None

    def kept_stub(self, name: str, size: int, server_argv: Sequence[str]) -> Optional[str]:
        if not sf.value(server_argv, "-m", "--model") or not name or size <= 0:
            return None
        return self._read()["fits"].get(self._stub_key(name, size, server_argv))

    def draft_record(self, server_argv: Sequence[str]) -> Optional[int]:
        key = self._key(server_argv)
        if key is None:
            return None
        doc = self._read()
        if self._take_watched(doc):
            self._write(doc)
        got = doc["drafts"].get(key)
        return got if isinstance(got, int) and got > 0 else None

    def watch_draft(self, server_argv: Sequence[str], log_path: str) -> None:
        key = self._key(server_argv)
        if key is None or not log_path:
            return
        doc = self._read()
        doc["watch"][key] = log_path
        self._write(doc)

    # ------------------------------------------------------------ inside
    def _key(self, server_argv: Sequence[str]) -> Optional[str]:
        files = [sf.value(server_argv, "-m", "--model"), sf.value(server_argv, "-md", "--model-draft")]
        stats = [self.stat(f) for f in files if f]
        if not files[0] or any(s is None for s in stats):
            return None
        return sf.cache_key(self._build(), self._gpus(), [s for s in stats if s is not None],
                            sf.key_args(server_argv))

    def _stub_key(self, name: str, size: int, server_argv: Sequence[str]) -> str:
        return sf.cache_key(self._build(), self._gpus(), [(size, "stub " + name)], sf.key_args(server_argv))

    def _build(self) -> str:
        """The llama.cpp build's version text, asked of the program once (carl fit's table makes many keys)."""
        if self._version is None:
            self._version = self.llama.version_text().strip()
        return self._version

    def _gpus(self) -> str:
        if self._gpu_text is None:
            try:
                self._gpu_text = self.gpus()
            except (OSError, ValueError):
                self._gpu_text = ""
        return self._gpu_text

    def _run(self, server_argv: Sequence[str]) -> Optional[str]:
        program, port = self.llama.server(), self.port()
        if not program or port is None:
            return None
        try:
            fd, log = tempfile.mkstemp(suffix=".log", prefix="fit-")
            os.close(fd)
        except OSError:
            return None
        pid = 0
        try:
            env = dict(self.env)
            env[CUDA_ORDER[0]] = CUDA_ORDER[1]          # the devices' numbers are CARL's, as the start gives them
            pid = self.procs.spawn([program] + sf.argv_of(server_argv, port), env, log, False)
            end = self.clock() + self.timeout
            text = ""
            while self.clock() < end:
                text = _read_text(log)
                past = any(p in text for p in sf.FIT_PAST)
                if sf.FIT_DONE in text or past or self.procs.wait(pid, 0) is not None:
                    break                                # the fit printed, the load began without it, or it ended
                self.sleep(POLL)
            self.procs.stop(pid, STOP)
            pid = 0
            text = _read_text(log)
            return text if sf.FIT_DONE in text else None
        except OSError:
            return None
        finally:
            if pid:
                self.procs.stop(pid, STOP)
            try:
                os.remove(log)
            except OSError:
                pass

    def _keep(self, key: str, log: Optional[str]) -> Optional[str]:
        """A fit's breakdown kept (its lines, not the whole log; the newest KEPT, the oldest go first) and answered; a
        log with no breakdown is answered as it is and not kept."""
        got = sf.parse(log or "")
        if got is None:
            return log
        doc = self._read()
        doc["fits"] = dict(list(doc["fits"].items())[-(KEPT - 1):])
        doc["fits"][key] = got.text()
        self._write(doc)
        return doc["fits"][key]

    def _take_watched(self, doc: Dict[str, Dict]) -> bool:
        """The draft records of the starts whose logs were named: read and kept; a log without a draft context yet
        stays watched (the start may still be loading), a missing log is dropped. True when anything changed."""
        changed = False
        for key, path in list(doc["watch"].items()):
            if not isinstance(path, str) or not os.path.isfile(path):
                del doc["watch"][key]
                changed = True
                continue
            got = sf.draft_record(_read_text(path))
            if got is not None:
                doc["drafts"][key] = got
                del doc["watch"][key]
                changed = True
        return changed

    def _path(self) -> str:
        return os.path.join(self.conf_dir, FILE)

    def _read(self) -> Dict[str, Dict]:
        try:
            with open(self._path(), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        fits = doc.get("fits") if isinstance(doc.get("fits"), dict) else {}
        drafts = doc.get("drafts") if isinstance(doc.get("drafts"), dict) else {}
        watch = doc.get("watch") if isinstance(doc.get("watch"), dict) else {}
        return {"fits": {k: v for k, v in fits.items() if isinstance(v, str)},
                "drafts": {k: v for k, v in drafts.items() if isinstance(v, int) and not isinstance(v, bool)},
                "watch": {k: v for k, v in watch.items() if isinstance(v, str)}}

    def _write(self, doc: Dict[str, Dict]) -> None:
        try:
            write_private(self._path(), json.dumps(doc, indent=0))
        except OSError:
            pass                                     # the answer is only kept for next time


def _stat(path: str) -> Optional[Tuple[int, str]]:
    """A model file's size and the SHA-256 of its first MiB (the header: its keys, its quant); None when it is not there."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD)
        return (os.path.getsize(path), hashlib.sha256(head).hexdigest()[:16])
    except OSError:
        return None


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


"""Shared helpers for the small measurement tools in tools/ (llama-spec-bench.py,
llama-ab-measure.py, llama-kv-longctx.py, llama-sesstest.py, sesstest.py,
make-memtest-prompt.py): the API key, chat requests to llama-server / MTPLX,
the listening server's pid and memory, and the MTPLX source tree they use as a
test corpus.

The parse_* functions are pure (tested in tests/scripts/test_carl_bench.py);
the others run commands or talk to the server.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

DEFAULT_BASE = "http://192.168.42.1:8080"
DEFAULT_KEY_FILE = "~/.mtplx/api-key"
NETSTAT = "/usr/sbin/netstat"
DEFAULT_PAGE_SIZE = 16384          # Apple silicon; vm_stat states it in its header

JsonObj = dict[str, Any]


class Message(TypedDict):
    role: str
    content: str


class Timings(TypedDict, total=False):
    """llama-server's per-request "timings" (absent on MTPLX)."""
    prompt_n: int
    prompt_per_second: float
    predicted_n: int
    predicted_per_second: float
    cache_n: int
    draft_n: int
    draft_n_accepted: int


class Usage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class Completion:
    """The parts of a /v1/chat/completions response the tools print."""
    content: str
    timings: Timings
    usage: Usage


@dataclass(frozen=True)
class MemorySnapshot:
    rss_gib: float        # the server process
    wired_gib: float      # system wired memory (Metal buffers live here)
    swap_used: str        # as sysctl vm.swapusage prints it, e.g. 0.00M


# ------------------------------------------------------------------ pure parsing
def parse_listen_pid(netstat_out: str, port: int) -> int | None:
    """pid of the process listening on TCP PORT in `netstat -anv -p tcp` output.
    netstat, not lsof: lsof stats every mounted filesystem and hangs on a stale
    network share."""
    for line in netstat_out.splitlines():
        f = line.split()
        if len(f) > 5 and f[5] == "LISTEN" and f[3].endswith(f".{port}"):
            m = re.search(r":(\d+)\s+\d{5}\s", line)        # process:pid, then the 5-digit state
            return int(m.group(1)) if m else None
    return None


def parse_wired_gib(vm_stat_out: str) -> float:
    """System wired memory in GiB from `vm_stat` output."""
    page = re.search(r"page size of (\d+) bytes", vm_stat_out)
    size = int(page.group(1)) if page else DEFAULT_PAGE_SIZE
    for line in vm_stat_out.splitlines():
        if "wired" in line:
            return int(line.split()[-1].rstrip(".")) * size / 2**30
    raise ValueError("vm_stat output has no wired line")


def parse_swap_used(swapusage: str) -> str:
    """The "used" figure of `sysctl -n vm.swapusage` (e.g. 1024.00M)."""
    m = re.search(r"used = (\S+)", swapusage)
    if not m:
        raise ValueError(f"unexpected vm.swapusage: {swapusage!r}")
    return m.group(1)


def port_of(base: str) -> int:
    """The TCP port of a base URL such as http://192.168.42.1:8080."""
    u = urllib.parse.urlsplit(base)
    if u.port is not None:
        return u.port
    return 443 if u.scheme == "https" else 80


def completion_from(resp: JsonObj) -> Completion:
    content = resp["choices"][0]["message"]["content"]
    return Completion(content=content if isinstance(content, str) else "",
                      timings=resp.get("timings") or {}, usage=resp.get("usage") or {})


def chat_body(messages: list[Message], max_tokens: int, model: str = "x", **sampling: float) -> JsonObj:
    """A chat request with thinking off (so decode numbers aren't dominated by
    variable reasoning length)."""
    body: JsonObj = {"model": model, "max_tokens": max_tokens, **sampling,
                     "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}
    return body


# ------------------------------------------------------------------ adapters
def read_api_key(path: str | None = None) -> str:
    """The server's Bearer key: PATH, else $API_KEY_FILE, else ~/.mtplx/api-key."""
    p = os.path.expanduser(path or os.environ.get("API_KEY_FILE") or DEFAULT_KEY_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError as e:
        raise SystemExit(f"error: cannot read the API key file {p}: {e.strerror}") from None
    if not key:
        raise SystemExit(f"error: the API key file {p} is empty")
    return key


def validate_base(base: str) -> str:
    """BASE_URL from the command line: http(s)://HOST:PORT[/path] only."""
    u = urllib.parse.urlsplit(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise SystemExit(f"error: BASE_URL must look like http://HOST:PORT, got {base!r}")
    return base.rstrip("/")


def post_chat(base: str, key: str, body: JsonObj, timeout: float,
              headers: Mapping[str, str] | None = None) -> JsonObj:
    """POST /v1/chat/completions; HTTP errors propagate (urllib.error.HTTPError)."""
    h = {"Authorization": "Bearer " + key, "content-type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if not isinstance(data, dict):
        raise ValueError("the server's reply is not a JSON object")
    return data


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def listen_pid(port: int) -> int:
    pid = parse_listen_pid(run([NETSTAT, "-anv", "-p", "tcp"]), port)
    if pid is None:
        raise SystemExit(f"no server listens on port {port}")
    return pid


def server_memory(port: int) -> MemorySnapshot:
    """RSS of the server listening on PORT, system wired memory and swap in use."""
    pid = listen_pid(port)
    rss_kib = int(run(["ps", "-o", "rss=", "-p", str(pid)]).strip())
    return MemorySnapshot(rss_gib=rss_kib / 1048576, wired_gib=parse_wired_gib(run(["vm_stat"])),
                          swap_used=parse_swap_used(run(["sysctl", "-n", "vm.swapusage"])))


def mtplx_source_dir() -> str:
    """The installed MTPLX package (uv tool install), whose source files serve as
    a deterministic long-context corpus."""
    try:
        tools = run(["uv", "tool", "dir"]).strip()
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("error: uv is not installed (these tests read the MTPLX source installed with uv tool)") from None
    hits = sorted(glob.glob(os.path.join(tools, "mtplx", "lib", "python3.*", "site-packages", "mtplx")))
    if not hits:
        raise SystemExit(f"error: no MTPLX install under {tools}/mtplx (uv tool install mtplx); "
                         "these tests use its source files as the long-context corpus")
    return hits[-1]


def read_source(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()

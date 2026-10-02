#!/usr/bin/env python3
"""CARL dashboard: the live monitor for the llama.cpp or MTPLX server.

Starting a server from a terminal (./carl.sh llama|grant|pocket) shows
this monitor there, with the server running in the background. Quitting asks
whether to stop the server or leave it running; `./carl.sh monitor`
re-attaches later.

Tabs (click, or keys 1-5 / Tab):
  1 Overview  cards: CONNECT, CONTEXT (fill, KV quant, KV RAM), MEMORY, ACTIVITY
              (live speed, prompt ETA), MODEL, HEALTH, SYSTEM, recent requests, log
  2 Connect   URL, API key, setup steps, OpenCode / Pi config and curl test,
              shown and copied to the clipboard
  3 Requests  every finished request with its speeds
  4 Log       full server log: scroll, wrap, errors only
  5 Settings  model, KV cache, context, slots, RAM cache, network, sampling:
              saved to ~/.config/llm-deploy/llama.env, applied by a restart
              (the old server starts again if the new one fails)

Mouse: click a card title for more detail (once more to collapse it); click
buttons; the wheel scrolls. Keys: q or Ctrl-C quit (asks) | k show/hide key |
o / p / t copy OpenCode / Pi / curl | e / c expand / collapse all | w wrap |
f errors only | arrows, PgUp/PgDn scroll | space refresh | ? all keys

  ./carl.sh monitor                      # attach to a running server
  ./carl.sh monitor --port 8081          # a server on another port
  ./carl.sh monitor --once --expand      # print one snapshot and exit

Read-only towards the server (/health, /slots, /metrics, /props, /v1/models
and its log file); the one thing it changes is stopping the server, when you
choose that. Context memory is computed from the GGUF metadata and the
server's flags (tools/gguf_shape.py).
"""
import argparse, collections, json, os, re, select, shutil, signal, subprocess, sys
import termios, threading, time, tty, urllib.request, unicodedata
sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gguf_shape import GIB, KV_BPE, OVERHEAD, gpu_limit, kv_bytes_per_token, local_meta, model_shape

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--host", default=None, help="server address (default: wherever the server on --port listens)")
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)), help="server port (default 8080)")
ap.add_argument("--lines", type=int, default=6, help="log lines on the Overview tab (default 6)")
ap.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes (default 2)")
ap.add_argument("--log", default=None, help="log file (default: the running server's --log-file)")
ap.add_argument("--server-pid", type=int, default=None, help="PID of the server to watch (set by the launcher)")
ap.add_argument("--owner", action="store_true", help=argparse.SUPPRESS)
ap.add_argument("--console", default=None, help=argparse.SUPPRESS)
ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
ap.add_argument("--tab", type=int, default=0, help=argparse.SUPPRESS)       # --once: which tab (1-4)
ap.add_argument("--expand", action="store_true", help="start with every card fully detailed")
args = ap.parse_args()

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.environ.get("CARL_DEMO") == "1"      # README screenshots: hide the key and the home path

# The CARL logo in the header (4 columns x 2 rows, top left) on terminals that
# show images: iTerm2 (its inline-image escape) and Ghostty / WezTerm / kitty
# (the kitty graphics protocol). Other terminals get an emoji.
# CARL_LOGO=0 turns it off; CARL_LOGO=iterm|kitty forces a protocol.
def logo_mode():
    want = os.environ.get("CARL_LOGO", "")
    if want in ("0", "off"):
        return None
    if want in ("iterm", "kitty"):
        return want
    if os.environ.get("TERM_PROGRAM") == "iTerm.app" or os.environ.get("LC_TERMINAL") == "iTerm2":
        return "iterm"
    if os.environ.get("TERM_PROGRAM") in ("ghostty", "WezTerm") or os.environ.get("TERM") == "xterm-kitty":
        return "kitty"
    return None

LOGO_FILE = os.path.join(REPO, "assets", "carl-icon.png")
LOGO = logo_mode() if not args.once and os.path.exists(LOGO_FILE) else None
LOGO_W = 5 if LOGO else 0                       # columns kept free for the logo (4 + a gap)

def logo_escape():
    import base64
    data = base64.b64encode(open(LOGO_FILE, "rb").read()).decode()
    if LOGO == "iterm":
        return (f"\x1b[1;1H\x1b]1337;File=inline=1;width=4;height=2;preserveAspectRatio=1;"
                f"size={os.path.getsize(LOGO_FILE)}:{data}\x07")
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    out = "\x1b_Ga=d,d=A,q=2\x1b\\\x1b[1;1H"          # kitty: delete the old copy, then place it again
    for n, c in enumerate(chunks):
        keys = "a=T,f=100,c=4,r=2,C=1,q=2," if n == 0 else ""
        out += f"\x1b_G{keys}m={1 if n < len(chunks) - 1 else 0};{c}\x1b\\"
    return out
KEY_FILE = os.path.expanduser(os.environ.get("API_KEY_FILE", "~/.mtplx/api-key"))

def read_key():
    try:
        return open(KEY_FILE).read().strip()
    except OSError:
        return ""

def listen_host(port):
    """Address the server on PORT listens on (lsof), or None."""
    out = subprocess.run(["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    m = re.search(r"TCP (\S+):%d \(LISTEN\)" % port, out)
    return m.group(1).strip("[]") if m else None

HOST = args.host or listen_host(args.port) or os.environ.get("HOST") or "127.0.0.1"
BASE = f"http://{HOST}:{args.port}"
KEY = read_key()

# ===================================================================== styling
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
R, DIM, B = "\x1b[0m", "\x1b[2m", "\x1b[1m"
GRN, YEL, RED, CYN, MAG = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[35m"

def cw(c):
    """Terminal columns of one character: 2 for wide ones (emoji, CJK)."""
    return 2 if unicodedata.east_asian_width(c) in "WF" else 1

def vlen(s): return sum(cw(c) for c in ANSI.sub("", s))

def fit(s, w):
    """Cut a coloured string to w visible columns and pad it."""
    out, n, i = [], 0, 0
    while i < len(s):
        m = ANSI.match(s, i)
        if m:
            out.append(m.group()); i = m.end(); continue
        c = cw(s[i])
        if n + c > w:
            break
        if n + c >= w and vlen(s[i:]) > c:
            out.append("…"); n += 1; break
        out.append(s[i]); n += c; i += 1
    return "".join(out) + R + " " * max(0, w - n)

def wrap(s, w):
    plain = ANSI.sub("", s)
    return [plain[i:i + w] for i in range(0, max(len(plain), 1), w)]

def bar(frac, w=18):
    frac = max(0.0, min(1.0, frac or 0))
    col = GRN if frac < 0.7 else YEL if frac < 0.9 else RED
    n = round(frac * w)
    return f"{col}{'█' * n}{DIM}{'░' * (w - n)}{R}"

def size(b):
    if b is None: return "?"
    for unit, div in (("G", 2**30), ("M", 2**20), ("K", 2**10)):
        if abs(b) >= div:
            return f"{b / div:.0f}{unit}" if unit == "K" else f"{b / div:.1f}{unit}"
    return f"{b:.0f}B"

def k(n):
    n = n or 0
    return f"{n / 1000:.1f}K" if n >= 1000 else f"{n:.0f}"

def dur(s):
    if s is None: return "–"
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"

def sh(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def http(path, timeout=2):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {KEY}"} if KEY else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()

# ===================================================================== log reader
TS = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+) ([IWED]) ")

class LogTail:
    """Incrementally reads the server log; keeps recent lines, counts and requests."""
    def __init__(self):
        self.path, self.pos, self.buf, self.start = None, 0, "", None
        self.lines = collections.deque(maxlen=4000)
        self.reset()

    def reset(self):
        self.lines.clear()
        self.counts = collections.Counter()
        self.errors = collections.deque(maxlen=8)
        self.requests = collections.deque(maxlen=50)
        self.current = {}          # in-flight requests by task id

    @staticmethod
    def offset(line):
        m = TS.match(line)   # "M.SS.mmm.uuu": minutes since start, seconds, ms, us
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000 if m else None

    def update(self, path):
        if path != self.path:
            self.path, self.pos, self.buf, self.start = path, 0, "", None
            self.reset()
            m = re.search(r"(\d{8}-\d{6})", os.path.basename(os.path.realpath(path or "")))
            if m:
                self.start = time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S"))
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                if f.tell() < self.pos:
                    self.pos = 0; self.reset()
                f.seek(self.pos); data = f.read(); self.pos = f.tell()
        except (OSError, TypeError):
            return
        parts = (self.buf + ANSI.sub("", data.decode(errors="replace"))).split("\n")
        self.buf = parts.pop()
        for line in parts:
            line = line.rstrip()
            if line:
                self.lines.append(line)
                self.parse(line)

    def parse(self, line):
        m = TS.match(line)
        lvl = m.group(5) if m else ""
        if lvl in ("E", "W"):
            self.counts[lvl] += 1
            if lvl == "E":
                self.errors.append(line)
        if "OutOfMemory" in line: self.counts["oom"] += 1
        if "Compute error" in line: self.counts["compute"] += 1
        t = re.search(r"task (\d+)", line)
        task = int(t.group(1)) if t else None
        if "launch_slot_" in line and task is not None:
            sm = re.search(r"id +(\d+) \|", line)
            self.current[task] = {"task": task, "slot": int(sm.group(1)) if sm else 0, "t0": self.offset(line)}
        elif task in self.current:
            c = self.current[task]
            mm = re.search(r"prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second", line)
            if mm:
                c["new"], c["pp"] = int(mm.group(1)), float(mm.group(2))
            mm = re.search(r"\|\s+eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second", line)
            if mm:
                c["gen"], c["tg"] = int(mm.group(1)), float(mm.group(2))
            mm = re.search(r"draft acceptance = ([\d.]+)", line)
            if mm:
                c["acc"] = float(mm.group(1))
            if "send_error" in line:
                c["error"] = True
            mm = re.search(r"release: .*n_tokens = (\d+)", line)
            if mm:
                c["ctx"], c["t1"] = int(mm.group(1)), self.offset(line)
                self.requests.append(c); del self.current[task]

    def wall(self, off):
        return time.strftime("%H:%M:%S", time.localtime(self.start + off)) if self.start and off is not None else "--:--:--"

# ===================================================================== collectors
PAGE = int(sh(["sysctl", "-n", "hw.pagesize"]) or 16384)
TOTAL = int(sh(["sysctl", "-n", "hw.memsize"]) or 1)
S = {"pid": None, "pid_t": 0, "props": {}, "props_t": 0, "last": None, "meta_path": None, "shape": {},
     "model_size": None, "slow": {}}
LOG = LogTail()

def flag(cmd, *names, default=None):
    for n in names:
        m = re.search(r"(?:^|\s)" + re.escape(n) + r"[ =](\S+)", cmd)
        if m:
            return m.group(1)
    return default

def pid_alive(pid):
    """True if pid is running. Reaps it first when it's our child: the launcher
    execs into this monitor, so the server is our child and would otherwise
    linger as a zombie (still listed by ps) after it exits."""
    if not pid:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    stat = sh(["ps", "-o", "stat=", "-p", str(pid)]).strip()
    return bool(stat) and not stat.startswith("Z")

def find_pid():
    now = time.time()
    pid = S["pid"]
    if not pid_alive(pid) or now - S["pid_t"] > 20:
        out = sh(["lsof", "-tiTCP:%d" % args.port, "-sTCP:LISTEN"]).split()
        S["pid"], S["pid_t"] = (int(out[0]) if out else None), now
    return S["pid"]

def slow_loop():
    """Slow-changing, expensive stats (pmset log takes ~1 s), refreshed in the background."""
    while True:
        slow = {}
        therm = sh(["pmset", "-g", "therm"])
        warn = [l.strip() for l in therm.splitlines() if l.strip() and not l.startswith("Note:")]
        slow["thermal"] = "nominal" if not warn else "; ".join(warn)[:60]
        start, pid = None, S["pid"]
        if pid:
            try:
                start = time.mktime(time.strptime(sh(["ps", "-o", "lstart=", "-p", str(pid)]).strip(), "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                pass
        events = []
        if start:
            for line in sh(["pmset", "-g", "log"], timeout=10).splitlines():
                m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) [+-]\d{4} (Sleep|Wake|DarkWake)\s+(.*)", line)
                if m and time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) >= start:
                    events.append((m.group(1)[11:], m.group(2), m.group(3).strip()[:50]))
        slow["sleep_events"] = events
        try:
            du = shutil.disk_usage(os.path.expanduser("~/models"))
            slow["disk"] = (du.free, du.total)
        except OSError:
            slow["disk"] = None
        S["slow"] = slow
        time.sleep(60)

def _uptime(d):
    """Seconds the server has run (ps etime [[dd-]hh:]mm:ss), for matching its console file."""
    parts = re.split(r"[-:]", d.get("etime") or "0")
    secs = 0
    for v, mul in zip(reversed(parts), (1, 60, 3600, 86400)):
        secs += int(v or 0) * mul
    return secs + 60

def collect():
    global HOST, BASE, KEY
    d = {"t": time.time(), "up": False}
    if not args.host:
        h = listen_host(args.port)
        if h and h != HOST:
            HOST, BASE = h, f"http://{h}:{args.port}"
    KEY = read_key() or KEY
    # ---- server process
    pid = find_pid()
    d["pid"], cmd = pid, ""
    if pid:
        m = re.match(r"\s*(\d+)\s+([\d.]+)\s+(\S+)\s+(.*)", sh(["ps", "-o", "rss=,%cpu=,etime=,command=", "-p", str(pid)]))
        if m:
            d["rss"], d["cpu"], d["etime"], cmd = int(m.group(1)) * 1024, float(m.group(2)), m.group(3), m.group(4).strip()
        d["awake"] = f"on behalf of Process ID {pid}" in sh(["pmset", "-g", "assertions"])
        d["conns"] = [(mm.group(1), mm.group(2)) for line in
                      sh(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:ESTABLISHED"]).splitlines()[1:]
                      for mm in [re.search(r"->(\S+):(\d+)", line)] if mm]
    d["cmd"] = cmd
    if args.server_pid and not pid_alive(args.server_pid):
        d["exited"] = True
    d["target_pid"] = args.server_pid or pid
    # ---- model metadata (once per model file)
    mpath = flag(cmd, "-m", "--model")
    if mpath and mpath != S["meta_path"] and os.path.exists(mpath):
        S["meta_path"], S["shape"], S["model_size"] = mpath, model_shape(local_meta(mpath)), os.path.getsize(mpath)
    d["shape"] = S["shape"] if mpath else {}
    # ---- HTTP
    try:
        t0 = time.time(); http("/health"); d["health_ms"] = (time.time() - t0) * 1000; d["up"] = True
    except Exception as e:
        d["err"] = str(getattr(e, "reason", e))[:50]
    d["m"], d["pos"] = {}, {}
    if d["up"]:
        try:
            all_slots = json.loads(http("/slots"))
            d["slot_list"] = [{"id": x.get("id"), "busy": x.get("is_processing", False), "task": x.get("id_task"),
                               "n_ctx": x.get("n_ctx", 0), "prompt": x.get("n_prompt_tokens", 0),
                               "cached": x.get("n_prompt_tokens_cache", 0),
                               "processed": x.get("n_prompt_tokens_processed", 0),
                               "decoded": ((x.get("next_token") or [{}])[0]).get("n_decoded", 0)} for x in all_slots]
            busy = [x for x in all_slots if x.get("is_processing")]
            slot = busy[0] if busy else all_slots[0]
            nt = (slot.get("next_token") or [{}])[0]
            d.update(n_ctx=slot.get("n_ctx", 0), busy=slot.get("is_processing", False),
                     prompt=slot.get("n_prompt_tokens", 0), cached=slot.get("n_prompt_tokens_cache", 0),
                     processed=slot.get("n_prompt_tokens_processed", 0), decoded=nt.get("n_decoded", 0),
                     task=slot.get("id_task"), slots=True)
        except Exception:
            d["slots"] = False
        try:
            for line in http("/metrics").splitlines():
                if line and not line.startswith("#"):
                    key, v = line.rsplit(" ", 1)
                    mm = re.search(r'position="(\d+)"', key)
                    if mm:
                        d["pos"][int(mm.group(1))] = float(v)
                    else:
                        d["m"][key.replace("llamacpp:", "")] = float(v)
        except Exception:
            pass
        if time.time() - S["props_t"] > 30:
            try:
                S["props"] = json.loads(http("/props"))
            except Exception:
                pass
            S["props_t"] = time.time()
    d["props"] = S["props"]
    # ---- MTPLX: no /slots; its own snapshot (+ flight for live decode) instead
    d["backend"] = "llama"
    if d["up"] and not d.get("slots"):
        try:
            mx = json.loads(http("/v1/mtplx/snapshot", 4))
            try:
                fl = json.loads(http("/v1/mtplx/flight", 2))
            except Exception:
                fl = {}
            d.update(backend="mtplx", mx=mx, n_ctx=mx.get("context_window") or 0, busy=bool(mx.get("active_requests")),
                     prompt=0, cached=0, processed=0, decoded=0, task=None)
            inf = (mx.get("in_flight") or [None])[0]
            act = ((fl.get("active") or [None]) or [None])[0] or {}
            d["flight"] = act
            if inf:
                ps = inf.get("prefill_state") or {}
                d["prompt"] = inf.get("prompt_tokens") or ps.get("tokens_total") or act.get("prompt_tokens") or 0
                d["task"] = inf.get("request_id")
                if act.get("phase") == "prefill" or (ps and not act.get("gen_tokens")):
                    d["cached"] = ps.get("cached_tokens") or 0
                    d["processed"] = max((ps.get("tokens_done") or 0) - d["cached"], 0)
                    d["mx_pp"] = ps.get("live_prefill_tok_s") or ps.get("prefill_tok_s")
                else:
                    d["processed"] = d["prompt"]
                    d["decoded"] = act.get("gen_tokens") or 1
                    d["mx_tg"] = act.get("tps_now") or act.get("tps_avg")
        except Exception:
            pass
    # live rates since the previous refresh (same request only)
    last = S["last"]
    d["pp_rate"] = d["tg_rate"] = None
    if last and d.get("busy") and last.get("task") == d.get("task"):
        dt = d["t"] - last["t"]
        if dt > 0:
            d["pp_rate"] = max(0, d["processed"] - last["processed"]) / dt or None
            d["tg_rate"] = max(0, d["decoded"] - last["decoded"]) / dt or None
    if d.get("slots"):
        S["last"] = {key: d[key] for key in ("task", "t", "processed", "decoded")}
    if d["backend"] == "mtplx":
        d["pp_rate"], d["tg_rate"] = d.get("mx_pp"), d.get("mx_tg")
    # ---- system
    vm = {}
    for line in sh(["vm_stat"]).splitlines():
        if ":" in line:
            key, v = line.split(":", 1); v = v.strip().rstrip(".")
            if v.isdigit():
                vm[key.strip()] = int(v) * PAGE
    d["wired"], d["active"] = vm.get("Pages wired down", 0), vm.get("Pages active", 0)
    d["comp"], d["free"] = vm.get("Pages occupied by compressor", 0), vm.get("Pages free", 0)
    d["used"] = d["wired"] + d["active"] + d["comp"]
    lvl = sh(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    d["pressure"] = {"1": "normal", "2": "WARNING", "4": "CRITICAL"}.get(lvl, lvl or "?")
    mm = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", sh(["sysctl", "-n", "vm.swapusage"]))
    d["swap"] = (float(mm.group(2)) * 2**20, float(mm.group(1)) * 2**20) if mm else (0, 0)
    io = sh(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"])
    g = re.search(r'"Device Utilization %"=(\d+)', io); gm = re.search(r'"In use system memory"=(\d+)', io)
    d["gpu"], d["gpumem"] = (int(g.group(1)) if g else None), (int(gm.group(1)) if gm else None)
    batt = sh(["pmset", "-g", "batt"]); p = re.search(r"(\d+)%", batt)
    d["power"] = ("AC" if "AC Power" in batt else "battery") + (f" {p.group(1)}%" if p else "")
    d["load"] = os.getloadavg()
    # ---- log
    path = flag(cmd, "--log-file") or args.log
    console = args.console or os.path.expanduser(f"~/models/logs/.console-{args.port}.out")
    if d["backend"] == "mtplx" or "mtplx serve" in cmd:   # MTPLX has no log file: its console output, if we have it
        path = console if os.path.exists(console) and (not pid or os.path.getmtime(console) >= time.time() - _uptime(d)) else None
    elif not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        path = console if os.path.exists(console) else (
            path or os.path.expanduser("~/models/logs/llama-server-latest.log"))
    d["log_path"] = path
    LOG.update(d["log_path"])
    return d

# ===================================================================== client configs
def bundle(rel):
    """A client config template from client/ with this server's address filled in."""
    try:
        text = open(os.path.join(REPO, "client", rel)).read()
    except OSError:
        return {}
    for a, b in (("__MTPLX_HOST__", HOST), ("__MTPLX_PORT__", str(args.port)), ("__LLAMA_PORT__", str(args.port)),
                 ("__HOME__", os.path.expanduser("~"))):
        text = text.replace(a, b)
    return json.loads(text)

def served(d):
    """(provider name, model id, context, is_llama) for the running server."""
    alias = (d.get("props") or {}).get("model_alias")
    llama = bool(d.get("slots"))
    if not alias:
        try:
            alias = json.loads(http("/v1/models"))["data"][0]["id"]
        except Exception:
            alias = "model"
    return ("llamacpp" if llama else "mtplx"), alias, d.get("n_ctx") or 49152, llama

# Snippets for pasting by hand are additive: one provider block under the id
# "llm-deploy" (or "llm-deploy-mtplx"), which can't collide with a provider the
# user already has, and nothing else (no default model, $schema or agents).
SNIP_ID = {"llamacpp": "llm-deploy", "mtplx": "llm-deploy-mtplx"}

def installed_here():
    """(client, provider id) pairs that install.sh on THIS Mac already pointed at this server."""
    out = []
    for client, path in (("OpenCode", "~/.config/opencode/llm-deploy.json"), ("Pi", "~/.pi/agent/llm-deploy.json")):
        try:
            st = json.load(open(os.path.expanduser(path)))
        except (OSError, ValueError):
            continue
        if st.get("base_url") == f"{BASE}/v1":
            out.append((client, st.get("providers", {}).get("llamacpp", "llamacpp")))
    return out

def opencode_config(d):
    prov, alias, ctx, llama = served(d)
    p = (bundle("opencode/opencode.json").get("provider") or {}).get(prov) or {
        "npm": "@ai-sdk/openai-compatible", "name": prov, "options": {}, "models": {}}
    p["options"]["baseURL"] = f"{BASE}/v1"
    p["options"]["apiKey"] = KEY
    m = p.get("models", {}).get(alias) or {
        "name": alias, "reasoning": True, "tool_call": True, "temperature": True,
        "limit": {"context": ctx, "output": 32000},
        "variants": {"none": {"reasoningEffort": "none"}}}
    m["limit"]["context"] = ctx
    m["limit"]["output"] = min(m["limit"].get("output", 32000), ctx // 2)
    p["models"] = {alias: m}
    p["name"] = p.get("name", prov) + " [llm-deploy]"
    return {"provider": {SNIP_ID[prov]: p}}

def pi_config(d):
    prov, alias, ctx, llama = served(d)
    p = (bundle("pi/models.json").get("providers") or {}).get(prov) or {
        "baseUrl": "", "api": "openai-completions", "models": []}
    p["baseUrl"] = f"{BASE}/v1"
    p["apiKey"] = KEY
    ms = [m for m in p.get("models", []) if m.get("id") == alias] or [
        {"id": alias, "name": alias, "reasoning": True, "input": ["text"], "contextWindow": ctx, "maxTokens": 32768}]
    for m in ms:
        m["contextWindow"] = ctx
        m["maxTokens"] = min(m.get("maxTokens", 32768), ctx // 2)
    p["models"] = ms
    return {"providers": {SNIP_ID[prov]: p}}

def curl_test(d):
    prov, alias, ctx, llama = served(d)
    off = '"reasoning_effort":"none"' if llama else '"enable_thinking":false'
    return (f"curl -s {BASE}/v1/chat/completions \\\n  -H 'Authorization: Bearer {KEY}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"model\":\"{alias}\",{off},\"messages\":[{{\"role\":\"user\",\"content\":\"Say hello\"}}]}}'")

def copy(text):
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=3, check=True)
        return True
    except Exception:
        return False

# ===================================================================== UI state
TABS = ["Overview", "Connect", "Requests", "Log", "Settings"]
level = {"connect": 1, "context": 1, "memory": 1, "activity": 1, "model": 1, "health": 1, "system": 1,
         "requests": 1, "log": 1}
if args.expand:
    level.update({x: 2 for x in level})
ui = {"tab": 0, "scroll": 0, "log_scroll": 0, "req_scroll": 0, "prev_scroll": 0, "wrap": False, "errors_only": False,
      "lines": args.lines, "key_shown": False, "quit": False, "stopping": None, "exit_msg": "", "help": False,
      "toast": ("", 0), "preview": "opencode", "copied": None,
      "set": None, "set_run": {}, "set_row": 0, "confirm": False, "restart": None}
regions = []      # (row, x0, x1, action): clickable screen areas, 1-based columns, x1 exclusive

def toast(msg, secs=4):
    ui["toast"] = (msg, time.time() + secs)

class Ln:
    """A line of card text, optionally clickable as a whole (act) or in parts (spans of visible columns)."""
    def __init__(self, text="", act=None, spans=None):
        self.text, self.act, self.spans = text, act, spans or []

def buttons(prefix, items):
    """Ln with inline buttons: items = [(label, action)]."""
    text, spans, col = prefix, [], vlen(prefix)
    for label, act in items:
        b = f"[ {label} ]"
        spans.append((col, col + len(b), act))
        text += f"{B}{CYN}{b}{R}  "; col += len(b) + 2
    return Ln(text, spans=spans)

def lv(label, value, w=10):
    return f"{DIM}{label:<{w}}{R}{value}"

NA = f"{DIM}N/A{R}"     # a value that is not known yet: cards keep their height

def pill(text, bg):
    return f"\x1b[1;30;{bg}m {text} {R}"

# ===================================================================== cards
def kv_info(d):
    shp, cmd = d.get("shape") or {}, d.get("cmd", "")
    ktype = flag(cmd, "-ctk", "--cache-type-k", default="f16")
    vtype = flag(cmd, "-ctv", "--cache-type-v", default="f16")
    n_ctx = d.get("n_ctx") or int(flag(cmd, "-c", "--ctx-size", default=0) or 0)
    if not shp or not n_ctx:
        return None
    nslots = int(flag(cmd, "--parallel", "-np", default=1) or 1)
    pool = int(flag(cmd, "-c", "--ctx-size", default=n_ctx) or n_ctx)
    per_tok = kv_bytes_per_token(shp, ktype, vtype)
    mtp_tok = shp["kv_elems_per_token_mtp"] / 2 * (KV_BPE.get(ktype, 2) + KV_BPE.get(vtype, 2))
    ckpt_n = int(flag(cmd, "--ctx-checkpoints", default=8) or 8)
    return {"k": ktype, "v": vtype, "n_ctx": n_ctx, "per_tok": per_tok, "kv": per_tok * pool, "slots": nslots,
            "mtp": mtp_tok * pool, "rs": shp["rs_bytes"] * nslots, "ckpt_n": ckpt_n, "ckpt_max": ckpt_n * shp["rs_bytes"] * nslots,
            "cache_ram": int(flag(cmd, "--cache-ram", default=0) or 0) * 2**20}

def status_of(d):
    if d.get("exited"):
        return "EXITED", "41", f"{RED}server process {args.server_pid} has exited{R}"
    if not d["up"]:
        return ("LOADING", "43", f"{YEL}loading the model…{R}") if d.get("pid") else ("OFFLINE", "41", f"{RED}not reachable{R}")
    if not d.get("slots") and d.get("backend") != "mtplx":
        return "UP", "42", "running (unknown server: limited stats)"
    if not d.get("busy"):
        return "IDLE", "42", "idle, waiting for requests"
    nbusy = sum(1 for x in d.get("slot_list") or [] if x["busy"])
    if d.get("backend") == "mtplx" and (d["mx"].get("active_requests") or 0) > 1:
        return f"BUSY +{d['mx']['active_requests'] - 1}", "46", f"1 request runs, {d['mx']['active_requests'] - 1} wait (MTPLX runs one at a time)"
    if nbusy > 1:
        return f"BUSY ×{nbusy}", "46", f"{nbusy} requests at once (slots in parallel)"
    if d.get("decoded", 0) == 0:
        done = d["cached"] + d["processed"]
        return "READING", "43", f"reading the prompt, {done / d['prompt'] * 100 if d['prompt'] else 0:.0f}%"
    return "GENERATING", "46", "generating"

def card_connect(d):
    reach = "the VM and this Mac" if HOST not in ("127.0.0.1", "::1") else "this Mac only"
    conns = d.get("conns") or []
    hosts = collections.Counter(h for h, _ in conns)
    shown = KEY if ui["key_shown"] else (("•" * 16 + KEY[-4:]) if KEY else f"{RED}no key file{R}")
    L = [lv("endpoint", f"{B}{BASE}/v1{R}")]
    if level["connect"] >= 1:
        L.append(lv("model", (d.get("props") or {}).get("model_alias") or (d.get("mx") or {}).get("model_id") or NA))
        L.append(Ln(lv("api key", f"{MAG}{shown}{R}  ") + f"{DIM}{'hide' if ui['key_shown'] else 'show'} (k){R}", "key"))
        L.append(lv("reachable", reach))
        L.append(lv("clients", f"{len(conns)} connected" + (f" from {', '.join(hosts)}" if hosts else "")))
        L.append(buttons(f"{DIM}{'copy':<10}{R}", [("OpenCode", "opencode"), ("Pi", "pi"), ("curl", "curl")]))
    if level["connect"] >= 2:
        L.append(lv("key file", KEY_FILE.replace(os.path.expanduser("~"), "~")))
        L.append(lv("install", "VM: ./install.sh   Mac: ./client/install.sh --local"))
    return "CONNECT", f"{DIM}{HOST}:{args.port}{R}", L

def card_context(d):
    kv = kv_info(d)
    cmd = d.get("cmd", "")
    sl = d.get("slot_list") or []
    n_ctx = d.get("n_ctx")
    nslots = len(sl) or int(flag(cmd, "--parallel", "-np", default=1) or 1)
    used = (d.get("prompt") or 0) + (d.get("decoded") or 0)
    frac = used / n_ctx if n_ctx else 0
    summary = f"{bar(frac, 10)} {frac * 100:3.0f}%"
    L = []
    if nslots > 1:
        tot = sum(x["prompt"] + x["decoded"] for x in sl)
        pool_tokens = int(flag(cmd, "-c", "--ctx-size", default=0) or 0)
        pool = pool_tokens or (n_ctx or 0) * nslots
        frac = tot / pool if pool else 0
        summary = f"{nslots} slots {bar(frac, 8)} {frac * 100:3.0f}%"
        for i in range(nslots):
            x = sl[i] if i < len(sl) else None
            if x:
                u = x["prompt"] + x["decoded"]
                st = (f"{CYN}generating{R}" if x["decoded"] else f"{YEL}reading{R}") if x["busy"] else f"{DIM}idle{R}"
                L.append(lv(f"slot {x['id']}", f"{bar(u / x['n_ctx'] if x['n_ctx'] else 0, 12)} {k(u)} / {k(x['n_ctx'])} · {st}"))
            else:
                L.append(lv(f"slot {i}", f"{bar(0, 12)} 0 / {k(n_ctx) if n_ctx else NA} · {NA}"))
        if n_ctx and pool_tokens and pool_tokens != n_ctx * nslots:
            L.append(lv("layout", f"{k(pool_tokens)} shared pool · each slot ≤{k(n_ctx)} · "
                                  f"{k(tot)} used ({tot / pool_tokens * 100:.0f}%)"))
    else:
        L.append(lv("fill", f"{bar(frac, 16)} {k(used)} / {k(n_ctx) if n_ctx else NA} tokens"))
    if kv:
        mixed = f"  {RED}mixed types: ~5x slower prefill{R}" if kv["k"] != kv["v"] else ""
        L.append(lv("KV quant", f"{MAG}{kv['k']}{R} K · {MAG}{kv['v']}{R} V{mixed}"))
        pool_note = f" (pool for {kv['slots']} slots)" if kv["slots"] > 1 else ""
        L.append(lv("KV RAM", f"{B}{size(kv['kv'])}{R} allocated{pool_note} · {size(kv['kv'] * frac)} in use"))
        if level["context"] >= 1:
            L.append(lv("state", f"{size(kv['rs'])} recurrent · ≤{size(kv['ckpt_max'])} checkpoints"))
            L.append(lv("total", f"≈{size(kv['kv'] + kv['rs'])} now · ≤{size(kv['kv'] + kv['rs'] + kv['ckpt_max'] + kv['cache_ram'])} max"))
        if level["context"] >= 2:
            shp = d.get("shape") or {}
            L.append(lv("per token", f"{size(kv['per_tok'])} = {shp.get('attn_layers')} attn layers × {shp.get('kvh')} KV heads × "
                                     f"({shp.get('kl')}+{shp.get('vl')}) × {kv['k']}"))
            L.append(lv("MTP head", f"≈{size(kv['mtp'])} KV if allocated (estimate)"))
            L.append(lv("caches", f"≤{kv['ckpt_n']} checkpoints × {size(kv['rs'])} · prompt cache ≤{size(kv['cache_ram'])}"))
            L.append(lv("window", f"{k(kv['n_ctx'])} served · {k(shp.get('ctx_train'))} trained"))
    else:
        labels = ["KV quant", "KV RAM"] + (["state", "total"] if level["context"] >= 1 else []) \
                 + (["per token", "MTP head", "caches", "window"] if level["context"] >= 2 else [])
        L += [lv(x, NA) for x in labels]
    return "CONTEXT", summary, L

def card_memory(d):
    kv = kv_info(d)
    rss = d.get("rss")
    w = S["model_size"] or 0
    ctx = (kv["kv"] + kv["rs"]) if kv else 0
    L = [lv("weights", size(w) if rss else NA),
         lv("context", f"{size(ctx)} (KV + state)" if rss else NA),
         lv("other", f"{size(max(rss - w - ctx, 0))} (buffers, caches)" if rss else NA)]
    if level["memory"] >= 2:
        L.append(lv("GPU limit", f"{size(S['gpu_limit'][0])} ({S['gpu_limit'][1]})" if S.get("gpu_limit") else NA))
        L.append(lv("GPU now", f"{size(d['gpumem'])} mapped (all apps)" if d.get("gpumem") else NA))
    return "MEMORY", (f"server {size(rss)}" if rss else f"{DIM}no server process{R}"), L

def card_activity(d):
    m = d.get("m") or {}
    pps, tgs = m.get("prompt_seconds_total", 0), m.get("tokens_predicted_seconds_total", 0)
    pp_avg = m.get("prompt_tokens_total", 0) / pps if pps else 0
    tg_avg = m.get("tokens_predicted_total", 0) / tgs if tgs else 0
    _, _, words = status_of(d)
    L = [lv("now", words)]
    summary = f"{DIM}idle{R}"
    prompt, cached, decoded = d.get("prompt") or 0, d.get("cached") or 0, d.get("decoded") or 0
    if d.get("busy"):
        remaining = max(prompt - cached - (d.get("processed") or 0), 0) if decoded == 0 else 0
        L.append(lv("prompt", f"{k(prompt)} tokens · {k(cached)} cached · {k(remaining)} left"))
        if decoded == 0:
            rate = d.get("pp_rate") or pp_avg
            eta = dur(remaining / rate) if rate else "N/A"
            L.append(lv("speed", f"read {d.get('pp_rate') or 0:.0f} tok/s · ETA {YEL}{B}{eta}{R}"))
            summary = f"ETA {eta}"
        else:
            L.append(lv("speed", f"generate {d.get('tg_rate') or 0:.1f} tok/s · {decoded} tokens out"))
            summary = f"{d.get('tg_rate') or 0:.1f} tok/s"
    else:
        L.append(lv("prompt", f"0 tokens · 0 cached · 0 left"))
        L.append(lv("speed", f"0 tok/s · ETA {DIM}none{R}"))
    llama = bool(d.get("slots"))
    if level["activity"] >= 1:
        L.append(lv("average", f"read {pp_avg:.0f} · generate {tg_avg:.1f} tok/s" if llama else NA))
        r = LOG.requests[-1] if LOG.requests else None
        L.append(lv("last", f"read {r.get('pp') or 0:.0f} · generate {r.get('tg') or 0:.1f} tok/s · "
                            f"{dur((r.get('t1') or 0) - (r.get('t0') or 0))}" if r else f"{DIM}none{R}"))
        drafted = m.get("spec_decode_num_draft_tokens_total", 0)
        acc = m.get("spec_decode_num_accepted_tokens_total", 0)
        L.append(lv("drafts", f"{acc / drafted * 100:.0f}% accepted · {acc / max(m.get('spec_decode_num_drafts_total', 1), 1):.2f} per draft"
                    if drafted else (f"0% accepted · 0 per draft" if llama else NA)))
    if level["activity"] >= 2:
        drafts = max(m.get("spec_decode_num_drafts_total", 0), 1)
        L.append(lv("by position", "  ".join(f"#{i} {d['pos'][i] / drafts * 100:.0f}%" for i in sorted(d["pos"])[:4])
                    if d.get("pos") else NA, 12))
        L.append(lv("totals", f"{k(m.get('prompt_tokens_total'))} read · {k(m.get('prompt_tokens_cached_total'))} cached · "
                              f"{k(m.get('tokens_predicted_total'))} generated" if llama else NA))
        L.append(lv("queue", f"{m.get('requests_deferred', 0):.0f} waiting · peak context {k(m.get('n_tokens_max'))}" if llama else NA))
    return "ACTIVITY", summary, L

def card_model(d):
    shp, cmd = d.get("shape") or {}, d.get("cmd", "")
    if shp:
        L = [lv("file", os.path.basename(S["meta_path"] or "")),
             lv("weights", f"{shp['ftype']} · {size(S['model_size'])}"),
             lv("spec", f"{flag(cmd, '--spec-type', default='none')} · {flag(cmd, '--spec-draft-n-max', default='0')} draft tokens")]
        if level["model"] >= 2:
            L.append(lv("arch", f"{shp['arch']} · {shp['blocks']} layers ({shp['attn_layers']} attention, "
                                f"{shp['rec_layers']} recurrent, {shp['nextn']} MTP)"))
            L.append(lv("experts", f"{shp['experts_used']} of {shp['experts']} active per token" if shp.get("experts") else "none (dense)"))
            L.append(lv("thinking", "effort levels + off" if shp.get("effort_levels") else "on/off"
                        + (" (patched: none = off)" if "--chat-template-file" in cmd else "")))
            L.append(lv("batch", f"ub {flag(cmd, '-ub', default='N/A')} · flash-attn {flag(cmd, '-fa', default='N/A')} · "
                                 f"pid {d.get('pid')} · up {d.get('etime', 'N/A')}"))
    else:
        why = "MTPLX or unknown model" if d.get("pid") else "no server process"
        L = [lv("file", f"{NA} {DIM}({why}){R}"), lv("weights", NA), lv("spec", NA)]
        if level["model"] >= 2:
            L += [lv(x, NA) for x in ("arch", "experts", "thinking", "batch")]
    return "MODEL", (f"{shp['ftype']}" if shp else NA), L

def card_health(d):
    c = LOG.counts
    broken = c["oom"] or c["compute"]
    sleeps = [e for e in S["slow"].get("sleep_events", []) if e[1] == "Sleep"]
    if broken:
        summary = f"{RED}{B}BROKEN · restart the server{R}"
    elif d.get("health_ms") is not None:
        summary = f"{GRN}ok{R} {d['health_ms']:.0f} ms"
    else:
        summary = NA
    L = [lv("log", f"{(RED + str(c['E']) + R) if c['E'] else 0} errors · {(YEL + str(c['W']) + R) if c['W'] else 0} warnings"),
         lv("GPU", (RED if broken else "") + f"{c['oom']} out-of-memory · {c['compute']} compute errors" + (R if broken else "")),
         lv("sleep", (f"{GRN}kept awake{R}" if d.get("awake") else f"{YEL}not kept awake{R}") + " · "
            + (f"{YEL}{len(sleeps)} sleeps{R}" if sleeps else "0 sleeps") + " since start")]
    if level["health"] >= 2:
        errs = [f"{RED}{e[13:]}{R}" for e in list(LOG.errors)[-3:]] or [f"{DIM}errors: none{R}"]
        evs = [f"{DIM}{t_} {kind} {why}{R}" for t_, kind, why in S["slow"].get("sleep_events", [])[-3:]] or [f"{DIM}sleep/wake: none{R}"]
        L += errs + [f"{DIM}–{R}"] * (3 - len(errs)) + evs + [f"{DIM}–{R}"] * (3 - len(evs))
    return "HEALTH", summary, L

def card_system(d):
    pc = GRN if d["pressure"] == "normal" else YEL if d["pressure"] == "WARNING" else RED
    L = [lv("memory", f"{bar(d['used'] / TOTAL, 12)} {size(d['used'])} / {size(TOTAL)}")]
    su, st = d["swap"]
    L.append(lv("swap", f"{(RED if su / st > 0.6 else YEL if su / st > 0.25 else GRN)}{size(su)}{R} of {size(st)}" if st else f"{GRN}0B{R} of 0B"))
    L.append(lv("GPU", f"{bar(d['gpu'] / 100, 12)} {d['gpu']}%" if d["gpu"] is not None else NA))
    if level["system"] >= 1:
        L.append(lv("power", f"{(YEL if d['power'].startswith('battery') else GRN)}{d['power']}{R} · thermal {S['slow'].get('thermal', 'N/A')}"))
    if level["system"] >= 2:
        L.append(lv("detail", f"wired {size(d['wired'])} · compressed {size(d['comp'])} · free {size(d['free'])}"))
        L.append(lv("CPU", f"server {d.get('cpu', 0):.0f}% · load {d['load'][0]:.1f} {d['load'][1]:.1f} {d['load'][2]:.1f}"))
        disk = S["slow"].get("disk")
        L.append(lv("disk", f"{size(disk[0])} free of {size(disk[1])}" if disk else NA))
    return "SYSTEM", f"pressure {pc}{d['pressure']}{R}", L

# ===================================================================== MTPLX cards
# MTPLX has no /slots or llama.cpp metrics: these cards read /v1/mtplx/snapshot
# (model, profile, window, in-flight request, session bank, memory, settings)
# and /v1/mtplx/flight (live decode). Same layout and heights as the llama cards.
def pick(rec, *names, default=None):
    for n in names:
        v = (rec or {}).get(n)
        if v is not None:
            return v
    return default

def mx_req(r):
    """One finished MTPLX request as a dict with the llama request-row keys."""
    t1, took = pick(r, "completed_at_s"), pick(r, "request_elapsed_s")
    dr = pick(r, "drafted_tokens", default=0) or 0
    return {"t0": (t1 - took) if (t1 and took) else t1, "t1": t1,
            "ctx": pick(r, "context_len", "prompt_tokens", default=0),
            "new": pick(r, "new_prefill_tokens", default=0),
            "pp": pick(r, "prefill_tok_s", "prompt_tps"),
            "gen": pick(r, "completion_tokens", default=0),
            "tg": pick(r, "display_decode_tok_s", "decode_tok_s"),
            "acc": (pick(r, "accepted_drafts", default=0) or 0) / dr if dr else None,
            "restore": pick(r, "session_restore_mode", default="cold"),
            "cached": pick(r, "cached_tokens", default=0) or 0,
            "error": bool(pick(r, "error"))}

def mx_wall(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "–"

def mx_card_context(d):
    mx = d["mx"]; plan = mx.get("memory_plan") or {}; bank = mx.get("session_bank") or {}
    n_ctx = d.get("n_ctx") or 0
    used = d.get("prompt") or 0
    frac = used / n_ctx if n_ctx else 0
    kvq = plan.get("kv_quantization") or "off"
    per_tok = plan.get("kv_bytes_per_token_effective") or plan.get("kv_bytes_per_token")
    L = [lv("request", f"{bar(frac, 16)} {k(used)} / {k(n_ctx) if n_ctx else NA} tokens" if d.get("busy") else f"{bar(0, 16)} 0 / {k(n_ctx) if n_ctx else NA} tokens"),
         lv("KV", f"{MAG}{'bf16' if kvq == 'off' else kvq}{R} · {size(per_tok) if per_tok else NA}/token · {size(plan.get('kv_reserve_bytes')) if plan.get('kv_reserve_bytes') else NA} reserved"),
         lv("sessions", f"{bank.get('entries', 0)} in the bank · {size(bank.get('total_nbytes') or 0)} of {size(bank.get('effective_max_bytes') or 0)} RAM")]
    if level["context"] >= 1:
        cold = bank.get("cold_tier") or {}
        L.append(lv("SSD bank", f"{cold.get('writes_completed', 0)} saved · {cold.get('restore_hits', 0)} restored · {cold.get('restore_misses', 0)} misses"
                    if cold else NA))
        lr = mx_req(mx["latest"]) if mx.get("latest") else None
        L.append(lv("last", (f"{lr['restore']} start · {k(lr['cached'])} tokens re-used, {k(lr['new'])} read" if lr else "none")
                    + f" · miss: {bank.get('last_miss_reason') or 'none'}"))
    if level["context"] >= 2:
        L.append(lv("session", f"each ≤{size(bank.get('per_session_max_bytes') or 0)} in RAM · {bank.get('max_entries', 0)} entries max · idle {dur(bank.get('idle_ttl_s'))}"))
        L.append(lv("window", f"{k(n_ctx)} served · {k(plan.get('context_window_fit'))} would fit (MTPLX plan)"))
        L.append(lv("note", ((plan.get("notes") or ["none"])[0])[:90]))
        L.append(lv("scheduler", f"{(mx.get('scheduler') or {}).get('mode', NA)}: one request at a time, the others wait"))
    return "CONTEXT", f"{bar(frac, 10)} {frac * 100:3.0f}%", L

def mx_card_memory(d):
    mem = d["mx"].get("mem") or {}
    L = [lv("weights", size(mem.get("model_weights_bytes")) if mem.get("model_weights_bytes") else NA),
         lv("active", f"{size(mem.get('active_memory_bytes') or 0)} · cache {size(mem.get('cache_memory_bytes') or 0)}"),
         lv("peak", size(mem.get("peak_memory_bytes") or 0))]
    if level["memory"] >= 2:
        L.append(lv("GPU limit", f"{size(S['gpu_limit'][0])} ({S['gpu_limit'][1]})" if S.get("gpu_limit") else NA))
        L.append(lv("allocator", f"{(d['mx'].get('allocator_fraction') or 0) * 100:.0f}% of the MLX limit · bank {size(mem.get('session_bank_bytes') or 0)}"))
    return "MEMORY", f"MLX {size(mem.get('active_memory_bytes') or 0)}", L

def mx_card_activity(d):
    mx = d["mx"]; life = mx.get("lifetime") or {}; roll = mx.get("rolling") or {}; fl = d.get("flight") or {}
    _, _, words = status_of(d)
    L = [lv("now", words)]
    summary = f"{DIM}idle{R}"
    if d.get("busy") and not d.get("decoded"):
        remaining = max(d["prompt"] - d["cached"] - d["processed"], 0)
        rate = d.get("pp_rate")
        eta = dur(remaining / rate) if rate else "N/A"
        L += [lv("prompt", f"{k(d['prompt'])} tokens · {k(d['cached'])} cached · {k(remaining)} left"),
              lv("speed", f"read {rate or 0:.0f} tok/s · ETA {YEL}{B}{eta}{R}")]
        summary = f"ETA {eta}"
    elif d.get("busy"):
        L += [lv("prompt", f"{k(d['prompt'])} tokens · read"),
              lv("speed", f"generate {d.get('tg_rate') or 0:.1f} tok/s · {d.get('decoded')} tokens out")]
        summary = f"{d.get('tg_rate') or 0:.1f} tok/s"
    else:
        L += [lv("prompt", "0 tokens · 0 cached · 0 left"), lv("speed", f"0 tok/s · ETA {DIM}none{R}")]
    if level["activity"] >= 1:
        L.append(lv("average", f"generate {roll['mean']:.1f} tok/s (5 min) · max {roll.get('max') or 0:.1f}" if roll.get("mean") else f"generate 0 tok/s {DIM}(no requests in 5 min){R}"))
        r = mx_req(mx["latest"]) if mx.get("latest") else None
        L.append(lv("last", f"read {r['pp'] or 0:.0f} · generate {r['tg'] or 0:.1f} tok/s · {k(r['gen'])} tokens" if r else f"{DIM}none{R}"))
        acc, dr = fl.get("accepted_by_depth"), fl.get("drafted_by_depth")
        if isinstance(acc, dict): acc = [acc[x] for x in sorted(acc)]
        if isinstance(dr, dict): dr = [dr[x] for x in sorted(dr)]
        if isinstance(acc, list) and isinstance(dr, list) and sum(dr):
            L.append(lv("drafts", "  ".join(f"D{i + 1} {a / b * 100:.0f}%" for i, (a, b) in enumerate(zip(acc, dr)) if b)))
        else:
            L.append(lv("drafts", f"{r['acc'] * 100:.0f}% accepted (last request)" if r and r.get("acc") is not None else NA))
    if level["activity"] >= 2:
        L.append(lv("totals", f"{k(life.get('prompt_tokens_total'))} read · {k(life.get('cached_tokens_total'))} cached · "
                              f"{k(life.get('completion_tokens_total'))} generated"))
        L.append(lv("requests", f"{life.get('requests_total', 0)} done · {life.get('cancelled_total', 0)} cancelled · "
                                f"{max((mx.get('active_requests') or 0) - 1, 0)} waiting"))
        L.append(lv("phase", f"{fl.get('phase') or 'idle'} · stalled {dur(fl.get('stalled_s')) if fl.get('stalled_s') else 'no'}"))
    return "ACTIVITY", summary, L

def mx_card_model(d):
    mx = d["mx"]; st = mx.get("settings") or {}; prof = mx.get("profile") or {}
    mc = st.get("model_controls") or {}
    L = [lv("model", mx.get("model_id") or NA),
         lv("profile", f"{prof.get('name', NA)} · MTP depth {st.get('depth', NA)} of {st.get('depth_max', NA)}"),
         lv("sampling", f"temperature {st.get('temperature', NA)} · top_p {st.get('top_p', NA)} · top_k {st.get('top_k', NA)}")]
    if level["model"] >= 2:
        L.append(lv("weights", os.path.basename(mc.get("model_ref") or "") or NA))
        L.append(lv("arch", f"{st.get('architecture_id', NA)} · {st.get('support_level', NA)}"))
        rp = st.get("reasoning_policy") or {}
        L.append(lv("thinking", f"{st.get('reasoning', NA)} · efforts {', '.join(rp.get('effort_levels') or []) or NA} (default {rp.get('default_effort', NA)})"))
        L.append(lv("server", f"MTPLX · pid {d.get('pid')} · up {d.get('etime', 'N/A')}"))
    return "MODEL", f"MTPLX · {(mx.get('profile') or {}).get('name', 'N/A')}", L

def mx_card_health(d):
    mx = d["mx"]; c = LOG.counts
    sleeps = [e for e in S["slow"].get("sleep_events", []) if e[1] == "Sleep"]
    guard = mx.get("memory_guard_events") or []
    summary = f"{GRN}ok{R} {d['health_ms']:.0f} ms" if d.get("health_ms") is not None else NA
    L = [lv("log", f"{(RED + str(c['E']) + R) if c['E'] else 0} errors · {(YEL + str(c['W']) + R) if c['W'] else 0} warnings"),
         lv("guard", (YEL if guard else "") + f"{len(guard)} memory-guard events" + (R if guard else "") +
             f" · pressure {({1: 'normal', 2: 'WARNING', 4: 'CRITICAL'}).get(mx.get('memory_pressure_level'), 'N/A')}"),
         lv("sleep", (f"{GRN}kept awake{R}" if d.get("awake") else f"{YEL}not kept awake{R}") + " · "
            + (f"{YEL}{len(sleeps)} sleeps{R}" if sleeps else "0 sleeps") + " since start")]
    if level["health"] >= 2:
        evs = [f"{YEL}{json.dumps(e)[:90]}{R}" for e in guard[-3:]] or [f"{DIM}memory guard: none{R}"]
        sl = [f"{DIM}{t_} {kind} {why}{R}" for t_, kind, why in S["slow"].get("sleep_events", [])[-3:]] or [f"{DIM}sleep/wake: none{R}"]
        L += evs + [f"{DIM}–{R}"] * (3 - len(evs)) + sl + [f"{DIM}–{R}"] * (3 - len(sl))
    return "HEALTH", summary, L

MX_CARDS = {"context": mx_card_context, "memory": mx_card_memory, "activity": mx_card_activity,
            "model": mx_card_model, "health": mx_card_health}


REQ_HEAD = f"{'started':8}  {'context':>8}  {'new':>7}  {'read/s':>6}  {'output':>6}  {'gen/s':>5}  {'took':>6}  {'drafts':>6}"

def req_row(r, wall=None):
    took = r["t1"] - r["t0"] if r.get("t1") is not None and r.get("t0") is not None else None
    acc = f"{round(r['acc'] * 100)}%" if r.get("acc") is not None else "–"
    return (f"{(wall or LOG.wall)(r.get('t0')):8}  {k(r.get('ctx')):>8}  {k(r.get('new')):>7}  {(r.get('pp') or 0):>6.0f}  "
            f"{k(r.get('gen')):>6}  {(r.get('tg') or 0):>5.1f}  {dur(took):>6}  {acc:>6}" + (f"  {RED}error{R}" if r.get("error") else ""))

def card_requests(d, n):
    if d.get("backend") == "mtplx":
        reqs = [mx_req(r) for r in (d["mx"].get("recent") or [])]
        running = f" · {d['mx'].get('active_requests')} running" if d["mx"].get("active_requests") else ""
        rows = [req_row(r, mx_wall) for r in reversed(reqs[-n:])] or [f"{DIM}no finished requests since MTPLX started{R}"]
    else:
        reqs = list(LOG.requests)
        running = f" · {len(LOG.current)} running" if LOG.current else ""
        rows = [req_row(r) for r in reversed(reqs[-n:])] or [f"{DIM}no finished requests in this log yet{R}"]
    L = [f"{DIM}{REQ_HEAD}{R}"] + rows + [f"{DIM}–{R}"] * (n - len(rows))
    return "RECENT REQUESTS", f"{len(reqs)} finished{running} {DIM}· tab 3 for all{R}", L

def log_view(n, width):
    if not LOG.path:
        return [f"{DIM}" + NOLOG_TEXT + f"{R}"] + [""] * (n - 1)
    lines = [l for l in LOG.lines if not ui["errors_only"] or (TS.match(l) and TS.match(l).group(5) in "EW")]
    end = len(lines) - ui["log_scroll"]
    out = []
    for line in lines[max(0, end - n * (4 if ui["wrap"] else 1)):end]:
        m = TS.match(line)
        col = RED if m and m.group(5) == "E" else YEL if m and m.group(5) == "W" else ""
        if not ui["wrap"]:
            out.append(col + line if col or not m else f"{DIM}{line[:m.end()]}{R}{line[m.end():]}")
        else:
            out += [col + part for part in wrap(line, width)]
    out = out[-n:] or [f"{DIM}(no log lines){R}"]
    return out + [""] * (n - len(out))             # fixed height: the card does not jump while the log fills

NOLOG_TEXT = "no log file: MTPLX writes to the terminal that started it (start it with ./carl.sh to see it here)"

# ===================================================================== drawing
def draw_card(name, title, summary, lines, w, lvl=None):
    """Rows of (text, [(x0, x1, action)]) for a bordered card w columns wide.
    The header shows the detail level as dots: ○○ collapsed, ●○ normal, ●● full."""
    lvl = level.get(name, 1) if lvl is None else lvl
    arrow = "▾" if lvl else "▸"
    dots = f"{CYN}{'●' * lvl}{DIM}{'○' * (2 - lvl)}{R}"
    head = f"{DIM}╭─{R} {B}{CYN}{arrow} {title}{R} {dots} "
    if summary:
        head += f"{summary} "
    fill = max(w - vlen(head) - 1, 0)
    rows = [(fit(head + DIM + "─" * fill, w - 1) + f"{DIM}╮{R}", [(0, w, f"level:{name}")])]
    if lvl:
        for ln in lines:
            ln = ln if isinstance(ln, Ln) else Ln(ln)
            spans = [(2 + a, 2 + b, act) for a, b, act in ln.spans]
            if ln.act:
                spans.append((2, w - 2, ln.act))
            rows.append((f"{DIM}│{R} " + fit(ln.text, w - 4) + f" {DIM}│{R}", spans))
    rows.append((f"{DIM}╰{'─' * (w - 2)}╯{R}", []))
    return rows

CARDS = {"connect": card_connect, "context": card_context, "memory": card_memory, "activity": card_activity,
         "model": card_model, "health": card_health, "system": card_system}

def column(names, d, w):
    rows = []
    for nm in names:
        fn = MX_CARDS.get(nm) if d.get("backend") == "mtplx" else None
        title, summary, lines = (fn or CARDS[nm])(d)
        rows += draw_card(nm, title, summary, lines, w)
    return rows

def body_overview(d, cols, height):
    rows = []
    if cols >= 100:
        cw = (cols - 3) // 2
        left = column(["connect", "context", "memory"], d, cw)
        right = column(["activity", "model", "health", "system"], d, cols - 3 - cw)
        for i in range(max(len(left), len(right))):
            lt, ls = left[i] if i < len(left) else (" " * cw, [])
            rt, rs = right[i] if i < len(right) else ("", [])
            rows.append((" " + lt + " " + rt, [(1 + a, 1 + b, act) for a, b, act in ls] +
                                             [(2 + cw + a, 2 + cw + b, act) for a, b, act in rs]))
    else:
        for r in column(["connect", "activity", "context", "memory", "model", "health", "system"], d, cols - 1):
            rows.append((" " + r[0], [(1 + a, 1 + b, act) for a, b, act in r[1]]))
    title, summary, lines = card_requests(d, 3 if level["requests"] == 1 else 8)
    rows += [(" " + t, [(1 + a, 1 + b, act) for a, b, act in sp]) for t, sp in draw_card("requests", title, summary, lines, cols - 1)]
    logs = log_view(ui["lines"], cols - 5)
    lt = (f"{DIM}{os.path.basename(os.path.realpath(D['log_path']))} · tab 4 for the full log{R}" if D.get("log_path")
          else f"{DIM}no log file{R}")
    rows += [(" " + t, [(1 + a, 1 + b, act) for a, b, act in sp]) for t, sp in draw_card("log", "LOG", lt, logs, cols - 1)]
    sc = min(ui["scroll"], max(len(rows) - height, 0)); ui["scroll"] = sc
    return rows[sc:sc + height]

def body_connect(d, cols, height):
    w = cols - 1
    rows = []
    title, summary, lines = card_connect(d)
    keep = level["connect"]; level["connect"] = 2
    title, summary, lines = card_connect(d); level["connect"] = keep
    rows += draw_card("connect", "CONNECTION", summary, lines, w, lvl=2)
    here = installed_here()
    guide = []
    if here:
        guide.append(f"{GRN}✓ This Mac:{R} already configured by install.sh for this server ("
                     + ", ".join(f"{c}: provider {p}" for c, p in here) + "). Nothing to paste here.")
    guide += [f"{B}Clients in the VM:{R}   copy the bundle in, then ./install-clients.sh && ./install.sh",
              f"{B}Clients on this Mac:{R} ./client/install-clients.sh && ./client/install.sh --local",
              f"{DIM}The installer merges without overwriting your providers, default model or agents, and adds the coder, sidebar and session switcher.{R}",
              f"{B}By hand:{R}             a provider block only (id llm-deploy), copied to the clipboard; it adds, never replaces",
              buttons("", [("OpenCode config", "opencode"), ("Pi config", "pi"), ("curl test", "curl")])]
    rows += draw_card("guide", "CLIENT SETUP", "", guide, w)
    kind = ui["preview"]
    text = preview_text(kind, d, mask=not ui["key_shown"])
    note = (f"{GRN}copied to the clipboard ✓{R}" if ui["copied"] == kind else f"{DIM}click its button (or o/p/t) to copy{R}")
    where = {"opencode": "→ add under \"provider\" in ~/.config/opencode/opencode.json (keep your other providers), restart OpenCode, pick it with /models",
             "pi": "→ add under \"providers\" in ~/.pi/agent/models.json (keep yours), then /model in Pi",
             "curl": "→ run anywhere that can reach the server"}[kind]
    plines = text.splitlines()
    room = max(height - len(rows) - 3, 3)
    sc = min(ui["prev_scroll"], max(len(plines) - room, 0)); ui["prev_scroll"] = sc
    shown = [f"{DIM}{where}{R}"] + plines[sc:sc + room - 1]
    names = {"opencode": "OPENCODE CONFIG", "pi": "PI CONFIG", "curl": "CURL TEST"}
    rows += draw_card("preview", names[kind], note, shown, w)
    return [(" " + t, [(1 + a, 1 + b, act) for a, b, act in sp]) for t, sp in rows][:height]

def body_requests(d, cols, height):
    reqs = list(reversed(LOG.requests))
    done = [r for r in reqs if r.get("tg")]
    avg_pp = sum(r.get("pp") or 0 for r in done) / len(done) if done else 0
    avg_tg = sum(r.get("tg") or 0 for r in done) / len(done) if done else 0
    head = (f"{len(reqs)} in this log · avg read {avg_pp:.0f} tok/s · avg generate {avg_tg:.1f} tok/s"
            + (f" · {len(LOG.current)} running" if LOG.current else ""))
    room = max(height - 4, 3)
    sc = min(ui["req_scroll"], max(len(reqs) - room, 0)); ui["req_scroll"] = sc
    lines = [f"{DIM}{REQ_HEAD}  {'prompt from cache':>17}{R}"]
    for r in reqs[sc:sc + room]:
        cached = (r.get("ctx") or 0) - (r.get("new") or 0) - (r.get("gen") or 0)
        lines.append(req_row(r) + f"  {k(max(cached, 0)):>17}")
    if not reqs:
        lines.append(f"{DIM}no finished requests yet{R}")
    return [(" " + t, [(1 + a, 1 + b, act) for a, b, act in sp]) for t, sp in draw_card("requests_tab", "REQUESTS", head, lines, cols - 1)]

def body_log(d, cols, height):
    bar_ = buttons("", [(f"wrap {'on' if ui['wrap'] else 'off'} (w)", "wrap"),
                        (f"errors only {'on' if ui['errors_only'] else 'off'} (f)", "errors"),
                        (("follow (end)" if ui["log_scroll"] else "following"), "follow")])
    path = D.get("log_path") or ""
    n = max(height - 3, 3) if not args.once else max(shutil.get_terminal_size((120, 36))[1] - 8, 10)
    lines = [bar_] + log_view(n, cols - 5)
    summ = f"{DIM}{path.replace(os.path.expanduser('~'), '~')}" + (f" · {ui['log_scroll']} lines back" if ui["log_scroll"] else "") + R
    return [(" " + t, [(1 + a, 1 + b, act) for a, b, act in sp]) for t, sp in draw_card("logtab", "LOG", summ, lines, cols - 1)]

# ===================================================================== settings (tab 5)
# The Settings tab chooses the backend (llama.cpp or MTPLX) and its settings,
# saves them (~/.config/llm-deploy/llama.env or mtplx.env; the launchers read
# them: flags > environment > file > built-in) and restarts the server. If the
# new server does not come up, the old one is started again.
CONF_DIR = os.path.expanduser("~/.config/llm-deploy")
SETTINGS_FILE = os.path.expanduser(os.environ.get("SETTINGS_FILE", f"{CONF_DIR}/llama.env"))
SETTINGS_FILE_MTPLX = os.path.expanduser(os.environ.get("SETTINGS_FILE_MTPLX", f"{CONF_DIR}/mtplx.env"))
MODELS_DIR = os.environ.get("MODELS_DIR", os.path.expanduser("~/models/gguf"))
VM_ADDR = os.environ.get("VM_HOST", "192.168.42.1")
PORTS = {"llama": int(os.environ.get("LLAMA_PORT", 8080)), "mtplx": int(os.environ.get("MTPLX_PORT", 8000))}
def local_addrs():
    """IPv4 addresses of this Mac (not loopback, not the VM address): the LAN, Parallels, ..."""
    out = []
    for m in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)", sh(["ifconfig"])):
        a = m.group(1)
        if a != "127.0.0.1" and a != VM_ADDR and a not in out:
            out.append(a)
    return out

ADDRS = local_addrs()
NET_CHOICES = ["auto", "local", "vm"] + ADDRS          # an address = HOST=that address in the settings file
MX_WEIGHTS = {"grant": 16.9e9, "pocket": 17.5e9}     # MTPLX 27B builds (bytes, from their memory plans)
MX_KV_TOK = {"off": 65536, "q8": 34816, "q4": 18432}  # KV bytes per token of the 27B (bf16 / q8 / q4)

def registry():
    rows = []
    try:
        for line in open(os.path.join(REPO, "host", "models.conf")):
            f = line.rstrip("\n").split("|")[:9]
            if line.strip() and not line.startswith("#") and len(f) == 9:
                rows.append({"name": f[0], "file": f[3], "alias": f[6], "spec": f[7]})
    except OSError:
        pass
    return rows

REG = registry()
# key, label, choices, env name in the settings file, built-in default (written as "no key")
BACKEND_ROW = ("backend", "backend", ["llama", "mtplx"], None, "llama")
LLAMA_ROWS = [
    ("model", "model", ["default"] + [r["name"] for r in REG], "MODEL_NAME", "default"),
    ("kv", "KV cache", ["q4_0", "q8_0"], "KV", "q4_0"),
    ("ctx", "context/slot", [32768, 49152, 65536, 98304, 131072, 163840], "CTX", 98304),
    ("slots", "slots", ["auto", "1", "2"], "SLOTS", "auto"),
    ("cache", "RAM cache", ["auto", 1024, 2560, 4096, 6144, 8192], "CACHE_RAM", "auto"),
    ("net", "network", NET_CHOICES, "NET", "auto"),
    ("temp", "temperature", ["1.0", "0.6"], "TEMP", "1.0"),
    ("presence", "presence", ["0", "1.5"], "PRESENCE", "0"),
    ("spec", "speculation", ["model", "none"], "SPEC", "model"),
]
MTPLX_ROWS = [
    ("preset", "preset", ["grant", "pocket"], None, "grant"),
    ("mctx", "context", [32768, 49152, 57344], "CONTEXT", 49152),
    ("profile", "profile", ["sustained", "turbo", "stable"], "PROFILE", "sustained"),
    ("depth", "MTP depth", ["1", "2", "3"], "DEPTH", "2"),
    ("mkv", "KV cache", ["off", "q8", "q4"], "KV_QUANT", "off"),
    ("mnet", "network", NET_CHOICES, "NET", "auto"),
]
ADV_ROW = ("adv", "advanced", ["hidden", "shown"], None, "hidden")
LLAMA_ADV = [
    ("top_k", "top_k", ["20", "40", "0"], "TOP_K", "20"),
    ("top_p", "top_p", ["0.95", "0.9", "0.8", "1.0"], "TOP_P", "0.95"),
    ("min_p", "min_p", ["0", "0.05", "0.1"], "MIN_P", "0"),
    ("repeat", "repeat penalty", ["1.0", "1.05", "1.1"], "REPEAT", "1.0"),
    ("specn", "draft tokens", ["model", "1", "2", "3"], "SPEC_N", "model"),
    ("ub", "-ub batch", ["512", "1024", "2048"], "UB", "512"),
    ("ckpt", "checkpoints", ["8", "4", "16"], "CKPT", "8"),
    ("ckstep", "ckpt step", ["4096", "1024", "2048", "8192"], "CKPT_STEP", "4096"),
]
MTPLX_ADV = [
    ("sched", "scheduler", ["default", "serial", "cooperative", "ar_batch", "hyper"], "SCHEDULER", "default"),
    ("batching", "batching", ["default", "latency", "agent", "solo", "throughput"], "BATCHING", "default"),
    ("pchunk", "prefill chunk", ["default", "1024", "2048", "4096"], "PREFILL_CHUNK", "default"),
    ("ssd", "SSD sessions", ["on", "off", "write-only"], "SSD_CACHE", "on"),
]
NUMERIC = {"ctx", "mctx", "temp", "presence", "top_k", "top_p", "min_p", "repeat", "specn", "ub", "ckpt", "ckstep", "pchunk", "cache"}
SETTINGS = [BACKEND_ROW] + LLAMA_ROWS                 # rows() picks the backend's list
SET_HELP = {
    "backend": "llama.cpp: GGUF models, quantized KV, 2 slots, long context · MTPLX: MLX 27B builds, faster decode, ≤48K",
    "model": "registry model (host/models.conf); default = the first entry, or the IQ3 35B when that does not fit",
    "kv": "q4_0: less memory, the tested default · q8_0: more exact long-range recall, about 2x the KV memory",
    "ctx": "tokens per slot; 96K is the default · 128K-160K work but read and decode slower (REFERENCE.md)",
    "slots": "auto = 2 when two full windows fit (main session + coder subagent), else 1",
    "cache": "RAM prompt cache in MiB: keeps evicted prompts so a session comes back without a full re-read",
    "net": "auto = the VM address if VMware's network is up, else this Mac only · an address = that interface (LAN: other computers can reach it)",
    "temp": "1.0 = Qwen's thinking-mode value (default) · 0.6 = more precise coding (35B card)",
    "presence": "0 = default · 1.5 = fewer repetition loops (35B card, general use)",
    "spec": "model = the registry's speculative decoding (MTP + n-gram) · none = off",
    "preset": "grant = grant-ai 4-bit build · pocket = PocketAiHub speed build (both abliterated Qwen3.8-27B)",
    "mctx": "one window for the whole server · 48K is the tested limit; 56K failed with 2 sessions (REFERENCE.md, MTPLX)",
    "profile": "sustained = the long-context default · turbo = faster short bursts · stable = conservative",
    "depth": "MTP draft depth: 2 measured best on this Mac (AR 7.5, D1 13.4, D2 23.6, D3 20.8 tok/s)",
    "mkv": "off = bf16 (tested) · q8 / q4 = less memory, but long sessions failed on MTPLX 2.11 (REFERENCE.md)",
    "mnet": "auto = the VM address if VMware's network is up, else this Mac only · an address = that interface (LAN: other computers can reach it)",
    "adv": "more server settings: sampling, speculation, batch and checkpoints (llama.cpp); scheduler and caches (MTPLX)",
    "top_k": "sample from the k most likely tokens; Qwen: 20 · 0 = off",
    "top_p": "nucleus sampling; Qwen: 0.95 (thinking), 0.8 (no thinking: the client sends it)",
    "min_p": "drop tokens below min_p × the top probability; Qwen: 0",
    "repeat": "repetition penalty; Qwen: 1.0 (off) · use presence instead",
    "specn": "draft tokens per speculation step; model = the registry value (27B: 1, 35B: 2: measured best)",
    "ub": "-ub physical batch; 512 measured best on Metal (90.5 vs 88.6 / 86.1 tok/s for 1024 / 2048)",
    "ckpt": "context checkpoints per slot (each ~63 MiB on the 35B, ~150 MiB on the 27B); more did not help (Phase 6)",
    "ckstep": "minimum tokens between checkpoints; 1024 vs 4096 made no difference in the Phase 6 test",
    "sched": "MTPLX scheduler; default (serial) runs one request at a time: measured best for MTP decode",
    "batching": "MTPLX concurrent batching preset; default = latency",
    "pchunk": "MTPLX prefill chunk in tokens; default = the profile's value",
    "ssd": "MTPLX session bank on the SSD: on = sessions come back after the RAM bank is full (14.8 s for 24K)",
}

def rows(p):
    mx = p.get("backend") == "mtplx"
    adv = (MTPLX_ADV if mx else LLAMA_ADV) if p.get("adv") == "shown" else []
    return [BACKEND_ROW] + (MTPLX_ROWS if mx else LLAMA_ROWS) + [ADV_ROW] + adv

ADV_WARN = ("CAUTION: these values are tuned and measured (REFERENCE.md).",
            "         A change can make the model slower, or its answers worse. Defaults (x) sets them back.")

def ctx_label(v):
    return f"{int(v) // 1024}K" if str(v).isdigit() else str(v)

def read_env(path):
    out = {}
    try:
        for line in open(path):
            if "=" in line and not line.startswith("#"):
                kk, v = line.strip().split("=", 1); out[kk] = v
    except OSError:
        pass
    return out

def running_settings(d):
    """The values the running server uses: llama.cpp from its command line, MTPLX from its snapshot."""
    cmd = d.get("cmd", "")
    host = flag(cmd, "--host", default="")
    net = "vm" if host == VM_ADDR else "local" if host in ("127.0.0.1", "::1") else host or "N/A"
    if d.get("backend") == "mtplx" and d.get("mx"):
        mx = d["mx"]; st = mx.get("settings") or {}; plan = mx.get("memory_plan") or {}
        mid = (mx.get("model_id") or "") + " " + ((st.get("model_controls") or {}).get("model_ref") or "")
        return {"backend": "mtplx", "preset": "pocket" if "pocket" in mid.lower() else "grant",
                "mctx": mx.get("context_window") or "N/A", "profile": (mx.get("profile") or {}).get("name", "N/A"),
                "depth": str(st.get("depth", "N/A")), "mkv": plan.get("kv_quantization") or "off", "mnet": net,
                "sched": (mx.get("scheduler") or {}).get("mode", "N/A"), "batching": (mx.get("scheduler") or {}).get("preset", "N/A"),
                "pchunk": str(((mx.get("scheduler") or {}).get("config") or {}).get("prefill_chunk_tokens", "N/A")),
                "ssd": ((mx.get("session_bank") or {}).get("cold_tier") or {}).get("mode", "N/A")}
    if "llama-server" not in cmd:
        return {}
    mfile = os.path.basename(flag(cmd, "-m", "--model", default="") or "")
    model = next((r["name"] for r in REG if r["file"] == mfile), mfile or "N/A")
    spec = flag(cmd, "--spec-type", default="none")
    return {"backend": "llama", "model": model, "kv": flag(cmd, "-ctk", "--cache-type-k", default="f16"),
            "ctx": int(flag(cmd, "--kv-unified-per-slot", default=0) or 0) or d.get("n_ctx") or "N/A",
            "slots": flag(cmd, "--parallel", "-np", default="1"), "cache": int(flag(cmd, "--cache-ram", default=0) or 0),
            "net": net, "temp": flag(cmd, "--temp", default="N/A"), "presence": flag(cmd, "--presence-penalty", default="N/A"),
            "spec": "none" if spec == "none" else "model",
            "top_k": flag(cmd, "--top-k", default="N/A"), "top_p": flag(cmd, "--top-p", default="N/A"),
            "min_p": flag(cmd, "--min-p", default="N/A"), "repeat": flag(cmd, "--repeat-penalty", default="N/A"),
            "specn": flag(cmd, "--spec-draft-n-max", default="N/A"), "ub": flag(cmd, "-ub", default="N/A"),
            "ckpt": flag(cmd, "--ctx-checkpoints", default="N/A"), "ckstep": flag(cmd, "--checkpoint-min-step", default="N/A")}

FROM_RUNNING = {"kv", "ctx", "temp", "presence", "spec", "model", "preset", "mctx", "profile", "depth", "mkv",
                "top_k", "top_p", "min_p", "repeat", "ckpt", "ckstep", "ub"}

def pending_init(d):
    """Start from the running server (so Apply without changes restarts the same setup);
    slots, cache and network keep their saved or automatic choice (or the running address)."""
    run = running_settings(d)
    saved = {**{f"llama:{k_}": v for k_, v in read_env(SETTINGS_FILE).items()},
             **{f"mtplx:{k_}": v for k_, v in read_env(SETTINGS_FILE_MTPLX).items()}}
    p = {"backend": run.get("backend", "llama"), "adv": "hidden"}
    for be, rws in (("llama", LLAMA_ROWS + LLAMA_ADV), ("mtplx", MTPLX_ROWS + MTPLX_ADV)):
        for key, _, choices, env, default in rws:
            v = saved.get(f"{be}:{env}", default) if env else default
            if run.get("backend") == be and run.get(key) not in (None, "N/A") and (
                    key in FROM_RUNNING or (key in ("net", "mnet") and f"{be}:NET" not in saved and f"{be}:HOST" not in saved)):
                v = run[key]
            if key in ("net", "mnet") and saved.get(f"{be}:HOST"):
                v = saved[f"{be}:HOST"]
            if key in ("ctx", "mctx"):
                v = int(v) if str(v).isdigit() else default
            if key == "cache" and str(v).isdigit():
                v = int(v)
            p[key] = v
    return p

def resolve_default_model(p):
    if p["model"] != "default":
        return p["model"]
    try:
        return subprocess.run([sys.executable, os.path.join(REPO, "tools", "llama-fit.py"), "--pick-default",
                               "--ctx", str(p["ctx"])], capture_output=True, text=True, timeout=20).stdout.strip() or REG[0]["name"]
    except Exception:
        return REG[0]["name"] if REG else ""

def fit_line(p):
    """(ok, text): does the pending setup fit the GPU limit, and is the model downloaded?"""
    limit = (S.get("gpu_limit") or gpu_limit())[0]
    if p["backend"] == "mtplx":
        w = MX_WEIGHTS.get(p["preset"], 17e9)
        need = w + MX_KV_TOK.get(p["mkv"], 65536) * p["mctx"] + 3 * GIB   # + MTPLX's runtime transients (3 GiB)
        ok = need <= limit
        warn = f" · {YEL}over 48K: two sessions do not fit (tested){R}" if p["mctx"] > 49152 else ""
        return ok, (f"{GRN if ok else RED}{'fits' if ok else 'does not fit'}{R}: MTPLX {p['preset']} needs about {size(need)} "
                    f"for {ctx_label(p['mctx'])} ({'bf16' if p['mkv'] == 'off' else p['mkv']}) of {size(limit)}{warn}")
    name = resolve_default_model(p)
    r = next((x for x in REG if x["name"] == name), None)
    if not r:
        return False, f"{RED}unknown model {name}{R}"
    path = os.path.join(MODELS_DIR, r["file"])
    if not os.path.exists(path):
        return False, f"{RED}{name} is not downloaded{R}: ./carl.sh download {name}"
    try:
        shp, w = model_shape(local_meta(path)), os.path.getsize(path)
        need = lambda n: w + kv_bytes_per_token(shp, p["kv"], p["kv"]) * p["ctx"] * n + shp["rs_bytes"] * n + OVERHEAD
        n = 2 if p["slots"] == "auto" and need(2) <= limit else 1 if p["slots"] == "auto" else int(p["slots"])
        ok = need(n) <= limit
        return ok, (f"{GRN if ok else RED}{'fits' if ok else 'does not fit'}{R}: {name} needs {size(need(n))} for {n} × "
                    f"{ctx_label(p['ctx'])} ({p['kv']}) of {size(limit)} GPU memory")
    except Exception as e:
        return False, f"{RED}fit check failed: {e}{R}"

FIT = {"key": None, "res": (False, "")}
def fit_cached(p):
    key = tuple(sorted((k_, str(v)) for k_, v in p.items()))
    if FIT["key"] != key:
        FIT["key"], FIT["res"] = key, fit_line(p)
    return FIT["res"]

def write_settings(p):
    be = p["backend"]
    path = SETTINGS_FILE_MTPLX if be == "mtplx" else SETTINGS_FILE
    lines = [f"# CARL {'MTPLX' if be == 'mtplx' else 'llama.cpp'} settings, written by the monitor's Settings tab.",
             f"# {'serve.sh grant|pocket' if be == 'mtplx' else 'serve-llama.sh'} reads them: flags > environment > this file > built-in defaults."]
    for key, _, _, env, default in (MTPLX_ROWS + MTPLX_ADV if be == "mtplx" else LLAMA_ROWS + LLAMA_ADV):
        if env == "NET" and str(p[key]).count(".") == 3:
            lines.append(f"HOST={p[key]}")             # one address of this Mac
        elif env and str(p[key]) != str(default):
            lines.append(f"{env}={p[key]}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return path

def env_from_cmd(cmd):
    """Environment that makes serve-llama.sh start the same llama.cpp server as cmd (the rollback)."""
    e = {"SETTINGS_FILE": "none", "MODEL": flag(cmd, "-m", "--model", default=""), "HOST": flag(cmd, "--host", default="127.0.0.1"),
         "CTX": flag(cmd, "--kv-unified-per-slot", default="") or flag(cmd, "-c", "--ctx-size", default="98304"),
         "SLOTS": flag(cmd, "--parallel", "-np", default="1"), "KV_K": flag(cmd, "-ctk", default="q4_0"),
         "KV_V": flag(cmd, "-ctv", default="q4_0"), "TEMP": flag(cmd, "--temp", default="1.0"),
         "TOP_P": flag(cmd, "--top-p", default="0.95"), "TOP_K": flag(cmd, "--top-k", default="20"),
         "MIN_P": flag(cmd, "--min-p", default="0"), "PRESENCE": flag(cmd, "--presence-penalty", default="0"),
         "CACHE_RAM": flag(cmd, "--cache-ram", default=""), "ALIAS": flag(cmd, "--alias", default=""),
         "SPEC": flag(cmd, "--spec-type", default="none"), "SPEC_N": flag(cmd, "--spec-draft-n-max", default="1")}
    return {kk: v for kk, v in e.items() if v}

def env_from_mtplx(cmd):
    """Environment that makes serve.sh start the same MTPLX server as cmd (the rollback)."""
    e = {"SETTINGS_FILE_MTPLX": "none", "MODEL": flag(cmd, "--model", default=""), "MODEL_ID": flag(cmd, "--model-id", default=""),
         "HOST": flag(cmd, "--host", default="127.0.0.1"), "CONTEXT": flag(cmd, "--context-window", default=""),
         "PROFILE": flag(cmd, "--profile", default=""), "DEPTH": flag(cmd, "--depth", default=""),
         "KV_QUANT": flag(cmd, "--kv-quant", default="")}
    return {kk: v for kk, v in e.items() if v}

CLEAN_ENV = ("CTX", "SLOTS", "KV", "KV_K", "KV_V", "MODEL", "MODEL_NAME", "MODEL_ID", "ALIAS", "NET", "HOST", "TEMP", "TOP_P",
             "TOP_K", "MIN_P", "PRESENCE", "SPEC", "SPEC_N", "CACHE_RAM", "CONTEXT", "PROFILE", "DEPTH", "KV_QUANT")

def start_server(backend, preset, port, extra_env, console, mode="w"):
    env = dict(os.environ, MONITOR="0", PORT=str(port), **extra_env)
    for kk in CLEAN_ENV:
        if kk not in extra_env:
            env.pop(kk, None)                       # the settings file (or the rollback env) decides, not our environment
    env["SETTINGS_FILE"] = extra_env.get("SETTINGS_FILE", SETTINGS_FILE)
    env["SETTINGS_FILE_MTPLX"] = extra_env.get("SETTINGS_FILE_MTPLX", SETTINGS_FILE_MTPLX)
    cmd = ([os.path.join(REPO, "host", "serve.sh"), preset] if backend == "mtplx"
           else [os.path.join(REPO, "host", "serve-llama.sh")])
    out = open(console, mode)                       # the rollback appends: the failed start's output stays
    return subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)

def wait_up(proc, port, secs=900, host=None):
    t_end = time.time() + secs
    while time.time() < t_end:
        if proc.poll() is not None:
            return False
        for h in ([host] if host else []) + [VM_ADDR, "127.0.0.1"]:   # the new server may use another address
            try:                                        # MTPLX's /health needs the key; llama.cpp ignores it
                urllib.request.urlopen(urllib.request.Request(f"http://{h}:{port}/health",
                                       headers={"Authorization": f"Bearer {KEY}"} if KEY else {}), timeout=2)
                return True
            except Exception:
                pass
        time.sleep(2)
    return False

def stop_pid(pid):
    if not pid or not pid_alive(pid):
        return
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: return
    for _ in range(60):
        if not pid_alive(pid): return
        time.sleep(0.5)
    try: os.kill(pid, signal.SIGKILL)
    except ProcessLookupError: pass
    time.sleep(2)

def follow(port, pid, console):
    """Point the monitor at the server on port (a backend switch changes the port)."""
    global BASE
    if port != args.port:
        args.port = port
        BASE = f"http://{HOST}:{port}"
        S["pid"], S["pid_t"], S["props"], S["props_t"], S["last"] = pid, time.time(), {}, 0, None
    args.server_pid, S["pid"], args.console = pid, pid, console

def restart_worker(p):
    """Background thread: save, stop the old server, start the new one; roll back on failure."""
    be, old_be = p["backend"], D.get("backend") or "llama"
    path = SETTINGS_FILE_MTPLX if be == "mtplx" else SETTINGS_FILE
    old_text = open(path).read() if os.path.exists(path) else None
    old_cmd, old_pid, old_port = D.get("cmd", ""), D.get("target_pid") or D.get("pid"), args.port
    port = old_port if be == old_be else PORTS[be]
    console = os.path.expanduser(f"~/models/logs/.console-{port}.out")
    os.makedirs(os.path.dirname(console), exist_ok=True)
    try:
        write_settings(p)
        if port in (8080, 8000):                    # ./carl.sh (no arguments) starts it next time; not for test ports
            with open(os.path.join(CONF_DIR, "last-backend"), "w") as f:
                f.write((p.get("preset", "grant") if be == "mtplx" else "llama") + "\n")
        ui["restart"] = f"stopping the server (pid {old_pid})…"
        args.server_pid = None
        stop_pid(old_pid)
        ui["restart"] = f"starting {'MTPLX' if be == 'mtplx' else 'llama.cpp'} with the new settings (the model loads)…"
        proc = start_server(be, p.get("preset", "grant"), port, {}, console)
        follow(port, proc.pid, console)
        chosen = str(p.get("mnet" if be == "mtplx" else "net"))
        if wait_up(proc, port, host=chosen if chosen.count(".") == 3 else None):
            changed = [lbl for key, lbl, *_ in rows(p) if key in ("ctx", "slots", "mctx", "backend")
                       and str(p[key]) != str(ui["set_run"].get(key))]
            toast("restarted with the new settings" + (f"; {' and '.join(changed)} changed: run install.sh again on each client" if changed else ""), 12)
            ui["restart"] = None; ui["set"] = None
            return
        tail = open(console, errors="replace").read().strip().splitlines()[-3:]
        stop_pid(proc.pid)
        if old_text is None:
            os.remove(path)
        else:
            open(path, "w").write(old_text)
        back = False
        if old_pid and ("llama-server" in old_cmd or "mtplx" in old_cmd):
            ui["restart"] = "the new settings failed: starting the old server again…"
            old_console = os.path.expanduser(f"~/models/logs/.console-{old_port}.out")
            if "llama-server" in old_cmd:
                proc = start_server("llama", "", old_port, env_from_cmd(old_cmd), old_console, "a")
            else:
                proc = start_server("mtplx", "pocket" if "pocket" in old_cmd.lower() else "grant", old_port,
                                    env_from_mtplx(old_cmd), old_console, "a")
            follow(old_port, proc.pid, old_console)
            back = wait_up(proc, old_port)
        toast(f"{RED}new settings failed{R}: " + ("the old server runs again" if back else "no server runs now")
              + f" · {' | '.join(tail)[-150:]}", 20)
    except Exception as e:
        toast(f"{RED}restart failed: {e}{R}", 20)
    ui["restart"] = None

def body_settings(d, cols, height):
    if ui.get("set") is None:
        ui["set"], ui["set_run"] = pending_init(d), running_settings(d)
    p, run = ui["set"], running_settings(d) or {}
    rws = rows(p)
    ui["set_row"] = min(ui["set_row"], len(rws) - 1)
    w = cols - 1
    L = [f"{DIM}{'':2}{'setting':<14}{'new':<26}{'running now':<18}{R}"]
    same_backend = run.get("backend") == p["backend"]
    for i, (key, label, choices, env, default) in enumerate(rws):
        sel = i == ui["set_row"]
        val = ctx_label(p[key]) if key in ("ctx", "mctx") else {"llama": "llama.cpp", "mtplx": "MTPLX"}.get(p[key], str(p[key]))
        if sel and ui.get("edit") is not None:
            val = ui["edit"] + "▏"
        rv = run.get(key, "N/A") if (same_backend or key == "backend") else "N/A"
        rv = "" if key == "adv" else rv
        rv = ctx_label(rv) if key in ("ctx", "mctx") else ("auto" if key == "cache" and rv == 0 else
                                                          {"llama": "llama.cpp", "mtplx": "MTPLX"}.get(rv, str(rv)))
        mark = (f"{YEL}*{R}" if run and (same_backend or key == "backend") and str(p[key]) != str(run.get(key))
                and key not in ("slots", "cache", "net", "mnet", "adv", "specn", "sched", "batching", "pchunk") else " ")
        pre = f"{CYN}{B}›{R} " if sel else "  "
        text = f"{pre}{(B if sel else '')}{label:<14}{R}"
        spans = [(0, 2 + 14, f"setrow:{i}")]
        col = vlen(text)
        for lab, act in (("<", f"setdec:{i}"), (f"{val:^18}", f"setrow:{i}"), (">", f"setinc:{i}")):
            b = f"[{lab}]" if lab in "<>" else lab
            spans.append((col, col + len(b), act)); text += f"{CYN}{b}{R}"; col += len(b)
        text += f" {mark}  {DIM}{rv}{R}"
        L.append(Ln(text, spans=spans))
    L += [""] * max(len(LLAMA_ROWS) + 2 - len(rws), 0)  # the same height for both backends
    key = rws[ui["set_row"]][0]
    adv_on = p.get("adv") == "shown"
    L += ["", f"{DIM}{SET_HELP[key]}{R}" + (f"  {CYN}(type a value, Enter){R}" if key in NUMERIC else ""),
          f"{YEL}{ADV_WARN[0]}{R}" if adv_on else "", f"{YEL}{ADV_WARN[1]}{R}" if adv_on else ""]
    ok, fl = fit_cached(p)
    path = SETTINGS_FILE_MTPLX if p["backend"] == "mtplx" else SETTINGS_FILE
    reader = "./carl.sh grant|pocket" if p["backend"] == "mtplx" else "./carl.sh llama"
    L.append(lv("fit", fl, 6))
    L.append(lv("file", path.replace(os.path.expanduser("~"), "~") + f"{DIM} (read by {reader}; flags still win){R}", 6))
    L.append("")
    if ui.get("restart"):
        L.append(f"{YEL}{ui['restart']}{R}")
    else:
        L.append(buttons("", [("Apply and restart (a)" if run else "Start server (a)", "setapply" if ok else "setnofit"), ("Revert (r)", "setrevert"),
                              ("Defaults (x)", "setdefaults")]))
    L.append(f"{DIM}↑↓ select · ←→ change · * differs from the running server · a change of backend, context or slots needs install.sh again{R}")
    srv = {"llama": "llama.cpp", "mtplx": "MTPLX"}.get(run.get("backend"), "no server")
    rows_ = draw_card("settings", "SERVER SETTINGS", f"{DIM}running: {srv} · port {args.port}{R}", L, w, lvl=2)
    if ui.get("confirm"):
        switch = run.get("backend") and run.get("backend") != p["backend"]
        new = "MTPLX" if p["backend"] == "mtplx" else "llama.cpp"
        first = ("This stops the server and starts it again with the new settings." if run and not switch else
                 f"This stops {srv} and starts {new} on port {PORTS[p['backend']]}." if run else
                 f"This starts {new} on port {PORTS[p['backend']] if p['backend'] != 'llama' else args.port}.")
        rows_ += draw_card("confirm", "START?" if not run else "RESTART?", "", [
            "", first,
            "Requests in progress stop. The model loads again (about 30 s to 2 min)." if run else "The model loads (about 30 s to 2 min).",
            "If the new server does not start, the old one starts again." if run else "", "",
            buttons("  ", [("Yes, restart (y)" if run else "Yes, start (y)", "setyes"), ("Cancel (n)", "setno")])], min(w, 80), lvl=2)
    return rows_[:height]

INT_KEYS = {"ctx", "mctx", "cache", "top_k", "specn", "ub", "ckpt", "ckstep", "pchunk"}

def commit_edit(key):
    """Settings: store a typed value (Enter) if it is a valid number for that row."""
    v, ui["edit"] = (ui.get("edit") or "").strip().lower(), None
    try:
        num = float(v[:-1]) * 1024 if v.endswith("k") else float(v)
    except ValueError:
        toast(f"not a number: {v!r}", 5); return
    if num < 0 or (key in ("ctx", "mctx") and not 4096 <= num <= 262144) or (key in ("top_p", "min_p") and num > 1):
        toast(f"{v} is out of range for {key}", 5); return
    ui["set"][key] = int(num) if key in INT_KEYS else (f"{num:g}" if num != int(num) else f"{num:.1f}" if key in ("temp", "repeat") else f"{int(num)}")

def settings_action(act):
    if ui.get("set") is None:
        return
    rws = rows(ui["set"])
    if act.startswith("setrow:"):
        ui["set_row"] = min(int(act[7:]), len(rws) - 1)
    elif act.startswith(("setinc:", "setdec:")):
        i = min(int(act[7:]), len(rws) - 1); ui["set_row"] = i
        key, _, choices, *_ = rws[i]
        cur = ui["set"][key]
        j = next((n for n, c in enumerate(choices) if str(c) == str(cur)), 0)
        ui["set"][key] = choices[(j + (1 if act.startswith("setinc") else -1)) % len(choices)]
    elif act == "setrevert":
        ui["set"] = None
    elif act == "setdefaults":
        be = ui["set"]["backend"]
        adv = ui["set"].get("adv", "hidden")
        ui["set"] = {key: default for key, _, _, _, default in [BACKEND_ROW] + LLAMA_ROWS + MTPLX_ROWS + LLAMA_ADV + MTPLX_ADV}
        ui["set"].update(backend=be, adv=adv)
    elif act == "setnofit":
        toast("this setup does not fit or is not downloaded: see the fit line", 6)
    elif act == "setapply" and not ui.get("restart"):
        if D.get("cmd") and "llama-server" not in D["cmd"] and "mtplx" not in D["cmd"]:
            toast("another server (not llama.cpp or MTPLX) uses this port: stop it first", 8); return
        ui["confirm"] = True
    elif act == "setno":
        ui["confirm"] = False
    elif act == "setyes":
        ui["confirm"] = False
        ui["restart"] = "saving the settings…"
        threading.Thread(target=restart_worker, args=(dict(ui["set"]),), daemon=True).start()


def quit_dialog(d, cols, height):
    pid = d.get("target_pid")
    alive = pid and not d.get("exited") and not ui["stopping"]
    w = min(70, cols - 4)
    lines = [""]
    if alive:
        lines += [f"The server (pid {pid}) is still running.", "",
                  buttons("  ", [("Stop server (s)", "stop"), ("Leave it running (d)", "detach"), ("Cancel (Esc)", "cancel")]),
                  "", f"{DIM}Leave it running: it keeps serving; re-attach with ./carl.sh monitor{R}"]
    else:
        lines += ["The server isn't running.", "", buttons("  ", [("Quit (q)", "detach"), ("Cancel (Esc)", "cancel")])]
    card = draw_card("quitbox", "QUIT", "", lines, w)
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows = [("", [])] * top
    rows += [(" " * pad + t, [(pad + a, pad + b, act) for a, b, act in sp]) for t, sp in card]
    return rows[:height]

def frame(d):
    cols, rows_ = shutil.get_terminal_size((120, 36))
    regions.clear()
    label, bg, _ = status_of(d)
    alias = (d.get("props") or {}).get("model_alias") or (d.get("mx") or {}).get("model_id") or ""
    up = f" · up {d['etime']}" if d.get("etime") else ""
    quit_lbl = "[ Quit ]"
    backend = {"mtplx": "MTPLX"}.get(d.get("backend"), "llama.cpp") if d.get("up") else ""
    left = f"{'' if LOGO else '😎 '}{B}CARL{R}  {pill(label, bg)}  {B}{alias}{R}{DIM}{(' · ' + backend) if backend else ''}{up}{R}"
    right = f"{DIM}{time.strftime('%H:%M:%S')}{R}  " + ("" if args.once else f"{B}{RED}{quit_lbl}{R}")
    head = fit(left, cols - LOGO_W - vlen(right) - 1) + " " + right
    regions.append((1, cols - len(quit_lbl) + 1, cols + 1, "quit"))
    # tabs
    tabs, x = " ", 2 + LOGO_W
    for i, t in enumerate(TABS):
        lab = f" {i + 1} {t} "
        tabs += (f"\x1b[1;7m{lab}{R}" if i == ui["tab"] else f"{DIM}{lab}{R}") + " "
        regions.append((2, x, x + len(lab), f"tab:{i}"))
        x += len(lab) + 1
    out = [head, fit(tabs, cols - LOGO_W), fit(DIM + "─" * cols, cols)]
    height = (rows_ - 5) if not args.once else 10**4
    if ui["quit"]:
        body = quit_dialog(d, cols, height)
    else:
        body = [body_overview, body_connect, body_requests, body_log, body_settings][ui["tab"]](d, cols, height)
    for text, spans in body:
        y = len(out) + 1
        regions.extend((y, a + 1, b + 1, act) for a, b, act in spans)
        out.append(fit(text, cols))
    if not args.once:
        while len(out) < rows_ - 2:
            out.append(" " * cols)
        msg, until = ui["toast"]
        if ui["stopping"]:
            foot = f"{YEL}stopping the server (pid {ui['stopping'][0]})…{R}"
        elif ui["restart"]:
            foot = f"{YEL}{ui['restart']}{R}"
        elif msg and time.time() < until:
            foot = f"{GRN}{msg}{R}"
        elif ui["help"]:
            foot = (f"{DIM}1-5/Tab tabs · click a card title: more/less detail · e/c expand/collapse all · k key · o/p/t copy configs · "
                    f"w wrap · f errors only · ↑↓ PgUp PgDn scroll · space refresh · q quit · ? hide{R}")
        else:
            foot = f"{DIM}click a card title for detail · 1-5 tabs · o/p/t copy configs · k key · q quit · ? all keys{R}"
        out += [fit(DIM + "─" * cols, cols), fit(" " + foot, cols)]
    if DEMO:                                       # screenshots: no key characters, no home path
        home = os.path.expanduser("~")
        out = [l.replace(home, "~").replace(KEY[-4:] if KEY else "\0", "••••") for l in out]
    return out

# ===================================================================== actions
D = {}   # latest collected data

def preview_text(kind, d, mask=False):
    """Config text for the running server. mask=True hides the key (on-screen preview)."""
    if not d or not d.get("up"):
        return "(the server isn't reachable yet: the config appears once the model has loaded)"
    if kind == "opencode":
        text = json.dumps(opencode_config(d), indent=2, ensure_ascii=False)
    elif kind == "pi":
        text = json.dumps(pi_config(d), indent=2, ensure_ascii=False)
    else:
        text = curl_test(d)
    return text.replace(KEY, "•" * 16 + KEY[-4:] + "  (k shows it; the copy has the real key)") if mask and KEY else text

def show_config(kind):
    ui["preview"], ui["prev_scroll"], ui["tab"] = kind, 0, 1
    if not D.get("up"):
        toast("server not reachable yet: nothing copied"); return
    ok = copy(preview_text(kind, D))
    ui["copied"] = kind if ok else None
    toast({"opencode": "OpenCode config", "pi": "Pi config", "curl": "curl test"}[kind]
          + (" copied to the clipboard" if ok else ": clipboard unavailable, select it on screen"))

def do(action):
    if action.startswith("set"):
        settings_action(action)
    elif action.startswith("level:"):
        nm = action[6:]
        if nm in level:
            level[nm] = (level[nm] + 1) % 3
    elif action.startswith("tab:"):
        ui["tab"] = int(action[4:])
    elif action == "quit":
        ui["quit"] = True
    elif action == "cancel":
        ui["quit"] = False
    elif action == "key":
        ui["key_shown"] = not ui["key_shown"]
    elif action in ("opencode", "pi", "curl"):
        show_config(action)
    elif action == "wrap":
        ui["wrap"] = not ui["wrap"]
    elif action == "errors":
        ui["errors_only"] = not ui["errors_only"]; ui["log_scroll"] = 0
    elif action == "follow":
        ui["log_scroll"] = 0
    elif action == "detach":
        pid = D.get("target_pid")
        if pid and not D.get("exited"):
            ui["exit_msg"] = (f"Monitor closed; the server is still running (pid {pid}) at {BASE}.\n"
                              f"  re-attach: ./carl.sh monitor --port {args.port}\n  stop:      kill {pid}")
        raise SystemExit
    elif action == "stop":
        pid = D.get("target_pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            ui["stopping"] = (pid, time.time() + 30)
        ui["quit"] = False

def scroll(step):
    if ui["tab"] == 4:
        n = len(rows(ui["set"])) if ui["set"] else len(SETTINGS)
        ui["set_row"] = (ui["set_row"] - step) % n if abs(step) == 1 else ui["set_row"]; return
    key = {0: "scroll", 1: "prev_scroll", 2: "req_scroll", 3: "log_scroll"}[ui["tab"]]
    if key == "log_scroll":
        ui[key] = max(0, min(ui[key] + step, max(len(LOG.lines) - 5, 0)))
    else:
        ui[key] = max(0, ui[key] - step)

def handle_click(btn, x, y):
    if btn in (64, 65):                                   # wheel: up = back in the log, up in lists
        scroll(3 if btn == 64 else -3); return
    if btn != 0:
        return                                            # left button only
    for ry, x0, x1, act in regions:
        if ry == y and x0 <= x < x1:
            if ui["quit"] and act not in ("stop", "detach", "cancel", "quit"):
                return
            if ui["confirm"] and act not in ("setyes", "setno"):
                return
            do(act); return

def handle_input(data):
    for mm in re.finditer(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])", data):
        if mm.group(4) == "M":
            handle_click(int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
    rest = re.sub(r"\x1b\[<\d+;\d+;\d+[Mm]", "", data)
    if ui["confirm"] and not ui["quit"]:
        if rest == "\x1b": do("setno")
        for ch in rest:
            if ch in "yY": do("setyes")
            elif ch in "nN": do("setno")
        return
    if ui["tab"] == 4 and ui["set"] is not None and not ui["quit"]:
        key = rows(ui["set"])[ui["set_row"]][0]
        if ui.get("edit") is not None:               # typing a value: digits . k, Backspace, Enter, Esc
            for ch in rest:
                if ch in "0123456789.kK": ui["edit"] += ch.lower()
                elif ch in "\x7f\x08": ui["edit"] = ui["edit"][:-1]
                elif ch in "\r\n": commit_edit(key); break
                elif ch == "\x1b": ui["edit"] = None; break
            return
        if rest in ("\r", "\n") and key in NUMERIC:
            ui["edit"] = ""; return
    if ui["quit"]:
        if rest == "\x1b": do("cancel")
        for ch in rest:
            if ch in "sS": do("stop")
            elif ch in "dDqQ": do("detach")
            elif ch in "nNcC": do("cancel")
        return
    seqs = {"\x1b[A": 1, "\x1b[B": -1, "\x1b[5~": 10, "\x1b[6~": -10}
    for seq, step in seqs.items():
        if seq in rest:
            scroll(step)
    if ui["tab"] == 4 and ui["set"] is not None:
        if "\x1b[C" in rest: do(f"setinc:{ui['set_row']}")
        if "\x1b[D" in rest: do(f"setdec:{ui['set_row']}")
    if "\x1b[F" in rest or "\x1b[4~" in rest:
        ui["log_scroll"] = 0
    rest = re.sub(r"\x1b\[[0-9;]*[A-Za-z~]", "", rest)
    for ch in rest:
        if ch in "qQ\x03": do("quit")
        elif ch in "12345": ui["tab"] = int(ch) - 1
        elif ui["tab"] == 4 and ch in "arx": do({"a": "setapply", "r": "setrevert", "x": "setdefaults"}[ch])
        elif ch == "\t": ui["tab"] = (ui["tab"] + 1) % len(TABS)
        elif ch == "k": do("key")
        elif ch == "o": do("opencode")
        elif ch == "p": do("pi")
        elif ch == "t": do("curl")
        elif ch == "e": level.update({x: 2 for x in level})
        elif ch == "c": level.update({x: 0 for x in level})
        elif ch == "w": do("wrap")
        elif ch == "f": do("errors")
        elif ch in "+=": ui["lines"] = min(ui["lines"] + 2, 60)
        elif ch in "-_": ui["lines"] = max(ui["lines"] - 2, 2)
        elif ch == "?": ui["help"] = not ui["help"]
        elif ch == " ": return "refresh"

# ===================================================================== main
def main():
    global D
    threading.Thread(target=slow_loop, daemon=True).start()
    threading.Thread(target=lambda: S.__setitem__("gpu_limit", gpu_limit()), daemon=True).start()
    if args.once:
        D = collect(); time.sleep(0.5)
        if args.tab:
            ui["tab"] = args.tab - 1
        print("\n".join(frame(D)))
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    wake_r, wake_w = os.pipe()                 # Ctrl-C wakes the loop and opens the quit dialog
    def restore():
        sys.stdout.write("\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l"); sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    def on_int(*_):
        ui["quit"] = True
        os.write(wake_w, b"x")
    signal.signal(signal.SIGINT, on_int)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (restore(), os._exit(0)))
    tty.setcbreak(fd)
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")   # alt screen, hide cursor, mouse (SGR)
    try:
        next_fetch = 0
        logo_at = [None, 0.0]
        while True:
            if time.time() >= next_fetch:
                D = collect(); next_fetch = time.time() + (0.5 if ui["stopping"] else args.interval)
            if ui["stopping"]:
                pid, deadline = ui["stopping"]
                if not pid_alive(pid):
                    ui["exit_msg"] = f"Server (pid {pid}) stopped."
                    break
                if time.time() > deadline:
                    try: os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError: pass
            size = shutil.get_terminal_size((120, 36))
            if LOGO and (size != logo_at[0] or time.time() - logo_at[1] > 30):
                sys.stdout.write("\x1b[2J" if size != logo_at[0] else "")
                sys.stdout.write(logo_escape()); logo_at[:] = [size, time.time()]
            # each line at its own position: the two header lines start right of the logo
            sys.stdout.write("".join(f"\x1b[{i + 1};{(LOGO_W if i < 2 else 0) + 1}H{l}\x1b[K"
                                     for i, l in enumerate(frame(D))) + "\x1b[J")
            sys.stdout.flush()
            r, _, _ = select.select([fd, wake_r], [], [], max(min(next_fetch - time.time(), 1.0), 0.05))
            if wake_r in r:
                os.read(wake_r, 64)
            if fd in r and handle_input(os.read(fd, 4096).decode(errors="replace")) == "refresh":
                next_fetch = 0
    except SystemExit:
        pass
    finally:
        restore()
        if ui["exit_msg"]:
            print(ui["exit_msg"])

main()

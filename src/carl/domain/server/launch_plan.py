"""The launch plan: the KEY=value lines the launcher reads (the facade's launch_env), and what a start makes of
them (W3.1): the options, the order of the sources, the defaults, the speculation and the drafter, the memory plan's
answer. Pure: the caller does the I/O (it reads launch-env, asks the OS which files and addresses exist, runs the
memory plan and check) and hands the answers in. argv.py builds llama-server's command line from the plan,
start_text.py its start lines, network.py its address.

The shell scripts read the lines with `read`/`printf -v`, never eval, and every value is
also restricted to a safe character set, so a config value can't inject shell syntax.

The order of the sources (one table, derived from the settings schema):
    the flags > the environment > config.json > Auto-tune > the catalogue > the defaults
- config.json, Auto-tune and the catalogue reach the start as launch-env's lines; a value set in the environment
  wins over a line, except for the forced keys (FORCED: the model, its drafter and the sources);
- KV_K, KV_V, NGRAM_* and LLAMA_DEVICES (the devices to use, llama.cpp's names: the rollback sets it) come from the
  environment only (ENV_ONLY);
- --local and --vm clear a saved address (HOST) unless --host is given too;
- an empty variable counts as unset, everywhere;
- Windows sets TEMP to its temporary folder: there TEMP counts only when it is a number (numeric_temp). On every OS a
  sampling variable that is not a number inside its range is left out with a warning (llama-server would refuse it).

The start in Python (application/start_server.py) starts llama-server from this plan, with the environment of
server_environment(); the bash launcher that forwarded to it is deleted (W4.2). The plan is the bash launcher's of
1.20.10 in every scenario, byte for byte, held by the CLI goldens. Contract C-119.

The context memory type of a start on the CPU alone (A71, domain/memory/cpu_kv.py): when nobody chose it (not a flag, the
environment, config.json, nor an Auto-tune record that measured the types: kv_open), the memory plan is asked for
the type (--kv-auto) and answers `kv f16` (or q8_0, q4_0) and the line why; it is f16 when the free RAM covers the
start. A start on a GPU backend or a Mac gets no such answer and keeps its type.

Rules of a backend (W6.5): the start's backend is the one of its devices (llama.cpp's names: CUDA0; the memory
plan's devices line, else LLAMA_DEVICES); a start on no device (a Mac, the CPU) has none, and these rules do not apply.
  - CUDA refuses different K and V cache types (KV_K, KV_V): its flash attention takes only matching types, and a
    mix runs it on the CPU. The start says why. FIT_CHECK=0 does not lift it.
  - On Windows with CUDA, FIT_CHECK=0 gets a warning: NVIDIA's driver may move a model that does not fit into system
    memory (Prefer sysmem fallback), and the model then crawls instead of failing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from ..hardware.cpu_topology import CpuTopology, ThreadPin, pin_for, typed_options
from ..hardware.gpu import CUDA_ORDER
from ..memory.autofit import RAM_MTP_NOTE
from ..memory import dry_run
from ..memory import server_fit as sf
from ..memory.calibration import backend_of
from . import runtime_doc as rd
from ..shared.constants import DEFAULT_PORT, NGRAM, UBATCH, VM_ADDR
from ..shared.contracts import require
from ..shared.errors import ConfigError
from ..shared.paths import policy_for
from ..shared.platform_words import PlatformWords
from ..catalog.drafts import FILE_SPECS, KIND_OF_SPEC, KIND_WORDS
from ..catalog.inventory import MtpSource, spec_drafter, vision_ready
from ..catalog.layout import TEMPLATE, model_folder_of
from ..config.settings import CACHE_KEYS, LLAMA_KEYS, MODEL_KEYS, Config, SettingSpec, coerce
from ..shared.types import ModelInfo, SettingSource, SettingValue, Settings
from .network import NetworkRefused, resolve_host
from .ub_order import FIXED, KEEP, TUNED, UbAsk, UbOrder
from .thinking_template import SUFFIX as TEMPLATE_SUFFIX

_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.,:/~+@ =-]*")
_SAFE_KEY = re.compile(r"[A-Z_][A-Z0-9_]*")
SOURCE_KEYS = ("ctx", "kv", "spec", "slots")       # reported in CARL_SOURCES


def shell_value(v: SettingValue) -> str:
    return "1" if v is True else "0" if v is False else str(v)


def shell_lines(env: Mapping[str, SettingValue]) -> str:
    """KEY=value lines; raises ConfigError on a key or value outside the safe character set."""
    out = []
    for k, v in env.items():
        s = shell_value(v)
        if not _SAFE_KEY.fullmatch(k):
            raise ConfigError(f"{k!r}: not a variable name")
        if not _SAFE_VALUE.fullmatch(s):
            raise ConfigError(f"{k}: unsafe characters in {s!r}")
        out.append(f"{k}={s}")
    return "\n".join(out)


def process_lines(env: Mapping[str, SettingValue]) -> str:
    """KEY=value lines for the start in this process on Windows (W6.4), where no shell reads them: a Windows path (a
    drive's colon, backslashes, letters outside ASCII) goes as it is. A value with a line break would split its line,
    and raises ConfigError; so does a key outside the safe set."""
    out = []
    for k, v in env.items():
        s = shell_value(v)
        if not _SAFE_KEY.fullmatch(k):
            raise ConfigError(f"{k!r}: not a variable name")
        if any(c in s for c in "\r\n\0"):
            raise ConfigError(f"{k}: a line break in {s!r}")
        out.append(f"{k}={s}")
    return "\n".join(out)


def launch_env(m: ModelInfo, vals: Settings, src: Mapping[str, SettingSource], cfg: Config,
               swa: bool = False, mtp: Optional[MtpSource] = None, os_name: str = "macos",
               ub: Optional[UbOrder] = None, stamp: str = "", pin: str = "") -> Dict[str, SettingValue]:
    """Settings for serve-llama.sh: the model, its effective tune and the server-wide
    config values that are set (the script applies flags and environment on top). swa: the
    model has sliding-window layers: SWA_MODE (cache.swa: auto, full, window; llama-fit --plan
    decides auto, and the launcher adds --swa-full for full). mtp: where its MTP speculation
    comes from (None: unknown): MTP_SOURCE, and DRAFT = the drafter's file when that is the
    source (the launcher passes -md DRAFT when SPEC uses draft-mtp, also a SPEC from the
    environment). os_name: the profile's OS: a server-wide key gives a line only on its platforms
    (SettingSpec.platforms), and only when the start reads its variable (ALLOWED); a model's own models.NAME.n_cpu_moe
    (W1.6) is N_CPU_MOE in place of llama.n_cpu_moe's, and its auto drops that line. ub: the order's -ub (ub_order.py,
    A51): a person's (a -ub typed in the server options, the model's saved choice, llama.ub for a model without a tune
    record) is UB, as it is; a typed one is still in EXTRA_ARGS, which the server gets on its command line. The model's
    own (its Auto-tune record, else the OS's default) is UB_AUTO, which the memory plan may lower to keep the setup
    (A45), with UB_FROM=tuned when it is the record's (without a record the plan picks the size, A52); the default 512
    gives no line; a config value that differs from the record is UB_ASK (and the record's UB_STAMP), which a start that
    can ask asks about. stamp: the model's tune record's (inventory.tune_stamp). pin: the tune record's verdict on the
    threads with experts in the RAM (inventory.tuned_pin, A57): pinned gives THREAD_PIN=on, unpinned THREAD_PIN=off, ""
    (nothing measured) no line, and a start with no line runs unpinned."""
    env: Dict[str, SettingValue] = {"MODEL": m.get("path", ""), "MODEL_NAME": m.get("name", ""), str(MODEL_KEYS["alias"].env): vals["alias"]}
    if mtp is not None:
        env["MTP_SOURCE"] = mtp
    if mtp == "drafter":
        env["DRAFT"] = m.get("draft_path", "")
    for var, spec_type in DRAFT_VARS.items():      # a drafter file of another kind, for the SPEC that names it
        d = spec_drafter(m, spec_type)
        if d is not None:
            env[var] = d.get("path", "")
    if vision_ready(m):
        env[MMPROJ] = m.get("vision_path", "")
    for k, s in MODEL_KEYS.items():
        if k != "alias" and s.env:
            env[s.env] = vals[k]
    for k, s in LLAMA_KEYS.items():
        v = cfg.llama.get(k)
        if v is not None and s.env in ALLOWED and v != "" and not (k in AUTO_KEYS and v == "auto") \
                and s.on(os_name) and not (k == "ub" and ub is not None):
            env[s.env] = v
    own = MODEL_KEYS["n_cpu_moe"]
    if src.get("n_cpu_moe", "default") != "default" and own.on(os_name):    # the model's own count wins (W1.6, B2)
        if vals.get("n_cpu_moe", AUTO) == AUTO:
            env.pop(N_CPU_MOE, None)
        else:
            env[N_CPU_MOE] = vals["n_cpu_moe"]
    if ub is not None and ub.source in FIXED:
        if ub.ub:
            env[UB] = ub.ub
    elif ub is not None:
        if ub.ub != UBATCH:
            env[UB_AUTO] = ub.ub
        if ub.source == TUNED:
            env[UB_FROM] = ub.source
        if ub.ask:
            env[UB_ASK], env[UB_STAMP] = ub.ask.to_var(), ub.ask.stamp or stamp
    if pin in (PINNED, UNPINNED):
        env[THREAD_PIN] = PIN_ON if pin == PINNED else PIN_OFF
    extra = cfg.llama.get("extra_args")
    if isinstance(extra, list) and extra:
        env["EXTRA_ARGS"] = " ".join(extra)
    if swa:
        env[str(CACHE_KEYS["swa"].env)] = cfg.cache.get("swa") or "auto"
    env["CARL_SOURCES"] = " ".join(f"{k}:{src[k]}" for k in SOURCE_KEYS)
    return env


# ---------------------------------------------------------------- the launcher's variables
def _env(keys: Mapping[str, SettingSpec], key: str) -> str:
    name = keys[key].env
    require(name, "C-119", f"the setting {key} has a launcher variable")
    return str(name)


# The settings schema's variables (each SettingSpec.env: the one source of their names, rule R14)
KV, CTX, SLOTS, SPEC, SPEC_N, ALIAS = (_env(MODEL_KEYS, k) for k in ("kv", "ctx", "slots", "spec", "spec_n", "alias"))
SAMPLING = ("temp", "top_p", "top_k", "min_p", "presence", "repeat")
TEMP, TOP_P, TOP_K, MIN_P, PRESENCE, REPEAT = (_env(MODEL_KEYS, k) for k in SAMPLING)
BATCHES = ("batch", "ckpt", "ckpt_step")
LLAMA_MODE, NET, HOST, CACHE_RAM, UB, BATCH, CKPT, CKPT_STEP, THINK_TOGGLE, VISION = (_env(LLAMA_KEYS, k) for k in (
    "mode", "net", "host", "cache_ram", "ub", *BATCHES, "think_toggle", "vision"))
LOAD_RESERVE = _env(LLAMA_KEYS, "load_reserve_gib")  # the GiB the free RAM keeps beyond load mode none's need
SWA_MODE = _env(CACHE_KEYS, "swa")
N_CPU_MOE = _env(LLAMA_KEYS, "n_cpu_moe")           # the layers whose MoE experts stay in the RAM (auto: the plan's)
AUTO_KEYS = ("cache_ram", "n_cpu_moe", "ub")        # auto gives no line: the start works the value out itself
# Every OS's settings, and the keys of some platforms only that a start reads: llama.n_cpu_moe (W6.7; launch-env gives
# its line only on its platforms). llama.path is read by the composition (W6.2), not the start.
SCHEMA_VARS = tuple(str(s.env) for keys in (MODEL_KEYS, LLAMA_KEYS, CACHE_KEYS) for s in keys.values()
                    if s.env and (not s.platforms or s.env == N_CPU_MOE))
# The launch's own variables: what launch-env adds, and what only the environment sets
MODEL, MODEL_NAME, CARL_SOURCES, DRAFT, MTP_SOURCE, EXTRA_ARGS = (
    "MODEL", "MODEL_NAME", "CARL_SOURCES", "DRAFT", "MTP_SOURCE", "EXTRA_ARGS")
# The drafter files of the other kinds (DFlash, DSpark, EAGLE-3: DRAFT_DFLASH ...; WCATX) and the vision projector
# (MMPROJ), named by launch-env when they are downloaded; the start passes them with -md and --mmproj
DRAFT_VARS = {f"DRAFT_{KIND_OF_SPEC[s].upper()}": s for s in FILE_SPECS}
MMPROJ = "MMPROJ"
UB_AUTO = "UB_AUTO"                                 # the model's -ub (Auto-tune, else the OS's), when UB sets none
UB_FROM = "UB_FROM"                                 # tuned: UB_AUTO is the model's Auto-tune record (no line: default)
UB_ASK = "UB_ASK"                                   # a config -ub against the model's tuned one (UbAsk.to_var)
UB_STAMP = "UB_STAMP"                               # the tune record the question is about (inventory.tune_stamp)
PORT, API_KEY_FILE, VM_HOST, LOG_DIR, LOG_FILE, FIT_CHECK, RESERVE_GB = (
    "PORT", "API_KEY_FILE", "VM_HOST", "LOG_DIR", "LOG_FILE", "FIT_CHECK", "RESERVE_GB")
KV_K, KV_V, NGRAM_MATCH, NGRAM_MIN, NGRAM_MAX = "KV_K", "KV_V", "NGRAM_MATCH", "NGRAM_MIN", "NGRAM_MAX"
LLAMA_DEVICES = "LLAMA_DEVICES"                     # the devices to plan the split over, llama.cpp's names (CUDA0,CUDA1)
THREAD_PIN = "THREAD_PIN"                           # on: pinned (the tune record says it won, A57); off: not
PIN_OFF, PIN_ON = "off", "on"
PINNED, UNPINNED = "pinned", "unpinned"             # the tune record's verdict (tune.pin)

# The precedence table. FORCED: launch-env's value always wins (the model it resolved, with the flag and the
# environment already in it). ALLOWED: the lines a start reads; any other line is ignored with a warning.
# ENV_ONLY: never a line (the per-cache KV types, n-gram's own settings). RESET: what only launch-env says (the
# launcher empties them before it reads the lines, so a DRAFT in the environment never names a drafter).
FORCED = (MODEL, MODEL_NAME, CARL_SOURCES, DRAFT, MTP_SOURCE, UB_AUTO, UB_FROM, UB_ASK, UB_STAMP, MMPROJ,
          *DRAFT_VARS)
RESET = (CARL_SOURCES, DRAFT, MTP_SOURCE, UB_AUTO, UB_FROM, UB_ASK, UB_STAMP, MMPROJ, *DRAFT_VARS)
ALLOWED: FrozenSet[str] = frozenset(FORCED + SCHEMA_VARS + (EXTRA_ARGS, THREAD_PIN))
ENV_ONLY = (KV_K, KV_V, NGRAM_MATCH, NGRAM_MIN, NGRAM_MAX, LLAMA_DEVICES)
NGRAM_VARS = ((NGRAM_MATCH, "match"), (NGRAM_MIN, "min"), (NGRAM_MAX, "max"))
# The settings the start lines report with their source: (the CARL_SOURCES key, its variable)
REPORTED = (("ctx", CTX), ("kv", KV), ("spec", SPEC), ("slots", SLOTS))

AUTO = "auto"
SPEC_NONE, SPEC_MTP, SPEC_NGRAM = MODEL_KEYS["spec"].choices[:3]       # none, draft-mtp, ngram-mod
MAX_SLOTS = max(int(c) for c in MODEL_KEYS["slots"].choices if c.isdigit())
NO_LOG = "none"                                     # LOG_FILE=none: no log file
NO_SOURCE = "none"                                  # MTP_SOURCE=none: no MTP head and no drafter
CACHE_RAM_FALLBACK = "4096"                         # MiB, when the memory plan cannot run
SWA_UNSET = "-"                                     # no sliding-window layers (or no plan)
PLAN_LINE = re.compile(r"([1-9]) ([0-9]+) (full|window|-) ([a-z,-]+)")   # llama-fit.py --plan's answer
DEVICES_LINE = re.compile(r"devices ([A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*)(?: ([0-9]+(?:,[0-9]+)*))?")  # its 2nd line
CPU_MOE_LINE = re.compile(r"cpu-moe ([0-9]{1,3})")  # then the MoE experts in the RAM (W6.7)
UB_LINE = re.compile(r"ub ([0-9]{2,4})")            # the -ub the plan keeps (A45; only with --ub-auto)
ROOM_LINE = re.compile(r"room ([0-9]{1,15})")       # the bytes a model could use on the GPUs it planned on (W1.6)
LOAD_LINE = re.compile(r"load (none|mapped)")       # the load mode (A44, A45; only when the free RAM is known)
MTP_RAM_LINE = re.compile(r"mtp ram")               # MTP is off because experts are in the RAM (A51)
KV_LINE = re.compile(r"kv (f16|q8_0|q4_0)")         # a start on the CPU alone: the type the memory plan chose (A71)
KV_NOTE_WORDS = "Context memory type: "             # ...and its line why starts so
KV_OPEN = ("default", "catalogue", "header")        # CARL_SOURCES' kv when nobody chose the type
LOAD_WORDS = "Load: "                               # the plan's line about the load mode starts so
UB_FLAGS = ("-ub", "--ubatch-size")                 # llama-server's options for -ub (the user's own win)
LOAD_FLAGS = ("--load-mode", "--mmap", "--no-mmap")   # and for the load (the user's own win)
REFUSED = 3                                         # llama-fit.py --check: the start does not fit
_WHOLE = re.compile(r"0|[1-9][0-9]{0,8}")
_PORT = re.compile(r"[0-9]{1,5}")
_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")
_CTX_K = re.compile(r"([0-9]{1,6})k")
_CTX_N = re.compile(r"[0-9]{1,9}")
_SLOTS = re.compile(r"[1-4]|auto")
HELP = ("-h", "--help")
KV_WORDS = {"q4_0": "q4", "q8_0": "q8"}             # the cache types in words (start_text.py has the same)
KV_FLAGS = {"q4": "q4_0", "q4_0": "q4_0", "q8": "q8_0", "q8_0": "q8_0", "f16": "f16", "bf16": "bf16"}


class LaunchError(Exception):
    """A start CARL refuses: the text for stderr (it may be empty: the memory check printed why) and the exit code
    (2: a wrong option or setting, 1: anything else)."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- the options
@dataclass
class Options:
    """The start's own options; rest goes to llama-server. help: -h or --help (the help of the start; the options after
    it are not read)."""
    model: str = ""
    quant: str = ""                     # --quant Q: the quantization of the model (domain/catalog/quants.py)
    kv: str = ""
    ctx: str = ""
    slots: str = ""
    net: str = ""                       # --local or --vm: local, vm
    host: str = ""                      # --host ADDR
    port: str = ""                      # --port N: the server's port, as PORT (W1.6: given to llama-server once)
    mode: str = ""                      # --router or --single
    help: str = ""
    rest: List[str] = field(default_factory=list)


def parse_options(argv: Sequence[str]) -> Options:
    """The options as host/serve-llama.sh reads them: --model, --quant, --kv, --ctx, --slots, --local, --vm, --host,
    --port, --single, --router, --q4, --q8; "--opt VALUE" or "--opt=VALUE" (an empty "=VALUE" takes the next word).
    Raises LaunchError (exit 2) for a wrong value."""
    o = Options()
    args = list(argv)
    i = 0

    def value(a: str, name: str) -> str:
        nonlocal i
        v = a[len(name):]
        v = v[1:] if v.startswith("=") else v
        if not v:
            i += 1
            v = args[i] if i < len(args) else ""
        return v

    def named(a: str, name: str) -> bool:
        return a == name or a.startswith(name + "=")
    while i < len(args):
        a = args[i]
        if a in HELP:
            o.help = a
            return o
        if a in ("--local", "--vm"):
            o.net = a[2:]
        elif a in ("--router", "--single"):
            o.mode = a[2:]
        elif named(a, "--host"):
            o.host = value(a, "--host")
            if not o.host:
                raise LaunchError("error: --host needs an address.", 2)
        elif named(a, "--port"):
            o.port = value(a, "--port")
            if not is_port(o.port):
                raise LaunchError(f"error: --port takes a TCP port from 1 to 65535, not '{o.port}'.", 2)
        elif a in ("--q4", "--q8"):
            o.kv = a[2:] + "_0"
        elif named(a, "--kv"):
            v = value(a, "--kv")
            if v not in KV_FLAGS:
                raise LaunchError(f"error: --kv takes q4 or q8 (or f16), not '{v}'.", 2)
            o.kv = KV_FLAGS[v]
        elif named(a, "--model"):
            o.model = value(a, "--model")
            if not o.model:
                raise LaunchError("error: --model needs a model name or the path of a .gguf file.", 2)
        elif named(a, "--quant"):
            o.quant = value(a, "--quant")
            if not o.quant:
                raise LaunchError("error: --quant needs a quantization (for example Q4_K_M).", 2)
        elif named(a, "--slots"):
            v = value(a, "--slots")
            if not _SLOTS.fullmatch(v):
                raise LaunchError(f"error: --slots takes 1 to {MAX_SLOTS} or auto, not '{v}'.", 2)
            o.slots = v
        elif named(a, "--ctx"):
            o.ctx = str(ctx_tokens(value(a, "--ctx").lower()))
        else:
            o.rest.append(a)
        i += 1
    if o.quant and not o.model:
        raise LaunchError("error: --quant goes with --model NAME.", 2)
    return o


def ctx_tokens(v: str) -> int:
    """--ctx N or Nk (k = 1024), from 4k to 256k; raises LaunchError (exit 2)."""
    m = _CTX_K.fullmatch(v)
    if m:
        n = int(m.group(1)) * 1024
    elif _CTX_N.fullmatch(v):
        n = int(v)
    else:
        raise LaunchError(f"error: --ctx takes a number of tokens (for example 131072) or Nk (for example 128k), not "
                          f"'{v}'.", 2)
    lo, hi = ctx_range()
    if not lo <= n <= hi:
        raise LaunchError(f"error: --ctx must be from {lo // 1024}k to {hi // 1024}k tokens, not {n}.", 2)
    return n


def ctx_range() -> Tuple[int, int]:
    """The context of a slot, in tokens: the schema's range of llama.ctx."""
    spec = MODEL_KEYS["ctx"]
    return int(spec.min or 0), int(spec.max or 0)


# ---------------------------------------------------------------- the checks of a value
def require_int(name: str, v: str, lo: Optional[int] = None, hi: Optional[int] = None) -> None:
    """A whole number (at most 9 digits, no sign, no leading zero) in range; else LaunchError (exit 2)."""
    if not _WHOLE.fullmatch(v) or (lo is not None and int(v) < lo) or (hi is not None and int(v) > hi):
        rng = (f" from {lo}" if lo is not None else "") + (f" to {hi}" if hi is not None else "")
        raise LaunchError(f"error: {name} must be a whole number{rng}, got '{v}'", 2)


def is_port(v: str) -> bool:
    return bool(_PORT.fullmatch(v)) and 1 <= int(v) <= 65535


def launch_lines(text: str) -> List[Tuple[str, str]]:
    """launch-env's KEY=value lines as the launcher reads them (bash `IFS='=' read -r k v`): split at the first =,
    the one trailing = of a value without another = dropped; a line without a key skipped."""
    out = []
    for line in text.split("\n"):
        k, _, v = line.partition("=")
        if v.endswith("=") and "=" not in v[:-1]:
            v = v[:-1]
        if k:
            out.append((k, v))
    return out


# ---------------------------------------------------------------- the plan
@dataclass
class LaunchPlan:
    """One start as it is resolved. vars: the launcher's variables (the environment, then launch-env's lines, then
    what the plan sets; the names above); stderr: the warnings and notes not shown yet, in order."""
    options: Options
    vars: Dict[str, str]
    env_set: FrozenSet[str]                     # the REPORTED variables and THREAD_PIN the environment set (start lines)
    stderr: List[str] = field(default_factory=list)
    mode: str = ""                              # single or router
    extra: List[str] = field(default_factory=list)   # llama-server's extra options: extra_args, then the command line's
    net_note: str = ""
    slot_dir: str = ""
    draft: str = ""                             # the drafter passed with -md ("" none)
    mmproj: str = ""                            # the vision projector passed with --mmproj ("" none: --no-mmproj)
    spec_note: str = ""
    slots_note: str = ""
    gpu_note: str = ""                          # the memory plan's lines about the GPUs (one for each), "" none
    devices: str = ""                           # -dev: the devices the memory plan put the model on (CUDA0,CUDA1), "" none
    tensor_split: str = ""                      # -ts: the layers of each (21,20), "" for one device or no plan
    n_cpu_moe: int = 0                          # --n-cpu-moe: the layers whose MoE experts stay in the RAM, 0 none
    room: int = 0                               # the bytes a model could use on the GPUs when the plan ran (0 unknown)
    ub_auto: bool = False                       # -ub is the model's (UB_AUTO), so the plan may lower it (A45)
    ub_pick: bool = False                       # ...and it has no tune record: the plan picks the size (A52)
    ub_ask: Optional[UbAsk] = None              # a config -ub against the tuned one, not answered yet (A51)
    mtp_off: bool = False                       # the plan turned MTP off because experts are in the RAM (A51)
    pin: Optional[ThreadPin] = None             # --threads and --cpu-mask: one thread per physical core (A54), None none
    pin_note: str = ""                          # why there is no pin with experts in the RAM ("" none)
    dry_note: str = ""                          # D's check against llama-server's own fit ("" none, or they agree; A67)
    need_note: str = ""                         # where the GPU need came from: E, the drafter's bound, B (A67; "" none)
    gpu_extra: int = 0                          # bytes llama-server's fit needs beyond CARL's estimate: the plan's room
                                                # is that much less (A67)
    kv_open: bool = False                       # the context memory type is nobody's choice: the CPU alone may pick (A71)
    kv_note: str = ""                           # why the memory plan chose it ("" it did not)
    ub_kept: bool = False                       # the question was answered Keep: the config -ub stands for this model
    load_mode: str = ""                         # --load-mode none when "none"; "" or mapped: llama.cpp's own load
    env_add: Dict[str, str] = field(default_factory=dict)   # variables the server gets besides (CUDA_DEVICE_ORDER)
    swa: str = SWA_UNSET                        # full, window or - (the memory plan's answer)
    template: str = ""                          # the thinking toggle's chat template ("" none)

    def get(self, name: str) -> str:
        return self.vars.get(name, "")

    def take_stderr(self) -> List[str]:
        """The lines to show now (and forget)."""
        out, self.stderr = self.stderr, []
        return out


LaunchLines = Union[Mapping[str, SettingValue], Iterable[Tuple[str, str]]]


def _drop_bad_sampling(env: Dict[str, str]) -> List[str]:
    """Removes the sampling variables of env whose value is not a number inside the setting's range (the 0..2 of
    TEMP and the like); the warnings for them."""
    notes: List[str] = []
    for key in SAMPLING:
        spec, name = MODEL_KEYS[key], _env(MODEL_KEYS, key)
        if name not in env:
            continue
        try:
            value = float(coerce(spec, env[name], name))
            ok = value == value and (spec.min is None or value >= spec.min) and (spec.max is None or value <= spec.max)
        except ConfigError:
            ok = False
        if not ok:
            notes.append(f"warning: ignoring {name}={env.pop(name)!r} from the environment: it is not a number from "
                         f"{spec.min} to {spec.max}")
    return notes


def begin(options: Options, environ: Mapping[str, str], lines: LaunchLines, numeric_temp: bool = False) -> LaunchPlan:
    """The plan from the options, the environment and launch-env's lines (a mapping, or the pairs of launch_lines).
    The environment wins over a line except for FORCED; RESET never comes from the environment; an unknown line
    gives a warning; --host counts as set before the lines. A sampling variable (TEMP, TOP_P, TOP_K, MIN_P, PRESENCE,
    REPEAT) that is not a number inside its setting's range is left out with a warning, for llama-server would refuse
    it. numeric_temp (Windows, where TEMP is the temporary folder in every environment): a TEMP that is not a number
    is left out without a word."""
    env = {k: v for k, v in environ.items() if v and k not in RESET}
    if numeric_temp and TEMP in env and not _NUMBER.fullmatch(env[TEMP]):
        del env[TEMP]
    dropped = _drop_bad_sampling(env)
    plan = LaunchPlan(options, dict(env), frozenset(n for n in [n for _, n in REPORTED] + [THREAD_PIN] if env.get(n)))
    plan.stderr.extend(dropped)
    if options.host:
        plan.vars[HOST] = options.host
    pairs = lines.items() if isinstance(lines, Mapping) else lines
    for k, v in pairs:
        if k not in ALLOWED:
            plan.stderr.append(f"warning: ignoring unknown setting '{k}' from the launch settings")
            continue
        if k in FORCED or not plan.vars.get(k):
            plan.vars[k] = shell_value(v)
    return plan


def resolve(plan: LaunchPlan, words: PlatformWords, home: str, conf_dir: str, key_file: str, stamp: str,
            is_file: Callable[[str], bool], has_addr: Callable[[str], bool], addr_tool: str = "ifconfig") -> None:
    """Everything a start decides before it checks the port, in host/serve-llama.sh's order: the mode, the model
    file, the alias, the extra options, the address, the port, the key file, the context, the slots, the KV types,
    -ub, the speculation and the drafter, the sampling and the batches, the log file, then the whole-number checks.
    home, conf_dir: the home and settings folders; key_file: the key file when API_KEY_FILE is unset; stamp: the log
    file's time (20260102-030405); is_file, has_addr: what this computer has. Raises LaunchError."""
    v, o, cmd = plan.vars, plan.options, words.command
    modes = LLAMA_KEYS["mode"].choices
    plan.mode = v[LLAMA_MODE] = o.mode or plan.get(LLAMA_MODE) or str(LLAMA_KEYS["mode"].default)
    if plan.mode not in modes:
        raise LaunchError(f"error: {LLAMA_MODE} takes {' or '.join(modes)}, not '{plan.mode}'.", 2)
    model = plan.get(MODEL)
    if not is_file(model):
        raise LaunchError(f"error: the model file {model} does not exist. {cmd} models lists the models; {cmd} download "
                          "NAME downloads one.")
    v[ALIAS] = plan.get(ALIAS) or gguf_stem(policy_for(model).basename(model))
    # extra_args: one line of words (launch-env allows no quotes), split, never evaluated; the command line's last
    first_line = plan.get(EXTRA_ARGS).split("\n", 1)[0]
    plan.extra = [w for w in re.split(r"[ \t]+", first_line) if w] + list(o.rest)
    if o.net and not o.host:
        v.pop(HOST, None)
    try:
        v[HOST], plan.net_note = resolve_host(o.net or plan.get(NET), plan.get(HOST), plan.get(VM_HOST) or VM_ADDR,
                                              has_addr, words, addr_tool)
    except NetworkRefused as e:
        raise LaunchError(str(e), e.code) from None
    v[PORT] = o.port or plan.get(PORT) or str(DEFAULT_PORT)
    if not is_port(v[PORT]):
        raise LaunchError(f"error: {PORT} must be a TCP port from 1 to 65535, not '{v[PORT]}'.", 2)
    v[API_KEY_FILE] = plan.get(API_KEY_FILE) or key_file
    v[CTX] = o.ctx or plan.get(CTX) or shell_value(MODEL_KEYS["ctx"].default)
    v[SLOTS] = o.slots or plan.get(SLOTS) or AUTO
    if v[SLOTS] != AUTO:
        require_int(SLOTS, v[SLOTS], 1, MAX_SLOTS)
    require_int(CTX, v[CTX], *ctx_range())
    v[KV] = o.kv or plan.get(KV) or shell_value(MODEL_KEYS["kv"].default)
    plan.kv_open = (not (o.kv or KV in plan.env_set or plan.get(KV_K) or plan.get(KV_V))
                    and source_key(plan, "kv") in KV_OPEN)
    v[KV_K], v[KV_V] = plan.get(KV_K) or v[KV], plan.get(KV_V) or v[KV]
    # -ub: the server options' own, then UB (llama.ub, the environment), then the model's (UB_AUTO), then the default
    given = extra_value(plan.extra, UB_FLAGS)
    plan.ub_auto = not given and not plan.get(UB) and bool(plan.get(UB_AUTO))
    # a typed -ub (the options, the command line, the environment's UB) wins over the question (A51)
    plan.ub_ask = None if given or plan.get(UB) else UbAsk.from_var(plan.get(UB_ASK), plan.get(UB_STAMP))
    plan.ub_pick = plan.ub_auto and plan.get(UB_FROM) != TUNED      # no tune record: the plan picks the size (A52)
    v[UB] = given or plan.get(UB) or plan.get(UB_AUTO) or str(UBATCH)
    spec = plan.get(SPEC) or shell_value(MODEL_KEYS["spec"].default)
    if ":" in spec:                                  # SPEC=spec:n
        v[SPEC_N] = plan.get(SPEC_N) or spec.rsplit(":", 1)[1]
        spec = spec.split(":", 1)[0]
    v[SPEC_N] = plan.get(SPEC_N) or shell_value(MODEL_KEYS["spec_n"].default)
    for name, key in NGRAM_VARS:
        v[name] = plan.get(name) or str(NGRAM[key])
    # MTP needs an MTP head in the model file or a drafter (launch-env names it, never the environment); launch-env
    # already falls back to n-gram for a configured SPEC, this covers a SPEC from the environment
    if SPEC_MTP in spec.split(","):
        if plan.get(MTP_SOURCE) == NO_SOURCE:
            plan.stderr.append(f"note: {plan.get(MODEL_NAME) or model} has no MTP head and no downloaded MTP drafter, "
                               "so the speculation is n-gram only.")
            spec = SPEC_NGRAM
        elif plan.get(DRAFT):
            if not is_file(v[DRAFT]):
                raise LaunchError(f"error: the MTP drafter {v[DRAFT]} does not exist. {cmd} download "
                                  f"{plan.get(MODEL_NAME) or 'NAME'} downloads it.")
            plan.draft = v[DRAFT]
    files = [s for s in spec.split(",") if s in FILE_SPECS]
    if files:                                         # a drafter file of another kind: its file, else n-gram stays
        var = f"DRAFT_{KIND_OF_SPEC[files[0]].upper()}"
        word = KIND_WORDS[KIND_OF_SPEC[files[0]]]
        if SPEC_MTP in spec.split(","):
            raise LaunchError(f"error: the speculation {spec} names an MTP drafter and a {word}, and llama-server loads "
                              "one drafter file (-md). Use one of them.", 2)
        if not plan.get(var):
            plan.stderr.append(f"note: {plan.get(MODEL_NAME) or model} has no downloaded {word}, so the speculation "
                               "leaves it out.")
            spec = ",".join(s for s in spec.split(",") if s not in FILE_SPECS) or SPEC_NONE
        elif not is_file(plan.get(var)):
            raise LaunchError(f"error: the {word} {plan.get(var)} does not exist. {cmd} download "
                              f"{plan.get(MODEL_NAME) or 'NAME'} downloads it.")
        else:
            plan.draft = plan.get(var)
    v[SPEC] = spec
    if plan.get(VISION) == "1":                       # vision on: the projector of the model, when it is downloaded
        if not plan.get(MMPROJ):
            plan.stderr.append(f"note: vision is on, but {plan.get(MODEL_NAME) or model} has no downloaded vision "
                               f"projector, so the start is text only. {cmd} download {plan.get(MODEL_NAME) or 'NAME'} "
                               "--vision downloads it.")
        elif not is_file(plan.get(MMPROJ)):
            raise LaunchError(f"error: the vision projector {plan.get(MMPROJ)} does not exist. {cmd} download "
                              f"{plan.get(MODEL_NAME) or 'NAME'} --vision downloads it.")
        else:
            plan.mmproj = plan.get(MMPROJ)
    for keys, names in ((MODEL_KEYS, SAMPLING), (LLAMA_KEYS, BATCHES)):
        for key in names:
            name = _env(keys, key)
            v[name] = plan.get(name) or shell_value(keys[key].default)
    sep = policy_for(home).sep
    log_dir = plan.get(LOG_DIR) or sep.join((home, "models", "logs"))
    v[LOG_FILE] = plan.get(LOG_FILE) or f"{log_dir}{sep}llama-server-{stamp}.log"
    for name in (UB, BATCH, CKPT, CKPT_STEP, SPEC_N, NGRAM_MATCH, NGRAM_MIN, NGRAM_MAX):
        require_int(name, v[name])
    for name in (CACHE_RAM, RESERVE_GB):
        if plan.get(name):
            require_int(name, v[name])
    if plan.get(LOAD_RESERVE) and not _NUMBER.fullmatch(plan.get(LOAD_RESERVE)):
        raise LaunchError(f"error: {LOAD_RESERVE} must be a number of GiB, got '{plan.get(LOAD_RESERVE)}'", 2)
    if plan.get(N_CPU_MOE) and plan.get(N_CPU_MOE) != AUTO:
        require_int(N_CPU_MOE, v[N_CPU_MOE], 0, int(LLAMA_KEYS["n_cpu_moe"].max or 999))
    plan.slot_dir = f"{conf_dir}{policy_for(conf_dir).sep}slots"


def source_key(plan: LaunchPlan, key: str) -> str:
    """Where one setting comes from, as launch-env says it (CARL_SOURCES' key:source: default, catalogue, header,
    auto-tune, config, auto-fit); "" when it does not say."""
    s = f" {plan.get(CARL_SOURCES)} "
    at = s.find(f" {key}:")
    return s[at + len(key) + 2:].split(" ", 1)[0] if at >= 0 else ""


def answer_ub(plan: LaunchPlan, choice: str) -> None:
    """The answer to the plan's -ub question (ub_order.UbAsk): Keep makes the config value the start's -ub, as it is;
    Use tuned leaves the tuned one (the plan may still lower it). Either way the question is over."""
    ask = plan.ub_ask
    if ask is not None and choice == KEEP:
        plan.vars[UB], plan.ub_auto, plan.ub_pick = str(ask.mine), False, False
        plan.ub_kept = True
    plan.ub_ask = None


def apply_pin(plan: LaunchPlan, topology: Optional[CpuTopology]) -> None:
    """The threads of a start that keeps MoE experts in the RAM (A54, A57): one per physical core, pinned (--threads
    and --cpu-mask, from the CPU's topology: cpu_topology.pin_for), only when THREAD_PIN=on: the environment's, or
    the line of the model's tune record, which says it when pinned wrote faster on this computer. Without either the
    start leaves llama.cpp's own threads (pinning put the threads on busy cores and lost up to 7% on a busy computer).
    Only the plan's n_cpu_moe makes a pin: a start with every expert on the GPUs, a Mac and a dense model whole on the
    card pass none. A thread or CPU-mask option in the server options wins as typed (the start passes none of its
    own), and a CPU whose topology is not known, or with one core, gets no pin and no note. Call after apply_plan.
    topology: None when the computer has no CpuLayout."""
    plan.pin, plan.pin_note = None, ""
    if plan.n_cpu_moe <= 0 or topology is None:
        return
    typed = typed_options(plan.extra)
    if typed:
        plan.pin_note = f"as your server options say ({typed})"
        return
    pin = pin_for(topology)
    if pin is None:
        return
    if plan.get(THREAD_PIN) == PIN_ON:
        plan.pin = pin
    else:
        plan.pin_note = "llama.cpp's own, not pinned (" + pin_reason(plan) + ")"


def pin_reason(plan: LaunchPlan) -> str:
    """Why a start with experts in the RAM is not pinned (the start line's words, inside the brackets)."""
    if THREAD_PIN in plan.env_set:
        return "from the environment"
    if plan.get(THREAD_PIN) == PIN_OFF:
        return "Auto-tune measured it faster on this computer"
    return "Auto-tune has not measured pinning on this computer"


def extra_value(extra: Sequence[str], flags: Sequence[str]) -> str:
    """The value of the last of these options in llama-server's extra options ("-ub 1024", "--ubatch-size=1024"; ""
    when none is there): llama-server takes the last one, and the start's plan and check use it too."""
    found = ""
    for i, a in enumerate(extra):
        name, eq, val = a.partition("=")
        if name in flags:
            found = val if eq else (extra[i + 1] if i + 1 < len(extra) else "")
    return found if _WHOLE.fullmatch(found) else ""


_OPTION = re.compile(r"--?[A-Za-z][A-Za-z0-9_-]*")         # "-t", "--threads", "--no-mmap" (not "-1", not "-")
_OPTION_IN_TEXT = re.compile(r"(?<![\w-])--?[A-Za-z][A-Za-z0-9_-]*")


def options_in_help(text: str) -> FrozenSet[str]:
    """The option names an engine's help names (llama-server --help: "-t,    --threads N", "--mmap, --no-mmap"): every
    word of the text that has the form of one. Generous: a name the help only mentions counts too, as a refusal
    (unknown_options) must be sure."""
    return frozenset(_OPTION_IN_TEXT.findall(text))


def option_words(extra: Sequence[str]) -> List[str]:
    """The words of the server options that have the form of an option ("-x", "--name", "--name=VALUE"), in order. A
    value such as -1 is no option."""
    return [a for a in extra if _OPTION.fullmatch(a.partition("=")[0])]


def unknown_options(extra: Sequence[str], known: FrozenSet[str]) -> List[str]:
    """The option words of the server options (option_words) that the engine's help does not name (known), in
    order. Empty when known is empty: the engine's help could not be read, so the engine answers for itself (WFIN)."""
    if not known:
        return []
    return [a for a in option_words(extra) if a.partition("=")[0] not in known]


def unknown_option_error(word: str, program: str, from_setting: bool, words: PlatformWords) -> LaunchError:
    """The refusal of an option the engine does not take (exit 2), before the start writes a file (WFIN). program: the
    engine's server program (its name is said, without .exe); from_setting: the word is in llama.extra_args."""
    name = policy_for(program).basename(program)
    name = name[:-4] if name.lower().endswith(".exe") else name
    where = " (in your server options, llama.extra_args)" if from_setting else ""
    return LaunchError(f"error: {name} has no option '{word}'{where}. {words.command} engine help shows its flags.", 2)


def own_load(extra: Sequence[str]) -> str:
    """The user's own load option in llama-server's extra options (--load-mode X, --mmap, --no-mmap; the last one),
    "" when there is none: then the start passes none of its own."""
    found = ""
    for i, a in enumerate(extra):
        name = a.partition("=")[0]
        if name in LOAD_FLAGS:
            found = a if name != "--load-mode" or "=" in a else " ".join(extra[i:i + 2])
    return found


def gguf_stem(name: str) -> str:
    """A file name without .gguf (bash `basename PATH .gguf`: a name that is only ".gguf" stays)."""
    return name[:-len(".gguf")] if name.endswith(".gguf") and name != ".gguf" else name


def logs(plan: LaunchPlan) -> bool:
    """The start writes a log file (LOG_FILE is not none)."""
    return plan.get(LOG_FILE) != NO_LOG


def thinking_toggle(plan: LaunchPlan) -> bool:
    """The start passes the thinking toggle's chat template (THINK_TOGGLE is not 0)."""
    return plan.get(THINK_TOGGLE) != "0"


def template_file(model: str, folder: str, models_dir: str = "") -> str:
    """The thinking toggle's chat template of a model file: in the template/ folder of the model's own folder when the
    file is in one in the models folder (A66, layout.py; models_dir: "" when it is not known, which is no folder of
    its own), else in the templates folder. A file outside the models folder gets nothing written next to it."""
    name = gguf_stem(policy_for(model).basename(model)) + TEMPLATE_SUFFIX
    own = model_folder_of(model, models_dir) if models_dir else None
    where = policy_for(own).join(own, TEMPLATE) if own else folder
    return f"{where}{policy_for(where).sep}{name}"


# ---------------------------------------------------------------- the memory plan and check (llama-fit.py)
def spec_kept(plan: LaunchPlan) -> bool:
    """The speculation is a person's or Auto-tune's choice (the environment's SPEC, config.json, the model's tune
    record), so the plan keeps MTP even with experts in the RAM (A51)."""
    if SPEC in plan.env_set:
        return True
    return any(w in ("spec:config", "spec:auto-tune") for w in plan.get(CARL_SOURCES).split())


def _spec_fit(plan: LaunchPlan) -> List[str]:
    """The speculation and -ub (MTP's draft context, the compute buffers); the drafter's weights with MTP."""
    out = ["--spec", plan.get(SPEC), "--spec-n", plan.get(SPEC_N), "--ub", plan.get(UB)]
    out += ["--spec-kept"] if spec_kept(plan) else []
    out += ["--mmproj", plan.mmproj] if plan.mmproj else []
    return out + (["--draft", plan.draft] if plan.draft else [])


def _ub_load(plan: LaunchPlan) -> List[str]:
    """The plan's own: --ub-auto when -ub is the model's (the plan may lower it, A45; --ub-pick without a tune record
    too: it picks the largest size that keeps the setup, W1.6), and the load reserve."""
    out = ["--ub-auto"] if plan.ub_auto else []
    out += ["--ub-pick"] if plan.ub_pick else []
    return out + (["--load-reserve-gib", plan.get(LOAD_RESERVE)] if plan.get(LOAD_RESERVE) else [])


def _extra(plan: LaunchPlan) -> List[str]:
    """The room llama-server's own fit needs beyond CARL's estimate (A67): the plan and the check have that much less."""
    return ["--gpu-extra", str(plan.gpu_extra)] if plan.gpu_extra > 0 else []


def _reserve(plan: LaunchPlan) -> List[str]:
    return ["--reserve-gb", plan.get(RESERVE_GB)] if plan.get(RESERVE_GB) else []


def _devices(plan: LaunchPlan, chosen: bool) -> List[str]:
    """The devices llama-fit.py plans over: LLAMA_DEVICES when the start names them; the check (chosen) takes the
    devices the plan used."""
    names = (plan.devices if chosen else "") or plan.get(LLAMA_DEVICES)
    return ["--devices", names] if names else []


def _cpu_moe(plan: LaunchPlan, chosen: bool) -> List[str]:
    """The MoE experts in the RAM llama-fit.py works with: the setting's N (N_CPU_MOE; auto: none, the plan finds it);
    the check (chosen) takes the plan's N."""
    if chosen:
        return ["--n-cpu-moe", str(plan.n_cpu_moe)] if plan.n_cpu_moe else []
    n = plan.get(N_CPU_MOE)
    return ["--n-cpu-moe", n] if n and n != AUTO else []


def plan_args(plan: LaunchPlan) -> List[str]:
    """llama-fit.py's arguments for the memory plan: the slots, the RAM cache, the sliding-window cache and the
    speculation that fit, with the setting's MoE experts in the RAM, over the devices LLAMA_DEVICES names (all of them
    without it)."""
    return (["--plan", plan.get(MODEL), "--ctx", plan.get(CTX), "--kv", plan.get(KV_K), "--want-slots",
             plan.get(SLOTS), "--swa", plan.get(SWA_MODE) or AUTO] + (["--kv-auto"] if plan.kv_open else [])
            + _reserve(plan) + _spec_fit(plan) + _cpu_moe(plan, False) + _devices(plan, False) + _ub_load(plan)
            + _extra(plan))


def backend(plan: LaunchPlan) -> str:
    """The backend the start runs on, from its devices: the memory plan's (-dev), else the devices LLAMA_DEVICES names:
    cuda, vulkan, rocm or metal; "" for a start on no device (a Mac, the CPU)."""
    names = (plan.devices or plan.get(LLAMA_DEVICES)).split(",")
    return backend_of(names[0])


def mixed_kv_refusal(plan: LaunchPlan, words: PlatformWords) -> str:
    """Why a CUDA start with different K and V cache types is refused, in words ("" when it is not). The CUDA build's
    flash attention takes only the pairs q4_0-q4_0, q8_0-q8_0, f16-f16 and bf16-bf16; another pair runs it on the CPU."""
    k, v = plan.get(KV_K), plan.get(KV_V)
    if backend(plan) != "cuda" or k == v:
        return ""
    return (f"error: CARL refuses the start: the context memory types differ (K {KV_WORDS.get(k, k)}, V "
            f"{KV_WORDS.get(v, v)}). CUDA's flash attention takes only the same type for both, and a mix runs it on the "
            f"CPU, which is much slower. Set {KV_K} and {KV_V} to the same value, or remove them ({words.command} "
            f"start --kv q4 or --kv q8 sets both).")


def sysmem_warning(plan: LaunchPlan) -> str:
    """The warning for FIT_CHECK=0 on Windows with CUDA ("" otherwise). NVIDIA's driver setting Prefer sysmem
    fallback is on by default there: a model that does not fit the VRAM does not fail, it moves into system memory and
    crawls. The memory check is what stops that, and FIT_CHECK=0 turns it off."""
    if checks_memory(plan) or backend(plan) != "cuda" or policy_for(plan.get(MODEL)).sep != "\\":
        return ""
    return ("warning: the memory check is off (FIT_CHECK=0). NVIDIA's driver can move a model that does not fit the GPU "
            "into system memory (its setting Prefer sysmem fallback), and the model then runs very slowly instead of "
            "failing.")


def start_record(plan: LaunchPlan) -> Dict[str, object]:
    """What the start resolved, for its server file (runtime_doc.start_record, W1.6): the room it planned with (the
    answer's `room` line), the layers whose experts it keeps in the RAM and whether that count was CARL's (the setting
    auto or unset), its -ub and its speculation. The dashboard shows the plan now beside them, with the reason."""
    ub = plan.get(UB)
    return rd.start_record(plan.room, plan.n_cpu_moe, plan.get(N_CPU_MOE) in ("", AUTO), int(ub) if ub.isdigit() else 0,
                           plan.get(SPEC))


def apply_plan(plan: LaunchPlan, ok: bool, answer: str, words: PlatformWords) -> None:
    """The memory plan's answer (ok: it ran; answer: its output). Auto fit's order: MTP goes (n-gram stays) before
    a slot. Without an answer: one slot for auto, a 4 GiB RAM cache, and the setting's MoE experts in the RAM (N_CPU_MOE
    when it is a number). After the first line a PC may answer a devices line (-dev, and -ts for several: `devices
    CUDA0,CUDA1 21,20`), a line of the MoE experts in the RAM (`cpu-moe 12`: --n-cpu-moe), the room it planned with
    (`room BYTES`, W1.6: the server file records it) and lines about its GPUs and the RAM (the notes). Any start may answer `ub N` (the -ub that keeps the setup, taken when -ub is the model's:
    A45) and `load none` or `load mapped` with its line (A44, A45; the user's own --load-mode, --mmap or --no-mmap
    wins, and the line says so). Raises
    LaunchError for a CUDA start with different K and V types (mixed_kv_refusal); FIT_CHECK=0 on Windows with CUDA
    adds a warning (sysmem_warning)."""
    v = plan.vars
    first, _, rest = answer.rstrip("\n").partition("\n")      # a PC adds lines about its GPUs
    m = PLAN_LINE.fullmatch(first) if ok else None
    if m:
        notes = []
        own = own_load(plan.extra)
        for line in rest.split("\n"):
            d = DEVICES_LINE.fullmatch(line.strip())
            n = CPU_MOE_LINE.fullmatch(line.strip())
            u = UB_LINE.fullmatch(line.strip())
            rm = ROOM_LINE.fullmatch(line.strip())
            ld = LOAD_LINE.fullmatch(line.strip())
            k = KV_LINE.fullmatch(line.strip())
            if MTP_RAM_LINE.fullmatch(line.strip()):
                plan.mtp_off = True
            elif k:
                if plan.kv_open:                            # the CPU alone: the type for speed (A71)
                    v[KV] = v[KV_K] = v[KV_V] = k.group(1)
            elif line.strip().startswith(KV_NOTE_WORDS):
                plan.kv_note = line.strip()[len(KV_NOTE_WORDS):] if plan.kv_open else ""
            elif u:
                if plan.ub_auto:
                    v[UB] = u.group(1)
            elif rm:
                plan.room = int(rm.group(1))
            elif ld:
                plan.load_mode = "" if own else ld.group(1)
            elif own and line.strip().startswith(LOAD_WORDS):
                notes.append(f"{LOAD_WORDS}as your server options say ({own}).")
            elif d:
                plan.devices, plan.tensor_split = d.group(1), d.group(2) or ""
                if d.group(1).startswith("CUDA"):          # the server's CUDA numbers are CARL's (PCI bus order)
                    plan.env_add[CUDA_ORDER[0]] = CUDA_ORDER[1]
            elif n:
                plan.n_cpu_moe = int(n.group(1))
            elif line.strip():
                notes.append(line.strip())
        plan.gpu_note = "\n".join(notes)
        if m.group(4) != v[SPEC]:
            v[SPEC], plan.draft = m.group(4), ""
            plan.spec_note = (RAM_MTP_NOTE if plan.mtp_off else
                              f"from Auto fit: MTP does not fit with these slots and this context on {words.machine}")
        if v[SLOTS] == AUTO:
            plan.slots_note = "auto: " + ("two slots do not fit" if m.group(1) == "1" else "two slots fit")
        v[SLOTS], plan.swa = m.group(1), m.group(3)
        v[CACHE_RAM] = plan.get(CACHE_RAM) or m.group(2)
    else:
        if v[SLOTS] == AUTO:
            v[SLOTS], plan.slots_note = "1", "auto: CARL could not calculate the memory"
        v[CACHE_RAM] = plan.get(CACHE_RAM) or CACHE_RAM_FALLBACK
        n = plan.get(N_CPU_MOE)
        plan.n_cpu_moe = int(n) if n.isdigit() else 0
    refusal = mixed_kv_refusal(plan, words)
    if refusal:
        raise LaunchError(refusal, 2)
    warning = sysmem_warning(plan)
    if warning:
        plan.stderr.append(warning)


def checks_memory(plan: LaunchPlan) -> bool:
    """The memory check runs (FIT_CHECK is not 0, the expert override)."""
    return plan.get(FIT_CHECK) != "0"


def check_args(plan: LaunchPlan) -> List[str]:
    """llama-fit.py's arguments for the memory check (exit 3: the start does not fit)."""
    return (["--check", plan.get(MODEL), "--ctx", plan.get(CTX), "--slots", plan.get(SLOTS), "--kv", plan.get(KV_K),
             "--swa", "window" if plan.swa == "window" else "full"] + _reserve(plan) + _spec_fit(plan)
            + _cpu_moe(plan, True) + _devices(plan, True) + _extra(plan))


def need_args(plan: LaunchPlan) -> List[str]:
    """llama-fit.py's arguments for CARL's own need of the start on its device (A67): the check's, and --need."""
    return check_args(plan) + ["--need"]


@dataclass(frozen=True)
class NeedAnswer:
    """The answer of need_args: CARL's need on the device (bytes, as the check counts it, without --gpu-extra), the part
    of it that is the backend's context beyond llama.cpp's buffers, the drafter's bound (its file and its context's
    compute buffer from the header; 0: no drafter in its own file) and the output weight's name (D's check)."""
    need: int
    context: int
    draft: int = 0
    output: str = ""


def need_answer(text: str) -> Optional[NeedAnswer]:
    """need_args' answer (`need BYTES CONTEXT`, then `draft BYTES` and `output NAME`); None for a start over several
    GPUs (it answers nothing) or an answer that does not read."""
    need = context = draft = -1
    output = ""
    for line in text.splitlines():
        w = line.split()
        if len(w) == 3 and w[0] == "need" and w[1].isdigit() and w[2].isdigit():
            need, context = int(w[1]), int(w[2])
        elif len(w) == 2 and w[0] == "draft" and w[1].isdigit():
            draft = int(w[1])
        elif len(w) == 2 and w[0] == "output":
            output = w[1]
    return NeedAnswer(need, context, max(draft, 0), output) if need >= 0 else None


@dataclass(frozen=True)
class Measured:
    """A start's need from llama-server's own fit (A67): the bytes on the device, the source (server_fit.SOURCES), the
    gap to CARL's estimate (bytes; under 0 the estimate was over), the main context's compute buffer (MiB) and the output
    weight's name (D's check)."""
    need: int
    source: str
    gap: int
    compute: int
    output: str = ""


def measured(plan: LaunchPlan, fit: sf.Breakdown, ans: NeedAnswer, record: Optional[int]) -> Measured:
    """The need of the start from its fit and CARL's answer: the device rows, the backend's context and, for a drafter
    in its own file (-md), the extra model's rows with its weights when the fit measured it, else its record from an
    earlier start (record) or its bound. Whatever the log says about the extra model (a failure in any words, or no
    block at all), a drafter the fit did not measure is never left out."""
    drafter = bool(plan.draft)
    need, source = fit.device_bytes(drafter) + ans.context, sf.E
    if drafter and not fit.extra_measured():
        need += record if record else ans.draft
        source = sf.E_RECORD if record else sf.E_BOUND
    return Measured(need, source, need - ans.need, fit.compute(), ans.output)


def apply_measured(plan: LaunchPlan, m: Measured) -> None:
    """The start's line about its need and its source (with the gap when the estimate was under)."""
    devices = [d for d in plan.devices.split(",") if d]
    plan.need_note = sf.need_line(m.source, m.need, devices, plan.gpu_extra)


def dry_args(server_argv: Sequence[str], output: str) -> List[str]:
    """The start's arguments for D's check (A64, A67): the output weight off the GPU and no op offload, which is the
    server's graph on a start whole on the GPU (the dry run takes its flags from them: dry_run.argv_of)."""
    return list(server_argv) + (["-ot", "^%s$=CPU" % output.replace(".", "\\."), "--no-op-offload"] if output else [])


def apply_dry(plan: LaunchPlan, m: Measured, dry_output: Optional[str]) -> None:
    """D's check on a start whole on the GPU: a line when the dry run's compute buffer is not the fit's (a changed
    build). Nothing when the dry run could not run or printed no device."""
    dry = dry_run.parse(dry_output or "")
    if dry is not None and dry.devices():
        plan.dry_note = sf.check_line(dry.compute(), m.compute)


def after_check(plan: LaunchPlan, code: int) -> None:
    """The memory check's exit code: 3 refuses the start (the check said why), another failure only warns."""
    if code == REFUSED:
        raise LaunchError("", 1)
    if code != 0:
        plan.stderr.append(f"warning: CARL could not check the memory (exit {code}). The server starts anyway.")


# ---------------------------------------------------------------- the plugins' launch steps (WP)
@dataclass(frozen=True)
class LaunchFacts:
    """What a plugin's launch step may read of a start that is resolved (a copy: a step cannot change the plan)."""
    mode: str                                   # single or router
    model: str                                  # the model file
    model_name: str
    alias: str
    host: str
    port: str
    ctx: str
    slots: str
    kv: str
    spec: str
    program: str                                # llama-server's program (its name, or a path)
    argv: Tuple[str, ...]                       # llama-server's arguments, as they stand (without the program)


@dataclass(frozen=True)
class LaunchStep:
    """What a plugin adds to a start: arguments after llama-server's own (before the extra options of the user, which
    stay last), variables for the server's environment, another program in place of llama-server (empty: the same),
    and a line for the start's output. A plugin's step never takes away an argument or a variable."""
    argv: Tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    program: str = ""
    note: str = ""


def launch_facts(plan: LaunchPlan, program: str, args: Sequence[str]) -> LaunchFacts:
    """The plan as a plugin's launch step may read it."""
    return LaunchFacts(plan.mode, plan.get(MODEL), plan.get(MODEL_NAME), plan.get(ALIAS), plan.get(HOST), plan.get(PORT),
                       plan.get(CTX), plan.get(SLOTS), plan.get(KV), plan.get(SPEC), program, tuple(args))


def with_steps(program: str, args: Sequence[str], env: Mapping[str, str], steps: Sequence[LaunchStep],
               extra: Sequence[str] = ()) -> Tuple[str, List[str], Dict[str, str], List[str]]:
    """(program, arguments, environment, notes) with the steps applied in order: each step's arguments go in front of
    `extra` (the user's own options, which stay last; they are the tail of `args`), its variables are added (a later step
    wins), its program replaces the program. Raises ConfigError for a variable name outside the safe set or an
    argument that is not text. With no step, the answer is the input."""
    if not steps:
        return program, list(args), dict(env), []
    n = len(extra) if list(args[len(args) - len(extra):]) == list(extra) else 0      # the user's options end the arguments
    head, tail = (list(args[:len(args) - n]), list(args[len(args) - n:])) if n else (list(args), [])
    out_env, notes = dict(env), []
    for step in steps:
        for a in step.argv:
            if not isinstance(a, str) or "\0" in a:
                raise ConfigError(f"a launch step gave {a!r}, which is not an argument")
            head.append(a)
        for k, v in step.env.items():
            if not _SAFE_KEY.fullmatch(k):
                raise ConfigError(f"a launch step gave {k!r}, which is not a variable name")
            out_env[k] = str(v)
        program = step.program or program
        if step.note:
            notes.append(step.note)
    return program, head + tail, out_env, notes


# ---------------------------------------------------------------- llama-server's environment
# The variables the bash launcher assigns: one that the caller exported reaches llama-server with the launcher's
# value (bash keeps an exported variable exported when it changes it). One the caller did not export stays out.
ASSIGNED = tuple(dict.fromkeys(FORCED + SCHEMA_VARS + (EXTRA_ARGS, PORT, API_KEY_FILE, LOG_FILE) + ENV_ONLY))
LOG_FILTER = "CARL_LOG_FILTER"                       # 1: a single model's output goes through the log filter


def server_environment(environ: Mapping[str, str], plan: LaunchPlan, home: str,
                       log_filter: Optional[bool] = None) -> Dict[str, str]:
    """The environment llama-server gets: the caller's, with the launcher's values of the variables it assigns (when
    the caller exported them: an empty one too), the log folder and the VM address it defaults, and for a single
    model CARL_LOG_FILTER (log_filter; None: a router start, which leaves it as it is). host/serve-llama.sh's of
    1.20.10, variable for variable, held by the CLI goldens."""
    env = dict(environ)
    for name in ASSIGNED:
        if name in env:
            env[name] = plan.get(name)
    for name, default in ((LOG_DIR, policy_for(home).sep.join((home, "models", "logs"))), (VM_HOST, VM_ADDR)):
        if name in env and not env[name]:
            env[name] = default
    env.update(plan.env_add)
    if log_filter is not None:
        env[LOG_FILTER] = "1" if log_filter else "0"
    return env


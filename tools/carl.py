#!/usr/bin/env python3
"""CARL models and configuration: one place for the built-in catalogue, the
models on this Mac, and the user's settings. Used by host/serve-llama.sh,
host/serve.sh, host/models.sh, tools/llama-fit.py and the monitor.

Files
  host/catalog.json                  built-in catalogue (in the repo): download source, pinned
                                     revision + sha256, tuned settings and why, context zones
  ~/.config/llm-deploy/models.json   models on this Mac that are not in the catalogue (Hugging
                                     Face downloads, files dropped into the models folder) and
                                     Auto-tune results for every model (per Mac)
  ~/.config/llm-deploy/config.json   the user's settings (monitor Settings tab, or by hand):
                                     backend, per-model profiles, server and MTPLX options
  ~/models/gguf/*.gguf               the models folder (paths.models_dir); every .gguf here is
                                     listed, catalogued or not

Settings precedence for a llama.cpp start: command-line flags > environment >
config.json (llama section, then models.<name>) > Auto-tune result for this Mac >
catalogue tune > built-in defaults.

CLI (./carl.sh models | download | verify use it through host/models.sh)
  carl.py list                         models: catalogue + models folder + custom
  carl.py download NAME|default|all    a catalogue model (pinned, verified)
  carl.py download hf:REPO/FILE.gguf   any GGUF from Hugging Face (also a huggingface.co URL)
  carl.py hf-files REPO                the GGUF files of a Hugging Face repo
  carl.py verify NAME... | delete NAME | path NAME | get NAME FIELD | default | downloaded
  carl.py launch-env [--model NAME|PATH] [--ctx N]   KEY=value lines for serve-llama.sh
  carl.py mtplx-env                    KEY=value lines for serve.sh grant|pocket
  carl.py config [show|path|get KEY|set KEY VALUE|unset KEY]   KEY like llama.net or models.NAME.ctx
"""
import hashlib, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_FILE = os.environ.get("CARL_CATALOG", os.path.join(REPO, "host", "catalog.json"))
CONF_DIR = os.path.expanduser(os.environ.get("CARL_CONF_DIR", "~/.config/llm-deploy"))
CONFIG_FILE = os.path.join(CONF_DIR, "config.json")
LOCAL_FILE = os.path.join(CONF_DIR, "models.json")
OLD_LLAMA_ENV = os.path.join(CONF_DIR, "llama.env")       # before config.json (migrated once)
OLD_MTPLX_ENV = os.path.join(CONF_DIR, "mtplx.env")
SCHEMA = 1
GIB = 2 ** 30

# ---------------------------------------------------------------- settings schema
# Per-model settings (config.json "models.<name>", Auto-tune, catalogue "tune").
MODEL_KEYS = {
    "kv":       {"type": "choice", "choices": ["q4_0", "q8_0", "f16"], "default": "q4_0", "env": "KV"},
    "ctx":      {"type": "int", "min": 4096, "max": 262144, "default": 98304, "env": "CTX"},
    "slots":    {"type": "choice", "choices": ["auto", "1", "2", "3", "4"], "default": "auto", "env": "SLOTS"},
    "spec":     {"type": "choice", "choices": ["none", "draft-mtp", "ngram-mod", "draft-mtp,ngram-mod"], "default": "draft-mtp,ngram-mod", "env": "SPEC"},
    "spec_n":   {"type": "int", "min": 1, "max": 8, "default": 1, "env": "SPEC_N"},
    "temp":     {"type": "float", "min": 0, "max": 2, "default": 1.0, "env": "TEMP"},
    "top_p":    {"type": "float", "min": 0, "max": 1, "default": 0.95, "env": "TOP_P"},
    "top_k":    {"type": "int", "min": 0, "max": 1000, "default": 20, "env": "TOP_K"},
    "min_p":    {"type": "float", "min": 0, "max": 1, "default": 0, "env": "MIN_P"},
    "presence": {"type": "float", "min": 0, "max": 2, "default": 0, "env": "PRESENCE"},
    "repeat":   {"type": "float", "min": 0.5, "max": 2, "default": 1.0, "env": "REPEAT"},
    "alias":    {"type": "str", "default": "", "env": "ALIAS"},
}
# Server-wide llama.cpp settings (config.json "llama").
LLAMA_KEYS = {
    "model":       {"type": "str", "default": "auto"},             # auto = this Mac's default
    "net":         {"type": "choice", "choices": ["auto", "local", "vm"], "default": "auto", "env": "NET"},
    "host":        {"type": "str", "default": "", "env": "HOST"},  # one address of this Mac (wins over net)
    "cache_ram":   {"type": "intauto", "min": 0, "max": 65536, "default": "auto", "env": "CACHE_RAM"},
    "ub":          {"type": "int", "min": 64, "max": 8192, "default": 512, "env": "UB"},
    "batch":       {"type": "int", "min": 64, "max": 16384, "default": 2048, "env": "BATCH"},
    "ckpt":        {"type": "int", "min": 0, "max": 64, "default": 8, "env": "CKPT"},
    "ckpt_step":   {"type": "int", "min": 256, "max": 65536, "default": 4096, "env": "CKPT_STEP"},
    "think_toggle": {"type": "bool", "default": True, "env": "THINK_TOGGLE"},
    "extra_args":  {"type": "list", "default": []},                # passed to llama-server as-is
}
MTPLX_KEYS = {
    "preset":        {"type": "choice", "choices": ["grant", "pocket"], "default": "grant"},
    "context":       {"type": "int", "min": 4096, "max": 262144, "default": 49152, "env": "CONTEXT"},
    "profile":       {"type": "choice", "choices": ["sustained", "turbo", "stable"], "default": "sustained", "env": "PROFILE"},
    "depth":         {"type": "int", "min": 1, "max": 4, "default": 2, "env": "DEPTH"},
    "kv_quant":      {"type": "choice", "choices": ["off", "q8", "q4"], "default": "off", "env": "KV_QUANT"},
    "net":           {"type": "choice", "choices": ["auto", "local", "vm"], "default": "auto", "env": "NET"},
    "host":          {"type": "str", "default": "", "env": "HOST"},
    "scheduler":     {"type": "str", "default": "", "env": "SCHEDULER"},
    "batching":      {"type": "str", "default": "", "env": "BATCHING"},
    "prefill_chunk": {"type": "str", "default": "", "env": "PREFILL_CHUNK"},
    "ssd_cache":     {"type": "str", "default": "", "env": "SSD_CACHE"},
}
PATH_KEYS = {"models_dir": {"type": "str", "default": "~/models/gguf"}}
SECTIONS = {"llama": LLAMA_KEYS, "mtplx": MTPLX_KEYS, "paths": PATH_KEYS}


class ConfigError(ValueError):
    pass


def coerce(spec, v, where):
    """Validate and normalize one value against its schema entry."""
    t = spec["type"]
    try:
        if t == "int":
            v = int(float(v[:-1]) * 1024) if isinstance(v, str) and v.lower().endswith("k") else int(v)
        elif t == "float":
            v = float(v)
        elif t == "intauto":
            v = "auto" if str(v) == "auto" else int(v)
        elif t == "bool":
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif t == "list":
            v = v if isinstance(v, list) else str(v).split()
            v = [str(x) for x in v]
        elif t == "choice":
            v = str(v)
            if v not in spec["choices"]:
                raise ConfigError(f"{where}: {v!r} is not one of {', '.join(spec['choices'])}")
        else:
            v = str(v)
    except (TypeError, ValueError) as e:
        if isinstance(e, ConfigError):
            raise
        raise ConfigError(f"{where}: {v!r} is not a valid {t}")
    if t in ("int", "float", "intauto") and v != "auto":
        if v < spec.get("min", v) or v > spec.get("max", v):
            raise ConfigError(f"{where}: {v} is out of range {spec.get('min')}..{spec.get('max')}")
    return v


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"{path}: {e}")


def _write_json(path, data):
    """Atomic write (temp file + rename), user-only permissions."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------- config.json
def _read_env(path):
    out = {}
    try:
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                out[k] = v
    except OSError:
        pass
    return out


def _migrate():
    """config.json from the old llama.env / mtplx.env (written by earlier monitors)."""
    cfg = {"schema": SCHEMA}
    old, mx = _read_env(OLD_LLAMA_ENV), _read_env(OLD_MTPLX_ENV)
    if not old and not mx:
        return None
    env_llama = {s["env"]: k for k, s in LLAMA_KEYS.items() if "env" in s}
    env_model = {s["env"]: k for k, s in MODEL_KEYS.items() if "env" in s}
    env_mx = {s["env"]: k for k, s in MTPLX_KEYS.items() if "env" in s}
    model = old.get("MODEL_NAME", "auto")
    for e, v in old.items():
        if e == "MODEL_NAME":
            cfg.setdefault("llama", {})["model"] = v
        elif e in env_llama:
            cfg.setdefault("llama", {})[env_llama[e]] = v
        elif e in env_model and model != "auto":
            cfg.setdefault("models", {}).setdefault(model, {})[env_model[e]] = v
        elif e == "SPEC" and v == "model":
            pass
    for e, v in mx.items():
        if e in env_mx:
            cfg.setdefault("mtplx", {})[env_mx[e]] = v
    try:
        cfg = validate_config(cfg)[0]
    except ConfigError:
        return None
    _write_json(CONFIG_FILE, cfg)
    return cfg


def validate_config(cfg):
    """(normalized config, [warnings]). Raises ConfigError on a bad value."""
    if not isinstance(cfg, dict):
        raise ConfigError("config.json must hold a JSON object")
    out, warn = {"schema": SCHEMA}, []
    for key, val in cfg.items():
        if key in ("schema", "_comment"):
            continue
        if key == "backend":
            if val not in ("llama", "mtplx"):
                raise ConfigError(f"backend: {val!r} is not llama or mtplx")
            out["backend"] = val
        elif key in SECTIONS:
            sec = out[key] = {}
            for k, v in (val or {}).items():
                if k not in SECTIONS[key]:
                    warn.append(f"{key}.{k}: unknown setting (ignored)")
                    continue
                sec[k] = coerce(SECTIONS[key][k], v, f"{key}.{k}")
        elif key == "models":
            out["models"] = {}
            for name, prof in (val or {}).items():
                p = out["models"][name] = {}
                for k, v in (prof or {}).items():
                    if k not in MODEL_KEYS:
                        warn.append(f"models.{name}.{k}: unknown setting (ignored)")
                        continue
                    p[k] = coerce(MODEL_KEYS[k], v, f"models.{name}.{k}")
        else:
            warn.append(f"{key}: unknown section (ignored)")
    return out, warn


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return _migrate() or {"schema": SCHEMA}
    return validate_config(_read_json(CONFIG_FILE, {}))[0]


def save_config(cfg):
    cfg = validate_config(cfg)[0]
    cfg = {"schema": SCHEMA, "_comment": "CARL settings. Edit here or in the monitor's Settings tab. "
           "Sections: backend, llama, models.<name> (per-model profile), mtplx, paths. "
           "Precedence: flags > environment > this file > Auto-tune > catalogue. ./carl.sh config show lists every key.",
           **{k: v for k, v in cfg.items() if k != "schema" and v != {}}}
    _write_json(CONFIG_FILE, cfg)
    return cfg


def models_dir(cfg=None):
    d = os.environ.get("MODELS_DIR") or ((cfg or load_config()).get("paths") or {}).get("models_dir") or "~/models/gguf"
    return os.path.expanduser(d)


# ---------------------------------------------------------------- catalogue + local models
def load_catalog():
    cat = _read_json(CATALOG_FILE, None)
    if not cat or cat.get("schema") != SCHEMA:
        raise ConfigError(f"{CATALOG_FILE}: missing or wrong schema")
    return cat


def load_local():
    db = _read_json(LOCAL_FILE, {"schema": SCHEMA, "models": {}})
    db.setdefault("models", {})
    return db


def save_local(db):
    db["schema"] = SCHEMA
    _write_json(LOCAL_FILE, db)


def _status(path, want):
    if os.path.exists(path + ".aria2") or (os.path.exists(path) and want and os.path.getsize(path) != want):
        return "partial"
    return "downloaded" if os.path.exists(path) else "missing"


def _is_part(fn):
    """Skip vision projectors and 2nd+ parts of split GGUFs."""
    return fn.lower().startswith("mmproj") or bool(re.search(r"-0000[2-9]-of-\d+\.gguf$|-000[1-9]\d-of-\d+\.gguf$", fn))


_SHAPES = {}


def shape_of(path):
    """GGUF header shape of a local file (cached by path + mtime: the header read is 64 MB)."""
    from gguf_shape import local_meta, model_shape
    key = (path, os.path.getmtime(path))
    if key not in _SHAPES:
        _SHAPES[key] = model_shape(local_meta(path))
    return _SHAPES[key]


_LIMIT = []


def gpu_limit_bytes():
    if not _LIMIT:
        from gguf_shape import gpu_limit
        _LIMIT.append(gpu_limit()[0])
    return _LIMIT[0]


def custom_defaults(path):
    """Starting tune for a model that is not in the catalogue, from its GGUF header."""
    t = {"kv": "q4_0", "ctx": 65536, "slots": "auto", "temp": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0, "presence": 0}
    info = {"arch": "dense", "mtp": False, "quant": "?"}
    try:
        shp = shape_of(path)
        info.update(arch="moe" if shp.get("experts") else "dense", mtp=bool(shp.get("nextn")), quant=shp.get("ftype", "?"),
                    ctx_train=shp.get("ctx_train"))
        # MTP + n-gram at n=1 measured best on Q4 and IQ3 alike; without an MTP head, n-gram only
        t["spec"], t["spec_n"] = ("draft-mtp,ngram-mod", 1) if info["mtp"] else ("ngram-mod", 2)
        t["ctx"] = min(98304 if info["arch"] == "moe" else 65536, shp.get("ctx_train") or 262144)
    except Exception:
        t["spec"], t["spec_n"] = "ngram-mod", 2
    return t, info


def all_models(cfg=None):
    """Every model CARL knows: the catalogue, plus each .gguf in the models folder and
    each custom download. One dict per model, with status, path and the effective tune."""
    cfg = cfg or load_config()
    mdir, cat, db = models_dir(cfg), load_catalog(), load_local()
    out, seen_files = [], set()
    for m in cat["models"]:
        path = os.path.join(mdir, m["hf"]["file"])
        seen_files.add(m["hf"]["file"])
        out.append({**m, "source": "catalog", "path": path, "bytes": m["hf"]["bytes"],
                    "status": _status(path, m["hf"]["bytes"]), "local": db["models"].get(m["name"], {})})
    for name, e in db["models"].items():
        if any(x["name"] == name for x in out) or not e.get("path"):
            continue
        path = os.path.expanduser(e["path"])
        seen_files.add(os.path.basename(path)) if os.path.dirname(path) == mdir else None
        out.append(_custom_entry(name, path, e))
    try:
        files = sorted(f for f in os.listdir(mdir) if f.endswith(".gguf") and not _is_part(f))
    except OSError:
        files = []
    for fn in files:
        if fn not in seen_files:
            name = re.sub(r"[^a-z0-9._-]+", "-", fn[:-5].lower())
            out.append(_custom_entry(name, os.path.join(mdir, fn), {"source": "file"}))
    return out


def _custom_entry(name, path, e):
    have = os.path.exists(path)
    hf = e.get("hf") or {}
    st = _status(path, hf.get("bytes")) if hf else ("downloaded" if have else "missing")
    return {"name": name, "label": e.get("label") or os.path.basename(path), "source": e.get("source", "file"),
            "path": path, "bytes": os.path.getsize(path) if have else hf.get("bytes", 0), "status": st, "hf": hf,
            "alias": e.get("alias") or name, "summary": e.get("summary") or
            ("Custom model from Hugging Face" if e.get("source") == "hf" else "Custom model (found in the models folder)"),
            "description": e.get("description", ""), "tune": {}, "why": {}, "ctx_zones": None, "measured": [],
            "local": e, "custom": True}


def find(name, models=None):
    models = models or all_models()
    return next((m for m in models if m["name"] == name), None) or next(
        (m for m in models if os.path.basename(m["path"]) == name or m["path"] == os.path.expanduser(name)), None)


def effective_tune(m, cfg=None):
    """(settings, source per key): built-ins < catalogue < Auto-tune (this Mac) < config profile."""
    cfg = cfg or load_config()
    vals = {k: s["default"] for k, s in MODEL_KEYS.items()}
    src = {k: "default" for k in vals}
    base = m.get("tune") or {}
    if m.get("custom") and m["status"] == "downloaded":
        base = {**custom_defaults(m["path"])[0], **base}
    for k, v in base.items():
        if k in vals:
            vals[k], src[k] = v, "catalogue" if not m.get("custom") else "header"
    for k, v in ((m.get("local") or {}).get("tune") or {}).get("settings", {}).items():
        if k in vals:
            vals[k], src[k] = v, "auto-tune"
    for k, v in ((cfg.get("models") or {}).get(m["name"]) or {}).items():
        if k in vals:
            vals[k], src[k] = v, "config"
    vals["alias"] = vals["alias"] or m.get("alias") or m["name"]
    return vals, src


def ctx_zones(m):
    """Context per slot: (good, slow, very_slow). Auto-tune's measured zones win."""
    z = ((m.get("local") or {}).get("tune") or {}).get("ctx_zones") or m.get("ctx_zones") or \
        ({"good": 98304, "slow": 131072, "very_slow": 163840} if (m.get("arch") == "moe") else
         {"good": 65536, "slow": 98304, "very_slow": 131072})
    return z["good"], z["slow"], z["very_slow"]


def ctx_zone(m, ctx):
    good, slow, very = ctx_zones(m)
    return "good" if ctx <= good else "slow" if ctx <= slow else "very_slow"


def pick_default(models=None, ctx=98304):
    """This Mac's default: the catalogue default, or default_small when it can't hold one
    window; if that isn't downloaded, the first downloaded model."""
    from gguf_shape import OVERHEAD, kv_bytes_per_token, model_shape, remote_meta
    cat, models = load_catalog(), models or all_models()
    pick = cat["default"]
    m = find(pick, models)
    try:
        shp = shape_of(m["path"]) if m["status"] == "downloaded" else \
            model_shape(remote_meta(m["hf"]["repo"], m["hf"]["revision"], m["hf"]["file"]))
        need = m["bytes"] + kv_bytes_per_token(shp, "q4_0") * ctx + shp["rs_bytes"] + OVERHEAD
        if need > gpu_limit_bytes():
            pick = cat.get("default_small") or pick
    except Exception:
        pass
    return pick


def resolve_launch(name=None, cfg=None):
    """The model a llama.cpp start uses: name, else config llama.model, else this Mac's default;
    a default that isn't downloaded falls back to the first downloaded model."""
    cfg = cfg or load_config()
    models = all_models(cfg)
    explicit = name or ((cfg.get("llama") or {}).get("model") if (cfg.get("llama") or {}).get("model") not in (None, "", "auto") else None)
    if explicit:
        m = find(explicit, models)
        if not m:
            raise ConfigError(f"unknown model '{explicit}' (see: ./carl.sh models)")
        if m["status"] != "downloaded":
            raise ConfigError(f"{m['name']} is not downloaded (run: ./carl.sh download {m['name']})")
        return m, models, None
    want = pick_default(models)
    m = find(want, models)
    if m and m["status"] == "downloaded":
        return m, models, None
    have = [x for x in models if x["status"] == "downloaded"]
    if not have:
        raise ConfigError("no model is downloaded. Download this Mac's default: ./carl.sh download default")
    return have[0], models, f"default model {want} is not downloaded; using {have[0]['name']} (downloaded)"


def shell_lines(d):
    """KEY=value lines; values restricted to a safe character set (the shell reads them without eval)."""
    out = []
    for k, v in d.items():
        v = "1" if v is True else "0" if v is False else str(v)
        if not re.fullmatch(r"[A-Za-z0-9_.,:/~+@ =-]*", v):
            raise ConfigError(f"{k}: unsafe characters in {v!r}")
        out.append(f"{k}={v}")
    return "\n".join(out)


def launch_env(name=None, use_config=True):
    """Settings for serve-llama.sh (everything below flags and environment)."""
    cfg = load_config() if use_config else {"schema": SCHEMA}
    if name and ("/" in name or name.endswith(".gguf")) and os.path.exists(os.path.expanduser(name)):
        path = os.path.abspath(os.path.expanduser(name))
        m = find(path) or _custom_entry(os.path.basename(path)[:-5].lower(), path, {"source": "file"})
        note = None
    else:
        m, _, note = resolve_launch(name, cfg)
    vals, src = effective_tune(m, cfg)
    env = {"MODEL": m["path"], "MODEL_NAME": m["name"], "ALIAS": vals["alias"]}
    for k, s in MODEL_KEYS.items():
        if k != "alias" and "env" in s:
            env[s["env"]] = vals[k]
    for k, s in LLAMA_KEYS.items():
        v = (cfg.get("llama") or {}).get(k)
        if v is not None and "env" in s and v != "" and not (k == "cache_ram" and v == "auto"):
            env[s["env"]] = v
    extra = (cfg.get("llama") or {}).get("extra_args") or []
    if extra:
        env["EXTRA_ARGS"] = " ".join(extra)
    env["CARL_SOURCES"] = " ".join(f"{k}:{src[k]}" for k in ("ctx", "kv", "spec", "slots"))
    return env, note


def mtplx_env():
    cfg = load_config()
    sec = cfg.get("mtplx") or {}
    env = {}
    for k, s in MTPLX_KEYS.items():
        if "env" in s and sec.get(k) not in (None, ""):
            env[s["env"]] = sec[k]
    return env


# ---------------------------------------------------------------- downloads
def hf_api(path):
    req = urllib.request.Request(f"https://huggingface.co/api/{path}", headers={"User-Agent": "carl"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def hf_files(repo, revision="main"):
    """[(file, bytes, sha256)] of the GGUF files in a Hugging Face repo (first parts only)."""
    tree = hf_api(f"models/{repo}/tree/{urllib.parse.quote(revision)}?recursive=true")
    return [(f["path"], f.get("size", 0), (f.get("lfs") or {}).get("oid", ""))
            for f in tree if f.get("type") == "file" and f["path"].endswith(".gguf") and not _is_part(os.path.basename(f["path"]))]


def parse_hf(spec):
    """hf:REPO/FILE.gguf, REPO/FILE.gguf, or a huggingface.co URL -> (repo, file, revision)."""
    s = spec[3:] if spec.startswith("hf:") else spec
    m = re.match(r"https?://huggingface\.co/([^/]+/[^/]+)/(?:blob|resolve)/([^/]+)/(.+?)(?:\?.*)?$", s)
    if m:
        return m.group(1), urllib.parse.unquote(m.group(3)), m.group(2)
    parts = s.split("/")
    if len(parts) >= 3 and s.endswith(".gguf"):
        return "/".join(parts[:2]), "/".join(parts[2:]), "main"
    if len(parts) == 2:
        return s, None, "main"
    raise ConfigError(f"not a Hugging Face model: {spec} (use hf:OWNER/REPO/FILE.gguf)")


def human(b):
    return f"{b / 1e9:.1f} GB"


def sha256_file(path, progress=None):
    h, done, total = hashlib.sha256(), 0, os.path.getsize(path)
    with open(path, "rb") as f:
        while True:
            b = f.read(16 * 2 ** 20)
            if not b:
                break
            h.update(b); done += len(b)
            if progress:
                progress(done, total)
    return h.hexdigest()


def verify(m, quiet=False):
    want = (m.get("hf") or {}).get("sha256")
    if m["status"] != "downloaded" and not os.path.exists(m["path"]):
        print(f"  {m['name']}: not downloaded", file=sys.stderr); return False
    if not want:
        print(f"  {m['name']}: no checksum known (a local file): skipped"); return True
    print(f"  {m['name']}: verifying sha256 ({human(os.path.getsize(m['path']))})...", flush=True)
    got = sha256_file(m["path"])
    if got == want:
        db = load_local()
        db["models"].setdefault(m["name"], {})["verified"] = time.strftime("%Y-%m-%d")
        if m.get("custom"):
            save_local(db)
        print(f"  {m['name']}: OK {got}"); return True
    print(f"  {m['name']}: MISMATCH got {got} want {want}", file=sys.stderr); return False


def download(m, mdir):
    hf = m["hf"]
    path = os.path.join(mdir, os.path.basename(hf["file"]))
    os.makedirs(mdir, exist_ok=True)
    if _status(path, hf.get("bytes")) == "downloaded":
        print(f"== {m['name']} already downloaded: {path}")
        return verify({**m, "path": path, "status": "downloaded"})
    free = shutil.disk_usage(mdir).free
    if free < hf["bytes"] + 5 * 1e9 - (os.path.getsize(path) if os.path.exists(path) else 0):
        print(f"error: {m['name']} needs {human(hf['bytes'])} + 5 GB headroom; only {human(free)} free", file=sys.stderr)
        return False
    url = f"https://huggingface.co/{hf['repo']}/resolve/{hf.get('revision', 'main')}/{urllib.parse.quote(hf['file'])}"
    print(f"== {m['name']}: {human(hf['bytes'])} -> {path}", flush=True)
    if shutil.which("aria2c"):
        rc = subprocess.call(["aria2c", "-x16", "-s16", "-k1M", "--continue=true", "--file-allocation=none",
                              "--summary-interval=30", "--console-log-level=warn", "-d", mdir, "-o", os.path.basename(path), url])
    else:
        rc = subprocess.call(["curl", "-fL", "-C", "-", "--progress-bar", "-o", path, url])
    if rc != 0 or not os.path.exists(path):
        print(f"error: download failed (exit {rc}); run it again to resume", file=sys.stderr); return False
    if hf.get("bytes") and os.path.getsize(path) != hf["bytes"]:
        print(f"error: size mismatch for {path}", file=sys.stderr); return False
    ok = verify({**m, "path": path, "status": "downloaded"})
    if not ok:
        os.replace(path, path + ".bad"); print(f"error: bad checksum; moved to {path}.bad", file=sys.stderr)
    return ok


def download_hf(spec, name=None):
    """Any GGUF from Hugging Face: resolve the revision, size and sha256, record it in models.json, download."""
    repo, file, rev = parse_hf(spec)
    if not file:
        print(f"{repo}: pick a file (./carl.sh download hf:{repo}/FILE.gguf):")
        for f, b, _ in hf_files(repo, rev):
            print(f"  {f:60} {human(b)}")
        return False
    info = hf_api(f"models/{repo}/revision/{urllib.parse.quote(rev)}")
    sha_rev = info.get("sha") or rev
    match = [x for x in hf_files(repo, sha_rev) if x[0] == file]
    if not match:
        raise ConfigError(f"{repo} has no file {file}")
    _, size, sha = match[0]
    name = name or re.sub(r"[^a-z0-9._-]+", "-", os.path.basename(file)[:-5].lower())
    mdir = models_dir()
    m = {"name": name, "hf": {"repo": repo, "revision": sha_rev, "file": file, "sha256": sha, "bytes": size}}
    db = load_local()
    db["models"][name] = {**db["models"].get(name, {}), "source": "hf", "hf": m["hf"],
                          "path": os.path.join(mdir, os.path.basename(file)), "added": time.strftime("%Y-%m-%d")}
    save_local(db)
    return download(m, mdir)


def delete(m):
    for p in (m["path"], m["path"] + ".aria2", m["path"] + ".bad"):
        if os.path.exists(p):
            os.remove(p)
    db = load_local()
    if m.get("custom"):
        db["models"].pop(m["name"], None)
    elif m["name"] in db["models"]:
        db["models"][m["name"]].pop("verified", None)
    save_local(db)


# ---------------------------------------------------------------- CLI
def cmd_list(models):
    cfg = load_config()
    default = load_catalog()["default"]
    print(f"{'NAME':28} {'SIZE':>8}  {'STATUS':11} {'SOURCE':8} SUMMARY")
    for m in models:
        tuned = " [auto-tuned]" if (m.get("local") or {}).get("tune") else ""
        mark = " [default]" if m["name"] == default else ""
        print(f"{m['name']:28} {human(m['bytes']):>8}  {m['status']:11} {m['source']:8} {m.get('summary', '')}{mark}{tuned}")
    d = models_dir(cfg)
    print(f"\ndir: {d}   free: {human(shutil.disk_usage(d).free) if os.path.isdir(d) else '?'}")
    print("download any GGUF: ./carl.sh download hf:OWNER/REPO/FILE.gguf   (files: ./carl.sh download hf:OWNER/REPO)")


def cmd_config(argv):
    sub = argv[0] if argv else "show"
    cfg = load_config()
    if sub == "path":
        print(CONFIG_FILE)
    elif sub == "show":
        print(f"# {CONFIG_FILE}")
        print(json.dumps(cfg, indent=2))
        print("\n# keys (section.key: type, default)")
        for sec, keys in SECTIONS.items():
            for k, s in keys.items():
                print(f"  {sec}.{k}: {s['type']}{' ' + '|'.join(s['choices']) if 'choices' in s else ''}, default {s['default']!r}")
        for k, s in MODEL_KEYS.items():
            print(f"  models.NAME.{k}: {s['type']}{' ' + '|'.join(s['choices']) if 'choices' in s else ''}")
    elif sub in ("get", "set", "unset") and len(argv) >= 2:
        parts = argv[1].split(".")
        if parts[0] == "models" and len(parts) > 3:           # model names may contain dots
            parts = ["models", ".".join(parts[1:-1]), parts[-1]]
        node = cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {}) if sub == "set" else (node or {}).get(p, {})
        if sub == "get":
            print(json.dumps(node.get(parts[-1])))
        elif sub == "set":
            node[parts[-1]] = argv[2] if len(argv) == 3 else argv[2:]
            node = save_config(cfg)
            for p in parts:
                node = node.get(p, {})
            print(f"{argv[1]} = {json.dumps(node)}")
        else:
            node.pop(parts[-1], None); save_config(cfg)
    else:
        raise ConfigError("usage: carl.py config [show|path|get KEY|set KEY VALUE|unset KEY]")


def main(argv):
    cmd = argv[0] if argv else "list"
    a = argv[1:]
    if cmd in ("list", "ls"):
        cmd_list(all_models())
    elif cmd in ("download", "dl"):
        if not a:
            raise ConfigError("usage: download NAME...|default|all|hf:OWNER/REPO/FILE.gguf")
        models = all_models()
        if a == ["all"]:
            a = [m["name"] for m in models if m["source"] == "catalog"]
        ok = True
        for n in a:
            if n == "default":
                n = pick_default(models); print(f"== default model for this Mac: {n}")
            if n.startswith(("hf:", "http")) or (n.count("/") >= 1 and not find(n, models)):
                ok = download_hf(n) and ok; continue
            m = find(n, models)
            if not m or not m.get("hf"):
                raise ConfigError(f"unknown model '{n}' (see: ./carl.sh models)")
            ok = download(m, models_dir()) and ok
        return 0 if ok else 1
    elif cmd == "hf-files":
        for f, b, _ in hf_files(a[0]):
            print(f"{f}\t{b}")
    elif cmd == "verify":
        models = all_models()
        a = a or [m["name"] for m in models if m["status"] == "downloaded"]       # no names: every downloaded model
        return 0 if all([verify(find(n, models) or {"name": n, "status": "missing", "path": ""}) for n in a]) else 1
    elif cmd == "delete":
        m = find(a[0])
        if not m:
            raise ConfigError(f"unknown model '{a[0]}'")
        delete(m); print(f"deleted {m['path']}")
    elif cmd == "path":
        m = find(a[0])
        if not m:
            raise ConfigError(f"unknown model '{a[0]}' (see: ./carl.sh models)")
        if m["status"] != "downloaded":
            raise ConfigError(f"{a[0]} not downloaded (run: ./carl.sh download {a[0]})")
        print(m["path"])
    elif cmd == "get":
        m = find(a[0])
        if not m:
            raise ConfigError(f"unknown model '{a[0]}'")
        vals = effective_tune(m)[0]
        f = {"repo": m["hf"].get("repo"), "rev": m["hf"].get("revision"), "file": m["hf"].get("file"),
             "sha256": m["hf"].get("sha256"), "bytes": m["bytes"], "alias": vals["alias"],
             "spec": f"{vals['spec']}:{vals['spec_n']}", "notes": m.get("summary"), "name": m["name"]}
        print(f.get(a[1], ""))
    elif cmd == "default":
        print(pick_default())
    elif cmd == "downloaded":
        for m in all_models():
            if m["status"] == "downloaded":
                print(m["name"])
    elif cmd == "launch-env":
        name = a[a.index("--model") + 1] if "--model" in a else None
        env, note = launch_env(name, use_config="--no-config" not in a)
        if note:
            print(note, file=sys.stderr)
        print(shell_lines(env))
    elif cmd == "mtplx-env":
        print(shell_lines(mtplx_env()))
    elif cmd == "config":
        cmd_config(a)
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        raise ConfigError(f"unknown command '{cmd}' (carl.py --help)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]) or 0)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

"""The models CARL knows and the settings each one starts with.

Models: the catalogue, each custom download in models.json, and each .gguf in the models
folder. Settings precedence for a llama.cpp start (below flags and environment, which the
launcher applies): config.json profile > Auto-tune result for this Mac > catalogue tune (or,
for a custom model, defaults from its GGUF header) > built-in defaults.
"""
from __future__ import annotations

import os
from typing import Dict, List, Literal, Mapping, Optional, Set, Tuple, cast

from .cards import apply_card
from .errors import ConfigError
from .gguf import ModelShape, ctx_train
from .hf import is_extra_part, local_file_name, model_name
from .ports import ModelFolder
from .settings import MODEL_KEYS, Config
from .types import (Catalog, CtxZones, CustomInfo, HfRef, LocalDb, LocalEntry, ModelInfo, SettingSource,
                    Settings, Status, Zone)

# 96K per slot is the smallest window people can work with: a slower cold read at 96K
# never lowers the default or colours it as slow; only larger windows get the warning.
CTX_FLOOR = 98304
DEFAULT_ZONES: CtxZones = {"good": 98304, "slow": 131072, "very_slow": 163840}
CUSTOM_CTX = 98304
PART_SUFFIX, BAD_SUFFIX = ".aria2", ".bad"      # aria2c's resume file; a file that failed its checksum
# Where a model's MTP speculation (llama.cpp draft-mtp) comes from: a head in the model file
# (Qwen: GGUF nextn_predict_layers > 0), a separate drafter file (Gemma 4: catalogue "draft",
# llama-server -md), or nowhere.
MtpSource = Literal["head", "drafter", "none"]


def status_of(path: str, want_bytes: Optional[int], files: ModelFolder) -> Status:
    """downloaded, partial (a resume file, or the wrong size) or missing."""
    if files.exists(path + PART_SUFFIX) or (files.exists(path) and want_bytes and files.size(path) != want_bytes):
        return "partial"
    return "downloaded" if files.exists(path) else "missing"


def custom_entry(name: str, path: str, entry: LocalEntry, files: ModelFolder) -> ModelInfo:
    """A model that is not in the catalogue: a Hugging Face download or a file in the folder."""
    have = files.exists(path)
    hf: HfRef = entry.get("hf") or {}
    status: Status = status_of(path, hf.get("bytes"), files) if hf else ("downloaded" if have else "missing")
    summary = entry.get("summary") or ("Custom model from Hugging Face" if entry.get("source") == "hf"
                                       else "Custom model (found in the models folder)")
    return {"name": name, "label": entry.get("label") or os.path.basename(path), "source": entry.get("source", "file"),
            "path": path, "bytes": files.size(path) if have else hf.get("bytes", 0), "status": status, "hf": hf,
            "summary": summary, "description": entry.get("description", ""),
            "tune": {}, "why": {}, "ctx_zones": None, "measured": [], "local": entry, "custom": True}


def expand_home(path: str, home: str) -> str:
    """~/x -> HOME/x (models.json may store paths relative to the home folder)."""
    return home + path[1:] if path == "~" or path.startswith("~/") else path


def adoptions(catalog: Catalog, db: LocalDb, models_dir: str, home: str) -> Dict[str, str]:
    """Custom models a catalogue entry now names (the same file in the models folder):
    {custom name: catalogue name}. The catalogue entry takes them over (adopt)."""
    files = {local_file_name((m.get("hf") or {}).get("file", "")): m.get("name", "") for m in catalog.get("models", [])
             if (m.get("hf") or {}).get("file")}
    names = {m.get("name") for m in catalog.get("models", [])}
    out: Dict[str, str] = {}
    for name, e in db["models"].items():
        path = expand_home(e.get("path", ""), home)
        if name not in names and path and os.path.dirname(path) == models_dir and os.path.basename(path) in files:
            out[name] = files[os.path.basename(path)]
    return out


def adopt(db: LocalDb, renames: Mapping[str, str]) -> List[str]:
    """Move adopted custom models' Auto-tune results (and verification date) to their catalogue
    name, unless it has its own, and drop the custom entries (their cards: the catalogue's
    card applies now). Changes db; returns a note per model."""
    notes = []
    for old, new in renames.items():
        e = db["models"].pop(old, None) or {}
        target = db["models"].setdefault(new, {})
        moved = [k for k in ("tune", "verified") if k in e and k not in target]
        for k in moved:
            target[k] = e[k]
        if not target:
            db["models"].pop(new)
        notes.append(f"{old} is now the catalogue model {new}" + (f" (its {' and '.join(moved)} moved there)"
                                                                     if moved else "")
                     + ("; your card for it no longer applies: the catalogue's card does" if e.get("card") else ""))
    return notes


def build_models(catalog: Catalog, db: LocalDb, models_dir: str, files: ModelFolder, home: str) -> List[ModelInfo]:
    """Every model CARL knows: the catalogue, each custom download, then each other .gguf
    in the models folder (split parts, vision projectors and the catalogue's MTP drafters left
    out). A catalogue model with a drafter knows its file and status (draft_path, draft_status).
    A custom model's card from models.json is joined into its record."""
    out: List[ModelInfo] = []
    seen_files: Set[str] = set()
    for m in catalog.get("models", []):
        hf = m.get("hf") or {}
        file = local_file_name(hf.get("file", ""))
        path = os.path.join(models_dir, file)
        seen_files.add(file)
        info = cast(ModelInfo, dict(m))          # a catalogue entry plus where it is
        info.update({"source": "catalog", "path": path, "bytes": hf.get("bytes", 0),
                     "status": status_of(path, hf.get("bytes"), files), "local": db["models"].get(m.get("name", ""), {})})
        draft = m.get("draft")
        if draft:
            dfile = local_file_name(draft.get("file", ""))
            dpath = os.path.join(models_dir, dfile)
            seen_files.add(dfile)                # a drafter is not a model of its own
            info.update({"draft_path": dpath, "draft_status": status_of(dpath, draft.get("bytes"), files)})
        out.append(info)
    names = {m["name"] for m in out}
    catalogue_files = set(seen_files)
    for name, e in db["models"].items():
        if name in names or not e.get("path"):
            continue
        path = expand_home(e.get("path", ""), home)
        if os.path.dirname(path) == models_dir and os.path.basename(path) in catalogue_files:
            continue                        # a catalogue entry names this file now (adoptions)
        if os.path.dirname(path) == models_dir:
            seen_files.add(os.path.basename(path))
        out.append(custom_entry(name, path, e, files))
    for fn in sorted(f for f in files.gguf_names(models_dir) if not is_extra_part(f)):
        if fn not in seen_files:
            out.append(custom_entry(model_name(fn), os.path.join(models_dir, fn), {"source": "file"}, files))
    names = {m.get("name", "") for m in out}
    for m in out:
        card = (m.get("local") or {}).get("card") if m.get("custom") else None
        if card:
            apply_card(m, card, names)
    return out


def draft_bytes(m: ModelInfo) -> int:
    """The weights of a model's MTP drafter (0 without one). Fit counts them with the model's
    whenever the catalogue names a drafter: a start with MTP loads it, and a download fetches it."""
    return int((m.get("draft") or {}).get("bytes", 0)) if m.get("draft") else 0


def has_drafter(m: ModelInfo) -> bool:
    """The model's drafter is downloaded (a start can pass it with -md)."""
    return bool(m.get("draft")) and m.get("draft_status") == "downloaded"


def mtp_source(m: ModelInfo, shape: Optional[ModelShape]) -> MtpSource:
    """Where this model's MTP speculation comes from: the head in its file wins over a drafter
    (llama.cpp needs no -md then)."""
    if shape is not None and shape["nextn"] > 0:
        return "head"
    return "drafter" if has_drafter(m) else "none"


def spec_without_mtp(spec: str) -> str:
    """The speculation a start falls back to when MTP is not available: n-gram (MTP + n-gram
    keeps its n-gram part; MTP alone becomes n-gram)."""
    return "ngram-mod" if "draft-mtp" in spec.split(",") else spec


def mtp_fallback(m: ModelInfo, spec: str, source: MtpSource) -> Tuple[str, Optional[str]]:
    """(the speculation a start uses, a note when it differs): MTP needs a head in the file or
    a downloaded drafter; without either the start uses n-gram and says so once."""
    if source != "none" or "draft-mtp" not in spec.split(","):
        return spec, None
    name = m.get("name", "")
    why = (f"its MTP drafter is not downloaded (./carl.sh download {name} downloads it)" if m.get("draft")
           else "its file has no MTP head")
    return spec_without_mtp(spec), f"{name}: the speculation is n-gram only, because {why}."


def find_model(models: List[ModelInfo], name: str, as_path: str) -> Optional[ModelInfo]:
    """A model by name, else by file name or full path (as_path: name with ~ expanded)."""
    return next((m for m in models if m.get("name") == name), None) or next(
        (m for m in models if os.path.basename(m.get("path", "")) == name or m.get("path") == as_path), None)


def custom_defaults(shape: Optional[ModelShape]) -> Tuple[Settings, CustomInfo]:
    """Starting tune for a model that is not in the catalogue, from its GGUF header (None:
    the header couldn't be read)."""
    tune: Settings = {"kv": "q4_0", "ctx": CUSTOM_CTX, "slots": "auto", "temp": 1.0, "top_p": 0.95, "top_k": 20,
                      "min_p": 0, "presence": 0}
    info: CustomInfo = {"arch": "dense", "mtp": False, "quant": "?"}
    if shape is None:
        tune["spec"], tune["spec_n"] = "ngram-mod", 2
        return tune, info
    info = {"arch": "moe" if shape["experts"] else "dense", "mtp": bool(shape["nextn"]), "quant": shape["ftype"],
            "ctx_train": shape["ctx_train"], "thinking": "effort" if shape.get("effort_levels") else "on-off"}
    # MTP + n-gram at n=1 measured best on Q4 and IQ3 alike; without an MTP head, n-gram only
    tune["spec"], tune["spec_n"] = ("draft-mtp,ngram-mod", 1) if info["mtp"] else ("ngram-mod", 2)
    tune["ctx"] = min(CUSTOM_CTX, ctx_train(shape))
    return tune, info


def effective_tune(m: ModelInfo, cfg: Config,
                   header: Optional[Settings] = None) -> Tuple[Settings, Dict[str, SettingSource]]:
    """(settings, source per key): built-ins < catalogue (custom: header) < Auto-tune (this
    Mac) < config profile. `header` is custom_defaults() of a downloaded custom model."""
    vals: Settings = {k: s.default for k, s in MODEL_KEYS.items()}
    src: Dict[str, SettingSource] = {k: "default" for k in vals}
    custom = bool(m.get("custom"))
    base: Settings = dict(m.get("tune") or {})
    if custom and m.get("status") == "downloaded" and header is not None:
        base = {**header, **base}
    layers: List[Tuple[Settings, SettingSource]] = [
        (base, "header" if custom else "catalogue"),
        (((m.get("local") or {}).get("tune") or {}).get("settings", {}), "auto-tune"),
        (cfg.profile(m.get("name", "")), "config"),
    ]
    for layer, source in layers:
        for k, v in layer.items():
            if k in vals:
                vals[k], src[k] = v, source
    vals["alias"] = vals["alias"] or m.get("name", "")      # served under its own name (unique)
    return vals, src


def tune_advice(m: ModelInfo, vals: Settings, src: Dict[str, SettingSource]) -> Optional[str]:
    """A note for a start whose window is below the floor only because the catalogue sized
    it for smaller Macs (no Auto-tune result here, no user choice): Auto-tune finds this
    Mac's window."""
    ctx = vals.get("ctx")
    if src.get("ctx") in ("auto-tune", "config") or not isinstance(ctx, int) or ctx >= CTX_FLOOR:
        return None
    name = m.get("name", "")
    return (f"{name} starts with a context of {ctx // 1024}K tokens (the catalogue sized it for smaller Macs). "
            f"To find the context for this Mac, run ./carl.sh tune {name} (or use Settings > Auto-tune).")


def ctx_zones(m: ModelInfo) -> Tuple[int, int, int]:
    """Context per slot: (good, slow, very_slow). Auto-tune's measured zones win over the
    catalogue's; the good zone always reaches at least CTX_FLOOR."""
    z: CtxZones = ((m.get("local") or {}).get("tune") or {}).get("ctx_zones") or m.get("ctx_zones") or DEFAULT_ZONES
    good = max(z["good"], CTX_FLOOR)
    slow = max(z["slow"], good)
    return good, slow, max(z["very_slow"], slow)


def ctx_zone(m: ModelInfo, ctx: int) -> Zone:
    good, slow, _ = ctx_zones(m)
    return "good" if ctx <= good else "slow" if ctx <= slow else "very_slow"


def configured_model(cfg: Config) -> Optional[str]:
    """config.json llama.model, None for auto (this Mac's default)."""
    v = cfg.llama.get("model")
    return v if isinstance(v, str) and v not in ("", "auto") else None


def select_named(models: List[ModelInfo], name: str, as_path: str) -> ModelInfo:
    """The model a start names: it must exist and be downloaded."""
    m = find_model(models, name, as_path)
    if not m:
        raise ConfigError(f"unknown model '{name}'. ./carl.sh models lists the models.")
    if m.get("status") != "downloaded":
        raise ConfigError(f"{m.get('name')} is not downloaded. To download it: ./carl.sh download {m.get('name')}")
    return m

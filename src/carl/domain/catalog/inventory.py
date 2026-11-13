"""The models CARL knows and the settings each one starts with.

Models: the catalogue, each custom download in models.json, and each .gguf in the models
folder. Settings precedence for a llama.cpp start (below flags and environment, which the
launcher applies): config.json profile > Auto-tune result for this Mac > catalogue tune (or,
for a custom model, defaults from its GGUF header) > built-in defaults.

Paths are text here (domain/shared/paths.py PathPolicy): each is read in the flavour it is written in, so the
same rules hold for a Mac's folders and a Windows one's.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Literal, Mapping, Optional, Protocol, Sequence, Set, Tuple, cast

from .cards import apply_card
from ..shared.constants import DEFAULT_CTX, DRAFT_MARGIN, MIN_P, PRESENCE, TEMP, TOP_K, TOP_P
from ..shared.errors import ConfigError
from ..memory.gguf import ModelShape, ctx_train
from ..memory.shape import has_head
from .hf import is_extra_part, local_file_name, model_name
from .quants import ModelGroup, default_quant, group_models, matches, quant_names, split_spec
from .drafts import KIND_WORDS, MTP_FILE, SPEC_TYPE, draft_of_kind, drafts_of, spec_kinds, vision_of
from .layout import (DRAFTER_KINDS, PART_SUFFIX, SECTIONS, VISION, in_models, in_place, is_drafter, locate,
                     locate_beside, names_model, relocate, same_file, split_file)
from ..llama.spec_support import supports
from ..llama.version import LlamaVersion
from ..shared.paths import expand_home, policy_for
from ..shared.platform_words import MACOS, PlatformWords
from . import thinking
from ..tune.provenance import measured_kv
from ..config.settings import MODEL_KEYS, Config
from ..shared.types import (Catalog, CtxZones, CustomInfo, DraftInfo, HfRef, LocalDb, LocalEntry, ModelInfo,
                            SettingSource, Settings, Status, Zone)

# 96K per slot is the smallest window people can work with: a slower cold read at 96K
# never lowers the default or colours it as slow; only larger windows get the warning.
CTX_FLOOR = DEFAULT_CTX
DEFAULT_ZONES: CtxZones = {"good": CTX_FLOOR, "slow": 131072, "very_slow": 163840}
CUSTOM_CTX = DEFAULT_CTX
BAD_SUFFIX = ".bad"                             # a file that failed its checksum (PART_SUFFIX: layout.py)
# Where a model's MTP speculation (llama.cpp draft-mtp) comes from: a head in the model file
# (Qwen: GGUF nextn_predict_layers > 0), a separate drafter file (Gemma 4: catalogue "draft",
# llama-server -md), or nowhere.
MtpSource = Literal["head", "drafter", "none"]


class ModelFiles(Protocol):
    """What the inventory reads of the model files: the part of the ModelFolder port it uses (ports/models.py).
    The domain imports no port (rule R1), so it names that part here."""

    def exists(self, path: str) -> bool: ...

    def size(self, path: str) -> int: ...

    def gguf_names(self, directory: str) -> List[str]: ...

    def subdirs(self, directory: str) -> List[str]: ...


def status_of(path: str, want_bytes: Optional[int], files: ModelFiles) -> Status:
    """downloaded, partial (a resume file, or the wrong size) or missing."""
    if files.exists(path + PART_SUFFIX) or (files.exists(path) and want_bytes and files.size(path) != want_bytes):
        return "partial"
    return "downloaded" if files.exists(path) else "missing"


def custom_entry(name: str, path: str, entry: LocalEntry, files: ModelFiles) -> ModelInfo:
    """A model that is not in the catalogue: a Hugging Face download or a file in the folder."""
    have = files.exists(path)
    hf: HfRef = entry.get("hf") or {}
    status: Status = status_of(path, hf.get("bytes"), files) if hf else ("downloaded" if have else "missing")
    summary = entry.get("summary") or ("Custom model from Hugging Face" if entry.get("source") == "hf"
                                       else "Custom model (found in the models folder)")
    pp = policy_for(path)
    info: ModelInfo = {"name": name, "label": entry.get("label") or pp.basename(path),
                       "source": entry.get("source", "file"), "path": path,
                       "bytes": files.size(path) if have else hf.get("bytes", 0), "status": status, "hf": hf,
                       "summary": summary, "description": entry.get("description", ""),
                       "tune": {}, "why": {}, "ctx_zones": None, "measured": [], "local": entry, "custom": True}
    draft = entry.get("draft")
    if draft:                                # a Gemma 4 drafter CARL matched to it (domain/drafters.py)
        dpath = locate_beside(path, local_file_name(draft.get("file", "")), files)
        dstatus = status_of(dpath, draft.get("bytes"), files)
        info.update({"draft": draft, "draft_path": dpath, "draft_status": dstatus,
                     "drafts": [{**draft, "kind": MTP_FILE, "path": dpath, "status": dstatus,
                                 "spec": SPEC_TYPE[MTP_FILE]}]})
    return info


def _beside(info: ModelInfo, m: Mapping[str, object], path: str, models_dir: str, files: ModelFiles,
            seen: Set[str]) -> None:
    """The files beside a catalogue model, where they are (WCATX): each drafter in the folder of its kind (info["drafts"]:
    its path, status and --spec-type; the MTP drafter also as draft, draft_path and draft_status, the fields the
    older code reads) and the vision projector in vision/ (vision_path, vision_status). Their names are added to
    `seen`: a drafter or a projector is not a model of its own."""
    found: List[DraftInfo] = []
    for d in drafts_of(m):
        kind = d.get("kind", MTP_FILE)
        dfile = local_file_name(d.get("file", ""))
        dpath = locate_beside(path, dfile, files, models_dir, DRAFTER_KINDS[kind][0])
        seen.add(dfile)
        found.append({**d, "kind": kind, "path": dpath, "status": status_of(dpath, d.get("bytes"), files),
                      "spec": SPEC_TYPE[kind]})
    if found:
        info["drafts"] = found
        mtp = next((d for d in found if d["kind"] == MTP_FILE), None)
        if mtp is not None:
            info.update({"draft": cast(HfRef, {k: v for k, v in mtp.items() if k in HfRef.__annotations__}),
                         "draft_path": mtp.get("path", ""), "draft_status": mtp.get("status", "missing")})
    vision = vision_of(m)
    if vision:
        vfile = local_file_name(vision.get("file", ""))
        vpath = locate_beside(path, vfile, files, models_dir, VISION)
        seen.add(vfile)
        info.update({"vision_path": vpath, "vision_status": status_of(vpath, vision.get("bytes"), files)})


def adoptions(catalog: Catalog, db: LocalDb, models_dir: str, home: str) -> Dict[str, str]:
    """Custom models a catalogue entry now names (the same file in the models folder, in its own folder or not):
    {custom name: catalogue name}. The catalogue entry takes them over (adopt)."""
    files = {local_file_name((m.get("hf") or {}).get("file", "")): m.get("name", "") for m in catalog.get("models", [])
             if (m.get("hf") or {}).get("file")}
    names = {m.get("name") for m in catalog.get("models", [])}
    out: Dict[str, str] = {}
    for name, e in db["models"].items():
        path = expand_home(e.get("path", ""), home)
        fn = in_models(path, models_dir) if path else None
        if name not in names and fn is not None and fn in files:
            out[name] = files[fn]
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


def build_models(catalog: Catalog, db: LocalDb, models_dir: str, files: ModelFiles, home: str) -> List[ModelInfo]:
    """Every model CARL knows: the catalogue, each custom download, then each other .gguf
    in the models folder (split parts, vision projectors and the catalogue's MTP drafters left
    out). A model's file is in its quant folder in the model's own folder, or in an older place (layout.py); the
    scan looks in all of them. A catalogue model with a drafter knows its file and status (draft_path, draft_status).
    A custom model's card from models.json is joined into its record."""
    out: List[ModelInfo] = []
    seen_files: Set[str] = set()
    pp = policy_for(models_dir)
    for m in catalog.get("models", []):
        hf = m.get("hf") or {}
        file = local_file_name(hf.get("file", ""))
        path = locate(models_dir, file, files)
        seen_files.add(file)
        info = cast(ModelInfo, dict(m))          # a catalogue entry plus where it is
        info.update({"source": "catalog", "path": path, "bytes": hf.get("bytes", 0),
                     "status": status_of(path, hf.get("bytes"), files), "local": db["models"].get(m.get("name", ""), {})})
        _beside(info, m, path, models_dir, files, seen_files)       # its drafters and projector: not models of their own
        out.append(info)
    names = {m["name"] for m in out}
    catalogue_files = set(seen_files)
    for name, e in db["models"].items():
        if name in names or not e.get("path"):
            continue
        path = expand_home(e.get("path", ""), home)
        fn = in_models(path, models_dir)
        if fn is not None and fn in catalogue_files:
            continue                        # a catalogue entry names this file now (adoptions)
        if fn is not None:
            path = relocate(path, models_dir, files)       # moved into its folder, or out of it
        if pp.basename(path).lower() in {f.lower() for f in seen_files}:
            continue                        # the same file under another name: a model shows once
        seen_files.add(pp.basename(path))
        if e.get("draft"):
            seen_files.add(local_file_name((e.get("draft") or {}).get("file", "")))   # not a model of its own
        out.append(custom_entry(name, path, e, files))
        names.add(name)
    taken = {f.lower() for f in seen_files}
    for path in scan_models(models_dir, files):
        fn = pp.basename(path)
        if fn.lower() in taken:
            continue                        # a file shows once, whatever its name or place
        taken.add(fn.lower())
        name = model_name(fn)
        out.append(custom_entry(name if name not in names else f"{name}-{len(names)}", path, {"source": "file"}, files))
        names.add(out[-1]["name"])
    names = {m.get("name", "") for m in out}
    for m in out:
        card = (m.get("local") or {}).get("card") if m.get("custom") else None
        if card:
            apply_card(m, card, names)
    return out


def scan_models(models_dir: str, files: ModelFiles) -> List[str]:
    """The model files in the models folder (decision A68): the .gguf files directly in it (the old flat layout), and
    those in a model's folder, in its quant folders or directly in it (layout.py). A folder is a model's only when
    the file in it has the folder's name for its model (split_file; a maker's prefix on the file is fine) or, in a quant
    folder, that folder's name for its quantization: r4/, bench-a3b/ and a user's own folders are not read. The folders of a model that hold no model (mtp/, vision/, template/, lora/) and hidden folders are not
    read either (they are mtp/, dflash/, dspark/, eagle3/, vision/, template/, imatrix/ and lora/). Split parts after the
    first, vision projectors and drafter files are left out. A file name that shows twice (the
    layout's place and an older one) shows once, at the layout's place. Sorted by file name."""
    pp = policy_for(models_dir)

    def of_model(model: str, quant: str, f: str) -> bool:
        """The file is the model folder's own: its name gives the folder's model, or (in a quant folder) the folder's
        quantization."""
        name, found = split_file(f)
        return not is_extra_part(f) and not is_drafter(f) \
            and (names_model(model, name) or (bool(quant) and found.lower() == quant.lower()))
    found = [pp.join(models_dir, f) for f in files.gguf_names(models_dir) if not is_extra_part(f) and not is_drafter(f)]
    for model in sorted(files.subdirs(models_dir)):
        if model.startswith("."):
            continue
        mdir = pp.join(models_dir, model)
        found += [pp.join(mdir, f) for f in files.gguf_names(mdir) if of_model(model, "", f)]
        for quant in sorted(files.subdirs(mdir)):
            if not quant.startswith(".") and quant.lower() not in SECTIONS:
                qdir = pp.join(mdir, quant)
                found += [pp.join(qdir, f) for f in files.gguf_names(qdir) if of_model(model, quant, f)]
    first: Dict[str, str] = {}
    for p in sorted(found, key=lambda p: (not in_place(p, models_dir), p)):      # the layout's place wins
        first.setdefault(pp.basename(p).lower(), p)
    return sorted(first.values(), key=lambda p: (pp.basename(p), p))


def flat_files(models: List[ModelInfo], models_dir: str) -> List[str]:
    """The file names of the downloaded models (and their drafters and projectors) that are in the models folder but not
    where the layout puts them (directly in it, or directly in the model's folder): the older layouts, which still work.
    `carl models` tells how to move them."""
    pp = policy_for(models_dir)
    out: List[str] = []
    for m in models:
        beside = [(str(d.get("path", "")), d.get("status")) for d in m.get("drafts", [])
                  if d.get("kind") != MTP_FILE or not m.get("draft_path")]
        for path, status in ((m.get("path", ""), m.get("status")), (m.get("draft_path", ""), m.get("draft_status")),
                             (m.get("vision_path", ""), m.get("vision_status")), *beside):
            if path and status == "downloaded" and in_models(path, models_dir) is not None \
                    and not in_place(path, models_dir):
                out.append(pp.basename(path))
    return out


def draft_bytes(m: ModelInfo) -> int:
    """The weights of a model's MTP drafter (0 without one). Fit counts them with the model's
    whenever the catalogue names a drafter: a start with MTP loads it, and a download fetches it."""
    draft = draft_of_kind(m, MTP_FILE)
    return int(draft.get("bytes", 0)) if draft else 0


def file_drafts(m: ModelInfo) -> List[DraftInfo]:
    """The drafters of a model that are a file of a kind other than MTP's (dflash, dspark, eagle3), cataloged: each
    with its path and status (build_models)."""
    return [d for d in drafts_of(m) if d.get("kind") != MTP_FILE]


def spec_drafter(m: ModelInfo, spec: str) -> Optional[DraftInfo]:
    """The downloaded drafter file that a --spec-type value uses, of a kind other than MTP's (a start passes it with
    -md); None when the speculation uses none, or the model has none of that kind downloaded."""
    for kind in spec_kinds(spec):
        d = next((x for x in file_drafts(m) if x.get("kind") == kind), None)
        if d is not None and d.get("status") == "downloaded":
            return d
    return None


def offered_drafts(m: ModelInfo, engine: Optional[LlamaVersion]) -> Tuple[List[DraftInfo], List[DraftInfo]]:
    """(the downloaded drafter files of the model that Auto-tune may try, the downloaded ones it may not): a kind
    other than MTP's is offered when the engine supports it (llama/spec_support.py) and not otherwise (A74: a kind
    the engine does not support is cataloged and downloadable, not used)."""
    got = [d for d in file_drafts(m) if d.get("status") == "downloaded"]
    return ([d for d in got if supports(d.get("spec", ""), engine)],
            [d for d in got if not supports(d.get("spec", ""), engine)])


def vision_ready(m: ModelInfo) -> bool:
    """The model has a vision projector and it is downloaded (a start can pass it with --mmproj)."""
    return bool(m.get("vision")) and m.get("vision_status") == "downloaded"


def vision_bytes(m: ModelInfo) -> int:
    """The size of the model's vision projector (0 without one): what a start with vision on loads beside the model, which
    the memory plan counts (domain/memory/fit.py, need_parts)."""
    return int((m.get("vision") or {}).get("bytes", 0))


def spec_draft_bytes(m: ModelInfo, spec: str) -> int:
    """The size of the drafter file that a speculation loads: that of its drafter of another kind (DFlash, DSpark,
    EAGLE-3) when it uses one, else the MTP drafter's (0 without one). The memory plan counts it."""
    for kind in spec_kinds(spec):
        d = next((x for x in file_drafts(m) if x.get("kind") == kind), None)
        if d is not None:
            return int(d.get("bytes", 0))
    return draft_bytes(m)


def has_drafter(m: ModelInfo) -> bool:
    """The model's drafter is downloaded (a start can pass it with -md)."""
    return bool(m.get("draft")) and m.get("draft_status") == "downloaded"


def mtp_source(m: ModelInfo, shape: Optional[ModelShape]) -> MtpSource:
    """Where this model's MTP speculation comes from: the head in its file wins over a drafter
    (llama.cpp needs no -md then)."""
    if shape is not None and has_head(shape):
        return "head"
    return "drafter" if has_drafter(m) else "none"


def spec_without_mtp(spec: str) -> str:
    """The speculation a start falls back to when MTP is not available: n-gram (MTP + n-gram
    keeps its n-gram part; MTP alone becomes n-gram)."""
    return "ngram-mod" if "draft-mtp" in spec.split(",") else spec


def mtp_fallback(m: ModelInfo, spec: str, source: MtpSource,
                 words: PlatformWords = MACOS) -> Tuple[str, Optional[str]]:
    """(the speculation a start uses, a note when it differs): MTP needs a head in the file or
    a downloaded drafter; without either the start uses n-gram and says so once. A drafter of another kind (DFlash,
    DSpark, EAGLE-3) needs its file: a kind whose file is not downloaded is left out of the speculation (n-gram stays)."""
    name = m.get("name", "")
    notes: List[str] = []
    for kind in spec_kinds(spec):
        if spec_drafter(m, SPEC_TYPE[kind]) is None:
            spec = ",".join(s for s in spec.split(",") if s != SPEC_TYPE[kind]) or "none"
            cataloged = any(d.get("kind") == kind for d in file_drafts(m))
            notes.append(f"{name}: the {KIND_WORDS[kind]} is not used, because "
                         + (f"it is not downloaded ({words.command} download {name} downloads it)." if cataloged
                            else "the model has none."))
    if source != "none" or "draft-mtp" not in spec.split(","):
        return spec, " ".join(notes) or None
    why = (f"its MTP drafter is not downloaded ({words.command} download {name} downloads it)" if m.get("draft")
           else "its file has no MTP head")
    notes.append(f"{name}: the speculation is n-gram only, because {why}.")
    return spec_without_mtp(spec), " ".join(notes)


def find_model(models: List[ModelInfo], name: str, as_path: str) -> Optional[ModelInfo]:
    """A model by name, else by file name or full path (as_path: name with ~ expanded), else by NAME:QUANT. A path
    names the model whether it gives the file's folder or the models folder itself (layout.py same_file). A model
    with several quantizations is named by the name of its first entry, which finds that entry; NAME:QUANT finds
    another."""
    found = next((m for m in models if m.get("name") == name), None) or next(
        (m for m in models if policy_for(m.get("path", "")).basename(m.get("path", "")) == name
         or m.get("path") == as_path), None) or next(
        (m for m in models if m.get("path") and same_file(m["path"], as_path)), None)
    if found is None and split_spec(name)[1]:
        base, quant = split_spec(name)
        group = group_of(models, base)
        hits = matches(group, quant) if group is not None else []
        found = hits[0] if len(hits) == 1 else None
    return found


def custom_defaults(shape: Optional[ModelShape], drafter: bool = False) -> Tuple[Settings, CustomInfo]:
    """Starting tune for a model that is not in the catalogue, from its GGUF header (None:
    the header couldn't be read). drafter: it has a recorded MTP drafter (a custom Gemma 4 model)."""
    tune: Settings = {"kv": "q4_0", "ctx": CUSTOM_CTX, "slots": "auto", "temp": TEMP, "top_p": TOP_P, "top_k": TOP_K,
                      "min_p": MIN_P, "presence": PRESENCE}
    info: CustomInfo = {"arch": "dense", "mtp": False, "quant": "?"}
    if shape is None:
        tune["spec"], tune["spec_n"] = "ngram-mod", 2
        return tune, info
    info = {"arch": "moe" if shape["experts"] else "dense", "mtp": has_head(shape), "quant": shape["ftype"],
            "ctx_train": shape["ctx_train"], "thinking": "effort" if shape.get("effort_levels") else "on-off"}
    # MTP + n-gram at n=1 measured best on Q4 and IQ3 alike (an MTP head); with a Gemma 4 drafter at n=2
    # (Phase 20: the best on the E4B, 12B and 26B-A4B); without either, n-gram only
    tune["spec"], tune["spec_n"] = (("draft-mtp,ngram-mod", 1) if info["mtp"] else
                                    ("draft-mtp,ngram-mod", 2) if drafter else ("ngram-mod", 2))
    tune["ctx"] = min(CUSTOM_CTX, ctx_train(shape))
    if shape.get("arch") == "gemma4":
        tune["top_k"] = 64                      # Google's Gemma 4 sampling: 1.0 / 0.95 / 64 for every use
    return tune, info


def effective_tune(m: ModelInfo, cfg: Config,
                   header: Optional[Settings] = None) -> Tuple[Settings, Dict[str, SettingSource]]:
    """(settings, source per key): built-ins < catalogue (custom: header) < Auto-tune (this
    Mac) < config profile. `header` is custom_defaults() of a downloaded custom model."""
    vals: Settings = {k: s.default for k, s in MODEL_KEYS.items()}
    src: Dict[str, SettingSource] = {k: "default" for k in vals}
    custom = bool(m.get("custom"))
    # the thinking of each role: its default from the model's thinking kind, below the catalogue's own tune
    base: Settings = {**thinking.defaults(m.get("thinking")), **dict(m.get("tune") or {})}
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
    if m.get("thinking"):                                   # a level on an on / off model is "on", and the reverse
        vals.update(thinking.values(m.get("thinking"), vals))
    return vals, src


def tuned_ub(m: ModelInfo) -> Optional[int]:
    """The batch size (-ub) Auto-tune chose for this model on this computer (A45); None without one (no record, or a
    record from before A45: the start takes the default)."""
    ub = ((m.get("local") or {}).get("tune") or {}).get("ub")
    return ub if isinstance(ub, int) and not isinstance(ub, bool) and ub > 0 else None


def pin_kept(pinned: float, unpinned: float) -> bool:
    """The rule for the threads with experts in the RAM (A54, A59): pinned is kept only when it writes faster than
    unpinned by more than the measurement's noise (DRAFT_MARGIN); a tie is unpinned."""
    return pinned > unpinned * DRAFT_MARGIN


def tuned_pin(m: ModelInfo) -> str:
    """What Auto-tune found for the threads of this model with experts in the RAM (A54, A57, A59): "pinned" (one per
    physical core wrote faster on this computer, by more than the noise), "unpinned" (llama.cpp's own did not lose), or
    "" (no measurement: no record, a record from a computer without the step, a value that is neither). The start pins
    only for "pinned". The rule is applied here, on read: a record that says pinned but whose two averaged results
    (results.threads_ram) are a tie, as A54's rule wrote them, reads as unpinned, with no re-tune. A pinned record
    without both results is taken as it says."""
    tune = (m.get("local") or {}).get("tune") or {}
    pin = tune.get("pin")
    if pin != "pinned":
        return pin if pin == "unpinned" else ""
    got = (tune.get("results") or {}).get("threads_ram") or {}
    try:
        p, u = float(got["pinned"]["score"]), float(got["unpinned"]["score"])
    except (KeyError, TypeError, ValueError):
        return "pinned"
    return "pinned" if pin_kept(p, u) else "unpinned"


def kv_measured(m: ModelInfo) -> bool:
    """The model's Auto-tune record measured the context memory types on this computer (A71: a tune on the CPU alone;
    results.kv has a row for each type), so its settings.kv is a measured choice. A record without them (a GPU tune, or
    one from before A71) holds q4_0 as a constant, which a start on the CPU alone does not take for a choice."""
    return measured_kv((m.get("local") or {}).get("tune") or {})


def without_kv(m: ModelInfo) -> ModelInfo:
    """The model with its Auto-tune record's context memory type left out (effective_tune then takes the one below it)."""
    local = dict(m.get("local") or {})
    tune = dict(local.get("tune") or {})
    tune["settings"] = {k: v for k, v in (tune.get("settings") or {}).items() if k != "kv"}
    local["tune"] = tune
    return {**m, "local": local}                                    # type: ignore[typeddict-item]


def tune_stamp(m: ModelInfo) -> str:
    """Which tune a person's -ub choice was made against: the record's date and its -ub ("2026-10-28/2048"); "" without
    a record. A later tune that gives another stamp asks the question again (A51; domain/server/ub_order.py)."""
    t = (m.get("local") or {}).get("tune") or {}
    ub = tuned_ub(m)
    day = re.sub(r"[^A-Za-z0-9_.:-]", "", str(t.get("date", "")))       # the stamp travels as a launch-env value
    return f"{day}/{ub}" if ub else ""


def tuned_by_ub(m: ModelInfo) -> List[List[float]]:
    """The record's batch size step: [-ub, prompt reading tok/s, writing tok/s] for each size measured ([] without)."""
    rows = (((m.get("local") or {}).get("tune") or {}).get("results") or {}).get("ub") or []
    return [[float(x) for x in r] for r in rows if isinstance(r, list) and len(r) >= 2]


def tune_advice(m: ModelInfo, vals: Settings, src: Dict[str, SettingSource],
                words: PlatformWords = MACOS) -> Optional[str]:
    """A note for a start whose window is below the floor only because the catalogue sized
    it for smaller Macs (no Auto-tune result here, no user choice): Auto-tune finds this
    Mac's window."""
    ctx = vals.get("ctx")
    if src.get("ctx") in ("auto-tune", "config") or not isinstance(ctx, int) or ctx >= CTX_FLOOR:
        return None
    name = m.get("name", "")
    return (f"{name} starts with a context of {ctx // 1024}K tokens (the catalogue sized it for smaller Macs). "
            f"To find the context for {words.machine}, run {words.command} tune {name} (or use Settings > Auto-tune).")


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


def select_named(models: List[ModelInfo], name: str, as_path: str, words: PlatformWords = MACOS, quant: str = "",
                 chosen: Sequence[str] = (), fits: Callable[[ModelInfo], bool] = lambda m: True,
                 cpu: bool = False) -> ModelInfo:
    """The model a start names: it must exist and be downloaded. NAME is a model with several quantizations: quant
    (or NAME:QUANT) names one, else the default one takes it (quants.default_quant: chosen, the latest Auto-tune
    record, the largest that fits; cpu, a start on the CPU alone: IQ4_NL and Q4_0 before the K-quants of their size,
    A72). A name that is the name of an entry of its own (qwen3.6-35b-a3b-iq3) names that entry, as it always did."""
    base, spec = split_spec(name)
    quant = quant or spec
    group = group_of(models, base)
    if group is not None and not quant and base == group.name and group.several:
        m: Optional[ModelInfo] = default_quant(group, chosen, fits, cpu)
    elif group is not None and quant:
        found = matches(group, quant)
        if len(found) != 1:
            raise ConfigError(f"{'unknown' if not found else 'ambiguous'} quantization '{quant}' of {group.name}. "
                              f"Its quantizations: {quant_names(group)}.")
        m = found[0]
    else:
        m = find_model(models, name, as_path)
    if not m:
        raise ConfigError(f"unknown model '{name}'. {words.command} models lists the models.")
    if m.get("status") != "downloaded":
        raise ConfigError(f"{m.get('name')} is not downloaded. To download it: {words.command} download "
                          f"{m.get('name')}")
    return m


def group_of(models: Sequence[ModelInfo], name: str) -> Optional[ModelGroup]:
    """The model (quants.ModelGroup) that a name points at: the model's name, or the name of any of its entries."""
    groups = group_models(models)
    return next((g for g in groups if g.name == name), None) or next(
        (g for g in groups if any(m.get("name") == name for m in g.entries)), None)

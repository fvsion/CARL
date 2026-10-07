#!/usr/bin/env python3
"""CARL models and configuration: one place for the built-in catalogue, the
models on this Mac, and the user's settings. Used by host/serve-llama.sh,
host/serve.sh, host/models.sh, tools/llama-fit.py and the monitor.

Files
  host/catalog.json              built-in catalogue (in the repo): download source, pinned
                                 revision + sha256, tuned settings and why, context zones
  ~/.config/carl/models.json     models on this Mac that are not in the catalogue (Hugging
                                 Face downloads, files dropped into the models folder) and
                                 Auto-tune results for every model (per Mac)
  ~/.config/carl/config.json     the user's settings (monitor Settings tab, or by hand):
                                 server options and per-model profiles
  ~/models/gguf/*.gguf           the models folder (paths.models_dir); every .gguf here is
                                 listed, catalogued or not

Settings precedence for a llama.cpp start: command-line flags > environment >
config.json (llama section, then models.<name>) > Auto-tune result for this Mac >
catalogue tune > built-in defaults. 96K per slot is the smallest "fast" window: only
larger windows are flagged as slow. llama.model = auto starts auto fit's pick: the best
ranked stock model for the goal (llama.auto_goal: everyday = fast first: MoE and small dense,
hard-code = dense first) that fits this Mac, from llama.auto_fit (catalogue or downloaded); when the pick is
not downloaded, the best downloaded one that fits (./carl.sh fit shows the reasons).

CLI (./carl.sh models | download | verify use it through host/models.sh)
  carl.py list                         models: catalogue + models folder + custom
  carl.py download NAME|default|all    a catalogue model (pinned, verified; with its MTP drafter when it has
                                       one); default = auto fit's pick
  carl.py download hf:REPO/FILE.gguf   any GGUF from Hugging Face (also a huggingface.co URL); a Gemma 4
                                       file also gets the catalogue's MTP drafter of its size
  carl.py download NAME (custom)       a custom Gemma 4 model: its MTP drafter (domain/drafters.py)
  carl.py hf-files REPO                the GGUF files of a Hugging Face repo
  carl.py verify NAME... | delete NAME | path NAME | get NAME FIELD | default | downloaded
                                       (default: auto fit's pick for this Mac, everyday goal)
  carl.py launch-env [--model NAME|PATH] [--no-config]   KEY=value lines for serve-llama.sh
  carl.py router-preset --out FILE --templates DIR   router mode's presets INI (serve-llama.sh); prints
                                       "start NAME", "model NAME SETUP" and "skip NAME: WHY" lines
  carl.py client-models                the installed models for the OpenCode / Pi configs (JSON:
                                       client/install.sh writes it to client/installed-models.json)
  carl.py config [show|path|get KEY|set KEY VALUE|unset KEY]   KEY like llama.net or models.NAME.ctx
  carl.py card NAME [set FIELD VALUE... | unset FIELD]   a model's card (custom models: yours, editable;
                                       catalogue models: read-only). FIELD like role, good_for, rank
  carl.py cache [show|trim|clear]      the disk cache of prompt states (~/.config/carl/slots): what it
                                       holds, trim it to cache.disk_gb, or remove every file
  carl.py push                         publish the client config (the installed models) for the clients'
                                       sync service on other computers (the dashboard's API serves it)
  carl.py package [--anyway] [--out DIR]   the client package: dist/carl-client-VERSION-HOST.zip, the
                                       client folder with the server's address and key, for ./setup on
                                       another computer (carl_core/adapters/client_package.py)

This module is also the public API of the monitor (tools/llama-monitor.py): the functions
below take and return plain dicts. The logic lives in tools/carl_core.
"""
import os
import sys

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json  # noqa: E402
from typing import Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from carl_core.app import Carl  # noqa: E402
from carl_core.domain import cards as _cards  # noqa: E402
from carl_core.domain.router import preset_ini  # noqa: E402
from carl_core.domain import models as _dm  # noqa: E402
from carl_core.domain.autofit import AutoFit as AutoFit, as_goal, as_scope  # noqa: E402
from carl_core.domain.errors import ConfigError as ConfigError  # noqa: E402  (re-exported)
from carl_core.domain.gguf import ModelShape  # noqa: E402
from carl_core.domain.hf import parse_hf as parse_hf  # noqa: E402
from carl_core.domain.launch import shell_lines as shell_lines  # noqa: E402
from carl_core.domain.settings import (MODEL_KEYS as _MODEL, SCHEMA as SCHEMA, SECTIONS as _SECTIONS,  # noqa: E402
                                       Config, SettingSpec, get_path, key_path, set_path, unset_path,
                                       validate_config as _validate)
from carl_core.domain.types import (Catalog, CustomCard, CustomInfo, HfFileList, JsonObject, JsonValue,  # noqa: E402
                                    LocalDb, ModelInfo, SettingSource, SettingValue, Settings, Zone)
from carl_core.domain.units import file_size  # noqa: E402
from carl_core.adapters.client_package import make_package as _make_package  # noqa: E402
from carl_core.domain.package import Outcome as PackageOutcome  # noqa: E402
from carl_core.wiring import REPO as REPO, CarlPaths, build_carl  # noqa: E402
from carl_help import columns, width, wrap  # noqa: E402

# The names this module imports only for its users (tools/monitor/store.py, tools/carl-tune.py).
__all__ = ["REPO", "SCHEMA", "ConfigError", "parse_hf"]

_PATHS_NOW = CarlPaths.from_env(os.environ)
CONF_DIR = _PATHS_NOW.conf_dir
CONFIG_FILE = _PATHS_NOW.config
LOCAL_FILE = _PATHS_NOW.local


def _spec_dicts(keys: Mapping[str, SettingSpec]) -> Dict[str, Dict[str, JsonValue]]:
    return {k: s.to_dict() for k, s in keys.items()}


# The settings schema as dicts ({"type", "default", "env", ...} per key), as the monitor reads it.
MODEL_KEYS = _spec_dicts(_MODEL)

_APP: List[Carl] = []


def app() -> Carl:
    """The Carl service for this process (built on first use)."""
    if not _APP:
        _APP.append(build_carl(_PATHS_NOW, os.environ))
    return _APP[0]


def _config(cfg: Optional[Mapping[str, object]]) -> Config:
    """A config dict from a caller (validated), or config.json when none is given."""
    return app().parse_config(dict(cfg) if cfg else None)


def _models(models: Optional[List[ModelInfo]]) -> List[ModelInfo]:
    return models or all_models()


# ---------------------------------------------------------------- config.json
def load_config() -> JsonObject:
    return app().load_config().to_json()


def save_config(cfg: Mapping[str, object]) -> JsonObject:
    """Validate and write config.json; the document as written."""
    return app().save_config(_validate(dict(cfg))[0]).to_file()


def models_dir(cfg: Optional[Mapping[str, object]] = None) -> str:
    return app().models_dir(_config(cfg))


# ---------------------------------------------------------------- catalogue + local models
def load_catalog() -> Catalog:
    return app().load_catalog()


def load_local() -> LocalDb:
    return app().load_local()


def save_local(db: LocalDb) -> None:
    app().save_local(db)


def shape_of(path: str) -> ModelShape:
    """GGUF header shape of a local file (cached by path + mtime: the header read is 64 MB)."""
    return app().shapes.local(path)


def custom_defaults(path: str) -> Tuple[Settings, CustomInfo]:
    """Starting tune for a model that is not in the catalogue, from its GGUF header."""
    return app().custom_defaults(path)


def all_models(cfg: Optional[Mapping[str, object]] = None) -> List[ModelInfo]:
    """Every model CARL knows: the catalogue, plus each .gguf in the models folder and
    each custom download. One dict per model, with status and path."""
    return app().all_models(_config(cfg))


def find(name: str, models: Optional[List[ModelInfo]] = None) -> Optional[ModelInfo]:
    return app().find(name, _models(models))


def effective_tune(m: ModelInfo, cfg: Optional[Mapping[str, object]] = None
                   ) -> Tuple[Settings, Dict[str, SettingSource]]:
    """(settings, source per key): built-ins < catalogue < Auto-tune (this Mac) < config profile."""
    return app().effective_tune(m, _config(cfg))


def ctx_zones(m: ModelInfo) -> Tuple[int, int, int]:
    """Context per slot: (good, slow, very_slow); good is at least 96K."""
    return _dm.ctx_zones(m)


def ctx_zone(m: ModelInfo, ctx: int) -> Zone:
    return _dm.ctx_zone(m, ctx)


def pick_default(models: Optional[List[ModelInfo]] = None) -> str:
    """This Mac's default: auto fit's pick from the whole catalogue (everyday goal)."""
    return app().pick_default(_models(models))


def resolve_launch(name: Optional[str] = None, cfg: Optional[Mapping[str, object]] = None
                   ) -> Tuple[ModelInfo, List[ModelInfo], Optional[str]]:
    """(model, all models, note) for a llama.cpp start: name, else config llama.model, else
    auto fit's pick (the best downloaded stock model that fits when the pick isn't downloaded).
    The dashboard calls it (tools/monitor/store.py: the model "auto" starts)."""
    return app().resolve_launch(name, _config(cfg))


def auto_fit(goal: Optional[str] = None, scope: Optional[str] = None, models: Optional[List[ModelInfo]] = None,
             cfg: Optional[Mapping[str, object]] = None) -> AutoFit:
    """Auto fit for this Mac: the best ranked stock model for the goal (everyday / hard-code)
    from the scope (catalogue / downloaded); None takes config.json's llama.auto_goal / auto_fit."""
    c = _config(cfg)
    g, s = app().auto_settings(c)
    return app().auto_fit(models or app().all_models(c), as_goal(goal) if goal else g, as_scope(scope) if scope else s)


def client_models(cfg: Optional[Mapping[str, object]] = None) -> JsonObject:
    """The installed models for the OpenCode / Pi configs: {schema, default, models: [{id, label,
    ctx, thinking}]} (client/carl_models.py turns them into entries)."""
    return app().client_models(_config(cfg))


def launch_env(name: Optional[str] = None, use_config: bool = True) -> Tuple[Dict[str, SettingValue], Optional[str]]:
    return app().launch_env(name, use_config)


# ---------------------------------------------------------------- model cards
def save_card(name: str, card: Mapping[str, object]) -> CustomCard:
    """Check and store the user's card of a custom model (models.json); the card as stored.
    Raises ConfigError for a catalogue model or a bad field."""
    return app().save_card(name, dict(card))


def set_card_field(name: str, key: str, args: Sequence[str]) -> CustomCard:
    """One field of a custom model's card from command-line text (see cmd_card)."""
    return app().set_card_field(name, key, args)


def unset_card_field(name: str, key: str) -> CustomCard:
    return app().unset_card_field(name, key)


# ---------------------------------------------------------------- downloads
def hf_files(repo: str, revision: str = "main") -> HfFileList:
    """[(file, bytes, sha256)] of the GGUF files in a Hugging Face repo (first parts only)."""
    return app().hf_files(repo, revision)


def human(b: float) -> str:
    """A file size (GB, 1000-based: glossary section 9)."""
    return file_size(b)


def verify(m: ModelInfo) -> bool:
    return app().verify(m)


def download(m: ModelInfo, mdir: str) -> bool:
    return app().download(m, mdir)


def download_hf(spec: str, name: Optional[str] = None) -> bool:
    return app().download_hf(spec, name)


def delete(m: ModelInfo) -> None:
    app().delete(m)


# ---------------------------------------------------------------- CLI
def _arg(a: Sequence[str], i: int, usage: str) -> str:
    if len(a) <= i:
        raise ConfigError(f"usage: carl.py {usage}")
    return a[i]


UNKNOWN_MODEL = "unknown model '{}'. ./carl.sh models lists the models."


def _known(name: str, models: Optional[List[ModelInfo]] = None) -> ModelInfo:
    m = find(name, models)
    if not m:
        raise ConfigError(UNKNOWN_MODEL.format(name))
    return m


STATUS_TEXT = {"downloaded": "downloaded", "missing": "not downloaded", "partial": "partial"}
SOURCE_TEXT = {"catalog": "catalogue", "hf": "Hugging Face", "file": "models folder"}


def _tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def cmd_list(models: List[ModelInfo]) -> None:
    """Every model: name, file size (GB), download status, source, and what it is (wrapped to the width)."""
    cfg = app().load_config()
    w = width()
    nw = max([len("model")] + [len(m.get("name", "")) for m in models])
    head = f"{'model':<{nw}}  {'size':>8}  {'status':<14}  {'source':<13}"
    side = w - len(head) - 2 >= 40                  # the description beside the row, else below it
    print((head + "  about") if side else head.rstrip())
    for m in models:
        name = m.get("name", "")
        about = f"{m.get('role')} (your card)" if m.get("custom") and m.get("role") else str(m.get("summary", ""))
        notes = []
        if (m.get("local") or {}).get("tune"):
            notes.append("Auto-tune done on this Mac.")
        if m.get("draft") and m.get("status") == "downloaded" and m.get("draft_status") != "downloaded":
            notes.append(f"The MTP drafter is not downloaded: ./carl.sh download {name}")
        if m.get("draft_offer"):
            notes.append(f"A Gemma 4 MTP drafter fits this model (the one of {m.get('draft_for')}): "
                         f"./carl.sh download {name}")
        text = " ".join([about.rstrip()] + notes).strip()
        row = (f"{name:<{nw}}  {human(m.get('bytes', 0)):>8}  {STATUS_TEXT.get(str(m.get('status')), str(m.get('status'))):<14}"
               f"  {SOURCE_TEXT.get(str(m.get('source')), str(m.get('source'))):<13}")
        if side:
            print("\n".join(wrap(text, w, row + "  ", " " * (len(head) + 2))))
        else:
            print(row.rstrip())
            if text:
                print("\n".join(wrap(text, w, "    ")))
    d = app().models_dir(cfg)
    free = app().files.free_bytes(d)
    print()
    for line in (f"The models folder is {_tilde(d)}. The disk has {human(free) if free is not None else 'an unknown size'} "
                 f"free.",
                 "Status: downloaded, not downloaded, or partial (the download stopped: run it again to continue).",
                 "To download a model from Hugging Face: ./carl.sh download hf:OWNER/REPO/FILE.gguf. To see the files "
                 "of a repository: ./carl.sh download hf:OWNER/REPO."):
        print("\n".join(wrap(line, w)))
    if any(m.get("custom") for m in models):
        print("\n".join(wrap("To describe a custom model (its role, what it is good for, its quality rank): "
                             "./carl.sh card NAME, or e in Settings > Models of the dashboard.", w)))


# What each setting does, and its name in the dashboard (./carl.sh config show).
KEY_HELP: Dict[str, str] = {
    "llama.model": "The model that the server starts (Settings > Server). auto: the model that Auto fit chooses.",
    "llama.auto_goal": "The goal of Auto fit: everyday (fast models first) or hard-code (dense models first: "
                       "better code, slower).",
    "llama.auto_fit": "The candidates of Auto fit: catalogue (all catalogue models) or downloaded (downloaded "
                      "models only).",
    "llama.mode": "single (single model: you change it in the dashboard) or router (router mode: OpenCode and Pi "
                  "switch the model). Settings > Router.",
    "llama.net": "The network: local (only this Mac can use the server) or vm (the VM network too).",
    "llama.host": "One address of this Mac to listen on. It wins over llama.net. Empty: llama.net decides.",
    "llama.cache_ram": "The size of the RAM cache in MiB. auto: the free memory after the model, from 1 GiB to "
                       "8 GiB.",
    "llama.ub": "The physical batch size of llama-server (-ub).",
    "llama.batch": "The logical batch size of llama-server (-b).",
    "llama.ckpt": "The number of context checkpoints for each slot (--ctx-checkpoints). Models with a recurrent "
                  "state (Qwen3.6, Qwen3.8) use them.",
    "llama.ckpt_step": "The smallest number of tokens between two checkpoints.",
    "llama.think_toggle": "A chat template that lets OpenCode turn thinking off. false: the template of the model "
                          "file.",
    "llama.extra_args": "More flags for llama-server, as a list of words.",
    "paths.models_dir": "The models folder. MODELS_DIR wins over it.",
    "cache.disk_gb": "The largest size of the disk cache, in GB (Settings > Caching).",
    "cache.prefix": "Save the prompt of each agent (saved prompts).",
    "cache.sessions": "Save the sessions (saved sessions).",
    "cache.save": "When CARL saves a session: auto (when it would take cache.auto_s seconds to read again), turn "
                  "(after each turn), switch or stop (CARL writes only a record).",
    "cache.auto_s": "For cache.save auto: the seconds of reading that make CARL save a session.",
    "cache.share": "Store a saved session as the changes to its saved prompt (less disk).",
    "cache.swa": "Sliding-window models (Gemma): full (full cache), window (window cache) or auto (the full cache "
                 "when it fits).",
    "cache.move": "Models whose chat template CARL does not know (not Qwen, not Gemma 4): off (the default) leaves "
                  "the parts of the prompt that change per project (the folder, the date, AGENTS.md) in the system "
                  "prompt; auto moves them to the start of your first message, so one saved prompt serves every "
                  "project (Settings > Caching > Other templates).",
    "models.NAME.kv": "The context memory type: q4_0 (q4), q8_0 (q8) or f16.",
    "models.NAME.ctx": "The context of each slot in tokens (96k = 98304).",
    "models.NAME.slots": "The number of slots, or auto (2 slots when they fit).",
    "models.NAME.spec": "The speculation: none, ngram-mod (n-gram), draft-mtp (MTP) or draft-mtp,ngram-mod "
                        "(MTP + n-gram).",
    "models.NAME.spec_n": "The number of guesses for each step of the speculation.",
    "models.NAME.temp": "The sampling temperature.",
    "models.NAME.top_p": "The sampling top_p.",
    "models.NAME.top_k": "The sampling top_k.",
    "models.NAME.min_p": "The sampling min_p.",
    "models.NAME.presence": "The presence penalty.",
    "models.NAME.repeat": "The repetition penalty (1.0 = off).",
    "models.NAME.alias": "The model name that the server shows to the clients. Empty: the model name.",
}


def _value_text(spec: SettingSpec) -> str:
    """The values a key takes and its default, in words."""
    kinds = {"int": "a whole number", "float": "a number", "intauto": "a whole number or auto", "bool": "true or false",
             "list": "a list", "str": "text", "choice": " | ".join(spec.choices)}
    text = kinds.get(spec.kind, spec.kind)
    if spec.min is not None and spec.max is not None and spec.kind in ("int", "float", "intauto"):
        text += f" from {spec.min} to {spec.max}"
    default = spec.default
    shown = ("empty" if default in ("", [], None) else "true" if default is True else "false" if default is False
             else str(default))
    return f"{text}. Default: {shown}."


def cmd_config(argv: Sequence[str]) -> None:
    usage = "config [show|path|get KEY|set KEY VALUE|unset KEY]"   # ConfigError("usage: carl.py ...")
    sub = argv[0] if argv else "show"
    if sub == "path":
        print(CONFIG_FILE)
        return
    cfg = load_config()
    if sub == "show":
        for warn in app().config_warnings():
            print(f"warning: {warn}", file=sys.stderr)
        w = width()
        print(f"Your settings ({_tilde(CONFIG_FILE)}):")
        print(json.dumps(cfg, indent=2))
        print()
        print("\n".join(wrap("Every setting: what it does, its values and its default. To change one: ./carl.sh config "
                             "set KEY VALUE. A setting of one model: models.NAME.KEY (for example "
                             "models.gemma-4-12b.ctx).", w)))
        rows: List[Tuple[str, str]] = []
        for sec, keys in _SECTIONS.items():
            for k, spec in keys.items():
                rows.append((f"{sec}.{k}", f"{KEY_HELP.get(f'{sec}.{k}', '')} Values: {_value_text(spec)}".strip()))
        for k, spec in _MODEL.items():
            rows.append((f"models.NAME.{k}", f"{KEY_HELP.get(f'models.NAME.{k}', '')} Values: "
                                             f"{_value_text(spec).split('. Default')[0]}.".strip()))
        print("\n".join(columns(rows, w, max_term=20)))
        return
    if sub not in ("get", "set", "unset"):
        raise ConfigError(f"usage: carl.py {usage}")
    key = _arg(argv, 1, usage)
    parts = key_path(key)
    if sub == "get":
        print(json.dumps(get_path(cfg, parts)))
    elif sub == "set":
        value: JsonValue = _arg(argv, 2, usage) if len(argv) == 3 else list(argv[2:])
        set_path(cfg, parts, value)
        print(f"{key} = {json.dumps(get_path(save_config(cfg), parts))}")
    else:
        unset_path(cfg, parts)
        save_config(cfg)


def _count(n: int, one: str, many: str = "") -> str:
    """1 file, 2 files."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def cmd_cache(argv: Sequence[str]) -> None:
    """The disk cache OpenCode and Pi fill (monitor.diskcache): show, trim to the limit, or clear."""
    from monitor import diskcache          # the dashboard's module: the same rules as its Caching panel
    sub = argv[0] if argv else "show"
    folder = os.path.join(os.path.dirname(CONFIG_FILE), "slots")
    conf = diskcache.config_of(load_config())
    if sub == "trim":
        diskcache.remove(folder, diskcache.legacy(folder))
        if conf.share:
            from monitor import slotpack
            t = slotpack.tidy(folder)
            if t.packed:
                print(f"CARL stored {_count(t.packed, 'saved session')} as the changes to its saved prompt.")
        gone = diskcache.over_budget(diskcache.listing(folder), conf.limit)
        diskcache.remove(folder, gone)
        print(f"CARL removed {_count(len(gone), 'file')}: the disk cache was larger than its limit of "
              f"{conf.disk_gb} GB." if gone else f"The disk cache is in its limit of {conf.disk_gb} GB.")
        return
    files = diskcache.listing(folder)
    if sub == "clear":
        diskcache.remove(folder, [f.name for f in files])
        print(f"CARL removed {_count(len(files), 'file')} from the disk cache ({_tilde(folder)}).")
        return
    if sub != "show":
        raise ConfigError("cache takes show, trim or clear. ./carl.sh cache --help tells more.")
    w = width()
    prompts = sum(1 for f in files if f.kind == diskcache.PROMPT)
    head = (f"The disk cache ({_tilde(folder)}) uses {file_size(diskcache.used(files))} of its limit of "
            f"{conf.disk_gb} GB. It holds {_count(prompts, 'saved prompt')} and "
            f"{_count(len(files) - prompts, 'saved session')}. Saved prompts: {'on' if conf.prefix else 'off'}. "
            f"Saved sessions: {'on' if conf.sessions else 'off'}.")
    print("\n".join(wrap(head, w)))
    packed = sum(f.packed for f in files)
    if packed:
        print("\n".join(wrap(f"{_count(packed, 'saved session')} {'is' if packed == 1 else 'are'} stored as the "
                             f"changes to {'its' if packed == 1 else 'their'} saved prompt. This saves "
                             f"{file_size(diskcache.shared_saving(files))}.", w)))
    if not files:
        return
    print()
    rows = []
    for f in sorted(files, key=lambda f: -f.mtime):
        kind = ("saved prompt" if f.kind == diskcache.PROMPT else
                "saved session*" if f.packed else "saved session")
        rows.append((kind, diskcache.describe(f.name), file_size(f.bytes)))
    kw = max(len(r[0]) for r in rows)
    sw = max(len(r[2]) for r in rows)
    nw = max(10, w - kw - sw - 6)
    print(f"  {'kind':<{kw}}  {'model · agent or session':<{nw}}  {'size':>{sw}}"[:w].rstrip())
    for kind, what, size in rows:
        what = what if len(what) <= nw else what[:nw - 1] + "…"
        print(f"  {kind:<{kw}}  {what:<{nw}}  {size:>{sw}}")
    if packed:
        print("  * stored as the changes to its saved prompt")


def cmd_download(names: List[str]) -> int:
    if not names:
        raise ConfigError("download needs a model: NAME, default, all or hf:OWNER/REPO/FILE.gguf. "
                          "./carl.sh download --help tells more.")
    models = all_models()
    if names == ["all"]:
        names = [m.get("name", "") for m in models if m.get("source") == "catalog"]
    ok = True
    for n in names:
        if n == "default":
            n = pick_default(models)
            print(f"Auto fit chooses {n} for this Mac.")
        if n.startswith(("hf:", "http")) or (n.count("/") >= 1 and not find(n, models)):
            ok = download_hf(n) and ok
            continue
        m = find(n, models)
        if m and m.get("custom") and m.get("status") == "downloaded" and (m.get("draft_offer") or m.get("draft")):
            ok = app().download_drafter(app().add_drafter(m)) and ok      # a custom Gemma 4 model: its drafter
            continue
        if not m or not m.get("hf"):
            raise ConfigError(UNKNOWN_MODEL.format(n))
        ok = download(m, models_dir()) and ok
    return 0 if ok else 1


def cmd_card(argv: Sequence[str]) -> None:
    usage = "card NAME [set FIELD VALUE... | unset FIELD]"
    name = _arg(argv, 0, usage)
    sub = argv[1] if len(argv) > 1 else "show"
    if sub == "set":
        if len(argv) < 4:
            raise ConfigError(f"usage: carl.py {usage}")
        set_card_field(name, argv[2], argv[3:])
    elif sub == "unset":
        if len(argv) != 3:
            raise ConfigError(f"usage: carl.py {usage}")
        unset_card_field(name, argv[2])
    elif sub != "show" or len(argv) > 2:
        raise ConfigError(f"usage: carl.py {usage}")
    print("\n".join(card_lines(_known(name), width())))


def card_lines(m: ModelInfo, w: int) -> List[str]:
    """A model's card for the command line (cards.card_rows), wrapped to w columns."""
    head, rows, hints = _cards.card_rows(m)
    out = wrap(head, w) + columns(rows, w, max_term=27)
    for h in hints:
        out += [""] + wrap(h, w)
    return out


def cmd_get(name: str, field: str) -> None:
    m = _known(name)
    vals = effective_tune(m)[0]
    hf = m.get("hf") or {}
    fields: Dict[str, object] = {
        "repo": hf.get("repo"), "rev": hf.get("revision"), "file": hf.get("file"), "sha256": hf.get("sha256"),
        "bytes": m.get("bytes"), "alias": vals["alias"], "spec": f"{vals['spec']}:{vals['spec_n']}",
        "notes": m.get("summary"), "name": m.get("name")}
    v = fields.get(field)
    print("" if v is None else v)


def make_package(out_dir: Optional[str] = None, anyway: bool = False, cmd: str = "./carl.sh") -> PackageOutcome:
    """The client package (./carl.sh package and the dashboard): the zip of client/ with the server's address
    and key. The server's remote.json and api-key come from CARL_CLIENT_DIR when it is set, else client/."""
    client_dir = os.environ.get("CARL_CLIENT_DIR") or os.path.join(REPO, "client")
    return _make_package(REPO, client_dir, out_dir or os.path.join(REPO, "dist"), client_models(),
                         load_config(), cmd, anyway)


def cmd_package(argv: Sequence[str]) -> int:
    use = "package [--anyway] [--out DIR]"
    out_dir: Optional[str] = None
    anyway = False
    i = 0
    while i < len(argv):
        if argv[i] == "--anyway":
            anyway = True
        elif argv[i] == "--out":
            out_dir = _arg(argv, i + 1, use)
            i += 1
        else:
            raise ConfigError(f"package does not know '{argv[i]}'. Usage: ./carl.sh {use}")
        i += 1
    res = make_package(out_dir, anyway, os.environ.get("CARL_CMD") or "./carl.sh")
    w = width()

    def para(note: str) -> str:
        text, *commands = note.split("\n")                 # the commands: as they are, to copy
        return "\n".join(wrap(text, w) + [f"  {c}" for c in commands])
    if res.error:
        print("\n".join([para(res.error), *map(para, res.notes)]), file=sys.stderr)
        return 1
    print(f"CARL made the client package ({res.files} files, {file_size(res.size)}):")
    print(f"  {res.path}")
    for n in res.notes:
        print()
        print(para(n))
    return 0


def main(argv: List[str]) -> int:
    cmd = argv[0] if argv else "list"
    a = argv[1:]
    if cmd in ("list", "ls"):
        cmd_list(all_models())
    elif cmd in ("download", "dl"):
        return cmd_download(a)
    elif cmd == "hf-files":
        for f, b, _ in hf_files(_arg(a, 0, "hf-files OWNER/REPO")):
            print(f"{f}\t{b}")
    elif cmd == "verify":
        models = all_models()
        names = a or [m.get("name", "") for m in models if m.get("status") == "downloaded"]  # none: every downloaded model
        unknown = [n for n in names if not find(n, models)]
        if unknown:
            raise ConfigError(UNKNOWN_MODEL.format(unknown[0]))
        if not names:
            print("No model is downloaded, so there is nothing to check.")
        results = [verify(_known(n, models)) for n in names]
        return 0 if all(results) else 1
    elif cmd == "delete":
        m = _known(_arg(a, 0, "delete NAME"))
        if m.get("status") != "downloaded" and not m.get("draft_path"):
            raise ConfigError(f"{m.get('name')} is not downloaded, so there is nothing to delete.")
        delete(m)
        print(f"CARL deleted {_tilde(m.get('path', ''))}"
              + (f" and its MTP drafter {_tilde(m.get('draft_path', ''))}." if m.get("draft_path") else "."))
    elif cmd == "path":
        name = _arg(a, 0, "path NAME")
        m = _known(name)
        if m.get("status") != "downloaded":
            raise ConfigError(f"{name} is not downloaded. To download it: ./carl.sh download {name}")
        print(m.get("path"))
    elif cmd == "get":
        cmd_get(_arg(a, 0, "get NAME FIELD"), _arg(a, 1, "get NAME FIELD"))
    elif cmd == "default":
        print(pick_default())
    elif cmd == "downloaded":
        for m in all_models():
            if m.get("status") == "downloaded":
                print(m.get("name"))
    elif cmd == "router-preset":
        use = "router-preset --out FILE --templates DIR"
        out = _arg(a, a.index("--out") + 1, use) if "--out" in a else _arg([], 0, use)
        tdir = _arg(a, a.index("--templates") + 1, use) if "--templates" in a else _arg([], 0, use)
        preset, common = app().router_preset(app().load_config(), tdir)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(preset_ini(preset, common))
        for p in preset.models:
            print(f"model {p.name} {p.describe()}")
        for n, why in preset.skipped:
            print(f"skip {n}: {why}")
        if preset.start:
            print(f"start {preset.start}")
    elif cmd == "client-models":
        print(json.dumps(client_models(), indent=2))
    elif cmd == "push":
        from monitor import clientsync         # the dashboard's module: its API serves what this writes
        version = clientsync.publish(os.path.dirname(CONFIG_FILE), client_models())
        print("\n".join(wrap(f"CARL is ready to send the client config to other computers (version {version}). "
                             "The dashboard sends it while it runs. OpenCode and Pi apply it at their next start.",
                             width())))
    elif cmd == "package":
        return cmd_package(a)
    elif cmd == "launch-env":
        model = _arg(a, a.index("--model") + 1, "launch-env [--model NAME|PATH] [--no-config]") if "--model" in a else None
        env, note = launch_env(model, use_config="--no-config" not in a)
        if note:
            print(note, file=sys.stderr)
        print(shell_lines(env))
    elif cmd == "config":
        cmd_config(a)
    elif cmd == "card":
        cmd_card(a)
    elif cmd == "cache":
        cmd_cache(a)
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        raise ConfigError(f"unknown command '{cmd}'. carl.py --help lists the commands.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

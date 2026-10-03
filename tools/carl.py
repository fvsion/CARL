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
ranked stock model for the goal (llama.auto_goal: everyday = MoE first, hard-code = dense
first) that fits this Mac, from llama.auto_fit (catalogue or downloaded); when the pick is
not downloaded, the best downloaded one that fits (./carl.sh fit shows the reasons).

CLI (./carl.sh models | download | verify use it through host/models.sh)
  carl.py list                         models: catalogue + models folder + custom
  carl.py download NAME|default|all    a catalogue model (pinned, verified); default = auto fit's pick
  carl.py download hf:REPO/FILE.gguf   any GGUF from Hugging Face (also a huggingface.co URL)
  carl.py hf-files REPO                the GGUF files of a Hugging Face repo
  carl.py verify NAME... | delete NAME | path NAME | get NAME FIELD | default | downloaded
                                       (default: auto fit's pick for this Mac, everyday goal)
  carl.py launch-env [--model NAME|PATH] [--no-config]   KEY=value lines for serve-llama.sh
  carl.py config [show|path|get KEY|set KEY VALUE|unset KEY]   KEY like llama.net or models.NAME.ctx
  carl.py card NAME [set FIELD VALUE... | unset FIELD]   a model's card (custom models: yours, editable;
                                       catalogue models: read-only). FIELD like role, good_for, rank

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
from carl_core.domain import models as _dm  # noqa: E402
from carl_core.domain.autofit import AutoFit as AutoFit, as_goal, as_scope  # noqa: E402
from carl_core.domain.errors import ConfigError as ConfigError  # noqa: E402  (re-exported)
from carl_core.domain.fit import human_gb  # noqa: E402
from carl_core.domain.gguf import GIB as GIB, ModelShape  # noqa: E402
from carl_core.domain.hf import parse_hf as parse_hf  # noqa: E402
from carl_core.domain.launch import shell_lines as shell_lines  # noqa: E402
from carl_core.domain.settings import (LLAMA_KEYS as _LLAMA, MODEL_KEYS as _MODEL,  # noqa: E402
                                       PATH_KEYS as _PATHS, SCHEMA as SCHEMA, SECTIONS as _SECTIONS, Config, SettingSpec,
                                       get_path, key_path, set_path, unset_path, validate_config as _validate)
from carl_core.domain.types import (Catalog, CustomCard, CustomInfo, HfFileList, JsonObject, JsonValue,  # noqa: E402
                                    LocalDb, ModelInfo, SettingSource, SettingValue, Settings, Zone)
from carl_core.wiring import REPO as REPO, CarlPaths, build_carl  # noqa: E402

_PATHS_NOW = CarlPaths.from_env(os.environ)
CATALOG_FILE = _PATHS_NOW.catalog
CONF_DIR = _PATHS_NOW.conf_dir
CONFIG_FILE = _PATHS_NOW.config
LOCAL_FILE = _PATHS_NOW.local
OLD_LLAMA_ENV = _PATHS_NOW.legacy_llama


def _spec_dicts(keys: Mapping[str, SettingSpec]) -> Dict[str, Dict[str, JsonValue]]:
    return {k: s.to_dict() for k, s in keys.items()}


# The settings schema as dicts ({"type", "default", "env", ...} per key), as the monitor reads it.
MODEL_KEYS = _spec_dicts(_MODEL)
LLAMA_KEYS = _spec_dicts(_LLAMA)
PATH_KEYS = _spec_dicts(_PATHS)

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
def validate_config(cfg: object) -> Tuple[JsonObject, List[str]]:
    """(normalized config, [warnings]). Raises ConfigError on a bad value."""
    c, warn = _validate(cfg)
    return c.to_json(), warn


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


def gpu_limit_bytes() -> int:
    return app().gpu.limit()[0]


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


def auto_fit(goal: Optional[str] = None, scope: Optional[str] = None, models: Optional[List[ModelInfo]] = None,
             cfg: Optional[Mapping[str, object]] = None) -> AutoFit:
    """Auto fit for this Mac: the best ranked stock model for the goal (everyday / hard-code)
    from the scope (catalogue / downloaded); None takes config.json's llama.auto_goal / auto_fit."""
    c = _config(cfg)
    g, s = app().auto_settings(c)
    return app().auto_fit(models or app().all_models(c), as_goal(goal) if goal else g, as_scope(scope) if scope else s)


def resolve_launch(name: Optional[str] = None, cfg: Optional[Mapping[str, object]] = None
                   ) -> Tuple[ModelInfo, List[ModelInfo], Optional[str]]:
    """(model, all models, note) for a llama.cpp start: name, else config llama.model, else
    auto fit's pick (the best downloaded stock model that fits when the pick isn't downloaded)."""
    return app().resolve_launch(name, _config(cfg))


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
    return human_gb(b)


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


def _known(name: str, models: Optional[List[ModelInfo]] = None) -> ModelInfo:
    m = find(name, models)
    if not m:
        raise ConfigError(f"unknown model '{name}' (see: ./carl.sh models)")
    return m


def cmd_list(models: List[ModelInfo]) -> None:
    cfg = app().load_config()
    default = load_catalog().get("default")
    print(f"{'NAME':28} {'SIZE':>8}  {'STATUS':11} {'SOURCE':8} SUMMARY")
    for m in models:
        tuned = " [auto-tuned]" if (m.get("local") or {}).get("tune") else ""
        mark = " [default]" if m.get("name") == default else ""
        about = (f"{m.get('role')} (your card)" if m.get("custom") and m.get("role") else m.get("summary", ""))
        print(f"{m.get('name', ''):28} {human(m.get('bytes', 0)):>8}  {m.get('status', ''):11} "
              f"{m.get('source', ''):8} {about}{mark}{tuned}")
    d = app().models_dir(cfg)
    free = app().files.free_bytes(d)
    print(f"\ndir: {d}   free: {human(free) if free is not None else '?'}")
    print("download any GGUF: ./carl.sh download hf:OWNER/REPO/FILE.gguf   (files: ./carl.sh download hf:OWNER/REPO)")
    if any(m.get("custom") for m in models):
        print("describe a custom model (role, good for, rank, ...): ./carl.sh card NAME, or e in the dashboard's "
              "Models panel")


def cmd_config(argv: Sequence[str]) -> None:
    usage = "config [show|path|get KEY|set KEY VALUE|unset KEY]"
    sub = argv[0] if argv else "show"
    if sub == "path":
        print(CONFIG_FILE)
        return
    cfg = load_config()
    if sub == "show":
        for w in app().config_warnings():
            print(f"warning: {w}", file=sys.stderr)
        print(f"# {CONFIG_FILE}")
        print(json.dumps(cfg, indent=2))
        print("\n# keys (section.key: type, default)")
        for sec, keys in _SECTIONS.items():
            for k, s in keys.items():
                choices = " " + "|".join(s.choices) if s.choices else ""
                print(f"  {sec}.{k}: {s.kind}{choices}, default {s.default!r}")
        for k, s in _MODEL.items():
            print(f"  models.NAME.{k}: {s.kind}{' ' + '|'.join(s.choices) if s.choices else ''}")
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


def cmd_download(names: List[str]) -> int:
    if not names:
        raise ConfigError("usage: download NAME...|default|all|hf:OWNER/REPO/FILE.gguf")
    models = all_models()
    if names == ["all"]:
        names = [m.get("name", "") for m in models if m.get("source") == "catalog"]
    ok = True
    for n in names:
        if n == "default":
            n = pick_default(models)
            print(f"== this Mac's auto-fit pick: {n}")
        if n.startswith(("hf:", "http")) or (n.count("/") >= 1 and not find(n, models)):
            ok = download_hf(n) and ok
            continue
        m = find(n, models)
        if not m or not m.get("hf"):
            raise ConfigError(f"unknown model '{n}' (see: ./carl.sh models)")
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
    print("\n".join(_cards.describe(_known(name))))


GET_FIELDS = ("repo", "rev", "file", "sha256", "bytes", "alias", "spec", "notes", "name")


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
        results = [verify(find(n, models) or {"name": n, "status": "missing", "path": ""}) for n in names]
        return 0 if all(results) else 1
    elif cmd == "delete":
        m = find(_arg(a, 0, "delete NAME"))
        if not m:
            raise ConfigError(f"unknown model '{a[0]}'")
        delete(m)
        print(f"deleted {m.get('path')}")
    elif cmd == "path":
        name = _arg(a, 0, "path NAME")
        m = _known(name)
        if m.get("status") != "downloaded":
            raise ConfigError(f"{name} not downloaded (run: ./carl.sh download {name})")
        print(m.get("path"))
    elif cmd == "get":
        cmd_get(_arg(a, 0, "get NAME FIELD"), _arg(a, 1, "get NAME FIELD"))
    elif cmd == "default":
        print(pick_default())
    elif cmd == "downloaded":
        for m in all_models():
            if m.get("status") == "downloaded":
                print(m.get("name"))
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
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        raise ConfigError(f"unknown command '{cmd}' (carl.py --help)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

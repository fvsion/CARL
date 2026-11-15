"""Validation of the catalogue (src/carl/data/catalog.json) and models.json as they are loaded.

The documents are kept as they are (unknown fields survive a load/save round trip); only
the fields CARL acts on are checked: names, Hugging Face references (they become URLs and
paths), file paths, tune settings, context zones and model cards. A user's card for a
custom model (models.json models.NAME.card) is checked in full: it is only ever written
through CARL, and its text is drawn in the terminal.
"""
from __future__ import annotations

import re
from typing import AbstractSet, Dict, Optional, cast

from ..shared.errors import ConfigError
from ..shared.platform_words import MACOS, PlatformWords
from .drafts import KINDS
from .hf import local_file_name, validate_ref
from ..config.settings import LLAMA_KEYS, MODEL_KEYS, SCHEMA, coerce
from ..server.engines import ENGINES
from ..shared.types import Catalog, CustomCard, HfRef, JsonValue, LocalDb

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# The "good for" tags a model card may carry; "uncensored" only on abliterated models.
GOOD_FOR = ("agent coding", "hard code", "chat & writing", "uncensored")
CARD_TEXT = ("role", "why_use", "trade_offs", "hardware", "uncensored")
ROLE_MAX = 60
# A user's card (models.json models.NAME.card, custom models only): the catalogue's card
# fields, what CARL can't read from a file it did not ship, and the auto-fit opt-in.
CUSTOM_CARD_KEYS = ("label", "role", "good_for", "why_use", "trade_offs", "hardware", "uncensored", "abliterated",
                    "arch", "quant", "rank", "pick_instead", "thinking", "auto_fit", "engine")
ARCHS = ("dense", "moe")
THINKING = ("on-off", "effort")            # thinking on / off only, or effort levels
LABEL_MAX, QUANT_MAX, TEXT_MAX = 60, 40, 1000
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def validate_name(name: object, where: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ConfigError(f"{where}: {name!r} is not a model name (letters, digits, '.', '_', '-')")
    return name


def _settings(raw: object, where: str) -> None:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    for k, v in raw.items():
        if k in MODEL_KEYS:
            coerce(MODEL_KEYS[k], v, f"{where}.{k}")


def _zones(raw: object, where: str) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), int) for k in ("good", "slow", "very_slow")):
        raise ConfigError(f"{where}: needs good, slow and very_slow token counts")


def _speed(raw: object, where: str) -> None:
    """A catalogue entry's measured speed: prose / code / re-emit tok/s on a named Mac."""
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: needs prose, code and edit tok/s and the machine")
    nums = [raw.get(k) for k in ("prose", "code", "edit")]
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in nums)
    if not ok or not isinstance(raw.get("machine"), str):
        raise ConfigError(f"{where}: needs prose, code and edit tok/s and the machine")


def _object(raw: object, where: str) -> Dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    return raw


def _card(entry: Dict[str, JsonValue], names: Optional[AbstractSet[str]], at: str,
          words: PlatformWords = MACOS) -> None:
    """The model card fields: optional, but well-formed when present. names: the models a
    pick_instead entry may name (None: not checked here)."""
    for k in CARD_TEXT:
        if k in entry and not isinstance(entry[k], str):
            raise ConfigError(f"{at}: {k} must be text")
    role = entry.get("role")
    if isinstance(role, str) and len(role) > ROLE_MAX:
        raise ConfigError(f"{at}: role must be {ROLE_MAX} characters or less (it is a headline)")
    tags = entry.get("good_for", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and t in GOOD_FOR for t in tags):
        raise ConfigError(f"{at}: good_for must list tags from: {', '.join(GOOD_FOR)}")
    if "uncensored" in tags and not entry.get("abliterated"):
        raise ConfigError(f"{at}: only an abliterated model can have the tag uncensored")
    rank = entry.get("rank")
    if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool) or rank < 1):
        raise ConfigError(f"{at}: rank must be a whole number, 1 or more (1 is the best quality)")
    if "fast" in entry and not (entry["fast"] is True and entry.get("arch") == "dense"):
        raise ConfigError(f"{at}: fast is true or absent, and only on a dense model (auto fit takes it only when no "
                          "other model of its rank or better fits, else names it as the fast option)")
    speed = entry.get("ram_speed")
    if speed is not None and not (isinstance(speed, dict) and all(
            isinstance(speed.get(k), (int, float)) and not isinstance(speed.get(k), bool) and speed[k] > 0
            for k in ("write", "read"))):
        raise ConfigError(f"{at}: ram_speed needs write and read, in tok/s (more than 0)")
    if "thinking" in entry and entry["thinking"] not in THINKING:
        raise ConfigError(f"{at}: thinking must be one of: {', '.join(THINKING)}")
    if "engine" in entry and entry["engine"] not in ENGINES:       # absent: llama.cpp (domain/server/engines.py)
        raise ConfigError(f"{at}: engine must be one of: {', '.join(ENGINES)}")
    alts = entry.get("pick_instead", [])
    if not isinstance(alts, list):
        raise ConfigError(f"{at}: pick_instead must be a list")
    for alt in alts:
        if not (isinstance(alt, dict) and isinstance(alt.get("when"), str) and isinstance(alt.get("model"), str)
                and (names is None or alt.get("model") in names)):
            raise ConfigError(f"{at}: each pick_instead entry needs a model that CARL knows ({words.command} models) "
                              "and a when text")


def _text(card: Dict[str, JsonValue], key: str, limit: int, at: str) -> None:
    """A text field of a user's card: one line of printable text (it is drawn in the terminal)."""
    v = card.get(key)
    if v is None:
        return
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{at}: {key} must be text (to remove it, use unset)")
    if _CONTROL.search(v):
        raise ConfigError(f"{at}: {key} must not contain control characters (newlines, escapes)")
    if len(v) > limit:
        raise ConfigError(f"{at}: {key} must be {limit} characters or less")


def parse_custom_card(raw: object, names: Optional[AbstractSet[str]], at: str,
                      model: Optional[str] = None, words: PlatformWords = MACOS) -> CustomCard:
    """A user's card for a custom model, checked with the catalogue's rules (good_for tags,
    uncensored only when abliterated, role length, rank >= 1, pick_instead models exist) and
    its own: known fields only, printable text, arch dense / moe, thinking on-off / effort,
    the uncensored text only on an abliterated model, and auto_fit only for a ranked stock
    model with an arch. names None: pick_instead models are not checked (models.json as it
    loads; entries naming a model that is gone are dropped when the card is shown)."""
    card = _object(raw, at)
    unknown = sorted(k for k in card if k not in CUSTOM_CARD_KEYS)
    if unknown:
        raise ConfigError(f"{at}: unknown field {', '.join(unknown)} (fields: {', '.join(CUSTOM_CARD_KEYS)})")
    _card(card, names, at, words)
    for key, limit in (("label", LABEL_MAX), ("role", ROLE_MAX), ("quant", QUANT_MAX), ("why_use", TEXT_MAX),
                       ("trade_offs", TEXT_MAX), ("hardware", TEXT_MAX), ("uncensored", TEXT_MAX)):
        _text(card, key, limit, at)
    for alt in cast("list[Dict[str, JsonValue]]", card.get("pick_instead", [])):
        _text(alt, "when", TEXT_MAX, f"{at}: pick_instead")
        if model is not None and alt.get("model") == model:
            raise ConfigError(f"{at}: pick_instead cannot name the model itself")
    for key in ("abliterated", "auto_fit"):
        if key in card and not isinstance(card[key], bool):
            raise ConfigError(f"{at}: {key} must be true or false")
    if "arch" in card and card["arch"] not in ARCHS:
        raise ConfigError(f"{at}: arch must be one of: {', '.join(ARCHS)}")
    if "uncensored" in card and not card.get("abliterated"):
        raise ConfigError(f"{at}: the uncensored text is for an abliterated model only (set abliterated first)")
    if card.get("auto_fit") and ("rank" not in card or "arch" not in card or card.get("abliterated")):
        raise ConfigError(f"{at}: Auto fit chooses only a stock model with a quality rank. Set rank and arch, "
                          f"and set abliterated to no")
    return cast(CustomCard, card)


def _pinned_file(raw: object, at: str, key: str) -> HfRef:
    """A catalogue file reference (the model's "hf", its drafter's "draft"): checked as models.json's
    hf is, and it needs a repo, a GGUF file and its size."""
    ref = validate_ref(raw, at, key)
    if "repo" not in ref or "file" not in ref or "bytes" not in ref:
        raise ConfigError(f"{at}: {key} needs repo, file and bytes")
    local_file_name(ref["file"])
    return ref


def _extras(entry: Dict[str, JsonValue], ref: HfRef, at: str) -> None:
    """The files beside a catalogue model (WCATX): its drafters (drafts: a list, each with its kind, at most one of a
    kind) or the older single draft (an mtp-file drafter), and its vision projector. Each is pinned with its SHA-256
    (the download checks it), and none is the model's own file or another's."""
    if "draft" in entry and "drafts" in entry:
        raise ConfigError(f"{at}: give drafts (a list with the kind of each) or the older draft, not both")
    seen = {local_file_name(ref["file"])}
    if "draft" in entry:
        draft = _pinned_file(entry["draft"], at, "draft")
        if local_file_name(draft["file"]) in seen:
            raise ConfigError(f"{at}: draft names the model file itself")
    listed = entry.get("drafts", [])
    if not isinstance(listed, list):
        raise ConfigError(f"{at}: drafts must be a list")
    kinds = []
    for i, d in enumerate(listed):
        where = f"drafts[{i}]"
        kind = _object(d, f"{at}: {where}").get("kind")
        if kind not in KINDS:
            raise ConfigError(f"{at}: {where}.kind must be one of: {', '.join(KINDS)}")
        draft = _pinned_file(d, at, where)
        if not draft.get("sha256"):
            raise ConfigError(f"{at}: {where} needs its sha256 (the download checks it)")
        file = local_file_name(draft["file"])
        if kind in kinds or file in seen:
            raise ConfigError(f"{at}: {where} is a second drafter of the kind {kind}" if kind in kinds
                              else f"{at}: {where} names a file that is the model's or another drafter's")
        kinds.append(kind)
        seen.add(file)
    if "vision" in entry:
        vision = _pinned_file(entry["vision"], at, "vision")
        if not vision.get("sha256"):
            raise ConfigError(f"{at}: vision needs its sha256 (the download checks it)")
        if local_file_name(vision["file"]) in seen:
            raise ConfigError(f"{at}: vision names a file that is the model's or a drafter's")


def _draft_size(raw: object, at: str) -> None:
    """The header of the model size a drafter fits (draft_size): the architecture, the layer count and the expert count."""
    if not (isinstance(raw, dict) and isinstance(raw.get("arch"), str)
            and all(isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool) and raw[k] >= 0
                    for k in ("blocks", "experts"))):
        raise ConfigError(f"{at}: draft_size needs arch, blocks and experts (the GGUF header of the size the draft fits)")


def parse_catalog(raw: object, where: str, words: PlatformWords = MACOS) -> Catalog:
    """The catalogue, checked. Raises ConfigError naming the bad entry."""
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ConfigError(f"{where}: missing or wrong schema")
    doc: Dict[str, JsonValue] = raw
    models = doc.get("models")
    if not isinstance(models, list) or not isinstance(doc.get("default"), str):
        raise ConfigError(f"{where}: needs a models list and a default")
    for i, m in enumerate(models):
        entry = _object(m, f"{where}: models[{i}]")
        name = validate_name(entry.get("name"), f"{where}: models[{i}].name")
        at = f"{where}: {name}"
        ref = _pinned_file(entry.get("hf"), at, "hf")
        _extras(entry, ref, at)
        if "draft_size" in entry:
            _draft_size(entry["draft_size"], at)
        if "model" in entry:                       # the model it is a quantization of (quants.model_key; A73)
            validate_name(entry["model"], f"{at}: model")
        _settings(entry.get("tune", {}), f"{at}: tune")
        _zones(entry.get("ctx_zones"), f"{at}: ctx_zones")
        _speed(entry.get("speed"), f"{at}: speed")
    names = {m.get("name") for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)}
    for m in models:
        entry = _object(m, where)
        _card(entry, cast("set[str]", names), f"{where}: {entry.get('name')}", words)
    return cast(Catalog, doc)


def parse_local_db(raw: object, where: str) -> LocalDb:
    """models.json, checked ({"schema": 1, "models": {}} when there is none yet)."""
    if raw is None:
        return {"schema": SCHEMA, "models": {}}
    doc = _object(raw, where)
    models = _object(doc.setdefault("models", {}), f"{where}: models")
    for name, e in models.items():
        at = f"{where}: {name}"
        entry = _object(e, at)
        path = entry.get("path")
        if path is not None and (not isinstance(path, str) or "\x00" in path):
            raise ConfigError(f"{at}: path must be a file path")
        if "hf" in entry:
            validate_ref(entry["hf"], at)
        tune = entry.get("tune")
        if tune is not None:
            t = _object(tune, f"{at}: tune")
            _settings(t.get("settings", {}), f"{at}: tune.settings")
            _zones(t.get("ctx_zones"), f"{at}: tune.ctx_zones")
            if "ub" in t:
                coerce(LLAMA_KEYS["ub"], t["ub"], f"{at}: tune.ub")
        if "card" in entry:
            parse_custom_card(entry["card"], None, f"{at}: card", name)
    return cast(LocalDb, doc)


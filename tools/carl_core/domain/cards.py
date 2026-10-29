"""Model cards: what a model is for. Catalogue models carry theirs in host/catalog.json
(read-only); a custom model (a Hugging Face download or a file in the models folder) gets
the user's card from models.json (models.NAME.card), edited in the dashboard or with
`./carl.sh card NAME set FIELD VALUE`.

The fields, how a value typed as text becomes a card value, and how the card joins the
model record so every reader (the MODEL card, the lists, sort, filters, auto fit) sees it.
Pure: validation is records.parse_custom_card, storage the application's.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AbstractSet, Dict, List, Literal, Sequence, Tuple, cast

from .errors import ConfigError
from .records import ARCHS, GOOD_FOR, LABEL_MAX, QUANT_MAX, ROLE_MAX, THINKING
from .types import CustomCard, JsonValue, ModelInfo, PickInstead

Kind = Literal["text", "tags", "bool", "choice", "number", "picks"]


@dataclass(frozen=True)
class CardField:
    """One field of a user's card: its key in models.json, the label people see, how it is
    edited, and a one-line explanation."""
    key: str
    label: str
    kind: Kind
    help: str
    choices: Tuple[str, ...] = ()
    limit: int = 0                                   # text: the longest value allowed (0 = TEXT_MAX)


FIELDS: Tuple[CardField, ...] = (
    CardField("label", "label", "text", "The name that the lists and the model card show. Empty: the file name.",
              limit=LABEL_MAX),
    CardField("role", "role", "text", f"A short headline that tells what this model is ({ROLE_MAX} characters maximum).",
              limit=ROLE_MAX),
    CardField("good_for", "good for", "tags", "The tasks that the model does best. The use-case filters of the model lists "
              "use these tags. Use uncensored only on an abliterated model.", choices=GOOD_FOR),
    CardField("why_use", "why use it", "text", "The reason to use this model and not a different model."),
    CardField("trade_offs", "trade-offs", "text", "When to use a different model: speed, quality, memory, refusals."),
    CardField("hardware", "hardware", "text", "The Macs (RAM) that the model is for, and what fits on them."),
    CardField("abliterated", "abliterated", "bool", "The safety training is removed, so the model has no refusals. "
              "The stock filter hides it, and Auto fit never chooses it."),
    CardField("uncensored", "uncensored", "text", "What uncensored means for this model (abliterated models only)."),
    CardField("arch", "architecture", "choice", "dense: all weights work on each token (slower, stronger). MoE: a "
              "few experts work on each token (fast). The dense and MoE filters and the goals of Auto fit use it.",
              choices=ARCHS),
    CardField("quant", "quantization", "text", "The quantization, for example Q4_K_M or UD-IQ3_XXS.", limit=QUANT_MAX),
    CardField("rank", "quality rank", "number", "The quality order: 1 is the best. The catalogue uses ranks 1-10. "
              "Sort by quality uses it. Type a whole number, or leave it empty."),
    CardField("thinking", "thinking", "choice", "How the model thinks: on / off only, or effort levels. OpenCode and "
              "Pi show the related options.", choices=THINKING),
    CardField("auto_fit", "Auto fit", "bool", "Auto fit can choose this model for this Mac. The model must have a "
              "quality rank and an architecture, and it must not be abliterated. CARL does not measure your rank, so "
              "the default is no."),
    CardField("pick_instead", "pick instead", "picks", "Similar models: a different model, and when it is the better "
              "pick."),
)
FIELD: Dict[str, CardField] = {f.key: f for f in FIELDS}            # the same keys as CUSTOM_CARD_KEYS (tested)
CHOICE_TEXT: Dict[str, str] = {"dense": "dense", "moe": "MoE", "on-off": "on / off only",
                               "effort": "effort levels"}
_TRUE, _FALSE = ("yes", "true", "on", "1"), ("no", "false", "off", "0")


def field(key: str) -> CardField:
    """A field by its key; ConfigError naming the fields when there is none."""
    f = FIELD.get(key)
    if f is None:
        raise ConfigError(f"unknown card field '{key}'. The fields: {', '.join(f.key for f in FIELDS)}")
    return f


def parse_value(f: CardField, args: Sequence[str]) -> JsonValue:
    """A value typed on the command line as the card stores it: text (words joined), tags
    (comma-separated), yes / no, a choice, a whole number, or pick_instead entries
    (MODEL=WHEN, repeated, or a JSON list of {"model", "when"})."""
    text = " ".join(args).strip()
    if not text:
        raise ConfigError(f"{f.key}: give a value (to remove the field: unset {f.key})")
    if f.kind == "text":
        return text
    if f.kind == "tags":
        tags = [t.strip() for a in args for t in a.split(",") if t.strip()]
        bad = [t for t in tags if t not in f.choices]
        if bad:
            raise ConfigError(f"{f.key}: unknown tag {', '.join(bad)}. The tags: {', '.join(f.choices)}")
        return cast(JsonValue, list(dict.fromkeys(tags)))
    if f.kind == "bool":
        if text.lower() in _TRUE + _FALSE:
            return text.lower() in _TRUE
        raise ConfigError(f"{f.key}: type yes or no")
    if f.kind == "choice":
        v = text.lower()
        if v not in f.choices:
            raise ConfigError(f"{f.key}: type one of {', '.join(f.choices)}")
        return v
    if f.kind == "number":
        if not text.isdigit():
            raise ConfigError(f"{f.key}: type a whole number, 1 or more (1 is the best quality)")
        return int(text)
    return cast(JsonValue, parse_picks(args))


def parse_picks(args: Sequence[str]) -> List[PickInstead]:
    """pick_instead from the command line: MODEL=WHEN arguments, or one JSON list."""
    if len(args) == 1 and args[0].lstrip().startswith("["):
        try:
            raw = json.loads(args[0])
        except ValueError as e:
            raise ConfigError(f"pick_instead: not JSON ({e})") from None
        if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
            raise ConfigError('pick_instead: a JSON list of {"model": ..., "when": ...}')
        return [{"model": str(x.get("model", "")), "when": str(x.get("when", ""))} for x in raw]
    out: List[PickInstead] = []
    for a in args:
        model, eq, when = a.partition("=")
        if not eq or not model.strip() or not when.strip():
            raise ConfigError(f"pick_instead: '{a}' is not MODEL=WHEN (e.g. qwen3.8-27b='harder code')")
        out.append({"model": model.strip(), "when": when.strip()})
    return out


def set_field(card: CustomCard, key: str, args: Sequence[str]) -> CustomCard:
    """The card with one field set from command-line text (checked when it is saved)."""
    out: Dict[str, JsonValue] = dict(cast(Dict[str, JsonValue], card))
    out[key] = parse_value(field(key), args)
    return cast(CustomCard, out)


def unset_field(card: CustomCard, key: str) -> CustomCard:
    """The card without one field."""
    field(key)
    out: Dict[str, JsonValue] = {k: v for k, v in cast(Dict[str, JsonValue], card).items() if k != key}
    return cast(CustomCard, out)


def user_card(m: ModelInfo) -> CustomCard:
    """The user's card of a model as models.json holds it ({} when there is none)."""
    return cast(CustomCard, dict((m.get("local") or {}).get("card") or {}))


def editable_card(m: ModelInfo) -> CustomCard:
    """The user's card of a model as an edit starts from it: without the pick_instead
    entries that name a model which is gone (apply_card left them out of the record), so
    a later save is not refused for them."""
    card = user_card(m)
    if "pick_instead" in card:
        live = list(m.get("pick_instead") or [])
        if live:
            card["pick_instead"] = live
        else:
            del card["pick_instead"]
    return card


def apply_card(info: ModelInfo, card: CustomCard, names: AbstractSet[str]) -> None:
    """Join a user's card into a custom model's record (so every reader sees it). A
    pick_instead entry naming a model that is gone (deleted, renamed) is left out."""
    fields: Dict[str, JsonValue] = dict(cast(Dict[str, JsonValue], card))
    if "pick_instead" in card:
        fields["pick_instead"] = cast(JsonValue, [a for a in card["pick_instead"]
                                                  if a.get("model") in names and a.get("model") != info.get("name")])
    cast(Dict[str, JsonValue], info).update(fields)


def shown(f: CardField, v: object) -> str:
    """A card value as one line of text."""
    if v is None:
        return "-"
    if f.kind == "tags" and isinstance(v, list):
        return ", ".join(str(t) for t in v) or "-"
    if f.kind == "bool":
        return "yes" if v else "no"
    if f.kind == "choice":
        return CHOICE_TEXT.get(str(v), str(v))
    if f.kind == "picks" and isinstance(v, list):
        return "; ".join(f"{a.get('model')} when {a.get('when')}" for a in v if isinstance(a, dict)) or "-"
    return str(v)


def card_rows(m: ModelInfo) -> Tuple[str, List[Tuple[str, str]], List[str]]:
    """A model's card for the command line: whose card it is, (label, value) per field (a custom
    model: every field, unset ones as -, with the key to set it; a catalogue model: the fields it
    has), and for a custom model how to edit it."""
    name = m.get("name", "")
    rec = cast(Dict[str, object], m)
    if m.get("custom"):
        card = user_card(m)
        head = (f"{name} is a custom model. This is your card (models.json)." if card else
                f"{name} is a custom model. It has no card yet.")
        keys = [f.key for f in FIELDS]
        src: Dict[str, object] = dict(cast(Dict[str, object], card))
    else:
        head = f"{name} is a catalogue model. You cannot change its card (host/catalog.json)."
        keys = [f.key for f in FIELDS if f.key in rec]
        src = rec
    rows = []
    for k in keys:
        f = FIELD[k]
        label = f"{f.label} ({f.key})" if m.get("custom") and f.label != f.key else f.label
        rows.append((label, shown(f, src.get(k))))
    hints: List[str] = []
    if m.get("custom"):
        hints = [f"To set a field: ./carl.sh card {name} set FIELD VALUE. To remove one: ./carl.sh card {name} unset "
                 f"FIELD.",
                 f"good_for takes tags with commas between them ({','.join(GOOD_FOR[:2])}). abliterated and auto_fit "
                 f"take yes or no. arch takes {' or '.join(ARCHS)}. thinking takes {' or '.join(THINKING)}. "
                 f"pick_instead takes MODEL=WHEN (one for each model) or a JSON list."]
    return head, rows, hints


def describe(m: ModelInfo) -> List[str]:
    """A model's card for the command line, one line per field (card_rows)."""
    head, rows, hints = card_rows(m)
    tw = max((len(t) for t, _ in rows), default=0)
    return [head] + [f"  {t:<{tw}}  {v}" for t, v in rows] + ([""] + hints if hints else [])

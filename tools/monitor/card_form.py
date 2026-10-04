"""Edit mode for a custom model's card (the Models panel, e): the form's rows, the text being
typed and the card as it changes. Pure: the controller saves it through the ModelStore (which
checks it with the catalogue's rules) and card_view draws it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from carl_core.domain.cards import FIELDS, CardField

from .model import JSONDict

ItemKind = Literal["text", "number", "choice", "bool", "tag", "pick", "add"]


@dataclass(frozen=True)
class Item:
    """One row of the form: a field, one "good for" tag, one pick-instead entry, or the row
    that adds an entry."""
    field: CardField
    kind: ItemKind
    tag: str = ""
    index: int = -1


def form_items(values: JSONDict) -> List[Item]:
    """The rows for a card: one per field, one per tag, one per pick-instead entry + "add"."""
    out: List[Item] = []
    for f in FIELDS:
        if f.kind == "tags":
            out += [Item(f, "tag", tag=t) for t in f.choices]
        elif f.kind == "picks":
            picks = values.get(f.key) or []
            out += [Item(f, "pick", index=i) for i in range(len(picks))] + [Item(f, "add")]
        elif f.kind == "text":
            out.append(Item(f, "text"))
        else:
            out.append(Item(f, f.kind))
    return out


@dataclass
class CardForm:
    """A card being edited. values: the card so far (models.json's shape); typing: the text of
    the selected row while it is typed (None: not typing); adding: the model of a new
    pick-instead entry whose when-text is typed; notes: dim hints beside a field (where a
    value came from)."""
    model: str
    values: JSONDict
    notes: Dict[str, str] = field(default_factory=dict)
    row: int = 0
    typing: Optional[str] = None
    adding: Optional[str] = None
    error: str = ""
    changed: bool = False

    def items(self) -> List[Item]:
        return form_items(self.values)

    def item(self) -> Item:
        its = self.items()
        self.row = max(0, min(self.row, len(its) - 1))
        return its[self.row]

    def select(self, i: int) -> None:
        """Select a row (not while typing: Enter or Esc ends that first)."""
        if self.typing is None:
            self.row = max(0, min(i, len(self.items()) - 1))
            self.error = ""

    def move(self, step: int) -> None:
        self.select(self.row + step)

    def _set(self, key: str, value: object) -> None:
        """Set a field (None removes it) and mark the card changed."""
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = value
        self.notes.pop(key, None)
        self.changed = True
        self.error = ""

    def enter(self) -> bool:
        """Enter on the selected row: start typing a text or number, toggle a tag or yes / no,
        step a choice, edit an entry's when-text. True: the "add" row wants a model picked."""
        it = self.item()
        if it.kind == "add":
            return True
        if it.kind in ("text", "number"):
            v = self.values.get(it.field.key)
            self.typing = "" if v is None else str(v)
        elif it.kind == "pick":
            self.typing = str(self._picks()[it.index].get("when", ""))
        else:
            self.cycle(1)
        return False

    def cycle(self, step: int) -> None:
        """← → (or space): the next / previous choice, or toggle a tag or yes / no."""
        it = self.item()
        key = it.field.key
        if it.kind == "choice":
            opts: List[Optional[str]] = [None, *it.field.choices]
            cur = self.values.get(key)
            i = opts.index(cur) if cur in opts else 0
            self._set(key, opts[(i + step) % len(opts)])
        elif it.kind == "bool":
            self._set(key, not self.values.get(key, False))
        elif it.kind == "tag":
            tags = [str(t) for t in self.values.get(key) or []]
            tags = [t for t in tags if t != it.tag] if it.tag in tags else tags + [it.tag]
            self._set(key, [t for t in it.field.choices if t in tags] or None)    # the catalogue's order

    def clear(self) -> None:
        """x: remove the selected field's value (a tag: untick it; an entry: remove it)."""
        it = self.item()
        key = it.field.key
        if it.kind == "tag":
            tags = [t for t in self.values.get(key) or [] if t != it.tag]
            self._set(key, tags or None)
        elif it.kind == "pick":
            picks = [p for i, p in enumerate(self._picks()) if i != it.index]
            self._set(key, picks or None)
        elif it.kind != "add":
            self._set(key, None)

    def _picks(self) -> List[JSONDict]:
        return [dict(p) for p in self.values.get("pick_instead") or []]

    # ---------------------------------------------------------------- typing
    def type_text(self, text: str) -> None:
        if self.typing is not None:
            self.typing += text
            self.error = ""

    def backspace(self) -> None:
        if self.typing:
            self.typing = self.typing[:-1]

    def cancel_typing(self) -> None:
        """Esc while typing: drop the text, keep the field as it was."""
        self.typing, self.adding, self.error = None, None, ""

    def add_pick(self, model: str) -> None:
        """A model chosen for a new pick-instead entry: its when-text is typed next."""
        self.adding, self.typing, self.error = model, "", ""

    def commit(self) -> bool:
        """Enter while typing: keep the text (empty: unset the field). False with an error
        when the text can't be used (typing goes on)."""
        if self.typing is None:
            return True
        text = " ".join(self.typing.split())                     # one line, single spaces
        it = self.item()
        key = it.field.key
        if self.adding is not None or it.kind == "pick":
            if not text:
                self.error = ("type when it is the better pick" if self.adding is not None else
                              "type when it is the better pick. To remove the entry, press Esc, then x")
                return False
            picks = self._picks()
            if self.adding is not None:
                picks.append({"model": self.adding, "when": text})
            else:
                picks[it.index]["when"] = text
            self._set(key, picks)
        elif it.kind == "number":
            if text and not (text.isdigit() and int(text) >= 1):
                self.error = f"{it.field.label}: type a whole number, 1 or more (empty: no rank)"
                return False
            self._set(key, int(text) if text else None)
        else:
            self._set(key, text or None)
        self.typing, self.adding = None, None
        return True

    def card(self) -> JSONDict:
        """The card to save: the fields that have a value."""
        return {k: v for k, v in self.values.items() if v not in (None, "", [])}

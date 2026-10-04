"""The Models panel's card edit mode (e on a custom model): opening the form, its keys and clicks,
the pick-instead drop-down, and saving the card (card_form.py holds the form, card_view.py draws it)."""
from __future__ import annotations

import os
from typing import List, cast

from carl_core.domain.cards import editable_card
from carl_core.domain.types import ModelInfo as CoreModelInfo

from .card_form import CardForm
from .keys import BACKSPACE, DOWN, ENTER, ESC, LEFTKEY, PANEL_PASSTHROUGH, RIGHT, UP, strip_escapes
from .model import JSONDict, ModelInfo
from .settings import SettingsService
from .settings_view import SettingsView
from .state import PickItem, Picker, UIState

READ_ONLY = ("You cannot edit a catalogue card. Only custom models (Hugging Face downloads and files in the models "
             "folder) have a card to edit.")


class CardActions:
    """Acts on the card edit form."""

    def __init__(self, ui: UIState, svc: SettingsService, view: SettingsView) -> None:
        self.ui = ui
        self.svc = svc
        self.view = view

    def open(self, m: ModelInfo) -> None:
        """e on a model: the edit form for a custom model's card; a catalogue model's is read-only.
        A card without arch / quant starts with what the GGUF header says."""
        ui = self.ui
        if not m.get("custom"):
            ui.toast(READ_ONLY, 8)
            return
        values: JSONDict = dict(editable_card(cast(CoreModelInfo, m)))     # the same record, carl.py's type
        notes = {} if "label" in values else {"label": f"(the file name: {os.path.basename(m['path'])})"}
        if m["status"] == "downloaded" and not ("arch" in values and "quant" in values and "thinking" in values):
            try:
                info = self.svc.store.header_info(m["path"])
            except Exception:           # an unreadable header: the user fills them in
                info = {}
            for key in ("arch", "quant", "thinking"):
                if key not in values and info.get(key) not in (None, "", "?"):
                    values[key] = info[key]
                    notes[key] = "(from the GGUF header)"
        ui.card = CardForm(m["name"], values, notes)

    def keys(self, rest: str) -> bool:
        """Keys of the card edit form. While a text is typed every key is text (Enter keeps it, Esc
        drops it); else ↑↓ select, Enter edits, ← → / space change, x clears, s saves, Esc cancels.
        True when the input was used here."""
        ui = self.ui
        f = ui.card
        if f is None:
            return False
        if f.typing is not None:
            if rest == ESC:
                f.cancel_typing()
                return True
            text = strip_escapes(rest)              # arrow keys, bracketed-paste markers
            for i, ch in enumerate(text):
                if ch in ENTER and i == len(text) - 1:
                    f.commit()
                elif ch in ENTER or ch == "\t":
                    f.type_text(" ")                # a pasted line break
                elif ch in BACKSPACE:
                    f.backspace()
                elif ch.isprintable():
                    f.type_text(ch)
            return True
        if rest == ESC:
            self.close(f)
        elif UP in rest or DOWN in rest:
            f.move(-1 if UP in rest else 1)
        elif RIGHT in rest or LEFTKEY in rest or rest == " ":
            f.cycle(-1 if LEFTKEY in rest else 1)
        elif rest in ENTER:
            if f.enter():
                self.open_picker(f)
        elif rest == "x":
            f.clear()
        elif rest == "s":
            self.save(f)
        elif rest in ("[", "]"):
            ui.toast("first press s to save the card, or Esc to cancel it", 5)
        else:
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        return True

    def button(self, act: str, f: CardForm) -> None:
        """A click in the form: a row selects it; Save, Cancel, Edit field, Clear field."""
        if act.startswith("cardrow:"):
            f.select(int(act[8:]))
        elif act == "cardsave":
            self.save(f)
        elif act == "cardcancel":
            if f.typing is not None:
                f.cancel_typing()
            else:
                self.close(f)
        elif act == "cardenter":
            if f.typing is not None:
                f.commit()
            elif f.enter():
                self.open_picker(f)
        elif act == "cardclear" and f.typing is None:
            f.clear()

    def close(self, f: CardForm) -> None:
        """Esc / Cancel: leave the form; nothing is saved."""
        self.ui.card = None
        self.ui.toast("card edit cancelled: nothing saved" if f.changed else "card edit closed", 5)

    def open_picker(self, f: CardForm) -> None:
        """The model drop-down for a new pick-instead entry (every model but this one, in the lists'
        sort order, no filter)."""
        items: List[PickItem] = [(m["name"], m) for m in self.view.visible(self.ui.msort, 0) if m["name"] != f.model]
        self.ui.picker = Picker("PICK INSTEAD: WHICH MODEL?", items, "pickcard",
                                foot="Press ↑ ↓ to select the alternative. Press Enter to choose it, then type when "
                                     "it is the better pick. Press Esc to go back to the card.")

    def save(self, f: CardForm) -> None:
        """s / Save: keep a text being typed, check and store the card; errors stay in the form.
        The lists, the MODEL card, sort and filters read it at once."""
        ui = self.ui
        if not f.commit():
            return
        try:
            self.svc.store.save_card(f.model, f.card())
        except Exception as e:      # carl.ConfigError (a rule the card breaks), models.json unwritable
            msg, head = str(e), f"{f.model}: card: "
            f.error = msg[len(head):] if msg.startswith(head) else msg
            return
        ui.card = None
        self.svc.models.get(refresh=True)
        ui.mrow = next((i for i, x in enumerate(self.view.visible(ui.msort, ui.mfilter)) if x["name"] == f.model), ui.mrow)
        ui.toast(f"card saved for {f.model}: the lists and its MODEL card show it", 6)

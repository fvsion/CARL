"""Draws the card edit form (the Models panel's edit mode): one row per field, the selected one
marked, the text being typed with a cursor, the field's help, an error, Save / Cancel."""
from __future__ import annotations

from typing import List, Tuple

from carl_core.domain.cards import CHOICE_TEXT
from carl_core.domain.records import GOOD_FOR, ROLE_MAX

from .card_form import CardForm, Item
from .fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Key, Ln, Row, button_rows, cwrap, draw_card, indent, vlen
from .words import plural

LABEL_W = 15                                           # the label column
REV = "\x1b[7m"                                        # reverse video: the selected row's label
TAG_COLOUR = {"agent coding": GRN, "hard code": CYN, "chat & writing": B, "uncensored": RED}
LABELS = {"uncensored": "what uncensored means"}         # the form's label where the field's own is too short
FORM_KEYS: List[Key] = [("↑↓", "field"), ("Enter", "edit"), ("← → space", "change a choice"), ("x", "clear"), ("s", "save"),
             ("Esc", "cancel")]
TYPING_KEYS: List[Key] = [("Enter", "keep the text"), ("Esc", "drop it"), ("Backspace", "delete")]


def value_text(form: CardForm, it: Item, sel: bool) -> str:
    """The value of one row as shown (coloured): typed text with a cursor, a tick box, a choice."""
    key = it.field.key
    v = form.values.get(key)
    typing = sel and form.typing is not None
    if it.kind == "add":
        if typing and form.adding:
            return f"{CYN}{form.adding}{R} {DIM}when{R} {CYN}{form.typing}▏{R}"
        return f"{DIM}+ add a model (press Enter){R}"
    if it.kind == "pick":
        p = (v or [])[it.index]
        when = f"{CYN}{form.typing}▏{R}" if typing else str(p.get("when", ""))
        return f"{CYN}{p.get('model')}{R} {DIM}when{R} {when}"
    if typing:
        count = f"  {DIM}{len(form.typing or '')}/{ROLE_MAX}{R}" if key == "role" else ""
        return f"{CYN}{form.typing}▏{R}{count}"
    if it.kind == "tag":
        on = it.tag in (v or [])
        return f"{CYN}[{'x' if on else ' '}]{R} {TAG_COLOUR.get(it.tag, '')}{it.tag}{R}"
    if it.kind == "bool":
        return f"{YEL if key == 'abliterated' and v else ''}{'yes' if v else 'no'}{R}"
    if it.kind == "choice":
        text = CHOICE_TEXT.get(str(v), str(v)) if v else f"{DIM}–{R}"
        return f"{CYN}‹{R} {text} {CYN}›{R}" if sel else text
    note = form.notes.get(key, "")
    if v is None:
        return f"{DIM}–{'  ' + note if note else ''}{R}"
    return str(v) + (f"  {DIM}{note}{R}" if note else "")


def row_lines(form: CardForm, it: Item, i: int, first: bool, w: int) -> List[CardLine]:
    """One form row (wrapped under the value column); first: the first row of its field (the
    label is shown once for the tags and the pick-instead entries)."""
    sel = i == form.row
    label = LABELS.get(it.field.key, it.field.label) if first else ""
    pre = f"{CYN}{B}›{R} " if sel else "  "
    lab = f"{label:<{LABEL_W - 2}}"
    head = pre + (f"{REV}{lab}{R}" if sel else lab) + "  "
    pad = " " * vlen(head)
    lines = cwrap(value_text(form, it, sel), w - vlen(head))
    out: List[CardLine] = [Ln(head + lines[0], act=f"cardrow:{i}")]
    out += [Ln(pad + x, act=f"cardrow:{i}") for x in lines[1:]]
    return out


def form_lines(form: CardForm, w: int) -> Tuple[List[CardLine], int, int]:
    """Every row's lines, and the first and last line of the selected row."""
    out: List[CardLine] = []
    start = end = 0
    prev = ""
    for i, it in enumerate(form.items()):
        if i == form.row:
            start = len(out)
        out += row_lines(form, it, i, it.field.key != prev, w)
        if i == form.row:
            end = len(out) - 1
        prev = it.field.key
    return out, start, end


def draw_form(form: CardForm, cols: int, height: int) -> List[Row]:
    """The edit form as a card cols wide, at most height rows: the rows scroll to keep the
    selected one in view; the help, the error and the buttons stay below them."""
    w = cols - 2
    tw = w - 4
    it = form.item()
    intro = cwrap(f"{DIM}Describe {form.model}. CARL cannot find the purpose of a model from its file. The MODEL card, "
                  f"the model lists (role, tags), the quality sort and the filters read this card. Auto fit uses the "
                  f"model only if you set Auto fit to yes. Good-for tags: {', '.join(GOOD_FOR)}.{R}", tw)
    foot: List[CardLine] = [""]
    foot += cwrap(f"{B}{it.field.label}{R}  {DIM}{it.field.help}{R}", tw)
    if form.typing is not None:
        foot += cwrap(f"{YEL}You type now. Enter keeps the text, Esc drops it. You can paste.{R}", tw)
    if form.error:
        foot += cwrap(f"{RED}{form.error}{R}", tw)
    foot += ["", *button_rows("", [("Save (s)", "cardsave"), ("Cancel (Esc)", "cardcancel"),
                                    ("Edit the field (Enter)", "cardenter"), ("Clear the field (x)", "cardclear")], tw)]
    rows, start, end = form_lines(form, tw)
    room = max(height - 2 - len(intro) - 1 - len(foot) - 2, 4)    # 2: the card's borders; 2: the "more" lines
    top = 0
    if len(rows) > room:
        top = min(max(end - room + 1, 0), start)
        top = max(0, min(top, len(rows) - room))
    shown = rows[top:top + room]
    above = [f"{DIM}  ↑ {plural(top, 'more line')} above{R}"] if top else []
    below = [f"{DIM}  ↓ {plural(len(rows) - top - room, 'more line')} below{R}"] if top + room < len(rows) else []
    L: List[CardLine] = [*intro, "", *above, *shown, *below, *foot]
    title = f"{B}{form.model}{R}  {DIM}custom model · your card (models.json){' · changed' if form.changed else ''}{R}"
    return indent(draw_card("cardedit", "EDIT THE CARD", title, L, w, 1))

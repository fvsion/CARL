"""The Agents panel (Phase 23.4.4): the thinking of the main session and of the coder, for each model. A model
picker (the selected model's values), then Main thinking and Coder thinking with your choice, the values the model
takes and the recommended value; a note when the coder does not think. CARL saves each change at once (no Apply, no
restart): OpenCode and Pi use it after their next update. r undoes this session's changes of the model, x uses the
recommended values. One section with three levels (simple: no Values column). Pure."""
from __future__ import annotations

from typing import List, Optional, Tuple

from ....application.settings_service import (CODER_OFF_NOTE, THINKING_LABELS, THINKING_ROWS, AgentsInfo, coder_off)
from ....domain.catalog import thinking
from ....ui.fmt import B, CYN, DIM, R, RED, YEL, CardLine, Ln, Row, button_rows, cwrap, fit
from ...act import AgentsOp, agents
from ...settings_texts import shown_value
from ...shown import Shown
from ...state import UIState
from .page import Part, body_height, layout, scrolled, section

AGENT_ROWS = ("model", *THINKING_ROWS)          # ↑↓: the model, Main thinking, Coder thinking
LABEL_W = 17                                    # the label column (as the Server panel's)
CHOICE_W = 23                                   # Your choice: "‹ same as main ›" and room
PROSE_W = 110                                   # the text above and under the table wraps here at most (the mock-up)
INTRO = ("The thinking of the main session and of the coder, for each model. OpenCode and Pi use a change after the "
         "next update (Connect tab: u; other computers: P). /carl can change the coder's thinking on one computer.")


def word(key: str, value: str) -> str:
    """A thinking value as the panel shows it ("main": same as main)."""
    return shown_value(key, value)


def values_text(info: AgentsInfo, key: str) -> str:
    return ", ".join(word(key, v) for v in info.choices[key])


def values_width() -> int:
    """The widest Values cell (an effort model's coder): the column keeps this width for every model, as the mock-up."""
    return len(", ".join(shown_value("thinking_coder", v) for v in thinking.choices("effort", "coder"))) + 2


def rec_text(info: AgentsInfo, key: str) -> str:
    return f"{word(key, info.rec[key])} ({info.source[key]})"


def columns(tw: int, info: AgentsInfo, full: bool) -> Tuple[int, int, int]:
    """(Your choice, Values, Recommended) widths in tw columns: the mock-up's at 115 columns and more; narrower, the
    choice column shrinks first, then the values wrap in their own column. Values 0: not shown (simple)."""
    recw = max(len("Recommended"), *(len(rec_text(info, k)) for k in THINKING_ROWS))
    cw = CHOICE_W
    if not full:
        return cw, 0, max(tw - 2 - LABEL_W - cw, recw)
    vw = values_width()
    over = 2 + LABEL_W + cw + vw + recw - tw
    if over > 0:
        cw -= min(over, cw - 18)                 # "‹ same as main ›" and 2 spaces
        over = 2 + LABEL_W + cw + vw + recw - tw
    if over > 0:
        vw = max(vw - over, 16)
    return cw, vw, max(tw - 2 - LABEL_W - cw - vw, recw)


def cell_lines(text: str, w: int) -> List[str]:
    """A Values cell in its column: whole values on each line (", " between them)."""
    if len(text) <= w:
        return [text]
    out: List[str] = []
    line = ""
    for part in text.split(", "):
        nxt = f"{line}, {part}" if line else part
        if line and len(nxt) + 1 > w:            # the comma stays on the line before
            out.append(line + ",")
            line = part
        else:
            line = nxt
    return out + [line]


def table(sel: int, info: AgentsInfo, tw: int, full: bool) -> List[CardLine]:
    """The model row, then Setting, Your choice, Values (full), Recommended. sel: the selected row."""
    mark = f"{CYN}{B}›{R} "
    pre = mark if sel == 0 else "  "
    c0 = 2 + LABEL_W
    name = info.name or "–"
    L: List[CardLine] = [
        Ln(f"{pre}{B if sel == 0 else ''}{'Model':<{LABEL_W}}{R}{name} ▾",
           spans=[(0, c0, agents(AgentsOp.ROW, 0)), (c0, c0 + len(name) + 2, agents(AgentsOp.PICK))]), ""]
    cw, vw, recw = columns(tw, info, full)
    head = f"  {B}{'Setting':<{LABEL_W}}{R}{B}{'Your choice':<{cw}}{R}"
    if vw:
        head += f"{B}{'Values':<{vw}}{R}"
    L.append(head + f"{B}Recommended{R}")
    for i, key in enumerate(THINKING_ROWS, start=1):
        on = sel == i
        val = word(key, info.mine[key])
        cell = f"‹ {val} ›" if on else val
        spans: List[Tuple[int, int, str]] = [(0, c0, agents(AgentsOp.ROW, i))]
        if on:
            spans += [(c0, c0 + 2, agents(AgentsOp.DEC, i)), (c0 + 2, c0 + 2 + len(val), agents(AgentsOp.ROW, i)),
                      (c0 + 3 + len(val), c0 + 5 + len(val), agents(AgentsOp.INC, i))]
        else:
            spans.append((c0, c0 + cw, agents(AgentsOp.ROW, i)))
        text = f"{mark if on else '  '}{B if on else ''}{THINKING_LABELS[key]:<{LABEL_W}}{R}{fit(cell, cw - 1)} "
        vals = cell_lines(values_text(info, key), vw - 2) if vw else []
        if vals:
            text += f"{DIM}{vals[0]:<{vw - 1}}{R} "
        L.append(Ln(text + fit(rec_text(info, key), recw), spans=spans))
        for more in vals[1:]:                    # the values that did not fit go on under their column
            L.append(f"{' ' * (2 + LABEL_W + cw)}{DIM}{more}{R}")
    return L


def agents_panel(ui: UIState, info: Optional[AgentsInfo], cols: int, height: int, error: str = "",
                 out: Optional[Shown] = None) -> List[Row]:
    """The panel: one section AGENTS (the model's name as its summary). info None: no model is known. error: the model
    list cannot be read (the catalogue or models.json)."""
    out = Shown() if out is None else out
    sel = out.fix("agents_row", max(0, min(ui.agents_row, len(AGENT_ROWS) - 1)))
    h = body_height(height)

    def agents_section(w: int) -> List[Row]:
        tw = w - 4
        pw = min(tw, PROSE_W)
        full = ui.levels.get("agents", 1) == 2
        L: List[CardLine] = [*cwrap(INTRO, pw), ""]
        if error:
            L += [*cwrap(f"{RED}The model list is not available: {error}{R}", pw), ""]
        if info is None:
            L.append(f"{DIM}No model is known. The Models panel lists the models.{R}")
            return section(ui, "agents", "AGENTS", "", L, w, 3)
        L += table(sel, info, tw, full)
        if coder_off(info.mine):
            L += ["", *cwrap(f"{YEL}{CODER_OFF_NOTE}{R}", pw)]
        L += ["", *button_rows("", [("Undo my changes (r)", agents(AgentsOp.UNDO)),
                                    ("Use the recommended settings (x)", agents(AgentsOp.REC))], tw)]
        return section(ui, "agents", "AGENTS", info.name, L, w, 3)

    rows = layout(out, [Part("agents", agents_section)], cols, 0)
    out.keys = [("↑↓", "setting"), ("← →", "change"), *([("Enter", "choose a model")] if sel == 0 else []),
                ("r", "undo"), ("x", "recommended")]
    if len(rows) > h:
        out.keys.append(("PgUp PgDn", "scroll"))
    out.keys += [("Tab", "section"), ("L", "level")]
    out.keys_more = ["CARL saves each change at once. The server does not restart.",
                    "OpenCode and Pi use a change after their next update: the Connect tab, u (other computers: P).",
                    "The simple level leaves out the Values column: ← → shows each value."]
    return scrolled(ui, out, rows, h, "page_scroll")


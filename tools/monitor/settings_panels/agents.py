"""The Agents panel (Phase 23.4.4): the thinking of the main session and of the coder, for each model. A model
picker (the selected model's values), then Main thinking and Coder thinking with your choice, the values the model
takes and the recommended value; a note when the coder does not think. CARL saves each change at once (no Apply, no
restart): OpenCode and Pi use it after their next update. r undoes this session's changes of the model, x uses the
recommended values. One section with three levels (simple: no Values column). Pure."""
from __future__ import annotations

from typing import List, Optional, Tuple

from carl_core.domain import thinking

from ..cards import sections as register
from ..fmt import B, CYN, DIM, R, RED, YEL, CardLine, Ln, Row, button_rows, cwrap, fit
from ..settings import CODER_OFF_NOTE, THINKING_LABELS, THINKING_ROWS, AgentsInfo, coder_off, shown_value
from ..state import UIState
from .page import Part, body_height, layout, scrolled, section

register(three=["agents"])

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


# the widest Values cell (an effort model's coder): the column keeps this width for every model, as the mock-up
VALUES_W = len(", ".join(shown_value("thinking_coder", v) for v in thinking.choices("effort", "coder"))) + 2


def rec_text(info: AgentsInfo, key: str) -> str:
    return f"{word(key, info.rec[key])} ({info.source[key]})"


def columns(tw: int, info: AgentsInfo, full: bool) -> Tuple[int, int, int]:
    """(Your choice, Values, Recommended) widths in tw columns: the mock-up's at 115 columns and more; narrower, the
    choice column shrinks first, then the values wrap in their own column. Values 0: not shown (simple)."""
    recw = max(len("Recommended"), *(len(rec_text(info, k)) for k in THINKING_ROWS))
    cw = CHOICE_W
    if not full:
        return cw, 0, max(tw - 2 - LABEL_W - cw, recw)
    vw = VALUES_W
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


def table(ui: UIState, info: AgentsInfo, tw: int, full: bool) -> List[CardLine]:
    """The model row, then Setting, Your choice, Values (full), Recommended."""
    sel = ui.agents_row
    mark = f"{CYN}{B}›{R} "
    pre = mark if sel == 0 else "  "
    c0 = 2 + LABEL_W
    name = info.name or "–"
    L: List[CardLine] = [
        Ln(f"{pre}{B if sel == 0 else ''}{'Model':<{LABEL_W}}{R}{name} ▾",
           spans=[(0, c0, "agents:row:0"), (c0, c0 + len(name) + 2, "agents:pick")]), ""]
    cw, vw, recw = columns(tw, info, full)
    head = f"  {B}{'Setting':<{LABEL_W}}{R}{B}{'Your choice':<{cw}}{R}"
    if vw:
        head += f"{B}{'Values':<{vw}}{R}"
    L.append(head + f"{B}Recommended{R}")
    for i, key in enumerate(THINKING_ROWS, start=1):
        on = sel == i
        val = word(key, info.mine[key])
        cell = f"‹ {val} ›" if on else val
        spans: List[Tuple[int, int, str]] = [(0, c0, f"agents:row:{i}")]
        if on:
            spans += [(c0, c0 + 2, f"agents:dec:{i}"), (c0 + 2, c0 + 2 + len(val), f"agents:row:{i}"),
                      (c0 + 3 + len(val), c0 + 5 + len(val), f"agents:inc:{i}")]
        else:
            spans.append((c0, c0 + cw, f"agents:row:{i}"))
        text = f"{mark if on else '  '}{B if on else ''}{THINKING_LABELS[key]:<{LABEL_W}}{R}{fit(cell, cw - 1)} "
        vals = cell_lines(values_text(info, key), vw - 2) if vw else []
        if vals:
            text += f"{DIM}{vals[0]:<{vw - 1}}{R} "
        L.append(Ln(text + fit(rec_text(info, key), recw), spans=spans))
        for more in vals[1:]:                    # the values that did not fit go on under their column
            L.append(f"{' ' * (2 + LABEL_W + cw)}{DIM}{more}{R}")
    return L


def agents_panel(ui: UIState, info: Optional[AgentsInfo], cols: int, height: int, error: str = "") -> List[Row]:
    """The panel: one section AGENTS (the model's name as its summary). info None: no model is known. error: the model
    list cannot be read (the catalogue or models.json)."""
    ui.agents_row = max(0, min(ui.agents_row, len(AGENT_ROWS) - 1))
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
        L += table(ui, info, tw, full)
        if coder_off(info.mine):
            L += ["", *cwrap(f"{YEL}{CODER_OFF_NOTE}{R}", pw)]
        L += ["", *button_rows("", [("Undo my changes (r)", "agents:undo"),
                                    ("Use the recommended settings (x)", "agents:rec")], tw)]
        return section(ui, "agents", "AGENTS", info.name, L, w, 3)

    rows = layout(ui, [Part("agents", agents_section)], cols, 0)
    ui.keys = [("↑↓", "setting"), ("← →", "change"), *([("Enter", "choose a model")] if ui.agents_row == 0 else []),
               ("r", "undo"), ("x", "recommended")]
    if len(rows) > h:
        ui.keys.append(("PgUp PgDn", "scroll"))
    ui.keys += [("Tab", "section"), ("L", "level")]
    ui.keys_more = ["CARL saves each change at once. The server does not restart.",
                    "OpenCode and Pi use a change after their next update: the Connect tab, u (other computers: P).",
                    "The simple level leaves out the Values column: ← → shows each value."]
    return scrolled(ui, rows, h, "page_scroll")


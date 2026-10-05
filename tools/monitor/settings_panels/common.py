"""Pieces more than one Settings panel draws: the panel bar, list lines, labelled text, rows of
clickable options, the "good for" tags, the speed and fit cells of the model tables, auto fit's
passed-over models, Auto-tune's speculation table and the download status. Pure."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from carl_core.domain.autofit import Rejection
from carl_core.domain.units import duration, file_size, percent

from ..arrange import MIN_FIT, speed_of
from ..fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, buttons, ctx_label, fit, wwrap
from ..model import JSONDict, ModelInfo, jdict
from ..state import SUBPANELS, Download
from ..words import spec_name

GOOD_FOR_COLOUR = {"agent coding": GRN, "hard code": CYN, "chat & writing": B, "uncensored": RED}
SPEC_HEAD = f"  {DIM}{'speculation':<28}{'prose':>7}{'code':>7}{'edit':>7}{'speed':>8}  (tok/s){R}"


def subpanel_bar(sp: int) -> Row:
    """The Server / Models / ... switch: the selected panel in brackets and reverse video."""
    text, spans, col = "", [], 0
    for i, name in enumerate(SUBPANELS):
        lab = f"[{name}]" if i == sp else f" {name} "
        text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + "  "
        spans.append((col, col + len(lab), f"sp:{i}"))
        col += len(lab) + 2
    text += f"{DIM}   [ ] change panel{R}"
    return " " + text, [(1 + a, 1 + b, act) for a, b, act in spans]


def selectable(text: str, sel: bool, w: int, act: str) -> Ln:
    """A list line: › and reverse video when selected."""
    return Ln((f"{CYN}{B}›{R} " if sel else "  ") + (f"\x1b[7m{fit(text, w - 2)}{R}" if sel else text), act=act)


def good_for_chip(tag: str) -> str:
    """A "good for" tag, coloured by kind."""
    return f"{GOOD_FOR_COLOUR.get(tag, '')}{tag}{R}"


def label_wrap(label: str, text: str, w: int, colour: str = "") -> List[CardLine]:
    """A bold label on the first line, the text wrapped under it."""
    pad = max(len(label) + 1, 11)                         # the same text column as lv(..., 11)
    lines = wwrap(text, w - pad)
    out: List[CardLine] = [f"{B}{label:<{pad}}{R}{colour}{lines[0]}{R}"]
    return out + [f"{' ' * pad}{colour}{x}{R}" for x in lines[1:]]


def choice_line(label: str, opts: Sequence[Tuple[str, str, str]], cur: str, w: int, lw: int = 11,
                sel: Optional[bool] = None) -> List[CardLine]:
    """A label and clickable options (value, text, action), the current one in brackets and highlighted;
    options that don't fit go on the next line. sel: the row has the keys (› before the label)."""
    out: List[CardLine] = []
    if sel is None:
        head = f"{B}{label:<{lw}}{R}"
    else:
        head = (f"{CYN}{B}›{R} " if sel else "  ") + f"{B if sel else ''}{label:<{lw - 2}}{R}"
    text, col = head, lw
    spans: List[Tuple[int, int, str]] = []
    for value, shown, act in opts:
        chip = f"[{shown}]" if value == cur else f" {shown} "
        if col > lw and col + len(chip) > w:
            out.append(Ln(text, spans=spans))
            text, spans, col = " " * lw, [], lw
        text += (f"\x1b[7m{chip}{R}" if value == cur else f"{DIM}{chip}{R}") + " "
        spans.append((col, col + len(chip), act))
        col += len(chip) + 1
    return out + [Ln(fit(text, w), spans=spans)]


def speed_cell(m: ModelInfo, width: int = 15) -> str:
    """A model's speed in a table: tok/s (the mean of prose, code and edit), ● measured on this Mac,
    ○ on another Mac (the catalogue); "not measured" when never measured."""
    sp = speed_of(m)
    if not sp:
        return f"{DIM}{'not measured':>{width}}{R}"
    mark = f"{GRN}●{R}" if sp[1] else f"{DIM}○{R}"
    return f"{sp[0]:>{width - 8}.0f} tok/s {mark}"


def fit_cell(mx: Optional[int], width: int, none: str) -> str:
    """The largest context per slot that fits this Mac in a table (red below MIN_FIT; `none`: the
    weights alone don't fit; "?": unknown)."""
    if mx is None:
        return f"{DIM}{'?':>{width}}{R}"
    return f"{(GRN if mx >= 65536 else YEL if mx >= MIN_FIT else RED)}{ctx_label(mx) if mx else none:>{width}}{R}"


def passed_over(rejected: Sequence[Rejection], w: int) -> List[CardLine]:
    """Auto fit's better-ranked models that it passed over, each with its reason."""
    out: List[CardLine] = []
    for i, r in enumerate(rejected):
        lines = wwrap(r.line().rstrip(".") + ".", w - 14)
        out.append(f"{B}{'Passed over' if i == 0 else '':<13}{R} {DIM}{lines[0]}{R}")
        out += [f"{' ' * 14}{DIM}{x}{R}" for x in lines[1:]]
    return out


def parallel_text(par: object) -> str:
    """Auto-tune's parallel step: total decode speed with n requests at once, and each one's."""
    rows = [r for r in (par if isinstance(par, list) else []) if isinstance(r, list) and len(r) >= 2]
    return "write speed in total: " + " · ".join(
        f"{int(r[0])} at once {float(r[1]):.0f} tok/s ({float(r[1]) / r[0]:.0f} each)" for r in rows if r[0])


def spec_table(res: JSONDict, best: str) -> List[CardLine]:
    """Auto-tune's speculation table: each mode's speeds (tok/s) and their mean; ◀ marks the chosen one."""
    out: List[CardLine] = [SPEC_HEAD]
    for mode, r in res.items():
        rr = jdict(r)
        spec, _, n = mode.partition(":")
        chosen = mode == best
        hi = GRN + B if chosen else ""
        def num(k: str, wd: int) -> str:
            v = rr.get(k)
            return f"{v:>{wd}.1f}" if isinstance(v, (int, float)) else f"{'–':>{wd}}"
        out.append(f"  {hi}{spec_name(spec, n or None):<28}{num('prose', 7)}{num('code', 7)}{num('edit', 7)}"
                   f"{num('score', 8)}{R}" + (f"  {GRN}◀ chosen{R}" if chosen else ""))
    return out


def download_status(dl: Download) -> List[CardLine]:
    """Progress (the size so far, the speed, the time left) while it runs; the outcome after."""
    what = f"{dl.name} (model + MTP drafter)" if dl.more else dl.name
    if not dl.done:
        frac = dl.have / dl.total if dl.total else 0
        left = f" · about {duration((dl.total - dl.have) / dl.rate)} left" if dl.rate > 0 and dl.total else ""
        line = (f"{YEL}CARL checks the file of {dl.name} (SHA-256)…{R}" if any("verifying" in x for x in dl.tail) else
                f"{B}Downloading {what}{R} {bar(frac, 20)} {percent(frac)} · {file_size(dl.have).replace(' GB', '')} of "
                f"{file_size(dl.total)} · {file_size(dl.rate)}/s{left}")
        return [line, buttons("", [("Cancel the download (c)", "mcancel")])]
    if dl.proc.returncode == 0:
        return [f"{GRN}✓ {dl.name} is downloaded and checked.{R}"]
    return [f"{RED}✗ The download of {dl.name} failed: {' '.join(dl.tail)[-100:]}{R}",
            f"{DIM}Press d to try again. The part on the disk stays: the download continues from there.{R}"]

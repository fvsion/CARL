"""Pieces more than one Settings panel draws: the panel bar, list lines, labelled text, rows of
clickable options, the "good for" tags, the speed and fit cells of the model tables, auto fit's
passed-over models, Auto-tune's speculation table and the download status. Pure."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from carl_core.domain.autofit import Rejection

from ..arrange import MIN_FIT, speed_of
from ..fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, buttons, ctx_label, dur, fit, size, wwrap
from ..model import JSONDict, ModelInfo, jdict
from ..state import SUBPANELS, Download

GOOD_FOR_COLOUR = {"agent coding": GRN, "hard code": CYN, "chat & writing": B, "uncensored": RED}
SPEC_HEAD = f"  {DIM}{'speculation':<24}{'prose':>7}{'code':>7}{'re-emit':>9}{'score':>8}{R}"


def subpanel_bar(sp: int) -> Row:
    """The Server / Models / Auto-tune switch, the selected panel in reverse video."""
    text, spans, col = "", [], 0
    for i, name in enumerate(SUBPANELS):
        lab = f" {name} "
        text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + " "
        spans.append((col, col + len(lab), f"sp:{i}"))
        col += len(lab) + 1
    text += f"{DIM}  press [ or ] to change panels{R}"
    return " " + text, [(1 + a, 1 + b, act) for a, b, act in spans]


def selectable(text: str, sel: bool, w: int, act: str) -> Ln:
    """A list line: › and reverse video when selected."""
    return Ln((f"{CYN}{B}›{R} " if sel else "  ") + (f"\x1b[7m{fit(text, w - 8)}{R}" if sel else text), act=act)


def good_for_chip(tag: str) -> str:
    """A "good for" tag, coloured by kind."""
    return f"{GOOD_FOR_COLOUR.get(tag, '')}[{tag}]{R}"


def label_wrap(label: str, text: str, w: int, colour: str = "") -> List[CardLine]:
    """A bold label on the first line, the text wrapped under it."""
    pad = max(len(label) + 1, 11)                         # the same text column as lv(..., 11)
    lines = wwrap(text, w - pad)
    out: List[CardLine] = [f"{B}{label:<{pad}}{R}{colour}{lines[0]}{R}"]
    return out + [f"{' ' * pad}{colour}{x}{R}" for x in lines[1:]]


def choice_line(label: str, opts: Sequence[Tuple[str, str, str]], cur: str, w: int) -> List[CardLine]:
    """A label and clickable options (value, text, action), the current one highlighted; options
    that don't fit go on the next line."""
    out: List[CardLine] = []
    text, col = f"{B}{label:<11}{R}", 11
    spans: List[Tuple[int, int, str]] = []
    for value, shown, act in opts:
        chip = f" {shown} "
        if col > 11 and col + len(chip) > w:
            out.append(Ln(text, spans=spans))
            text, spans, col = " " * 11, [], 11
        text += (f"\x1b[7m{chip}{R}" if value == cur else f"{DIM}{chip}{R}") + " "
        spans.append((col, col + len(chip), act))
        col += len(chip) + 1
    return out + [Ln(fit(text, w), spans=spans)]


def speed_cell(m: ModelInfo) -> str:
    """A model's decode speed in a table: green when measured on this Mac, "?" when never measured."""
    sp = speed_of(m)
    return f"{GRN if sp[1] else DIM}{sp[0]:>3.0f} t/s{R}" if sp else f"{DIM}{'?':>7}{R}"


def fit_cell(mx: Optional[int], width: int, none: str) -> str:
    """The largest window that fits this Mac in a table (red below MIN_FIT; `none`: the weights alone
    don't fit; "?": unknown)."""
    if mx is None:
        return f"{DIM}{'?':>{width}}{R}"
    return f"{(GRN if mx >= 65536 else YEL if mx >= MIN_FIT else RED)}{ctx_label(mx) if mx else none:>{width}}{R}"


def passed_over(rejected: Sequence[Rejection], w: int) -> List[CardLine]:
    """Auto fit's better-ranked models that it passed over, each with its reason."""
    out: List[CardLine] = []
    for i, r in enumerate(rejected):
        lines = wwrap(r.line(), w - 14)
        out.append(f"{B}{'Passed over' if i == 0 else '':<11}{R} {DIM}· {lines[0]}{R}")
        out += [f"{' ' * 14}{DIM}{x}{R}" for x in lines[1:]]
    return out


def parallel_text(par: object) -> str:
    """Auto-tune's parallel step: total decode speed with n requests at once, and each one's."""
    rows = [r for r in (par if isinstance(par, list) else []) if isinstance(r, list) and len(r) >= 2]
    return "total decode speed: " + " · ".join(f"{int(r[0])} at once {float(r[1]):.0f} tok/s ({float(r[1]) / r[0]:.0f} each)"
                                              for r in rows if r[0])


def spec_table(res: JSONDict, best: str) -> List[CardLine]:
    """Auto-tune's speculation table: each mode's speeds and score, the chosen one (best) highlighted."""
    out: List[CardLine] = [SPEC_HEAD]
    for mode, r in res.items():
        rr = jdict(r)
        hi = GRN + B if mode == best else ""
        out.append(f"  {hi}{mode:<24}{rr.get('prose', ''):>7}{rr.get('code', ''):>7}{rr.get('edit', ''):>9}"
                   f"{rr.get('score', ''):>8}{R}")
    return out


def download_status(dl: Download) -> List[CardLine]:
    """Progress (bytes so far, speed, ETA) while it runs; the outcome after."""
    if not dl.done:
        frac = dl.have / dl.total if dl.total else 0
        eta = dur((dl.total - dl.have) / dl.rate) if dl.rate > 0 and dl.total else "–"
        line = (f"{YEL}CARL checks the SHA-256 of {dl.name}…{R}" if any("verifying" in x for x in dl.tail) else
                f"{B}downloading {dl.name}{R} {bar(frac, 24)} {frac:5.1%} {size(dl.have)} / {size(dl.total)} · "
                f"{size(dl.rate)}/s · ETA {eta}")
        return [line, buttons("", [("Cancel download (c)", "mcancel")])]
    ok = dl.proc.returncode == 0
    return [f"{GRN if ok else RED}{dl.name}: {'downloaded and verified' if ok else 'failed: ' + ' '.join(dl.tail)[-100:]}{R}"]

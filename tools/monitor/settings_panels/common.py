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
SPEC_HEAD = f"  {DIM}{'Speculation':<28}{'Prose':>7}{'Code':>7}{'Edit':>7}{'Speed':>8}  (tok/s){R}"


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


def speed_cell(m: ModelInfo, width: int = 13) -> str:
    """A model's prose speed in a table (tok/s): ● measured on this Mac, ○ on another Mac (the catalogue); – when
    never measured."""
    sp = speed_of(m)
    if not sp:
        return f"{DIM}{'–':>{width - 2}}{R}  "
    mark = f"{GRN}●{R}" if sp[1] else f"{DIM}○{R}"
    return f"{sp[0]:>{width - 2}.0f} {mark}"


def source_cells(v: Optional[JSONDict]) -> str:
    """prose, code and edit of one source (speed_sources), 6 columns each; – when not measured there."""
    return "".join(f"{float(v[k]):>6.0f}" if v and isinstance(v.get(k), (int, float)) else f"{DIM}{'–':>6}{R}"
                   for k in ("prose", "code", "edit"))


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
    return "write speed in total: " + ", ".join(
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
        sep = f"  {DIM}│{R}  "                       # one line at 100 columns: a clear separator (no " · ")
        left = f"{sep}{duration((dl.total - dl.have) / dl.rate)} left" if dl.rate > 0 and dl.total else ""
        line = (f"{YEL}CARL is checking the file of {dl.name} (SHA-256)…{R}" if any("verifying" in x for x in dl.tail) else
                f"{B}Downloading {what}{R} {bar(frac, 20)} {percent(frac)}{sep}{file_size(dl.have).replace(' GB', '')} of "
                f"{file_size(dl.total)}{sep}{file_size(dl.rate)}/s{left}")
        return [line, buttons("", [("Cancel the download (c)", "mcancel")])]
    if dl.proc.returncode == 0:
        return [f"{GRN}✓ {dl.name} is downloaded and checked.{R}"]
    return [f"{RED}✗ The download of {dl.name} failed: {' '.join(dl.tail)[-100:]}{R}",
            f"{DIM}Press d to try again. The part on the disk stays: the download continues from there.{R}"]


def long_date(iso: object) -> str:
    """'2026-10-03' as '3 Oct 2026' (as it is when it can't be read)."""
    import time
    try:
        t = time.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso or "–")
    return f"{t.tm_mday} {time.strftime('%b', t)} {t.tm_year}"


def speed_sources(m: ModelInfo) -> List[Tuple[str, Optional[JSONDict]]]:
    """A model's measured speeds by source: this Mac's Auto-tune result and the catalogue's measurement, each
    {prose, code, edit, machine, date, spec} (None: not measured there)."""
    t = jdict(jdict(m.get("local")).get("tune"))
    st = jdict(t.get("settings"))
    best = jdict(jdict(jdict(t.get("results")).get("speculation")).get(f"{st.get('spec')}:{st.get('spec_n')}"))
    here = ({"prose": best.get("prose"), "code": best.get("code"), "edit": best.get("edit"),
             "machine": t.get("machine", "this Mac"), "date": t.get("date"),
             "spec": spec_name(st.get("spec"), st.get("spec_n"))} if best.get("prose") is not None else None)
    sp = jdict(m.get("speed"))
    cat = None
    if sp.get("prose") is not None:
        mode, _, n = str(sp.get("mode") or "").partition(" n=")
        cat = {"prose": sp.get("prose"), "code": sp.get("code"), "edit": sp.get("edit"), "machine": sp.get("machine"),
               "date": sp.get("date"), "spec": spec_name(mode, n or None) if mode else "–"}
    return [("this Mac", here), ("catalogue", None if m.get("custom") else cat)]


def speeds_table(m: ModelInfo, w: int) -> List[CardLine]:
    """The speeds of a model by source, one row each (tok/s): this Mac's Auto-tune and the catalogue's, with the
    Mac, the date and the speculation they were measured with."""
    narrow = w < 84
    head = f"{DIM}{'Tok/s':<11}{'Prose':>6}{'Code':>6}{'Edit':>6}   {'Measured on':<20}{'Date':<13}" + \
        ("" if narrow else "Speculation") + R
    out: List[CardLine] = [head]
    for src, v in speed_sources(m):
        if v is None:
            what = "Not measured. Run Auto-tune (Settings > Auto-tune)." if src == "this Mac" else "Not measured."
            out.append(f"{DIM}{src[:1].upper() + src[1:]:<11}{R}{DIM}{what}{R}")
            continue
        col = GRN if src == "this Mac" else ""
        nums = "".join(f"{float(v[k]):>6.0f}" if isinstance(v.get(k), (int, float)) else f"{'–':>6}"
                       for k in ("prose", "code", "edit"))
        out.append(f"{DIM}{src[:1].upper() + src[1:]:<11}{R}{col}{nums}{R}   {str(v.get('machine') or '–'):<20}{long_date(v.get('date')):<13}"
                   + ("" if narrow else str(v.get("spec") or "")))
    return out

"""Pieces more than one Settings panel draws: the panel bar, list lines, labelled text, rows of
clickable options, the "good for" tags, the speed and fit cells of the model tables, auto fit's
passed-over models, Auto-tune's speculation table and the download status. Pure."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ....domain.catalog.quants import mtp_mark
from ....domain.memory.autofit import Rejection
from ....domain.server.snapshot import JSONDict, ModelInfo, jdict
from ....domain.shared.units import duration, file_size, percent
from ....ui.fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, buttons, ctx_label, fit, wwrap
from ....ports.plugin import GROUPS
from ....ui.words import computer, spec_name
from ...act import Verb, act as make
from ...arrange import MIN_FIT, speed_of
from ...state import SUBPANEL_GROUP, SUBPANELS, Download

GOOD_FOR_COLOUR = {"agent coding": GRN, "hard code": CYN, "chat & writing": B, "uncensored": RED}
SPEC_HEAD = f"  {DIM}{'Speculation':<28}{'Prose':>7}{'Code':>7}{'Edit':>7}{'Speed':>8}  (tok/s){R}"


def quant_line(m: ModelInfo) -> str:
    """The quantizations of a model on one line, the shown one marked: ‹UD-IQ3_XXS 12.9 GB ● MTP›   UD-Q4_K_M 21.5 GB ○
    (● downloaded, ◐ partial, ○ not; MTP: the quantization has an MTP head, drafter: it has a drafter file; A73)."""
    parts = []
    for q in m.get("quants") or []:
        dot = f"{GRN}●{R}" if q["status"] == "downloaded" else f"{YEL}◐{R}" if q["status"] == "partial" else f"{DIM}○{R}"
        text = f"{q['quant']} {file_size(q['bytes'])} {dot}" + (f" {mtp_mark(q.get('mtp', ''))}" if q.get("mtp") else "")
        parts.append(f"{B}{CYN}‹{R}{B}{text}{R}{B}{CYN}›{R}" if q["name"] == m["name"] else text)
    return "   ".join(parts)


def quant_spans(m: ModelInfo) -> List[Tuple[int, int, str]]:
    """The click targets of quant_line(m): each quantization shows that one (mquant:=NAME; W1.6 B3)."""
    out, col = [], 0
    for q in m.get("quants") or []:
        text = f"{q['quant']} {file_size(q['bytes'])} ●" + (f" {mtp_mark(q.get('mtp', ''))}" if q.get("mtp") else "")
        width = len(text) + (2 if q["name"] == m["name"] else 0)
        out.append((col, col + width, make(Verb.MQUANT, f"={q['name']}")))
        col += width + 3
    return out


def subpanel_bar(sp: int, extra: Sequence[str] = (), cols: int = 0, groups: Sequence[str] = ()) -> Row:
    """The Server / Models / ... switch: the selected panel in brackets and reverse video. extra: the titles of the
    plugins' panels, after Caching (WP); groups: the group of each (a key of GROUPS, "" for none; A73). A group's panels
    (CARL's own of the group, SUBPANEL_GROUP, then the plugins') come after a │ and the group's name ("Context
    management:"), or the │ alone where the bar would not fit with the name (and the hint, with plugin panels); a │
    ends the group. cols: the screen's width; with plugin panels, the hint "[ ] change panel" goes when the bar would
    not fit with it. With none, the hint is always there (the frame clips it)."""
    names = [*SUBPANELS, *extra]
    of = [SUBPANEL_GROUP.get(n, "") for n in SUBPANELS] + [(list(groups) + [""] * len(extra))[i]
                                                           for i in range(len(extra))]

    def build(named: bool) -> Tuple[str, List[Tuple[int, int, str]], int]:
        text, spans, col, prev = "", [], 0, ""
        for i, name in enumerate(names):
            if of[i] != prev:
                mark = "│ " + (f"{GROUPS[of[i]]}: " if of[i] and named else "")
                text += f"{DIM}{mark}{R}"
                col += len(mark)
                prev = of[i]
            lab = f"[{name}]" if i == sp else f" {name} "
            text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + "  "
            spans.append((col, col + len(lab), make(Verb.SP, i)))
            col += len(lab) + 2
        return text, spans, col
    hint = "   [ ] change panel"
    text, spans, col = build(True)
    if cols and col + (len(hint) if extra else 0) >= cols - 2:
        text, spans, col = build(False)
    if not (cols and extra) or col + len(hint) < cols - 2:
        text += f"{DIM}{hint}{R}"
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
        return [line, buttons("", [("Cancel the download (c)", make(Verb.MCANCEL))])]
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
             "machine": t.get("machine", computer()), "date": t.get("date"),
             "spec": spec_name(st.get("spec"), st.get("spec_n"))} if best.get("prose") is not None else None)
    sp = jdict(m.get("speed"))
    cat = None
    if sp.get("prose") is not None:
        mode, _, n = str(sp.get("mode") or "").partition(" n=")
        cat = {"prose": sp.get("prose"), "code": sp.get("code"), "edit": sp.get("edit"), "machine": sp.get("machine"),
               "date": sp.get("date"), "spec": spec_name(mode, n or None) if mode else "–"}
    return [(computer(), here), ("catalogue", None if m.get("custom") else cat)]


def speeds_table(m: ModelInfo, w: int) -> List[CardLine]:
    """The speeds of a model by source, one row each (tok/s): this Mac's Auto-tune and the catalogue's, with the
    Mac, the date and the speculation they were measured with."""
    narrow = w < 84
    lw = max(11, len(computer()) + 2)               # "This computer" (a PC) needs more than "This Mac" (W1.6)
    head = f"{DIM}{'Tok/s':<{lw}}{'Prose':>6}{'Code':>6}{'Edit':>6}   {'Measured on':<20}{'Date':<13}" + \
        ("" if narrow else "Speculation") + R
    out: List[CardLine] = [head]
    for src, v in speed_sources(m):
        if v is None:
            what = "Not measured. Run Auto-tune (Settings > Auto-tune)." if src == computer() else "Not measured."
            out.append(f"{DIM}{src[:1].upper() + src[1:]:<{lw}}{R}{DIM}{what}{R}")
            continue
        col = GRN if src == computer() else ""
        nums = "".join(f"{float(v[k]):>6.0f}" if isinstance(v.get(k), (int, float)) else f"{'–':>6}"
                       for k in ("prose", "code", "edit"))
        out.append(f"{DIM}{src[:1].upper() + src[1:]:<{lw}}{R}{col}{nums}{R}   {str(v.get('machine') or '–'):<20}"
                   f"{long_date(v.get('date')):<13}" + ("" if narrow else str(v.get("spec") or "")))
    return out

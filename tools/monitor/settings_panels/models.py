"""The Models panel: every model (the catalogue, the models folder, Hugging Face downloads) in the
chosen sort and filter, the selected one's details, a download in progress and the actions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..arrange import FILTERS, SORTS, label as arrange_label
from ..fmt import (B, CYN, DIM, GRN, R, YEL, CardLine, Ln, Row, Section, button_rows, ctx_label, cwrap, draw_card,
                   home_short, indent, lv, side_lines, size, with_side)
from ..model import jdict
from ..settings import SettingsService
from ..state import UIState
from .common import download_status, selectable
from .model_lines import MODEL_HEADER, ModelLines


@dataclass
class ModelsDir:
    """Where models are kept and the free disk there."""
    path: str
    free: int


def arrange_chips(sort: int, filt: int, w: int) -> List[CardLine]:
    """Every sort and filter option as a clickable chip, the current one highlighted (a wide list)."""
    out: List[CardLine] = []
    for kind, opts, cur, act in (("Sort", SORTS, sort % len(SORTS), "msortset"), ("Show", FILTERS, filt % len(FILTERS), "mfilterset")):
        text, col = f"{B}{kind:<5}{R}", 5
        spans: List[Tuple[int, int, str]] = []
        for i, o in enumerate(opts):
            chip = f" {o} "
            if col + len(chip) + 1 > w:                     # wrap onto another line
                out.append(Ln(text, spans=spans))
                text, spans, col = " " * 5, [], 5
            text += (f"\x1b[7m{chip}{R}" if i == cur else f"{DIM}{chip}{R}") + " "
            spans.append((col, col + len(chip), f"{act}:{i}"))
            col += len(chip) + 1
        out.append(Ln(text, spans=spans))
    return out


def best_tune(t: object) -> str:
    """Auto-tune's best result in one line: the speculation it picked with its speeds, the KV cache, the
    window and the slots, when and where it was measured."""
    tune = jdict(t)
    st = jdict(tune.get("settings"))
    if not st:
        return f"{DIM}not on this Mac yet (the Auto-tune panel measures it){R}"
    mode = f"{st.get('spec')}:{st.get('spec_n')}"
    r = jdict(jdict(jdict(tune.get("results")).get("speculation")).get(mode))
    speeds = (f" · {r.get('prose')} prose · {r.get('code')} code · {r.get('edit')} re-emit tok/s"
              if r.get("prose") is not None else "")
    ctx = ctx_label(st["ctx"]) if isinstance(st.get("ctx"), int) else "?"
    return (f"{GRN}{st.get('spec')} n={st.get('spec_n')}{speeds}{R} · kv {st.get('kv')} · {ctx} × {st.get('slots')} "
            f"slots {DIM}({tune.get('date', '?')}, {tune.get('machine', '?')}){R}")


class ModelsPanel:
    """Draws the Models panel."""

    def __init__(self, svc: SettingsService, lines: ModelLines, home: str) -> None:
        self.svc = svc
        self.lines = lines
        self.home = home

    def draw(self, ui: UIState, cols: int, height: int, mdir: ModelsDir) -> List[Row]:
        """Every model, the selected one's details, the download in progress and the actions."""
        ms = self.lines.visible(ui.msort, ui.mfilter)
        ui.mrow = max(0, min(ui.mrow, len(ms) - 1))
        w = cols - 2
        _, fi = arrange_label(ui.msort, ui.mfilter)
        af = self.svc.auto_fit(ui.pending or {})
        pick = af.name if af else None
        m = ms[ui.mrow] if ms else None

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = [*arrange_chips(ui.msort, ui.mfilter, mw), f"{DIM}{len(ms)} of {len(self.svc.models.get())} models{R}",
                                 MODEL_HEADER]
            if not ms:
                L.append(f"{DIM}  no model matches \"{fi}\": select a different filter above{R}")
            vis = max(height - 11 - below, 4)                # the sections under the list stay on the screen
            top = min(max(ui.mrow - vis // 2, 0), max(len(ms) - vis, 0))
            for i in range(top, min(top + vis, len(ms))):
                L.append(selectable(self.lines.model_line(ms[i], pick), i == ui.mrow, mw, f"mrow:{i}"))
            if len(ms) > vis:
                L.append(f"{DIM}  {top + 1}-{min(top + vis, len(ms))} of {len(ms)} (↑↓){R}")
            L.append("")
            if ui.dl:
                L += download_status(ui.dl)
            L += button_rows("", acts, mw)
            if ui.text:
                L += ["", *cwrap(f"{B}{ui.text.prompt}{R} {CYN}{ui.text.value}▏{R}  "
                                 f"{DIM}(Enter: find it · Esc: cancel){R}", mw)]
            if ui.hf and ui.hf.status:
                L += ["", *cwrap(f"{YEL}{ui.hf.status}{R}", mw)]
            return L

        acts = [("Use it (Enter)", "museit")]
        if m and m["status"] != "downloaded" and jdict(m.get("hf")).get("repo"):
            acts.append(("Download (d)", "mdl"))
        if m and m["status"] == "downloaded":
            acts += [("Verify (v)", "mverify"), ("Auto-tune (u)", "mtune"), ("Delete (x)", "mdelete")]
        elif m and m["status"] == "partial":
            acts.append(("Delete (x)", "mdelete"))
        if m and m.get("custom"):
            acts.append(("Edit card (e)", "medit"))
        acts.append(("Add from Hugging Face (h)", "mhf"))
        about: List[CardLine] = []
        tip = "Press h to add any GGUF from Hugging Face."
        if m:
            hf = jdict(m.get("hf"))
            about += [f"{B}{m.get('label', m['name'])}{R}  {DIM}{m['source']}{R}",
                      f"{DIM}{m.get('description') or m.get('summary', '')}{R}"]
            if hf.get("repo"):
                about.append(lv("source", f"huggingface.co/{hf['repo']} · {hf.get('file')}"
                                + (f" @ {hf['revision'][:8]}" if hf.get("revision") else ""), 8))
            about.append(lv("file", home_short(m["path"], self.home), 8))
            if m.get("custom"):
                has = bool(jdict(jdict(m.get("local")).get("card")))
                about.append(lv("card", f"{GRN}your card{R}" if has else f"{YEL}none yet{R}", 8))
            about.append(lv("tuned", best_tune(jdict(m.get("local")).get("tune")), 8))
            tip = ("Press Enter to use it in the Server panel." if m["status"] == "downloaded"
                   else "Press d to download it. You can resume the download, and CARL checks its SHA-256."
                   if hf.get("repo")
                   else "Press Enter to use it in the Server panel.")
            if m.get("custom") and not jdict(jdict(m.get("local")).get("card")):
                tip = "Press e to write the card of this model: its role, tags and rank."
        secs: List[Section] = [
            ("Selected model", about),
            ("The list", [f"speed: {GRN}measured on this Mac{R} (Auto-tune) or on a different Mac (the catalogue) · "
                          f"? = not measured · fits: the largest window per slot on this Mac"]),
            ("Models folder", [f"The list shows all .gguf files in {home_short(mdir.path, self.home)} · free disk "
                               f"{size(mdir.free)}"])]
        below = len(side_lines(tip, secs, w - 4)) + 1
        L = with_side(main, tip, secs, w - 4, beside=False)
        ui.keys = [("↑↓", "select"), ("Enter", "use"), ("d", "download"), ("v", "verify"), ("u", "auto-tune"),
                   ("x", "delete"), ("e", "edit card"), ("h", "add from HF"), ("s f", "sort / filter"), ("[ ]", "panels")]
        ui.keys_more = ["S / F: the previous sort / filter. You can also click an option.", "c cancels a download.",
                        "e edits the card of a custom model (a model that you added): what it is good for, its rank and "
                        "its thinking."]
        return indent(draw_card("models", "MODELS", f"{DIM}catalogue + models folder + Hugging Face downloads{R}", L, w, 2))

"""The Models panel: every model (the catalogue, the models folder, Hugging Face downloads) in the
chosen sort and filter as a table (download size, status, fits this Mac, speed, what it is for), a
legend, the actions, a download in progress and the selected model's section."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from carl_core.domain.units import file_size

from ..arrange import label as arrange_label
from ..cards import wrapped
from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, aligned, button_rows, cwrap, draw_card,
                   home_short, indent, row)
from ..model import ModelInfo, draft_bytes, drafter_missing, jdict
from ..settings import SettingsService, gib_pair, plan_words
from ..state import UIState
from ..words import spec_name, status_name
from .common import download_status, good_for_chip, selectable, speeds_table
from .model_card import short_date
from .model_lines import WIDE, ModelLines, model_header


@dataclass
class ModelsDir:
    """Where models are kept and the free disk there."""
    path: str
    free: int


def best_tune(t: object) -> str:
    """Auto-tune's result in one line: the speculation, the context memory, the context and the slots,
    when and where it was measured."""
    tune = jdict(t)
    st = jdict(tune.get("settings"))
    if not st:
        return ""
    ctx = st.get("ctx")
    plan = f"{int(ctx) // 1024}K × {st.get('slots')}" if isinstance(ctx, int) else f"? × {st.get('slots')}"
    return (f"{spec_name(st.get('spec'), st.get('spec_n'))}, context memory {str(st.get('kv', '')).split('_')[0]}, "
            f"{plan}   {DIM}Auto-tune, {short_date(tune.get('date'))}{R}")


class ModelsPanel:
    """Draws the Models panel."""

    def __init__(self, svc: SettingsService, lines: ModelLines, home: str) -> None:
        self.svc = svc
        self.lines = lines
        self.home = home

    def draw(self, ui: UIState, cols: int, height: int, mdir: ModelsDir, running: str = "") -> List[Row]:
        """Two sections: MODELS (the list: sort, show, the table, its legend) and the selected model (its actions,
        a download, what it is, how it runs here, its speeds by source). Each at its own level."""
        svc = self.svc
        ms = self.lines.visible(ui.msort, ui.mfilter)
        ui.mrow = max(0, min(ui.mrow, len(ms) - 1))
        w = cols - 2
        tw = w - 4
        so, fi = arrange_label(ui.msort, ui.mfilter)
        af = svc.auto_fit(ui.pending or {})
        pick = af.name if af else None
        m = ms[ui.mrow] if ms else None
        all_ms = svc.models.get()
        here = sum(1 for x in ms if x["status"] == "downloaded")
        lfull = ui.levels.get("mlist", 1) == 2
        sfull = ui.levels.get("msel", 1) == 2

        acts = [("Use it (Enter)" if m and m["status"] == "downloaded" else "Choose it (Enter)", "museit")]
        if m and (m["status"] != "downloaded" or drafter_missing(m) or m.get("draft_offer")) and \
                (jdict(m.get("hf")).get("repo") or m.get("draft") or m.get("draft_offer")):
            acts.append(("Download (d)", "mdl"))
        if m and m["status"] == "downloaded":
            acts += [("Check the file (v)", "mverify"), ("Auto-tune (u)", "mtune"), ("Delete (x)", "mdelete")]
        elif m and m["status"] == "partial":
            acts.append(("Delete (x)", "mdelete"))
        if m and m.get("custom"):
            acts.append(("Edit its card (e)", "medit"))
        acts.append(("Add from Hugging Face (h)", "mhf"))

        S: List[CardLine] = [*button_rows("", acts, tw)]
        if ui.dl:
            S += download_status(ui.dl)
        if ui.text:
            S += ["", *cwrap(f"{B}{ui.text.prompt}{R} {CYN}{ui.text.value}▏{R}  {DIM}(Enter: find it   Esc: "
                             f"cancel){R}", tw)]
        if ui.hf and ui.hf.status:
            S += ["", *cwrap(f"{YEL}{ui.hf.status}{R}", tw)]
        if m:
            S += ["", *self.selected(m, running, mdir, tw, sfull, ui.dl)]
        S += ["", *cwrap(f"{CYN}{self.tip(m)}{R}", tw)]
        sel_rows = draw_card("msel", m["name"].upper() if m else "NO MODEL", "", wrapped(aligned(S, tw), tw), w,
                             ui.levels.get("msel", 1), 3, ui.section == "msel")

        sort_len, show_len = len(f"Sort: {so} (s)"), len(f"Show: {fi} (f)")
        top: List[CardLine] = [
            Ln(f"Sort: {CYN}{so}{R} (s)    Show: {CYN}{fi}{R} (f)    {'The list' if ui.mspeeds else 'The speeds'}: t",
               spans=[(0, sort_len, "msortpick"), (sort_len + 4, sort_len + 4 + show_len, "mfilterpick")]),
            "", *model_header(lfull, tw, ui.mspeeds).split("\n")]
        legend: List[CardLine] = [
            "", (f"{CYN}★{R} Auto fit's choice   this Mac: Auto-tune   catalogue: measured on another Mac"
                 if tw >= WIDE or ui.mspeeds else
                 f"{GRN}●{R} this Mac (Auto-tune)   {DIM}○{R} another Mac (the catalogue)   {CYN}★{R} Auto fit's choice")]
        vis = max(height - 4 - len(top) - len(legend) - len(sel_rows), 4)
        rows: List[CardLine] = []
        if not ms:
            rows.append(f"{DIM}  No model matches \"{fi}\". Choose another filter (f).{R}")
        top_i = min(max(ui.mrow - vis // 2, 0), max(len(ms) - vis, 0))
        for i in range(top_i, min(top_i + vis, len(ms))):
            rows.append(selectable(self.lines.model_line(ms[i], pick, lfull, tw, ui.mspeeds), i == ui.mrow, tw,
                                   f"mrow:{i}"))
        if len(ms) > vis:
            rows.append(f"{DIM}  {top_i + 1}-{min(top_i + vis, len(ms))} of {len(ms)} (↑↓ to scroll){R}")
        lst = draw_card("mlist", "MODELS", f"{len(ms)} of {len(all_ms)}, {here} downloaded", top + rows + legend, w,
                        ui.levels.get("mlist", 1), 3, ui.section == "mlist")
        ui.sections = ["mlist", "msel"]
        ui.keys = [("↑↓", "model"), ("Enter", "use it"), ("d", "download"), ("t", "speeds"), ("u", "Auto-tune"),
                   ("h", "add"), ("s f", "sort / show"), ("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
        ui.keys_more = ["v checks the file. x deletes it. c cancels a download. S and F go back to the sort and the "
                        "filter before.",
                        "e edits the card of a custom model (a model that you added): its role, its tags, its rank "
                        "and its thinking."]
        return indent(lst + sel_rows)

    def tip(self, m: object) -> str:
        if not isinstance(m, dict):
            return "Press h to add any GGUF file from Hugging Face."
        if m.get("custom") and not jdict(jdict(m.get("local")).get("card")):
            return "Press e to write the card of this model: its role, its tags and its rank."
        if m["status"] == "downloaded" and m.get("draft") and m.get("draft_status") != "downloaded":
            return "Press d to download its MTP drafter: speculation is then faster on new text."
        if m["status"] == "downloaded":
            return "Press Enter to use it in the Server panel."
        if jdict(m.get("hf")).get("repo"):
            return "Press d to download it. You can stop and continue the download. CARL checks the file."
        return "Press Enter to choose it in the Server panel."

    def selected(self, m: ModelInfo, running: str, mdir: ModelsDir, tw: int, full: bool,
                 dl: object = None) -> List[CardLine]:
        """The selected model, one reading per row: what it is for, its status and size, how it runs on this Mac,
        its drafter, its speeds by source; full: the recommended settings, its source and file, its
        description."""
        svc = self.svc
        L: List[CardLine] = []
        good = [str(t) for t in (m.get("good_for") or [])]           # the tags say what it is for (user, 2026-10-08)
        if good:
            L.append(row("good for", ", ".join(good_for_chip(t) for t in good)))
        busy = dl is not None and not getattr(dl, "done", True) and getattr(dl, "name", "") == m["name"]
        state = f"{YEL}downloading{R}" if busy else status_name(m["status"])
        L.append(row("status", state + ("   It is running now." if running and running == m["name"] else "")))
        L.append(row("size", file_size(int(m.get("bytes", 0)) + draft_bytes(m)) + ("   with its MTP drafter"
                                                                                    if m.get("draft") else "")))
        plan = svc.plan_of(m)
        if plan is not None and not plan.error:
            need, limit = gib_pair(plan.need, plan.limit)
            L.append(row("on this Mac", f"{GRN}It fits{R}: {plan_words(plan.slots, plan.ctx)}, {need.replace(' GiB', '')} "
                         f"of {limit}." if plan.fits else f"{RED}It does not fit{R}: it needs {need} with "
                                                         f"{plan_words(plan.slots, plan.ctx)}, and the GPU has {limit}."))
        if m.get("draft"):
            st = str(m.get("draft_status", "missing"))
            L.append(row("MTP drafter", f"{file_size(draft_bytes(m))}, " + (f"{GRN}downloaded{R}" if st == "downloaded"
                         else f"{YEL}{status_name(st)}{R}. Until you download it (d), speculation uses n-gram only.")))
        elif m.get("draft_offer"):
            offer = jdict(m.get("draft_offer"))
            L.append(row("MTP drafter", f"{YEL}None yet{R}. The drafter of {m.get('draft_for')} fits it "
                         f"({file_size(int(offer.get('bytes', 0)))}): press d."))
        if m.get("custom"):
            has = bool(jdict(jdict(m.get("local")).get("card")))
            L.append(row("card", f"{GRN}your card{R}" if has else f"{YEL}None yet.{R} Press e to write it."))
        if isinstance(m.get("rank"), int):
            L.append(row("quality", f"Rank {m['rank']} (1 = best)"))
        L += ["", *speeds_table(m, tw)]
        if full:
            L.append("")
            rec = best_tune(jdict(jdict(m.get("local")).get("tune")))
            if rec:
                L.append(row("recommended", rec))
            hf = jdict(m.get("hf"))
            if hf.get("repo"):
                L.append(row("source", f"huggingface.co/{hf['repo']}   {hf.get('file')}"
                             + (f" @ {str(hf['revision'])[:8]}" if hf.get("revision") else "")))
            L.append(row("file" if m["status"] != "missing" else "saved as", home_short(m["path"], self.home)))
            if m.get("draft"):
                L.append(row("drafter file", home_short(str(m.get("draft_path", "")), self.home)))
            L.append(row("free disk", f"{file_size(mdir.free)} in {home_short(mdir.path, self.home)}"))
            desc = str(m.get("description") or "")
            if desc:
                L += ["", f"{DIM}{desc}{R}"]
        return L

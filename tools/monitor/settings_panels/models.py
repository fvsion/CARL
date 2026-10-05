"""The Models panel: every model (the catalogue, the models folder, Hugging Face downloads) in the
chosen sort and filter as a table (download size, status, fits this Mac, speed, what it is for), a
legend, the actions, a download in progress and the selected model's section."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from carl_core.domain.units import file_size, memory

from ..arrange import label as arrange_label
from ..fmt import B, CYN, DIM, GRN, R, YEL, CardLine, Ln, Row, button_rows, cwrap, draw_card, heading, home_short, indent
from ..model import ModelInfo, draft_bytes, drafter_missing, jdict
from ..settings import SettingsService, gib_pair, plan_words
from ..state import UIState
from ..words import spec_name, status_name
from .common import download_status, selectable
from .model_card import short_date, speed_line
from .model_lines import ModelLines, model_header


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
    return (f"{spec_name(st.get('spec'), st.get('spec_n'))} · {str(st.get('kv', '')).split('_')[0]} · {plan} "
            f"(Auto-tune, {short_date(tune.get('date'))}, {tune.get('machine', '?')})")


def drafter_line(m: ModelInfo, home: str, full: bool) -> str:
    """A model's separate MTP drafter (Gemma 4): downloaded or not, and what that means."""
    st = str(m.get("draft_status", "missing"))
    where = f" {DIM}{home_short(str(m.get('draft_path', '')), home)}{R}" if full else ""
    if st == "downloaded":
        return f"MTP drafter ({file_size(draft_bytes(m))}): {GRN}downloaded{R}.{where}"
    return (f"MTP drafter ({file_size(draft_bytes(m))}): {YEL}{status_name(st)}{R}. Until it is downloaded, speculation "
            f"uses n-gram only (slower on new text). Press d.{where}")


def offer_line(m: ModelInfo, full: bool) -> str:
    """A custom Gemma 4 model with no drafter yet: the catalogue drafter of its size fits it."""
    offer = jdict(m.get("draft_offer"))
    where = f" {DIM}{offer.get('repo', '')} · {offer.get('file', '')}{R}" if full else ""
    return (f"MTP drafter: {YEL}none yet{R}. This is a Gemma 4 model of the same size as {m.get('draft_for')}, so its "
            f"MTP drafter fits ({file_size(int(offer.get('bytes', 0)))}). With it, speculation is usually much faster. "
            f"Press d to download it.{where}")


class ModelsPanel:
    """Draws the Models panel."""

    def __init__(self, svc: SettingsService, lines: ModelLines, home: str) -> None:
        self.svc = svc
        self.lines = lines
        self.home = home

    def draw(self, ui: UIState, cols: int, height: int, mdir: ModelsDir, running: str = "") -> List[Row]:
        """The table, the legend, the actions, a download, the selected model."""
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
        here = sum(1 for x in all_ms if x["status"] == "downloaded")

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

        below: List[CardLine] = ["", *button_rows("", acts, tw)]
        if ui.dl:
            below += download_status(ui.dl)
        if ui.text:
            below += ["", *cwrap(f"{B}{ui.text.prompt}{R} {CYN}{ui.text.value}▏{R}  {DIM}(Enter: find it · Esc: "
                                 f"cancel){R}", tw)]
        if ui.hf and ui.hf.status:
            below += ["", *cwrap(f"{YEL}{ui.hf.status}{R}", tw)]
        if m:
            below += ["", heading(m["name"], tw), *self.selected(m, running, mdir, tw, ui.full)]
        below += ["", *cwrap(f"{CYN}Quick tip:{R} {self.tip(m)}", tw)]

        sort_len, show_len = len(f"Sort: {so} (s)"), len(f"Show: {fi} (f)")
        top: List[CardLine] = [
            Ln(f"Sort: {CYN}{so}{R} (s)    Show: {CYN}{fi}{R} (f)    {len(ms)} of {len(all_ms)} models: "
               f"{here} downloaded", spans=[(0, sort_len, "msortpick"),
                                            (sort_len + 4, sort_len + 4 + show_len, "mfilterpick")]),
            "", model_header(ui.full)]
        legend: List[CardLine] = [
            "", *cwrap(f"{GRN}●{R} speed measured on this Mac (Auto-tune)  {DIM}○{R} measured on another Mac (the "
                       f"catalogue)  not measured: never measured", tw),
            *cwrap(f"Speed = the mean of the prose, code and edit speeds (tok/s). {CYN}★{R} = Auto fit's choice for this "
                   f"Mac." + (" Max context = the largest context per slot that fits this Mac (1 slot, q4)."
                              if ui.full else ""), tw)]
        vis = max(height - 2 - len(top) - len(legend) - len(below) - 1, 4)
        rows: List[CardLine] = []
        if not ms:
            rows.append(f"{DIM}  No model matches \"{fi}\". Choose another filter (f).{R}")
        top_i = min(max(ui.mrow - vis // 2, 0), max(len(ms) - vis, 0))
        for i in range(top_i, min(top_i + vis, len(ms))):
            rows.append(selectable(self.lines.model_line(ms[i], pick, ui.full), i == ui.mrow, tw, f"mrow:{i}"))
        if len(ms) > vis:
            rows.append(f"{DIM}  {top_i + 1}-{min(top_i + vis, len(ms))} of {len(ms)} (↑↓ to scroll){R}")
        L = top + rows + legend + below
        ui.keys = [("↑↓", "model"), ("Enter", "use it"), ("d", "download"), ("v", "check"), ("u", "Auto-tune"),
                   ("x", "delete"), ("e", "card"), ("h", "add"), ("s", "sort"), ("f", "show"), ("c", "cancel download"),
                   ("[ ]", "panels")]
        ui.keys_more = ["S and F go back to the sort and the filter before. You can also click Sort or Show.",
                        "e edits the card of a custom model (a model that you added): its role, its tags, its rank "
                        "and its thinking."]
        return indent(draw_card("models", "MODELS", f"{DIM}the catalogue, the models folder and your downloads{R}",
                                L, w, 1))

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

    def selected(self, m: ModelInfo, running: str, mdir: ModelsDir, tw: int, full: bool) -> List[CardLine]:
        """The selected model in sentences: what it is for, its status and size, how it runs on this Mac,
        its drafter; full: the recommended settings, its source and file, its description."""
        svc = self.svc
        L: List[CardLine] = []
        role = str(m.get("role") or m.get("summary") or "")
        state = f"{status_name(m['status']).capitalize()}, {file_size(int(m.get('bytes', 0)) + draft_bytes(m))}."  # with its drafter, as the list
        if running and running == m["name"]:
            state += " It runs now."
        L += cwrap(f"{role}{'.' if role and not role.endswith('.') else ''} {state}".strip(), tw)
        plan = svc.plan_of(m)
        tune = jdict(jdict(m.get("local")).get("tune"))
        speed = speed_line(m, tune)
        if plan is not None and not plan.error:
            need, limit = gib_pair(plan.need, plan.limit)
            fits = (f"On this Mac: {plan_words(plan.slots, plan.ctx)} fit ({need.replace(' GiB', '')} of {limit})."
                    if plan.fits else f"On this Mac: it does not fit. It needs {need} with "
                                      f"{plan_words(plan.slots, plan.ctx)}; this Mac gives the GPU {limit}.")
            L += cwrap(f"{fits} {speed}", tw)
        else:
            L += cwrap(speed, tw)
        if m.get("draft"):
            L += cwrap(drafter_line(m, self.home, full), tw)
        elif m.get("draft_offer"):
            L += cwrap(offer_line(m, full), tw)
        if m.get("custom"):
            has = bool(jdict(jdict(m.get("local")).get("card")))
            L += cwrap(f"Card: {GRN}your card{R}." if has else f"Card: {YEL}none yet{R}. Press e to write one.", tw)
        if full:
            rec = best_tune(tune)
            if rec:
                L += cwrap(f"Recommended: {rec}.", tw)
            hf = jdict(m.get("hf"))
            if hf.get("repo"):
                L += cwrap(f"Source: huggingface.co/{hf['repo']} · {hf.get('file')}"
                           + (f" @ {str(hf['revision'])[:8]}" if hf.get("revision") else ""), tw, "  ")
            where = "File" if m["status"] != "missing" else "It will be saved as"
            L += cwrap(f"{where}: {home_short(m['path'], self.home)} · free disk {file_size(mdir.free)}", tw, "  ")
            L += cwrap(f"{DIM}The list shows the catalogue and every .gguf file in "
                       f"{home_short(mdir.path, self.home)}.{R}", tw)
            desc = str(m.get("description") or "")
            if desc:
                L += cwrap(f"{DIM}{desc}{R}", tw)
            if plan is not None and plan.fits:
                L.append(f"{DIM}Memory with this plan: {memory(plan.need)}.{R}")
        return L

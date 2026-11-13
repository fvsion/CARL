"""The Models panel: every model (the catalogue, the models folder, Hugging Face downloads) in the
chosen sort and filter as a table (download size, status, fits this Mac, speed, what it is for), a
legend, the actions, a download in progress and the selected model's section."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ....application.settings_service import SettingsService, gib_pair, plan_words
from ....domain.catalog.drafts import KIND_WORDS, MTP_FILE, VISION_WORDS
from ....domain.catalog.hf_token import SOURCE_CARL, SOURCE_ENV, SOURCE_NONE, SOURCE_SHARED, TOKEN_ENV, TokenStatus
from ....domain.catalog.quants import quant_of
from ....domain.server.snapshot import ModelInfo, draft_bytes, drafter_missing, drafts_missing, jdict, jlist
from ....domain.shared.units import file_size
from ....ui.fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, aligned, button_rows, cwrap, draw_card,
                        home_short, indent, row)
from ....ui.words import computer, other_computer, spec_name, status_name
from ...act import Verb, act as make
from ...arrange import downloaded, label as arrange_label
from ...shown import Shown
from ...state import UIState
from ..cards import wrapped
from .common import download_status, good_for_chip, quant_line, selectable, speeds_table
from .model_card import short_date
from .model_lines import WIDE, ModelLines, has_quants, model_header


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
        self._token: Optional[Tuple[int, TokenStatus]] = None         # (the revision it was asked at, the status)

    def draw(self, ui: UIState, cols: int, height: int, mdir: ModelsDir, running: str = "",
             out: Optional[Shown] = None) -> List[Row]:
        """Two sections: MODELS (the list: sort, show, the table, its legend) and the selected model (its actions,
        a download, what it is, how it runs here, its speeds by source). Each at its own level."""
        svc = self.svc
        out = Shown() if out is None else out
        ms = self.lines.visible(ui.msort, ui.mfilter, self.lines.shown(ui))
        qc = has_quants(ms)
        mrow = out.fix("mrow", max(0, min(ui.mrow, len(ms) - 1)))
        w = cols - 2
        tw = w - 4
        so, fi = arrange_label(ui.msort, ui.mfilter)
        af = svc.auto_fit(ui.pending or {})
        pick = af.name if af else None
        m = ms[mrow] if ms else None
        all_ms = self.lines.rows(svc.models.get(), {})                # a model with several quantizations counts once
        here = sum(1 for x in ms if downloaded(x))
        lfull = ui.levels.get("mlist", 1) == 2
        sfull = ui.levels.get("msel", 1) == 2

        acts = [("Use it (Enter)" if m and m["status"] == "downloaded" else "Choose it (Enter)", make(Verb.MUSEIT))]
        if m and (m["status"] != "downloaded" or drafter_missing(m) or drafts_missing(m) or m.get("draft_offer")) and \
                (jdict(m.get("hf")).get("repo") or m.get("draft") or m.get("draft_offer") or m.get("drafts")):
            acts.append(("Download (d)", make(Verb.MDL)))
        if m and m["status"] == "downloaded":
            acts += [("Check the file (v)", make(Verb.MVERIFY)), ("Auto-tune (u)", make(Verb.MTUNE)),
                     ("Delete (x)", make(Verb.MDELETE))]
        elif m and m["status"] == "partial":
            acts.append(("Delete (x)", make(Verb.MDELETE)))
        if m and m.get("custom"):
            acts.append(("Edit its card (e)", make(Verb.MEDIT)))
        if m and m.get("quants"):
            acts.append(("Other quantization (← →)", make(Verb.MQUANT, 1)))
        acts.append(("Add from Hugging Face (h)", make(Verb.MHF)))

        S: List[CardLine] = [*button_rows("", acts, tw)]
        if ui.dl:
            S += download_status(ui.dl)
        if ui.text:
            secret = ui.text.secret            # the token shows as dots: never the text
            S += ["", *cwrap(f"{B}{ui.text.prompt}{R} {CYN}{'•' * len(ui.text.value) if secret else ui.text.value}▏{R}"
                             f"  {DIM}(Enter: {'keep it' if secret else 'find it'}   Esc: cancel){R}", tw)]
        if ui.hf and ui.hf.status:
            S += ["", *cwrap(f"{YEL}{ui.hf.status}{R}", tw)]
        if m:
            S += ["", *self.selected(m, running, mdir, tw, sfull, ui.dl)]
        S += ["", *cwrap(f"{CYN}{self.tip(m)}{R}", tw)]
        sel_rows = draw_card("msel", str(m.get("group") or m["name"]).upper() if m else "NO MODEL",
                             quant_of(m) if m and m.get("quants") else "", wrapped(aligned(S, tw), tw), w,
                             ui.levels.get("msel", 1), 3, ui.section == "msel")

        sort_len, show_len = len(f"Sort: {so} (s)"), len(f"Show: {fi} (f)")
        top: List[CardLine] = [
            Ln(f"Sort: {CYN}{so}{R} (s)    Show: {CYN}{fi}{R} (f)    {'The list' if ui.mspeeds else 'The speeds'}: t",
               spans=[(0, sort_len, make(Verb.MSORTPICK)), (sort_len + 4, sort_len + 4 + show_len, make(Verb.MFILTERPICK))]),
            *self.token_rows(ui, tw),
            "", *model_header(lfull, tw, ui.mspeeds, qc and not ui.mspeeds).split("\n")]
        legend: List[CardLine] = [
            "", (f"{CYN}★{R} Auto fit's choice   {computer()}: Auto-tune   catalogue: measured on {other_computer()}"
                 if tw >= WIDE or ui.mspeeds else
                 f"{GRN}●{R} {computer()} (Auto-tune)   {DIM}○{R} {other_computer()} (the catalogue)   {CYN}★{R} Auto fit's choice")]
        vis = max(height - 4 - len(top) - len(legend) - len(sel_rows), 4)
        rows: List[CardLine] = []
        if not ms:
            rows.append(f"{DIM}  No model matches \"{fi}\". Choose another filter (f).{R}")
        top_i = min(max(mrow - vis // 2, 0), max(len(ms) - vis, 0))
        for i in range(top_i, min(top_i + vis, len(ms))):
            rows.append(selectable(self.lines.model_line(ms[i], pick, lfull, tw, ui.mspeeds, qc and not ui.mspeeds),
                                   i == mrow, tw, make(Verb.MROW, i)))
        if len(ms) > vis:
            rows.append(f"{DIM}  {top_i + 1}-{min(top_i + vis, len(ms))} of {len(ms)} (↑↓ to scroll){R}")
        lst = draw_card("mlist", "MODELS", f"{len(ms)} of {len(all_ms)}, {here} downloaded", top + rows + legend, w,
                        ui.levels.get("mlist", 1), 3, ui.section == "mlist")
        out.sections = ["mlist", "msel"]
        out.keys = [("↑↓", "model"), *([("← →", "quantization")] if qc else []), ("Enter", "use it"),
                    ("d", "download"), ("t", "speeds"), ("u", "Auto-tune"), ("h", "add"), ("s f", "sort / show"),
                    ("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
        out.keys_more = ["v checks the file. x deletes it. c cancels a download. S and F go back to the sort and the "
                        "filter before.",
                        "k keeps your Hugging Face token (it shows as dots). K removes the token that CARL keeps.",
                        "m measures the model with llama-server's own fit. The command fit shows it.",
                        "e edits the card of a custom model (a model that you added): its role, its tags, its rank "
                        "and its thinking."]
        return indent(lst + sel_rows)

    def token_rows(self, ui: UIState, tw: int) -> List[CardLine]:
        """The Hugging Face token row: set or not, its last 4 characters and where it comes from (never the token),
        and the buttons to set and to clear the one CARL keeps (decision A75). The status is asked once and kept until
        ui.hf_token_rev changes (a render never changes the UI state)."""
        if self._token is None or self._token[0] != ui.hf_token_rev:      # asked again after a set or a clear
            try:
                self._token = (ui.hf_token_rev, self.svc.store.hf_token_status())
            except Exception:                   # a store that does not answer: show it as not set
                self._token = (ui.hf_token_rev, TokenStatus(SOURCE_NONE, ""))
        t = self._token[1]
        where = {SOURCE_ENV: f", from {TOKEN_ENV}", SOURCE_SHARED: ", from the Hugging Face tools",
                 SOURCE_CARL: f", in {t.where}" if t.where else ""}.get(t.source, "")
        state = f"{GRN}set{R} {DIM}(ends {t.last4}{where}){R}" if t.is_set else f"{DIM}not set{R}"
        buttons = [("Set it (k)", make(Verb.HFTOKEN))] + ([("Clear it (K)", make(Verb.HFTOKENCLEAR))]
                                                          if t.source == SOURCE_CARL else [])
        return ["", *button_rows(f"Hugging Face token: {state}   ", buttons, tw)]

    def tip(self, m: object) -> str:
        if not isinstance(m, dict):
            return "Press h to add any GGUF file from Hugging Face."
        if m.get("quants") and m["status"] != "downloaded":
            return "Press ← → for another quantization of this model, or d to download this one."
        if m.get("custom") and not jdict(jdict(m.get("local")).get("card")):
            return "Press e to write the card of this model: its role, its tags and its rank."
        if m["status"] == "downloaded" and m.get("draft") and m.get("draft_status") != "downloaded":
            return "Press d to download its MTP drafter: speculation is then faster on new text."
        if m["status"] == "downloaded" and drafts_missing(m):
            return "Press d to download its drafters: Auto-tune then tries them."
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
        if m.get("quants"):
            L.append(row("quantization", quant_line(m)))
        L.append(row("status", state + ("   It is running now." if running and running == m["name"] else "")))
        L.append(row("size", file_size(int(m.get("bytes", 0)) + draft_bytes(m)) + ("   with its MTP drafter"
                                                                                    if m.get("draft") else "")))
        plan = svc.plan_of(m)
        if plan is not None and not plan.error:
            need, limit = gib_pair(plan.need, plan.limit)
            L.append(row(f"on {computer()}", f"{GRN}It fits{R}: {plan_words(plan.slots, plan.ctx)}, {need.replace(' GiB', '')} "
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
        for d in (jdict(x) for x in jlist(m.get("drafts"))):       # the drafters of other kinds (WCATX)
            if d.get("kind") != MTP_FILE:
                st = str(d.get("status", "missing"))
                L.append(row(str(KIND_WORDS.get(str(d.get("kind")), "drafter")),
                             f"{file_size(int(d.get('bytes', 0)))}, "
                             + (f"{GRN}downloaded{R}" if st == "downloaded" else f"{YEL}{status_name(st)}{R}. Press d to "
                                f"download it.")))
        if m.get("vision"):
            st = str(m.get("vision_status", "missing"))
            L.append(row(VISION_WORDS, f"{file_size(int(jdict(m.get('vision')).get('bytes', 0)))}, "
                         + (f"{GRN}downloaded{R}" if st == "downloaded" else f"{YEL}{status_name(st)}{R}")
                         + ". A start uses it when llama.vision is on."))
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

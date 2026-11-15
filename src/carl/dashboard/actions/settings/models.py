"""The Models panel (and the model lists of the Server panel): a model chosen, downloaded, checked, deleted, tuned or
edited; Hugging Face; the sort and the filter."""
from __future__ import annotations

from ....domain.server.snapshot import draft_bytes
from ....domain.shared.errors import ConfigError
from ....domain.shared.units import file_size
from ....ui.fmt import R, RED, home_short, row
from ...act import Action, Verb, act
from ...keys import DOWN, LEFTKEY, PGDN, PGUP, RIGHT, UP
from ...state import SP_MODELS, SP_SERVER, SP_TUNE, Confirm, TextPrompt
from .context import Context

MODEL_KEYS = {"\r": act(Verb.MUSEIT), "\n": act(Verb.MUSEIT), "d": act(Verb.MDL), "v": act(Verb.MVERIFY),
              "x": act(Verb.MDELETE), "h": act(Verb.MHF), "k": act(Verb.HFTOKEN), "K": act(Verb.HFTOKENCLEAR),
              "c": act(Verb.MCANCEL), "u": act(Verb.MTUNE),
              "e": act(Verb.MEDIT), "s": act(Verb.MSORT), "f": act(Verb.MFILTER), "S": act(Verb.MSORT_BACK),
              "F": act(Verb.MFILTER_BACK), "t": act(Verb.MSPEEDS), "m": act(Verb.MMEASURE)}


class ModelsActions:
    """Acts on the Models panel."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def act(self, a: Action) -> None:
        """The Models panel (and the Server panel's model list: smodel:NAME; the sort and filter of every model
        list)."""
        c = self.c
        ui, d, verb = c.ui, c.snapshot(), a.verb
        if verb is Verb.MROW:
            ui.mrow = int(a.arg)
            return
        if verb is Verb.MSPEEDS:                         # t: the list as the speeds table, or back
            ui.mspeeds = not ui.mspeeds
            return
        if verb is Verb.MQUANT:                          # ← →: another quantization of the selected model
            self.quant(a.arg)
            return
        if verb is Verb.SMODEL:                          # the Server panel's model list
            c.choose_model(a.arg)
            return
        if verb in (Verb.MSORTPICK, Verb.MFILTERPICK):   # a drop-down of every sort / filter
            ui.picker = c.view.arrange_picker("sort" if verb is Verb.MSORTPICK else "filter", ui.msort, ui.mfilter)
            return
        if verb in (Verb.MSORT, Verb.MSORT_BACK, Verb.MSORTSET, Verb.MFILTER, Verb.MFILTER_BACK, Verb.MFILTERSET):
            kind = "sort" if verb in (Verb.MSORT, Verb.MSORT_BACK, Verb.MSORTSET) else "filter"
            if verb in (Verb.MSORTSET, Verb.MFILTERSET):      # choose one
                c.set_arrangement(kind, int(a.arg))
            else:                                            # the next or the one before
                cur = ui.msort if kind == "sort" else ui.mfilter
                c.set_arrangement(kind, cur + (-1 if verb in (Verb.MSORT_BACK, Verb.MFILTER_BACK) else 1))
            return
        if verb is Verb.MAUTODL:                        # Auto fit's choice: download it, show it in the Models panel
            q, ui.confirm2 = ui.confirm2, None
            if q and q.model:
                c.jobs.start_download(q.model)
                ui.sp = SP_MODELS
                ui.mrow = next((i for i, x in enumerate(c.visible()) if x["name"] == q.model), ui.mrow)
            return
        if verb is Verb.MDELYES:
            q, ui.confirm2 = ui.confirm2, None
            mm = c.models.by_name(q.model) if q else None
            if mm:
                try:
                    c.store.delete(mm)
                    ui.toast(f"Deleted {mm['name']}. OpenCode and Pi still list it: press u in the Connect tab.", 8)
                except Exception as e:  # a file in use, permissions, ...: say so, keep running
                    ui.toast(f"{RED}CARL cannot delete it: {e}{R}", 10)
                c.models.get(refresh=True)
            return
        if verb is Verb.MHF:
            ui.text = TextPrompt("Hugging Face repo (OWNER/REPO, or the address of a .gguf file):", "hf")
            return
        if verb is Verb.HFTOKEN:                         # the token is typed as dots, never shown
            ui.text = TextPrompt("Hugging Face token:", "hftoken", secret=True)
            return
        if verb is Verb.HFTOKENCLEAR:
            self.clear_token()
            return
        if verb is Verb.MCANCEL:
            if ui.dl:
                c.jobs.cancel_download()
            return
        ms = c.visible()
        m = ms[min(ui.mrow, len(ms) - 1)] if ms else None
        if m is None:
            return
        if verb is Verb.MUSEIT:
            chosen = ui.pending if ui.pending is not None else c.pending_init(d)
            ui.pending = chosen
            chosen["model"] = m["name"]
            c.svc.load_profile(chosen, m["name"])
            ui.sp, ui.set_row = SP_SERVER, 0            # the model row
            ui.toast(f"{m['name']} is your choice in the Server panel. "
                     + ("Press a to start it." if m["status"] == "downloaded" else
                        "Download it first: the Models panel, d."), 6)
        elif verb is Verb.MDL:
            c.jobs.start_download(m["name"])
        elif verb is Verb.MVERIFY:
            c.jobs.verify(m["name"])
        elif verb is Verb.MMEASURE:
            c.jobs.measure_fit(m["name"])
        elif verb is Verb.MDELETE:
            if d.cmd and m["path"] in d.cmd:
                ui.toast(f"{m['name']} is running now: CARL cannot delete it. Start another model first (Settings > "
                         f"Server).", 8, error=True)
                return
            lines = [row("model", f"{file_size(m['bytes']):>8}   {home_short(m['path'], c.home)}")]
            if m.get("draft"):
                lines.append(row("MTP drafter", f"{file_size(draft_bytes(m)):>8}   "
                                                f"{home_short(str(m.get('draft_path', '')), c.home)}"))
            lines += ["", "This cannot be undone. Then update OpenCode and Pi: the Connect tab, u."]
            ui.confirm2 = Confirm("DELETE THE MODEL?", lines, act(Verb.MDELYES), m["name"], yes_label="Delete")
        elif verb is Verb.MTUNE:
            ui.tune_model, ui.sp = m["name"], SP_TUNE
        elif verb is Verb.MEDIT:
            c.card.open(m)

    def set_token(self, text: str) -> None:
        """Keep the token the user typed (a toast says where, or what went wrong); the text is not kept."""
        c = self.c
        try:
            where = c.store.set_hf_token(text)
            c.ui.toast(f"CARL keeps your Hugging Face token in {where}.", 6)
        except ConfigError as e:
            c.ui.toast(f"{RED}{e}{R}", 10)
        except Exception as e:          # the store's program failed: say so, keep running
            c.ui.toast(f"{RED}CARL cannot keep the token: {type(e).__name__}{R}", 10)
        c.ui.hf_token_rev += 1

    def clear_token(self) -> None:
        c = self.c
        try:
            done = c.store.clear_hf_token()
            c.ui.toast(f"CARL removed your Hugging Face token from {done.where}." if done.had else
                       "CARL keeps no Hugging Face token of its own.", 6)
        except ConfigError as e:
            c.ui.toast(f"{RED}{e}{R}", 10)
        except Exception as e:
            c.ui.toast(f"{RED}CARL cannot remove the token: {type(e).__name__}{R}", 10)
        c.ui.hf_token_rev += 1

    def quant(self, arg: str) -> None:
        """Another quantization of a model of the list: 1 or -1 the next or the one before of the selected row; "N,1" or
        "N,-1" of row N (a click on the ‹ › of its Quant cell selects it too); "=NAME" that entry of the selected
        model (a click on it in the Quantization line; W1.6 B3). The row stays selected where a sort moves it."""
        c, ui = self.c, self.c.ui
        ms = c.visible()
        row, _, step = arg.rpartition(",")
        if row.isdigit():
            ui.mrow = min(int(row), len(ms) - 1)
        at = ms[min(ui.mrow, len(ms) - 1)] if ms else None
        if at is None:
            return
        if arg.startswith("="):
            name = arg[1:]
            if not any(q["name"] == name for q in at.get("quants") or []):
                return
            ui.mquants[at.get("group", at["name"])] = name
        elif not c.cycle_quant(at, -1 if step == "-1" else 1):
            return
        ui.mrow = next((i for i, x in enumerate(c.visible()) if x.get("group") == at.get("group")), ui.mrow)

    def key(self, k: str) -> bool:
        """The Models panel: ↑↓ PgUp PgDn a model, Enter use it, d v x h c u e, s f (S F back)."""
        c = self.c
        ui = c.ui
        if k in (UP, DOWN, PGUP, PGDN):
            step = {UP: -1, DOWN: 1, PGUP: -10, PGDN: 10}[k]
            ui.mrow = max(min(ui.mrow + step, len(c.visible()) - 1), 0)
        elif k in (RIGHT, LEFTKEY):
            c.act(act(Verb.MQUANT, -1 if k == LEFTKEY else 1))
        elif k in MODEL_KEYS:
            c.act(MODEL_KEYS[k])
        else:
            return False
        return True

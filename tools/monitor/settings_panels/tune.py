"""The Auto-tune panel: the model to tune (or all downloaded models), the mode, the run in
progress and the last results."""
from __future__ import annotations

import re
from typing import List, Sequence

from carl_core.domain.tuning import DEPTH_TEXT, DEPTHS, as_depth

from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, button_rows, buttons, ctx_label, cwrap,
                   draw_card, fit, indent, with_side)
from ..model import JSONDict, ModelInfo, jdict
from ..settings import SettingsService
from ..state import TUNE_ALL, TuneRun, UIState
from .common import parallel_text, spec_table

_STEP = re.compile(r"STEP (\d+)/(\d+) (.*)")
STEPS: List[CardLine] = [
    "Auto-tune measures this model on this Mac and saves the best settings for it. This takes about 5-10 min. "
    "The model loads once for each mode:",
    "1 memory: the largest window with 1 and 2 slots",
    "2 speculation: none, n-gram, MTP, MTP + n-gram (n = 1, 2; with an MTP drafter n = 1-4) on prose, code and a "
    "code re-emit",
    "3 prompt read speed at 8K/32K/64K → the context zones of this Mac", "4 the result",
    "After that, each start of the model uses these settings. A value that you change in the Server panel "
    "overrides them (config.json wins)."]


def tune_progress(tn: TuneRun, w: int, server_up: bool) -> List[CardLine]:
    """The tune's step bar and its last output lines; Cancel while it runs, the result after."""
    steps = [x for x in tn.lines if x.startswith("STEP ")]
    out: List[CardLine] = []
    if steps:
        mm = _STEP.match(steps[-1])
        if mm:
            n, of = int(mm.group(1)), int(mm.group(2))
            out.append(f"{B}step {n} of {of}{R} {bar(n / of if of else 0, 24)} {mm.group(3)}")
    out += [f"{DIM}{fit(x, w - 6)}{R}" for x in tn.lines[-9:]]
    if not tn.done:
        out.append(buttons("", [("Cancel (c)", "tcancel")]))
    else:
        ok = tn.proc.returncode == 0
        out.append(f"{GRN if ok else RED}{'finished' if ok else 'failed'}{R}"
                   + (f"{DIM} · the server starts again…{R}" if tn.restart and not server_up else ""))
        out.append(buttons("", [("Run again (Enter)", "trun")]))
    return out


def set_keys(ui: UIState) -> None:
    """The panel's keys for the footer and the ? card."""
    ui.keys = [("← →", "model"), ("space", "mode"), ("Enter", "run"), ("c", "cancel a run"), ("[ ]", "panels")]
    ui.keys_more = ["Click the model name to see the downloaded models. The list also has an option for all "
                    "models.",
                    "Auto-tune must have the GPU for itself. It stops the running server and starts it again after "
                    "the run."]


def depth_line(depth: str) -> Ln:
    """The mode row: quick, default and long as clickable chips, the current one highlighted."""
    text, spans, col = "mode      ", [], 10
    for d in DEPTHS:
        chip = f" {d} "
        text += (f"\x1b[7m{chip}{R}" if d == depth else f"{DIM}{chip}{R}") + " "
        spans.append((col, col + len(chip), f"tdepth:{d}"))
        col += len(chip) + 1
    return Ln(text, spans=spans)


def model_selector(label: str) -> Ln:
    """The model row: [<] the model (click: the list) [>]."""
    sel = f"{CYN}[<]{R} {B}{label:^30}{R} {CYN}[>]{R}"
    return Ln(f"model     {sel}", spans=[(10, 13, "tprev"), (14, 44, "tpick"), (45, 48, "tnext")])


def last_result(t: JSONDict, m: ModelInfo, mw: int) -> List[CardLine]:
    """A model's last Auto-tune result on this Mac (t: its tune record), or why the catalogue values apply."""
    if not t:
        return [*cwrap(f"{DIM}Auto-tune did not measure this model on this Mac. The catalogue values apply"
                     f"{' (measured on a different Mac)' if not m.get('custom') else ' (from the GGUF header: an estimate)'}"
                     f".{R}",
                     mw)]
    st = t["settings"]
    L: List[CardLine] = [*cwrap(f"{B}Last result{R} {DIM}{t['date']} · {t.get('machine', '?')} · {t.get('llama_cpp', '')}"
              f"{' · ' + str(t['depth']) + ' mode' if t.get('depth') else ''}{R}", mw)]
    L += cwrap(f"  {GRN}kv {st['kv']} · speculation {st['spec']} n={st['spec_n']} · context "
               f"{ctx_label(st['ctx'])} per slot · {st['slots']} slot(s){R}", mw, "  ")
    L += spec_table(jdict(jdict(t.get("results")).get("speculation")), f"{st['spec']}:{st['spec_n']}")
    pr = jdict(t.get("results")).get("prompt_read") or []
    if pr:
        L += cwrap("  prompt read speed: " + " · ".join(f"{int(n) // 1024}K at {tps:.0f} tok/s" for n, tps in pr),
                   mw, "  ")
    par = jdict(t.get("results")).get("parallel") or []
    if par:
        L += cwrap(f"  {parallel_text(par)}", mw, "  ")
    dd = jdict(t.get("results")).get("decode_at_depth") or []
    if dd:
        L += cwrap("  decode speed after a read of: " + " · ".join(f"{int(n) // 1024}K {tps:.1f} tok/s"
                                                              for n, tps in dd), mw, "  ")
    z = t.get("ctx_zones")
    if z:
        L.append(f"  context zones: {GRN}≤{ctx_label(z['good'])} fast{R} · {YEL}≤{ctx_label(z['slow'])} "
                 f"slow{R} · {RED}>{ctx_label(z['slow'])} very slow{R}")
    return L + button_rows("  ", [("Use these values (clear my overrides)", "tclear")], mw)


class TunePanel:
    """Draws the Auto-tune panel."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def draw(self, ui: UIState, cols: int, server_up: bool) -> List[Row]:
        """The model to tune, the run in progress, and the last result."""
        ms = self.svc.models.downloaded()
        w = cols - 2
        if not ms:
            set_keys(ui)
            return indent(draw_card("tune", "AUTO-TUNE", "", cwrap("No model is downloaded. Use the Models panel ([) "
                                                                   "to download one.", w - 4), w, 2))
        names = [m["name"] for m in ms]
        if ui.tune_model not in names and ui.tune_model != TUNE_ALL:
            cur = self.svc.resolved_model(ui.pending) if ui.pending else names[0]
            ui.tune_model = cur if cur in names else names[0]
        if ui.tune_model == TUNE_ALL:
            return self._all(ui, ms, cols, server_up)
        m = ms[names.index(ui.tune_model)]
        tn = ui.tune
        depth = as_depth(ui.tune_depth)
        t = jdict(jdict(m.get("local")).get("tune"))
        set_keys(ui)

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = [model_selector(m["name"]), depth_line(depth), ""]
            if tn and (not tn.done or tn.model == m["name"]):
                L += tune_progress(tn, mw + 4, server_up)
            else:
                L.append(buttons("", [("Run auto-tune (Enter)", "trun")]))
            return L + [""] + last_result(t, m, mw)

        tip = ("Press c to cancel the run." if tn and not tn.done
               else "Press Enter to start the run. Press ← → to select a different model, or all models.")
        L = with_side(main, tip, [("This model", [m["summary"]] if m.get("summary") else []),
                                  (f"Mode: {depth}", [DEPTH_TEXT[depth]]), ("What it measures", STEPS)], w - 4, main_w=80)
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}per model, per Mac{R}", L, w, 2))

    @staticmethod
    def _all(ui: UIState, ms: Sequence[ModelInfo], cols: int, server_up: bool) -> List[Row]:
        """Auto-tune for every downloaded model: the list with each one's last result, the run."""
        w = cols - 2
        tn = ui.tune
        depth = as_depth(ui.tune_depth)
        set_keys(ui)

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = [model_selector(f"all models ({len(ms)})"), depth_line(depth), ""]
            if tn and (not tn.done or tn.model == TUNE_ALL):
                L += tune_progress(tn, mw + 4, server_up)
            else:
                L.append(buttons("", [("Run auto-tune for all (Enter)", "trun")]))
            L += ["", f"{B}Last results{R}"]
            for m in ms:
                t = jdict(jdict(m.get("local")).get("tune"))
                st = jdict(t.get("settings"))
                res = (f"{GRN}{t.get('date')} · kv {st.get('kv')} · {st.get('spec')} n={st.get('spec_n')} · "
                       f"{ctx_label(int(st['ctx'])) if isinstance(st.get('ctx'), int) else '?'} × {st.get('slots')}{R}"
                       if st else f"{DIM}not tuned on this Mac yet{R}")
                L.append(f"  {m['name']:<28} {res}")
            return L

        L = with_side(main, "Press c to cancel the run." if tn and not tn.done else "Press Enter to tune all downloaded models.",
                      [("All models", ["Auto-tune tunes all downloaded models, one after the other, in the selected "
                                       "mode. Each model takes about 5-10 min. If a model fails, the run continues with "
                                       "the next model. CARL saves each result as for a single run."]),
                       (f"Mode: {depth}", [DEPTH_TEXT[depth]])], w - 4, main_w=96)
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}all models, per Mac{R}", L, w, 2))

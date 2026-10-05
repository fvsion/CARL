"""The Auto-tune panel: the model to tune (or all downloaded models), the length of the run, the run
in progress and the last results."""
from __future__ import annotations

import re
from typing import Dict, List, Sequence

from carl_core.domain.tuning import DEPTHS, as_depth
from carl_core.domain.units import speed

from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, button_rows, buttons, ctx_label, cwrap,
                   draw_card, fit, indent, with_side)
from ..model import JSONDict, ModelInfo, jdict
from ..settings import SettingsService
from ..state import TUNE_ALL, TuneRun, UIState
from ..words import DEPTH_NAMES, kv_name, plural, spec_name
from .common import parallel_text, spec_table
from .model_card import short_date

_STEP = re.compile(r"STEP (\d+)/(\d+) (.*)")
INTRO = "Auto-tune measures a model on this Mac and saves its best settings. Each start of the model then uses them."
DEPTH_HELP: Dict[str, str] = {
    "quick": "Quick: the MTP modes with 1 guess (1 and 2 with an MTP drafter), reads of about 8,000 and 32,000 tokens. "
             "About 4 min.",
    "default": "Normal: every speculation mode, reads of about 8,000, 32,000 and 64,000 tokens. About 5-10 min.",
    "long": "Long: the normal steps, then reads of about 128,000 and 192,000 tokens and the write speed after each "
            "read. 10-40 min more."}
STEPS: List[CardLine] = [
    "Auto-tune measures in steps (the run shows each step):",
    "· the memory: the largest context with 1 and 2 slots",
    "· speculation: none, n-gram, MTP, MTP + n-gram, with 1 or more guesses, on prose, new code and an edit",
    "· the read speed of long prompts: the context zones of this Mac",
    "· more slots at the same time",
    "· the result: the best settings, saved for this Mac",
    "The model loads again for each speculation mode. A value that you change in the Server panel wins over the "
    "result (config.json)."]


def tune_progress(tn: TuneRun, w: int, server_up: bool) -> List[CardLine]:
    """The run's step bar and its last output lines; Cancel while it runs, the outcome after."""
    steps = [x for x in tn.lines if x.startswith("STEP ")]
    who = "all downloaded models" if tn.model == TUNE_ALL else tn.model
    out: List[CardLine] = []
    if steps:
        mm = _STEP.match(steps[-1])
        if mm:
            n, of = int(mm.group(1)), int(mm.group(2))
            out.append(f"{B}Auto-tune runs for {who}: step {n} of {of}{R} {bar(n / of if of else 0, 20)} {mm.group(3)}")
    elif not tn.done:
        out.append(f"{B}Auto-tune runs for {who}…{R}")
    out += [f"{DIM}{fit(x, w - 6)}{R}" for x in tn.lines[-8:]]
    if not tn.done:
        out.append(buttons("", [("Cancel (c)", "tcancel")]))
    else:
        ok = tn.proc.returncode == 0
        out.append(f"{GRN}✓ Auto-tune is done. The next start of the model uses the new settings.{R}" if ok else
                   f"{RED}✗ Auto-tune stopped with an error. Its last lines are above.{R}")
        if tn.restart and not server_up:
            out.append(f"{DIM}The server starts again…{R}")
        out.append(buttons("", [("Run again (Enter)", "trun")]))
    return out


def set_keys(ui: UIState) -> None:
    """The panel's keys for the footer and the ? card."""
    ui.keys = [("← →", "model"), ("space", "length"), ("Enter", "run"), ("c", "cancel the run"),
               ("x", "recommended settings"), ("[ ]", "panels")]
    ui.keys_more = ["Click the model name to see the downloaded models. The list also has all models, one after the "
                    "other.",
                    "Auto-tune needs the GPU for itself. It stops the server and starts it again after the run."]


def depth_line(depth: str) -> Ln:
    """The length row: quick, normal and long as clickable choices, the current one in brackets."""
    text, spans, col = "Length    ", [], 10
    for d in DEPTHS:
        name = DEPTH_NAMES.get(d, d)
        chip = f"[{name}]" if d == depth else f" {name} "
        text += (f"\x1b[7m{chip}{R}" if d == depth else f"{DIM}{chip}{R}") + " "
        spans.append((col, col + len(chip), f"tdepth:{d}"))
        col += len(chip) + 1
    return Ln(text, spans=spans)


def model_selector(label: str) -> Ln:
    """The model row: ‹ the model (click: the list) ›."""
    sel = f"{CYN}‹{R} {B}{label:^30}{R} {CYN}›{R}"
    return Ln(f"Model     {sel}", spans=[(10, 11, "tprev"), (12, 42, "tpick"), (43, 44, "tnext")])


def last_result(t: JSONDict, m: ModelInfo, mw: int, full: bool) -> List[CardLine]:
    """A model's last Auto-tune result on this Mac (t: its record), or why the catalogue values apply."""
    if not t:
        src = "the catalogue (measured on another Mac)" if not m.get("custom") else "an estimate from the model file"
        return [*cwrap(f"{DIM}Auto-tune did not measure this model on this Mac. Its recommended settings come from "
                       f"{src}.{R}", mw)]
    st = t["settings"]
    build = str(t.get("llama_cpp", "")).replace("version: ", "llama.cpp ")
    L: List[CardLine] = [*cwrap(f"{B}Last result{R} {DIM}{short_date(t['date'])} · {t.get('machine', '?')}"
                                + (f" · {build}" if full and build else "")
                                + (f" · {DEPTH_NAMES.get(str(t['depth']), t['depth'])} length" if t.get('depth') else "")
                                + R, mw)]
    L += cwrap(f"  {GRN}{spec_name(st['spec'], st['spec_n'])} · {kv_name(st['kv'], short=True)} context memory · "
               f"{ctx_label(st['ctx'])} tokens per slot · {plural(int(str(st['slots'])) if str(st['slots']).isdigit() else 1, 'slot')}"
               f"{R}", mw, "  ")
    L += spec_table(jdict(jdict(t.get("results")).get("speculation")), f"{st['spec']}:{st['spec_n']}")
    pr = jdict(t.get("results")).get("prompt_read") or []
    if pr:
        L += cwrap("  Read speed: " + " · ".join(f"{int(n):,} tokens at {speed(tps)}" for n, tps in pr), mw, "  ")
    par = jdict(t.get("results")).get("parallel") or []
    if par:
        L += cwrap(f"  {parallel_text(par)}", mw, "  ")
    dd = jdict(t.get("results")).get("decode_at_depth") or []
    if dd:
        L += cwrap("  Write speed after a read of: " + " · ".join(f"{int(n):,} tokens {speed(tps)}" for n, tps in dd),
                   mw, "  ")
    z = t.get("ctx_zones")
    if z:
        L.append(f"  Context zones: {GRN}up to {ctx_label(z['good'])} fast{R} · {YEL}up to {ctx_label(z['slow'])} "
                 f"slow{R} · {RED}more is very slow{R}")
    return L + button_rows("  ", [("Use the recommended settings (x)", "tclear")], mw) + cwrap(
        f"{DIM}  x removes your changes for this model in config.json: Auto-tune's result applies again.{R}", mw)


class TunePanel:
    """Draws the Auto-tune panel."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def draw(self, ui: UIState, cols: int, server_up: bool) -> List[Row]:
        """The model to tune, the run in progress, and the last result."""
        ms = self.svc.models.downloaded()
        w = cols - 2
        set_keys(ui)
        if not ms:
            return indent(draw_card("tune", "AUTO-TUNE", "", cwrap("No model is downloaded. Use the Models panel ([) to "
                                                                   "download one.", w - 4), w, 1))
        names = [m["name"] for m in ms]
        tn = ui.tune
        if tn and not tn.done:
            ui.tune_model = tn.model                   # the run's model: the selector follows it
        if ui.tune_model not in names and ui.tune_model != TUNE_ALL:
            cur = self.svc.resolved_model(ui.pending) if ui.pending else names[0]
            ui.tune_model = cur if cur in names else names[0]
        if ui.tune_model == TUNE_ALL:
            return self._all(ui, ms, cols, server_up)
        m = ms[names.index(ui.tune_model)]
        depth = as_depth(ui.tune_depth)
        t = jdict(jdict(m.get("local")).get("tune"))

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = [*cwrap(f"{DIM}{INTRO}{R}", mw), "", model_selector(m["name"]), depth_line(depth), ""]
            if tn and (not tn.done or tn.model == m["name"]):
                L += tune_progress(tn, mw + 4, server_up)
            else:
                L.append(buttons("", [("Run Auto-tune (Enter)", "trun")]))
            return L + [""] + last_result(t, m, mw, ui.full)

        tip = ("Press c to cancel the run." if tn and not tn.done
               else "Press Enter to start the run. Press ← → for another model, or all models.")
        L = with_side(main, tip, [("This model", [str(m.get("role") or m.get("summary") or "")]),
                                  (f"Length: {DEPTH_NAMES.get(depth, depth)}", [DEPTH_HELP[depth]]),
                                  ("What it measures", STEPS)], w - 4, main_w=86)
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}{m['name']}{R}", L, w, 1))

    @staticmethod
    def _all(ui: UIState, ms: Sequence[ModelInfo], cols: int, server_up: bool) -> List[Row]:
        """Auto-tune for every downloaded model: the list with each one's last result, the run."""
        w = cols - 2
        tn = ui.tune
        depth = as_depth(ui.tune_depth)

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = [*cwrap(f"{DIM}{INTRO}{R}", mw), "", model_selector(f"all models ({len(ms)})"),
                                 depth_line(depth), ""]
            if tn and (not tn.done or tn.model == TUNE_ALL):
                L += tune_progress(tn, mw + 4, server_up)
            else:
                L.append(buttons("", [("Run Auto-tune for all (Enter)", "trun")]))
            L += ["", f"{B}Last results{R}"]
            for m in ms:
                t = jdict(jdict(m.get("local")).get("tune"))
                st = jdict(t.get("settings"))
                res = (f"{GRN}{short_date(t.get('date'))} · {spec_name(st.get('spec'), st.get('spec_n'))} · "
                       f"{kv_name(st.get('kv'), short=True)} · "
                       f"{ctx_label(int(st['ctx'])) if isinstance(st.get('ctx'), int) else '?'} × {st.get('slots')}{R}"
                       if st else f"{DIM}not measured on this Mac yet{R}")
                L.append(f"  {m['name']:<28} {res}")
            return L

        L = with_side(main, "Press c to cancel the run." if tn and not tn.done else "Press Enter to tune all downloaded "
                                                                                     "models.",
                      [("All models", ["Auto-tune measures every downloaded model, one after the other, with the length "
                                       "you chose. Each model takes about 5-10 min. If one fails, the run continues "
                                       "with the next one. CARL saves each result as for a single run."]),
                       (f"Length: {DEPTH_NAMES.get(depth, depth)}", [DEPTH_HELP[depth]])], w - 4, main_w=96)
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}all models{R}", L, w, 1))

"""The Auto-tune panel: the model to tune (or all downloaded models), the length of the run, the run
in progress and the last result on this Mac; beside them (or under them) the Quick tip, this model,
the length and what Auto-tune measures. Each part is a section with its own level; the page scrolls."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from carl_core.domain.tuning import DEPTHS, as_depth

from ..cards import sections as register
from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, button_rows, buttons, ctx_label, row,
                   vlen)
from ..model import JSONDict, ModelInfo, jdict
from ..settings import SettingsService
from ..state import TUNE_ALL, TuneRun, UIState
from ..words import DEPTH_NAMES, kv_name, plural, spec_name
from .page import Part, body_height, help_part, layout, scrolled, section, side_width
from .common import long_date, spec_table

register(three=["tune", "tunesteps"], two=["tunerun", "tuneall", "tunetip", "tunemodel", "tunelen"])

_STEP = re.compile(r"STEP (\d+)/(\d+) (.*)")
_MODE = re.compile(r"(draft-mtp,ngram-mod|draft-mtp|ngram-mod|none) n=(\d+)")
INTRO = "Auto-tune measures a model on this Mac and saves its best settings. Each start of the model then uses them."
LW = 16                                                  # the label column of the AUTO-TUNE section
DEPTH_HELP: Dict[str, str] = {
    "quick": "Quick tries the MTP modes with 1 guess (1 and 2 with an MTP drafter). It reads prompts of about 8,000 and "
             "32,000 tokens. It takes about 4 min.",
    "default": "Normal tries every speculation mode. It reads prompts of about 8,000, 32,000 and 64,000 tokens. It "
               "takes about 5-10 min.",
    "long": "Long does the normal steps. Then it reads prompts of about 128,000 and 192,000 tokens, and it measures the "
            "write speed after each read. It takes 10-40 min more."}
STEPS: List[CardLine] = [
    "Auto-tune measures in steps. The run shows each step.",
    "• Memory: it finds the largest context with 1 and 2 slots.",
    "• Speculation: it tries none, n-gram, MTP and MTP + n-gram, with 1 or more guesses, on prose, new code and an "
    "edit.",
    "• Long prompts: it measures their read speed. This gives the context zones of this Mac.",
    "• Slots: it measures more slots at the same time.",
    "• Result: it saves the best settings for this Mac.",
    "The model loads again for each speculation mode. A value that you change in the Server panel wins over the "
    "result."]
GUESSES: List[CardLine] = [
    "", f"{B}Guesses per mode{R}",
    row("n-gram", "2 (1 too for a model with no MTP)"),
    row("MTP", "1 and 2 (quick: 1)"),
    row("MTP + n-gram", "1 and 2 (quick: 1)"),
    row("MTP drafter", "1 to 4 (quick: 1 and 2), for Gemma 4")]
STEP_WORDS = {"memory": "Memory", "prompt reading": "Read speed", "result": "Result", "parallel": "More slots",
              "context zones": "Context zones"}


def run_words(line: str) -> str:
    """A line of the run's output in the panel's words (n-gram, 2 guesses; edit; speed), its readings apart by
    three spaces; a step line as "Step 2 of 7", then what it measures."""
    if line.startswith("DONE "):
        return ""
    mm = _STEP.match(line)
    if mm:
        return f"Step {mm.group(1)} of {mm.group(2)}   {step_words(mm.group(3))}"
    return readings(line)


def step_words(what: str) -> str:
    """What a step measures, in the panel's words, with a capital: "No speculation: prose 26.1   code 26.3 ...",
    "Context zones: fast up to 96K ..."."""
    sm = re.match(r"speculation (.*?)(:|$)(.*)", what)
    if sm:
        mode = _MODE.sub(lambda x: spec_name(x.group(1), x.group(2)), sm.group(1))
        mode = "No speculation" if mode in ("none", "None") else mode
        what = f"{mode}{sm.group(2)}{sm.group(3)}"
    else:
        head, sep, rest = what.partition(":")
        what = STEP_WORDS.get(head, head) + sep + rest
    s = readings(what)
    return s[:1].upper() + s[1:]


def readings(line: str) -> str:
    """The run's readings in the panel's words: edit (not re-emit), speed (not score), three spaces between them."""
    s = _MODE.sub(lambda x: spec_name(x.group(1), x.group(2)), line)
    s = re.sub(r"re-emit ([\d.]+)( tok/s)?", r"edit \1", s)
    s = re.sub(r"score (\d+\.\d+)", lambda x: f"speed {float(x.group(1)):.1f}" + (" tok/s" if "prose" in s else ""), s)
    s = re.sub(r"(\d+) tokens read cold", lambda x: f"{int(x.group(1)):,} tokens read", s)
    s = re.sub(r"loaded in (\d+)s", r"loaded in \1 s", s)
    s = re.sub(r"(\d) (?=(?:code|edit|speed) \d)", r"\1   ", s)
    s = re.sub(r"(\d+) at once (?=\d)", r"\1 requests at once, ", s)
    s = (s.replace("largest window per slot", "largest context per slot")
         .replace("context zones (cold read of a full window)", "context zones").replace(" vs ", ", ")
         .replace("kv q4_0", "context memory q4").replace("kv q8_0", "context memory q8").replace("slot(s)", "slots"))
    return s.replace(" · ", "   ")


def run_section(ui: UIState, tn: TuneRun, w: int, server_up: bool) -> List[Row]:
    """RUN: the model, the step and its bar, the last lines of the run in the panel's words; Cancel while it
    runs; the outcome after."""
    steps = [x for x in tn.lines if x.startswith("STEP ")]
    who = "all downloaded models" if tn.model == TUNE_ALL else tn.model
    L: List[CardLine] = [row("model", who)]
    summary = "starting"
    mm = _STEP.match(steps[-1]) if steps else None
    if mm:
        n, of = int(mm.group(1)), int(mm.group(2))
        summary = f"Step {n} of {of}"
        L += [row("step", f"{n} of {of}   {bar(n / of if of else 0, 20)}"), row("now", step_words(mm.group(3)))]
    tail = [x for x in (run_words(y) for y in tn.lines[-8:]) if x]
    L += ["", *[f"{DIM}{x}{R}" for x in tail], ""]
    if not tn.done:
        L.append(buttons("", [("Cancel (c)", "tcancel")]))
    else:
        ok = tn.proc.returncode == 0
        summary = f"{GRN}✓ done{R}" if ok else f"{RED}✗ stopped{R}"
        L.append(f"{GRN}✓ Auto-tune is done. The next start of the model uses the new settings.{R}" if ok else
                 f"{RED}✗ Auto-tune stopped with an error. Its last lines are above.{R}")
        if tn.restart and not server_up:
            L.append(f"{DIM}The server is starting again.{R}")
        L += ["", buttons("", [("Run again (Enter)", "trun")])]
    return section(ui, "tunerun", "RUN", summary, L, w, 2)


def set_keys(ui: UIState, running: bool, scrolls: bool) -> None:
    """The panel's keys for the footer and the ? card."""
    ui.keys = [("← →", "model"), ("space", "length"), ("c" if running else "Enter", "cancel" if running else "run"),
               ("x", "recommended")]
    if scrolls:
        ui.keys.append(("PgUp PgDn", "scroll"))
    ui.keys += [("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
    ui.keys_more = ["Click the model name to see the downloaded models. The list also has all models, one after the "
                    "other.",
                    "Auto-tune needs the GPU for itself. It stops the server and starts it again after the run.",
                    "The x key removes your changes for this model in config.json. Then Auto-tune's result applies again."]


def choices(label: str, depth: str) -> List[CardLine]:
    """The model row (‹ the model ›, click: the list) and the length row (quick, normal, long)."""
    sel = f"{CYN}‹{R} {B}{label}{R} {CYN}›{R}"
    c = LW
    model = Ln(f"{B}{'Model':<{LW}}{R}{sel}", spans=[(c, c + 1, "tprev"), (c + 2, c + 2 + vlen(label), "tpick"),
                                                      (c + 3 + vlen(label), c + 4 + vlen(label), "tnext")])
    text, spans, col = f"{B}{'Length':<{LW}}{R}", [], LW
    for d in DEPTHS:
        name = DEPTH_NAMES.get(d, d)
        chip = f"[{name}]" if d == depth else f" {name} "
        text += (f"\x1b[7m{chip}{R}" if d == depth else f"{DIM}{chip}{R}") + " "
        spans.append((col, col + len(chip), f"tdepth:{d}"))
        col += len(chip) + 1
    return [model, Ln(text, spans=spans)]


def num_table(head: str, rows: Sequence[Sequence[object]], fmt: str = "{:>7.0f}") -> List[CardLine]:
    """A small table under the speculation table's first column: a dim header, then one row each."""
    out: List[CardLine] = [f"  {DIM}{head}{R}"]
    for r in rows:
        out.append(f"  {str(r[0]):<28}" + "".join(fmt.format(float(str(x))) for x in r[1:]))
    return out


def last_result(t: JSONDict, m: ModelInfo, full: bool) -> List[CardLine]:
    """A model's last Auto-tune result on this Mac (t: its record) as label rows and tables; or where its
    recommended settings come from."""
    if not t:
        src = "the catalogue" if not m.get("custom") else "an estimate from the model file"
        return [row("last result", f"{DIM}Auto-tune has not measured this model on this Mac.{R}"),
                row("settings from", src)]
    st = t["settings"]
    res = jdict(t.get("results"))
    slots = int(str(st["slots"])) if str(st["slots"]).isdigit() else 1
    L: List[CardLine] = [f"{B}Last result{R}", row("date", long_date(t.get("date"))),
                         row("Mac", str(t.get("machine", "?")))]
    if t.get("depth"):
        L.append(row("length", DEPTH_NAMES.get(str(t["depth"]), str(t["depth"]))))
    build = str(t.get("llama_cpp", "")).replace("version: ", "")
    if full and build:
        L.append(row("build", f"llama.cpp {build}"))
    L += [row("speculation", spec_name(st["spec"], st["spec_n"])),
          row("context memory", kv_name(st["kv"], short=True)),
          row("context", f"{ctx_label(st['ctx'])} tokens per slot"),
          row("slots", str(slots)), ""]
    L += spec_table(jdict(res.get("speculation")), f"{st['spec']}:{st['spec_n']}")
    pr = res.get("prompt_read") or []
    if pr:
        L += ["", *num_table(f"{'Read speed of':<28}{'Tok/s':>7}", [(f"{int(n):,} tokens", tps) for n, tps in pr])]
    dd = res.get("decode_at_depth") or []
    if dd:
        L += ["", *num_table(f"{'Write speed after a read of':<28}{'Tok/s':>7}",
                             [(f"{int(n):,} tokens", tps) for n, tps in dd])]
    par = [r for r in (res.get("parallel") or []) if isinstance(r, list) and len(r) >= 2 and r[0]]
    if par:
        L += ["", *num_table(f"{'Requests at once':<28}{'Total':>7}{'Each':>7}  (tok/s)",
                             [(int(r[0]), r[1], float(r[1]) / r[0]) for r in par])]
    z = t.get("ctx_zones")
    if z:
        L += ["", f"{B}Context zones{R}", row("fast", f"{GRN}up to {ctx_label(z['good'])}{R}"),
              row("slow", f"{YEL}up to {ctx_label(z['slow'])}{R}"),
              row("very slow", f"{RED}more than {ctx_label(z['slow'])}{R}")]
    return L


class TunePanel:
    """Draws the Auto-tune panel."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def draw(self, ui: UIState, cols: int, server_up: bool, height: int = 0) -> List[Row]:
        """The sections: RUN (while a run runs, or its outcome), AUTO-TUNE (the model, the length, Run, the last
        result) or ALL MODELS; the Quick tip and the help beside them or under them. The page scrolls."""
        ms = self.svc.models.downloaded()
        tn = ui.tune
        running = bool(tn and not tn.done)
        h = body_height(height)
        if not ms:
            set_keys(ui, False, False)
            ui.keys = [("[ ]", "panels")]
            return layout(ui, [Part("tune", lambda w: section(ui, "tune", "AUTO-TUNE", "", [
                "No model is downloaded. Use the Models panel ([) to download one."], w, 2))], cols, 0)
        names = [m["name"] for m in ms]
        if tn and not tn.done:
            ui.tune_model = tn.model                   # the run's model: the selector follows it
        if ui.tune_model not in names and ui.tune_model != TUNE_ALL:
            cur = self.svc.resolved_model(ui.pending) if ui.pending else names[0]
            ui.tune_model = cur if cur in names else names[0]
        depth = as_depth(ui.tune_depth)
        everyone = ui.tune_model == TUNE_ALL
        m: Optional[ModelInfo] = None if everyone else ms[names.index(str(ui.tune_model))]
        parts: List[Part] = []
        if tn and (running or tn.model == ui.tune_model):
            parts.append(Part("tunerun", lambda w: run_section(ui, tn, w, server_up)))
        if m is None:
            parts.append(Part("tuneall", lambda w: self.all_section(ui, ms, depth, running, w)))
        else:
            parts.append(Part("tune", lambda w: self.model_section(ui, m, depth, running, w)))
        tip = ("Press c to cancel the run." if running else "Press Enter to tune all downloaded models." if m is None
               else "Press Enter to start the run. Press ← → for another model, or all models.")
        parts.append(help_part(ui, "tunetip", "QUICK TIP", [f"{CYN}{tip}{R}"]))
        if m is None:
            parts.append(help_part(ui, "tunemodel", "ONE AFTER THE OTHER", [
                "Auto-tune measures every downloaded model, one after the other, with the length that you chose. Each "
                "model takes about 5-10 min. If one fails, the run continues with the next one. CARL saves each "
                "result as for a single run."]))
        else:
            about = str(m.get("summary") or m.get("role") or "")
            if about:
                parts.append(help_part(ui, "tunemodel", "THIS MODEL", [about]))
        parts.append(help_part(ui, "tunelen", "LENGTH", [DEPTH_HELP[depth]], DEPTH_NAMES.get(depth, depth)))
        full = ui.levels.get("tunesteps", 1) == 2
        parts.append(help_part(ui, "tunesteps", "WHAT IT MEASURES", [*STEPS, *(GUESSES if full else [])], marks=3))
        side = side_width(cols)
        if running and cols < 190:                     # the run's lines need the width
            side = 0
        rows = layout(ui, parts, cols, side, balance=True)
        set_keys(ui, running, len(rows) > h)
        return scrolled(ui, rows, h, "page_scroll")

    def model_section(self, ui: UIState, m: ModelInfo, depth: str, running: bool, w: int) -> List[Row]:
        """AUTO-TUNE: the model and the length, Run, the last result on this Mac; full adds the llama.cpp
        build."""
        full = ui.levels.get("tune", 1) == 2
        t = jdict(jdict(m.get("local")).get("tune"))
        L: List[CardLine] = [f"{DIM}{INTRO}{R}", "", *choices(m["name"], depth), ""]
        if not running:
            L += [buttons("", [("Run Auto-tune (Enter)", "trun")]), ""]
        L += last_result(t, m, full)
        if t:
            L += ["", *button_rows("", [("Use the recommended settings (x)", "tclear")], w - 4)]
        return section(ui, "tune", "AUTO-TUNE", m["name"], L, w, 3)

    @staticmethod
    def all_section(ui: UIState, ms: Sequence[ModelInfo], depth: str, running: bool, w: int) -> List[Row]:
        """ALL MODELS: every downloaded model with its last result on this Mac, as a table; Run for all."""
        nw = max(len(str(m["name"])) for m in ms) + 2
        L: List[CardLine] = [f"{DIM}{INTRO}{R}", "", *choices(f"all models ({len(ms)})", depth), ""]
        if not running:
            L += [buttons("", [("Run Auto-tune for all (Enter)", "trun")]), ""]
        L.append(f"  {DIM}{'Model':<{nw}}{'Tuned':<13}{'Speculation':<25}{'Context memory':<16}Slots × context{R}")
        tuned = 0
        for m in ms:
            t = jdict(jdict(m.get("local")).get("tune"))
            st = jdict(t.get("settings"))
            if not st:
                L.append(f"  {m['name']:<{nw}}{DIM}not tuned on this Mac{R}")
                continue
            tuned += 1
            ctx = ctx_label(int(st["ctx"])) if isinstance(st.get("ctx"), int) else "?"
            L.append(f"  {m['name']:<{nw}}{long_date(t.get('date')):<13}"
                     f"{spec_name(st.get('spec'), st.get('spec_n')):<25}{kv_name(st.get('kv'), short=True):<16}"
                     f"{st.get('slots')} × {ctx}")
        return section(ui, "tuneall", "ALL MODELS", f"{tuned} of {plural(len(ms), 'model')} tuned", L, w, 2)

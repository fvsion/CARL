"""The Server panel: the state and the next action, then each setting in three columns (your choice,
running now, recommended with its source), Apply, the Memory section (does it fit), what Auto fit
suggests; beside it the Quick tip, About the selected setting and the model list; below it the
MODEL card. Full detail adds the More settings rows, the flags and config keys, the memory parts and
where the settings come from."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple, cast

from ....application.settings_service import (ADV_WARN, FULL_ONLY, MODEL_ROW_KEYS, NOT_RUNNING, NUMERIC,
                                              FitInfo, Pending, SettingRow, SettingsService, fmt_val,
                                              gib_pair, plan_words, ram_resolved, row_instruction, spec_value,
                                              stepped, typed_at)
from ....domain.memory.budget import CardParts, card_parts
from ....domain.memory.fit import RESERVE_GB, Spec
from ....domain.memory.split import Slot
from ....domain.shared.units import GIB
from ....domain.catalog.quants import row_of
from ....domain.config.server_rows import ladder
from ....domain.server.snapshot import JSONDict, ModelInfo, ServerData, jdict
from ....domain.shared.units import MIB, file_size, memory
from ....domain.shared.units import tokens
from ....ui.fmt import (reveal, B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, aligned, bar, button_rows, buttons,
                        cwrap, draw_card, fit, home_short, indent, row, side_by_side, vlen)
from ....ui.words import command, computer, net_name, os_and_apps, plural, spec_name
from ...act import Verb, act as make
from ...arrange import label as arrange_label
from ...settings_texts import SET_HELP, fit_sentence, range_ends, range_words, shown_value
from ...shown import Shown
from ...state import SP_FIT, UIState
from ..cards import wrapped
from .common import quant_line
from .model_card import ModelCard
from .model_lines import ModelLines

RAM_RESERVE = RESERVE_GB * GIB                          # the part of a Mac's RAM Auto fit keeps for macOS and apps


def gib_short(n: float) -> str:
    """A size in GiB with one decimal, two when one would lose it (0.25): 8.0 GiB."""
    t = n / GIB
    return f"{t:.2f} GiB" if abs(t * 10 - round(t * 10)) > 0.05 and t < 1 else f"{t:.1f} GiB"


def sum_parts(cards: List[CardParts]) -> CardParts:
    """The parts of several cards added up (one card: itself)."""
    if len(cards) == 1:
        return cards[0]
    progs = [c.programs for c in cards]
    return CardParts(sum(c.total for c in cards), sum(c.in_use for c in cards), sum(c.reserved for c in cards),
                     None if any(x is None for x in progs) else sum(x or 0 for x in progs),
                     sum(c.other for c in cards), sum(c.kept for c in cards), sum(c.model for c in cards),
                     sum(c.own for c in cards))


def card_words(cards: List[CardParts]) -> str:
    """The GPU side's line at the simple level, its parts adding up to the card (in use is the remainder): the card's
    8.0 GiB less 3.1 in use and 0.5 kept free."""
    t = sum_parts(cards)
    kept = gib_short(t.kept).replace(" GiB", "")
    dec = len(kept.partition(".")[2]) or 1
    total, model = round(t.total / GIB, 1), round(t.model / GIB, 1)
    in_use = max(total - model - float(kept), 0.0)
    whose = "the card's" if len(cards) == 1 else f"the {len(cards)} cards'"
    return f"{whose} {total:.1f} GiB less {in_use:.{dec}f} in use and {kept} kept free"


SIDE_W = (38, 60)                                       # the side column: the Quick tip, About, the model list
MAIN_MIN = 90                                           # the settings column needs this much to sit beside it
LABEL_W = 17
SOURCE = {"auto-tune": "Auto-tune", "catalogue": "catalogue", "header": "the file", "default": "CARL's default",
          "config": "your setting"}
FLAGS: Dict[str, str] = {"model": "-m · llama.model", "quant": "-m (the file) · llama.model (the file's name)",
                         "ctx": "-c · ctx", "slots": "--parallel · slots",
                         "spec": "--spec-type · spec, spec_n", "kv": "-ctk -ctv · kv", "cache": "--cache-ram · llama.cache_ram",
                         "net": "--host · llama.net", "temp": "--temp · temp", "presence": "--presence-penalty · presence",
                         "top_k": "--top-k · top_k", "top_p": "--top-p · top_p", "min_p": "--min-p · min_p",
                         "repeat": "--repeat-penalty · repeat", "ub": "-ub · models.NAME.ub",
                         "ckpt": "--ctx-checkpoints · llama.ckpt", "ckstep": "--checkpoint-min-step · llama.ckpt_step",
                         "swa": "--swa-full · cache.swa",
                         "cpu_moe": "--n-cpu-moe · models.NAME.n_cpu_moe (unset: llama.n_cpu_moe)"}
SIDE_AT = 150                                           # the side column (About, the model list) from this width
SECTIONS = ["srv", "srvmem", "srvfit", "modelinfo", "srvabout", "srvlist"]


def confirm_restart(run: bool, port: int, changes: List[str], w: int, new_port: Optional[int] = None,
                    taken: str = "") -> List[Row]:
    """Apply's question: restart (or start) the server with these settings? It lists the changes. new_port: the port is
    taken by another program (`taken`): the start offers this free one (Phase 23.5), never a silent change."""
    if run:
        first = (["You changed:", *[f"  {c}" for c in changes]] if changes else
                 ["Nothing changed. Restart the server anyway?"])
        L: List[CardLine] = ["", *first, "",
                             "Requests in progress stop. The model loads again (30 s to 2 min).",
                             "If the new server does not start, CARL starts the old server again.", "",
                             buttons("  ", [("Restart (y)", make(Verb.SETYES)), ("Cancel (n)", make(Verb.SETNO))])]
    elif new_port:
        L = ["", f"Port {port} is in use by another program ({taken}).",
             f"This starts the server on port {new_port}. The model loads (30 s to 2 min).", "",
             buttons("  ", [(f"Start on port {new_port} (y)", make(Verb.SETYES)), ("Cancel (n)", make(Verb.SETNO))])]
    else:
        L = ["", f"This starts the server on port {port}. The model loads (30 s to 2 min).", "",
             buttons("  ", [("Start (y)", make(Verb.SETYES)), ("Cancel (n)", make(Verb.SETNO))])]
    return draw_card("confirm", "RESTART THE SERVER?" if run else "START THE SERVER?", "", L, w, 1)


class ServerPanel:
    """Draws the Server panel."""

    def __init__(self, svc: SettingsService, lines: ModelLines, card: ModelCard, config_file: str, home: str) -> None:
        self.svc = svc
        self.lines = lines
        self.card = card
        self.config_file = config_file
        self.home = home
        self.api_port: Optional[int] = None      # the dashboard API's port now (draw(): from ui)
        self.api_note = ""                       # why it is not the server's port + 1 ("8081 is in use (ssh)")

    # ------------------------------------------------------------ values
    def same(self, key: str, p: Pending, run: Pending, f: FitInfo) -> bool:
        """Is the pending value what the running server uses?"""
        rv = run.get("model" if key == "quant" else key)
        if rv is None:
            return False
        if key == "model":                      # the model, whichever quantization (the Quantization row's, B1)
            return self.svc.model_name(str(rv)) == self.svc.model_name(self.svc.resolved_model(p))
        if key == "quant":
            return str(rv) == self.svc.resolved_model(p)
        if key == "slots":
            return str(rv) == (str(f.slots) if p[key] == "auto" else str(p[key]))
        if key == "cache":
            return p[key] == "auto" or str(p[key]) == str(rv)
        if key == "spec":                       # or MTP off because experts are in the RAM (A51): not a change (W1.6)
            return spec_value(p) == f"{run.get('spec')}|{run.get('specn')}" or self.spec_resolved(p, run)
        if key == "ub" and self.ub_is_auto(p) and f.ub:     # auto: the -ub the start resolves (W1.6, A-M5)
            return str(rv) == str(f.ub)
        if key == "net":
            return str(rv) == str(p[key])
        if key == "swa":
            return str(rv) == (("full" if f.full else "window") if p[key] == "auto" else str(p[key]))
        if key == "cpu_moe":                    # auto: the layers the check plans (0 when the model fits the GPUs)
            return str(rv) == (str(f.cpu_moe) if p[key] == "auto" else str(p[key]))
        return str(fmt_val(key, rv)) == str(p[key])

    @staticmethod
    def spec_resolved(p: Pending, run: Pending) -> bool:
        """The server runs n-gram for the pending MTP + n-gram because experts are in the RAM (A51)."""
        return ram_resolved(str(p.get("spec") or ""), str(run.get("spec") or ""), run.get("cpu_moe"))

    def ub_is_auto(self, p: Pending) -> bool:
        """The Batch size row holds auto (W1.6): the start takes the model's own -ub and may lower it (A45)."""
        return "ub" in p and self.svc.ub_auto(self.svc.resolved_model(p), p.get("ub"))

    def api_same(self, p: Pending) -> bool:
        """The API Port row: the choice is what the API runs on (auto: wherever auto put it)."""
        return p.get("api") == "auto" or str(p.get("api")) == str(self.api_port)

    def choice_text(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        """Your choice, in words: 'auto' always with what it chose."""
        if key == "quant":
            return self.quant_text(p)
        v = p[key]
        if key == "model":
            name = self.svc.model_name(self.svc.resolved_model(p))
            return f"auto ({name})" if v == "auto" else name
        if key == "slots" and v == "auto":
            return f"auto ({f.slots})" if f.slots else "auto (2 when two fit)"
        if key == "cache" and v == "auto":
            rv = run.get("cache")
            mib = int(str(rv)) if str(rv).isdigit() and int(str(rv)) else self.svc.auto_cache_mib(p)
            return f"auto ({memory(mib * MIB)})" if mib else "auto (CARL sizes it at the start)"
        if key == "spec":
            return spec_name(p.get("spec"), p.get("specn"))
        if key == "swa" and v == "auto" and f.full is not None:
            return f"auto ({'full cache' if f.full else 'window cache'})"
        if key == "api" and v == "auto":
            return f"auto ({self.api_port})" if self.api_port else "auto"
        if key == "cpu_moe":
            return self.experts_text(p, f)
        if key == "ub" and self.ub_is_auto(p):              # auto: the model's own -ub, or less (A45; W1.6, A-M5)
            return f"auto ({f.ub or self.svc.model_ub(self.svc.resolved_model(p))})"
        return shown_value(key, v, long=True)

    def quant_text(self, p: Pending) -> str:
        """The Quantization row's value (B1): the quantization and ● downloaded or ○ not; grey with Model auto (it
        chooses both) and for a model with one quantization (or a file of its own)."""
        svc = self.svc
        name = svc.resolved_model(p)
        quant = svc.quant_name(name) or "–"
        if p.get("model") == "auto":
            return f"{DIM}auto ({quant}){R}"
        if not svc.quant_choices(p):
            return f"{DIM}{quant} (the only one){R}"
        m = svc.models.by_name(name)
        return f"{quant} " + (f"{GRN}●{R}" if m and m["status"] == "downloaded" else f"{DIM}○{R}")

    def experts_text(self, p: Pending, f: FitInfo) -> str:
        """The Experts in RAM row's value (W1.6, B2): the count and the model's top ("21 of 40", "auto (32 of 40)");
        "– (no experts)" for a dense model. Only the count: the GPU and RAM split is the MEMORY section's."""
        if self.svc.dense(p):
            return f"{DIM}– (no experts){R}"
        v, of = str(p["cpu_moe"]), f" of {f.moe_layers}" if f.moe_layers else ""
        if v == "auto":
            return f"auto ({f.cpu_moe}{of})" if f.moe_layers else "auto"
        return f"{v}{of}"

    def running_text(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        if key == "api":
            if not self.api_port:
                return f"{YEL}not running{': ' + self.api_note if self.api_note else ''}{R}"
            if self.api_note:
                return f"{YEL}{self.api_port}: {self.api_note}{R}"
            return f"{DIM}the same{R}" if self.api_same(p) else f"{YEL}{self.api_port}{R}"
        if not run or key in NOT_RUNNING:
            return f"{DIM}–{R}"
        if key == "spec" and self.spec_resolved(p, run):    # what runs, grey: the note under the table says why
            return f"{DIM}{spec_name(run.get('spec'), run.get('specn'))}{R}"
        if self.same(key, p, run, f):
            return f"{DIM}the same{R}"
        rv = run.get(key, "–")
        if key in ("model", "quant"):
            rm = str(run.get("model", "–"))
            return f"{YEL}{self.svc.model_name(rm) if key == 'model' else self.svc.quant_name(rm) or '–'}{R}"
        if key == "spec":
            text = spec_name(run.get("spec"), run.get("specn"))
        elif key == "cache":
            text = shown_value("cache", rv) if str(rv) != "0" else "–"
        else:
            text = shown_value(key, rv)
        return f"{YEL}{text}{R}"

    def recommended_text(self, key: str, p: Pending, rec: JSONDict, src: Dict[str, str]) -> str:
        if key == "model":
            return f"{DIM}see Auto fit below{R}"
        if key == "quant":                              # Auto fit's quantization when it picks this model
            af = self.svc.auto_fit(p)
            name = self.svc.resolved_model(p)
            if af is None or not af.pick or self.svc.model_name(af.pick.name) != self.svc.model_name(name) or \
                    not self.svc.quant_choices(dict(p, model=af.pick.name)):
                return ""
            mine = af.pick.name == name and p.get("model") != "auto"
            return f"{DIM if mine else ''}{self.svc.quant_name(af.pick.name)} (Auto fit){R}"
        pk = MODEL_ROW_KEYS.get(key)
        if pk is None or pk not in rec:
            return ""
        if key == "ub":         # auto: the start picks the -ub from the tune record's or this computer's (W1.6)
            o = self.svc.order(self.svc.resolved_model(p))
            tuned = src.get(pk) == "auto-tune" or (o is not None and bool(o.tuned))
            who = SOURCE["auto-tune"] if tuned else SOURCE["default"]
            return f"{DIM if self.ub_is_auto(p) else ''}auto ({who}){R}"
        source = SOURCE.get(src.get(pk, ""), src.get(pk, ""))
        if key == "spec":
            text = spec_name(rec.get("spec"), rec.get("spec_n"))
            mine = spec_value(p) == f"{rec.get('spec')}|{rec.get('spec_n')}"
        else:
            text = shown_value(key, fmt_val(key, rec[pk]))
            mine = str(fmt_val(key, rec[pk])) == str(p[key])
        if mine and len(text) > 10:
            text = "the same"
        return f"{DIM if mine else ''}{text} ({source}){R}"

    # ------------------------------------------------------------ the panel
    def section(self, ui: UIState, name: str, title: str, summary: str, lines: List[CardLine], w: int,
                marks: int) -> List[Row]:
        """One section of the panel at its own level (its title shows it; Tab selects it, L changes it)."""
        lvl = ui.levels.get(name, 1)
        return draw_card(name, title, summary, wrapped(aligned(lines, w - 4), w - 4), w, lvl, marks, ui.section == name)

    def draw(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int,
             limit_src: str = "", out: Optional[Shown] = None) -> List[Row]:
        """The sections: SERVER (the settings), MEMORY, AUTO FIT, the MODEL card; beside them from SIDE_AT columns
        (else under them) ABOUT the selected setting and the model list. The page scrolls (wheel, PgUp PgDn)."""
        svc = self.svc
        out = Shown() if out is None else out
        full = ui.levels.get("srv", 1) == 2
        svc.full = full
        run = svc.running(d)
        self.api_port, self.api_note = ui.api_port, ui.api_note
        rws = svc.rows(p)
        set_row = out.fix("set_row", min(ui.set_row, len(rws) - 1))
        key = rws[set_row].key
        f = svc.fit_cached(p)
        w = cols - 1
        side_w = 60 if w >= SIDE_AT else 0
        main_w = w - side_w - 1 if side_w else w
        changed = [r for r in rws if r.key not in NOT_RUNNING and run
                   and not (self.api_same(p) if r.key == "api" else self.same(r.key, p, run, f))]

        L = self.intro(p, d, run, f, changed, port, main_w - 4)
        L += self.table(ui, p, run, f, rws, main_w - 4, full and main_w - 4 >= 140, set_row)
        if full:
            L += ["", row("your choices", f"{home_short(self.config_file, self.home)}"),
                  row("recommended", self.rec_source(p))]
        state = (f"{GRN}running{R}" if run else f"{RED}stopped{R}") + ("   router mode" if d.router is not None else "")
        if changed:
            state += f"   {YEL}{plural(len(changed), 'change')}{R}"
        main = self.section(ui, "srv", "SERVER", state, L, main_w, 3)
        mem, mem_sum = self.memory_lines(ui, p, f, limit_src, d, run)
        main += self.section(ui, "srvmem", "MEMORY", mem_sum, mem, main_w, 3)
        fit_l, fit_sum = self.auto_fit_lines(p)
        main += self.section(ui, "srvfit", "AUTO FIT", fit_sum, fit_l, main_w, 2)
        mlvl = ui.levels.get("modelinfo", 1)
        card = self.card.draw(p, key, main_w, mlvl, mlvl == 2, ui.section == "modelinfo")
        label = next(r.label for r in rws if r.key == key)
        about = self.section(ui, "srvabout", f"ABOUT: {label.upper()}", "",
                             self.about_lines(p, key, (side_w or w) - 4), side_w or w, 2)
        if side_w:
            lst = self.section(ui, "srvlist", "MODELS", "", self.model_list(ui, out, p, side_w - 4,
                                                                             max(len(main) + len(card) - len(about) - 4, 12)),
                               side_w, 2)
            rows = side_by_side(main + card, about + lst, main_w, pad_left=False)
        else:
            lst = self.section(ui, "srvlist", "MODELS", "", self.model_list(ui, out, p, w - 4, 16), w, 2)
            rows = indent(main + about + card + lst)
        out.sections = list(SECTIONS)
        if ui.confirm:
            ch = [f"{r.label}: {self.running_plain(r.key, p, run, f)} → {self.choice_plain(r.key, p, run, f)}"
                  for r in changed]
            rows = indent(confirm_restart(bool(run) and not ui.confirm_port, port, ch, min(w, 84), ui.confirm_port,
                                          ui.confirm_taken)) + rows
        self.set_keys(ui, out, key)
        out.revealed, top = reveal(ui.section, ui.revealed, rows, ui.set_scroll, height)
        top = out.fix("set_scroll", max(0, min(top, len(rows) - height)))
        out.more = len(rows) > top + height
        return rows[top:top + height]

    def about_lines(self, p: Pending, key: str, w: int) -> List[CardLine]:
        """The selected setting: what to do, what it is, its flag and config key; for the sliding window what
        each choice needs."""
        L: List[CardLine] = [*cwrap(f"{CYN}{row_instruction(key)}{R}", w), "", *cwrap(SET_HELP[key], w)]
        if key in FULL_ONLY:
            L += ["", *cwrap(f"{YEL}{ADV_WARN}{R}", w)]
        if key == "swa":
            nf, nw = self.svc.swa_needs(p)
            if nf or nw:
                L += ["", row("full cache", memory(nf)), row("window cache", memory(nw))]
        L += self.range_rows(p, key)
        if key == "quant":
            g = self.svc.group(self.svc.resolved_model(p))
            if g is not None and g.several:
                none = row_of(g, cast(ModelInfo, dict(g.entries[0], name="")))      # no quantization marked
                L += ["", row("this model", quant_line(none).replace("   ", ", "))]
        flag_, _, ck = FLAGS.get(key, "").partition(" · ")
        if flag_:
            L += ["", row("flag", flag_), row("config key", ck)]
        return L

    def range_rows(self, p: Pending, key: str) -> List[CardLine]:
        """A number row's range and steps (B6); Experts in RAM: the model's layers with experts and auto's count now."""
        if key == "cpu_moe":
            f = self.svc.fit_cached(p)
            if self.svc.dense(p):
                return ["", row("this model", f"{self.svc.resolved_model(p)} has no experts.")]
            if not f.moe_layers:
                return ["", row("range", "0 to 999 (the model's layers are not known until it is downloaded)")]
            out = ["", row("this model", f"{f.moe_layers} layers with experts: 0 to {f.moe_layers}. {f.moe_layers}: "
                                         f"every expert in the RAM."),
                   row("auto", f"{plural(f.cpu_moe, 'layer')} now (the smallest number that fits the GPU).")]
            if not self.svc.full:
                out += [row("presets", "auto, " + range_words(key, f.moe_layers, False)),
                        row("full level", "steps of one layer, or a typed count (L)")]
            return out
        if key in NUMERIC and stepped(key):
            top = self.svc.top(key, p)
            if self.svc.full:
                return ["", row("range", range_words(key, top))]
            finer = ladder(key, top, False) != ladder(key, top)
            return ["", row("presets", range_words(key, top, False)),
                    row("full level", (f"{range_ends(key, top)} in finer steps, or a typed value (L)" if finer else
                                       "a typed value (L)"))]
        return []

    def running_plain(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        if key == "api":
            return str(self.api_port or "–")
        if key in ("model", "quant"):
            rm = str(run.get("model", "–"))
            return self.svc.model_name(rm) if key == "model" else self.svc.quant_name(rm) or "–"
        if key == "spec":
            return spec_name(run.get("spec"), run.get("specn"))
        return shown_value(key, run.get(key, "–"))

    def choice_plain(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        if key == "quant":                      # Apply's question: the quantization alone (B1)
            return self.svc.quant_name(self.svc.resolved_model(p)) or "–"
        return self.choice_text(key, p, run, f)

    def set_keys(self, ui: UIState, out: Shown, key: str) -> None:
        """The footer's keys and the ? card's lines for this panel (its mode: the list, a number typed)."""
        if ui.confirm:
            out.keys = [("y Enter", "yes"), ("n Esc", "cancel")]
        elif ui.edit is not None:
            out.keys = [("0-9 . k", "type the value"), ("Enter", "keep it"), ("Esc", "drop it"), ("Backspace", "delete")]
        elif ui.slist:
            out.keys = [("↑↓", "model"), ("Enter", "choose it"), ("m Esc", "back to the settings"), ("s f", "sort / show")]
        else:
            enter = "choose a model" if key == "model" else \
                "type a value" if key in NUMERIC and typed_at(key, self.svc.full) else ""
            out.keys = [("↑↓", "setting"), ("← →", "change"), ("a", "apply"), *([("Enter", enter)] if enter else []),
                        ("r", "undo"), ("x", "recommended"), ("Tab", "section"), ("L", "level"),
                        ("A", "Auto fit"), ("m", "model list"), ("[ ]", "panels"), ("PgUp PgDn", "scroll")]
        out.keys_more = ["The Running now column shows a value only when it is different from your choice.",
                        "At the full level of SERVER (L), the number rows take fine steps, and Enter types a "
                        "number (96k = 96K tokens).",
                        "To add a model from Hugging Face: the Models panel (]), then h.",
                        "After a change to the context or the slots, update OpenCode and Pi: the Connect tab, u."]

    def intro(self, p: Pending, d: ServerData, run: Pending, f: FitInfo, changed: List[SettingRow], port: int,
              w: int) -> List[CardLine]:
        """The state in one line (an unreadable model list first)."""
        svc = self.svc
        L: List[CardLine] = []
        if svc.models.error:                            # a broken catalogue / models.json: say so here
            L += cwrap(f"{RED}The model list is not available: {svc.models.error}{R}", w)
            L += cwrap(f"{DIM}Correct the file. {command()} models shows the same error. CARL reads the list again "
                       f"every 10 s.{R}", w) + [""]
        if d.router is not None:
            first = f"Router mode on port {port}: every model it offers uses these settings."
        elif run:
            rm = str(run.get("model"))
            g = svc.group(rm)
            shown = f"{g.name} ({svc.quant_name(rm)})" if g is not None and g.several else rm
            first = (f"{shown} runs on port {port}, {net_name(run.get('net', 'local'))}. "
                     + ("Your settings match it." if not changed else
                        f"{YEL}You changed {plural(len(changed), 'setting')}: a applies "
                        f"{'it' if len(changed) == 1 else 'them'} (the server restarts).{R}"))
        else:
            first = "The server is not running. a starts it with these settings."
        return L + cwrap(first, w) + [""]

    def table(self, ui: UIState, p: Pending, run: Pending, f: FitInfo, rws: List[SettingRow],
              w: int, flag_col: bool = False, set_row: int = 0) -> List[CardLine]:
        """The settings: label, your choice, running now, recommended (with its source); full detail: the
        flag and config key of each (a column when there is room, else under the row)."""
        svc = self.svc
        name = svc.resolved_model(p)
        rec, src = svc.recommended(name)
        cw = max(min(34, (w - 2 - LABEL_W) * 34 // 100), 24)
        rw = max(min(18, (w - 2 - LABEL_W - cw) // 3), 12)
        recw = w - 2 - LABEL_W - cw - rw - (30 if flag_col else 0)
        head = f"  {'Setting':<{LABEL_W}}{'Your choice':<{cw}}{'Running now':<{rw}}{'Recommended':<{recw}}"
        L: List[CardLine] = [f"{DIM}{head}{'Flag' if flag_col else ''}{R}"]
        notes: List[str] = []
        for i, r in enumerate(rws):
            sel = i == set_row
            val = self.choice_text(r.key, p, run, f)
            if sel and ui.edit is not None:
                val = f"{ui.edit}▏" + (f" of {f.moe_layers}" if r.key == "cpu_moe" and f.moe_layers else "")
            col = svc.value_color(r.key, p) if r.key != "model" else ""
            pre = f"{CYN}{B}›{R} " if sel else "  "
            text = f"{pre}{B if sel else ''}{r.label:<{LABEL_W}}{R}"
            spans: List[Tuple[int, int, str]] = [(0, 2 + LABEL_W, make(Verb.SETROW, i))]
            c0 = 2 + LABEL_W
            if r.key == "model":
                cell = f"{val} ▾"
                spans.append((c0, c0 + cw, make(Verb.SETPICK)))
            elif sel and r.choices:
                cell = f"‹ {val} ›"
                spans += [(c0, c0 + 2, make(Verb.SETDEC, i)), (c0 + 2, c0 + 2 + vlen(val), make(Verb.SETROW, i)),
                          (c0 + 3 + vlen(val), c0 + 5 + vlen(val), make(Verb.SETINC, i))]
            else:
                cell = val
                spans.append((c0, c0 + cw, make(Verb.SETROW, i)))
            text += fit(f"{col}{cell}{R}", cw - 1) + " " + fit(self.running_text(r.key, p, run, f), rw - 1) + " "
            rec_text = self.recommended_text(r.key, p, rec, src)
            L.append(Ln(text + fit(rec_text, recw - 1) + (f" {DIM}{FLAGS.get(r.key, '').split(' · ')[0]}{R}"
                                                         if flag_col else ""), spans=spans))
            note = svc.value_note(r.key, p, computer())
            if note:
                notes.append(note)
        if not self.svc.full:
            L.append(f"  {DIM}{'More settings':<{LABEL_W}}{R}" + fit(f"{DIM}Presence, sampling, batch size and "
                                                                       f"checkpoints: the full level (L).{R}",
                                                                       w - 2 - LABEL_W))
        L += [x for n in notes for x in cwrap(f"{RED}⚠{R} {n}", w, "  ")]
        L += [x for n in self.resolved_notes(p, run, f, rws) for x in cwrap(f"{DIM}{n}{R}", w, "  ")]
        L.append("")
        if ui.restart:
            L += cwrap(f"{YEL}The server is restarting now. The line at the bottom shows each step.{R}", w)
        else:
            go = "Apply and restart (a)" if run else "Start the server (a)"
            L += button_rows("", [(go, make(Verb.SETAPPLY if f.fits else Verb.SETNOFIT)),
                                  ("Undo my changes (r)", make(Verb.SETREVERT)),
                                  ("Use the recommended settings (x)", make(Verb.SETDEFAULTS))], w)
        return L

    def resolved_notes(self, p: Pending, run: Pending, f: FitInfo, rws: List[SettingRow]) -> List[str]:
        """The grey lines under the table about a value the start resolves (W1.6): MTP off with experts in the RAM; the
        Batch size row's auto when the start takes a smaller -ub."""
        out: List[str] = []
        keys = {r.key for r in rws}
        if "ctx" in keys and f.ctx and str(f.ctx) != str(p.get("ctx")):     # the start's window (SettingsService.order_ctx)
            out.append(f"Context: a start takes {tokens(f.ctx)} tokens per slot, the first setup in Auto fit's order that "
                       f"fits ({tokens(int(str(p.get('ctx'))))} is not your setting). MEMORY plans with it.")
        mtp = Spec(str(p.get("spec") or "none")).mtp
        if "spec" in keys and f.cpu_moe and mtp and not Spec(f.spec or "none").mtp:
            out.append("Speculation: MTP is off while experts are in the RAM, so the server runs n-gram. Not a change."
                       if run else "Speculation: MTP is off while experts are in the RAM, so a start runs n-gram.")
        own = self.svc.model_ub(self.svc.resolved_model(p)) if "ub" in keys else 0
        if "ub" in keys and self.ub_is_auto(p) and f.ub and f.ub != own:
            out.append(f"Batch size: auto is {own}, less when a smaller batch keeps the setup: {f.ub} here.")
        return out

    def memory_lines(self, ui: UIState, p: Pending, f: FitInfo, limit_src: str, d: Optional[ServerData] = None,
                     run: Optional[Pending] = None) -> Tuple[List[CardLine], str]:
        """Does it fit (a row and a bar), the sliding-window cache, a start that failed; full: the parts. And the
        section's summary. On a PC with discrete GPUs: the GPU and the RAM side each with its bar and what it is made
        of, the experts in the RAM as planned now beside the running count with the reason (W1.6, A-M2); full: where
        the card's and the RAM's memory go (A-M1). A Mac's full level: the RAM's block (A-M3)."""
        full = ui.levels.get("srvmem", 1) == 2
        devices = self.svc.devices()
        ok = f.known and f.downloaded and not f.error
        if ok and devices and not any(x.shared for x in devices):
            L, summary = self.pc_memory(p, f, devices, full, d, run or {})
        else:
            L, summary = self.mac_memory(p, f, full, limit_src)
        if f.full is not None:
            L.append(row("sliding window", "Full cache: saved sessions can be restored." if f.full else
                         f"{YEL}Window cache{R}: saved sessions cannot be restored."))
        if ui.start_error:
            L += ["", f"{RED}{B}The last start failed. The launcher said:{R}"]
            L += [f"{RED}{e}{R}" for e in ui.start_error[:8]]
        return L, summary

    def mac_memory(self, p: Pending, f: FitInfo, full: bool, limit_src: str) -> Tuple[List[CardLine], str]:
        """The one memory of a Mac (and a PC without a discrete GPU): needs of the limit; full: the RAM's block, whose
        parts add up to it (W1.6, A-M3), then the parts of the need."""
        L: List[CardLine] = []
        ok = f.known and f.downloaded and not f.error
        if not ok:
            return [fit_sentence(f)], f"{RED}✗{R}"
        need, limit = gib_pair(f.need, f.limit)
        L.append(row("fits", f"{GRN}✓ yes{R}" if f.fits else f"{RED}✗ no{R}"))
        L.append(row("needs", f"{bar(f.need / f.limit if f.limit else 0, 30)}  {need.replace(' GiB', '')} of {limit}"))
        if not f.fits:
            L.append(row("at most", f"{tokens(f.largest)} per slot with {plural(f.slots, 'slot')}" if f.largest
                         else "The weights alone do not fit."))
        if not f.fits and f.gpus > 1 and f.need <= f.limit:        # the rooms added up would do, the split does not
            L.append(row("split", f"The layers do not fit the {f.gpus} GPUs one by one (each has a compute buffer of "
                                  f"its own)."))
        summary = (f"{GRN}✓{R} {need.replace(' GiB', '')} of {limit}" if f.fits else f"{RED}✗ {need} of {limit}{R}")
        if f.cpu_moe:                            # a MoE model larger than the GPUs (W6.8): need is the GPU side
            L.append(row("experts in RAM", f"{plural(f.cpu_moe, 'layer')}, {memory(f.experts)}"
                                           + ("" if f.ram_ok else f"   {RED}the RAM is too small{R}")))
        if f.dropped:
            L.append(row("speculation", f"{YEL}n-gram only{R}: MTP does not fit with these slots and this context. "
                                        f"The server starts without it."))
        if full:
            L.append("")
            ram = float(self.svc.ram)
            if ram > 0 and not f.gpus:
                af = self.svc.auto_fit(p)                  # the reserve Auto fit keeps (more with the VM's network up)
                L += self.ram_block(ram, f.limit, limit_src, af.budget.reserve if af is not None else RAM_RESERVE)
            L += [row("needs" if ram > 0 and not f.gpus else "weights",
                      f"{memory(f.need if ram > 0 and not f.gpus else f.weights):>9}" +
                      (f"   {DIM}of the {gib_short(f.limit)}{R}" if ram > 0 and not f.gpus else ""))]
            parts = [("weights", f.weights), ("drafter", f.drafter), ("context memory", f.context),
                     ("recurrent state", f.state), ("buffers", f.buffers)]
            if ram > 0 and not f.gpus:
                L += [row(f"  {k[:1].upper()}{k[1:]}", f"{memory(v) if v else '0':>9}") for k, v in parts]
            else:
                L += [row(k, f"{memory(v) if v else '0':>9}") for k, v in parts[1:]]
                L.append(row("GPU limit", f"{memory(f.limit):>9}" + (f"   {DIM}{limit_src}{R}" if limit_src and "Metal"
                                                                       not in limit_src else "")))
        return L, summary

    def ram_block(self, ram: float, limit: float, limit_src: str, reserve: float = RAM_RESERVE) -> List[CardLine]:
        """The RAM of a Mac in parts that add up to it (A-M3): kept for macOS and apps, above the GPU limit (only when
        more than 0), for a model (the GPU limit). Where the limit reaches into the part kept, the kept row is the rest
        and says so."""
        words = os_and_apps()
        out: List[CardLine] = [row("the RAM", f"{memory(ram):>9}   {DIM}the GPU and the CPU share it{R}")]
        above = ram - reserve - limit
        src = limit_src if limit_src and "Metal" not in limit_src else "the GPU limit"
        if above >= 0:
            out.append(row("  Kept", f"{memory(reserve):>9}   {DIM}for {words}{R}"))
            if above >= 0.05 * GIB:
                out.append(row("  Above the limit", f"{memory(above):>9}   {DIM}macOS lets the GPU use "
                                                    f"{gib_short(limit)} (Metal's working set){R}"))
            out.append(row("  For a model", f"{memory(limit):>9}   {DIM}{src}{R}"))
        else:
            out.append(row("  Kept", f"{memory(max(ram - limit, 0)):>9}   {DIM}the rest: less than the "
                                     f"{gib_short(reserve)} Auto fit keeps for {words}{R}"))
            out.append(row("  For a model", f"{memory(limit):>9}   {DIM}{src}{R}"))
        return out

    def pc_memory(self, p: Pending, f: FitInfo, devices: List[Slot], full: bool, d: Optional[ServerData],
                  run: Pending) -> Tuple[List[CardLine], str]:
        """MEMORY on a PC with discrete GPUs (W1.6, A-M1, A-M2): the GPU side and the RAM side, each of the part for a
        model, with what it is the rest of; the experts in the RAM (the plan now, the running count and why); full:
        the card's parts and the RAM's, and each side's parts."""
        try:
            keep = int(float(str(p.get("keep"))) * GIB)           # the row GPU memory kept, pending (A46)
        except ValueError:
            keep = int(0.5 * GIB)
        cards = [c for c in (card_parts(x.gpu, keep, x.own) for x in devices) if c is not None]
        moe = f.cpu_moe > 0
        gpu_ok = f.need <= f.limit and f.split_ok
        need, limit = gib_pair(f.need, f.limit)
        rneed, rroom = gib_pair(f.ram_need, f.ram_room)
        L: List[CardLine] = []
        if f.fits:
            L.append(row("fits", f"{GRN}✓ yes{R}: " + ("on the GPU and in the RAM" if moe else "all on the GPU")))
        elif not gpu_ok:
            mine = str(p.get("cpu_moe", "auto")).isdigit() and moe
            L.append(row("fits", f"{RED}✗ no{R}: the GPU is too small" + (" for your count" if mine else "")))
        else:
            L.append(row("fits", f"{RED}✗ no{R}: the RAM is too small for the experts"))
        L.append(row("GPU", f"{bar(f.need / f.limit if f.limit else 0, 30)}   {need.replace(' GiB', '')} of {limit}"))
        if cards:
            L.append(row("", f"{DIM}{card_words(cards)}{R}"))
        if moe:
            L.append(row("RAM", f"{bar(f.ram_need / f.ram_room if f.ram_room else 0, 30)}  "
                                f"{rneed.replace(' GiB', '')} of {rroom}"))
            ram = float(self.svc.ram)
            if ram > 0 and f.ram_room:
                L.append(row("", f"{DIM}the RAM's {gib_short(ram)} less {gib_short(ram - f.ram_room)[:-4]} kept for "
                                 f"{os_and_apps()}{R}"))
            free = int(getattr(d.system, "available", 0) or 0) if d is not None else 0
            if free and f.ram_need > free:           # A-M2: checked against the RAM less the reserve; said when larger
                L.append(row("", f"{YEL}The RAM has {gib_short(free)} free now, less than the {gib_short(f.ram_need)} "
                                 f"this setup puts there.{R}"))
        elif f.known:
            L.append(row("RAM", f"{DIM}– (the model is on the GPU; the RAM holds the RAM cache){R}"))
        if moe or f.moe_layers:
            L += self.experts_rows(p, f, run, d, full)
        if not f.fits and not gpu_ok and not moe:
            L.append(row("at most", f"{tokens(f.largest)} per slot with {plural(f.slots, 'slot')}" if f.largest
                         else "The weights alone do not fit."))
        if not f.fits and gpu_ok and moe and f.ram_room:
            L.append(row("at most", f"{gib_short(f.ram_room)} in the RAM: a smaller quantization or model."))
        if not f.fits and f.gpus > 1 and f.need <= f.limit:
            L.append(row("split", f"The layers do not fit the {f.gpus} GPUs one by one (each has a compute buffer of "
                                  f"its own)."))
        if f.dropped:
            L.append(row("speculation", f"{YEL}n-gram only{R}: MTP does not fit with these slots and this context. "
                                        f"The server starts without it."))
        if full:
            L += self.pc_parts(f, cards, devices, run, int(getattr(d.system, "available", 0) or 0) if d else 0)
        if f.fits:
            summary = f"{GRN}✓{R} GPU {need.replace(' GiB', '')} of {limit}" + (
                f", RAM {rneed.replace(' GiB', '')} of {rroom}" if moe else "")
        elif not gpu_ok:
            summary = f"{RED}✗ GPU {need.replace(' GiB', '')} of {limit}{R}"
        else:
            summary = f"{RED}✗ RAM {rneed.replace(' GiB', '')} of {rroom}{R}"
        return L, summary

    def experts_rows(self, p: Pending, f: FitInfo, run: Pending, d: Optional[ServerData], full: bool) -> List[CardLine]:
        """Experts in RAM as planned now (auto or your count), the least count that fits when yours does not, and the
        count the server runs with the reason when it differs (the start's record in its server file)."""
        layers, n = f.moe_layers, f.cpu_moe
        of = f" of {layers}" if layers else ""
        mine = str(p.get("cpu_moe", "auto")).isdigit()
        if not n and not mine:
            return [row("experts in RAM", "none: the whole model fits the GPU (auto)")]
        out: List[CardLine] = [row("experts in RAM", f"{n}{of} layers, {memory(f.experts)}" + (
            ": your setting" if mine else " (auto: the fewest that fit the GPU now)"))]
        if mine and not (f.need <= f.limit and f.split_ok):
            auto = self.svc.fit_cached(dict(p, cpu_moe="auto"))
            if auto.cpu_moe:
                one = f.experts / n if n else 0
                out.append(row("at least", f"{plural(auto.cpu_moe, 'layer')}: the fewest that fit the GPU now (auto)"
                                           + (f"; one layer is about {one / GIB:.2f} GiB." if one else ".")))
        ran = run.get("cpu_moe") if run else None
        if ran is None or not self.same("model", p, run, f) or not self.same("quant", p, run, f):
            return out
        rn = int(str(ran)) if str(ran).isdigit() else 0
        flag = f" (--n-cpu-moe {rn})" if full and rn else ""
        if rn == n:
            return out + [row("running", f"{rn}{of} layers, the same{flag}")]
        rec = d.start if d is not None else {}
        room = rec.get("room") if isinstance(rec.get("room"), int) else 0
        moves = (f"Apply and restart (a) moves the experts of {plural(rn - n, 'layer')} to the GPU." if rn > n else
                 f"Apply and restart (a) moves the experts of {plural(n - rn, 'layer')} more to the RAM.")
        if mine and str(rn) != str(p.get("cpu_moe")):
            why = f"the server runs the count it was started with, not your {p.get('cpu_moe')}"
        elif room and abs(room - f.limit) >= 64 * MIB:
            why = f"at its start the GPU had {gib_short(room)} for a model, now {gib_short(f.limit)}"
        elif rec.get("ub") and f.ub and int(rec.get("ub") or 0) != f.ub:
            why = f"it started at -ub {rec.get('ub')}, the plan now is at -ub {f.ub}"
        else:
            why = "the plan now differs from the one at its start"
        return out + [row("running", f"{rn}{of} layers{flag}: {why}."), row("", moves)]

    def pc_parts(self, f: FitInfo, cards: List[CardParts], devices: List[Slot], run: Pending,
                 free: int = 0) -> List[CardLine]:
        """The full level on a PC: where the card's memory goes (it adds up: in use, kept free, for a model) and what
        the plan puts on the GPU; where the RAM goes, with the RAM free now (free: 0 not known), and what the plan
        puts there (W1.6, A-M1, A-M2)."""
        L: List[CardLine] = [""]
        if cards:
            t = sum_parts(cards)
            names = ", ".join(x.gpu.name for x in devices)
            L.append(row("The GPU", f"{memory(t.total):>9}   {DIM}{names}{R}"))
            detail = [f"reserved {memory(t.reserved)}"] if t.reserved else []
            detail += [f"programs {memory(t.programs)}"] if t.programs is not None else []
            detail += [f"in no program {memory(t.other)}"] if t.programs is not None else []
            L.append(row("  In use", f"{memory(t.in_use):>9}" + (f"   {DIM}{', '.join(detail)}{R}" if detail else "")))
            L.append(row("  Kept free", f"{memory(t.kept):>9}   {DIM}the row GPU memory kept{R}"))
            own = f"; the server uses {gib_short(t.own)[:-4]} of it now" if t.own and run else ""
            L.append(row("  For a model", f"{memory(t.model):>9}   {DIM}the rest{own}{R}"))
        L.append(row("On the GPU", f"{memory(f.need):>9}   {DIM}of the {gib_short(f.limit)}{R}"))
        L += [row("  Weights", f"{memory(f.weights):>9}" + (f"   {DIM}the model less the experts in the RAM{R}"
                                                             if f.cpu_moe else "")),
              row("  Drafter", f"{memory(f.drafter) if f.drafter else '0':>9}"),
              row("  Context memory", f"{memory(f.context):>9}"), row("  Recurrent state", f"{memory(f.state):>9}"),
              row("  Buffers", f"{memory(f.buffers):>9}")]
        ram = float(self.svc.ram)
        if f.cpu_moe and ram > 0 and f.ram_room:
            L += ["", row("The RAM", f"{memory(ram):>9}"),
                  row("  Kept", f"{memory(ram - f.ram_room):>9}   {DIM}for {os_and_apps()}{R}"),
                  row("  For a model", f"{memory(f.ram_room):>9}   {DIM}the rest"
                                       + (f" (free now: {gib_short(free)})" if free else "") + R),
                  row("In the RAM", f"{memory(f.ram_need):>9}   {DIM}of the {gib_short(f.ram_room)}{R}"),
                  row("  Experts", f"{memory(f.experts):>9}   {DIM}{f.cpu_moe} of {f.moe_layers or f.cpu_moe} "
                                   f"layers{R}")]
            if f.ram_other:
                L.append(row("  Other weights", f"{memory(f.ram_other):>9}   {DIM}the token embeddings{R}"))
            L.append(row("  CPU buffer", f"{memory(max(f.ram_need - f.experts - f.ram_other, 0)):>9}   "
                                         f"{DIM}the CPU's compute buffer (an estimate){R}"))
        return L

    def auto_fit_lines(self, p: Pending) -> Tuple[List[CardLine], str]:
        """What Auto fit suggests for this Mac, as rows; the summary is its pick."""
        svc = self.svc
        af = svc.auto_fit(p)
        if af is None:
            return [f"{YEL}Auto fit cannot work now: {svc.models.fit_error or 'no model list'}.{R}"], ""
        fast = [row("fast option", f"{af.fast[0].name} with {af.fast[1].describe()}: Auto fit takes it only when no "
                                   f"other model of its rank or better fits. Choose it in the model list.")] if af.fast else []
        if not af.pick or not af.plan:
            return [f"{RED}No model fits {computer()} with this goal.{R} The Auto fit panel (A) tells why.", *fast], ""
        name = af.pick.name
        m = svc.models.by_name(name)
        mine = name == svc.resolved_model(p)
        L: List[CardLine] = [Ln(row("suggests", f"{CYN}{name}{R} ★" + (f"   {DIM}your choice{R}" if mine else "")),
                                act=make(Verb.SP, SP_FIT)),
                             row("with", plan_words(af.plan.slots, af.plan.ctx) + af.plan.ram_text())]
        if not af.pick.downloaded:
            L.append(row("download", f"{file_size(m['bytes']) if m else 'Its size is not known'}. Download it in the "
                                     f"Auto fit panel (A)."))
            if p.get("model") == "auto":
                L.append(row("until then", f"Auto starts {svc.resolved_model(p)}."))
        L.append(row("why", f"{af.order_text()}. The Auto fit panel (A) tells more."))
        return L + fast, name

    def rec_source(self, p: Pending) -> str:
        m = self.svc.models.by_name(self.svc.resolved_model(p))
        t = jdict(jdict(m.get("local")).get("tune")) if m else {}
        if t:
            return f"Auto-tune on {computer()} ({t.get('date', '?')}, {t.get('machine', '?')}), else the catalogue."
        return f"The catalogue. Auto-tune did not measure this model on {computer()}."

    def model_list(self, ui: UIState, out: Shown, p: Pending, w: int, height: int) -> List[CardLine]:
        """The model selector (w columns, height lines): click a model (or m, then ↑↓ Enter); the sort and
        show drop-downs. Only names and status: the MODEL card below has the rest."""
        listed = self.lines.visible(ui.msort, ui.mfilter, self.lines.shown(ui))
        items = ["auto"] + [m["name"] for m in listed]
        rows = {m["name"]: m for m in listed}
        cur = str(p.get("model", "auto"))
        srow = ui.srow
        if not ui.slist:                                    # the cursor follows the chosen model
            srow = items.index(cur) if cur in items else 0
        srow = out.fix("srow", max(0, min(srow, len(items) - 1)))
        ms = {m["name"]: m for m in self.svc.models.get()}
        af = self.svc.auto_fit(p)
        pick = af.name if af else None
        so, fi = arrange_label(ui.msort, ui.mfilter)
        L: List[CardLine] = [f"{GRN}●{R} downloaded   {DIM}○{R} not downloaded",
                             f"{CYN}★{R} Auto fit's choice   {RED}red{R} = does not fit",
                             Ln(fit(f"Sort: {CYN}{so}{R} (s)", w), act=make(Verb.MSORTPICK)),
                             Ln(fit(f"Show: {CYN}{fi}{R} (f)", w), act=make(Verb.MFILTERPICK))]
        vis = max(height - len(L) - 1, 3)
        top = min(max(srow - vis // 2, 0), max(len(items) - vis, 0))
        for i in range(top, min(top + vis, len(items))):
            name = items[i]
            st = ms[name]["status"] if name in ms else ""
            dot = (f"{GRN}●{R}" if st == "downloaded" else f"{YEL}◐{R}" if st == "partial" else f"{DIM}○{R}")
            chosen = name == cur
            col = (B + CYN) if chosen else RED if name in ms and self.lines.too_big(ms[name]) else ""
            if name == "auto":
                text = f"  {col}auto{R} {DIM}(now: {self.svc.resolved_model(dict(p, model='auto'))}){R}"
            else:                                           # each model once, its quantization grey (B1)
                shown = rows.get(name, {})
                label = fit(f"{col}{shown.get('group') or name}{R}" + (f" {CYN}★{R}" if name == pick else ""), 26)
                text = f"{dot} {label}  {DIM}{self.svc.quant_name(name)}{R}"
            if ui.slist and i == srow:
                text = f"\x1b[7m{fit(text, w - 2)}{R}"
            L.append(Ln(("› " if chosen else "  ") + text, act=make(Verb.SMODEL, name)))
        more = len(items) - (top + vis)
        L.append(f"{DIM}" + (f"… and {more} more. Press m, then ↑↓." if more > 0 else "Press m to choose with the keys.") + R)
        return L


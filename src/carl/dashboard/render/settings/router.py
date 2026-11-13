"""The Router panel: single model (you choose the model in the dashboard) or router mode (OpenCode and
Pi switch the model), the router's models with Load / Unload, its recent switches, and whether the
OpenCode and Pi configs on this Mac list the installed models; beside them (or under them) the Quick
tip and the help. Each part is a section with its own level; the page scrolls. Pure."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ....domain.server.snapshot import RouterModel, ServerData, flag
from ....ui.fmt import B, CYN, DIM, GRN, R, YEL, CardLine, Ln, Row, buttons, ctx_label, fit, row, vlen
from ....ui.words import command, computer, kv_name
from ...act import Verb, act as make
from ...clients import Drift
from ...shown import Shown
from ...state import UIState
from .common import choice_line
from .page import Part, body_height, help_part, layout, scrolled, section, side_width

MODE_NAMES = {"single": "single model", "router": "router mode"}
RUNS = {"single": "one model", "router": "router mode"}
COST = ("After a switch, the new model has none of the sessions in memory. A saved session belongs to the model it ran "
        "on: when you switch back to that model, OpenCode and Pi restore it from the disk cache (with Saved sessions "
        "on, and for a Gemma model the full cache). A session that continues on the new model is read again: minutes "
        "for a long session.")


def setup_of(m: RouterModel) -> Tuple[str, str]:
    """A router model's slots × context and its context memory type, from the router's arguments."""
    args = " ".join(m.args)
    per = flag(args, "--kv-unified-per-slot") or flag(args, "-c", "--ctx-size") or "?"
    slots = flag(args, "--parallel", "-np") or "1"
    kv = flag(args, "-ctk", "--cache-type-k") or ""
    return f"{slots} × {ctx_label(int(per)) if per.isdigit() else per}", kv_name(kv, short=True) if kv else "–"


def router_rows(models: Sequence[RouterModel], sel: int, w: int) -> List[CardLine]:
    """The router's models as a table: ● loaded / ○ not, the name, the state, slots × context, the context
    memory, Load / Unload; a failed last load on its own row."""
    nw = max([len(m.id) for m in models] + [5]) + 2
    out: List[CardLine] = [f"{DIM}    {'Model':<{nw}}{'State':<11}{'Slots × context':<17}{'Context memory':<16}{R}"]
    for i, m in enumerate(models):
        col = {"loaded": GRN, "loading": YEL, "sleeping": CYN}.get(m.status, DIM)
        dot = {"loaded": "●", "loading": "◐", "sleeping": "◑"}.get(m.status, "○")
        setup, kv = setup_of(m)
        pre = f"{CYN}{B}›{R} " if i == sel else "  "
        text = f"{pre}{col}{dot}{R} {m.id:<{nw}}{col}{m.status:<11}{R}{setup:<17}{kv:<16}"
        label, act = ("[ Unload ]", make(Verb.RUNLOAD, m.id)) if m.active else ("[ Load ]", make(Verb.RLOAD, m.id))
        c = vlen(text)
        out.append(Ln(fit(text + f"{B}{CYN}{label}{R}", w), spans=[(c, c + len(label), act)]))
        if m.failed:
            out.append(f"{' ' * (nw + 4)}{YEL}Its last load failed. The Log tab tells why.{R}")
    return out


def drift_rows(stale: Sequence[Drift], installed: int) -> List[CardLine]:
    """The ⚠ lines of the OpenCode and Pi configs as label rows: the client, what an update adds and removes. A config
    that lists other models than the installed ones is taken apart here; any other drift (the server moved, one model in
    single-model mode, the context) is its sentence."""
    out: List[CardLine] = []
    for dr in stale:
        if dr.moved or dr.server or not (dr.added or dr.removed):
            out.append(row("⚠", f"{YEL}{dr.line(installed).removesuffix(' Press u.')}{R}"))
            continue
        out.append(row(dr.client, f"{YEL}⚠ Out of date.{R} It lists {dr.listed} {'model' if dr.listed == 1 else 'models'}, "
                                  f"and {installed} {'is' if installed == 1 else 'are'} installed."))
        for verb, names in (("adds", dr.added), ("removes", dr.removed)):
            if names:
                out.append(row(f"an update {verb}", ", ".join(names)))
        if dr.window:
            out.append(row("", f"{dr.client}: the context of {dr.window}."))
    return out


def router_panel(ui: UIState, d: ServerData, saved: str, switches: Sequence[Tuple[str, str]], stale: Sequence[Drift],
                 installed: int, cols: int, here: bool = False, height: int = 0,
                 out: Optional[Shown] = None) -> List[Row]:
    """The sections: ROUTER (the mode, saved and running), the router's models with Load / Unload, its recent
    switches, the OpenCode and Pi configs on this Mac; the Quick tip and the help beside them or under them.
    The page scrolls."""
    out = Shown() if out is None else out
    running = "router" if d.router is not None else "single" if d.up else ""
    models = d.router.models if d.router is not None else []
    sel = out.fix("router_row", max(min(ui.router_row, len(models) - 1), 0)) if d.router is not None else ui.router_row

    def mode_section(w: int) -> List[Row]:
        full = ui.levels.get("router", 1) == 2
        L: List[CardLine] = [f"{DIM}Choose who changes the model: you in the dashboard, or OpenCode and Pi.{R}", ""]
        L += choice_line("Mode", [("single", "Single model (s)", make(Verb.RMODE, "single")),
                                  ("router", "Router mode: OpenCode and Pi switch (r)", make(Verb.RMODE, "router"))], saved, w - 4,
                         13)
        L += [row("running now", RUNS[running] if running else f"{DIM}stopped{R}"),
              row("next start", MODE_NAMES.get(saved, saved) if running in ("", saved) else
                  f"{YEL}{MODE_NAMES.get(saved, saved)}{R}")]
        if running not in ("", saved):
            L.append(row("", "This is not the mode that is running now."))
        if full:
            L.append(row("config key", f"llama.mode = {saved}"))
        summary = MODE_NAMES[running] if running else f"{DIM}{MODE_NAMES.get(saved, saved)} (stopped){R}"
        return section(ui, "router", "ROUTER", summary, L, w, 3)

    def models_section(w: int) -> List[Row]:
        L = router_rows(models, sel, w - 4) if models else [
            f"{DIM}No downloaded model fits. The command {command()} fit tells why.{R}"]
        loaded = sum(1 for m in models if m.status == "loaded")
        return section(ui, "rtmodels", "MODELS THE ROUTER OFFERS",
                       f"{len(models)} {'model' if len(models) == 1 else 'models'}, {loaded} loaded", L, w, 2)

    def switches_section(w: int) -> List[Row]:
        L: List[CardLine] = [f"{DIM}Time   Model{R}", *[f"{t[:5]}  {name}" for t, name in list(switches)[-5:]]]
        return section(ui, "rtswitches", "RECENT SWITCHES", f"Last {list(switches)[-1][0][:5]}", L, w, 2)

    def clients_section(w: int) -> List[Row]:
        if stale:
            L, summary = drift_rows(stale, installed), f"{YEL}out of date{R}"
        elif here:
            L, summary = [f"{GRN}✓{R} Their configs list the installed models."], "up to date"
        else:
            L, summary = [f"{DIM}They are not set up on {computer()} for this server. The Connect tab sets them up.{R}"], \
                "not set up"
        L += ["", buttons("", [("Update the OpenCode and Pi configs (u)", make(Verb.INSCONFIG))])]
        return section(ui, "rtclients", f"OPENCODE AND PI ON {computer().upper()}", summary, L, w, 2)

    parts: List[Part] = [Part("router", mode_section)]
    if d.router is not None:
        parts.append(Part("rtmodels", models_section))
        if switches:
            parts.append(Part("rtswitches", switches_section))
    parts.append(Part("rtclients", clients_section))
    tip = ("Press Enter to load the selected model now. OpenCode and Pi load the model that they ask for."
           if d.router is not None else "Press s or r to choose who changes the model.")
    full_settings = ui.levels.get("rtsettings", 1) == 2
    parts += [
        help_part(ui, "rttip", "QUICK TIP", [f"{CYN}{tip}{R}"]),
        help_part(ui, "rtwho", "WHO CHANGES THE MODEL", [
            f"{B}Single model{R} (the default): one model runs. You or Auto fit choose it in the Server panel. OpenCode "
            f"and Pi use the model that runs.",
            f"{B}Router mode{R} is for work that changes the model as it goes. The router offers all downloaded models "
            f"that fit. It loads the model that you choose in OpenCode (/models) or Pi (/model), one model at a time. "
            f"A switch takes 30 s to 2 min: the old model stops and the new model loads. Auto fit chooses only the "
            f"first model that loads."]),
        help_part(ui, "rtcost", "EACH SWITCH COSTS TIME", [COST]),
        help_part(ui, "rtsettings", "SETTINGS PER MODEL", [
            "Router mode gives each model the same settings as a single start of that model: your settings, else "
            "Auto-tune's, else the catalogue's. It does not offer a model that does not fit.",
            *(["If a client asks for a model that is not installed, the client gets an error (HTTP 400) and no "
               "model loads. The OpenCode plugin shows this error.",
               "For a custom model, set the thinking field of its card (the Models panel): OpenCode then shows the "
               "correct levels."] if full_settings else [])], marks=3)]
    if d.router is not None:
        parts.append(help_part(ui, "rtload", "LOAD AND UNLOAD", [
            "Load starts a model now. The loaded model stops first. Unload frees the memory."]))
    rows = layout(out, parts, cols, side_width(cols), balance=True)
    h = body_height(height)
    out.keys = [("s r", "mode")]
    if d.router is not None:
        out.keys += [("↑↓", "model"), ("Enter", "load / unload")]
    out.keys.append(("u", "update configs"))
    if len(rows) > h:
        out.keys.append(("PgUp PgDn", "scroll"))
    out.keys += [("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
    out.keys_more = ["Press s for single model or r for router mode. CARL asks first. When a server is running, CARL restarts "
                    "it in the new mode.",
                    f"The u key runs the installer for {computer()}. The Connect tab shows its output."]
    return scrolled(ui, out, rows, h, "page_scroll")

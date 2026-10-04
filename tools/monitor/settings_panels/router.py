"""The Router panel: who switches the model (the dashboard, or OpenCode / Pi through a llama.cpp
router), the router's models with Load / Unload, its recent switches, and whether the client
configs list the installed models. Pure."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from ..fmt import (B, CYN, DIM, GRN, R, YEL, CardLine, Ln, Row, button_rows, ctx_label, cwrap, draw_card, fit,
                   heading, indent, vlen, with_side)
from ..model import RouterModel, ServerData, flag
from ..state import UIState
from .common import choice_line


def router_row(m: RouterModel, w: int) -> Ln:
    """One router model: state, its setup (from the router's arguments), Load / Unload."""
    args = " ".join(m.args)
    per = flag(args, "--kv-unified-per-slot") or flag(args, "-c", "--ctx-size") or "?"
    setup = f"{flag(args, '--parallel', '-np') or '1'} × {ctx_label(int(per)) if per.isdigit() else per} " \
            f"{flag(args, '-ctk', '--cache-type-k') or ''}"
    col = {"loaded": GRN, "loading": YEL, "sleeping": CYN}.get(m.status, DIM)
    dot = {"loaded": "●", "loading": "◐", "sleeping": "◑"}.get(m.status, "○")
    state = m.status + (" (last load failed)" if m.failed else "")
    text = f"  {col}{dot}{R} {m.id:<26} {col}{state:<11}{R} {DIM}{setup:<16}{R} "
    label, act = ("[ Unload ]", f"runload:{m.id}") if m.active else ("[ Load ]", f"rload:{m.id}")
    c = vlen(text)
    return Ln(fit(text + f"{B}{CYN}{label}{R}", w), spans=[(c, c + len(label), act)])


def router_panel(ui: UIState, d: ServerData, saved: str, switches: Sequence[Tuple[str, str]], stale: Sequence[str],
                 cols: int) -> List[Row]:
    """Model switching: dashboard only (single, the default) or router mode (OpenCode / Pi
    switch models), what each means, the router's models with Load / Unload, its recent
    switches, and whether the client configs list the installed models."""
    w = cols - 2
    running = "router" if d.router is not None else "single" if d.up else ""
    ui.keys = [("click", "a mode, Load / Unload"), ("[ ]", "panels")]
    ui.keys_more = ["A change of mode applies at the next start. If a server runs, CARL asks to restart it now."]
    now = (f"{GRN}running as {running}{R}" if running == saved else
           f"{YEL}running as {running}: the change applies at the next start (CARL asks to restart now){R}"
           if running else f"{DIM}no server runs{R}")

    def main(mw: int) -> List[CardLine]:
        L: List[CardLine] = choice_line("Mode", [("single", "Dashboard only (single model)", "rmode:single"),
                                                 ("router", "OpenCode / Pi switch models (router)", "rmode:router")],
                                        saved, mw)
        L += [f"{' ' * 11}{x}" for x in cwrap(f"{DIM}saved: llama.mode = {saved} ·{R} {now}", mw - 11)]
        if d.router is not None:
            L += ["", heading("Models the router offers", mw)]
            for m in d.router.models:
                L.append(router_row(m, mw))
            if not d.router.models:
                L.append(f"{DIM}  none: the router has no models (./carl.sh fit){R}")
            if switches:
                L += ["", heading("Recent switches", mw)]
                L += [f"  {DIM}{t}{R}  {name}" for t, name in list(switches)[-5:]]
        L += ["", heading("OpenCode and Pi", mw)]
        if stale:
            L += [x for line in stale for x in cwrap(f"{YEL}⚠ {line}{R}", mw, "  ")]
        else:
            L += cwrap(f"{GRN}✓{R} {DIM}the configs on this Mac list the installed models (or OpenCode and Pi are "
                       f"not installed: see the Connect tab){R}", mw)
        return L + button_rows("", [("Update the OpenCode / Pi configs", "insconfig")], mw)

    tip = ("Click Load to start a model now. OpenCode and Pi load the model that they ask for."
           if d.router is not None else "Click a mode: dashboard only (you pick the model here) or router "
                                        "(OpenCode / Pi change the model).")
    L = with_side(main, tip, [
        ("Who changes the model", [
            f"{B}Dashboard only{R} (the default): one model runs. You or auto fit select it here, and OpenCode / Pi "
            f"use the model that runs. {B}Router{R}: the router offers all downloaded models that fit. It loads the "
            f"model that you select in OpenCode (/models) or Pi (/model), one model at a time. A switch takes 30 s "
            f"to 2 min: the old model stops and the new model loads. Router mode is for users who change models "
            f"during their work. Auto fit then selects only the first model that loads."]),
        ("Warning", [f"{YEL}WARNING: Each switch empties the prompt cache. The model that loads starts cold. OpenCode "
                     f"and Pi restore a session from the disk cache about one second after the load (Caching "
                     f"panel). Other clients read the full conversation again. For a long conversation, this takes "
                     f"minutes (see the context zones). Switch with this in consideration.{R}"]),
        ("Settings per model", [
            "Router mode gives each model the same settings as a single start of that model (config.json > "
            "Auto-tune > catalogue). It does not offer a model that does not fit. If a client asks for a model "
            "that is not installed, the client gets an error (HTTP 400) and no model loads. The OpenCode plugin "
            "shows this error. For a custom model, set the thinking field of its card (Models panel). Then "
            "OpenCode shows the correct levels."]),
        ("Load and Unload", ["Load starts a model now. The loaded model stops first. Unload releases the memory."]
         if d.router is not None else [])], w - 4, main_w=96)
    return indent(draw_card("router", "ROUTER", f"{DIM}model switching: {saved}{R}", L, w, 2))

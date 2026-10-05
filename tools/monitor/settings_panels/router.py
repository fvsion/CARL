"""The Router panel: single model (you choose the model in the dashboard) or router mode (OpenCode and
Pi switch the model), the router's models with Load / Unload, its recent switches, and whether the
OpenCode and Pi configs on this Mac list the installed models. Pure."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from ..fmt import (B, CYN, DIM, GRN, R, YEL, CardLine, Ln, Row, Section, button_rows, ctx_label, cwrap, draw_card, fit,
                   heading, indent, vlen, with_side)
from ..model import RouterModel, ServerData, flag
from ..state import UIState
from .common import choice_line

MODE_NAMES = {"single": "single model", "router": "router mode"}


def router_row(m: RouterModel, w: int, sel: bool) -> List[CardLine]:
    """One router model: its state, its setup (from the router's arguments), Load / Unload; a failed
    last load on its own line."""
    args = " ".join(m.args)
    per = flag(args, "--kv-unified-per-slot") or flag(args, "-c", "--ctx-size") or "?"
    slots = flag(args, "--parallel", "-np") or "1"
    setup = f"{slots} × {ctx_label(int(per)) if per.isdigit() else per}"
    col = {"loaded": GRN, "loading": YEL, "sleeping": CYN}.get(m.status, DIM)
    dot = {"loaded": "●", "loading": "◐", "sleeping": "◑"}.get(m.status, "○")
    pre = f"{CYN}{B}›{R} " if sel else "  "
    text = f"{pre}{col}{dot}{R} {fit(m.id, 26)} {col}{m.status:<10}{R} {DIM}{setup:<12}{R} "
    label, act = ("[ Unload ]", f"runload:{m.id}") if m.active else ("[ Load ]", f"rload:{m.id}")
    c = vlen(text)
    out: List[CardLine] = [Ln(fit(text + f"{B}{CYN}{label}{R}", w), spans=[(c, c + len(label), act)])]
    if m.failed:
        out.append(f"      {YEL}Its last load failed: the Log tab tells why.{R}")
    return out


def router_panel(ui: UIState, d: ServerData, saved: str, switches: Sequence[Tuple[str, str]], stale: Sequence[str],
                 cols: int, here: bool = False) -> List[Row]:
    """The mode (saved and running), what each means, the router's models with Load / Unload, its recent
    switches, and the OpenCode and Pi configs on this Mac."""
    w = cols - 2
    running = "router" if d.router is not None else "single" if d.up else ""
    ui.keys = [("s", "single model"), ("r", "router mode"), ("u", "update the configs"), ("[ ]", "panels")]
    if d.router is not None:
        ui.keys[2:2] = [("↑↓", "model"), ("Enter", "load / unload")]
    ui.keys_more = ["s and r ask first. When a server runs, CARL restarts it in the new mode.",
                    "u runs the installer for this Mac: the Connect tab shows its output."]
    if running == saved:
        now = f"{GRN}The server runs in {MODE_NAMES[running]} now.{R}"
    elif running:
        now = (f"{YEL}Saved for the next start: {MODE_NAMES.get(saved, saved)}. The server runs in "
               f"{MODE_NAMES[running]} now.{R}")
    else:
        now = f"{DIM}Saved for the next start: {MODE_NAMES.get(saved, saved)}. No server runs.{R}"

    def main(mw: int) -> List[CardLine]:
        L: List[CardLine] = [*cwrap(f"{DIM}Who changes the model: you in the dashboard, or OpenCode and Pi.{R}", mw), ""]
        L += choice_line("Mode", [("single", "Single model (s)", "rmode:single"),
                                  ("router", "Router mode: OpenCode and Pi switch (r)", "rmode:router")], saved, mw)
        L += [f"{' ' * 11}{x}" for x in cwrap(now + (f" {DIM}(llama.mode = {saved}){R}" if ui.full else ""), mw - 11)]
        if d.router is not None:
            L += ["", heading("Models the router offers", mw)]
            models = d.router.models
            ui.router_row = max(min(ui.router_row, len(models) - 1), 0)
            for i, m in enumerate(models):
                L += router_row(m, mw, i == ui.router_row)
            if not models:
                L.append(f"{DIM}  None: no downloaded model fits (./carl.sh fit tells why).{R}")
            if switches:
                L += ["", heading("Recent switches", mw)]
                L += [f"  {DIM}{t[:5]}{R}  loaded {name}" for t, name in list(switches)[-5:]]
        L += ["", heading("OpenCode and Pi on this Mac", mw)]
        if stale:
            L += [x for line in stale for x in cwrap(f"{YEL}⚠ {line}{R}", mw, "  ")]
        elif here:
            L += cwrap(f"{GRN}✓{R} Their configs list the installed models.", mw)
        else:
            L += cwrap(f"{DIM}They are not set up on this Mac for this server (the Connect tab).{R}", mw)
        return L + button_rows("", [("Update the OpenCode and Pi configs (u)", "insconfig")], mw)

    tip = ("Press Enter to load the selected model now. OpenCode and Pi load the model that they ask for."
           if d.router is not None else "Press s or r to choose who changes the model.")
    sections: List[Section] = [
        ("Who changes the model", [
            f"{B}Single model{R} (the default): one model runs. You or Auto fit choose it in the Server panel. OpenCode "
            f"and Pi use the model that runs. {B}Router mode{R}: the router offers all downloaded models that fit. It "
            f"loads the model that you choose in OpenCode (/models) or Pi (/model), one model at a time. A switch takes "
            f"30 s to 2 min: the old model stops and the new model loads. Auto fit then chooses only the first model."]),
        ("Each switch costs time", [f"{YEL}After a switch, the new model has none of the sessions in memory. OpenCode "
                                    f"and Pi restore a session from the disk cache about one second after the load "
                                    f"(the Caching panel). Other clients read the whole session again: minutes for a "
                                    f"long session.{R}"]),
        ("Settings per model", [
            "Router mode gives each model the same settings as a single start of that model. It does not offer a "
            "model that does not fit."
            + (" If a client asks for a model that is not installed, the client gets an error (HTTP 400) and no model "
               "loads. The OpenCode plugin shows this error. For a custom model, set the thinking field of its card "
               "(the Models panel): OpenCode then shows the correct levels." if ui.full else "")]),
        ("Load and Unload", ["Load starts a model now. The loaded model stops first. Unload frees the memory."]
         if d.router is not None else [])]
    L = with_side(main, tip, sections, w - 4, main_w=96)
    return indent(draw_card("router", "ROUTER", f"{DIM}{MODE_NAMES.get(saved, saved)}{R}", L, w, 1))

"""The dashboard's main loop: one snapshot printed (--once), or the interactive dashboard until quit. The terminal is
reached only through the Terminal port (the composition builds it, and only here: --once never needs termios). The
loop waits on the port's read_key, which a key, Ctrl-C (the port's wake) or the next refresh ends."""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, List, Optional, Tuple

from ..ui.fmt import DIM, GRN, R, YEL, CardLine, draw_card
from ..ui.screen import logo_escape, place_lines
from ..ui.words import computer
from .keys import InputBuffer

if TYPE_CHECKING:
    from ..ports import Terminal
    from .app import App


def run(app: App) -> None:
    """Print one snapshot (--once), or run the interactive dashboard until quit."""
    c, ui = app.collector, app.ui
    threading.Thread(target=c.slow_loop, daemon=True).start()
    threading.Thread(target=c.load_gpu_limit, daemon=True).start()
    if app.opts.once:
        app.ctl.data = c.collect()
        time.sleep(0.5)
        if app.opts.tab:
            ui.tab = app.opts.tab - 1
        print("\n".join(app.frame(app.ctl.data)))
        return
    term = app.host.terminal()
    app.size = term.size

    def interrupted() -> None:                  # Ctrl-C opens the quit dialog (the port wakes the loop)
        ui.quit = True

    term.enter(interrupted)
    buf = InputBuffer()
    early: List[str] = []                       # input read while the end of a split key sequence was awaited

    def more_coming() -> bool:
        more = term.read_key(0.05)
        if more:
            early.append(more)
        return bool(more)

    try:
        warm_up(app, term)
        next_fetch = time.time() + app.opts.interval
        logo_size: Optional[Tuple[int, int]] = None
        logo_t = 0.0
        while True:
            if time.time() >= next_fetch:
                app.ctl.data = c.collect()
                next_fetch = time.time() + (0.5 if ui.stopping else app.opts.interval)
            app.jobs.poll()
            app.jobs.drain_tick(app.ctl.data)
            app.drain_events()
            app._status = {**c.tail.book.status(), "up": app.ctl.data.up}   # a new dict: the API reads it whole
            app.cache_api()
            if ui.stopping:
                pid, deadline = ui.stopping
                if not app.watch.alive(pid):
                    ui.exit_msg = f"Server (pid {pid}) stopped."
                    break
                if time.time() > deadline:
                    app.watch.kill(pid)
            tsize = term.size()
            if app.logo and app.logo_kind and (tsize != logo_size or time.time() - logo_t > 30):
                term.write(("\x1b[2J" if tsize != logo_size else "") + logo_escape(app.logo_kind, app.logo))
                logo_size, logo_t = tsize, time.time()
            term.write(place_lines(app.frame(app.ctl.data), app.logo_cols))
            got = early.pop(0) if early else term.read_key(max(min(next_fetch - time.time(), 1.0), 0.05))
            if got:
                data = buf.feed(got, more_coming)
                if data and app.ctl.handle_input(data):
                    next_fetch = 0
    except SystemExit:
        pass
    finally:
        term.leave()
        if ui.exit_msg:
            print(ui.exit_msg)


def warm_up(app: App, term: Terminal) -> None:
    """The first start: the server state, the model list and what fits read once, with a progress line
    for each step (the GGUF headers and a Hugging Face read can take some seconds)."""
    cols, rows = term.size()

    def show(done: int) -> None:
        term.write(place_lines(progress_lines(done, cols, rows), 0))

    def models() -> None:
        app.svc.models.get()

    def fits() -> None:
        p = app.ensure_pending(app.ctl.data)
        app.svc.fit_cached(p)

    for i, step in enumerate((lambda: setattr(app.ctl, "data", app.collector.collect()), models, fits)):
        show(i)
        try:
            step()
        except Exception:       # a broken catalogue or config: the screens say why
            pass
    show(len(progress_steps()))


def progress_steps() -> Tuple[str, ...]:
    return ("Reading the server state", "Reading the model list", f"Checking what fits {computer()}")


def progress_lines(done: int, cols: int, rows: int) -> List[str]:
    """The first start's progress: each step with ✓ (done) or … (now), centred."""
    w = min(60, cols - 4)
    L: List[CardLine] = ["", *[(f"{GRN}✓{R} " if i < done else f"{YEL}…{R} " if i == done else "  ") + step
                               for i, step in enumerate(progress_steps())], ""]
    card = draw_card("start", "CARL STARTS", f"{DIM}the dashboard reads what it shows{R}", L, w, 1)
    top = max((rows - len(card)) // 2, 0)
    pad = " " * max((cols - w) // 2, 0)
    return [""] * top + [pad + t for t, _ in card]

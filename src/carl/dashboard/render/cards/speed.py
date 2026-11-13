"""The SPEED card: the live and average speeds and the speculation table.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import List, Mapping, Sequence, Tuple

from ....domain.server.snapshot import ServerData, flag
from ....domain.shared.units import duration, percent, speed, tokens
from ....ui.fmt import (DIM, R, Card, CardLine, row)
from ....ui.words import spec_name
from .view import View

AVG_MIN_S = 0.5         # seconds of reading or writing before an average shows


def averages(d: ServerData) -> Tuple[float, float]:
    """(read, write) tok/s since the server started (0: not enough time behind it: one 1-token
    answer measures ~1 µs, 1,000,000 tok/s)."""
    m = d.metrics
    pps, tgs = m.get("prompt_seconds_total", 0), m.get("tokens_predicted_seconds_total", 0)
    pp_avg = m.get("prompt_tokens_total", 0) / pps if pps >= AVG_MIN_S else 0
    tg_avg = m.get("tokens_predicted_total", 0) / tgs if tgs >= AVG_MIN_S else 0
    return pp_avg, tg_avg


def _num(x: float, decimals: int = 0) -> str:
    return "–" if not x else f"{x:,.{decimals}f}"


def per_step(mode: str, n: int, pos: Mapping[int, float], steps: float, acc: float) -> Tuple[str, List[str], List[str]]:
    """The speed card's Per step table (Phase 23.5.3.1, from mock-ups): its label, header cells and value cells. MTP: a
    column per guess (the share of the steps that kept it). n-gram: the steps that kept more than MTP's guesses (only
    an n-gram run can), and the tokens such a step kept (tkn/run). n-gram alone counts per draft: every step is one.
    Then the tokens accepted a step. pos: llama.cpp's accepted tokens by position (from 0), over all steps."""
    mtp, ngram = "draft-mtp" in mode, "ngram-mod" in mode
    k = max(n, 1) if mtp else 0
    heads: List[str] = [str(i) for i in range(1, k + 1)]
    vals = [percent(pos.get(i - 1, 0) / steps) for i in range(1, k + 1)]
    if ngram:
        runs = pos.get(k, 0)                          # the steps that kept more than MTP's k guesses
        tail = sum(c for i, c in pos.items() if i >= k)
        heads += ["n-gram", "tkn/run"]
        vals += [percent(runs / steps), f"{k + tail / runs:.0f}" if runs else "–"]
    heads.append("tokens acpt")
    per = acc / steps
    vals.append(f"{per:.2f}" if per < 10 else f"{per:.1f}")
    return ("Per draft" if ngram and not mtp else "Per step"), heads, vals


def step_table(heads: Sequence[str], vals: Sequence[str]) -> Tuple[str, str]:
    """The header and value rows: MTP's guesses 6 wide; n-gram and tkn/run close, split by " | " (the n-gram share
    right-aligned against it); tokens acpt centred under its header."""
    h = v = ""
    i = 0
    while i < len(heads):
        if heads[i] == "n-gram":
            h += f"{'n-gram':<6} | {'tkn/run':<7}   "
            v += f"{vals[i]:>6} | {vals[i + 1]:<7}   "
            i += 2
        elif heads[i] == "tokens acpt":
            h += heads[i]
            v += f"{vals[i]:^11}"
            i += 1
        else:
            h += f"{heads[i]:<6}"
            v += f"{vals[i]:<6}"
            i += 1
    return h.rstrip(), v.rstrip()


def card_speed(v: View, d: ServerData, w: int = 66) -> Card:
    """A table: read and write tok/s now, on average and in the last request; the share of guesses accepted. At full
    also the speculation with that share at its right, the Per step table, the last request's time, the totals (two
    rows) and the queue (Phase 23.5.3.1)."""
    m = d.metrics
    pp_avg, tg_avg = averages(d)
    writing = d.busy and d.decoded > 0
    reading = d.busy and d.decoded == 0
    r = v.log.last_answer()
    live_pp, live_tg = v.log.live()         # the log's progress lines: every slot, whether or not a poll answered
    pp_now = live_pp or (d.pp_rate if reading else 0)
    tg_now = live_tg or (d.tg_rate if writing else 0)
    # the tables' label column lines up with the rows below them (aligned(): the longest label + 2)
    lw = len("last request" if v.full else "guesses ACPT") + 2
    inner = w - 4
    last = "Last" if inner < 50 else "Last request"            # a narrow card: the short header
    lc = len(last) + 3
    head = f"{DIM}{'Tok/s':<{lw}}{'Now':>7}{'Average':>10}{last:>{lc}}{R}"
    L: List[CardLine] = [
        head,
        f"{DIM}{'Read':<{lw}}{R}{_num(pp_now or 0):>7}{_num(pp_avg):>10}{_num(r.pp if r else 0):>{lc}}",
        f"{DIM}{'Write':<{lw}}{R}{_num(tg_now or 0, 1):>7}{_num(tg_avg, 1):>10}"
        f"{_num(r.tg if r else 0, 1):>{lc}}"]
    drafted = m.get("spec_decode_num_draft_tokens_total", 0)
    acc = m.get("spec_decode_num_accepted_tokens_total", 0)
    mode = flag(d.cmd, "--spec-type", default="none")
    share = percent(acc / drafted) if drafted else ("–" if mode != "none" else "")
    if not v.full:
        L.append(row("guesses ACPT", share if drafted else ("off" if mode == "none" else f"{DIM}no guesses yet{R}")))
        return Card("SPEED", f"write {speed(tg_avg)}" if tg_avg else "", L)
    L.append("")
    lab = (lambda name, value: f"{DIM}{name:<{lw}}{R}{value}")     # full detail: every row in the tables' column
    n = flag(d.cmd, "--spec-draft-n-max", default="1")
    name = spec_name(mode, n)
    right = f"Guesses ACPT {share}" if mode != "none" else ""
    gap = inner - lw - len(name) - len(right)
    if right and gap >= 2:
        L.append(f"{DIM}{'Speculation':<{lw}}{R}{name}{' ' * gap}{DIM}Guesses ACPT{R} {share}")
    else:
        L.append(lab("Speculation", name))
        if right:
            L.append(lab("Guesses ACPT", share))
    if mode != "none":
        steps = m.get("spec_decode_num_drafts_total", 0)
        if steps:
            label, heads, vals = per_step(mode, int(n) if str(n).isdigit() else 1, d.accepted_by_pos, steps, acc)
            h, val = step_table(heads, vals)
            L.append(f"{DIM}{label:<{lw}}{h}{R}")
            L.append(" " * lw + val)
        else:
            L.append(lab("Per step", f"{DIM}no steps yet{R}"))
    if r:
        took = (r.t1 or 0) - (r.t0 or 0)
        L.append(lab("Last request", duration(took)))
    if d.slots:
        L.append("")
        L.append(f"{DIM}{'Totals':<{lw}}{'read':<9}{'reused':<9}written{R}")
        L.append(" " * lw + f"{tokens(m.get('prompt_tokens_total', 0)):<9}"
                 f"{tokens(m.get('prompt_tokens_cached_total', 0)):<9}{tokens(m.get('tokens_predicted_total', 0))}")
        L.append(lab("Waiting", f"{m.get('requests_deferred', 0):.0f}"))
    return Card("SPEED", f"write {speed(tg_avg)}" if tg_avg else "", L)

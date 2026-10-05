"""The Server panel's MODEL card: what the selected model is for. Simple detail: its role, what it is
good for, its speed on this Mac, thinking and its trade-off. Full detail adds why to use it, the
quality rank, the context zones, the alternatives, what "uncensored" means, the reason for the
selected setting, Auto-tune's measurements, its source and file."""
from __future__ import annotations

import time
from typing import List

from carl_core.domain.units import tokens

from ..fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Row, cwrap, draw_card, home_short, wwrap
from ..model import JSONDict, ModelInfo, jdict
from ..settings import LLAMA_ADV, Pending, SettingsService
from ..words import THINKING_NAMES
from .common import good_for_chip, parallel_text, spec_table

WHY_KEY = {"specn": "spec", "presence": "temp", "top_k": "temp", "top_p": "temp", "min_p": "temp", "repeat": "temp"}
CTX_FLOOR = 98304                                       # the fast zone reaches at least this (carl_core.domain.models)


def short_date(iso: object) -> str:
    """'2026-10-03' as '3 Oct' (as it is when it can't be read)."""
    try:
        t = time.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso)
    return f"{t.tm_mday} {time.strftime('%b', t)}"


def speed_line(m: ModelInfo, tune: object) -> str:
    """This Mac's measured speeds after Auto-tune, else the catalogue's (another Mac)."""
    t = jdict(tune)
    st = jdict(t.get("settings"))
    best = jdict(jdict(jdict(t.get("results")).get("speculation")).get(f"{st.get('spec')}:{st.get('spec_n')}"))
    if best.get("prose") is not None:
        return (f"Speed on this Mac: {GRN}{best.get('prose'):.0f} tok/s prose · {best.get('code'):.0f} code · "
                f"{best.get('edit'):.0f} edit{R} (Auto-tune, {short_date(t.get('date'))}).")
    sp = jdict(m.get("speed"))
    if sp.get("prose") is not None:
        return (f"Speed on another Mac ({sp.get('machine', '?')}): {sp.get('prose'):.0f} tok/s prose · "
                f"{sp.get('code'):.0f} code · {sp.get('edit'):.0f} edit. {DIM}Auto-tune measures this Mac.{R}")
    return f"Speed: {DIM}not measured. Auto-tune measures it on this Mac.{R}"


def tune_table(tune: object) -> List[CardLine]:
    """Auto-tune's speculation table, the chosen mode marked."""
    t = jdict(tune)
    res = jdict(jdict(t.get("results")).get("speculation"))
    if not res:
        return []
    st = jdict(t.get("settings"))
    head: List[CardLine] = ["", f"{B}Auto-tune on this Mac{R} {DIM}{short_date(t.get('date'))} · {t.get('machine', '?')}{R}"]
    return head + spec_table(res, f"{st.get('spec')}:{st.get('spec_n')}")


def zones_line(svc: SettingsService, m: ModelInfo, tune: JSONDict) -> str:
    """The context zones of this model on this Mac, in words."""
    zg, zs, _ = svc.store.ctx_zones(m)
    measured = jdict(tune.get("ctx_zones"))
    raw = measured.get("good")
    floor = (f" {DIM}(CARL's floor: Auto-tune measured {tokens(int(raw))}){R}"
             if isinstance(raw, int) and raw < CTX_FLOOR <= zg else "")
    src = "measured on this Mac" if measured else "from the catalogue"
    return (f"Context zones ({src}): {GRN}up to {tokens(zg)} fast{R}{floor} · {YEL}up to {tokens(zs)} slow{R} · "
            f"{RED}more is very slow{R} (the time to read a full context again).")


class ModelCard:
    """Draws the MODEL card of the Server panel."""

    def __init__(self, svc: SettingsService, home: str) -> None:
        self.svc = svc
        self.home = home

    def draw(self, p: Pending, key: str, w: int, lvl: int = 1, full: bool = False) -> List[Row]:
        """The card of the model a start uses (auto: Auto fit's choice as resolved now)."""
        svc = self.svc
        name = svc.resolved_model(p)
        m = svc.models.by_name(name)
        if not m:
            return draw_card("modelinfo", "MODEL", "", [f"{RED}{name} is not a known model.{R}"], w, lvl)
        tune = jdict(jdict(m.get("local")).get("tune"))
        tw = w - 4
        L: List[CardLine] = []
        role = str(m.get("role") or "")
        good = [str(t) for t in (m.get("good_for") or [])]
        if p.get("model") == "auto":
            L += cwrap(f"{DIM}auto starts {name} now (Auto fit's choice, or the best downloaded model while Auto fit's "
                       f"choice is not downloaded).{R}", tw)
        first = (f"{role}." if role else str(m.get("summary") or "")) + (
            f"   Good for: {' · '.join(good_for_chip(t) for t in good)}" if good else "")
        if first.strip():
            L += cwrap(first, tw)
        think = THINKING_NAMES.get(str(m.get("thinking") or ""), "")
        L += cwrap(speed_line(m, tune) + (f"   Thinking: {think}." if think else ""), tw)
        if m.get("trade_offs"):
            L += cwrap(f"{YEL}Trade-off:{R} {m['trade_offs']}", tw, "  ")
        if m.get("custom") and not jdict(m.get("local")).get("card"):
            L += cwrap(f"{DIM}This model has no card. To write one: the Models panel (]), select it, then e. Or run "
                       f"./carl.sh card {name}.{R}", tw)
        if full:
            L += self.full_lines(m, key, tune, tw)
        title = f"{B}{m.get('label') or name}{R}"
        if m.get("custom"):
            title += f" {DIM}· custom model, {'your card' if jdict(m.get('local')).get('card') else 'no card'}{R}"
        return draw_card("modelinfo", "MODEL", title, L, w, lvl)

    def full_lines(self, m: ModelInfo, key: str, tune: JSONDict, tw: int) -> List[CardLine]:
        svc = self.svc
        L: List[CardLine] = [""]
        if m.get("why_use"):
            L += cwrap(f"{B}Why use it:{R} {m['why_use']}", tw, "  ")
        tags = [x for x in (str(m.get("arch") or "").replace("moe", "MoE"), str(m.get("quant") or ""),
                            "MTP head" if m.get("mtp") else "MTP drafter" if m.get("draft") else "",
                            f"{RED}abliterated{R}" if m.get("abliterated") else "") if x]
        if isinstance(m.get("rank"), int):
            how = ("from your card, not measured" if m.get("custom") else
                   "from published benchmarks and CARL's code test")
            L += cwrap(f"Quality rank {m['rank']} (1 = best; {how}).   " + " · ".join(tags), tw)
        elif tags:
            L += cwrap(" · ".join(tags), tw)
        L += cwrap(zones_line(svc, m, tune), tw, "  ")
        par = jdict(tune.get("results")).get("parallel") or []
        if par:
            L += cwrap(f"Slots at work: {parallel_text(par)} (measured on this Mac).", tw, "  ")
        alts = [jdict(a) for a in (m.get("pick_instead") or [])]
        if alts:
            L += cwrap("Pick instead: " + " · ".join(f"{CYN}{a.get('model')}{R} {a.get('when')}" for a in alts), tw, "  ")
        if m.get("uncensored"):
            L += cwrap(f"{RED}Uncensored:{R} {m['uncensored']}", tw, "  ")
        if m.get("hardware"):
            L += cwrap(f"Hardware: {m['hardware']}", tw, "  ")
        labels = {r.key: r.label for r in svc.schema.llama + LLAMA_ADV}
        wk = WHY_KEY.get(key, key)
        why = str(jdict(m.get("why")).get(wk) or "")
        if why and not (wk == "spec" and tune):
            L += ["", f"{B}Why this {labels.get(key, key).lower()}{R}", *wwrap(why, tw)[:8]]
        L += tune_table(tune)
        desc = wwrap(m.get("description", ""), tw)
        if desc and desc != [""]:
            L += ["", *[f"{DIM}{x}{R}" for x in desc]]
        hf = jdict(m.get("hf"))
        if hf.get("repo"):
            L += cwrap(f"{DIM}Source: huggingface.co/{hf['repo']} · {hf.get('file')}"
                       + (f" @ {str(hf.get('revision'))[:8]}" if hf.get("revision") else "") + R, tw, "  ")
        L += cwrap(f"{DIM}File: {home_short(str(m.get('path', '')), self.home)}{R}", tw, "  ")
        return L


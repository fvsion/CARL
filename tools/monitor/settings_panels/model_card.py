"""The Server panel's MODEL card: what the selected model is good for (its tags: the user, 2026-10-08, "for and good
for are redundant, we only need the tags"), thinking, its trade-off, why to use it and its speeds by source. The
full level adds its type, quantization, quality rank, context zones, the alternatives, what "uncensored" means, the
reason for the selected setting, Auto-tune's measurements, its source and file."""
from __future__ import annotations

import time
from typing import List

from carl_core.domain.units import tokens

from ..cards import wrapped
from ..fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Row, aligned, draw_card, home_short, row
from ..model import JSONDict, ModelInfo, jdict
from ..settings import LLAMA_ADV, Pending, SettingsService
from ..words import THINKING_NAMES
from .common import good_for_chip, parallel_text, spec_table, speeds_table

WHY_KEY = {"specn": "spec", "presence": "temp", "top_k": "temp", "top_p": "temp", "min_p": "temp", "repeat": "temp"}
CTX_FLOOR = 98304                                       # the fast zone reaches at least this (carl_core.domain.models)


def short_date(iso: object) -> str:
    """'2026-10-03' as '3 Oct' (as it is when it can't be read)."""
    try:
        t = time.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso)
    return f"{t.tm_mday} {time.strftime('%b', t)}"


def tune_table(tune: object) -> List[CardLine]:
    """Auto-tune's speculation table, the chosen mode marked."""
    t = jdict(tune)
    res = jdict(jdict(t.get("results")).get("speculation"))
    if not res:
        return []
    st = jdict(t.get("settings"))
    head: List[CardLine] = ["", f"{B}Auto-tune on this Mac{R}   {DIM}{short_date(t.get('date'))}, {t.get('machine', '?')}{R}"]
    return head + spec_table(res, f"{st.get('spec')}:{st.get('spec_n')}")


class ModelCard:
    """Draws the MODEL card of the Server panel: one reading per row."""

    def __init__(self, svc: SettingsService, home: str) -> None:
        self.svc = svc
        self.home = home

    def draw(self, p: Pending, key: str, w: int, lvl: int = 1, full: bool = False, sel: bool = False) -> List[Row]:
        """The card of the model a start uses (auto: Auto fit's choice as resolved now). lvl: 0 collapsed, 1 simple,
        2 full (its title shows it)."""
        svc = self.svc
        name = svc.resolved_model(p)
        m = svc.models.by_name(name)
        if not m:
            return draw_card("modelinfo", "MODEL", "", [f"{RED}{name} is not a known model.{R}"], w, lvl, 3, sel)
        tune = jdict(jdict(m.get("local")).get("tune"))
        tw = w - 4
        L: List[CardLine] = []
        if p.get("model") == "auto":
            L.append(row("auto", f"Starts {name} now."))
        good = [str(t) for t in (m.get("good_for") or [])]           # the tags say what it is for (user, 2026-10-08)
        if good:
            L.append(row("good for", ", ".join(good_for_chip(t) for t in good)))
        think = THINKING_NAMES.get(str(m.get("thinking") or ""), "")
        if think:
            L.append(row("thinking", think))
        if m.get("trade_offs"):
            L.append(row("trade-off", f"{YEL}{m['trade_offs']}{R}"))
        if m.get("why_use"):
            L.append(row("why use it", str(m["why_use"])))
        L += ["", *speeds_table(m, tw)]
        if m.get("custom") and not jdict(m.get("local")).get("card"):
            L += ["", f"{DIM}No card yet: the Models panel (]), select it, then e.{R}"]
        if full:
            L += self.full_lines(m, key, tune, tw)
        title = f"{B}{str(m.get('label') or name).replace(' · ', '  ')}{R}"
        if m.get("custom"):
            title += f"   {DIM}custom model, {'your card' if jdict(m.get('local')).get('card') else 'no card'}{R}"
        return draw_card("modelinfo", "MODEL", title, wrapped(aligned(L, tw), tw), w, lvl, 3, sel)

    def full_lines(self, m: ModelInfo, key: str, tune: JSONDict, tw: int) -> List[CardLine]:
        svc = self.svc
        L: List[CardLine] = [""]
        arch = str(m.get("arch") or "")
        if arch:
            L.append(row("type", "MoE" if arch == "moe" else arch))
        if m.get("quant"):
            L.append(row("quant", str(m["quant"])))
        if m.get("mtp") or m.get("draft"):
            L.append(row("MTP", "an MTP head" if m.get("mtp") else "an MTP drafter"))
        if m.get("abliterated"):
            L.append(row("abliterated", f"{RED}yes{R}"))
        if isinstance(m.get("rank"), int):
            L.append(row("quality", f"Rank {m['rank']} (1 = best)"))
        zg, zs, _ = svc.store.ctx_zones(m)
        L.append(row("context", f"{GRN}Fast up to {tokens(zg)}{R}, {YEL}slow up to {tokens(zs)}{R}, {RED}more is very "
                                f"slow{R}."))
        par = jdict(tune.get("results")).get("parallel") or []
        if par:
            L.append(row("slots at work", parallel_text(par)))
        for i, a in enumerate(jdict(x) for x in (m.get("pick_instead") or [])):
            L.append(row("pick instead" if i == 0 else "", f"{CYN}{a.get('model')}{R}   {a.get('when')}"))
        if m.get("uncensored"):
            L.append(row("uncensored", f"{RED}{m['uncensored']}{R}"))
        if m.get("hardware"):
            L.append(row("hardware", str(m["hardware"])))
        labels = {r.key: r.label for r in svc.schema.llama + LLAMA_ADV}
        wk = WHY_KEY.get(key, key)
        why = str(jdict(m.get("why")).get(wk) or "")
        if why and not (wk == "spec" and tune):
            L.append(row(f"why this {labels.get(key, key).lower()}", why))
        L += tune_table(tune)
        if m.get("description"):
            L += ["", f"{DIM}{m['description']}{R}"]
        hf = jdict(m.get("hf"))
        L.append("")
        if hf.get("repo"):
            L.append(row("source", f"huggingface.co/{hf['repo']}   {hf.get('file')}"
                         + (f" @ {str(hf.get('revision'))[:8]}" if hf.get("revision") else "")))
        L.append(row("file", home_short(str(m.get("path", "")), self.home)))
        return L

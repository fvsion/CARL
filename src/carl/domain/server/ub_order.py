"""The -ub of a start: the one order every reader follows (decision A51; rule R14). Pure.

The order, first match wins:
  typed    a -ub the user typed: in the server options (llama.extra_args) or on carl's own command line. As typed.
  kept     the model's own saved choice (config.json models.NAME.ub), while it stands: no tune record yet, or it was
           made against the model's current record (models.NAME.ub_tune), or it is the tuned -ub.
  tuned    the model's Auto-tune record (its -ub, with A45's context-first choice in it).
  config   llama.ub (a global for every model: how config.json held it before A51), for a model without a tune record.
  default  this computer's -ub (2048 on a PC, 512 on a Mac), which a start without a tune record may still replace by
           the largest -ub that keeps the setup, n-gram on (ub_pick: 2048 where it fits, else 1024, else 512;
           W1.6).

A config value (llama.ub, or a kept choice a later tune has outdated) that differs from the tuned -ub is never applied
silently over the record: UbAsk says what to ask. A start that can ask (the dashboard, `carl start` in a terminal) asks
once, in one prompt; one that cannot (router mode, scripts, the clients, any start without a terminal) uses the tuned
-ub and says so in one line (UbOrder.note). The answer is a change of config.json (answer_config): Use tuned removes
the config value; Keep saves it as that one model's choice, asked again only after the model's next tune.

The start's variables (launch_plan.py: UB, UB_AUTO, UB_ASK, UB_STAMP) carry the order's result; fit.py's UbChoice then
lowers a tuned or default -ub where the setup would cost context or a slot (A45).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from ..shared.constants import UB_CANDIDATES
from ..shared.types import JsonObject

TYPED, KEPT, TUNED, CONFIG, DEFAULT = "typed", "kept", "tuned", "config", "default"
CHANGED = "changed"                         # a UbAsk only: the dashboard's Batch size row changed from the tuned -ub
FIXED = (TYPED, KEPT, CONFIG)               # the -ub is a person's: the start does not lower it
USE_TUNED, KEEP = "tuned", "keep"           # the two answers


@dataclass(frozen=True)
class UbAsk:
    """A config value against the model's tuned -ub. where: config (llama.ub), kept (the model's earlier choice) or
    changed (the dashboard's Batch size row, just changed); mine, tuned: the two sizes; stamp: the tune record's (inventory.tune_stamp); tuned_read, mine_read: their prompt
    reading speeds in the record's batch size step (0: not measured)."""
    where: str
    mine: int
    tuned: int
    stamp: str
    tuned_read: float = 0.0
    mine_read: float = 0.0

    def sets(self) -> str:
        """Your config sets -ub 512 / You kept -ub 512 for this model / You chose -ub 512 for this model."""
        return (f"Your config sets -ub {self.mine}" if self.where == CONFIG
                else f"You chose -ub {self.mine} for this model" if self.where == CHANGED
                else f"You kept -ub {self.mine} for this model")

    def evidence(self, model: str) -> str:
        """Auto-tune found 2048 best for the A3B (prompt 980 against 375 tok/s)."""
        speeds = (f" (prompt {self.tuned_read:.0f} against {self.mine_read:.0f} tok/s)"
                  if self.tuned_read > 0 and self.mine_read > 0 else "")
        return f"Auto-tune found {self.tuned} best for {model}{speeds}"

    def question(self, model: str) -> str:
        """The prompt of a terminal: both sizes, the evidence and the two answers."""
        return (f"{self.sets()}. {self.evidence(model)}. Use tuned ({self.tuned}) or keep {self.mine} for this model? "
                f"[T/k] ")

    def buttons(self) -> Tuple[str, str]:
        """The two answers as the dashboard names them: [Use tuned] [Keep 512 for this model]."""
        return "Use tuned", f"Keep {self.mine} for this model"

    def ignored(self, ub: int = 0) -> str:
        """What a start that cannot ask says: -ub 2048 (tuned); config's 512 ignored. ub: the -ub the start runs (the
        plan may have lowered the tuned one)."""
        who = "config's" if self.where == CONFIG else "your saved"
        return f"-ub {ub or self.tuned} (tuned); {who} {self.mine} ignored"

    def to_var(self) -> str:
        """The start's variable (UB_ASK): where, mine, tuned, then the two reading speeds when known."""
        reads = f" {self.tuned_read:.0f} {self.mine_read:.0f}" if self.tuned_read > 0 and self.mine_read > 0 else ""
        return f"{self.where} {self.mine} {self.tuned}{reads}"

    @staticmethod
    def from_var(text: str, stamp: str = "") -> Optional["UbAsk"]:
        """The ask a UB_ASK variable holds (None for a value that is not one)."""
        w = text.split()
        if len(w) not in (3, 5) or w[0] not in (CONFIG, KEPT) or not all(x.isdigit() for x in w[1:]):
            return None
        reads = (float(w[3]), float(w[4])) if len(w) == 5 else (0.0, 0.0)
        return UbAsk(w[0], int(w[1]), int(w[2]), stamp, reads[0], reads[1])


@dataclass(frozen=True)
class UbOrder:
    """The order's answer. ub: the -ub; source: which step gave it; ask: a config value to ask about (the tuned -ub is
    `ub` meanwhile); note: the one line of a start that cannot ask ("" none). tuned, stamp, reads: the model's tune
    record (0, "", () without one): its -ub, its stamp and its batch size step, for a reader that asks about a new
    choice (the dashboard's Batch size row)."""
    ub: int
    source: str
    ask: Optional[UbAsk] = None
    tuned: int = 0
    stamp: str = ""
    reads: Tuple[Tuple[float, ...], ...] = ()

    @property
    def fixed(self) -> bool:
        """The -ub is a person's choice, which a start keeps as it is."""
        return self.source in FIXED

    @property
    def note(self) -> str:
        return self.ask.ignored() if self.ask else ""


def reads_of(by_ub: Sequence[Sequence[float]], mine: int, tuned: int) -> Tuple[float, float]:
    """(the prompt reading speed at the tuned -ub, at mine) from a record's batch size step ([-ub, read tok/s, write
    tok/s] rows); (0, 0) unless both were measured."""
    speeds = {int(r[0]): float(r[1]) for r in by_ub if len(r) >= 2}
    return (speeds[tuned], speeds[mine]) if tuned in speeds and mine in speeds else (0.0, 0.0)


def ub_order(typed: int, kept: int, kept_for: str, config: int, tuned: int, stamp: str, default: int,
             by_ub: Sequence[Sequence[float]] = ()) -> UbOrder:
    """The -ub of a start (module docstring). typed, kept, config, tuned: sizes (0: none); kept_for: the tune stamp
    the kept choice was made against; stamp: the model's tune record's now ("" without a record); default: this
    computer's; by_ub: the record's batch size step."""
    rec = {"tuned": tuned, "stamp": stamp, "reads": tuple(tuple(float(x) for x in r) for r in by_ub)}
    if typed:
        return UbOrder(typed, TYPED, **rec)  # type: ignore[arg-type]
    if kept and (not tuned or kept == tuned or kept_for == stamp):
        return UbOrder(kept, KEPT, **rec)  # type: ignore[arg-type]
    mine, where = (kept, KEPT) if kept else (config, CONFIG)
    if not tuned:
        return UbOrder(mine, CONFIG, **rec) if mine else UbOrder(default, DEFAULT, **rec)  # type: ignore[arg-type]
    if mine and mine != tuned:
        t, m = reads_of(by_ub, mine, tuned)
        return UbOrder(tuned, TUNED, UbAsk(where, mine, tuned, stamp, t, m), **rec)  # type: ignore[arg-type]
    return UbOrder(tuned, TUNED, **rec)  # type: ignore[arg-type]


def ub_pick(default: int, ram_layers: Callable[[int], Tuple[object, int]]) -> int:
    """The -ub of a start without a tune record: of the sizes up to this computer's default, the largest whose setup is
    the smallest size's setup (the slots, the context and the speculation, n-gram on: the context comes first, A45). W1.6
    (the user, 2026-11-13: "We want ngram still so batch 1024 if we must"): -ub 2048 where it fits beside n-gram, else
    1024, rather than A52's size that kept the most expert layers on the GPUs (a PC, the A3B Q4 with every expert in the
    RAM: -ub 2048 read 61% faster than 1024 at the same write speed, A-repro.md). ram_layers(ub): (the setup's key, the
    layers in the RAM); the layers no longer choose. A model with no experts in the RAM gives the largest size too. It
    gives 512 when neither 2048 nor 1024 keeps the setup: the context comes first, 1024 is not a floor."""
    sizes = [u for u in UB_CANDIDATES if u <= default] or [default]
    got = {u: ram_layers(u) for u in sizes}
    ref = got[min(sizes)][0]
    return max(u for u in sizes if got[u][0] == ref)


def answer_config(cfg: JsonObject, model: str, ask: UbAsk, choice: str) -> JsonObject:
    """config.json (a dict) with the answer to a UbAsk, a new dict. Use tuned (USE_TUNED): llama.ub is removed when
    that was the value asked about, the model's own choice (models.NAME.ub and ub_tune) when that was. Keep (KEEP): the
    model's choice becomes models.NAME.ub = mine, stamped with the tune it was made against (models.NAME.ub_tune); a
    llama.ub stays for the other models (nothing is lost silently)."""
    out: Dict[str, object] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}
    llama = dict(out.get("llama") or {}) if isinstance(out.get("llama"), dict) else {}
    models = dict(out.get("models") or {}) if isinstance(out.get("models"), dict) else {}
    prof = dict(models.get(model) or {}) if isinstance(models.get(model), dict) else {}
    if choice == KEEP:
        prof["ub"], prof["ub_tune"] = ask.mine, ask.stamp
    else:
        for key in ("ub", "ub_tune"):
            prof.pop(key, None)
        if ask.where == CONFIG:
            llama.pop("ub", None)
    for name, section in (("llama", llama), ("models", models)):
        if section:
            out[name] = section
        else:
            out.pop(name, None)
    if prof:
        models[model] = prof
        out["models"] = models
    else:
        models.pop(model, None)
        if models:
            out["models"] = models
        else:
            out.pop("models", None)
    return out  # type: ignore[return-value]


def choice_from(answer: Optional[str]) -> str:
    """The answer typed at the prompt: k or keep is KEEP; no input at all (None: Ctrl-D, the end of the input) is ""
    (nothing is answered, nothing saved: the start uses the tuned -ub and says so); anything else (Enter, t, tuned)
    USE_TUNED."""
    if answer is None:
        return ""
    return KEEP if answer.strip().lower() in ("k", "keep") else USE_TUNED

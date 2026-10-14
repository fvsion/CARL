#!/usr/bin/env python3
"""The rules list's combiners over every score we have (Phase 23.5.2.2; user, 2026-10-14: "let's try a B+C combo"):
the frozen encoder (B, bge), the zero-shot NLI model (C, DeBERTa), and the fine-tuned ones from finetune.py (SetFit
on bge, DeBERTa fine-tuned in NLI form), each alone and in combinations, each with the pattern list's and the parse's
flags. The same folds and thresholds as train_rules.py (out-of-fold F1 by session); scored on the held-out sets with
the hard drops (code alone, thanks, fragments) never a rule. Runs in CARL's NLP environment:

    .nlp/bin/python stack_eval.py train_set.json [finetune-OUT/scores.json]
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "monitor"))
sys.path.insert(0, HERE)
import nlp_tiers as nt  # noqa: E402
import rules as rl  # noqa: E402
import rules_eval as ev  # noqa: E402
import train_rules as tr  # noqa: E402

COMBOS = (("bge",), ("deb",), ("setfit",), ("nli",), ("bge", "deb"), ("setfit", "deb"), ("bge", "nli"),
          ("setfit", "nli"), ("setfit", "nli", "deb"), ("bge", "setfit", "nli", "deb"))


def flags(texts: Sequence[str]) -> List[List[float]]:
    _, feats = nt.parse([rl.Sentence(0, t, i) for i, t in enumerate(texts)])
    out = []
    for t, f in zip(texts, feats):
        st = rl.pattern_strength(t)
        out.append([1.0 if st == "strong" else 0.0, 1.0 if st == "weak" else 0.0]
                   + [1.0 if f[k] else 0.0 for k in nt.STACK_FLAGS] + [min(len(t.split()), 60) / 60])
    return out


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    ext = {}
    if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):
        with open(sys.argv[2], encoding="utf-8") as f:
            ext = json.load(f)
    texts, ys, fs = d["texts"], d["ys"], d["folds"]
    sets = {k: v for k, v in d["eval"].items()}
    gold = {k: [y for *_, y in ev.gold(ev.load(k))] for k in sets}
    hyps = tuple(nt.HYPOTHESES) + (nt.TASK_HYPOTHESIS,)
    # every base score: on the training sentences out of fold, and on each held-out set from the model fitted on all
    base: Dict[str, Dict[str, List[List[float]]]] = {"train": {}, **{k: {} for k in sets}}
    vecs = nt.embed(texts, "bge")
    oof = [0.0] * len(texts)
    for k in fs:
        rest = [i for i in range(len(texts)) if i not in set(k)]
        w, b = tr.fit_logistic([vecs[i] for i in rest], [ys[i] for i in rest])
        for i in k:
            oof[i] = nt.logistic(vecs[i], w, b)
    w, b = tr.fit_logistic(vecs, ys)
    base["train"]["bge"] = [[x] for x in oof]
    for k, v in sets.items():
        base[k]["bge"] = [[nt.logistic(x, w, b)] for x in nt.embed(v, "bge")]
    for k, v in (("train", texts), *sets.items()):
        m = nt.nli_matrix(v, "deberta", hyps)
        base[k]["deb"] = [[sum(r[:-1]) / (len(r) - 1), max(r[:-1]), r[-1]] for r in m]
        for name in ("setfit", "nli"):
            if name in ext:
                src = ext[name]["oof"] if k == "train" else ext[name][k]
                base[k][name] = [[x] for x in src]
    fl = {k: flags(v) for k, v in (("train", texts), *sets.items())}
    drop = {k: [nt.hard_drop(t) for t in v] for k, v in sets.items()}
    print(f"{len(texts)} training sentences; held out: " + ", ".join(f"{k} {len(v)}" for k, v in sets.items()))
    print(f"{'combination':28} {'OOF F1':>6}  " + "  ".join(f"{k + ' P / R / F1':>20}" for k in sets))
    for combo in COMBOS:
        if any(c not in base["train"] for c in combo):
            continue
        for with_flags in (False, True):
            def x(k: str, i: int) -> List[float]:
                return sum((base[k][c][i] for c in combo), []) + (fl[k][i] if with_flags else [])
            xs = [x("train", i) for i in range(len(texts))]
            oofs = [0.0] * len(texts)
            for k in fs:
                rest = [i for i in range(len(texts)) if i not in set(k)]
                w, b = tr.fit_logistic([xs[i] for i in rest], [ys[i] for i in rest])
                for i in k:
                    oofs[i] = nt.logistic(xs[i], w, b)
            thr, f1 = tr.best_threshold(oofs, ys)
            w, b = tr.fit_logistic(xs, ys)
            cells = []
            for k in sets:
                pred = [0 if drop[k][i] else int(nt.logistic(x(k, i), w, b) >= thr) for i in range(len(sets[k]))]
                g = [(0, "", y) for y in gold[k]]
                s = ev.scores(g, pred)
                cells.append(f"{s['precision']:.2f} / {s['recall']:.2f} / {s['f1']:.2f}")
            name = "+".join(combo) + (" +flags" if with_flags else "")
            print(f"{name:28} {f1:6.2f}  " + "  ".join(f"{c:>20}" for c in cells), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Trains the rules list's classifiers on labels-train.jsonl (Phase 23.5.2.2) and writes tools/monitor/rules-heads.json
(B's heads, every threshold) and A's model (NLP_DIR/models/rules-textcat). Each threshold is the best F1 on
out-of-fold scores (5 folds by session), so it is not set on sentences the classifier was fitted to; the final model
is then fitted on all the training labels. Runs in CARL's NLP environment:

    .nlp/bin/python tools/compaction-bench/rules/train_rules.py [--only A,B,C,S]   (S: the B+C combiner; needs B)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Dict, List, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "monitor"))
import nlp_tiers as nt  # noqa: E402
import rules as rl  # noqa: E402

# The training labels: hand-written, in-domain (long task specs), CC0 rules files in chat voice, and the discovery's
# (2026-10-14: statement-form conventions, lists, rules ending in inline code) (CARL_RULES_TRAIN:
# a comma list of these files, to measure each one's part)
TRAIN_FILES = (os.environ.get("CARL_RULES_TRAIN") or
               "labels-train.jsonl,labels-train-indomain.jsonl,labels-train-cursorrules.jsonl,"
               "labels-train-discovery.jsonl").split(",")
FOLDS = 5
SEED = 7
L2 = 1.0


def load() -> Tuple[List[str], List[int], List[int]]:
    """The training sentences, their labels, and each one's session (for the folds); hard drops left out."""
    texts, ys, groups = [], [], []
    g = 0
    for name in TRAIN_FILES:
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in (x for x in f if x.strip()):
                g += 1
                for turn in json.loads(line)["turns"]:
                    for s, y in turn:
                        if not nt.hard_drop(s):
                            texts.append(s)
                            ys.append(int(y))
                            groups.append(g)
    return texts, ys, groups


def folds(groups: Sequence[int]) -> List[List[int]]:
    ids = sorted(set(groups))
    random.Random(SEED).shuffle(ids)
    return [[i for i, g in enumerate(groups) if g in set(ids[k::FOLDS])] for k in range(FOLDS)]


def best_threshold(scores: Sequence[float], ys: Sequence[int]) -> Tuple[float, float]:
    best = (0.5, -1.0)
    for t in sorted(set(round(s, 4) for s in scores)):
        tp = sum(1 for s, y in zip(scores, ys) if s >= t and y)
        fp = sum(1 for s, y in zip(scores, ys) if s >= t and not y)
        fn = sum(1 for s, y in zip(scores, ys) if s < t and y)
        f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
        if f1 > best[1]:
            best = (t, f1)
    return best


def fit_logistic(xs: Sequence[Sequence[float]], ys: Sequence[int]) -> Tuple[List[float], float]:
    """L2 logistic regression by Newton steps (IRLS), classes weighted to balance; deterministic."""
    import numpy as np
    x = np.hstack([np.asarray(xs, dtype=float), np.ones((len(xs), 1))])
    y = np.asarray(ys, dtype=float)
    cw = np.where(y == 1, len(y) / (2 * max(1, y.sum())), len(y) / (2 * max(1, len(y) - y.sum())))
    w = np.zeros(x.shape[1])
    reg = L2 * np.eye(x.shape[1])
    reg[-1, -1] = 0.0
    for _ in range(50):
        p = 1 / (1 + np.exp(-np.clip(x @ w, -60, 60)))
        g = x.T @ (cw * (p - y)) + reg @ w
        h = x.T @ (x * (cw * p * (1 - p))[:, None]) + reg + 1e-9 * np.eye(x.shape[1])
        step = np.linalg.solve(h, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return [float(v) for v in w[:-1]], float(w[-1])


def train_textcat(texts: Sequence[str], ys: Sequence[int], out: str = "") -> object:
    import spacy
    from spacy.training import Example
    spacy.util.fix_random_seed(SEED)
    m = spacy.blank("en")
    tc = m.add_pipe("textcat")
    tc.add_label("RULE")
    tc.add_label("OTHER")
    ex = [Example.from_dict(m.make_doc(t), {"cats": {"RULE": float(y), "OTHER": float(1 - y)}}) for t, y in zip(texts, ys)]
    opt = m.initialize(lambda: ex)
    order = list(range(len(ex)))
    for epoch in range(25):
        random.Random(SEED + epoch).shuffle(order)
        for i in range(0, len(order), 8):
            m.update([ex[j] for j in order[i:i + 8]], sgd=opt, drop=0.2)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        m.to_disk(out)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="A,B,C,S")
    ap.add_argument("--export", help="write the training set, its folds and the held-out sentences here (finetune.py)")
    a = ap.parse_args()
    which = set(a.only.split(","))
    texts, ys, groups = load()
    clean = [nt.clean(t) for t in texts]
    _, feats = nt.parse([rl.Sentence(0, t, i) for i, t in enumerate(texts)])
    fs = folds(groups)
    if a.export:
        import rules_eval as ev
        out = {"texts": clean, "ys": ys, "groups": groups, "folds": fs,
               "eval": {k: [nt.clean(t) for _, t, _ in ev.gold(ev.load(k))] for k in ("test", "bench")}}
        with open(a.export, "w", encoding="utf-8") as f:
            json.dump(out, f)
        print(f"wrote {a.export}")
        return 0
    heads: Dict[str, dict] = nt._heads()
    print(f"{len(texts)} training sentences, {sum(ys)} rules, {FOLDS} folds by session")

    def report(key: str, oof: Sequence[float]) -> float:
        t, f1 = best_threshold(oof, ys)
        print(f"  {key:16} out-of-fold F1 {f1:.2f} at threshold {t:.3f}")
        return t
    if "A" in which:
        oof = [0.0] * len(texts)
        for k in fs:
            rest = [i for i in range(len(texts)) if i not in set(k)]
            m = train_textcat([clean[i] for i in rest], [ys[i] for i in rest])
            for i, d in zip(k, m.pipe([clean[i] for i in k])):
                oof[i] = d.cats["RULE"]
        thr = report("A", oof)
        train_textcat(clean, ys, nt.TEXTCAT)
        heads["A"] = {"threshold": thr}
    b_oof: Dict[str, List[float]] = {}
    if "B" in which or "S" in which:
        for enc in nt.ENCODERS:
            vecs = nt.embed(clean, enc)
            for with_f in (False, True):
                xs = [v + nt.feature_vector(f) if with_f else v for v, f in zip(vecs, feats)]
                oof = [0.0] * len(texts)
                for k in fs:
                    rest = [i for i in range(len(texts)) if i not in set(k)]
                    w, b = fit_logistic([xs[i] for i in rest], [ys[i] for i in rest])
                    for i in k:
                        oof[i] = nt.logistic(xs[i], w, b)
                key = f"B:{enc}{'+f' if with_f else ''}"
                if not with_f:
                    b_oof[enc] = oof
                thr = report(key, oof)
                w, b = fit_logistic(xs, ys)
                heads[key] = {"threshold": thr, "w": [round(v, 6) for v in w], "b": round(b, 6),
                              "features": list(nt.FEATURES) if with_f else []}
    if "C" in which:
        for m in nt.NLI:
            sc = nt.nli_scores(clean, m)
            heads[f"C:{m}"] = {"threshold": report(f"C:{m}", sc), "hypotheses": list(nt.HYPOTHESES)}
    if "D" in which:                     # the tiny LLM: zero-shot, the threshold only
        for m in nt.LLMS:
            sc = nt.llm_scores(clean, m)
            heads[f"D:{m}"] = {"threshold": report(f"D:{m}", sc)}
    if "S" in which:                     # the combiner: B's out-of-fold score, C's scores, the flags
        for enc in nt.ENCODERS:
            for nli in nt.NLI:
                c = nt.nli_matrix(clean, nli, tuple(nt.HYPOTHESES) + (nt.TASK_HYPOTHESIS,))
                xs = nt.stack_inputs(b_oof[enc], c, texts, feats)
                oof = [0.0] * len(texts)
                for k in fs:
                    rest = [i for i in range(len(texts)) if i not in set(k)]
                    w, b = fit_logistic([xs[i] for i in rest], [ys[i] for i in rest])
                    for i in k:
                        oof[i] = nt.logistic(xs[i], w, b)
                key = f"S:{enc}+{nli}"
                thr = report(key, oof)
                w, b = fit_logistic(xs, ys)
                heads[key] = {"threshold": thr, "w": [round(v, 6) for v in w], "bias": round(b, 6), "b": enc, "c": nli}
    with open(nt.HEADS, "w", encoding="utf-8") as f:
        json.dump(heads, f, indent=None, separators=(",", ":"))
        f.write("\n")
    print(f"wrote {nt.HEADS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

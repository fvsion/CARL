#!/usr/bin/env python3
"""The rules list's tiers against hand labels (Phase 23.5.2.2; user, 2026-10-14: "Try B and C both, A as baseline").

The label sets (one session a line: {"id", "set", "project", "turns": [[[sentence, 0|1], ...], ...]}; 1 = a rule):
- labels-bench.jsonl: the compaction bench's own user messages (agent-bench's prompts, the scripts' follow-ups and
  briefs) and the pyrules project rules said in chat.
- labels-train.jsonl: written for training A and B (and C's threshold); never scored as a result.
- labels-test.jsonl: held out, written separately (other projects, other phrasing, hard negatives); the result.

Each tier labels each gold sentence alone (the classifier's quality, apart from the split); the split is scored on its
own: how many gold sentences the splitter gives back whole from the joined message.

    python3 rules_eval.py [--tiers patterns,spacy,spacy+A,spacy+B,spacy+C] [--sets bench,test] [--errors]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "monitor"))
import rules as rl  # noqa: E402

Gold = List[Tuple[int, str, int]]          # (turn, sentence, label)


def load(name: str) -> List[dict]:
    p = os.path.join(HERE, f"labels-{name}.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def gold(sessions: Sequence[dict]) -> Gold:
    return [(t, s, int(lab)) for d in sessions for t, turn in enumerate(d["turns"], 1) for s, lab in turn]


def joined(turn: Sequence[Sequence]) -> str:
    """A turn's sentences as the user's message: list items on their own lines."""
    out = ""
    for s, _ in turn:
        out += ("\n" if s.startswith("- ") or out.endswith(":") else " ") + s if out else s
    return out


def split_score(sessions: Sequence[dict]) -> Tuple[int, int]:
    whole = total = 0
    for d in sessions:
        for t, turn in enumerate(d["turns"], 1):
            got = {s.text for s in rl.split(joined(turn), t)}
            for s, _ in turn:
                total += 1
                whole += (s[2:] if s.startswith("- ") else s) in got
    return whole, total


def classify(tier: str, g: Gold) -> Tuple[str, List[int]]:
    ss = [rl.Sentence(t, s, i) for i, (t, s, _) in enumerate(g)]
    if tier == "patterns":
        return "patterns", [int(bool(x)) for x in rl.classify_patterns(ss)]
    import nlp_tiers
    used, st = nlp_tiers.classify(ss, tier)
    return used, [int(bool(x)) for x in st]


def scores(g: Gold, pred: Sequence[int]) -> Dict[str, float]:
    tp = sum(1 for (_, _, y), p in zip(g, pred) if y and p)
    fp = sum(1 for (_, _, y), p in zip(g, pred) if not y and p)
    fn = sum(1 for (_, _, y), p in zip(g, pred) if y and not p)
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": pr, "recall": rc, "f1": 2 * pr * rc / (pr + rc) if pr + rc else 0.0, "tp": tp, "fp": fp,
            "fn": fn, "n": len(g), "rules": tp + fn}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default="patterns")
    ap.add_argument("--sets", default="bench,test")
    ap.add_argument("--errors", action="store_true", help="list each wrong sentence")
    ap.add_argument("--json", help="write the scores here (JSON lines)")
    a = ap.parse_args()
    rows = []
    for name in a.sets.split(","):
        sessions = load(name)
        if not sessions:
            print(f"{name}: no labels")
            continue
        g = gold(sessions)
        w, n = split_score(sessions)
        print(f"\n{name}: {len(sessions)} sessions, {n} sentences, {sum(y for *_, y in g)} rules; the split gives back "
              f"{w} of {n} whole ({w / n:.0%})")
        print(f"  {'tier':10} {'precision':>9} {'recall':>7} {'F1':>5}   tp  fp  fn")
        for tier in a.tiers.split(","):
            used, pred = classify(tier, g)
            sc = scores(g, pred)
            rows.append({"set": name, "tier": tier, "used": used, **sc})
            print(f"  {tier:10} {sc['precision']:9.2f} {sc['recall']:7.2f} {sc['f1']:5.2f}  {sc['tp']:3} {sc['fp']:3} "
                  f"{sc['fn']:3}" + (f"   (ran as {used})" if used != tier else ""))
            if a.errors:
                for (t, s, y), p in zip(g, pred):
                    if y != p:
                        print(f"      {'missed' if y else 'wrong '}  {s[:150]}")
    if a.json:
        with open(a.json, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

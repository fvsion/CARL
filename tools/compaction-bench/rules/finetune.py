#!/usr/bin/env python3
"""Step 2 of the rules list's tuning (Phase 23.5.2.2; research/rules-classifier-tuning.md, changes 3 and 4): fine-tunes
the two small models on the training labels, on a CUDA computer (user, 2026-10-14: "you could try that on the `ssh
zeus` system since it has an RTX 2070"); the sizes stay as they are ("it has to fit with our main model"):
- SetFit on bge-small-en-v1.5 (contrastive pairs, then a logistic head): B's encoder, tuned;
- DeBERTa-v3-xsmall zero-shot, fine-tuned in NLI form (Laurer et al.: each sentence with the rule hypothesis and the
  task hypothesis, entailment for the true one): C, tuned.
Each model: out-of-fold scores on the training sentences (the folds of train_rules.py, by session; for the combiner and
the thresholds), then a model fitted on all of them, its scores on the held-out sets, and the model saved (DeBERTa also
as ONNX, int8). Input: train_set.json (train_rules.py --export); output: OUT/scores.json and the models.

    python finetune.py train_set.json OUT [--only setfit,nli]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Sequence

SEED = 7
H_RULE = "This is a rule."
H_TASK = "This is a one-off task."


def setfit_run(train_x: Sequence[str], train_y: Sequence[int], score_sets: Dict[str, Sequence[str]], save: str = ""):
    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments
    model = SetFitModel.from_pretrained("BAAI/bge-small-en-v1.5")
    args = TrainingArguments(batch_size=32, num_epochs=1, num_iterations=20, seed=SEED, show_progress_bar=False)
    tr = Trainer(model=model, args=args, train_dataset=Dataset.from_dict({"text": list(train_x), "label": list(train_y)}))
    tr.train()
    out = {k: [float(p[1]) for p in model.predict_proba(list(v))] for k, v in score_sets.items()}
    if save:
        model.save_pretrained(save)
    return out


def nli_run(train_x: Sequence[str], train_y: Sequence[int], score_sets: Dict[str, Sequence[str]], save: str = ""):
    import numpy as np
    import torch
    from datasets import Dataset
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments,
                              set_seed)
    set_seed(SEED)
    base = "MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33"
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSequenceClassification.from_pretrained(base)       # 0 entailment, 1 not_entailment
    prem, hyp, lab = [], [], []
    for t, y in zip(train_x, train_y):
        for h, true in ((H_RULE, y == 1), (H_TASK, y == 0)):
            prem.append(t)
            hyp.append(h)
            lab.append(0 if true else 1)
    ds = Dataset.from_dict({"p": prem, "h": hyp, "label": lab}).map(
        lambda b: tok(b["p"], b["h"], truncation=True, max_length=256), batched=True)
    args = TrainingArguments(output_dir=os.path.join(save or "tmp-nli", "run"), per_device_train_batch_size=32,
                             num_train_epochs=3, learning_rate=2e-5, warmup_ratio=0.06, weight_decay=0.01,
                             fp16=torch.cuda.is_available(), seed=SEED, save_strategy="no", report_to=[],
                             logging_strategy="no")
    Trainer(model=model, args=args, train_dataset=ds, processing_class=tok).train()
    model.eval()

    def score(texts: Sequence[str]) -> List[float]:
        out: List[float] = []
        for i in range(0, len(texts), 64):
            enc = tok(list(texts[i:i + 64]), [H_RULE] * len(texts[i:i + 64]), truncation=True, max_length=256,
                      padding=True, return_tensors="pt").to(model.device)
            with torch.no_grad():
                p = torch.softmax(model(**enc).logits.float(), dim=-1)[:, 0]
            out += [float(x) for x in p.cpu().numpy()]
        return out
    out = {k: score(v) for k, v in score_sets.items()}
    if save:
        model.save_pretrained(save)
        tok.save_pretrained(save)
        cpu = model.to("cpu").float()
        enc = tok(["Never use print."], [H_RULE], return_tensors="pt")
        onnx = os.path.join(save, "model.onnx")
        torch.onnx.export(cpu, (enc["input_ids"], enc["attention_mask"]), onnx, input_names=["input_ids", "attention_mask"],
                          output_names=["logits"], dynamic_axes={"input_ids": {0: "batch", 1: "seq"},
                                                                 "attention_mask": {0: "batch", 1: "seq"},
                                                                 "logits": {0: "batch"}}, opset_version=17,
                          dynamo=False)
        from onnxruntime.quantization import QuantType, quantize_dynamic
        quantize_dynamic(onnx, os.path.join(save, "model_quantized.onnx"), weight_type=QuantType.QInt8)
        np.save(os.path.join(save, "check.npy"), np.array(score(["Never use print.", "Add a --json flag."])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("train_set")
    ap.add_argument("out")
    ap.add_argument("--only", default="setfit,nli")
    a = ap.parse_args()
    with open(a.train_set, encoding="utf-8") as f:
        d = json.load(f)
    texts, ys, folds = d["texts"], d["ys"], d["folds"]
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "scores.json")
    res = json.load(open(path)) if os.path.exists(path) else {}
    for name, run in (("setfit", setfit_run), ("nli", nli_run)):
        if name not in a.only.split(","):
            continue
        oof = [0.0] * len(texts)
        for k, fold in enumerate(folds):
            held = set(fold)
            rest = [i for i in range(len(texts)) if i not in held]
            got = run([texts[i] for i in rest], [ys[i] for i in rest], {"fold": [texts[i] for i in fold]})
            for i, p in zip(fold, got["fold"]):
                oof[i] = p
            print(f"{name}: fold {k + 1} of {len(folds)} done", flush=True)
        final = run(texts, ys, d["eval"], os.path.join(a.out, name))
        res[name] = {"oof": oof, **final}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(res, f)
        print(f"{name}: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

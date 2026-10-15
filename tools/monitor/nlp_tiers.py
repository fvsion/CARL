"""The rules list's NLP tiers (Phase 23.5.2.2; rules.py imports this in CARL's NLP environment, NLP_DIR): spaCy's parse
in place of the pattern list's word lists, and the classifiers that decide rule or not (user, 2026-10-14: "Try B and C
both, A as baseline").

- "spacy": the parse finds commands (a base-form verb with no subject, its "and ..." parts too), questions (a "?" or
  an inverted auxiliary), narration (a past-tense verb with a pronoun subject), negated commands, modals, and "no X";
  the pattern list's rule words still count. Strong or weak as the pattern list.
- "spacy+A": spaCy's own text classifier, trained on the training labels.
- "spacy+B:bge" | "spacy+B:minilm": a sentence encoder (bge-small-en-v1.5 or all-MiniLM-L6-v2, GGUF) run by llama.cpp's
  llama-embedding on the CPU (one run for all the sentences, no server), and a logistic head trained on the training
  labels; "+f" adds the parse's features to the encoder's vector ("spacy+B:bge+f").
- "spacy+C:deberta" | "spacy+C:modernbert": a zero-shot NLI model (ONNX Runtime, CPU): "the sentence entails the
  hypothesis" is the rule score; no training, the threshold set on the training labels.
A classifier decides which sentences are rules; the parse gives the strength (a rule with none: weak). Sentences that
are code only, thanks, or under two words never reach a classifier. The heads and thresholds: rules-heads.json (by
tools/compaction-bench/rules/train_rules.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

import rules as rl

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.environ.get("CARL_NLP_MODELS") or os.path.expanduser("~/models/gguf")
ENCODERS = {"bge": ("bge-small-en-v1.5-f16.gguf", "cls"), "minilm": ("all-MiniLM-L6-v2-f16.gguf", "mean"),
            # bge-small fine-tuned by SetFit on the training labels (finetune.py on a CUDA computer, then llama.cpp's
            # converter): the same size, its own head (B:setfit in rules-heads.json)
            "setfit": ("rules-setfit-bge-small-f16.gguf", "cls"),
            # the same, retrained with the discovery labels and inline code without its backticks (head "input":
            # "nocode"; discovery 2026-10-14: a sentence ending in "`code`." no longer reads as a task spec)
            "setfit2": ("rules-setfit2-bge-small-f16.gguf", "cls"),
            # setfit2's recipe at three seeds, the bodies' weights averaged into one (a model soup; discovery round 2:
            # the same mean as one seed without the seed lottery: bench recall 0.88 in three soups of nine seeds)
            "setfit3": ("rules-setfit3-bge-small-f16.gguf", "cls")}
NLI = {"deberta": "nlp/deberta-v3-xsmall-zeroshot", "modernbert": "nlp/modernbert-base-zeroshot"}
HYPOTHESES = ("This is a rule.", "This is a coding convention or a restriction.",   # the best of 9 on the training
              "This is a standing instruction, not a single task.")                   # labels (F1 0.82, DeBERTa)
HEADS = os.path.join(HERE, "rules-heads.json")
TEXTCAT = os.path.join(rl.NLP_DIR, "models", "rules-textcat")
SEP = "<#carl-sep#>"
# CPU or GPU (user, 2026-10-14: "If these are gonna be in RAM anyway.... doing it on GPU isn't awful"): the models are
# in the shared memory either way; "gpu" runs llama.cpp's on Metal and ONNX Runtime's on Core ML
DEVICE = os.environ.get("CARL_RULES_DEVICE", "cpu")
STACK_MAX_TOKENS = 256
NLI_FILE = os.environ.get("CARL_RULES_NLI_FILE", "")    # another ONNX file of the NLI model (the fp32 check)
TASK_HYPOTHESIS = "This is a one-off task."                  # the stacker's "not a rule" signal (research Q2)
STACK_FLAGS = ("question", "narration", "command", "request", "modal", "negated", "absolute", "limit", "no_x", "we",
               "you")
TEXT_MAX = 600
FEATURES = ("command", "request", "modal", "negated", "absolute", "limit", "no_x", "question", "narration",
            "conditional", "literal", "you", "we")

_nlp = None


def nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm", disable=["ner"])
    return _nlp


def available() -> List[str]:
    out: List[str] = []
    try:
        nlp()
    except Exception:  # noqa: BLE001  (no spaCy or no English model: the pattern list only)
        return out
    out.append("spacy")
    heads = _heads()
    if os.path.isdir(TEXTCAT) and "A" in heads:
        out.append("spacy+A")
    for k, (f, _) in ENCODERS.items():
        if os.path.exists(os.path.join(MODELS, f)) and f"B:{k}" in heads:
            out.append(f"spacy+B:{k}")
    for k, d in NLI.items():
        if os.path.isdir(os.path.join(MODELS, d)) and f"C:{k}" in heads:
            out.append(f"spacy+C:{k}")
    out += [f"spacy+{k}" for k, h in heads.items() if k.startswith("S:") and f"spacy+B:{h['b']}" in out
            and f"spacy+C:{h['c']}" in out]
    return out


def _heads() -> Dict[str, Any]:
    try:
        with open(HEADS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clean(text: str) -> str:
    """The text a model reads: code blocks as CODE, at most TEXT_MAX characters."""
    t = rl._CODE_BLOCK.sub(" CODE ", text)
    return re.sub(r"\s+", " ", t).strip()[:TEXT_MAX]

# ------------------------------------------------------------------ the parse


def parse_features(doc) -> Dict[str, bool]:
    text = doc.text
    body = rl._body(text)
    root = next((t for t in doc if t.dep_ == "ROOT"), None)
    verbs = [root] + [t for t in (root.conjuncts if root is not None else [])] if root is not None else []
    subj = lambda v: any(c.dep_ in ("nsubj", "nsubjpass", "expl") for c in v.children)   # noqa: E731
    aux_md = lambda v: any(c.tag_ == "MD" for c in v.children)                            # noqa: E731
    request = bool(re.match(r"^(?:can|could|would|will) you\b", body, re.I))
    command = any(v.tag_ == "VB" and not subj(v) and not aux_md(v) for v in verbs) or request
    first = doc[0] if len(doc) else None
    inverted = bool(first is not None and first.tag_ in ("MD", "VBZ", "VBP", "VBD") and first.dep_ == "aux"
                    and root is not None and subj(root)) and not request
    question = text.rstrip().endswith("?") and not request or inverted
    pron = {"i", "we", "it", "they", "he", "she", "that", "this"}
    narration = bool(root is not None and root.tag_ in ("VBD",) and any(
        c.dep_ == "nsubj" and c.lower_ in pron for c in root.children))
    negated = any(t.dep_ == "neg" or t.lower_ == "never" for t in doc if t.head.tag_.startswith("VB") or
                  t.head.dep_ == "ROOT")
    modal = any(t.tag_ == "MD" and t.lemma_ in ("must", "should", "shall") for t in doc) or bool(
        re.search(r"\b(?:have|has|need|needs) to\b", text, re.I))
    no_x = any(t.lower_ == "no" and t.dep_ == "det" and t.head.dep_ in ("npadvmod", "dobj", "ROOT", "conj", "attr",
                                                                         "nsubj", "appos") for t in doc)
    conditional = any(t.dep_ == "mark" and t.lower_ in ("if", "when", "whenever", "once", "unless", "before", "after")
                      for t in doc)
    return {"command": command, "request": request, "modal": modal, "negated": negated,
            "absolute": bool(rl._ABSOLUTE.search(body)), "limit": bool(rl._LIMIT.search(body)), "no_x": no_x,
            "question": question, "narration": narration, "conditional": conditional,
            "literal": rl.has_literal(body), "you": bool(re.search(r"\byou\b", text, re.I)),
            "we": bool(re.match(r"^(?:we|our)\b", body, re.I))}


def hard_drop(text: str) -> bool:
    """Never a rule, whatever a classifier says: code alone, thanks, under two words."""
    b = rl._body(rl._CODE_BLOCK.sub(" ", text))
    return len(re.findall(r"[\w'`-]+", b)) < 2 or bool(rl._PLEASANT.match(b))


def spacy_strength(text: str, f: Dict[str, bool], doc) -> Optional[str]:
    if hard_drop(text) or (f["question"] and not f["request"]):
        return None
    if f["narration"] and not (f["absolute"] or f["modal"]):
        return None
    if f["absolute"] or f["modal"] or (f["negated"] and f["command"]) or f["no_x"]:
        return "strong"
    if f["limit"] and (f["command"] or f["you"]):
        return "strong"
    if f["command"]:
        b = rl._body(text)
        verbs = {t.lemma_.lower() for t in doc if t.tag_ == "VB"}
        if verbs & rl.RULE_VERBS or (verbs & rl.RULE_VERBS_WITH and rl._RULE_PREP.search(b)) or (
                rl.LITERAL_COMMANDS and f["literal"]):
            return "weak"
    return None


def parse(ss: Sequence[rl.Sentence]) -> Tuple[List[Optional[str]], List[Dict[str, bool]]]:
    texts = [clean(s.text) for s in ss]
    strengths, feats = [], []
    for s, doc in zip(ss, nlp().pipe(texts)):
        f = parse_features(doc)
        feats.append(f)
        strengths.append(spacy_strength(s.text, f, doc))
    return strengths, feats

# ------------------------------------------------------------------ the classifiers


def embed(texts: Sequence[str], which: str, batch: int = 64) -> List[List[float]]:
    """Sentence vectors by llama-embedding on the CPU (normalised), in runs of batch sentences."""
    f, pooling = ENCODERS[which]
    out: List[List[float]] = []
    for i in range(0, len(texts), batch):
        part = [t.replace(SEP, " ") or "." for t in texts[i:i + batch]]
        dev = ["-ngl", "99"] if DEVICE == "gpu" else ["--device", "none", "-ngl", "0"]
        p = subprocess.run(["llama-embedding", "-m", os.path.join(MODELS, f), *dev,
                            "--pooling", pooling, "--embd-normalize", "2", "--embd-output-format", "array",
                            "--embd-separator", SEP, "-c", "512", "-b", "4096", "-ub", "4096", "-p", SEP.join(part)],
                           capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise RuntimeError(f"llama-embedding: {p.stderr.strip()[-200:]}")
        vecs = json.loads(p.stdout[p.stdout.index("[["):])
        if len(vecs) != len(part):
            raise RuntimeError(f"llama-embedding gave {len(vecs)} vectors for {len(part)} sentences")
        out += vecs
    return out


def feature_vector(f: Dict[str, bool]) -> List[float]:
    return [1.0 if f[k] else 0.0 for k in FEATURES]


def b_inputs(texts: Sequence[str], feats: Sequence[Dict[str, bool]], which: str, with_f: bool) -> List[List[float]]:
    vecs = embed(texts, which)
    return [v + feature_vector(f) if with_f else v for v, f in zip(vecs, feats)]


def logistic(x: Sequence[float], w: Sequence[float], b: float) -> float:
    import math
    z = b + sum(a * c for a, c in zip(x, w))
    return 1 / (1 + math.exp(-max(-60.0, min(60.0, z))))


def nli_scores(texts: Sequence[str], which: str, batch: int = 16) -> List[float]:
    """The entailment probability of the rule hypotheses (their mean), per sentence."""
    m = nli_matrix(texts, which, HYPOTHESES, batch)
    return [sum(r) / len(r) for r in m]


def nli_matrix(texts: Sequence[str], which: str, hyps: Sequence[str], batch: int = 16) -> List[List[float]]:
    """The entailment probability of each hypothesis, per sentence."""
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer
    d = os.path.join(MODELS, NLI[which])
    model = NLI_FILE or next(x for x in sorted(os.listdir(d)) if x.endswith(".onnx"))
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    prov = (["CoreMLExecutionProvider", "CPUExecutionProvider"] if DEVICE == "gpu" else ["CPUExecutionProvider"])
    sess = ort.InferenceSession(os.path.join(d, model), opts, providers=prov)
    tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
    tok.enable_truncation(STACK_MAX_TOKENS)
    tok.enable_padding()
    out = np.zeros((len(texts), len(hyps)))
    for j, h in enumerate(hyps):
        for i in range(0, len(texts), batch):
            enc = tok.encode_batch([(t, h) for t in texts[i:i + batch]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            logits = sess.run(None, {"input_ids": ids, "attention_mask": mask})[0]
            e = np.exp(logits - logits.max(axis=1, keepdims=True))
            out[i:i + batch, j] = (e / e.sum(axis=1, keepdims=True))[:, 0]
    return [[round(float(x), 6) for x in r] for r in out]


def textcat_scores(texts: Sequence[str]) -> List[float]:
    import spacy
    m = spacy.load(TEXTCAT)
    return [float(d.cats.get("RULE", 0.0)) for d in m.pipe(texts)]


def stack_inputs(b: Sequence[float], c: Sequence[Sequence[float]], texts: Sequence[str],
                 feats: Sequence[Dict[str, bool]]) -> List[List[float]]:
    """The combiner's inputs per sentence (user, 2026-10-14: "let's try a B+C combo"): B's score, C's rule hypotheses
    (mean and max), C's "one-off task" score, the pattern list's strength, the parse's flags, the length in words."""
    out = []
    for bi, ci, t, f in zip(b, c, texts, feats):
        rule = ci[:len(HYPOTHESES)]
        st = rl.pattern_strength(t)
        out.append([bi, sum(rule) / len(rule), max(rule), ci[-1], 1.0 if st == "strong" else 0.0,
                    1.0 if st == "weak" else 0.0] + [1.0 if f[k] else 0.0 for k in STACK_FLAGS]
                   + [min(len(t.split()), 60) / 60])
    return out


def stack_scores(texts: Sequence[str], feats: Sequence[Dict[str, bool]], key: str) -> List[float]:
    head = _heads()[key]
    enc, nli = head["b"], head["c"]
    bh = _heads()[f"B:{enc}"]
    b = [logistic(x, bh["w"], bh["b"]) for x in b_inputs(texts, feats, enc, False)]
    c = nli_matrix(texts, nli, tuple(HYPOTHESES) + (TASK_HYPOTHESIS,))
    return [logistic(x, head["w"], head["bias"]) for x in stack_inputs(b, c, texts, feats)]


# D: a tiny LLM asked "rule or not" (user, 2026-10-14: "it could be possible to use the qwen LLM the tiny one as well and
# see how well it does in a test case"): llama-server on the CPU, the probability of "yes" as the score. A test only.
LLMS = {"qwen35": "Qwen3.5-0.8B-Q8_0.gguf", "qwen3": "Qwen3-0.6B-Q8_0.gguf"}
LLM_PORT = int(os.environ.get("CARL_RULES_LLM_PORT", "8231"))
LLM_PROMPT = """You sort sentences from a user's messages to a coding assistant.
A RULE sets how the work is done from then on, or limits the task it is in: a constraint, must or never, a convention, a preference, a scope limit, a tool or library to keep using, a compatibility limit, a standing process ("after every change, run the linter"). "Add a cache without new dependencies." is a rule (a limit inside a task).
NOT A RULE: a one-off task, the spec of that task (inputs, outputs, formats, exit codes, file names, how the new code behaves), a question, information, narration, a bug report, pasted errors, thanks.
Examples:
"Never commit secrets." -> yes
"Use httpx, not requests." -> yes
"Keep the public header unchanged." -> yes
"We're on Python 3.8, so no match statements." -> yes
"Add a --json flag to the export command." -> no
"It should return 404 when the user does not exist." -> no
"Why is the test client getting a 500?" -> no
"I tried it twice and it still fails." -> no
Answer with one word: yes or no."""


def llm_scores(texts: Sequence[str], which: str) -> List[float]:
    import math
    import time
    import urllib.request
    url = f"http://127.0.0.1:{LLM_PORT}"
    proc = None
    try:
        urllib.request.urlopen(url + "/health", timeout=2)
    except OSError:
        dev = ["-ngl", "99"] if DEVICE == "gpu" else ["--device", "none", "-ngl", "0"]
        proc = subprocess.Popen(["llama-server", "-m", os.path.join(MODELS, LLMS[which]), *dev,
                                 "--port", str(LLM_PORT), "--host", "127.0.0.1", "-c", "4096", "-np", "1",
                                 "--temp", "0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                if json.load(urllib.request.urlopen(url + "/health", timeout=2)).get("status") == "ok":
                    break
            except (OSError, ValueError):
                time.sleep(0.5)
    out = []
    try:
        for t in texts:
            body = {"messages": [{"role": "system", "content": LLM_PROMPT},
                                 {"role": "user", "content": f'"{t}" ->'}],
                    "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
                    "chat_template_kwargs": {"enable_thinking": False}}
            r = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                       headers={"Content-Type": "application/json"})
            doc = json.load(urllib.request.urlopen(r, timeout=120))
            top = doc["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
            p = {"yes": 0.0, "no": 0.0}
            for x in top:
                w = x["token"].strip().lower()
                if w in p:
                    p[w] += math.exp(x["logprob"])
            out.append(p["yes"] / (p["yes"] + p["no"]) if p["yes"] + p["no"] else 0.5)
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=30)
    return out


def scores(tier: str, texts: Sequence[str], feats: Sequence[Dict[str, bool]]) -> List[float]:
    """The rule score (0 to 1) of each sentence by a tier's classifier."""
    kind = tier.split("+", 1)[1]
    if kind.startswith("D:"):
        return llm_scores(texts, kind[2:])
    if kind.startswith("S:"):
        return stack_scores(texts, feats, kind)
    if kind == "A":
        return textcat_scores(texts)
    if kind.startswith("B:"):
        which, _, plus = kind[2:].partition("+")
        head = _heads()[f"B:{which}{'+f' if plus == 'f' else ''}"]
        return [logistic(x, head["w"], head["b"]) for x in b_inputs(texts, feats, which, plus == "f")]
    if kind.startswith("C:"):
        return nli_scores(texts, kind[2:])
    raise ValueError(f"unknown tier {tier}")


def model_input(t: str, head: Dict[str, Any]) -> str:
    """The text as the classifier was trained on it: "nocode" drops inline code's backticks (head "input")."""
    if head.get("input") == "nocode" or os.environ.get("CARL_RULES_INPUT") == "nocode":
        return re.sub(r"`([^`\n]*)`", r"\1", t)
    return t


def head_key(tier: str) -> str:
    k = tier.split("+", 1)[1]
    return k if k == "A" or k.startswith(("C:", "S:", "D:")) else "B:" + k[2:]


def classify(ss: Sequence[rl.Sentence], tier: str) -> Tuple[str, List[Optional[str]]]:
    """(the tier used, each sentence's strength or None). "auto": the best one installed (B, then C, A, spaCy)."""
    strengths, feats = parse(ss)
    if tier == "auto":
        av = available()
        # the best measured first (2026-10-14, held-out F1: SetFit 0.89, the B+C combiner 0.81, B 0.76, C 0.74)
        tier = next((t for pre in ("spacy+B:setfit2", "spacy+B:setfit3", "spacy+B:setfit", "spacy+S", "spacy+B", "spacy+C", "spacy+A", "spacy") for t in av
                     if t.startswith(pre)),
                    "patterns")
        if tier == "patterns":
            return "patterns", rl.classify_patterns(ss)
    if tier == "spacy":
        return "spacy", strengths
    # the flow (user, 2026-10-14: "a flow from NLP > small model"): "+" the model decides every sentence but code,
    # thanks and fragments; ">" spaCy first drops questions and narration too, then the model decides; ">>" the
    # model only confirms the sentences spaCy calls rules
    flow = ">>" if ">>" in tier else ">" if ">" in tier else "+"
    idx = [i for i, s in enumerate(ss) if not hard_drop(s.text) and (
        flow == "+" or (flow == ">" and not (feats[i]["question"] and not feats[i]["request"])
                        and not (feats[i]["narration"] and not (feats[i]["absolute"] or feats[i]["modal"])))
        or (flow == ">>" and strengths[i]))]
    tier = tier.replace(">>", "+").replace(">", "+")
    texts = [clean(ss[i].text) for i in idx]
    fs = [feats[i] for i in idx]
    owner = list(range(len(idx)))
    # a unit of several sentences (a list item) scores as its best sentence too (discovery, 2026-10-14: the second
    # sentence of "Every function ... gets a docstring. Its first line starts with `Does:` ..." took it to 0.27)
    extra = [(k, p) for k, i in enumerate(idx) for p in rl.parts(ss[i].text) if not hard_drop(p)]
    if extra:
        _, efs = parse([rl.Sentence(0, p, j) for j, (_, p) in enumerate(extra)])
        texts += [clean(p) for _, p in extra]
        fs += efs
        owner += [k for k, _ in extra]
    head = _heads().get(head_key(tier), {})
    texts = [model_input(t, head) for t in texts]
    raw = scores(tier, texts, fs)
    sc = [0.0] * len(idx)
    for k, p in zip(owner, raw):
        sc[k] = max(sc[k], p)
    thr = float(head.get("threshold", 0.5))
    out: List[Optional[str]] = [None] * len(ss)
    for i, p in zip(idx, sc):
        if p >= thr:
            out[i] = strengths[i] or "weak"
    return {"+": tier, ">": tier.replace("+", ">", 1), ">>": tier.replace("+", ">>", 1)}[flow], out

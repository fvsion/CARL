#!/usr/bin/env python3
"""The rules list of carl-compaction's CARL note (Phase 23.5.2.2): the user's rules, quoted word for word from the
user's messages, so a rule said early in a long session is in front of the model again right before the current turn
(user, 2026-10-14: "python has extremely robust NLP libraries we could run to be even better, the pre LLM NLP stuff, or
a small CPU only ML model"; "b and all in python").

A rule: a sentence of the user's that sets how the work is done from then on (a limit, a must, a never, a convention,
a preference), or a limit on the task it is in ("... without changing the API"). Not a rule: a task done once, a
question, information, narration, thanks. The list: exact quotes, "(turn N)" on each, the newest last, duplicates
merged, about 1K tokens at most (strong rules before weak ones; the oldest go first); "The newest rule wins.".

Three tiers, the best one installed is used; a missing tier falls back to the one below, inside this module:
- "patterns": the pattern list (the standard library only; always there).
- "spacy": spaCy's parse (imperatives, modals, negations, subjects, tense) in place of the word patterns.
- "spacy+A" | "spacy+B" | "spacy+C": a classifier decides rule or not (A: spaCy's own text classifier, trained on the
  labels; B: a small sentence encoder run by llama.cpp's llama-embedding on the CPU and a trained head; C: a zero-shot
  NLI model, DeBERTa, by ONNX Runtime). spaCy still gives the strength (strong or weak) for the cap.
spaCy and the classifiers live in CARL's NLP environment (NLP_DIR, a virtual environment); the standard Python runs
this file and it re-runs itself there when a tier needs it.

The split into sentences is the same for every tier: code blocks, inline code, paths, URLs, versions and
abbreviations are held whole.

    python3 rules.py [--tier auto|patterns|spacy|spacy+A|spacy+B|spacy+C] [--cap TOKENS] < {"messages": [...],
                                                                                         "moved": "..."}
    -> {"text": "...", "rules": [{"turn", "quote", "strength"}], "tier": "...", "dropped": N}
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
NLP_DIR = os.environ.get("CARL_NLP_DIR") or os.path.join(ROOT, ".nlp")       # CARL's NLP environment (git-ignored)
CAP_TOKENS = 1000
CHARS_PER_TOKEN = 3.35               # as carl-compaction.js (messages)
QUOTE_MAX = 400                      # a longer sentence is quoted to here and marked cut
TIERS = ("patterns", "spacy", "spacy+A", "spacy+B:bge", "spacy+B:minilm", "spacy+B:bge+f", "spacy+B:minilm+f",
         "spacy+C:deberta", "spacy+C:modernbert")
HEADER = "Rules from the user (their exact words, oldest first). The newest rule wins."

# ------------------------------------------------------------------ the user's own words

# CARL's and the clients' text in a user message (not the user's words): the moved project block (OpenCode's <env>
# and "Instructions from:" blocks, Pi's <project_context> and <cwd>), the clients' system reminders, CARL's marks.
_OC_ENV = re.compile(r"Here is some useful information about the environment you are running in:.*?</env>\n*", re.S)
_PI_BLOCKS = re.compile(r"<(project_context|cwd)>.*?</\1>\n*", re.S)
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\n*", re.S)
_CARL = re.compile(r"\n*\[CARL[^\]]*\].*", re.S)         # a CARL mark ([CARL reminder], [CARL note]...) to the end


def message_text(m: Mapping[str, Any]) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(p.get("text", "")) for p in c if isinstance(p, Mapping) and p.get("type") == "text")
    return ""


def own_words(text: str, first: bool = False, moved: str = "") -> str:
    """A user message's text without CARL's and the clients' parts. moved: the project block carl-cache moved to the
    first message's start, when the client knows it (its "Instructions from:" blocks have no end mark of their own)."""
    t = _REMINDER.sub("", text)
    if first:
        if moved and t.startswith(moved):
            t = t[len(moved):]
        t = _PI_BLOCKS.sub("", _OC_ENV.sub("", t))
    return _CARL.sub("", t).strip()


def user_turns(messages: Sequence[Mapping[str, Any]], moved: str = "") -> List[Tuple[int, str]]:
    """(turn, the user's own words) for each user message; the turn counts the user's messages from 1."""
    out: List[Tuple[int, str]] = []
    n = 0
    for m in messages:
        if m.get("role") != "user":
            continue
        n += 1
        t = own_words(message_text(m), first=n == 1, moved=moved)
        if t:
            out.append((n, t))
    return out

# ------------------------------------------------------------------ sentences

_HELD = re.compile("|".join([
    r"```.*?(?:```|\Z)",                                    # a code block
    r"`[^`\n]+`",                                           # inline code
    r"https?://\S+",                                        # a URL
    r"\((?:[^()\n]|\([^()\n]*\))*\)",                       # a bracket (one level inside), its ; and . held
    r"(?<![\w/])~?(?:[\w.-]+/)+(?:[\w.-]*[\w-])?",          # a path with a slash (not its sentence's full stop)
    r"\b[\w-]+\.(?:py|js|ts|mjs|cjs|json|toml|md|txt|csv|ya?ml|sh|go|rs|java|c|h|cpp|rb|html|css|sql|lock|cfg|ini)\b",
    r"\b\d+(?:\.\d+)+\b",                                   # a version or a decimal
    r"\b(?:e\.g|i\.e|etc|vs|cf|approx|incl|no)\.(?=\s|,)",  # abbreviations
    r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\(?",                # a dotted name (os.path.join, self.x)
]), re.S | re.I)
_BREAK = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=\S)|\n\s*\n|\n(?=\s*(?:[-*•]|\d+[.)])\s)|\n(?=[A-Z`])")
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass
class Sentence:
    turn: int
    text: str            # the user's words, as written (spaces squashed)
    pos: int             # its place in the turn


def split(text: str, turn: int = 0) -> List[Sentence]:
    """The sentences of a text; held parts (code, paths, URLs, versions, abbreviations) never split."""
    held: List[str] = []

    def hold(m: re.Match) -> str:
        held.append(m.group(0))
        return f"\x00{len(held) - 1}\x00"
    t = _HELD.sub(hold, text)
    out: List[Sentence] = []
    for k, part in enumerate(_BREAK.split(t)):
        s = _ITEM.sub("", part)
        s = re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], s)
        s = re.sub(r"[ \t]*\n[ \t]*", " ", s) if "```" not in s else s
        s = re.sub(r"[ \t]{2,}", " ", s).strip()
        if s:
            out.append(Sentence(turn, s, k))
    return out

# ------------------------------------------------------------------ the pattern list (the standard library)

# Words that make a sentence a rule wherever they are.
_ABSOLUTE = re.compile(r"\b(?:never|don'?t|do not|do n't|must|mustn'?t|should(?:n'?t| not)?|make sure|ensure|avoid|"
                       r"under no circumstances|from now on|going forward|no longer|not allowed|at all times|"
                       r"is required|are required|mandatory|forbidden|have to|has to|need to|needs to|stick to|"
                       r"refrain|cannot|can'?t|please don'?t|let'?s not|let'?s (?:always|never|only|keep|stick)|"
                       r"(?:i|we)(?:'d| would)? (?:rather|prefer)|(?:i|we) (?:don'?t|do not) want|"
                       r"(?:i|we) want (?:you|it|them|this|that|everything|all|each|every))\b|"
                       r"^no\s+(?!problem|idea|worries|need|way|thanks|luck)\w|"
                       r"\bno (?:changes|edits|new \w+|more \w+)\b|\bonly[.!]?$|"
                       # a thing kept as it is; ", no X" / "so no X"; a step after or before each change
                       r"\b(?:unchanged|untouched|intact|as it is|as is)\b|\balone\b(?=[.!,]?\s*(?:for now)?[.!]?$)|"
                       r"(?:,|\bso)\s+no\s+\w|\b(?:after|before) (?:every|each)\b|\beverywhere\b|"
                       # a permission or a ban said as a fact; a preference
                       r"\b(?:is|are) (?:fine|ok|okay|allowed|off the table|not allowed|off limits)\b|"
                       r"\b(?:i'?d|i would) like (?:you|it|them|everything|all|the \w+) to\b|"
                       r"\b(?:not a fan of|i hate|i don'?t like)\b|"
                       # a convention of the project said as a fact
                       r"^(?:we|our \w+)\s+(?:use|are on|'re on|support|run on|target|prefer|stick to|work in)\b", re.I)
# Words that make a sentence a rule when it is also a command or speaks to the model ("you").
_LIMIT = re.compile(r"\b(?:only|always|instead of|rather than|unless|except|without|every time|each time|whenever|"
                    r"no more than|at most|at least|nothing but)\b", re.I)
_FILLER = re.compile(r"^(?:(?:please|also|and|but|so|then|just|now|ok(?:ay)?|right|oh|hey|again|finally|first|next|"
                     r"lastly)\b[,:]?\s+)+", re.I)
_QUESTION_START = re.compile(r"^(?:what|why|how|where|when|which|who|whose|is|are|was|were|do|does|did|can|could|"
                             r"would|will|have|has|should|shall|may|might)\b(?!.*\b(?:you|it)\s+(?:please)\b)", re.I)
_PLEASANT = re.compile(r"^(?:thanks?(?: you)?(?: so much)?|great|nice|perfect|good|cool|awesome|excellent|ok(?:ay)?|"
                       r"sounds good|looks good|lgtm|yes|yep|no|nope|sure|got it|right|done|great work|nice work|"
                       r"good job|well done)\b[\s!.,]*(?:thanks?(?: you)?)?[\s!.]*$", re.I)
_NARRATE = re.compile(r"^(?:i|we|it|that|this|they|he|she|the \w+|you)\s+(?:just\s+|already\s+|also\s+)?"
                      r"(?:was|were|did|had|tried|found|saw|got|ran|made|said|thought|noticed|wanted|used to|"
                      r"\w+ed)\b", re.I)
# A command's first word (after fillers): common verbs of coding work, in their base form.
VERBS = frozenset("""
add adjust allow apply ask avoid build bump call change check clean clear comment commit compile configure copy
create debug declare define delete deploy describe disable document drop edit enable ensure explain export extend
extract fix follow format generate give handle ignore implement import include indent inline install keep label leave
limit lint list load log make mark match measure merge migrate mirror move name note open optimize organize pass patch
pin place prefer prefix print put raise read rebase refactor remove rename reorder replace report require reset
resolve restore restructure return reuse revert review rewrite run save scan search send separate set show simplify
skip sort split start stick stop store strip structure suffix support switch tag test touch trace treat trim try
tune type update upgrade use validate verify wrap write work go do be think look reply answer respond say tell
speak push wait pause continue finish plan stay assume consider remember mention include
""".split())
# Plain commands that are rules: those with a rule-style verb (user, 2026-10-14: "b"; its literal half dropped below).
RULE_VERBS = frozenset("use prefer keep follow stick leave mirror match".split())
# A plain command with a literal (a path, a file name, backticked text) is not a rule by that alone: measured, it let in
# the tasks (bench precision 0.33 -> 0.51, test 0.57 -> 0.63, recall about the same; user, 2026-10-14: "drop the
# literal clause"). CARL_RULES_LITERALS=1 puts it back for a measurement.
LITERAL_COMMANDS = os.environ.get("CARL_RULES_LITERALS", "0") == "1"
RULE_VERBS_WITH = frozenset("write name format put place store call indent label prefix suffix document "
                            "comment structure organize treat handle log return raise print".split())
_RULE_PREP = re.compile(r"\b(?:in|as|with|like|under|using|to|by|for every|for each|for all|for any)\b", re.I)
_CONDITION = re.compile(r"^(?:if|when|whenever|once|before|after|while|unless|each time|every time)\b", re.I)

_LITERAL = re.compile(r"`[^`\n]+`|(?<![\w/.-])(?:[\w-]+/)+[\w.-]*|\b[\w-]+\.(?:py|js|ts|mjs|cjs|json|toml|md|txt|"
                      r"csv|ya?ml|sh|go|rs)\b|\b[A-Za-z_]\w*\([^()]*\)|(?<![\w-])--[A-Za-z][\w-]*|"
                      r"(?<![\[(\w])\"[^\"\n]{1,60}\"|\b\d+(?:\.\d+)?\b")


def has_literal(s: str) -> bool:
    """A literal the Request Check would hold (carl-brief.js requestLiterals: backticked text, a path, a call, a
    --flag, quoted text), or a number."""
    return bool(_LITERAL.search(s))


def _body(s: str) -> str:
    """The sentence without leading fillers ("please", "also", "and then") or a list mark."""
    return _FILLER.sub("", s.strip().lstrip("-*• ")).strip()


_CODE_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.S)


def dropped(s: str) -> bool:
    """A question, thanks, narration, a code block alone, or too short to be a rule."""
    b = _body(_CODE_BLOCK.sub(" ", s))
    words = re.findall(r"[\w'`-]+", b)
    if len(words) < 2 or _PLEASANT.match(b):
        return True
    if b.rstrip().endswith("?") and not re.match(r"^(?:can|could|would|will) you\b", b, re.I):
        return True
    return bool(_NARRATE.match(b)) and not _ABSOLUTE.search(b)


def first_word(s: str) -> str:
    b = re.sub(r"^(?:can|could|would|will) you (?:please )?", "", _body(s), flags=re.I)
    b = re.sub(r"^(?:always|never|only|also|just|first|then)\s+", "", b, flags=re.I)
    m = re.match(r"[A-Za-z']+", b)
    return m.group(0).lower() if m else ""


def is_command(s: str) -> bool:
    """Starts with a base-form verb ("Use pathlib", "Always run the tests", "Please keep it short"), or asks the
    model to ("can you keep...")."""
    b = _body(s)
    if re.match(r"^(?:can|could|would|will) you (?:please )?(\w+)", b, re.I):
        return True
    w = first_word(b)
    return w in VERBS or w in ("don't", "dont", "do", "never", "let's", "lets")


def pattern_strength(s: str) -> Optional[str]:
    """The pattern list: "strong", "weak", or None (not a rule)."""
    if dropped(s):
        return None
    b = _body(_CODE_BLOCK.sub(" ", s))
    if _ABSOLUTE.search(b):
        return "strong"
    command = is_command(b)
    talks = bool(re.search(r"\byou(?:'ll|'d)?\b", b, re.I))
    if _LIMIT.search(b) and (command or talks or re.match(r"^(?:always|only|never)\b", b, re.I)):
        return "strong"
    if _CONDITION.match(b):
        rest = b.split(",", 1)[1] if "," in b else ""
        return "weak" if rest and is_command(rest) else None
    if command:
        w = first_word(b)
        if w in RULE_VERBS or (w in RULE_VERBS_WITH and _RULE_PREP.search(b)) or (LITERAL_COMMANDS and has_literal(b)):
            return "weak"
    return None

# ------------------------------------------------------------------ the list


@dataclass
class Rule:
    turn: int
    quote: str
    strength: str
    pos: int = 0


def _key(q: str) -> str:
    return re.sub(r"[^\w]+", " ", q.lower()).strip()


def quote_of(s: str) -> str:
    return s if len(s) <= QUOTE_MAX else s[:QUOTE_MAX].rsplit(" ", 1)[0] + " [cut]"


def build(rules: Iterable[Rule], cap: int = CAP_TOKENS) -> Tuple[str, List[Rule], int]:
    """The list's text, the rules kept (oldest first), and how many were left out. Duplicates: the newest kept. Over
    the cap: the oldest weak rule goes first, then the oldest strong one."""
    newest: Dict[str, Rule] = {}
    for r in sorted(rules, key=lambda r: (r.turn, r.pos)):
        newest.pop(_key(r.quote), None)
        newest[_key(r.quote)] = r
    kept = sorted(newest.values(), key=lambda r: (r.turn, r.pos))

    def line(r: Rule) -> str:
        return f'- "{r.quote}" (turn {r.turn})'

    def size(rs: Sequence[Rule]) -> float:
        return (len(HEADER) + sum(len(line(r)) + 1 for r in rs) + 40) / CHARS_PER_TOKEN
    left = 0
    for strength in ("weak", "strong"):
        while kept and size(kept) > cap and any(r.strength == strength for r in kept):
            kept.remove(next(r for r in kept if r.strength == strength))
            left += 1
    if not kept:
        return "", [], left
    lines = [HEADER] + [line(r) for r in kept]
    if left:
        lines.append(f"({left} older {'rule was' if left == 1 else 'rules were'} left out.)")
    return "\n".join(lines), kept, left

# ------------------------------------------------------------------ the tiers


def sentences(messages: Sequence[Mapping[str, Any]], moved: str = "") -> List[Sentence]:
    return [s for turn, text in user_turns(messages, moved) for s in split(text, turn)]


def classify_patterns(ss: Sequence[Sentence]) -> List[Optional[str]]:
    return [pattern_strength(s.text) for s in ss]


def available() -> List[str]:
    """The tiers that can run here (in this Python or in the NLP environment)."""
    out = ["patterns"]
    try:
        import nlp_tiers  # noqa: F401  (the NLP environment's side: spaCy and the classifiers)
        out += nlp_tiers.available()
    except ImportError:
        pass
    return out


def run(messages: Sequence[Mapping[str, Any]], tier: str = "auto", cap: int = CAP_TOKENS,
        moved: str = "") -> Dict[str, Any]:
    """The rules list for a request's messages, with the tier that made it (a missing tier: the one below)."""
    ss = sentences(messages, moved)
    used = "patterns"
    strengths: List[Optional[str]] = classify_patterns(ss)
    if tier != "patterns":
        try:
            import nlp_tiers
            used, strengths = nlp_tiers.classify(ss, tier)
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001  (fail to the pattern list, and say why)
            used = f"patterns ({type(e).__name__}: {str(e)[:120]})"
    rules = [Rule(s.turn, quote_of(s.text), st, s.pos) for s, st in zip(ss, strengths) if st]
    text, kept, left = build(rules, cap)
    return {"text": text, "rules": [{"turn": r.turn, "quote": r.quote, "strength": r.strength} for r in kept],
            "tier": used, "dropped": left}


def _nlp_python() -> Optional[str]:
    p = os.path.join(NLP_DIR, "bin", "python")
    return p if os.path.exists(p) else None


def main(argv: Sequence[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="The rules list of carl-compaction's CARL note.")
    ap.add_argument("--tier", default="auto", choices=("auto",) + TIERS)
    ap.add_argument("--cap", type=int, default=CAP_TOKENS)
    a = ap.parse_args(argv)
    py = _nlp_python()
    if a.tier != "patterns" and py and os.path.realpath(sys.prefix) != os.path.realpath(NLP_DIR):
        os.execv(py, [py, os.path.abspath(__file__), *argv])          # spaCy and the classifiers are there
    doc = json.load(sys.stdin)
    json.dump(run(doc.get("messages") or [], a.tier, a.cap, str(doc.get("moved") or "")), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main(sys.argv[1:]))

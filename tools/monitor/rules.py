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
# A tier: "patterns", "spacy", or spaCy then a classifier: "+" (it decides alone), ">" (spaCy drops questions and
# narration first), ">>" (it confirms spaCy's rules); A | B:ENCODER[+f] | C:NLI | S:ENCODER+NLI | D:LLM (nlp_tiers.py)
TIER_RE = re.compile(r"^(?:auto|patterns|spacy|spacy(?:\+|>|>>)(?:A|B:\w+(?:\+f)?|C:[\w-]+|S:\w+\+[\w-]+|D:\w+))$")
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
# a line break before a lower-case line also ends a sentence (chat lines without full stops: "dont touch the colors
# file\nalso add a toggle"), unless the line before ends mid-phrase (a comma, "and", "the" ...) (discovery, 2026-10-14)
_BREAK_NL = re.compile(_BREAK.pattern + r"|(?<![,;:(&/-])(?<!\band)(?<!\bor)(?<!\bthe)(?<!\bto)(?<!\bof)(?<!\ba)"
                       r"(?<!\bwith)(?<!\bin)(?<!\bfor)[ \t]*\n(?=[ \t]*[a-z])")


@dataclass
class Sentence:
    turn: int
    text: str            # the user's words, as written (spaces squashed)
    pos: int             # its place in the turn
    item: bool = False   # a list item (kept whole: its sentences are one rule)
    intro: bool = False  # a line that ends with ":" and names rules ("A few rules for this project:")
    lead: bool = False   # a line that ends with ":" right before a list (any list): not a rule itself


_LINE_ITEM = re.compile(r"\n(?=[ \t]*(?:[-*\u2022]|\d+[.)])[ \t])")
_INTRO = re.compile(r"\b(?:rules?|guidelines?|conventions?|instructions?|requirements?|constraints?|policy|"
                    r"policies|standards?)\b[^:\n]*:\s*$", re.I)
# a standing intro without those words: "Some things to keep in mind the whole time:", "House style for this repo:"
# (but not a task's list: "Steps for today:", "The new command should:", "Acceptance criteria:")
_INTRO2 = re.compile(r"(?:\b(?:rules?|guidelines?|conventions?|instructions?|constraints?|policy|policies|standards?|"
                     r"house style|style guide|ground rules|keep in mind|bear in mind|remember|the whole (?:time|session)|"
                     r"(?:for|during) (?:the )?(?:whole|entire|rest of)|from now on|going forward|at all times|throughout|"
                     r"while you work|always|every time|in this (?:repo|project|codebase)|for this (?:repo|project|"
                     r"codebase)|keep to (?:these|this)|stick to (?:these|this)|follow (?:these|this) (?:the|for|while|"
                     r"always|throughout|at))\b)[^:\n]*:\s*$", re.I)
_NOT_INTRO = re.compile(r"\b(?:steps?|to ?do|todos?|the following|tasks?|changes|found|criteria|should|needs?|"
                        r"must do|plan)\b[^:\n]*:\s*$", re.I)


def split(text: str, turn: int = 0) -> List[Sentence]:
    """The sentences of a text; held parts (code, paths, URLs, versions, abbreviations) never split; a list item is
    one unit (user, 2026-10-14, the directive test: "Every function ... gets a docstring. Its first line starts with
    `Does:`" is one rule)."""
    held: List[str] = []

    def hold(m: re.Match) -> str:
        held.append(m.group(0))
        return f"\x00{len(held) - 1}\x00"
    t = _HELD.sub(hold, text)
    out: List[Sentence] = []

    def emit(part: str, item: bool) -> None:
        s = _ITEM.sub("", part)
        s = re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], s)
        s = re.sub(r"[ \t]*\n[ \t]*", " ", s) if "```" not in s else s
        s = re.sub(r"[ \t]{2,}", " ", s).strip()
        if s:
            intro = bool(_INTRO.search(s)) or (bool(_INTRO2.search(s)) and not _NOT_INTRO.search(s))
            out.append(Sentence(turn, s, len(out), item=item, intro=not item and intro))
    for block in re.split(r"\n\s*\n", t):
        lines = _LINE_ITEM.split(block)
        for j, chunk in enumerate(lines):
            if _ITEM.match(chunk):
                rest = ""
                # a line after the item that starts a new sentence (the item's last line ends one) is not part of
                # the item: "- a CSV button.\nKeep the layout as it is."
                m = re.search(r"(?<=[.!?;])[\"')\]]*[ \t]*\n(?=[A-Z`])", chunk)
                if m:
                    chunk, rest = chunk[:m.start()], chunk[m.end():]
                emit(chunk, True)
                if rest:
                    for part in _BREAK.split(rest):
                        emit(part, False)
            else:
                for part in _BREAK_NL.split(chunk):
                    emit(part, False)
    for i, s in enumerate(out[:-1]):              # "Steps for today:", "How I like things done:" before a list
        if not s.item and s.text.rstrip().endswith(":") and out[i + 1].item:
            s.lead = True
    return out


def parts(text: str) -> List[str]:
    """A unit's sentences by the plain sentence split (a list item kept whole can hold several); [] for one."""
    held: List[str] = []

    def hold(m: re.Match) -> str:
        held.append(m.group(0))
        return f"\x00{len(held) - 1}\x00"
    t = _HELD.sub(hold, text)
    ps = [re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], p).strip() for p in _BREAK.split(t)]
    ps = [p for p in ps if p]
    return ps if len(ps) > 1 else []


def under_intro(ss: Sequence[Sentence]) -> List[bool]:
    """The list items right after a rules intro, in the same turn: each one is a rule."""
    out, on = [False] * len(ss), False
    for i, s in enumerate(ss):
        if s.intro:
            on = True
        elif s.item and on and ss[i - 1].turn == s.turn:
            out[i] = True
        else:
            on = False
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


# Conventions said as facts, and the project's own way (discovery, 2026-10-14: the training sets' pattern misses;
# training recall 0.73 -> 0.84 at the same precision, held-out test recall 0.65 -> 0.75, bench 0.88 -> 0.92).
# Weak: "Every resource gets the tags", "New tests go in tests/unit", "Config keys are snake_case", "Fix the code, not
# the tests", "Standard library only, please".
_CONVENTION = re.compile("|".join([
    r"^(?:every|each|all|any)\s+(?:\w+[\s-]+){0,7}?(?:gets?|has|have|needs?|is|are|uses?|goes|go|lives?|stays?|starts?|"
    r"ends?|follows?|belongs?|comes?|takes?)\b",
    r"\b(?:go|goes|live|lives|belong|belongs|stay|stays|come|comes)\s+(?:in|into|under|at|through|out of|from|on top)\b",
    r"\b(?:snake|camel|pascal|kebab|screaming[_ ]snake)[_ -]?case\b|\b(?:lower|upper)[ _-]?case\b|\bALL_CAPS\b|"
    r"\bUPPER_CASE\b",
    r"\w,\s+not\s+(?!a\b|an\b|sure\b|yet\b|now\b|just\b|only\b|really\b)\w",
    r"^[\w\s'-]{1,40}?\bonly\b\s*(?:[,.;!]|please|$)"]), re.I)
# Strong: "In this codebase we write ...", "like the rest of the package", "from here on", "We're on Python 3.8".
_STANDING = re.compile(r"\b(?:in|for|across) this (?:codebase|repo|repository|project|package|crate|app|module)\b|"
                       r"\b(?:like|as in|same as|as with) the rest of\b|\bfrom here on\b|\bfor the rest of\b|"
                       r"\bthe whole (?:time|session)\b|"
                       r"^(?:we|our \w+)'(?:re|ll)\s+(?:on|using|targeting|sticking)\b|"
                       r"^we(?:'re| are) (?:on|using|targeting)\b", re.I)


def pattern_strength(s: str) -> Optional[str]:
    """The pattern list: "strong", "weak", or None (not a rule)."""
    if dropped(s):
        return None
    b = _body(_CODE_BLOCK.sub(" ", s))
    if _ABSOLUTE.search(b):
        return "strong"
    if _STANDING.search(b) and not (is_command(b) and first_word(b) not in RULE_VERBS | RULE_VERBS_WITH):
        return "strong"                      # but "Read every file in this project ..." is a task
    if _CONVENTION.search(b):
        return "weak"
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
    # the first message's rules (where a session's project rules are said) go last (discovery, 2026-10-14: in a long
    # session the old order dropped all of them)
    tiers = ((lambda r: r.strength == "weak" and r.turn > 1), (lambda r: r.turn > 1), (lambda r: r.strength == "weak"),
             (lambda r: True))
    for ok in tiers:
        while kept and size(kept) > cap and any(ok(r) for r in kept):
            kept.remove(next(r for r in kept if ok(r)))
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


def pattern_strengths(ss: Sequence[Sentence]) -> List[Optional[str]]:
    """Each unit's strength; a unit of several sentences (a list item) is as strong as its strongest sentence."""
    order = {"strong": 2, "weak": 1, None: 0}
    return [max([pattern_strength(s.text)] + [pattern_strength(p) for p in parts(s.text)], key=lambda x: order[x])
            for s in ss]


def classify_patterns(ss: Sequence[Sentence]) -> List[Optional[str]]:
    return pattern_strengths(ss)


_ATTACH = re.compile(r"^(?:its?|this|that|these|those|they|their|them|the same|for example|for instance|e\.g\.|"
                     r"i\.e\.|like|such as|so|otherwise|except)\b", re.I)


def list_majority(ss: Sequence[Sentence], strengths: Sequence[Optional[str]]) -> List[bool]:
    """The items of a list (consecutive items in one turn, after a lead line) where most items are rules: each one."""
    out = [False] * len(ss)
    i = 0
    while i < len(ss):
        if ss[i].item:
            j = i
            while j < len(ss) and ss[j].item and ss[j].turn == ss[i].turn:
                j += 1
            n = j - i
            k = sum(1 for x in strengths[i:j] if x)
            if n >= 2 and k * 3 >= n * 2:
                for m in range(i, j):
                    out[m] = True
            i = j
        else:
            i += 1
    return out


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
    listed = under_intro(ss)
    major = list_majority(ss, strengths)
    strengths = ["strong" if listed[i] else (None if s.intro or s.lead else st or ("weak" if major[i] else None))
                 for i, (s, st) in enumerate(zip(ss, strengths))]
    if os.environ.get("CARL_RULES_CHANGE", "1") == "1":
        strengths = changed_rules(ss, strengths)
    if os.environ.get("CARL_RULES_QVETO", "1") == "1":
        strengths = [None if st and s.text.rstrip().endswith("?") and not _REQUEST.match(_body(s.text)) else st
                     for s, st in zip(ss, strengths)]
    # a sentence that only says more about the rule before it ("Its first line...", "For example, ...") is quoted
    # with it, so a rule said in two sentences comes back whole
    merged: List[Tuple[Sentence, Optional[str]]] = []
    for s, st in zip(ss, strengths):
        if (merged and merged[-1][1] and not s.item and not merged[-1][0].item and not s.lead
                and s.turn == merged[-1][0].turn and s.pos == merged[-1][0].pos + 1 and _ATTACH.match(s.text)
                and not s.text.rstrip().endswith("?")):
            prev, pst = merged[-1]
            merged[-1] = (Sentence(prev.turn, prev.text + " " + s.text, s.pos), pst if pst == "strong" else st or pst)
        else:
            merged.append((s, st))
    ss, strengths = [m[0] for m in merged], [m[1] for m in merged]
    rules = [Rule(s.turn, quote_of(s.text), st, s.pos) for s, st in zip(ss, strengths) if st]
    text, kept, left = build(rules, cap)
    return {"text": text, "rules": [{"turn": r.turn, "quote": r.quote, "strength": r.strength} for r in kept],
            "tier": used, "dropped": left}


# A rule changed or lifted later: a change cue, and a content word shared with an earlier rule ("Don't change any code
# until I've seen the numbers." ... "You can change code now."; discovery, 2026-10-14)
_CHANGE = re.compile(r"\b(?:change of plan|forget (?:what|the|about)|scratch (?:that|the|what)|ignore what i said|"
                     r"never ?mind|on second thought|you can (?:\w+ ){0,4}now\b|(?:is|are) (?:fine|ok|okay|allowed) "
                     r"(?:now|after all|again)|no longer|not anymore|any ?more\b|from here on|actually,? |"
                     r"instead of what i said|same rule)", re.I)
_REQUEST = re.compile(r"^(?:can|could|would|will) you\b", re.I)
_STOP = frozenset("""the a an and or but to of in on for with from by at as is are be it its this that these those you
your we our i me my any all every each some no not do does don't did can could would will should must have has had
use make keep add new now then than so if when also just only more less one two""".split())


def _content(text: str) -> set:
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z_]+", text.lower()) if len(w) > 3 and w not in _STOP}


def changed_rules(ss: Sequence[Sentence], strengths: Sequence[Optional[str]]) -> List[Optional[str]]:
    out = list(strengths)
    for i, s in enumerate(ss):
        if out[i] or s.text.rstrip().endswith("?") or not _CHANGE.search(s.text):
            continue
        words = _content(s.text)
        if any(out[j] and words & _content(ss[j].text) for j in range(i)):
            out[i] = "weak"
    return out


def _nlp_python() -> Optional[str]:
    p = os.path.join(NLP_DIR, "bin", "python")
    return p if os.path.exists(p) else None


def main(argv: Sequence[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="The rules list of carl-compaction's CARL note.")
    ap.add_argument("--tier", default="auto", help="auto (the best installed), patterns, spacy, spacy+B:setfit, ...")
    ap.add_argument("--cap", type=int, default=CAP_TOKENS)
    a = ap.parse_args(argv)
    if not TIER_RE.match(a.tier):
        ap.error(f"unknown tier {a.tier!r}")
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

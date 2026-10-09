"""The report of a results file: per model, client, thinking and variant, the share of right decisions, the wrong
ones, the errors, the time to the first tool call, and the full runs. Text or Markdown. Pure."""
from __future__ import annotations

import statistics
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import events as ev
from .prompts import CATEGORIES

GroupKey = Tuple[str, str, str, str, str]
PASS_MARK = 90.0          # % delegated on large + stuck, and % kept on small + question


@dataclass
class Share:
    ok: int = 0
    total: int = 0

    def add(self, ok: Optional[bool]) -> None:
        if ok is not None:
            self.ok += int(ok)
            self.total += 1

    @property
    def pct(self) -> Optional[float]:
        return 100.0 * self.ok / self.total if self.total else None

    def text(self) -> str:
        return "-" if self.pct is None else f"{self.pct:.0f}% ({self.ok}/{self.total})"


@dataclass
class Group:
    """One model, client, thinking, variant and measure. strict: the first tool call decides; practical: the first
    decisive action (only in results of the practical measure)."""
    key: GroupKey
    runs: int = 0
    errors: int = 0
    undecided: int = 0
    practical: bool = False
    deleg_strict: Share = field(default_factory=Share)      # large + stuck, errors not counted
    deleg_prac: Share = field(default_factory=Share)
    keep_strict: Share = field(default_factory=Share)       # small + question, errors not counted
    keep_prac: Share = field(default_factory=Share)
    by_cat: Dict[str, Share] = field(default_factory=dict)  # category -> delegated (practical, else strict)
    wrong: "Counter[Tuple[str, str, str, str]]" = field(default_factory=Counter)  # (prompt, expected, got, first)
    error_texts: "Counter[str]" = field(default_factory=Counter)
    looks: List[float] = field(default_factory=list)
    decision_seconds: List[float] = field(default_factory=list)
    tool_seconds: List[float] = field(default_factory=list)
    thinking_tokens: List[int] = field(default_factory=list)
    versions: "Counter[str]" = field(default_factory=Counter)
    keep_from: str = ""                                      # the measure the keep shares come from, when not this one

    @staticmethod
    def _passed(d: Share, k: Share) -> Optional[bool]:
        if d.pct is None or k.pct is None:
            return None
        return d.pct >= PASS_MARK and k.pct >= PASS_MARK

    @property
    def passed_strict(self) -> Optional[bool]:
        return self._passed(self.deleg_strict, self.keep_strict)

    @property
    def passed_practical(self) -> Optional[bool]:
        return self._passed(self.deleg_prac, self.keep_prac) if self.practical else None


def measure_of(doc: Mapping[str, Any]) -> str:
    return str(doc.get("measure", ev.LEGACY_MEASURE))


def group_key(doc: Mapping[str, Any]) -> GroupKey:
    return (str(doc.get("model")), str(doc.get("client")), str(doc.get("thinking")), str(doc.get("variant")),
            measure_of(doc))


def _with_tool(decision: str, tool: Any) -> str:
    return decision + (f" ({tool})" if tool else "")


def summarize(docs: Iterable[Mapping[str, Any]]) -> "OrderedDict[GroupKey, Group]":
    """The decision runs, grouped (in the order of the file)."""
    groups: "OrderedDict[GroupKey, Group]" = OrderedDict()
    for d in docs:
        if d.get("mode", "decision") != "decision":
            continue
        k = group_key(d)
        g = groups.setdefault(k, Group(k))
        g.runs += 1
        g.versions[str(d.get("client_version", ""))] += 1
        expected, cat = str(d.get("expected")), str(d.get("category"))
        legacy = measure_of(d) == ev.LEGACY_MEASURE
        strict = str(d.get("decision") if legacy else d.get("decision_strict"))
        prac = None if legacy else str(d.get("decision"))
        g.practical = g.practical or prac is not None
        main = prac if prac is not None else strict
        deleg, keep = (g.deleg_strict, g.keep_strict)
        (deleg if expected == "delegate" else keep).add(ev.expected_ok(expected, strict))
        if prac is not None:
            (g.deleg_prac if expected == "delegate" else g.keep_prac).add(ev.expected_ok(expected, prac))
        if main == ev.ERROR:
            g.errors += 1
            g.error_texts[str(d.get("error", ""))[:120]] += 1
            continue
        if main == ev.UNDECIDED:
            g.undecided += 1
        g.by_cat.setdefault(cat, Share()).add(main == ev.DELEGATED)
        if ev.expected_ok(expected, main) is False:
            got = _with_tool(main, d.get("tool") if legacy else d.get("decision_tool"))
            first = _with_tool(strict, d.get("tool"))
            g.wrong[(str(d.get("prompt")), expected, got, first)] += 1
        for name, target in (("looks", g.looks), ("decision_seconds", g.decision_seconds),
                             ("tool_seconds", g.tool_seconds)):
            v = d.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and (name != "looks" or not legacy):
                if name == "looks" and main not in (ev.DELEGATED, ev.SELF_WRITE):
                    continue                                 # looks before a decision only
                target.append(float(v))
        tt = d.get("thinking_tokens_decision") or d.get("thinking_tokens")
        if isinstance(tt, int) and tt > 0:
            g.thinking_tokens.append(tt)
    borrow_keep(groups)
    return groups


def borrow_keep(groups: "OrderedDict[GroupKey, Group]") -> None:
    """A group of the current measure with no small requests or questions (only large and stuck were run again at
    the new limits, user 2026-10-06) takes its keep shares from the same model, client, thinking and variant at the
    previous measure. The look limit hardly changes a small request or a question: undecided is right for them."""
    for k, g in groups.items():
        if k[4] != ev.MEASURE or g.keep_prac.total or g.keep_strict.total:
            continue
        old = groups.get((k[0], k[1], k[2], k[3], ev.PREVIOUS_MEASURE))
        if old is not None and old.keep_prac.total:
            g.keep_strict, g.keep_prac, g.keep_from = old.keep_strict, old.keep_prac, ev.PREVIOUS_MEASURE


def _coder(f: Mapping[str, Any]) -> str:
    """The coder's own work in a full run: worked, STALLED, or - (no coder, or a result of the older format)."""
    if "coder_stalled" not in f or not f.get("coder_calls"):
        return "-"
    return "STALLED" if f.get("coder_stalled") else "worked"


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.0f}%"


def _median(xs: Sequence[float], fmt: str = "{:.1f}") -> str:
    return fmt.format(statistics.median(xs)) if xs else "-"


@dataclass
class Table:
    title: str
    head: List[str]
    rows: List[List[str]]
    note: str = ""


def yn(v: Any) -> str:
    return "-" if v is None else ("yes" if v else "no")


BriefKey = Tuple[str, str, str, str]


@dataclass
class BriefGroup:
    """The coder briefs of one model, client, variant and mode (decision | full), over the runs that sent one."""
    key: BriefKey
    runs: int = 0                                       # runs with at least one brief
    briefs: int = 0
    formats: "Counter[str]" = field(default_factory=Counter)
    first_valid: Share = field(default_factory=Share)   # the first brief valid (TOML or JSON briefs only)
    accepted: Share = field(default_factory=Share)      # the last brief not refused: the coder started
    refusals: List[int] = field(default_factory=list)   # refused briefs, one count per run
    first_tokens: List[float] = field(default_factory=list)
    tokens: List[float] = field(default_factory=list)
    hidden: Share = field(default_factory=Share)        # full runs: the hidden tests passed
    minutes: List[float] = field(default_factory=list)  # full runs


def brief_groups(docs: Iterable[Mapping[str, Any]]) -> "OrderedDict[BriefKey, BriefGroup]":
    """The runs with coder briefs (result lines with "briefs"), grouped by model, client, variant and mode."""
    groups: "OrderedDict[BriefKey, BriefGroup]" = OrderedDict()
    for d in docs:
        briefs = [b for b in (d.get("briefs") or []) if isinstance(b, dict)]
        if not briefs:
            continue
        k = (str(d.get("model")), str(d.get("client")), str(d.get("variant")), str(d.get("mode", "decision")))
        g = groups.setdefault(k, BriefGroup(k))
        g.runs += 1
        g.briefs += len(briefs)
        g.formats.update(str(b.get("format", "unknown")) for b in briefs)
        if briefs[0].get("format") in ("toml", "json"):
            g.first_valid.add(bool(briefs[0].get("valid")))
        g.accepted.add(not briefs[-1].get("refused"))
        g.refusals.append(sum(1 for b in briefs if b.get("refused")))
        toks = list(d.get("brief_tokens") or [])
        nums = [float(t) for t in toks if isinstance(t, int) and not isinstance(t, bool)]
        g.tokens += nums
        if toks and isinstance(toks[0], int) and not isinstance(toks[0], bool):
            g.first_tokens.append(float(toks[0]))
        if k[3] == "full":
            f = d.get("full") or {}
            if f.get("hidden_passed") is not None:
                g.hidden.add(bool(f.get("hidden_passed")))
            g.minutes.append(float(d.get("seconds") or 0) / 60)
    return groups


def brief_table(docs: Sequence[Mapping[str, Any]]) -> Optional[Table]:
    """The coder briefs per model, client, variant and mode (None: no result line has briefs)."""
    groups = brief_groups(docs)
    if not groups:
        return None
    t = Table("Coder briefs", ["Model", "Client", "Variant", "Mode", "Runs", "Briefs sent", "Formats",
                               "Valid at the first try", "Coder started in the end", "Refusals per run",
                               "Median tokens: first brief", "all briefs", "Hidden tests passed",
                               "Median minutes"], [])
    for k, g in groups.items():
        per_run = f"{sum(g.refusals) / len(g.refusals):.1f} (max {max(g.refusals)})" if g.refusals else "-"
        t.rows.append([*k, str(g.runs), str(g.briefs), ", ".join(f"{f} {n}" for f, n in g.formats.most_common()),
                       g.first_valid.text(), g.accepted.text(), per_run,
                       _median(g.first_tokens, "{:.0f}"), _median(g.tokens, "{:.0f}"),
                       g.hidden.text() if k[3] == "full" else "-",
                       _median(g.minutes) if k[3] == "full" else "-"])
    t.note = ("Runs: the runs with at least one coder brief. Valid at the first try: the run's first brief reads and "
              "passes CARL's check (client/shared/carl-brief.js), counted for TOML and JSON briefs only (a kv brief "
              "has no check). Coder started in the end: the run's last brief was not refused (a decision run goes on "
              "after a refusal until a brief is taken, the turn ends or a limit). Refusals per run: the mean (and "
              "the most) of the briefs that CARL refused. Tokens: the model's own count (the server's /tokenize; "
              "bench.py tokens fills it in afterwards). Hidden tests and minutes: full runs only.")
    return t


def tables(docs: Sequence[Mapping[str, Any]]) -> List[Table]:
    groups = summarize(docs)
    head = ["Model", "Client", "Thinking", "Variant", "Measure"]
    main = Table("Decision runs",
                 [*head, "Runs", "Delegated large + stuck: strict", "practical", "Kept small + question: strict",
                  "practical", "Undecided", "Errors", "Median looks before the decision",
                  "Median s to the decision", "Median s to the 1st tool", "Median thinking tokens",
                  "Pass: strict", "Pass: practical"], [])
    cats = Table("Delegated, per category (practical)", [*head, *CATEGORIES], [])
    wrong = Table("Wrong decisions (practical)", [*head, "Prompt", "Expected", "Got", "First tool", "Times"], [])
    errs = Table("Errors", [*head, "Error", "Times"], [])

    for k, g in groups.items():
        main.rows.append([*k, str(g.runs), g.deleg_strict.text(),
                          g.deleg_prac.text() if g.practical else "-", g.keep_strict.text(),
                          (g.keep_prac.text() + (f" [{g.keep_from}]" if g.keep_from else "")) if g.practical else "-",
                          str(g.undecided), str(g.errors),
                          _median(g.looks), _median(g.decision_seconds), _median(g.tool_seconds),
                          _median([float(x) for x in g.thinking_tokens], "{:.0f}"),
                          yn(g.passed_strict), yn(g.passed_practical)])
        cats.rows.append([*k, *[g.by_cat[c].text() if c in g.by_cat else "-" for c in CATEGORIES]])
        for (prompt, expected, got, first), n in sorted(g.wrong.items()):
            wrong.rows.append([*k, prompt, expected, got, first, str(n)])
        for text, n in g.error_texts.most_common():
            errs.rows.append([*k, text, str(n)])
    main.note = (
        "Strict: the first tool call decides. Practical: the first decisive action decides: the call to the coder "
        "(delegated) or a write of the main agent (self-write: write, edit, patch, or a shell command that writes "
        "files); the looks before it (read, grep, ls ...) do not decide. With no decisive action, the turn's end "
        f"is an answer; {ev.MAX_TOOLS} tool calls or {ev.MAX_SECONDS:.0f} s from the first model step with no "
        "decisive action are undecided. Undecided counts as not delegated: wrong for a large or stuck request, "
        f"right for a small request or a question. Pass: at least {PASS_MARK:.0f}% delegated on large and stuck "
        f"requests and at least {PASS_MARK:.0f}% kept on small requests and questions. Errors do not count in the "
        "shares. Times: from the first model step, in seconds. Measure first-tool: results of the older format "
        "(strict only).")
    out = [main, cats, wrong, errs]
    full = [d for d in docs if d.get("mode") == "full"]
    if full:
        ft = Table("Full runs", ["Model", "Client", "Thinking", "Variant", "Measure", "Prompt", "Run", "Delegated",
                                 "Coder result back", "Coder", "Tests run", "Hidden tests", "Suite before",
                                 "Suite after", "Minutes", "Error"], [])
        for d in full:
            f = d.get("full") or {}
            ft.rows.append([*group_key(d), str(d.get("prompt")), str(d.get("run")),
                            yn(f.get("coder_calls", 0) > 0 if "coder_calls" in f else None),
                            yn(f.get("coder_results", 0) > 0 if "coder_results" in f else None),
                            _coder(f), yn(f.get("tests_run")), yn(f.get("hidden_passed")), yn(f.get("suite_passed_before")),
                            yn(f.get("suite_passed")),
                            f"{float(d.get('seconds') or 0) / 60:.1f}", str(d.get("error", ""))[:60]])
        ft.note = ("Hidden tests: the tests that the harness copies in after the run. Suite before and after: the "
                   "project's own tests (the pylib fixture has one failing test before any work). Coder: worked, or "
                   "STALLED (it came back with no write and no test run: OpenCode, from its own session; Pi, no "
                   "file changed when its result came back).")
        out.append(ft)
    bt = brief_table(docs)
    if bt is not None:
        out.append(bt)
    return out


def _cell(s: str, md: bool) -> str:
    return s.replace("|", "\\|").replace("\n", " ") if md else s.replace("\n", " ")


def render(tbls: Sequence[Table], md: bool = False) -> str:
    lines: List[str] = []
    for t in tbls:
        if md:
            lines += [f"## {t.title}", ""]
            if not t.rows:
                lines += ["None.", ""]
                continue
            lines.append("| " + " | ".join(t.head) + " |")
            lines.append("|" + "|".join("---" for _ in t.head) + "|")
            lines += ["| " + " | ".join(_cell(c, True) for c in r) + " |" for r in t.rows]
            lines.append("")
            if t.note:
                lines += [t.note, ""]
        else:
            lines += [t.title, "=" * len(t.title)]
            if not t.rows:
                lines += ["None.", ""]
                continue
            widths = [max(len(h), *(len(_cell(r[i], False)) for r in t.rows)) for i, h in enumerate(t.head)]
            lines.append("  ".join(h.ljust(w) for h, w in zip(t.head, widths)).rstrip())
            lines += ["  ".join(_cell(c, False).ljust(w) for c, w in zip(r, widths)).rstrip() for r in t.rows]
            if t.note:
                lines += ["", t.note]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report(docs: Sequence[Mapping[str, Any]], md: bool = False) -> str:
    return render(tables(docs), md)


def versions_line(docs: Iterable[Mapping[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, Set[str]] = {}
    for d in docs:
        out.setdefault(str(d.get("client")), set()).add(str(d.get("client_version", "")))
    return {k: sorted(v) for k, v in out.items()}

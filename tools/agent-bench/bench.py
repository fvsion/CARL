#!/usr/bin/env python3
"""agent-bench: does the main agent of OpenCode and Pi hand coding work to the coder subagent?

    bench.py prepare --home DIR --model NAME          OpenCode and Pi in a HOME of the harness (client package)
    bench.py run --home DIR --models A,B --out results.jsonl [...]   the matrix (resumable)
    bench.py report results.jsonl [--md]              the tables
    bench.py fetch MODEL / bench.py drop MODEL        copy a model in from the library / remove the local copy
    bench.py variants                                 the variants (tools/agent-bench/variants/)
    bench.py tokens results.jsonl --url URL           the coder briefs' tokens, counted afterwards (POST /tokenize)

See tools/agent-bench/README.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentbench import REPO  # noqa: E402
from agentbench import clients as cl  # noqa: E402
from agentbench import events as ev  # noqa: E402
from agentbench import briefs as bf  # noqa: E402
from agentbench import fixtures, home as hm, library, matrix, models, proc, report, variant  # noqa: E402
from agentbench.prompts import CATEGORIES, load_prompts, select  # noqa: E402
from agentbench.results import Result, ResultStore, read_results  # noqa: E402
from agentbench.server import CarlServer, ServerError, ServerSetup, check_port, ensure_key  # noqa: E402

DEFAULT_PORT = 8097


def say(msg: str) -> None:
    print(msg, flush=True)


def csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def keep_awake() -> Optional["subprocess.Popen[bytes]"]:
    """caffeinate -i -s while this process lives (macOS): no idle or system sleep during a long run."""
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return None
    return subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def work_dir(a: argparse.Namespace) -> str:
    w = str(a.work or (os.path.abspath(os.path.expanduser(a.home)).rstrip("/") + "-work"))
    os.makedirs(w, exist_ok=True)
    return os.path.abspath(w)


def models_dir(a: argparse.Namespace) -> str:
    return os.path.abspath(os.path.expanduser(str(a.models_dir)))


def server_setup(a: argparse.Namespace, model: str) -> ServerSetup:
    return ServerSetup(repo=a.repo, model=model, work=os.path.join(work_dir(a), "server"), port=a.port,
                       models_dir=models_dir(a), slots=a.slots)


def package_source(a: argparse.Namespace, s: ServerSetup) -> hm.PackageSource:
    return hm.PackageSource(a.repo, s.client_dir, s.conf_dir, os.path.join(work_dir(a), "dist"), s.models_dir)


# ------------------------------------------------------------------------------------------------ prepare
def cmd_prepare(a: argparse.Namespace) -> int:
    home = hm.check_home_path(a.home)
    s = server_setup(a, a.model)
    srv = CarlServer(s)
    say(f"Starting CARL's server for {a.model} on port {a.port} (log: {srv.log_file})")
    srv.start()
    try:
        say(f"The server is ready after {srv.wait_ready():.0f} s. Making the client package and running ./setup "
            f"in {home} (this installs OpenCode and Pi; some minutes)")
        found = hm.prepare(package_source(a, s), home, None if a.latest else a.opencode_version,
                           None if a.latest else a.pi_version)
    finally:
        srv.stop()
    say(f"Ready: {home} (OpenCode {found.get('opencode')}, Pi {found.get('pi')}). The server is stopped.")
    return 0


# ------------------------------------------------------------------------------------------------ run
def measure_name(limits: cl.Limits) -> str:
    """The measure of the results: other limits are another measure (their runs do not mix on resume)."""
    if (limits.max_tools, limits.max_seconds) == (ev.MAX_TOOLS, ev.MAX_SECONDS):
        return ev.MEASURE
    return f"{ev.MEASURE}-t{limits.max_tools}-s{limits.max_seconds:.0f}"


def run_cell(cell: matrix.Cell, home: str, env: Dict[str, str], runs_dir: str, limit: float,
             versions: Dict[str, str], keep_copy: bool, limits: cl.Limits = cl.Limits(),
             count: Optional[bf.Counter] = None) -> Result:
    repo_dir = fixtures.make_fresh(cell.prompt.fixture, runs_dir)
    spec = cl.RunSpec(cell.client, cell.model, cell.thinking, cell.prompt.text, repo_dir, home, limit)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    full: Dict[str, object] = {}
    if cell.mode == "full":
        before = fixtures.run_suite(repo_dir)
        full.update(suite_passed_before=before.passed)
        out = cl.run_full(spec, env)
        full.update(out.full)
        checks = fixtures.check_hidden(repo_dir, cell.prompt.fixture, cell.prompt.hidden)
        full.update(hidden_passed=checks["hidden"].passed, hidden_output=checks["hidden"].output,
                    suite_passed=checks["suite"].passed, suite_output=checks["suite"].output,
                    changed_files=fixtures.changed_files(repo_dir)[:80], workdir=repo_dir)
    else:
        out = cl.run_decision(spec, env, limits)
    o = out.obs
    strict = out.decision
    prac = out.practical
    decision = prac.decision
    tool = o.first_tool
    error = o.error if ev.ERROR in (decision, strict) else ""
    if decision == ev.ERROR and cell.client == "pi" and out.stderr.strip():
        error += " | stderr: " + " / ".join(out.stderr.strip().splitlines()[-3:])[:500]   # OpenCode: log lines
    briefs = bf.collect(cell.client, o.tools)
    result = Result(
        time=stamp, mode=cell.mode, model=cell.model, client=cell.client,
        client_version=versions.get(cell.client, "unknown"), thinking=cell.thinking,
        thinking_level=out.thinking_level, variant=cell.variant, category=cell.prompt.category,
        prompt=cell.prompt.id, run=cell.run, decision=decision, expected=cell.prompt.expect,
        correct=ev.expected_ok(cell.prompt.expect, decision), measure=cell.measure, decision_strict=strict,
        correct_strict=ev.expected_ok(cell.prompt.expect, strict),
        decision_tool=prac.tool.name if prac.tool else "",
        decision_args=ev.trim(prac.tool.args, 400) if prac.tool else {}, looks=prac.looks,
        decision_seconds=None if prac.seconds is None else round(prac.seconds, 2), undecided_reason=prac.reason,
        tool=tool.name if tool else "", args=ev.trim(tool.args, 400) if tool else {},
        step_tools=list(o.step_tools), seconds=round(out.seconds, 2),
        tool_seconds=None if o.tool_seconds() is None else round(float(o.tool_seconds() or 0.0), 2),
        startup_seconds=None if o.model_start is None else round(o.model_start, 2),
        thinking_tokens=o.thinking_tokens or None, thinking_tokens_decision=o.thinking_tokens_all or None,
        thinking_chars=o.thinking_chars,
        first_event=ev.trim(o.first_event, 300), error=error[:2000], full=full,
        briefs=briefs, brief_tokens=bf.count_all(briefs, count))
    if not keep_copy and cell.mode != "full":
        shutil.rmtree(os.path.dirname(repo_dir), ignore_errors=True)
    return result


def cmd_run(a: argparse.Namespace) -> int:
    home = hm.require_harness_home(a.home)
    clients = csv(a.clients)
    for c in clients:
        if c not in ev.CLIENTS:
            raise SystemExit(f"error: unknown client {c!r} (opencode, pi)")
    thinking = csv(a.thinking)
    for t in thinking:
        if t not in ("default", "off"):
            cl.check_level(t)
    model_names = csv(a.models)
    if not model_names:
        raise SystemExit("error: --models needs at least one model name")
    if not a.no_server:
        check_port(a.port)
    var = variant.load(a.variant)
    prompts = select(load_prompts(), csv(a.categories) or None, csv(a.prompts) or None, a.full, a.with_extra)
    if not prompts:
        raise SystemExit("error: no prompt matches (full runs use the prompts with hidden tests)")
    mode = "full" if a.full else "decision"
    limit = a.limit if a.limit else (1800.0 if a.full else 600.0)
    store = ResultStore(a.out, retry_errors=a.retry_errors)
    limits = cl.Limits(a.max_tools, a.max_seconds)
    if limits.max_tools < 1 or limits.max_seconds <= 0:
        raise SystemExit("error: --max-tools and --max-seconds must be more than 0")
    cells = [dataclasses.replace(c, measure=measure_name(limits))
             for c in matrix.build(model_names, clients, thinking, a.runs, var.name, prompts, mode)]
    todo = matrix.pending(cells, store.done_keys())
    say(f"{len(cells)} runs in the matrix, {len(cells) - len(todo)} done already, {len(todo)} to run "
        f"({mode} runs, variant {var.name}). Results: {os.path.abspath(a.out)}")
    if not todo:
        return 0
    awake = keep_awake()
    work = work_dir(a)
    runs_dir = os.path.join(work, "runs")
    times: List[float] = []
    n = 0
    catalog = library.load_catalog(a.catalog)
    try:
        for model, mcells in matrix.by_model(todo).items():
            say(f"== {model}: {len(mcells)} runs")
            if a.fetch:
                library.fetch(catalog, model, a.library, models_dir(a), say=say)
            srv: Optional[CarlServer] = None
            count: Optional[bf.Counter] = None
            try:
                if not a.no_server:
                    s = server_setup(a, model)
                    srv = CarlServer(s)
                    pid = srv.start()
                    say(f"server: pid {pid}, port {a.port}, log {srv.log_file}")
                    say(f"server: ready after {srv.wait_ready():.0f} s")
                    count = bf.tokenizer(srv.url, ensure_key(s.key_file))      # the briefs' tokens
                    hm.refresh(package_source(a, s), home)
                    say("clients: configs written again for this model (./setup --no-install)")
                changed = var.apply(home)
                if changed:
                    say(f"variant {var.name}: changed {', '.join(changed)}")
                env = cl.client_env(home)
                versions = cl.versions(sorted(set(clients)), env, work)
                say("clients: " + ", ".join(f"{k} {v}" for k, v in versions.items()))
                for cell in mcells:
                    n += 1
                    res = run_cell(cell, home, env, runs_dir, limit, versions, a.keep_runs, limits, count)
                    store.append(res)
                    times.append(res.seconds)
                    eta = matrix.eta_seconds(times, len(todo) - n)
                    what = res.decision + (f" ({res.decision_tool})" if res.decision_tool else "")
                    first = res.decision_strict + (f" ({res.tool})" if res.tool else "")
                    mark = {True: "right", False: "WRONG", None: "error"}[res.correct]
                    refused = sum(1 for b in res.briefs if b.get("refused"))
                    brief = (f", {len(res.briefs)} brief(s), {refused} refused" if res.briefs else "")
                    say(f"[{n}/{len(todo)}] {cell.label()}: {what} after {res.looks} looks, {mark} "
                        f"(first tool: {first}){brief}, {res.seconds:.0f} s"
                        f"; time left about {matrix.fmt_duration(eta)}")
            except ServerError as e:
                say(f"error: {model}: {e}")
                if not a.keep_going:
                    return 1
            finally:
                if srv is not None:
                    srv.stop()
                    say("server: stopped")
            if a.drop:
                library.drop(catalog, model, a.library, models_dir(a), list(models.DEFAULT_KEEP) + csv(a.keep),
                             say=say)
    finally:
        if awake is not None:
            awake.terminate()
    say(f"Done. {n} runs. bench.py report {a.out}")
    return 0


# ------------------------------------------------------------------------------------------------ the others
def cmd_report(a: argparse.Namespace) -> int:
    docs = list(read_results(a.results))
    if not docs:
        raise SystemExit(f"error: no results in {a.results}")
    vers = report.versions_line(docs)
    head = "Client versions: " + "; ".join(f"{k} {', '.join(v)}" for k, v in sorted(vers.items()))
    text = (f"{head}\n\n" if not a.md else f"{head}.\n\n") + report.report(docs, a.md)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(text)
        say(f"wrote {a.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_fetch(a: argparse.Namespace) -> int:
    library.fetch(library.load_catalog(a.catalog), a.model, a.library, models_dir(a), say=say)
    return 0


def cmd_drop(a: argparse.Namespace) -> int:
    keep = list(models.DEFAULT_KEEP) + csv(a.keep)
    removed = library.drop(library.load_catalog(a.catalog), a.model, a.library, models_dir(a), keep, say=say,
                           dry_run=a.dry_run)
    say(f"{len(removed)} file(s) removed.")
    return 0


def cmd_tokens(a: argparse.Namespace) -> int:
    """Count the coder briefs' tokens afterwards (a run without a known server: --no-server, or an older file):
    POST /tokenize on a server of the same model. Lines of another model (--model) and lines with every count
    are left as they are. The file is written again in place (atomically)."""
    key = ""
    if a.key_file:
        with open(os.path.expanduser(a.key_file), encoding="utf-8") as f:
            key = f.read().strip()
    count = bf.tokenizer(a.url, key)
    with open(a.results, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out: List[str] = []
    filled = docs = 0
    for line in lines:
        doc = ev.parse_line(line)
        if doc is None or (a.model and doc.get("model") != a.model) or not doc.get("briefs"):
            out.append(line)
            continue
        n = bf.fill_tokens(doc, count)
        docs += 1 if n else 0
        filled += n
        out.append(json.dumps(doc, ensure_ascii=False) if n else line)
    if filled:
        tmp = a.results + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, a.results)
    missing = sum(1 for line in out for t in ((ev.parse_line(line) or {}).get("brief_tokens") or []) if t is None)
    say(f"{filled} brief(s) counted in {docs} line(s); {missing} still without a count.")
    return 0


def cmd_variants(a: argparse.Namespace) -> int:
    for name in variant.available():
        say(f"{name}: {variant.load(name).description}")
    return 0


# ------------------------------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench.py", description="Measure the main agent's hand-off to the coder.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, with_home: bool = True) -> None:
        if with_home:
            sp.add_argument("--home", required=True, help="the harness HOME (never your own)")
            sp.add_argument("--work", help="the work folder (default: HOME-work): server files, fixture copies")
        sp.add_argument("--repo", default=REPO, help="the CARL repository (default: this one)")
        sp.add_argument("--models-dir", default=models.DEFAULT_MODELS_DIR, help="the local models folder")
        sp.add_argument("--library", default=models.DEFAULT_LIBRARY, help="the models library (read only)")
        sp.add_argument("--catalog", default=models.CATALOG, help="the catalogue (host/catalog.json)")

    sp = sub.add_parser("prepare", help="install OpenCode and Pi into a harness HOME from a client package")
    common(sp)
    sp.add_argument("--model", required=True, help="a downloaded model for the server that makes the package")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--slots", type=int, default=2)
    sp.add_argument("--opencode-version", default=hm.DEFAULT_OPENCODE)
    sp.add_argument("--pi-version", default=hm.DEFAULT_PI)
    sp.add_argument("--latest", action="store_true", help="keep the newest versions that ./setup installs")
    sp.set_defaults(fn=cmd_prepare)

    sp = sub.add_parser("run", help="run the matrix (resumable: a run with a result is skipped)")
    common(sp)
    sp.add_argument("--models", required=True, help="catalogue names, comma-separated")
    sp.add_argument("--clients", default="opencode,pi")
    sp.add_argument("--thinking", default="default,off", help="default, off or a level; comma-separated")
    sp.add_argument("--runs", type=int, default=2)
    sp.add_argument("--variant", default="baseline")
    sp.add_argument("--out", required=True, help="the results file (JSONL)")
    sp.add_argument("--categories", default="", help=f"some of: {', '.join(CATEGORIES)}")
    sp.add_argument("--prompts", default="", help="prompt ids, comma-separated")
    sp.add_argument("--with-extra", action="store_true", help="also the extra prompts (added after the baseline)")
    sp.add_argument("--full", action="store_true", help="full runs: the task to the end, then the hidden tests")
    sp.add_argument("--limit", type=float, default=0, help="seconds per run (default 600; full runs 1800)")
    sp.add_argument("--max-tools", type=int, default=ev.MAX_TOOLS,
                    help=f"tool calls with no decisive action before 'undecided' (default {ev.MAX_TOOLS})")
    sp.add_argument("--max-seconds", type=float, default=ev.MAX_SECONDS,
                    help=f"seconds from the first model step before 'undecided' (default {ev.MAX_SECONDS:.0f})")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--slots", type=int, default=2)
    sp.add_argument("--fetch", action="store_true", help="copy each model in from the library first")
    sp.add_argument("--drop", action="store_true", help="remove each model's local copy after its batch")
    sp.add_argument("--keep", default="", help="more model files that --drop never removes")
    sp.add_argument("--no-server", action="store_true",
                    help="use the server that the HOME's configs point to (no start, no config refresh)")
    sp.add_argument("--retry-errors", action="store_true", help="run again the cells whose result is an error")
    sp.add_argument("--keep-going", action="store_true", help="go on with the next model when a server fails")
    sp.add_argument("--keep-runs", action="store_true", help="keep the fixture copies of decision runs")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("report", help="the tables of a results file")
    sp.add_argument("results")
    sp.add_argument("--md", action="store_true", help="Markdown")
    sp.add_argument("--output", help="write to this file")
    sp.set_defaults(fn=cmd_report)

    for name, fn, text in (("fetch", cmd_fetch, "copy a model (and its drafter) from the library, SHA-256 checked"),
                           ("drop", cmd_drop, "remove a model's local copy (only with a verified library copy)")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("model")
        common(sp, with_home=False)
        if name == "drop":
            sp.add_argument("--keep", default="", help="more files that are never removed")
            sp.add_argument("--dry-run", action="store_true")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("tokens", help="count the coder briefs' tokens afterwards (the server's POST /tokenize)")
    sp.add_argument("results")
    sp.add_argument("--url", required=True, help="a server of the same model, for example http://127.0.0.1:8097")
    sp.add_argument("--key-file", default="", help="the server's API key file (the harness's: WORK/server/api-key)")
    sp.add_argument("--model", default="", help="only the lines of this model (the model the server runs)")
    sp.set_defaults(fn=cmd_tokens)

    sp = sub.add_parser("variants", help="list the variants")
    sp.set_defaults(fn=cmd_variants)
    return p


def _on_term(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    signal.signal(signal.SIGTERM, _on_term)          # stop as on Ctrl-C: the clients and the server too
    try:
        return int(a.fn(a))
    except (hm.HomeError, ServerError, library.FetchError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        proc.stop_all()
        print("stopped (the results so far are saved; run the same command again to go on)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

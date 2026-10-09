// @ts-check
// CARL: the chain of a test session and a code session (Phase 23.4.3, item 3; the decisions are in
// docs/design_and_analysis/agent-coding-decisions.md). Shared by Pi's subagent extension (it runs the two sessions
// itself) and OpenCode's carl-delegation (the task tool runs the test session; the plugin starts the code session);
// each carries a copy next to carl-brief.js, installed by client/configure.py.
//
// A coder task with mode = "code", tests = "new", no [error] / [[tried]] and not a continued task (testSessionDue):
//   1. the test session: a fresh coder session in mode test (testSessionBrief). It writes the tests and reports.
//   2. the red start: at least one new test fails before any code. The test session's report decides, unless CARL
//      ran a check's `run` itself: only a plain test runner or ruff on the project's files (allowedCheck), once,
//      with no shell, a time limit, in the project folder. Any other command is not run (one line in the result
//      says so). No red start: the code session still runs, and the result warns.
//   3. the freeze: the hashes of the project's test files after the test session.
//   4. the code session: a fresh coder session with the original brief (mode code) and CARL's [test_session] table
//      (the test files, the test session's report in short). Its gates refuse every test-file write.
//   5. one result for the main agent (chainText): the red start or the warning, "tests unchanged" or the changed
//      test files, the test session's report, the code session's report.
// tests = "existing" (Watch): one session; the test files that the checks name are hashed before and after it.
// Else: one session, as before. I/O: the project's test files (read and hashed) and the check commands (run).

import { spawn } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { changedFiles, checkBrief, hashFiles, isTestFile, namesTests, parseBrief, parseReport, testSessionBrief,
         testSessionDue, writeBrief, writeBriefJson } from "./carl-brief.js";

export const CHAIN_MARK = "[CARL] Chain";
export const WARN_MARK = "[CARL] Warning";
export const RUN_MS = 120_000;                               // a check command's time limit
const OUTPUT_CAP = 4_000;                                    // the output of a check command that CARL keeps
const REPORT_CAP = 30_000;                                   // each session's report in the one result
const MAX_FILES = 20_000;                                    // the files that the test-file walk looks at
const SKIP_DIRS = new Set([".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache",
                           ".pytest_cache", ".ruff_cache", ".tox", ".nox", "dist", "build", "target", ".next", ".cache",
                           "coverage", ".idea", ".vscode"]);

/**
 * @typedef {import("./carl-brief.js").Brief} Brief
 * @typedef {{ code: number | null, timedOut: boolean, output: string, refused?: string }} RunOutcome
 * @typedef {(run: string, cwd: string, ms: number) => Promise<RunOutcome>} RunCheck
 * @typedef {{ run: string, result: "pass" | "fail" | "not run", code: number | null, output: string,
 *             refused?: string, timedOut?: boolean }} CheckRun
 * @typedef {"toml" | "json"} Format the brief's format: the chain writes its sessions' briefs in the same one
 * @typedef {{ kind: "chain", brief: Brief, format: Format } | { kind: "watch", brief: Brief, format: Format }
 *          | { kind: "one", brief: Brief | null, format: Format }} Plan
 */

/**
 * What CARL does with a coder task: "chain" (a test session, then a code session), "watch" (one session; the
 * test files that its checks name are hashed before and after), or "one" (one session, as before). A text that is
 * no complete brief is "one" (the brief check deals with it).
 * @param {string} taskText @param {boolean} [continued] the task continues an earlier one (OpenCode's task_id)
 * @returns {Plan}
 */
export function coderPlan(taskText, continued = false) {
  const { brief, format: f } = parseBrief(taskText);
  const format = f === "json" ? "json" : "toml";
  if (!brief || checkBrief(brief).length) return { kind: "one", brief: null, format };
  if (testSessionDue(brief, continued)) return { kind: "chain", brief, format };
  if (!continued && brief.mode === "code" && brief.tests === "existing") return { kind: "watch", brief, format };
  return { kind: "one", brief, format };
}

// ------------------------------------------------------------------ the project's test files

/** A path relative to the project folder, with "/". @param {string} root @param {string} p */
function rel(root, p) {
  return relative(root, isAbsolute(p) ? p : resolve(root, p)).split(sep).join("/");
}

/**
 * The test files (isTestFile) under a folder of the project, relative to the project, sorted. Tool and cache
 * folders (.git, node_modules, .venv, ...) are left out; at most MAX_FILES entries are looked at.
 * @param {string} root the project folder @param {string} [under] a folder in it (default: all of it)
 * @returns {string[]}
 */
export function projectTestFiles(root, under = root) {
  /** @type {string[]} */
  const out = [];
  let seen = 0;
  /** @param {string} dir */
  const walk = (dir) => {
    let names;
    try {
      names = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const d of names) {
      if (++seen > MAX_FILES) return;
      const p = join(dir, d.name);
      if (d.isDirectory()) {
        if (!SKIP_DIRS.has(d.name)) walk(p);
      } else if (d.isFile()) {
        const r = rel(root, p);
        if (isTestFile(r)) out.push(r);
      }
    }
  };
  walk(isAbsolute(under) ? under : resolve(root, under));
  return out.sort();
}

/**
 * The test files that a brief's checks name (tests = "existing"): a test file in a check's run, or the test files
 * under a folder that a run names (tests/, a path in tests/). None found: all the project's test files.
 * @param {Brief} b @param {string} root @returns {string[]}
 */
export function namedTestFiles(b, root) {
  const out = new Set();
  for (const c of b.checks) {
    if (!namesTests(c.run)) continue;
    for (const w of c.run.split(/[\s"'=]+/)) {
      const p = w.replace(/::.*$/, "").replace(/^\.\//, "").replace(/\/+$/, "");
      if (!p || p.startsWith("-")) continue;
      let st;
      try {
        st = statSync(resolve(root, p));
      } catch {
        continue;
      }
      const r = rel(root, p);
      if (!r || r.startsWith("..")) continue;
      if (st.isFile() && isTestFile(r)) out.add(r);
      else if (st.isDirectory()) for (const f of projectTestFiles(root, r)) out.add(f);
    }
  }
  return out.size ? [...out].sort() : projectTestFiles(root);
}

// ------------------------------------------------------------------ the check commands

// The commands that CARL runs itself for the red start (user, 2026-10-09: "C for checks but I would like to support
// ruff as well"): a plain test runner or ruff on the project's files. Any other command is not run: the test
// session's report decides. CARL parses the command itself and starts the program with no shell:
//   - no shell syntax: none of ; & | < > ` $ ( ) \ * ? [ ] { } ~ ! # and no line break; quotes ('...' or "...")
//     only group words (with $ and ` refused, the two quotes mean the same);
//   - KEY=value words first (as a shell would read them) for the names in SAFE_ENV only;
//   - an optional `uv run` (followed by the runner straight away) or `uvx` (pytest or ruff);
//   - the runner: pytest, python -m pytest, python3 -m pytest, node --test, npm test, npm run test, go test,
//     cargo test, ruff check (no --fix), ruff format --check;
//   - then flags (-x, --name, --name=VALUE) and words; every word, and every value, is a path in the project
//     (an absolute path or a path with .. must stay in the project folder); a few flags that run other programs
//     or write elsewhere are refused (NO_FLAGS).
// It runs in the project folder, with the time limit RUN_MS.

/** The environment names that a check's KEY=value words may set. */
export const SAFE_ENV = new Set(["CI", "NO_COLOR", "FORCE_COLOR", "TERM", "TZ", "LANG", "LC_ALL", "PYTHONPATH",
                                 "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "PYTHONUNBUFFERED", "PYTHONWARNINGS",
                                 "NODE_ENV", "RUST_BACKTRACE", "RUST_LOG", "RUST_TEST_THREADS", "CGO_ENABLED"]);
const SHELL_CHARS = /[;&|<>`$()\\*?[\]{}~!#\n\r]/;
const WORD = /^[A-Za-z0-9_.,:@%+=\/ -]*$/;                  // a word's characters (a space only from quotes)
/** The flags that a runner must not get: they run another program, load code from elsewhere, or write files. */
const NO_FLAGS = {
  node: ["-e", "--eval", "-p", "--print", "-r", "--require", "--import", "--loader", "--experimental-loader", "-i", "--interactive"],
  go: ["-exec", "--exec", "-toolexec", "--toolexec", "-overlay", "--overlay"],
  cargo: ["--config", "-Z"],
  ruff: ["--fix", "--fix-only", "--unsafe-fixes", "--add-noqa", "--watch", "-w"],
  npm: ["--prefix", "-C", "--script-shell", "--userconfig", "--globalconfig"],
  pytest: [],
};

/**
 * The words of a command, as a shell would split it (quotes group words). null: shell syntax that CARL does not
 * run (see SHELL_CHARS) or a quote that does not end.
 * @param {string} run @returns {string[] | null}
 */
export function commandWords(run) {
  const s = String(run ?? "").trim();
  if (!s || SHELL_CHARS.test(s)) return null;
  /** @type {string[]} */
  const out = [];
  let cur = "";
  let has = false;
  /** @type {string} */
  let quote = "";
  for (const ch of s) {
    if (quote) {
      if (ch === quote) quote = "";
      else cur += ch;
    } else if (ch === "'" || ch === '"') {
      quote = ch;
      has = true;
    } else if (/\s/.test(ch)) {
      if (has) out.push(cur);
      cur = "";
      has = false;
    } else {
      cur += ch;
      has = true;
    }
  }
  if (quote) return null;
  if (has) out.push(cur);
  return out;
}

/** A word (or a flag's value) is in the project: each path in it (split at = , : and spaces) that is absolute or
 * has .. stays in the project folder. @param {string} w @param {string} root */
function inProject(w, root) {
  if (!WORD.test(w)) return false;
  const top = resolve(root);
  return w.split(/[=,:\s]+/).every((piece) => {
    if (!piece) return true;
    if (!isAbsolute(piece) && !piece.split("/").includes("..")) return true;
    const r = relative(top, resolve(top, piece));
    return r === "" || (!r.startsWith("..") && !isAbsolute(r));
  });
}

/**
 * @typedef {"pytest" | "node" | "npm" | "go" | "cargo" | "ruff"} Runner
 * @typedef {{ ok: true, argv: string[], env: Record<string, string>, runner: Runner } | { ok: false, why: string }} Allowed
 */

/**
 * Whether CARL runs a check's command itself for the red start, and how (the program's argv and the KEY=value
 * words; no shell). See the rules above.
 * @param {string} run @param {string} root the project folder @returns {Allowed}
 */
export function allowedCheck(run, root) {
  const no = (/** @type {string} */ why) => ({ ok: /** @type {false} */ (false), why });
  const words = commandWords(run);
  if (!words) return no("shell syntax");
  /** @type {Record<string, string>} */
  const env = {};
  let i = 0;
  for (let m; i < words.length && (m = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/s.exec(words[i])); i++) {
    if (!SAFE_ENV.has(m[1])) return no(`the variable ${m[1]}`);
    if (!inProject(m[2], root)) return no(`the value of ${m[1]}`);
    env[m[1]] = m[2];
  }
  let w = words.slice(i);
  /** @type {string[]} */
  let prefix = [];
  if (w[0] === "uv" && w[1] === "run") {
    prefix = w.slice(0, 2);
    w = w.slice(2);
  } else if (w[0] === "uvx") {
    if (w[1] !== "pytest" && w[1] !== "ruff") return no("uvx with another tool");
    prefix = w.slice(0, 1);
    w = w.slice(1);
  }
  /** @type {Runner | ""} */
  let runner = "";
  let n = 0;                                                  // the runner's own words
  const [a, b, c] = w;
  if (a === "pytest") [runner, n] = ["pytest", 1];
  else if ((a === "python" || a === "python3") && b === "-m" && c === "pytest") [runner, n] = ["pytest", 3];
  else if (a === "node" && b === "--test") [runner, n] = ["node", 2];
  else if (a === "npm" && b === "test") [runner, n] = ["npm", 2];
  else if (a === "npm" && b === "run" && c === "test") [runner, n] = ["npm", 3];
  else if (a === "go" && b === "test") [runner, n] = ["go", 2];
  else if (a === "cargo" && b === "test") [runner, n] = ["cargo", 2];
  else if (a === "ruff" && b === "check") [runner, n] = ["ruff", 2];
  else if (a === "ruff" && b === "format" && w.slice(2).includes("--check")) [runner, n] = ["ruff", 2];
  if (!runner) return no("not a plain test runner or ruff");
  for (const arg of w.slice(n)) {
    if (arg.startsWith("-")) {
      const eq = arg.indexOf("=");
      const name = eq < 0 ? arg : arg.slice(0, eq);
      if (NO_FLAGS[runner].includes(name)) return no(`the flag ${name}`);
      if (!/^--?[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name) && arg !== "-" && arg !== "--") return no(`the flag ${name}`);
      if (eq >= 0 && !inProject(arg.slice(eq + 1), root)) return no(`the value of ${name}`);
    } else if (!inProject(arg, root)) return no(`${arg} is not in the project`);
  }
  return { ok: true, argv: [...prefix, ...w], env, runner };
}

/**
 * What a runner's exit means for the red start: pass, fail, or not run (the runner itself is missing or stopped
 * with an error that is not a failed test). @param {Runner | ""} runner @param {number | null} code
 * @param {string} output @returns {"pass" | "fail" | "not run"}
 */
export function exitMeaning(runner, code, output) {
  if (code === null) return "not run";
  if (code === 0) return "pass";
  if (/No module named pytest|command not found|Missing script: "?test|ENOENT/.test(output)) return "not run";
  if (runner === "pytest") {                                   // 1: failed; 2 with a collection error: the tests
    if (code === 1 || (code === 2 && /errors? during collection|ERROR collecting/.test(output))) return "fail";
    return "not run";                                          // cannot import the code yet. Else an error (2-4)
  }                                                            // or no test collected (5)
  if (runner === "ruff") return code === 1 ? "fail" : "not run";         // 2: ruff's own error
  return "fail";
}

/**
 * Run a program in the project folder (no shell), with a time limit; the output is cut to OUTPUT_CAP.
 * @param {string[]} argv @param {Record<string, string>} env added to this process's environment
 * @param {string} cwd @param {number} ms @returns {Promise<RunOutcome>}
 */
export function runArgv(argv, env, cwd, ms) {
  return new Promise((done) => {
    let output = "";
    let timedOut = false;
    const base = { ...process.env, ...env };
    delete base.NODE_TEST_CONTEXT;                              // a node --test of the project is its own run
    /** @type {import("node:child_process").ChildProcess} */
    let proc;
    try {
      proc = spawn(argv[0], argv.slice(1), { cwd, env: base, stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32" });
    } catch (e) {
      done({ code: null, timedOut: false, output: String(e) });
      return;
    }
    const keep = (/** @type {Buffer} */ b) => {
      if (output.length < OUTPUT_CAP * 2) output += b.toString();
    };
    proc.stdout?.on("data", keep);
    proc.stderr?.on("data", keep);
    const kill = () => {
      try {
        if (proc.pid && process.platform !== "win32") process.kill(-proc.pid, "SIGKILL");   // the whole group
        else proc.kill("SIGKILL");
      } catch { /* gone */ }
    };
    const timer = setTimeout(() => {
      timedOut = true;
      kill();
    }, ms);
    proc.on("error", (e) => {
      clearTimeout(timer);
      done({ code: null, timedOut: false, output: String(e) });
    });
    proc.on("close", (code) => {
      clearTimeout(timer);
      done({ code: timedOut ? null : code, timedOut, output: tail(output, OUTPUT_CAP) });
    });
  });
}

/**
 * Run one check command as CARL does for the red start: only when allowedCheck accepts it (else not run, with
 * the reason in `refused`), with no shell, in the project folder, with a time limit.
 * @type {RunCheck}
 */
export async function runCommand(run, cwd, ms) {
  const a = allowedCheck(run, cwd);
  if (!a.ok) return { code: null, timedOut: false, output: "", refused: a.why };
  return runArgv(a.argv, a.env, cwd, ms);
}

/** The end of a text, at most n characters. @param {string} s @param {number} n */
function tail(s, n) {
  return s.length <= n ? s : "…" + s.slice(s.length - n);
}

/** A text cut to n characters. @param {string} s @param {number} n */
function cut(s, n) {
  return s.length <= n ? s : s.slice(0, n) + "\n… (cut)";
}

/** Text for a TOML string in the brief: no ``` (a fence would end the brief's own fence). @param {string} s */
const plain = (s) => String(s ?? "").replace(/```/g, "'''").trim();

// ------------------------------------------------------------------ the chain

/**
 * The red start: from CARL's own runs of the checks when it ran one (a failing run before any code is the red
 * start), else from the test session's report (a failed check or a finding).
 * @param {import("./carl-brief.js").Report | null} report @param {CheckRun[]} runs
 * @returns {{ red: boolean, why: string }}
 */
export function redStart(report, runs) {
  const ran = runs.filter((r) => r.result !== "not run");
  const q = (/** @type {CheckRun[]} */ rs) => rs.map((r) => "`" + r.run + "`").join(", ");
  if (ran.length) {
    const failed = ran.filter((r) => r.result === "fail");
    return failed.length
      ? { red: true, why: `CARL ran ${q(failed)} before the code: it failed (exit ${failed.map((r) => r.code).join(", ")}).` }
      : { red: false, why: `CARL ran ${q(ran)} before the code: it passed.` };
  }
  const failing = report ? report.checks.filter((c) => c.result === "fail").length + report.findings.length : 0;
  if (failing) return { red: true, why: "the test session's report has failing tests." };
  return { red: false, why: report ? "the test session's report has no failing test." : "the test session gave no report." };
}

/**
 * The line about the checks that CARL did not run itself ("" when it ran them all).
 * @param {CheckRun[]} runs @returns {string}
 */
export function notRunLine(runs) {
  const q = (/** @type {CheckRun[]} */ rs) => rs.map((r) => "`" + r.run + "`").join(", ");
  const refused = runs.filter((r) => r.refused);
  const failed = runs.filter((r) => !r.refused && r.result === "not run");
  const parts = [];
  if (refused.length) parts.push(`CARL did not run ${q(refused)} itself (it runs only a plain test runner or ruff on the project's files).`);
  if (failed.length) parts.push(`CARL could not run ${q(failed)} (${failed.some((r) => r.timedOut) ? "the time limit" : "no runner, or an error of the runner"}).`);
  return parts.join(" ");
}

/** One session's outcome: ok (it ran to its end) and its final message (or the error). */
/** @typedef {{ ok: boolean, output: string }} SessionOutcome */

/**
 * One chain: build it with the brief (coderPlan's), then testTask() before the test session, afterTest() with its
 * final message (it gives the code session's task), and result() with the code session's final message. o.format:
 * the format of the main agent's brief (coderPlan's); the sessions' briefs are written in it (default TOML).
 */
export class Chain {
  /** @param {Brief} brief @param {string} cwd the project folder
   *  @param {{ runCheck?: RunCheck, runMs?: number, format?: Format }} [o] */
  constructor(brief, cwd, o = {}) {
    this.brief = brief;
    this.cwd = cwd;
    /** @type {Format} */
    this.format = o.format === "json" ? "json" : "toml";
    this.write = o.format === "json" ? writeBriefJson : writeBrief;
    this.runCheck = o.runCheck ?? runCommand;
    this.runMs = o.runMs ?? RUN_MS;
    /** @type {Record<string, string | null>} */
    this.before = {};
    /** @type {Record<string, string | null>} the test files after the test session (the freeze) */
    this.frozen = {};
    /** @type {string[]} the test files that the test session wrote */
    this.written = [];
    /** @type {CheckRun[]} */
    this.runs = [];
    this.red = { red: false, why: "" };
    this.testOutput = "";
    this.testOk = true;
  }

  /** The test session's task (a brief in mode test). Notes the project's test files first. */
  testTask() {
    this.before = hashFiles(projectTestFiles(this.cwd), this.cwd);
    return this.write(testSessionBrief(this.brief));
  }

  /** The commands to run for the red start: the checks whose run names tests or a test file the session wrote;
   * when none does, every check's run. Each command once. */
  commands() {
    const runs = [...new Set(this.brief.checks.map((c) => c.run).filter(Boolean))];
    const tests = runs.filter((r) => namesTests(r) || this.written.some((f) => r.includes(f)));
    return tests.length ? tests : runs;
  }

  /**
   * After the test session: the files it wrote, the red start (CARL runs the checks), the freeze. Gives the code
   * session's task: the original brief and CARL's [test_session] table.
   * @param {SessionOutcome} test @returns {Promise<string>}
   */
  async afterTest(test) {
    this.testOk = test.ok;
    this.testOutput = test.output;
    const now = hashFiles(projectTestFiles(this.cwd), this.cwd);
    const report = parseReport(test.output);
    const named = (report?.files ?? []).map((f) => f.path).filter((p) => isTestFile(p) && now[p]);
    this.written = [...new Set([...changedFiles(this.before, now).filter((p) => now[p]), ...named])].sort();
    this.frozen = now;
    this.runs = [];
    for (const run of this.commands()) {
      const ok = allowedCheck(run, this.cwd);
      if (!ok.ok) {                                            // not a plain test runner or ruff: the report decides
        this.runs.push({ run, code: null, output: "", result: "not run", refused: ok.why });
        continue;
      }
      const r = await this.runCheck(run, this.cwd, this.runMs);
      if (r.refused) {
        this.runs.push({ run, code: null, output: "", result: "not run", refused: r.refused });
        continue;
      }
      this.runs.push({ run, code: r.code, output: r.output, timedOut: r.timedOut,
                       result: r.timedOut ? "not run" : exitMeaning(ok.runner, r.code, r.output) });
    }
    this.red = redStart(report, this.runs);
    return this.write({
      ...this.brief,
      testSession: {
        files: this.written,
        summary: plain(report ? report.summary : cut(test.output, 1_500)),
        failing: (report?.findings ?? []).map((f) => ({ test: plain(f.test), requirement: plain(f.requirement), why: plain(f.why) })),
        notes: (report?.openIssues ?? []).map(plain),
      },
    });
  }

  /** The test files that differ from the freeze now (changed, made or removed). */
  changed() {
    return changedFiles(this.frozen, hashFiles(projectTestFiles(this.cwd), this.cwd));
  }

  /**
   * The one result for the main agent after the code session.
   * @param {SessionOutcome} code @returns {string}
   */
  result(code) {
    const changed = this.changed();
    const lines = [`${CHAIN_MARK}: the coder ran in two new sessions: first the tests (mode test), then the code (mode code).`];
    if (!this.red.red) {
      lines.push(`${WARN_MARK}: no new test failed before the code (no red start): ${this.red.why} The tests may not test the new behaviour: read them before you trust a pass.`);
    } else lines.push(`Red start: ${this.red.why}`);
    const notRun = notRunLine(this.runs);
    if (notRun) lines.push(notRun);
    if (!this.written.length) lines.push(`${WARN_MARK}: the test session wrote no test file.`);
    lines.push(changed.length
      ? `${WARN_MARK}: the code session changed these test files after the test session: ${changed.join(", ")}. Look at the change before you trust a pass.`
      : `Tests unchanged after the code session${this.written.length ? ` (${this.written.join(", ")})` : ""}.`);
    if (!code.ok) lines.push("The code session failed: its error is below.");
    lines.push("Run the checks yourself before you answer the user.");
    return [lines.join("\n"),
            `## The test session's report (mode test)\n\n${cut(this.testOutput.trim() || "(no output)", REPORT_CAP)}`,
            `## The code session's report (mode code)\n\n${cut(code.output.trim() || "(no output)", REPORT_CAP)}`].join("\n\n");
  }

  /**
   * The one result when the client stopped before the chain ended (OpenCode's held chain, delivered at its next
   * start): what exists. stage: where it stopped ("test": in the test session; "held": after it, before the code
   * session; "code": in the code session). code: what the code session wrote before it stopped, when known.
   * @param {"test" | "held" | "code"} stage @param {string} [code] @returns {string}
   */
  interrupted(stage, code = "") {
    const where = stage === "test" ? "the test session did not finish, and the code session did not run"
      : stage === "held" ? "the code session did not run" : "the code session did not finish";
    const parts = [`${CHAIN_MARK}: OpenCode stopped before the chain ended: ${where}. The project's files may hold part of the work: look at them and give the task to the coder again if it is not done.`];
    if (stage !== "test" && this.red.why) parts[0] += `\n${this.red.red ? "Red start" : `${WARN_MARK}: no red start`}: ${this.red.why}`;
    parts.push(`## The test session's report (mode test)\n\n${cut(this.testOutput.trim() || "(no report)", REPORT_CAP)}`);
    if (stage === "code") parts.push(`## The code session's last answer (mode code, unfinished)\n\n${cut(code.trim() || "(none)", REPORT_CAP)}`);
    return parts.join("\n\n");
  }

  /**
   * The one result when the test session ran in the background and its completion could not be held (OpenCode
   * without the hold): the code session did not run. @param {string} testText @returns {string}
   */
  unheld(testText) {
    this.testOutput = testText;
    return `${CHAIN_MARK}: the test session ran in the background, where CARL cannot hold its result on this OpenCode, so the code session did not run. Give the task to the coder again with tests = "existing" and the new tests in its checks.\n\n` +
      `## The test session's report (mode test)\n\n${cut(testText.trim() || "(no report)", REPORT_CAP)}`;
  }

  /** The chain's state as plain data (OpenCode's state file of a held chain); Chain.from reads it back. */
  snapshot() {
    return { brief: this.brief, cwd: this.cwd, format: this.format, before: this.before, frozen: this.frozen,
             written: this.written, runs: this.runs.map(({ output: _o, ...r }) => r), red: this.red,
             testOutput: this.testOutput, testOk: this.testOk };
  }

  /** A chain from its snapshot. @param {any} o @returns {Chain} */
  static from(o) {
    const c = new Chain(o.brief, o.cwd, { format: o.format });
    c.before = o.before ?? {};
    c.frozen = o.frozen ?? {};
    c.written = o.written ?? [];
    c.runs = (o.runs ?? []).map((/** @type {any} */ r) => ({ output: "", ...r }));
    c.red = o.red ?? { red: false, why: "" };
    c.testOutput = String(o.testOutput ?? "");
    c.testOk = o.testOk !== false;
    return c;
  }

  /** The one result when the test session failed: the code session did not run. @returns {string} */
  stopped() {
    return `${CHAIN_MARK}: the test session failed, so the code session did not run. Its error is below.\n\n` +
      `## The test session (mode test)\n\n${cut(this.testOutput.trim() || "(no output)", REPORT_CAP)}`;
  }
}

/** tests = "existing": the named test files, hashed before the session and compared after it. */
export class Watch {
  /** @param {Brief} brief @param {string} cwd */
  constructor(brief, cwd) {
    this.cwd = cwd;
    this.paths = namedTestFiles(brief, cwd);
    this.before = hashFiles(this.paths, cwd);
  }

  /** The line for the result: "tests unchanged" or the changed test files. */
  note() {
    const changed = changedFiles(this.before, hashFiles(this.paths, this.cwd));
    if (!this.paths.length) return "";
    return changed.length
      ? `${WARN_MARK}: the coder changed these test files: ${changed.join(", ")}. Look at the change before you trust a pass.`
      : `[CARL] Tests unchanged: ${this.paths.length > 8 ? `${this.paths.length} test files` : this.paths.join(", ")}.`;
  }
}

/**
 * Run a coder task as CARL does (Pi's subagent tool; the OpenCode plugin runs the same steps around the task tool).
 * run(taskText) runs one fresh coder session. The result: kind (coderPlan's), ok, and text: the one result for the
 * main agent (for "one", the session's own output).
 * @param {string} taskText @param {string} cwd
 * @param {(taskText: string, step: "test" | "code" | "one") => Promise<SessionOutcome>} run
 * @param {{ runCheck?: RunCheck, runMs?: number, chain?: boolean }} [o] chain: false runs every task as one session
 * @returns {Promise<{ kind: Plan["kind"], ok: boolean, text: string }>}
 */
export async function runCoderTask(taskText, cwd, run, o = {}) {
  const plan = coderPlan(taskText);
  if (plan.kind === "chain" && o.chain !== false) {
    const chain = new Chain(plan.brief, cwd, { ...o, format: plan.format });
    const test = await run(chain.testTask(), "test");
    if (!test.ok) {
      chain.testOutput = test.output;
      return { kind: "chain", ok: false, text: chain.stopped() };
    }
    const code = await run(await chain.afterTest(test), "code");
    return { kind: "chain", ok: code.ok, text: chain.result(code) };
  }
  if (plan.kind === "watch") {
    const watch = new Watch(plan.brief, cwd);
    const one = await run(taskText, "one");
    const note = watch.note();
    return { kind: "watch", ok: one.ok, text: note ? `${one.output}\n\n${note}` : one.output };
  }
  const one = await run(taskText, "one");
  return { kind: "one", ok: one.ok, text: one.output };
}

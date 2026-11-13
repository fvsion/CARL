// @ts-check
// CARL's program starts, on any OS (Phase W, W7.4). carl-chain.js (the project's checks and suites),
// carl-panel.js (carl-sync.py) and carl-cache.js (zstd) start programs through it; each plugin or extension folder that
// needs it carries a copy (client/shared/manifest.json).
//
// macOS and Linux: the program is started by its name, as before (node finds it on PATH); nothing changes.
// Windows:
//   - PATH is split at path.delimiter (";"), and a name without an extension is looked up with each PATHEXT extension
//     (npm -> npm.cmd, pytest -> pytest.exe).
//   - python3 and python: a python3.exe or python.exe on PATH that is not the Microsoft Store's alias (WindowsApps),
//     else the py launcher with -3.
//   - a .cmd or .bat file (npm.cmd, npx.cmd) only runs through cmd.exe (node refuses it without a shell): CARL runs
//     cmd.exe /d /s /c with each word quoted and cmd's special characters escaped, so a word stays one word.
//   - every program starts with windowsHide (no console window flashes up).
// carl-sync.py runs with the interpreter the setup recorded (client-sync.json "python"), else python3 as above.

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import * as path from "node:path";

/**
 * @typedef {{ platform?: string, env?: Record<string, string | undefined>, isFile?: (p: string) => boolean }} ProcOptions
 *   platform and env: the computer's own by default (a test passes others); isFile: is a file there (existsSync)
 * @typedef {{ command: string, args: string[], options: { windowsHide: boolean, windowsVerbatimArguments?: boolean } }} SpawnPlan
 */

const PATHEXT = ".COM;.EXE;.BAT;.CMD";                      // Windows' default, when the variable is not set
const STORE_ALIAS = /[\\/]Microsoft[\\/]WindowsApps[\\/]?$/i; // the Store's python3.exe opens the Store
const CMD_SPECIAL = /([()\][%!^"`<>&|;, *?])/g;             // the characters cmd.exe reads, escaped with ^

/** A variable of env; on Windows its name in any case (Path, PATH). @param {Record<string, string | undefined>} env
 * @param {string} name @param {string} platform */
function envValue(env, name, platform) {
  if (env[name] !== undefined || platform !== "win32") return env[name];
  const key = Object.keys(env).find((k) => k.toUpperCase() === name);
  return key === undefined ? undefined : env[key];
}

/** @param {ProcOptions} o */
function settings(o) {
  const platform = o.platform ?? process.platform;
  return { platform, env: o.env ?? process.env, isFile: o.isFile ?? existsSync,
           p: platform === "win32" ? path.win32 : path.posix };
}

/** The folders on PATH, in order (split at the platform's path.delimiter).
 * @param {ProcOptions} [o] @returns {string[]} */
export function pathDirs(o = {}) {
  const { platform, env, p } = settings(o);
  return String(envValue(env, "PATH", platform) ?? "").split(p.delimiter)
    .map((d) => (platform === "win32" ? d.trim().replace(/^"(.*)"$/, "$1") : d)).filter(Boolean);
}

/** The extensions Windows tries for a program name (PATHEXT, lower case).
 * @param {ProcOptions} [o] @returns {string[]} */
export function pathExts(o = {}) {
  const { platform, env } = settings(o);
  return String(envValue(env, "PATHEXT", platform) || PATHEXT).split(";").map((e) => e.trim().toLowerCase())
    .filter((e) => e.startsWith("."));
}

/**
 * The program's file on PATH ("" if none): on Windows with each PATHEXT extension unless the name has one.
 * A name with a separator is checked as it is. @param {string} cmd
 * @param {ProcOptions & { skip?: (dir: string) => boolean }} [o] skip: folders not to look in
 * @returns {string}
 */
export function findProgram(cmd, o = {}) {
  const { platform, isFile, p } = settings(o);
  if (!cmd) return "";
  let names = [cmd];
  if (platform === "win32") {
    const exts = pathExts(o);
    if (!exts.includes(p.extname(cmd).toLowerCase())) names = exts.map((e) => cmd + e);
  }
  if ((platform === "win32" ? /[\\/]/ : /\//).test(cmd)) return names.find((n) => isFile(n)) ?? "";
  for (const dir of pathDirs(o)) {
    if (o.skip?.(dir)) continue;
    for (const n of names) {
      const full = p.join(dir, n);
      if (isFile(full)) return full;
    }
  }
  return "";
}

/** Is a program on PATH? @param {string} cmd @param {ProcOptions} [o] */
export function onPath(cmd, o = {}) {
  return findProgram(cmd, o) !== "";
}

/**
 * The argv to start: unchanged on macOS and Linux; on Windows the program's file found on PATH (with its PATHEXT
 * extension), and python3 or python as a real interpreter or `py -3`. A program that is not found stays as it is
 * (the start then fails as it would have).
 * @param {string[]} argv @param {ProcOptions} [o] @returns {string[]}
 */
export function resolveArgv(argv, o = {}) {
  const { platform } = settings(o);
  if (platform !== "win32" || !argv.length) return [...argv];
  const [cmd, ...rest] = argv;
  if (cmd === "python3" || cmd === "python") {
    const real = { ...o, skip: (/** @type {string} */ d) => STORE_ALIAS.test(d) };
    for (const name of cmd === "python3" ? ["python3", "python"] : ["python"]) {
      const full = findProgram(name, real);
      if (full) return [full, ...rest];
    }
    const py = findProgram("py", real);
    if (py) return [py, "-3", ...rest];
    return [...argv];
  }
  const full = findProgram(cmd, o);
  return full ? [full, ...rest] : [...argv];
}

/** A word for cmd.exe /s /c: in quotes (MSVCRT rules for the program that reads it), cmd's characters escaped.
 * @param {string} word */
export function cmdWord(word) {
  const quoted = `"${String(word).replace(/(\\*)"/g, '$1$1\\"').replace(/(\\*)$/, "$1$1")}"`;
  return quoted.replace(CMD_SPECIAL, "^$1");
}

/**
 * How to start argv: the command, its arguments and the spawn options CARL adds (windowsHide; for a .cmd or .bat on
 * Windows, cmd.exe with the words quoted, windowsVerbatimArguments).
 * @param {string[]} argv @param {ProcOptions} [o] @returns {SpawnPlan}
 */
export function spawnPlan(argv, o = {}) {
  const { platform, env } = settings(o);
  const [cmd, ...rest] = resolveArgv(argv, o);
  if (platform === "win32" && /\.(?:cmd|bat)$/i.test(cmd)) {
    const line = [cmd.replace(CMD_SPECIAL, "^$1"), ...rest.map(cmdWord)].join(" ");
    return { command: envValue(env, "COMSPEC", platform) || "cmd.exe", args: ["/d", "/s", "/c", `"${line}"`],
             options: { windowsHide: true, windowsVerbatimArguments: true } };
  }
  return { command: cmd, args: rest, options: { windowsHide: true } };
}

/**
 * Start argv (no shell of its own) with node's spawn options; see spawnPlan.
 * @param {string[]} argv @param {import("node:child_process").SpawnOptions} [options] @param {ProcOptions} [o]
 * @returns {import("node:child_process").ChildProcess}
 */
export function spawnProgram(argv, options = {}, o = {}) {
  const plan = spawnPlan(argv, o);
  return spawn(plan.command, plan.args, { ...options, ...plan.options });
}

/**
 * Run argv to the end (spawnSync), as spawnProgram starts it.
 * @param {string[]} argv @param {import("node:child_process").SpawnSyncOptions} [options] @param {ProcOptions} [o]
 */
export function runProgramSync(argv, options = {}, o = {}) {
  const plan = spawnPlan(argv, o);
  return spawnSync(plan.command, plan.args, { ...options, ...plan.options });
}

/**
 * Stop a program and what it started: its process group on macOS and Linux (when it was started detached), the
 * process tree on Windows (taskkill /T: npm.cmd runs cmd.exe, which runs node).
 * @param {import("node:child_process").ChildProcess} proc @param {{ group?: boolean, platform?: string }} [o]
 */
export function killTree(proc, o = {}) {
  const platform = o.platform ?? process.platform;
  try {
    if (proc.pid && platform === "win32") {
      const r = spawnSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
      if (r.status === 0) return;
    } else if (proc.pid && o.group) {
      process.kill(-proc.pid, "SIGKILL");                    // the whole group
      return;
    }
    proc.kill("SIGKILL");
  } catch { /* gone */ }
}

/**
 * The interpreter that runs carl-sync.py: the one the setup recorded (client-sync.json "python", when that file is
 * there), else python3 (resolveArgv). @param {{ [key: string]: unknown }} state client-sync.json
 * @param {ProcOptions} [o] @returns {string[]} the argv's first words
 */
export function syncPython(state, o = {}) {
  const { isFile } = settings(o);
  const rec = state?.python;
  if (typeof rec === "string" && rec && isFile(rec)) return [rec];
  return resolveArgv(["python3"], o);
}

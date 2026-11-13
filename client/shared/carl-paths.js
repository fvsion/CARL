// @ts-check
// CARL's paths: they may come from Windows ("C:\Users\me\proj", "tests\x.py") or from macOS and Linux
// ("/Users/me/proj"). The clients and the model print the paths of the computer they run on; the shared code splits
// them with either separator (Phase W, W7.4). carl-chain.js, carl-cache.js and the OpenCode panel's tui.js use it; each plugin or
// extension folder that needs it carries a copy (client/shared/manifest.json).
//
// A POSIX path never holds a "\" as a separator, so a path without one splits the same as before: the Mac and Linux
// results do not change.

import * as path from "node:path";

/** Either separator. */
export const SEPARATORS = /[\\/]/;

/** The parts of a path, split at "/" and "\". @param {string} p @returns {string[]} */
export function pathParts(p) {
  return String(p ?? "").split(SEPARATORS);
}

/** Does the path go up a folder (a ".." part, with either separator)? @param {string} p */
export function goesUp(p) {
  return pathParts(p).includes("..");
}

/** Where the last part of a path starts: the index after its last "/" or "\" (0: none). @param {string} p */
export function lastPartAt(p) {
  const s = String(p ?? "");
  return Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\")) + 1;
}

/** Is this a Windows path: a drive ("C:\", "C:/") or a share ("\\server\share")? @param {string} p */
export function isWindowsPath(p) {
  return /^(?:[A-Za-z]:[\\/]|\\\\)/.test(String(p ?? ""));
}

/** Is this the top of a file system ("/", "C:\", "C:/", "C:")? @param {string} p */
export function isFsRoot(p) {
  return /^(?:\/|[A-Za-z]:[\\/]?)$/.test(String(p ?? ""));
}

/**
 * Is p the folder root or a path in it? A Windows root and the path are compared with either separator; a POSIX
 * root only with "/".
 * @param {string} p @param {string} root
 */
export function isUnder(p, root) {
  if (p === root) return true;
  if (!isWindowsPath(root)) return p.startsWith(root.endsWith("/") ? root : root + "/");
  const back = (/** @type {string} */ s) => s.replace(/\//g, "\\");
  const top = back(root).replace(/\\$/, "");
  return back(p) === top || back(p).startsWith(top + "\\");
}

/**
 * node:path for a platform: path.win32 on Windows, path.posix elsewhere (a test can ask for the other one).
 * @param {string} [platform] @returns {path.PlatformPath}
 */
export function pathFor(platform = process.platform) {
  return platform === "win32" ? path.win32 : path.posix;
}

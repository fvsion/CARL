"""The programs the setup asks and runs by name on PATH: node and npm, OpenCode and Pi (the bash setup's
`command -v`, `X --version` and `npm install -g --prefix ~/.local`). On Windows the names find their .cmd files
(PATHEXT, through shutil.which).

A program's output goes to the setup's streams: straight through when a stream is a real file (npm's progress shows
as it comes), else read and written there after the program ends.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import IO, List, Mapping, Optional

VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def fileno(stream: IO[str]) -> Optional[int]:
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None


class Programs:
    """environ: the environment of the programs (its PATH finds them); out and err: where their output goes."""

    def __init__(self, environ: Mapping[str, str], out: IO[str], err: IO[str]) -> None:
        self.environ = dict(environ)
        self.out = out
        self.err = err

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name, path=self.environ.get("PATH", ""))

    def output(self, argv: List[str], stderr: bool = False) -> Optional[subprocess.CompletedProcess]:
        """A program's run with its stdout read (stderr: also its errors; else they go nowhere); None when it does
        not start."""
        exe = self.which(argv[0]) or argv[0]
        try:
            return subprocess.run([exe, *argv[1:]], env=self.environ, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE if stderr else subprocess.DEVNULL,
                                  encoding="utf-8", errors="replace", timeout=300)
        except (OSError, subprocess.SubprocessError):
            return None

    def version_of(self, name: str) -> str:
        """The first X.Y.Z in `NAME --version` ("" when there is none)."""
        p = self.output([name, "--version"])
        m = VERSION_RE.search(p.stdout) if p is not None else None
        return m.group(0) if m else ""

    def node_version(self) -> str:
        """node -p process.versions.node ("" when it does not run)."""
        p = self.output(["node", "-p", "process.versions.node"])
        return p.stdout.strip() if p is not None and p.returncode == 0 else ""

    def run(self, argv: List[str]) -> int:
        """Run a program with its output on the streams; its exit code (127 when it does not start)."""
        exe = self.which(argv[0]) or argv[0]
        for s in (self.out, self.err):
            s.flush()
        outs = (fileno(self.out), fileno(self.err))
        try:
            if None not in outs:
                return subprocess.run([exe, *argv[1:]], env=self.environ, stdin=subprocess.DEVNULL,
                                      stdout=outs[0], stderr=outs[1]).returncode
            p = subprocess.run([exe, *argv[1:]], env=self.environ, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        except OSError as e:
            self.err.write(f"{argv[0]}: {e.strerror or e}\n")
            return 127
        self.out.write(p.stdout)
        self.err.write(p.stderr)
        return p.returncode

    def npm_install(self, prefix: str, pkgs: List[str]) -> int:
        return self.run(["npm", "install", "-g", "--prefix", prefix, *pkgs])

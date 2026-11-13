"""Node from nodejs.org for this user, when the computer has none or an older one (the bash setup's
download, unchanged): the release line's SHASUMS256.txt, the archive for this OS and CPU named there, its SHA-256
checked against that list, unpacked into ~/.local/lib/nodejs (the folder before it is replaced only when the new one
is whole), and node, npm and npx linked into ~/.local/bin.

macOS and Linux take the .tar.xz (unpacked with tar, as before). Windows takes the .zip (unpacked here, its top
folder left out) and has npm's Windows layout: the programs are in the prefix itself, not in PREFIX/bin (npm puts
opencode.cmd and pi.cmd there), so node.exe goes there (a hard link, else a copy: a symbolic link needs a privilege),
with an npm.cmd and an npx.cmd that start the ones in lib\nodejs.
"""
from __future__ import annotations

import hashlib
import os
import platform as _platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from typing import IO, Callable, Optional

BASE = "https://nodejs.org/dist"
OSES = {"Linux": "linux", "Darwin": "darwin", "Windows": "win"}
CPUS = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64", "amd64": "x64"}     # platform.machine(), lower case
# npm.cmd and npx.cmd in the prefix on Windows: they run the ones in lib\nodejs, which find npm next to them
WIN_FORWARDER = '@"%~dp0lib\\nodejs\\{name}" %*\r\n'


class NodeError(Exception):
    """The download stopped; the message is the line for the user."""


def fetch(url: str, dest: str) -> None:
    """url into the file dest (urllib; an HTTP error or no answer raises OSError)."""
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


class NodeDist:
    """line: the release line (latest-v22.x); base: the download site (tests pass a local one); system and machine:
    platform.system() and platform.machine(); fetch: url -> file."""

    def __init__(self, line: str, base: str = BASE, system: Optional[str] = None, machine: Optional[str] = None,
                 get: Callable[[str, str], None] = fetch) -> None:
        self.line = line
        self.base = base
        self.system = system if system is not None else _platform.system()
        self.machine = machine if machine is not None else _platform.machine()
        self.get = get

    def target(self, need: str) -> str:
        """os-arch of this computer; NodeError naming what is missing."""
        os_name = OSES.get(self.system)
        if os_name is None:
            raise NodeError(f"error: unsupported OS {self.system}: install Node {need} or newer yourself, then run "
                            f"this again")
        arch = CPUS.get(self.machine.lower())
        if arch is None:
            raise NodeError(f"error: unsupported CPU {self.machine}: install Node {need} or newer yourself, then run "
                            f"this again")
        return f"{os_name}-{arch}"

    def install(self, need: str, prefix: str, out: IO[str]) -> str:
        """Download, check and unpack Node into PREFIX/lib/nodejs and link it into PREFIX/bin; the folder."""
        target = self.target(need)
        base = f"{self.base}/{self.line}"
        tmp = tempfile.mkdtemp()
        try:
            sums = os.path.join(tmp, "SHASUMS256.txt")
            self.get(f"{base}/SHASUMS256.txt", sums)
            with open(sums, encoding="utf-8", errors="replace") as f:
                text = f.read()
            windows = target.startswith("win-")
            m = re.search(r"node-v[0-9.]+-" + re.escape(target) + (r"\.zip" if windows else r"\.tar\.xz"), text)
            if not m:
                raise NodeError(f"error: no {target} {'zip' if windows else 'tarball'} in {base}")
            tarball = m.group(0)
            out.write(f"Downloading {tarball}\n")
            out.flush()
            path = os.path.join(tmp, tarball)
            self.get(f"{base}/{tarball}", path)
            want = [ln.split()[0] for ln in text.splitlines() if ln.endswith(" " + tarball) and ln.split()]
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if not want or want[0].lower() != h.hexdigest():
                out.write(f"{tarball}: FAILED\n")
                raise NodeError("error: the SHA-256 sum of the Node download does not match SHASUMS256.txt")
            out.write(f"{tarball}: OK\n")
            dest = os.path.join(prefix, "lib", "nodejs")
            shutil.rmtree(dest + ".new", ignore_errors=True)
            os.makedirs(dest + ".new")
            if windows:
                unzip(path, dest + ".new", tarball)
            else:
                p = subprocess.run(["tar", "-xJf", path, "-C", dest + ".new", "--strip-components=1"],
                                   stdin=subprocess.DEVNULL)
                if p.returncode != 0:
                    raise NodeError(f"error: tar could not unpack {tarball}")
            shutil.rmtree(dest, ignore_errors=True)
            os.rename(dest + ".new", dest)
            if windows:
                link_windows(dest, prefix)
                return dest
            bindir = os.path.join(prefix, "bin")
            for b in ("node", "npm", "npx"):
                link = os.path.join(bindir, b)
                if os.path.lexists(link):
                    os.remove(link)
                os.symlink(os.path.join(dest, "bin", b), link)
            return dest
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def unzip(path: str, dest: str, name: str) -> None:
    """The zip's files into dest, without its top folder (node-vX-win-x64/); NodeError for a name that would land
    outside dest."""
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            parts = info.filename.replace("\\", "/").split("/")[1:]
            if not any(parts):
                continue                                       # the top folder itself
            if ".." in parts or ":" in info.filename or info.filename.startswith("/"):
                raise NodeError(f"error: {name} holds a file outside its folder: {info.filename}")
            target = os.path.join(dest, *[p for p in parts if p])
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def link_windows(dest: str, prefix: str) -> None:
    """node.exe into the prefix (a hard link, else a copy), and npm.cmd and npx.cmd there that start dest's."""
    exe = os.path.join(prefix, "node.exe")
    if os.path.lexists(exe):
        os.remove(exe)
    try:
        os.link(os.path.join(dest, "node.exe"), exe)
    except OSError:
        shutil.copy2(os.path.join(dest, "node.exe"), exe)
    for name in ("npm.cmd", "npx.cmd"):
        with open(os.path.join(prefix, name), "w", encoding="ascii", newline="") as f:
            f.write(WIN_FORWARDER.format(name=name))

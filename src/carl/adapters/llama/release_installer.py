"""The pinned llama.cpp release builds in the settings folder (implements LlamaReleaseInstaller, contract C-023).

install(backend) puts the build of the pinned release (domain/llama/release.py: the manifest data/llamacpp-release.json)
into <settings folder>/llama.cpp/bNNNNN-BACKEND/:

  1. A finished folder of this build is a no-op (its marker file names the files it was made from).
  2. Each file is downloaded with the Downloader port into <settings folder>/llama.cpp/.download/, which resumes a
     part file. It is checked against the manifest (size and SHA-256) and against GitHub's own digest of the file
     (best effort: no network answer is a note, not a failure). A file that differs is removed and refused.
  3. The archives unpack into a temp folder of their own. An entry that would leave the folder (an absolute path, a
     drive, "..", a link that points out, a device file) refuses the whole install and leaves nothing behind.
  4. The temp folder is renamed to the build's folder. Other builds stay (the previous one is kept).

A Windows zip has its files at the top; a Linux tarball has one folder (llama-bNNNNN) around them, which is removed,
so llama-server is always <build folder>/llama-server(.exe). The CUDA runtime unpacks into the same folder.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from typing import IO, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...domain.llama import release as rel
from ...domain.llama.release import Asset, Build, Release
from ...domain.shared.contracts import ensure, require
from ...domain.shared.errors import ConfigError
from ...domain.shared.units import file_size
from ...ports import Downloader

DOWNLOADS = ".download"                    # in the llama.cpp folder: the part files
MAX_UNPACKED = 12 * 2 ** 30                # an archive that unpacks to more than this is refused
CHUNK = 1 << 20
RENAME_TRIES = 6                           # Windows holds a new folder for a moment while it scans the files


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _installed_at(folder: str) -> float:
    """When a build folder was finished: its marker's time (the folder's, for one without a marker)."""
    for path in (os.path.join(folder, rel.MARKER), folder):
        try:
            return os.path.getmtime(path)
        except OSError:
            continue
    return 0.0


def builds_in(root: str) -> List[str]:
    """CARL's build folders in the llama.cpp folder (bNNNNN-BACKEND), the newest build first; of one build (cuda-13
    and cpu of b11738), the one installed last first."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = [(rel.folder_key(n), os.path.join(root, n)) for n in names]
    keyed = [(k, p) for k, p in found if k is not None and os.path.isdir(p)]
    return [p for k, p in sorted(keyed, key=lambda x: (-x[0][0], -_installed_at(x[1]), x[0][1]))]


def _remove_tree(path: str) -> None:
    def writable(func: Callable[[str], None], p: str, _err: object) -> None:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(p)
    shutil.rmtree(path, onerror=writable)


class GithubDigests:
    """GitHub's SHA-256 of each file of the release (the release API's "digest" fields): {name: sha256}, or {} when
    the answer cannot be read. url: the release's API address (the manifest's)."""

    def __init__(self, url: str, timeout: float = 20.0) -> None:
        self.url, self.timeout = url, timeout

    def __call__(self) -> Mapping[str, str]:
        req = urllib.request.Request(self.url, headers={"Accept": "application/vnd.github+json",
                                                        "User-Agent": "carl-engine-install"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                doc = json.load(resp)
            return {str(a["name"]): str(a["digest"])[len("sha256:"):] for a in doc.get("assets", [])
                    if str(a.get("digest", "")).startswith("sha256:")}
        except (OSError, ValueError, KeyError, AttributeError):
            return {}


class UrllibFetch:
    """A download with the standard library alone, for a computer with neither aria2c nor curl (implements
    Downloader, contract C-008): resumes a part file with a Range request. The exit code: 0, or 1 when it failed."""

    def __init__(self, out: Callable[[str], None] = lambda line: None) -> None:
        self.out = out

    def fetch(self, url: str, directory: str, file_name: str) -> int:
        require(os.path.basename(file_name) == file_name, "C-008", "the file name has no folder")
        path = os.path.join(directory, file_name)
        have = os.path.getsize(path) if os.path.isfile(path) else 0
        req = urllib.request.Request(url, headers={"User-Agent": "carl-engine-install"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                partial = getattr(resp, "status", 200) == 206
                with open(path, "ab" if partial else "wb") as f:
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        f.write(block)
        except urllib.error.HTTPError as e:
            if e.code == 416:                       # the file is complete already
                return 0
            self.out(f"error: {url}: {e}")
            return 1
        except OSError as e:
            self.out(f"error: {url}: {e}")
            return 1
        return 0


class ReleaseInstaller:
    """The builds of one release in root (the settings folder's llama.cpp folder). os_name, arch: windows or linux,
    x64 or arm64; fetch: the Downloader; digests: GitHub's digests ({} when it cannot be asked); out: a line for the
    user; free_bytes: the free space of a folder (tests give their own)."""

    def __init__(self, release: Release, os_name: str, arch: str, root: str, fetch: Downloader,
                 digests: Callable[[], Mapping[str, str]], out: Callable[[str], None],
                 free_bytes: Callable[[str], int] = lambda p: shutil.disk_usage(p).free) -> None:
        self.release, self.os, self.arch, self.root = release, os_name, arch, root
        self.fetch, self.digests, self.out, self.free_bytes = fetch, digests, out, free_bytes

    # ------------------------------------------------------------------ the port
    def installed(self) -> List[str]:
        return builds_in(self.root)

    def install(self, backend: str) -> str:
        build = self.release.find(self.os, self.arch, backend)
        require(build is not None and bool(build.files), "C-023", "the backend has files in the release")
        assert build is not None
        final = os.path.join(self.root, rel.folder_name(self.release, backend))
        if self._finished(final, build):
            self.out(f"llama.cpp {self.release.tag} ({backend}) is installed already: nothing to download.")
            return final
        os.makedirs(self.root, exist_ok=True)
        self._room(build)
        self._compare_digests(build)
        downloads = os.path.join(self.root, DOWNLOADS)
        os.makedirs(downloads, exist_ok=True)
        archives = [self._download(a, downloads) for a in build.files]
        stage = os.path.join(self.root, f".unpack-{rel.folder_name(self.release, backend)}-{os.getpid()}")
        if os.path.isdir(stage):
            _remove_tree(stage)
        os.makedirs(stage)
        try:
            for path in archives:
                self.out(f"Unpacking {os.path.basename(path)}.")
                unpack(path, stage)
            if not self._program(stage):
                raise ConfigError(f"llama.cpp {self.release.tag} ({backend}) holds no llama-server. Nothing is "
                                  "installed.")
            self._mark(stage, build, backend)
            if os.path.isdir(final):
                _remove_tree(final)                          # an unfinished folder of an earlier run
            self._rename(stage, final)
        except BaseException:
            if os.path.isdir(stage):
                _remove_tree(stage)
            raise
        for path in archives:
            os.remove(path)
        try:
            os.rmdir(downloads)
        except OSError:
            pass
        ensure(self._finished(final, build), "C-023", "the folder holds the build, with its marker")
        return final

    # ------------------------------------------------------------------ the steps
    def _program(self, folder: str) -> bool:
        return any(os.path.isfile(os.path.join(folder, "llama-server" + s)) for s in ("", ".exe"))

    def _marker(self, build: Build, backend: str) -> Dict[str, object]:
        return {"tag": self.release.tag, "backend": backend,
                "files": [{"name": a.name, "sha256": a.sha256} for a in build.files]}

    def _mark(self, folder: str, build: Build, backend: str) -> None:
        with open(os.path.join(folder, rel.MARKER), "w", encoding="utf-8", newline="\n") as f:
            json.dump(self._marker(build, backend), f, indent=2)
            f.write("\n")

    def _finished(self, folder: str, build: Build) -> bool:
        try:
            with open(os.path.join(folder, rel.MARKER), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return False
        return isinstance(doc, dict) and doc == self._marker(build, str(doc.get("backend"))) and self._program(folder)

    def _room(self, build: Build) -> None:
        """Room for the downloads and what they unpack to (about three times their size)."""
        need = 3 * build.size + 256 * 2 ** 20
        have = self.free_bytes(self.root)
        if have < need:
            raise ConfigError(f"There is not enough room: llama.cpp needs about {file_size(need)} free in "
                              f"{self.root}, and {file_size(have)} is free.")

    def _compare_digests(self, build: Build) -> None:
        """GitHub's digest of each file must be the manifest's. No answer: one note, and the manifest decides."""
        given = self.digests()
        if not given:
            self.out("note: GitHub's SHA-256 of the files could not be read. CARL checks them against its own list "
                     "only.")
            return
        for a in build.files:
            theirs = given.get(a.name)
            if theirs is not None and theirs != a.sha256:
                raise ConfigError(f"GitHub gives another SHA-256 for {a.name} than CARL's list. The release has "
                                  "changed since this CARL was tested. Nothing is downloaded.")

    def _download(self, asset: Asset, folder: str) -> str:
        path = os.path.join(folder, asset.name)
        for _ in (1, 2):
            have = os.path.getsize(path) if os.path.isfile(path) else 0
            if have == asset.bytes and sha256_of(path) == asset.sha256:
                return path                                  # an earlier run finished this file
            if have >= asset.bytes:
                os.remove(path)
                have = 0
            self.out(f"Downloading {asset.name} ({file_size(asset.bytes)})" + (", resuming." if have else "."))
            code = self.fetch.fetch(self.release.url_of(asset), folder, asset.name)
            if code != 0:
                raise ConfigError(f"The download of {asset.name} failed (exit code {code}). Run the command again: "
                                  "it resumes.")
            if os.path.isfile(path) and os.path.getsize(path) == asset.bytes and sha256_of(path) == asset.sha256:
                return path
            if os.path.isfile(path):
                os.remove(path)
            if not have:
                break                                        # a whole new file that differs: refused
            self.out(f"note: the part file of {asset.name} was wrong. CARL downloads it again.")
        raise ConfigError(f"{asset.name} is not the file that CARL expects: its size or SHA-256 differs from CARL's "
                          "list. It is removed and refused.")

    def _rename(self, stage: str, final: str) -> None:
        for attempt in range(RENAME_TRIES):
            try:
                os.replace(stage, final)
                return
            except PermissionError:
                if attempt == RENAME_TRIES - 1:
                    raise ConfigError(f"The folder {final} could not be made (another program holds the new files "
                                      "open). Run the command again.") from None
                time.sleep(1.0)


# ---------------------------------------------------------------------- unpacking
def _members(path: str) -> List[Tuple[str, bool]]:
    """(member name, is a folder) of an archive."""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            return [(i.filename, i.is_dir()) for i in z.infolist()]
    with tarfile.open(path, "r:*") as t:
        return [(m.name, m.isdir()) for m in t.getmembers()]


def _refuse(path: str, name: str, why: str) -> ConfigError:
    return ConfigError(f"{os.path.basename(path)} holds an entry that is not safe to unpack ({name!r}: {why}). "
                       "Nothing is installed.")


def strip_prefix(names: Sequence[Tuple[str, bool]]) -> str:
    """The one folder that holds every entry of a tarball (llama-bNNNNN), "" when the entries are at the top."""
    tops = set()
    for name, is_dir in names:
        safe = rel.safe_member(name)
        if safe is None:
            return ""
        parts = safe.split("/")
        if len(parts) == 1 and not is_dir:
            return ""
        tops.add(parts[0])
    return tops.pop() if len(tops) == 1 else ""


def _inside(root: str, target: str) -> bool:
    root_real, target_real = os.path.realpath(root), os.path.realpath(target)
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def unpack(path: str, dest: str) -> None:
    """Unpack a zip or a tar.gz into dest, refusing any entry that would leave it. ConfigError for a bad entry or an
    archive that cannot be read."""
    try:
        names = _members(path)
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
        raise ConfigError(f"{os.path.basename(path)} cannot be read: {e}") from None
    for name, _ in names:
        if rel.safe_member(name) is None:
            raise _refuse(path, name, "its path leaves the folder")
    strip = strip_prefix(names)
    written = [0]

    def target_of(name: str) -> Optional[str]:
        safe = rel.safe_member(name)
        assert safe is not None
        parts = safe.split("/")
        if strip and parts[0] == strip:
            parts = parts[1:]
        if not parts:
            return None
        target = os.path.join(dest, *parts)
        if not _inside(dest, os.path.dirname(target)):
            raise _refuse(path, name, "its folder is a link that leaves the folder")
        return target

    def put(src: IO[bytes], target: str, mode: int, name: str) -> None:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target) and (os.path.islink(target) or os.path.isdir(target)):
            raise _refuse(path, name, "it replaces a link or a folder")
        with open(target, "wb") as out:
            while True:
                block = src.read(CHUNK)
                if not block:
                    break
                written[0] += len(block)
                if written[0] > MAX_UNPACKED:
                    raise _refuse(path, name, "the archive unpacks to more than 12 GB")
                out.write(block)
        os.chmod(target, 0o755 if mode & 0o111 else 0o644)

    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                target = target_of(info.filename)
                if target is None:
                    continue
                if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                    raise _refuse(path, info.filename, "it is a link")
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                with z.open(info) as src:
                    put(src, target, (info.external_attr >> 16) & 0o777, info.filename)
        return
    with tarfile.open(path, "r:*") as t:
        links: List[Tuple[str, str, str]] = []
        for m in t:
            target = target_of(m.name)
            if target is None:
                continue
            safe = rel.safe_member(m.name) or ""
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isreg():
                src = t.extractfile(m)
                assert src is not None
                put(src, target, m.mode, m.name)
            elif m.issym():
                inner = safe.split("/")[1:] if strip and safe.split("/")[0] == strip else safe.split("/")
                if rel.link_target("/".join(inner), m.linkname) is None:
                    raise _refuse(path, m.name, f"the link points outside the folder ({m.linkname})")
                links.append((target, m.linkname, "/".join(inner)))
            elif m.islnk():
                raise _refuse(path, m.name, "it is a hard link")
            else:
                raise _refuse(path, m.name, "it is a device or a pipe")
        through = rel.link_through_link([(member, linkname) for _, linkname, member in links])
        if through is not None:                               # before any link is made: the OS would decide (WFIN)
            raise _refuse(path, through, "a link that goes through another link")
        for target, linkname, _ in links:                     # after the files, so a link never leads a file out
            if not _inside(dest, os.path.dirname(target)):        # a link made earlier in this loop leads out
                raise _refuse(path, linkname, "its folder is a link that leaves the folder")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                raise _refuse(path, linkname, "it replaces a file")
            os.symlink(linkname, target)
            if not _inside(dest, target):                          # a chain of links (q -> ., p -> q/..) gets out
                raise _refuse(path, linkname, f"the link leads outside the folder ({linkname})")
        for target, linkname, _ in links:                     # a later link can redirect an earlier one
            if not _inside(dest, target):
                raise _refuse(path, linkname, f"the link leads outside the folder ({linkname})")

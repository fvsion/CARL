"""The pinned llama.cpp release (W6.2): the manifest data/llamacpp-release.json as values, the build for a computer, the
folder a build is installed into, and the check of an archive's member names. Pure.

The manifest is the pinned release's asset list: per OS and architecture, per build (cuda-13, cuda-12, rocm, vulkan,
cpu), the files that make the build (the main archive, and for CUDA its runtime) with their sizes and SHA-256. A
build the release has no asset for is recorded as missing, with the reason. A development tool writes
it from GitHub's API when the pin moves (version.py PINNED).

classify() reads an asset's name; the refresh program and the tests use it, so the names are read in one place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ..shared.errors import ConfigError

SCHEMA = 1
OSES = ("windows", "linux")                      # the OSes whose release builds CARL installs (a Mac uses Homebrew)
ARCHES = ("x64", "arm64")
BUILDS = ("cuda-13", "cuda-12", "rocm", "vulkan", "cpu")
FALLBACK = "vulkan"                              # the build for a backend the release lacks (it runs on any GPU)
OS_LABEL = {"windows": "Windows", "linux": "Linux"}
MARKER = ".carl-install.json"                    # in a finished build folder: what it holds (the rerun is a no-op)
SUBFOLDER = "llama.cpp"                          # in the settings folder: one folder per installed build

_ASSET = re.compile(
    r"^(?P<runtime>cudart-)?llama-(?:b\d+-)?bin-(?P<os>win|ubuntu)-(?P<kind>x64|arm64|[a-z]+(?:-[\d.]+)?)(?:-(?P<arch>x64|arm64))?"
    r"\.(?:zip|tar\.gz)$")
_FOLDER = re.compile(r"^b(\d+)-([a-z0-9][a-z0-9-]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Asset:
    """One file of a release: its name, size in bytes and SHA-256."""
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Build:
    """One build of one OS and architecture: its files (() when the release has none, then why in `missing`) and
    the CUDA version of its runtime ("" for the others)."""
    backend: str
    files: Tuple[Asset, ...] = ()
    missing: str = ""
    cuda: str = ""

    @property
    def size(self) -> int:
        return sum(a.bytes for a in self.files)


@dataclass(frozen=True)
class Release:
    """The pinned release. url: where a file is ("{tag}" and "{name}" stand for the tag and the file's name); api:
    the release's description with GitHub's digest of each file ("{tag}" stands for the tag)."""
    tag: str
    version: str
    build: int
    url: str
    api: str
    builds: Mapping[Tuple[str, str, str], Build] = field(default_factory=dict)

    def find(self, os_name: str, arch: str, backend: str) -> Optional[Build]:
        """The manifest's entry for this OS, architecture and build (None: the manifest has none)."""
        return self.builds.get((os_name, arch, backend))

    def url_of(self, asset: Asset) -> str:
        return self.url.replace("{tag}", self.tag).replace("{name}", asset.name)

    def api_url(self) -> str:
        return self.api.replace("{tag}", self.tag)


@dataclass(frozen=True)
class Resolution:
    """The build to install: `build` (None: the release has nothing for this computer), the backend it is
    (`backend`; Vulkan when the wanted one is missing) and one sentence about it (`note`, "" when nothing changed)."""
    backend: str
    build: Optional[Build]
    note: str = ""


def classify(name: str) -> Optional[Tuple[str, str, str, str, bool]]:
    """(OS, architecture, build, CUDA version, is a CUDA runtime) of an asset name, or None for an asset CARL does
    not install (another OS, android, sycl, openvino, the xcframework). The Linux CPU build is the plain
    "ubuntu" archive; the Windows one says cpu."""
    m = _ASSET.match(name)
    if not m:
        return None
    os_name = "windows" if m.group("os") == "win" else "linux"
    kind, arch = m.group("kind"), m.group("arch") or ""
    if os_name == "linux" and not arch and kind in ("x64", "arm64"):
        return os_name, kind, "cpu", "", False          # llama-b1-bin-ubuntu-x64.tar.gz: the kind is the arch
    if not arch:
        return None
    ver = ""
    if kind.startswith("cuda-"):
        build, ver = (f"cuda-{kind[5:].split('.')[0]}", kind[5:])
    elif kind.startswith("rocm-"):
        build = "rocm"
    elif kind in ("vulkan", "cpu"):
        build = kind
    else:
        return None
    if build not in BUILDS:
        return None
    return os_name, arch, build, ver, bool(m.group("runtime"))


def _asset(doc: object, where: str) -> Asset:
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not isinstance(doc.get("bytes"), int) \
            or not isinstance(doc.get("sha256"), str):
        raise ConfigError(f"the llama.cpp release list is damaged: {where} is not a file entry")
    name, size, digest = doc["name"], doc["bytes"], doc["sha256"]
    if not name or safe_member(name) != name or "/" in name or size <= 0 or not _SHA256.match(digest):
        raise ConfigError(f"the llama.cpp release list is damaged: {where} ({name!r}) has a wrong name, size or hash")
    return Asset(name, size, digest)


def parse_release(doc: object) -> Release:
    """The Release of the manifest's document. ConfigError when it is damaged (a wrong schema, a missing field, a
    hash that is not a SHA-256): the install refuses rather than guess."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ConfigError("the llama.cpp release list is damaged: it has the wrong schema")
    tag, url, build, api = doc.get("tag"), doc.get("url"), doc.get("build"), doc.get("api")
    version = doc.get("version")
    if not (isinstance(tag, str) and re.match(r"^b\d+$", tag) and isinstance(url, str) and isinstance(build, int)
            and isinstance(version, str) and isinstance(api, str) and "{name}" in url):
        raise ConfigError("the llama.cpp release list is damaged: its tag, version, build or address is wrong")
    builds: Dict[Tuple[str, str, str], Build] = {}
    systems = doc.get("platforms")
    if not isinstance(systems, dict):
        raise ConfigError("the llama.cpp release list is damaged: it has no platforms")
    for os_name, arches in systems.items():
        for arch, entries in (arches.items() if isinstance(arches, dict) else ()):
            for backend, entry in (entries.items() if isinstance(entries, dict) else ()):
                where = f"{os_name}/{arch}/{backend}"
                if os_name not in OSES or arch not in ARCHES or backend not in BUILDS or not isinstance(entry, dict):
                    raise ConfigError(f"the llama.cpp release list is damaged: {where} is not a known build")
                raw = entry.get("files", [])
                files = tuple(_asset(a, where) for a in raw) if isinstance(raw, list) else ()
                missing = entry.get("missing", "")
                if bool(files) == bool(missing) or not isinstance(missing, str):
                    raise ConfigError(f"the llama.cpp release list is damaged: {where} has files or a reason, not both")
                cuda = entry.get("cuda", "")
                builds[(os_name, arch, backend)] = Build(backend, files, missing, cuda if isinstance(cuda, str) else "")
    return Release(tag, version, build, url, api, builds)


def resolve(release: Release, os_name: str, arch: str, backend: str) -> Resolution:
    """The build to install for a wanted backend. The backend the release has no files for (a missing entry, or none)
    falls back to Vulkan with one sentence saying so; with no Vulkan build either the build is None and the sentence
    says to point llama.path at a build of one's own."""
    wanted = release.find(os_name, arch, backend)
    if wanted is not None and wanted.files:
        return Resolution(backend, wanted)
    where = f"{OS_LABEL.get(os_name, os_name)} {arch}"
    why = f" ({wanted.missing})" if wanted is not None and wanted.missing else ""
    spare = release.find(os_name, arch, FALLBACK)
    if backend != FALLBACK and spare is not None and spare.files:
        return Resolution(FALLBACK, spare, f"llama.cpp {release.tag} has no {backend} build for {where}{why}. "
                                           f"CARL takes the {FALLBACK} build.")
    return Resolution(backend, None, f"llama.cpp {release.tag} has no {backend} build for {where}{why}. Take a build "
                                     "of your own and set llama.path to its folder.")


def folder_name(release: Release, backend: str) -> str:
    """The folder of an installed build: bNNNNN-BACKEND."""
    return f"{release.tag}-{backend}"


def folder_key(name: str) -> Optional[Tuple[int, str]]:
    """(the build number, the backend) of a build folder's name, or None when the name is not one of CARL's."""
    m = _FOLDER.match(name)
    return (int(m.group(1)), m.group(2)) if m else None


def newest_first(names: Sequence[str]) -> List[str]:
    """CARL's build folders among names, the newest build first (the same build: by the backend's name)."""
    keyed = [(folder_key(n), n) for n in names]
    return [n for k, n in sorted(((k, n) for k, n in keyed if k is not None), key=lambda x: (-x[0][0], x[0][1]))]


def safe_member(name: str) -> Optional[str]:
    """An archive member's path as a relative path with "/" (a backslash counts as a separator), or None when it
    would leave the folder it unpacks into: an absolute path, a drive, a ".." part, an empty name or a NUL. "." parts
    and doubled separators are dropped."""
    if not name or "\0" in name:
        return None
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".")]
    if not parts or name[0] in "/\\" or re.match(r"^[A-Za-z]:", parts[0]) or ".." in parts:
        return None
    return "/".join(parts)


def link_through_link(links: Sequence[Tuple[str, str]]) -> Optional[str]:
    """The first link (member, target; safe relative paths) whose own folder or whose target's path goes through
    another of the links, None when there is none. A link may point at another link (libx.so -> libx.so.0), but
    never through one: the OS then decides where the path lands (q -> ., p -> q/.. leaves the folder on a POSIX
    system and not on Windows), so CARL refuses it everywhere."""
    names = {m for m, _ in links}
    for member, target in links:
        parts = member.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            return member
        stack = parts[:-1]
        steps = [p for p in re.split(r"[\\/]+", target) if p not in ("", ".")]
        for i, part in enumerate(steps):
            if part == "..":
                if stack:
                    stack.pop()
            else:
                stack.append(part)
                if i < len(steps) - 1 and "/".join(stack) in names:
                    return member
    return None


def link_target(member: str, target: str) -> Optional[str]:
    """Where a symbolic link at `member` (a safe relative path) pointing at `target` lands, as a path relative to the
    folder; None when it points outside the folder or is absolute. Used for the links of a Linux tarball."""
    if not target or target[0] in "/\\" or re.match(r"^[A-Za-z]:", target) or "\0" in target:
        return None
    stack = member.split("/")[:-1]
    for part in re.split(r"[\\/]+", target):
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(part)
    return "/".join(stack)

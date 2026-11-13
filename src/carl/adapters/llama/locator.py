"""Where llama.cpp's programs are on a computer that has no Homebrew (implements LlamaCpp, contract C-022):

  1. CARL_LLAMA_DIR, the folder of an unpacked llama.cpp build (wins over everything);
  2. the setting llama.path (config.json), which `carl engine install` records;
  3. the newest build that `carl engine install` put in the settings folder (llama.cpp/bNNNNN-BACKEND);
  4. PATH.

A folder that is named but holds no program gives none: the locator does not fall through to PATH, so a wrong
setting shows at once. llama-batched-bench is found the same way as llama-server. The composition reads the
environment and gives it in (rule R6); resolve_folder() is what it calls for the folder (Machine.llama_dir).
"""
from __future__ import annotations

import os
import shutil
from typing import Callable, Dict, Optional, Tuple

from ...domain.llama.release import MARKER, SUBFOLDER, folder_key
from ...domain.shared.errors import ConfigError
from ..common.json_files import read_json
from .release_installer import builds_in
from .version_probe import version_text

SERVER = "llama-server"
BATCHED_BENCH = "llama-batched-bench"
FIT_PARAMS = "llama-fit-params"


def configured_path(conf_dir: str) -> str:
    """The setting llama.path of config.json ("" when it is not set, or the file cannot be read)."""
    try:
        doc = read_json(os.path.join(conf_dir, "config.json")) if conf_dir else None
    except ConfigError:
        return ""
    section = doc.get("llama") if isinstance(doc, dict) else None
    value = section.get("path") if isinstance(section, dict) else None
    return value.strip() if isinstance(value, str) else ""


def resolve_folder(env_dir: Optional[str], setting: str, conf_dir: str) -> Optional[str]:
    """The folder of the llama.cpp build to run: CARL_LLAMA_DIR, else llama.path, else the newest installed build;
    None when there is none (then PATH decides)."""
    if env_dir:
        return env_dir
    if setting:
        return setting
    builds = builds_in(os.path.join(conf_dir, SUBFOLDER)) if conf_dir else []
    return builds[0] if builds else None


def signature(program: str) -> Optional[Tuple[int, int]]:
    """(size, time in seconds) of a program's file: what an engine record of a detected build names it by."""
    try:
        st = os.stat(program)
    except OSError:
        return None
    return st.st_size, int(st.st_mtime)


def _current(folder: str, doc: Dict[str, object], suffix: str) -> bool:
    """An engine record that CARL wrote for a build it classed (detected) holds while llama-server is the file it was
    written for (A72); an install's record always holds."""
    if not doc.get("detected"):
        return True
    server, now = doc.get("server"), signature(os.path.join(folder, SERVER + suffix))
    return isinstance(server, dict) and now is not None and [server.get("size"), server.get("mtime")] == list(now)


def build_backend(folder: Optional[str], suffix: str = "") -> str:
    """The backend of the llama.cpp build in folder (cpu, cuda-13, vulkan, ...): its engine record (the install marker,
    or the record of a build CARL classed: build_class.py), else the backend in the folder's name (bNNNNN-BACKEND); ""
    when neither says (a build of your own not classed yet, or PATH)."""
    if not folder:
        return ""
    folder = os.path.expanduser(folder)
    try:
        doc = read_json(os.path.join(folder, MARKER))
    except ConfigError:
        doc = None
    named = doc.get("backend") if isinstance(doc, dict) else None
    if isinstance(named, str) and named.strip() and _current(folder, doc, suffix):       # type: ignore[arg-type]
        return named.strip()
    key = folder_key(os.path.basename(os.path.normpath(folder)))
    return key[1] if key else ""


def find(name: str, folder: Optional[str], suffix: str = "",
         which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """A program of llama.cpp: in folder when there is one (no PATH then), else on PATH (suffix: .exe on Windows)."""
    if folder:
        path = os.path.join(os.path.expanduser(folder), name + suffix)
        return path if os.path.isfile(path) else None
    return which(name)


class LlamaLocator:
    """The LlamaCpp port for Windows and Linux. folder: what resolve_folder gives; update: the command that updates
    llama.cpp here (the platform words' hint); suffix: ".exe" on Windows."""

    def __init__(self, folder: Optional[str], update: str, suffix: str = "",
                 which: Callable[[str], Optional[str]] = shutil.which) -> None:
        self.folder, self.update, self.suffix, self.which = folder, update, suffix, which

    def server(self) -> Optional[str]:
        return find(SERVER, self.folder, self.suffix, self.which)

    def batched_bench(self) -> Optional[str]:
        return find(BATCHED_BENCH, self.folder, self.suffix, self.which)

    def fit_params(self) -> Optional[str]:
        return find(FIT_PARAMS, self.folder, self.suffix, self.which)

    def version_text(self) -> str:
        return version_text(self.server())

    def update_hint(self) -> str:
        return self.update

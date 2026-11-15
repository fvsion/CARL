"""The jobs' side of this computer (contract C-132): the processes they start and stop (ProcessControl), the files of a
start and of the disk cache, the client config they publish, the client package, the desktop and the platform's
words. The composition gives it (composition/dashboard.py JobsHostAdapter); the jobs reach the computer only through
it, so they run the same on every OS and in a test with fakes (rule R5)."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple

from ...domain.clients.package import Outcome
from ...domain.server.snapshot import JSONDict
from ...domain.shared.platform_words import PlatformWords
from ...ports import Desktop, ProcessControl


class CacheSettings(Protocol):
    """The Caching settings of config.json "cache" (and delegation.gate), with the defaults filled in."""

    disk_gb: int
    prefix: bool
    sessions: bool
    save: str
    swa: str
    auto_s: int
    share: bool
    move: str
    gate: int
    keep_slots: bool

    @property
    def limit(self) -> int:
        """disk_gb in bytes."""
        ...


class Resident(Protocol):
    """A session a client recorded in a slot: saved before the slot is reused."""

    slot: int
    task: int
    file: str


class JobsHost(Protocol):
    """What the jobs need of this computer. python: the interpreter CARL's programs run in; words: the platform's."""

    procs: ProcessControl
    desktop: Desktop
    words: PlatformWords
    python: str

    def env(self) -> Mapping[str, str]:
        """The environment the jobs' children start from (the dashboard's own)."""
        ...

    def interrupt(self, pid: int) -> None:
        """Ask a job CARL started to stop as Ctrl-C does (Auto-tune then stops its test server first)."""
        ...

    def end(self, pid: int) -> None:
        """Ask the server to end (the quit dialog's stop): SIGTERM on a Mac and Linux, the console's Ctrl-C on Windows
        (decision 7); it does not wait. A process already gone is fine."""
        ...

    def alive(self, pid: int) -> bool: ...

    def healthy(self, host: str, port: int, key: str) -> bool:
        """The llama-server at host:port answers /health with this key."""
        ...

    # ------------------------------------------------------------ files
    def read_text(self, path: str) -> Optional[str]:
        """A file's text (undecodable bytes replaced), None when there is none."""
        ...

    def read_lines(self, path: str) -> List[str]:
        """A job's output so far, line by line ([] when there is none)."""
        ...

    def file_size(self, path: str) -> int:
        """0 when there is no such file."""
        ...

    def file_stamp(self, path: str) -> Optional[Tuple[int, float]]:
        """A file's (bytes, modification time); None when it cannot be read (the slot handoff's fingerprint)."""
        ...

    def exists(self, path: str) -> bool: ...

    def write_private(self, path: str, text: str) -> None:
        """Write the file 0600 through a temporary file (atomic)."""
        ...

    def remove(self, path: str) -> None: ...

    def make_folder(self, path: str) -> None:
        """The folder and its parents (nothing when it exists)."""
        ...

    # ------------------------------------------------------------ the disk cache (the slots folder)
    def cache_settings(self, config: JSONDict) -> CacheSettings:
        """The Caching settings of a loaded config.json ({}: the defaults)."""
        ...

    def tidy_cache(self, folder: str, conf_dir: str, now: float, pack: bool) -> None:
        """Remove the files of earlier versions and what clients that went away left, the cache's events older than
        two days; pack: store the new saved sessions as the changes to their saved prompts."""
        ...

    def over_limit(self, folder: str, limit: int, keep: str) -> List[str]:
        """The saved states to remove so that the rest fit limit bytes, oldest sessions first (keep: not this one
        unless it alone is over)."""
        ...

    def cache_files(self, folder: str) -> List[Any]:
        """The saved states, oldest first (name, bytes)."""
        ...

    def remove_states(self, folder: str, names: Sequence[str]) -> None:
        """Saved states in every form (the file, a patch, its meta)."""
        ...

    def free_enough(self, folder: str) -> bool:
        """The disk has room for a save (the clients' rule)."""
        ...

    def residents(self, folder: str, model: str) -> List[Resident]: ...

    def drop_resident(self, resident: Resident) -> None: ...

    def turns(self, folder: str, model: str) -> List[int]:
        """The slots of a model where a client's turn runs."""
        ...

    def saving(self, folder: str, within: float) -> bool:
        """A saved state was written in the last `within` seconds (a save may still be going on)."""
        ...

    def placed(self, conf_dir: str, model: str, since: float) -> List[int]:
        """The slots of a model where a client with CARL's plugins put a session since `since` (its cache events)."""
        ...

    def cache_event(self, conf_dir: str, event: JSONDict) -> None:
        """Add one event to what the cache did (the counters: cache-events/DAY.jsonl)."""
        ...

    # ------------------------------------------------------------ the clients
    def publish(self, conf_dir: str, models: JSONDict) -> str:
        """Publish the client config for the clients' sync; its version."""
        ...

    def published(self, conf_dir: str) -> Optional[JSONDict]:
        """The client config published last (None: none)."""
        ...

    def installed_here(self, base: str, home: str) -> bool:
        """OpenCode or Pi on this computer was set up by CARL for the server at base."""
        ...

    def llama_backends(self, conf_dir: str) -> Optional[List[str]]:
        """The backends of the llama.cpp builds `carl engine install` put in the settings folder conf_dir (cuda-13,
        cpu; [] none); None when a folder is named (CARL_LLAMA_DIR or llama.path): CARL cannot tell the
        backend of that build (A46: a GPU backend with no build is refused when applied)."""
        ...

    def make_package(self, repo: str, models: JSONDict, config: JSONDict) -> Outcome:
        """The client package for another computer, in repo's dist/ (the code of carl package)."""
        ...

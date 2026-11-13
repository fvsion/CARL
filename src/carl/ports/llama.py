"""Ports of llama.cpp: where its programs are, installing a release build, and the first-run check."""
from __future__ import annotations

from typing import Callable, List, Optional, Protocol, Sequence


class LlamaCpp(Protocol):
    """The llama.cpp programs CARL runs. Contract C-022."""

    def server(self) -> Optional[str]:
        """The llama-server program (None when it is not found)."""
        ...

    def batched_bench(self) -> Optional[str]:
        """The llama-batched-bench program (None when it is not found)."""
        ...

    def fit_params(self) -> Optional[str]:
        """The llama-fit-params program, llama.cpp's no-alloc memory estimate (None when it is not found; A57)."""
        ...

    def version_text(self) -> str:
        """What `llama-server --version` prints ("" when it can't run)."""
        ...

    def update_hint(self) -> str:
        """The command that updates llama.cpp on this computer."""
        ...


class MemoryDryRun(Protocol):
    """llama.cpp's own no-alloc dry run of a start (llama-fit-params --fit-print on), the cross-check of the estimate
    (A57). Contract C-059."""

    def run(self, server_argv: Sequence[str]) -> Optional[str]:
        """The dry run's output (stdout) for a start with these llama-server arguments (the program is not in them), None
        when it cannot run: no program, a failed or timed-out run. A run is kept by the build, the model file and the
        flags, so the same start asks llama.cpp once."""
        ...


class ServerFit(Protocol):
    """llama-server's own fit of a start (--fit on, stopped when it has printed the memory breakdown of the start's
    parameters): the source of a start's GPU need (A67). Contract C-060."""

    def measure(self, server_argv: Sequence[str]) -> Optional[str]:
        """The fit's log for a start with these llama-server arguments (the program is not in them), on a local port of
        its own: its breakdown's lines (server_fit.parse reads them; a kept answer is those lines alone, not the whole
        log); None when it cannot run: no llama-server, a model file that is not there, a run that never reaches the
        fit. An answer is kept by the build, the GPUs, the model files and the flags, so the same start asks the server
        once."""
        ...

    def measure_stub(self, name: str, size: int, head: Callable[[], bytes],
                     server_argv: Sequence[str]) -> Optional[str]:
        """measure for a model not on disk (Auto fit's pick before its download): the fit on a sparse stub of its header
        (head: reads the file's first bytes, its header and tensor table; size: the real file's), made in the cache and
        removed after. The answer is kept by name (the file on Hugging Face at its revision), the build and the flags, so
        head is read only for a new one. None as for measure, and when head cannot be read."""
        ...

    def kept(self, server_argv: Sequence[str]) -> Optional[str]:
        """measure's answer when an earlier fit of the same start was kept; None when it was not. No server is started
        (carl fit's table fills its rows from what was kept, A72)."""
        ...

    def kept_stub(self, name: str, size: int, server_argv: Sequence[str]) -> Optional[str]:
        """measure_stub's answer when it was kept, as kept: no server is started and no header is read."""
        ...

    def draft_record(self, server_argv: Sequence[str]) -> Optional[int]:
        """The draft context's buffers on the GPU, bytes, as an earlier real start of the same start logged them (a
        drafter in its own file, which the fit cannot measure); None when no start was recorded."""
        ...

    def watch_draft(self, server_argv: Sequence[str], log_path: str) -> None:
        """This start's log holds its draft context's buffers once it has loaded: a later measure or draft_record reads
        them from it."""
        ...


class LlamaReleaseInstaller(Protocol):
    """llama.cpp release builds in the settings folder (one folder per build and backend). Contract C-023."""

    def installed(self) -> List[str]:
        """The build folders there, newest first."""
        ...

    def install(self, backend: str) -> str:
        """Download, check and unpack the pinned build for a backend; its folder. Raises ConfigError."""
        ...


class Dependencies(Protocol):
    """What CARL needs before its first start, and the offer to install it. Contract C-024."""

    def missing(self) -> List[str]:
        """The names of what is missing ([] when nothing is)."""
        ...

    def offer(self, interactive: bool) -> bool:
        """Offer to install what is missing (on a terminal: ask; off one: print the command). True when nothing
        is missing afterwards."""
        ...

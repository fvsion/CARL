"""What the dashboard tells about the clients on this computer (OpenCode and Pi set up for the server it watches):
whether they are set up here, which coder model they use, their versions against the tested ones, and whether their
configs list the models they get now (Phase 23.4.4 item 13: a change of the server's model makes them out of date).

The client setups are read through ClientLookups, which the composition fills from its adapters
(composition/dashboard.py); the rules that compare the lists (drift, serving) come from the caller."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from ..domain.server.snapshot import JSONDict, ServerData, jdict
from .model_list import ModelList

VERSIONS_S = 300.0      # the client versions are read again this often (they run `opencode --version`)
Versions = Tuple[Tuple[str, str, str, str], ...]


class Job(Protocol):
    """A job of the dashboard (the install): done once it ended."""
    done: bool


@dataclass(frozen=True)
class ClientLookups:
    """How the client status reads this computer (the composition gives the adapters' functions).

    installed_here(base, home)      (client, provider id) of the setups pointed at base (or at its port before a move)
    moved_here(base, home)          client -> the other port of a setup made for this server before it moved
    listed_models(home, client, provider)   the model ids a client's config lists, with their context; None: unread
    coder_model_here(base, home)    the coder's model of the first setup for base ("" when none)
    versions(repo, home)            (client, installed, tested, tested major) for OpenCode and Pi
    version_of(doc)                 the version (a hash) of a client-models document
    file_size(path)                 bytes in a file, 0 when it does not exist
    """
    installed_here: Callable[[str, str], List[Tuple[str, str]]]
    moved_here: Callable[[str, str], Dict[str, int]]
    listed_models: Callable[[str, str, str], Optional[Dict[str, int]]]
    coder_model_here: Callable[[str, str], str]
    versions: Callable[[str, str], List[Tuple[str, str, str, str]]]
    version_of: Callable[[JSONDict], str]
    file_size: Callable[[str], int]


class ClientStatus:
    """The clients on this computer for one server. models: the model list (the configs' models); load_config:
    config.json (llama.mode when no server runs); drift and serving: the rules of dashboard/clients.py."""

    def __init__(self, lookups: ClientLookups, models: ModelList, load_config: Callable[[], JSONDict], home: str,
                 drift: Callable[..., List[Any]],
                 serving: Callable[[ServerData], Tuple[Optional[str], Optional[bool]]]) -> None:
        self.lookups = lookups
        self.models = models
        self.load_config = load_config
        self.home = home
        self.drift_of = drift
        self.serving = serving
        self._versions: Tuple[float, Versions] = (0.0, ())
        self._versions_after: object = None             # the install the versions were read after

    def here(self, base: str) -> List[Tuple[str, str]]:
        return self.lookups.installed_here(base, self.home)

    def versions(self, repo: str, install: Optional[Job]) -> Versions:
        """OpenCode and Pi here against the versions CARL is tested with (the Connect tab; read again every
        VERSIONS_S and after an install ends). install: the install job (its done), or None."""
        done = install is not None and install.done
        fresh_install = done and self._versions_after is not install       # an install just ended: read again
        if fresh_install:
            self._versions_after = install
        if fresh_install or time.time() - self._versions[0] > VERSIONS_S:
            try:
                self._versions = (time.time(), tuple(self.lookups.versions(repo, self.home)))
            except Exception:          # a client that hangs or cannot run: no row
                self._versions = (time.time(), ())
        return self._versions[1]

    def here_text(self, base: str, d: ServerData, port: int) -> str:
        """OpenCode and Pi here, for the CONNECT card."""
        try:
            here = self.here(base)
            stale = self.drift(here, base, d, port)[0] if here else []
        except Exception:           # a model list that can't be read: say nothing
            return ""
        return ("need an update (tab 2, u)" if stale else "set up (tab 2)") if here else "not set up here (tab 2)"

    def coder_here(self, base: str) -> str:
        """The coder's model of OpenCode and Pi here, for the Clients list (Phase 23.4.5)."""
        try:
            return self.lookups.coder_model_here(base, self.home)
        except Exception:           # a state file that can't be read: say nothing
            return ""

    def drift(self, here: Sequence[Tuple[str, str]], base: str, d: ServerData, port: int) -> Tuple[List[Any], int]:
        """The client configs here (for this server) that list other models than the configs get, and how many they
        get: single-model mode, only the model the server runs; router mode, every installed model."""
        if not here:                # no client set up here for this server: nothing to compare
            return [], 0
        router = d.router is not None if d.up else self.saved_mode() == "router"    # no server: config.json's mode
        doc = self.client_doc(d)
        try:
            want = [str(jdict(m)["id"]) for m in doc["models"]] if doc else None
        except (KeyError, TypeError):
            want = None
        if want is None:            # no list: the installed models
            want = [m["name"] for m in self.models.downloaded()]
        server = want[0] if not router and len(want) == 1 else ""
        running = (d.alias, d.n_ctx) if d.up and d.alias and d.n_ctx else None
        moved = {c: (p, port) for c, p in self.lookups.moved_here(base, self.home).items()}
        listed = {c: self.lookups.listed_models(self.home, c, pid) for c, pid in here}
        return self.drift_of(listed, want, running, server, moved), len(want)

    def client_doc(self, d: ServerData) -> Optional[JSONDict]:
        """The model list the client configs get now (single-model mode: the model the server runs)."""
        run, router = self.serving(d)
        return self.models.client_list(run, self.saved_mode() == "router" if router is None else router)

    def single_model(self, d: ServerData) -> str:
        """Single-model mode: the model the server runs (or a start loads), which the configs list alone; else ""."""
        if d.router is not None or (not d.up and self.saved_mode() == "router"):
            return ""
        try:
            doc = self.client_doc(d)
            ms = doc.get("models") if doc else None
            return str(jdict(ms[0]).get("id") or "") if isinstance(ms, list) and len(ms) == 1 else ""
        except Exception:           # a model list that can't be read: say nothing
            return ""

    def config_unsent(self, d: ServerData, pushed: str) -> bool:
        """The config sent to the other computers (pushed: "VERSION at TIME", "" when none) is out of date: the list
        they would get now differs (Phase 23.4.4: in single-model mode, a change of the server's model)."""
        sent = pushed.partition(" at ")[0]
        if not sent:
            return False
        try:
            doc = self.client_doc(d)
        except Exception:           # a model list that can't be read: no warning
            return False
        return doc is not None and self.lookups.version_of(doc) != sent

    def configs_stale(self, base: str, d: ServerData, port: int) -> bool:
        """The client configs here need carl install --config-only (the Connect tab's ⚠)."""
        try:
            return bool(self.drift(self.here(base), base, d, port)[0])
        except Exception:           # a model list that can't be read: no warning (the Settings tab says why)
            return False

    def saved_mode(self) -> str:
        """llama.mode in config.json (single when unset or unreadable)."""
        try:
            return str(jdict(self.load_config().get("llama")).get("mode") or "single")
        except Exception:           # a broken config.json: the Server panel says why
            return "single"

"""The running server's file for this computer's clients, server-PORT.json in the settings folder (its layout:
domain/server/runtime_doc.py; carl-cache reads it): written at each start, single-model or router mode.
"""
from __future__ import annotations

from typing import Callable

from ..domain.server import runtime_doc as rd
from ..domain.server.router_presets import Preset
from ..domain.shared.contracts import ensure
from ..ports import RuntimeStore


class ServerFileService:
    """Writes the file through the RuntimeStore port. now: the local time in rd.TIME_FORMAT (the "written" field;
    the application reads no clock itself). Contract C-110."""

    def __init__(self, store: RuntimeStore, now: Callable[[], str]) -> None:
        self.store = store
        self.now = now

    def write_single(self, port: int, model: str, swa: str, kv: str = "", ctx: int = 0, slots: int = 0) -> str:
        """The file of a single-model start (swa: full, window, or "-" for a model with no sliding window); its
        path."""
        return self._written(port, self.store.write(port, rd.document(
            port, "single", {model: rd.model_entry(swa, kv, ctx, slots)}, self.now())))

    def write_router(self, port: int, preset: Preset) -> str:
        """The file of a router start: every model of its presets; its path."""
        models = {p.name: rd.model_entry("full" if p.swa else "window" if p.window else "none", p.kv, p.ctx, p.slots)
                  for p in preset.models}
        return self._written(port, self.store.write(port, rd.document(port, "router", models, self.now())))

    @staticmethod
    def _written(port: int, path: str) -> str:
        ensure(path.endswith(rd.file_name(port)), "C-110", "the path is the port's server-PORT.json")
        return path

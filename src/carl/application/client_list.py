"""The installed models for the OpenCode and Pi configs (the rules: domain/clients/model_list.py): each with its
window per slot from its effective settings, and the model a start loads as the default.
"""
from __future__ import annotations

from typing import Dict, Optional

from ..domain.catalog import inventory as dm
from ..domain.clients.model_list import client_list
from ..domain.config.settings import LLAMA_KEYS, Config
from ..domain.shared.contracts import ensure
from ..domain.shared.errors import ConfigError
from ..domain.shared.types import JsonObject, ModelInfo
from .fit import FitService
from .inventory import Inventory


class ClientListService:
    """The clients' model list (contract C-108; the file it becomes: C-221)."""

    def __init__(self, inventory: Inventory, fit: FitService) -> None:
        self.inventory = inventory
        self.fit = fit

    def client_models(self, cfg: Config, running: Optional[str] = None, router: Optional[bool] = None) -> JsonObject:
        """The installed models for the OpenCode / Pi configs (domain/clientlist.py): each with
        its window per slot from its effective settings, and the model a start loads as the
        default (none when nothing can start). Single-model mode (Phase 23.4.4 item 13): only the
        model the server runs (running: its name or alias, from the server); every downloaded model
        when it is not known or not downloaded (1.13.1). Router mode: every downloaded model.
        router None: config.json's llama.mode."""
        inv = self.inventory
        models = [inv.with_thinking(m) for m in inv.all_models(cfg)]
        if router is None:
            router = cfg.llama.get("mode", LLAMA_KEYS["mode"].default) == "router"

        def ctx_of(m: ModelInfo) -> int:
            ctx = inv.effective_tune(m, cfg)[0].get("ctx")
            return ctx if isinstance(ctx, int) else dm.CTX_FLOOR

        def roles_of(m: ModelInfo) -> Dict[str, object]:
            return dict(inv.effective_tune(m, cfg)[0])          # thinking_main, thinking_coder (Phase 23.4.4)
        try:
            default: Optional[str] = self.fit.resolve_launch(None, cfg)[0].get("name")
        except ConfigError:
            default = None
        if not router:
            down = [m for m in models if m.get("status") == "downloaded"]
            served = [m for m in down if running and running in (m.get("name"), inv.effective_tune(m, cfg)[0].get("alias"))]
            # Only the running model when the server says which one; else every downloaded model (1.13.1): the
            # model a start would load is not always the one that runs (a start with --model), so a package made
            # with it named only that model and the client could not use the running one ("Model not found").
            # The client side keeps only the running model (configure.py --running) when it installs.
            if served:
                models, default = served[:1], served[0].get("name")
            elif down:
                models = down
        out = client_list(models, ctx_of, default, roles_of)
        ensure(all(k in out for k in ("schema", "default", "models")), "C-221",
               "the list has the keys of installed-models.json")
        return out

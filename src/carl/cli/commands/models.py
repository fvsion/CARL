"""`carl models`: every model CARL knows (cli/text/models.py). Contract C-405."""
from __future__ import annotations

from typing import List, Optional

from ...domain.catalog.inventory import flat_files
from ...domain.shared.errors import ConfigError
from ...domain.shared.types import ModelInfo
from ..text.models import model_lines
from .base import Cli, unknown_model


def run(cli: Cli, argv: List[str]) -> int:
    """The model list (its arguments are not read, as before)."""
    api = cli.api()
    models = api.marked(api.all_models())
    cfg = api.carl.load_config()
    d = api.carl.models_dir(cfg)
    print("\n".join(model_lines(models, cli.words, cli.width(), cli.tilde(d), api.carl.files.free_bytes(d),
                           flat_files(models, d), api.carl.default_quants(models, cfg))))
    return 0


def known(cli: Cli, name: str, models: Optional[List[ModelInfo]] = None) -> ModelInfo:
    """The model of a name, else ConfigError (unknown model)."""
    m = cli.api().find(name, models)
    if not m:
        raise ConfigError(unknown_model(name, cli.words))
    return m

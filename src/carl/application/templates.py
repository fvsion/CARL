"""The thinking toggle's chat templates of a start (the rule: domain/server/thinking_template.py; the files:
TemplateStore, contract C-028). A single start asks for its model's template; a router start for every downloaded
model's, so each preset can name its own.
"""
from __future__ import annotations

from typing import Callable, Dict, Sequence

from ..ports import TemplateStore


def ensure_templates(store: TemplateStore, model_files: Sequence[str],
                     is_file: Callable[[str], bool]) -> Dict[str, str]:
    """model file -> its template ("" when the model needs none), for each model file that exists, in order."""
    return {path: store.ensure(path) for path in model_files if is_file(path)}

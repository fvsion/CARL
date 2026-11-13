"""The thinking toggle's chat templates (implements TemplateStore, contract C-028; the layout C-217):
NAME.thinking-toggle.jinja, the model's own chat template with the rule of domain/server/thinking_template.py in
front. It is in the template/ folder of the model's own folder (gguf/MODEL/template/, A66); a model that is not in
one (a file directly in the models folder) has it in ~/models/templates.

A start asks for the template of its model. The file is written again when it is missing or empty, or when its
text is not what the rules make of the model's template now: a change of the rules, or of the model, rewrites it
(not the generator's modification time, which a checkout changes). A model whose template has no thinking switch
gets none, and a template that cannot be made leaves no file (its reason on stderr, as the launcher's generator
said it).
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from ...domain.memory.gguf import LOCAL_HEADER_BYTES, GGUFError, chat_template
from ...domain.server.launch_plan import template_file
from ...domain.server.thinking_template import patched
from .json_files import write_private

FILE_MODE = 0o644                                      # llama-server and the user read it


class TemplateFiles:
    """The templates folder (~/models/templates, for the models without a folder of their own). warn: where a reason
    goes (stderr). models_dir: the models folder, when the models' own folders hold the templates (it is read at the
    first template; "" or a folder that cannot be named: every template is in the templates folder)."""

    def __init__(self, folder: str, warn: Callable[[str], None], models_dir: Callable[[], str] = lambda: "") -> None:
        self.folder = folder
        self.warn = warn
        self.models_dir = models_dir

    def ensure(self, model_path: str) -> str:
        path = template_file(model_path, self.folder, self._models())
        text = self._text(model_path)
        if text is None:
            self._remove(path)
            return ""
        if self._current(path) != text:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)      # a folder that cannot be made is a warning
                write_private(path, text, mode=FILE_MODE, dir_mode=0o777)
            except OSError as e:
                self.warn(f"cannot write {path}: {e.strerror}")
                self._remove(path)
                return ""
        return path

    def _models(self) -> str:
        try:
            return self.models_dir()
        except Exception:                                  # a settings file that cannot be read: the templates folder
            return ""

    def _text(self, model_path: str) -> Optional[str]:
        """The patched template, or None (a reason was given, or the template has no switch: no reason)."""
        try:
            with open(model_path, "rb") as f:
                head = f.read(LOCAL_HEADER_BYTES)              # the metadata sits at the front
            tpl = chat_template(head)
        except OSError as e:
            self.warn(f"cannot read {model_path}: {e.strerror}")
            return None
        except GGUFError as e:
            self.warn(f"{e}: {model_path}")
            return None
        if tpl is None:
            self.warn("no tokenizer.chat_template in GGUF")
            return None
        return patched(tpl)

    @staticmethod
    def _current(path: str) -> Optional[str]:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

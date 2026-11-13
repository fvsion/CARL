"""Where a model's files are in the models folder (decisions A65, A66).

One folder per model, the quants inside it: gguf/<model>/<quant>/<file>.gguf (gguf/Qwen3.6-35B-A3B/UD-IQ3_XXS/
Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf). What belongs to the model as a whole has a folder of its own beside the quants:
one folder for each kind of drafter (A76: mtp/ for the MTP drafters only, dflash/, dspark/, eagle3/), vision/ (the
vision projector), template/ (the thinking toggle's chat template), imatrix/ (an importance matrix) and lora/
(adapters, later). The model and quant names come from the file name (split_file). The same layout holds on every
system and in a model library on a network share; mlx/ beside gguf/ has a folder for every model too.

A file directly in the models folder (the layout before A66) or directly in its model's folder works as well, and CARL
never moves a user's file by itself.

Paths are text here, each read in the flavour it is written in (domain/shared/paths.py).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Protocol, Tuple

from ..shared.paths import policy_for

PART_SUFFIX = ".aria2"                      # aria2c's resume file
MTP, DFLASH, DSPARK, EAGLE3 = "mtp", "dflash", "dspark", "eagle3"
VISION, TEMPLATE, LORA, IMATRIX = "vision", "template", "lora", "imatrix"
# The kinds of drafter (the catalogue's drafts: kind): the folder of the kind in the model's folder, and the start of
# its files' names (mtp-gemma-4-E4B-it-Q4_0.gguf, dflash-Qwen3.8-Flash-Next-Q8_0.gguf, dspark-DeepSeek-V4-Flash-0731-
# Q8_0.gguf, eagle3-gpt-oss-20b-Q8_0.gguf). mtp-file: an MTP drafter in a file of its own (not the head in the model's).
DRAFTER_KINDS: Dict[str, Tuple[str, str]] = {"mtp-file": (MTP, "mtp-"), "dflash": (DFLASH, "dflash"),
                                             "dspark": (DSPARK, "dspark-"), "eagle3": (EAGLE3, "eagle3-")}
DRAFTER_FOLDERS = tuple(folder for folder, _ in DRAFTER_KINDS.values())
BESIDE = (*DRAFTER_FOLDERS, VISION)         # the folders of files that belong to the model as a whole
SECTIONS = (*BESIDE, TEMPLATE, IMATRIX, LORA)       # the folders of a model that are not a quant
PROJECTOR_PREFIX = "mmproj"                 # a vision projector's file name (mmproj-F16.gguf)
FALLBACK_FOLDER = "model"
FALLBACK_QUANT = "default"                  # a file name that ends in no quant
_SPLIT_SUFFIX = re.compile(r"-\d{5}-of-\d{5}$")
# the end of a file name that names its quant: UD-IQ3_XXS, Q4_K_M, Q4_0, MXFP4, f16, i1-IQ3_XXS, IQ2-mix
_QUANT = r"(?:(?:UD|i1)-)?(?:IQ\d(?:_[A-Z0-9]+|-[a-z]+)*|Q\d(?:_[A-Z0-9]+)*|MXFP4|NVFP4|(?i:BF16|F16|F32))"
_NAME = re.compile(r"^(?P<model>.+?)[-.](?P<quant>" + _QUANT + r")$")


class Exists(Protocol):
    """The part of the ModelFolder port that the layout reads (the domain imports no port, rule R1)."""

    def exists(self, path: str) -> bool: ...


def drafter_kind(file: str) -> Optional[str]:
    """The kind of drafter a file name gives (mtp-, dflash, dspark-, eagle3-: DRAFTER_KINDS); None for any other file.
    A drafter whose file name starts with none of them (a maker's own name) is placed by the catalogue's kind."""
    base = policy_for(file).basename(file).lower()
    return next((kind for kind, (_, prefix) in DRAFTER_KINDS.items() if base.startswith(prefix)), None)


def is_drafter(file: str) -> bool:
    """The file is a drafter of any kind: it goes in the folder of its kind in the folder of its model."""
    return drafter_kind(file) is not None


def is_projector(file: str) -> bool:
    """The file is a vision projector (mmproj*.gguf): it goes in the vision/ folder of its model."""
    return policy_for(file).basename(file).lower().startswith(PROJECTOR_PREFIX)


def section_of(file: str) -> Optional[str]:
    """The folder beside the quants that a file name puts a file in (mtp, dflash, dspark, eagle3, vision); None: a quant."""
    kind = drafter_kind(file)
    if kind is not None:
        return DRAFTER_KINDS[kind][0]
    return VISION if is_projector(file) else None


def split_file(file: str) -> Tuple[str, str]:
    """(model, quant) of a file name: the folders it goes in. The base name without .gguf, without the part number
    of a split file and without mtp-, ends in the quant: Qwen3.6-35B-A3B-UD-IQ3_XXS is (Qwen3.6-35B-A3B, UD-IQ3_XXS),
    gpt-oss-20b-MXFP4 is (gpt-oss-20b, MXFP4). A name that ends in no quant is its own model, with the quant default."""
    base = policy_for(file).basename(file)
    stem = base[:-5] if base.lower().endswith(".gguf") else base
    stem = _SPLIT_SUFFIX.sub("", stem).strip()
    kind = drafter_kind(base)
    if kind is not None:
        stem = stem[len(DRAFTER_KINDS[kind][1]):].lstrip("-")
    found = _NAME.match(stem)
    model, quant = (found.group("model"), found.group("quant")) if found else (stem, FALLBACK_QUANT)
    return (model.strip() if model.strip() not in ("", ".", "..") else FALLBACK_FOLDER), quant


def names_model(folder: str, model: str) -> bool:
    """The folder is the folder of the model that a file name gives (split_file): the same name, or the file name has
    a maker's prefix before it (Qwen_Qwen3.6-35B-A3B-IQ3_M.gguf is in Qwen3.6-35B-A3B/IQ3_M/, as a model library's shelf
    has it). Case does not matter."""
    f, m = folder.lower(), model.lower()
    return bool(f) and (m == f or (m.endswith(f) and m[-len(f) - 1] in "_-. "))


def folder_path(models_dir: str, file: str) -> str:
    """Where a model's file goes: models_dir/MODEL/QUANT/FILE (a drafter: models_dir/MODEL/mtp/FILE)."""
    pp = policy_for(models_dir)
    base = pp.basename(file)
    model, quant = split_file(base)
    return pp.join(models_dir, model, section_of(base) or quant, base)


def model_dir_path(models_dir: str, file: str) -> str:
    """A place older than the quant folders: the file directly in its model's folder (models_dir/MODEL/FILE)."""
    pp = policy_for(models_dir)
    base = pp.basename(file)
    return pp.join(models_dir, split_file(base)[0], base)


def flat_path(models_dir: str, file: str) -> str:
    """The oldest place of a model's file: directly in the models folder."""
    pp = policy_for(models_dir)
    return pp.join(models_dir, pp.basename(file))


def places(models_dir: str, file: str) -> List[str]:
    """The three places a file in the models folder can be, the layout's own first."""
    return [folder_path(models_dir, file), model_dir_path(models_dir, file), flat_path(models_dir, file)]


def relative_place(file: str) -> str:
    """Where the layout puts a file, from the models folder, as text for a note (Model/Quant/FILE)."""
    base = policy_for(file).basename(file)
    model, quant = split_file(base)
    return f"{model}/{section_of(base) or quant}/{base}"


def _present(path: str, files: Exists) -> bool:
    return files.exists(path) or files.exists(path + PART_SUFFIX)


def locate(models_dir: str, file: str, files: Exists) -> str:
    """The path of a model's file: the first of its places where the file (or its resume file) is, else the layout's
    own (where a download puts it)."""
    options = places(models_dir, file)
    return next((p for p in options if _present(p, files)), options[0])


def locate_beside(model_path: str, file: str, files: Exists, models_dir: str = "", kind: str = MTP) -> str:
    """The path of a file that belongs to a model as a whole (its MTP drafter, kind mtp; a vision projector, kind
    vision): in that folder of the model when the file is there, else beside the model, else directly in the models
    folder (models_dir, when given), else the folder of the kind when the model has a folder, else beside the model."""
    pp = policy_for(model_path)
    base = pp.basename(file)
    folder = model_folder_of(model_path, models_dir or None)
    home = pp.join(folder, kind, base) if folder else None
    options = ([home] if home else []) + [pp.join(pp.dirname(model_path), base)]
    if models_dir:
        options += places(models_dir, base)
    return next((p for p in options if _present(p, files)), home or options[0])


def model_folder_of(path: str, models_dir: Optional[str] = None) -> Optional[str]:
    """The model's own folder when PATH is in it: models_dir/MODEL/QUANT/FILE, MODEL/mtp/FILE (or vision/) or, older,
    MODEL/FILE, with MODEL and QUANT the names split_file makes of the file; else None. models_dir, when given, must
    be the folder that holds the model's folder (a drafter or projector there may belong to a model of another
    name, a custom model's)."""
    pp = policy_for(path)
    base = pp.basename(path)
    parent = pp.dirname(path)
    grand = pp.dirname(parent)
    model, quant = split_file(base)
    inner = section_of(base) or quant
    found: Optional[str] = None
    if pp.same(pp.basename(parent), inner) and names_model(pp.basename(grand), model):
        found = grand
    elif names_model(pp.basename(parent), model):
        found = parent
    elif models_dir and pp.basename(parent).lower() in BESIDE:
        found = grand
    if found is None or (models_dir is not None and not pp.same(pp.dirname(found), models_dir)):
        return None
    return found


def in_models(path: str, models_dir: str) -> Optional[str]:
    """The file name of PATH when it is in the models folder: directly in it, or in the folder of its model (in a
    quant folder, in mtp/ or vision/, or directly in the model's folder); else None."""
    pp = policy_for(models_dir)
    if pp.same(pp.dirname(path), models_dir) or model_folder_of(path, models_dir) is not None:
        return pp.basename(path)
    return None


def in_place(path: str, models_dir: str) -> bool:
    """PATH is where the layout puts a file: in a quant folder of its model, or in its mtp/ or vision/ folder."""
    folder = model_folder_of(path, models_dir)
    return folder is not None and not policy_for(path).same(policy_for(path).dirname(path), folder)


def tidy_folders(path: str, models_dir: str) -> List[str]:
    """The folders a deleted file may leave empty, the innermost first: its quant (mtp/ ...) folder and its model's.
    A file directly in the models folder (or outside it) leaves none."""
    folder = model_folder_of(path, models_dir)
    if folder is None:
        return []
    parent = policy_for(path).dirname(path)
    return [parent, folder] if not policy_for(path).same(parent, folder) else [folder]


def variants(path: str, models_dir: str) -> List[str]:
    """The places a file in the models folder can be (places()); [PATH] for a path outside the models folder."""
    base = in_models(path, models_dir)
    return [path] if base is None else places(models_dir, base)


def relocate(path: str, models_dir: str, files: Exists) -> str:
    """The path a recorded file (models.json) is at now: PATH when it is there, else its other place in the models
    folder when the file is there, else PATH. A model moved into its folder (or out of it) keeps its record."""
    if _present(path, files):
        return path
    for p in variants(path, models_dir):
        if _present(p, files):
            return p
    return path


def _root(path: str) -> str:
    """The folder that holds the model's folder when PATH is in one, else the folder of PATH."""
    pp = policy_for(path)
    folder = model_folder_of(path)
    return pp.dirname(folder) if folder else pp.dirname(path)


def same_file(a: str, b: str) -> bool:
    """The two paths name one model file: equal, or the places of one file (in its model's folder, in a quant folder
    of it, or in the folder that holds the model's folder)."""
    pp = policy_for(a)
    return pp.same(a, b) or (pp.same(pp.basename(a), pp.basename(b)) and pp.same(_root(a), _root(b)))

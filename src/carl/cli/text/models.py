"""The text of `carl models`: every model with its size (GB), its download status, its source and what it is, wrapped
to the width; then where the models folder is and how to add a model."""
from __future__ import annotations

from typing import List, Mapping, Optional, Sequence

from ...domain.catalog.drafts import KIND_WORDS
from ...domain.catalog.layout import relative_place
from ...domain.catalog.quants import group_models, mtp_kind, mtp_mark, quant_of
from ...domain.shared.platform_words import PlatformWords
from ...domain.shared.types import ModelInfo
from ...domain.shared.units import file_size
from ...ui.layout import wrap

STATUS_TEXT = {"downloaded": "downloaded", "missing": "not downloaded", "partial": "partial"}
SOURCE_TEXT = {"catalog": "catalogue", "hf": "Hugging Face", "file": "models folder"}


def model_lines(models: List[ModelInfo], words: PlatformWords, w: int, models_dir: str,
                free: Optional[int], flat: Sequence[str] = (), defaults: Mapping[str, str] = {}) -> List[str]:
    """Every model: name, file size (GB), download status, source, and what it is (wrapped to w). A model with several
    quantizations (decision A68) is listed once, its quantizations under its name, each with its own size, status and
    text; defaults: the entry a start takes for each such model (by the model's name). models_dir: the models folder
    as the user writes it (~); free: the disk's free bytes there (None: unknown); flat: the file names of downloaded
    models that are not where the layout puts them (the older layouts), for the note on moving them."""
    cmd, here = words.command, words.machine
    out: List[str] = []
    groups = group_models(models)
    names = [g.name if g.several else g.entries[0].get("name", "") for g in groups]
    names += [quant_label(m, defaults.get(g.name) == m.get("name")) for g in groups if g.several for m in g.entries]
    nw = max([len("model")] + [len(n) for n in names])
    head = f"{'model':<{nw}}  {'size':>8}  {'status':<14}  {'source':<13}"
    side = w - len(head) - 2 >= 40                  # the description beside the row, else below it
    out.append((head + "  about") if side else head.rstrip())
    for g in groups:
        if g.several:
            out.append(f"{g.name:<{nw}}  {'':>8}  {'':<14}  {SOURCE_TEXT.get(str(g.entries[0].get('source')), ''):<13}"
                       .rstrip())
        for m in g.entries:
            name = m.get("name", "")
            label = quant_label(m, defaults.get(g.name) == name) if g.several else name
            about = f"{m.get('role')} (your card)" if m.get("custom") and m.get("role") else str(m.get("summary", ""))
            notes = []
            if (m.get("local") or {}).get("tune"):
                notes.append(f"Auto-tune done on {here}.")
            if m.get("draft") and m.get("status") == "downloaded" and m.get("draft_status") != "downloaded":
                notes.append(f"The MTP drafter is not downloaded: {cmd} download {name}")
            for d in m.get("drafts", []):                 # the drafters of other kinds: DFlash, DSpark, EAGLE-3 (WCATX)
                if d.get("kind") != "mtp-file" and m.get("status") == "downloaded" and d.get("status") != "downloaded":
                    notes.append(f"The {KIND_WORDS.get(str(d.get('kind')), 'drafter')} is not downloaded: "
                                 f"{cmd} download {name}")
            if m.get("draft_offer"):
                notes.append(f"A Gemma 4 MTP drafter fits this model (the one of {m.get('draft_for')}): "
                             f"{cmd} download {name}")
            text = " ".join([about.rstrip()] + notes).strip()
            row = (f"{label:<{nw}}  {file_size(m.get('bytes', 0)):>8}  "
                   f"{STATUS_TEXT.get(str(m.get('status')), str(m.get('status'))):<14}"
                   f"  {'' if g.several else SOURCE_TEXT.get(str(m.get('source')), str(m.get('source'))):<13}")
            if side:
                out.extend(wrap(text, w, row + "  ", " " * (len(head) + 2)))
            else:
                out.append(row.rstrip())
                if text:
                    out.extend(wrap(text, w, "    "))
    out.append("")
    several = any(g.several for g in groups)
    for line in (f"The models folder is {models_dir}. The disk has "
                 f"{file_size(free) if free is not None else 'an unknown size'} free.",
                 "Each model has its own folder in it, with a folder for each quantization.",
                 *([f"A model with several quantizations is listed once, each quantization under its name. {cmd} start "
                    f"--model NAME starts the one marked default (the one you chose last, else the one tuned last, else "
                    f"the largest that fits); {cmd} start --model NAME --quant Q starts another. MTP marks a quantization "
                    "whose file has an MTP head (MTP and n-gram speculation); without it speculation is n-gram only."]
                 if several else []),
                 "Status: downloaded, not downloaded, or partial (the download stopped: run it again to continue).",
                 f"To download a model from Hugging Face: {cmd} download hf:OWNER/REPO/FILE.gguf. To see the files "
                 f"of a repository: {cmd} download hf:OWNER/REPO."):
        out.extend(wrap(line, w))
    if flat:
        out.extend(wrap(flat_note(flat), w))
    if any(m.get("custom") for m in models):
        out.extend(wrap("To describe a custom model (its role, what it is good for, its quality rank): "
                        f"{cmd} card NAME, or e in Settings > Models of the dashboard.", w))
    return out


def quant_label(m: ModelInfo, default: bool) -> str:
    """A quantization under its model's name: its name, (default), its MTP mark (MTP: the head is in its file; A73)."""
    mark = mtp_mark(mtp_kind(m))
    return f"  {quant_of(m)}{' (default)' if default else ''}{' ' + mark if mark else ''}"


def flat_note(flat: Sequence[str]) -> str:
    """The note on files that are not where the layout puts them (directly in the models folder, or directly in the
    model's folder): they work as they are, and how to move each. CARL moves no file by itself."""
    shown = ", ".join(flat[:4]) + (f" and {len(flat) - 4} more" if len(flat) > 4 else "")
    return (f"Files that are not in the layout work as they are: {shown}. To put a model in the layout, make its "
            "folders in the models folder and move the file into them, a drafter into the mtp folder of its model "
            f"(for example {flat[0]} to {relative_place(flat[0])}). CARL moves no file by itself.")

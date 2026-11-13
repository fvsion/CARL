"""The texts of `carl cache`: what the disk cache holds (show), what a trim did, and what a clear removed."""
from __future__ import annotations

from typing import List

from ...application.disk_cache import CacheReport, TrimResult
from ...domain.shared.units import file_size
from ...ui.layout import wrap
from ..commands.base import count


def show_lines(report: CacheReport, folder: str, saving: int, w: int) -> List[str]:
    """The summary, then (when the cache holds files) one row per file, newest first. folder: the folder as the user
    writes it (~); saving: the bytes the sessions stored as changes save."""
    s = report.settings
    files = report.files
    prompts, packed = report.prompts, report.packed
    out = wrap(f"The disk cache ({folder}) uses {file_size(report.used)} of its limit of {s.disk_gb} GB. It holds "
               f"{count(prompts, 'saved prompt')} and {count(len(files) - prompts, 'saved session')}. Saved prompts: "
               f"{'on' if s.prefix else 'off'}. Saved sessions: {'on' if s.sessions else 'off'}.", w)
    if packed:
        out += wrap(f"{count(packed, 'saved session')} {'is' if packed == 1 else 'are'} stored as the changes to "
                    f"{'its' if packed == 1 else 'their'} saved prompt. This saves {file_size(saving)}.", w)
    if not files:
        return out
    out.append("")
    rows = []
    for f in files:
        kind = "saved prompt" if f.prompt else "saved session*" if f.packed else "saved session"
        name = report.names.get(f.session, "")
        rows.append((kind, f.label + (f" ({name})" if name else ""), file_size(f.bytes)))
    kw = max(len(r[0]) for r in rows)
    sw = max(len(r[2]) for r in rows)
    nw = max(10, w - kw - sw - 6)
    out.append(f"  {'kind':<{kw}}  {'model · agent or session':<{nw}}  {'size':>{sw}}"[:w].rstrip())
    for kind, what, size in rows:
        what = what if len(what) <= nw else what[:nw - 1] + "…"
        out.append(f"  {kind:<{kw}}  {what:<{nw}}  {size:>{sw}}")
    if packed:
        out.append("  * stored as the changes to its saved prompt")
    return out


def trim_lines(result: TrimResult) -> List[str]:
    """What a trim did: the sessions it stored as changes, then the files it removed (or that the cache fits)."""
    out = []
    if result.packed:
        out.append(f"CARL stored {count(result.packed, 'saved session')} as the changes to its saved prompt.")
    gb = result.settings.disk_gb
    out.append(f"CARL removed {count(len(result.removed), 'file')}: the disk cache was larger than its limit of {gb} "
               f"GB." if result.removed else f"The disk cache is in its limit of {gb} GB.")
    return out


def clear_line(removed: int, folder: str) -> str:
    return f"CARL removed {count(removed, 'file')} from the disk cache ({folder})."

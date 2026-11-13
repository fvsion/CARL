"""The GPU limit, from the package (the same code the launcher and carl fit use; the limit: carl.application.fit)."""
from __future__ import annotations

from typing import Tuple


def gpu_limit() -> Tuple[int, str]:
    """(bytes, how it was found) the GPU may use (may run a Swift probe, up to 2 min, once): the budget's builder,
    application/fit.py, through the dashboard entry's API (imported here, at the first call, as the store does)."""
    from ..composition.entries import build_dashboard
    limit, how = build_dashboard().api().gpu_limit()
    return int(limit), str(how)

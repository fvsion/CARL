"""Terminal escape codes in text: the one ANSI stripper (the dashboard, llama-fit and the log filter use it).

A code is a CSI sequence: ESC [, then any bytes up to the final byte (@ to ~). A sequence cut off at the end of the
text counts as a code too (it goes up to the end).
"""
from __future__ import annotations

import re

ANSI = re.compile(r"\x1b\[[^@-~]*(?:[@-~]|\Z)")
R, GRN, YEL, RED = "\x1b[0m", "\x1b[32m", "\x1b[33m", "\x1b[31m"      # reset, green, yellow, red


def strip_ansi(text: str) -> str:
    """text without its ANSI codes (llama.cpp colours its output on a terminal)."""
    return ANSI.sub("", text)

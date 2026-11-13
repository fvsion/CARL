"""The Windows console the dashboard draws on (implements Terminal, contract C-032; W6.8): the console's VT modes (the
same escape sequences as a Mac's terminal: the alternate screen, the cursor, SGR mouse reports), a reader thread with
a wake, a console control handler, and the size read again at each frame (the loop asks for it: a resized window
shows at the next frame).

  input   ENABLE_VIRTUAL_TERMINAL_INPUT: keys, the arrows and the mouse come as VT sequences, as on a Mac. Line input,
          echo and processed input are off, so Ctrl-C comes as a key (\\x03): it calls on_interrupt and wakes the
          wait, as SIGINT does on a Mac, and never reaches a process that shares the console. Quick edit is off, so a
          click is a mouse report, not a selection.
  output  ENABLE_VIRTUAL_TERMINAL_PROCESSING; the text goes through WriteConsoleW (UTF-16, whatever the console's
          code page), or as UTF-8 to a pipe.
  events  Ctrl-Break and a Ctrl-C sent by another program call on_interrupt too; closing the window, logging off and
          shutting down give the console back first and end the process at once (exit), as SIGTERM and SIGHUP do on
          a Mac.

A stdin that is not a console (a pipe, ssh without -t) is read as bytes by the same thread. Every Win32 call is made
through ctypes when it runs, never at import (rule R4): the module imports on every OS.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import queue
import shutil
import sys
import threading
from typing import Any, Callable, List, Optional, Tuple

# A Mac's modes (the alternate screen, the cursor, mouse reports in SGR form) and, only here, auto-wrap off (DECAWM,
# ?7). The console host (ConPTY) draws a row that fills the line, as the dashboard's rows do, by writing it and
# letting the terminal wrap to the next row, so one cell that Windows Terminal counts differently from the console
# host moves every row after it; with auto-wrap off it ends each row with a line break and the rows stay apart
# (decision A48, measured on the Windows test PC with a ConPTY).
ENTER = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?1000h\x1b[?1006h"
LEAVE = "\x1b[?1000l\x1b[?1006l\x1b[?7h\x1b[?25h\x1b[?1049l"
DEFAULT_SIZE = (120, 36)                                # when the size can't be read
STD_INPUT_HANDLE, STD_OUTPUT_HANDLE = -10, -11
ENABLE_PROCESSED_INPUT, ENABLE_LINE_INPUT, ENABLE_ECHO_INPUT = 0x0001, 0x0002, 0x0004
ENABLE_WINDOW_INPUT, ENABLE_MOUSE_INPUT, ENABLE_QUICK_EDIT_MODE = 0x0008, 0x0010, 0x0040
ENABLE_EXTENDED_FLAGS, ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0080, 0x0200
ENABLE_PROCESSED_OUTPUT, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0001, 0x0004
CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT = 0, 1, 2, 5, 6
CTRL_C = "\x03"
READ_CHARS = 4096
WRITE_CHARS = 8192                                      # WriteConsoleW's piece, in UTF-16 units (a frame is far larger)


def input_mode(saved: int) -> int:
    """The console input mode for the dashboard: VT input and the extended flags on; line input, echo, processed
    input, quick edit, window and mouse records off (the mouse comes as VT reports)."""
    off = (ENABLE_PROCESSED_INPUT | ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_QUICK_EDIT_MODE
           | ENABLE_WINDOW_INPUT | ENABLE_MOUSE_INPUT)
    return (saved & ~off) | ENABLE_VIRTUAL_TERMINAL_INPUT | ENABLE_EXTENDED_FLAGS


def output_mode(saved: int) -> int:
    """The console output mode: VT processing on."""
    return saved | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING


def pieces(text: str, size: int = WRITE_CHARS) -> List[Tuple[str, int]]:
    """text cut into pieces of at most size UTF-16 units, each with its length in those units. WriteConsoleW counts
    UTF-16 units, and Python counts code points: a character above U+FFFF (the emoji in the header) is one of the
    first and two of the second. A piece measured in code points was therefore written short, and the console lost its
    last character, every frame, at the same place: the N of Needs on the Settings tab while a model loaded (decision
    A48, measured on the Windows test PC). A piece never ends between the two units of one character."""
    size = max(size, 2)
    if len(text.encode("utf-16-le", "surrogatepass")) // 2 == len(text):      # nothing above U+FFFF: units = code points
        return [(text[i:i + size], len(text[i:i + size])) for i in range(0, len(text), size)]
    out: List[Tuple[str, int]] = []
    start = units = 0
    for i, ch in enumerate(text):
        n = 2 if ord(ch) > 0xFFFF else 1
        if units + n > size:
            out.append((text[start:i], units))
            start, units = i, 0
        units += n
    if start < len(text):
        out.append((text[start:], units))
    return out


def split_interrupt(text: str) -> Tuple[str, bool]:
    """(the keys without Ctrl-C, whether Ctrl-C was among them)."""
    return text.replace(CTRL_C, ""), CTRL_C in text


class Win32Console:
    """The console calls (kernel32 through ctypes, loaded at the first call). A test gives its own object with the
    same methods."""

    def __init__(self) -> None:
        self._k: Any = None
        self._handler: Any = None                          # the ctypes callback, kept alive while it is installed

    def k(self) -> Any:
        if self._k is None:
            k = ctypes.WinDLL("kernel32", use_last_error=True)         # type: ignore[attr-defined]
            vp, dw = ctypes.c_void_p, ctypes.c_uint32
            k.GetStdHandle.restype = vp
            k.GetStdHandle.argtypes = [dw]
            k.GetConsoleMode.argtypes = [vp, ctypes.POINTER(dw)]
            k.SetConsoleMode.argtypes = [vp, dw]
            k.ReadConsoleW.argtypes = [vp, ctypes.c_wchar_p, dw, ctypes.POINTER(dw), vp]
            k.WriteConsoleW.argtypes = [vp, ctypes.c_wchar_p, dw, ctypes.POINTER(dw), vp]
            self._k = k
        return self._k

    def handle(self, which: int) -> Optional[int]:
        try:
            h = self.k().GetStdHandle(ctypes.c_uint32(which & 0xFFFFFFFF))
        except (AttributeError, OSError):
            return None
        return h or None

    def get_mode(self, h: Optional[int]) -> Optional[int]:
        """The console mode of h, None when h is not a console."""
        if h is None:
            return None
        mode = ctypes.c_uint32()
        return int(mode.value) if self.k().GetConsoleMode(h, ctypes.byref(mode)) else None

    def set_mode(self, h: Optional[int], mode: int) -> bool:
        return h is not None and bool(self.k().SetConsoleMode(h, mode))

    def read(self, h: int) -> Optional[str]:
        """What the console has (waits for at least one character); None when the read fails."""
        buf = ctypes.create_unicode_buffer(READ_CHARS)
        got = ctypes.c_uint32()
        if not self.k().ReadConsoleW(h, buf, READ_CHARS, ctypes.byref(got), None):
            return None
        return buf.value[:got.value] if got.value else ""

    def write(self, h: int, text: str) -> bool:
        k, done = self.k(), ctypes.c_uint32()
        for part, units in pieces(text):
            if not k.WriteConsoleW(h, part, units, ctypes.byref(done), None):
                return False
        return True

    def on_event(self, fn: Callable[[int], bool]) -> bool:
        """Install fn as the console control handler (it says whether the event was handled)."""
        proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint32)         # type: ignore[attr-defined]
        self._handler = proto(lambda event: 1 if fn(int(event)) else 0)
        k = self.k()
        k.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return bool(k.SetConsoleCtrlHandler(self._handler, 1))


class WindowsConsole:
    """The interactive console on stdin / stdout (implements Terminal, contract C-032). console: the Win32 calls; exit
    ends the process after a close, log-off or shut-down event (a test gives its own); fd_in, fd_out: the file
    descriptors when they are not a console."""

    def __init__(self, console: Optional[Win32Console] = None, exit: Callable[[int], None] = os._exit,
                 fd_in: Optional[int] = None, fd_out: Optional[int] = None) -> None:
        self.con = console or Win32Console()
        self.exit = exit
        self.fd = sys.stdin.fileno() if fd_in is None else fd_in
        self.out = sys.stdout.fileno() if fd_out is None else fd_out
        self.h_in = self.con.handle(STD_INPUT_HANDLE)
        self.h_out = self.con.handle(STD_OUTPUT_HANDLE)
        self.saved_in = self.con.get_mode(self.h_in)        # None: not a console
        self.saved_out = self.con.get_mode(self.h_out)
        self.keys: "queue.Queue[Optional[str]]" = queue.Queue()
        self.on_interrupt: Callable[[], None] = lambda: None
        self._entered = False
        self._lock = threading.Lock()

    def size(self) -> Tuple[int, int]:
        cols, rows = shutil.get_terminal_size(DEFAULT_SIZE)
        return cols, rows

    def write(self, text: str) -> None:
        with self._lock:
            if self.saved_out is not None and self.h_out is not None and self.con.write(self.h_out, text):
                return
            data = memoryview(text.encode("utf-8", "replace"))
            while data:
                data = data[os.write(self.out, data):]

    def enter(self, on_interrupt: Callable[[], None]) -> None:
        """VT input and output, the control handler, the reader thread, the alternate screen and mouse reports."""
        self.on_interrupt = on_interrupt
        if self.saved_in is not None:
            self.con.set_mode(self.h_in, input_mode(self.saved_in))
        if self.saved_out is not None:
            self.con.set_mode(self.h_out, output_mode(self.saved_out))
        try:
            self.con.on_event(self.event)
        except (AttributeError, OSError):
            pass
        atexit.register(self.leave)            # never leave mouse reporting on in the user's console
        self._entered = True
        threading.Thread(target=self._reader, daemon=True).start()
        self.write(ENTER)

    def leave(self) -> None:
        """Mouse off, cursor on, main screen, the console modes as they were. Safe to call more than once and from
        the control handler's thread."""
        if not self._entered:
            return
        self.write(LEAVE)
        if self.saved_in is not None:
            self.con.set_mode(self.h_in, self.saved_in)
        if self.saved_out is not None:
            self.con.set_mode(self.h_out, self.saved_out)

    def event(self, kind: int) -> bool:
        """The console control handler: Ctrl-C and Ctrl-Break ask to quit (on_interrupt); a close, log-off or
        shut-down gives the console back and ends the process."""
        if kind in (CTRL_C_EVENT, CTRL_BREAK_EVENT):
            self.on_interrupt()
            self.wake()
            return True
        if kind in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            self.leave()
            self.exit(0)
            return True
        return False

    def got(self, text: str) -> None:
        """Keys the reader read: Ctrl-C asks to quit; the rest waits for read_key."""
        keys, interrupted = split_interrupt(text)
        if interrupted:
            self.on_interrupt()
        if keys:
            self.keys.put(keys)
        elif interrupted:
            self.wake()

    def _reader(self) -> None:
        while True:
            if self.saved_in is not None and self.h_in is not None:
                text = self.con.read(self.h_in)
                if text is None:
                    return
            else:
                try:
                    data = os.read(self.fd, READ_CHARS)
                except OSError:
                    return
                if not data:
                    return
                text = data.decode("utf-8", "replace")
            if text:
                self.got(text)

    def read_key(self, timeout: float) -> Optional[str]:
        try:
            first = self.keys.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None
        parts: List[str] = [first] if first else []
        while True:                                        # what else came in: one read, as a Mac's read gives it
            try:
                more = self.keys.get_nowait()
            except queue.Empty:
                break
            if more:
                parts.append(more)
        return "".join(parts) or None

    def wake(self) -> None:
        self.keys.put(None)

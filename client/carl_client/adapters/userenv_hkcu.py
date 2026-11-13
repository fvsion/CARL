"""OpenCode's tool switches on Windows (the UserEnvironment port there): the user's environment variables in
HKCU\\Environment (no admin rights), then WM_SETTINGCHANGE, so a program started after it (a new terminal, OpenCode)
gets them. OpenCode reads these switches only from the environment. ~/.config/carl/opencode.env, the same text as on
macOS and Linux, is the record of what CARL set: a variable CARL set and that is now off is taken out, but only while
it still has the value CARL gave it (a value the user changed stays). NO_PROFILE=1 (profile False): the registry and
the record stay as they are, and the report says which commands to run.

Dry run: the registry is read but not written, no message goes out and the record is not written; `planned` lists
what would change. W7.3 checked the adapter this way on Windows; W7.5 set and removed the real variables on the Windows test PC.

The setup also puts the programs' folder (~/.local, npm's Windows layout) on the user's Path here: add_to_user_path
appends it to the Path value, of the same type as before (REG_EXPAND_SZ keeps its %VARIABLES%)."""
from __future__ import annotations

import ntpath
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..domain.merge_opencode import ENV_FILE

KEY = "Environment"                                  # under HKEY_CURRENT_USER
WHERE = "HKCU\\Environment"
EXPORT_RE = re.compile(r"^export ([A-Z][A-Z0-9_]*)=(\S*)")


def env_vars(text: Optional[str]) -> Dict[str, str]:
    """The variables of the env file's text (its `export NAME=value` lines; the comments go)."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        m = EXPORT_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


class WinRegistry:
    """HKCU\\Environment through winreg (imported here: Windows only). Each value is written as REG_SZ."""

    def _key(self, write: bool) -> Any:
        import winreg
        access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, access)

    def get(self, name: str) -> Optional[str]:
        import winreg
        with self._key(False) as k:
            try:
                value, _ = winreg.QueryValueEx(k, name)
            except FileNotFoundError:
                return None
        return str(value)

    def set(self, name: str, value: str) -> None:
        import winreg
        with self._key(True) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        with self._key(True) as k:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass

    def get_typed(self, name: str) -> Optional[Tuple[str, int]]:
        """A value and its registry type (None when there is none)."""
        import winreg
        with self._key(False) as k:
            try:
                value, kind = winreg.QueryValueEx(k, name)
            except FileNotFoundError:
                return None
        return str(value), int(kind)

    def set_typed(self, name: str, value: str, kind: int) -> None:
        import winreg
        with self._key(True) as k:
            winreg.SetValueEx(k, name, 0, kind, value)


def broadcast() -> None:
    """WM_SETTINGCHANGE with "Environment" to every top window (Explorer then starts programs with the new values);
    a window that hangs is skipped after 5 s."""
    import ctypes
    from ctypes import wintypes
    send = ctypes.windll.user32.SendMessageTimeoutW
    # the last argument is a PDWORD_PTR: a pointer-sized result (a DWORD there would be overwritten past its end)
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_wchar_p, wintypes.UINT, wintypes.UINT,
                     ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_size_t
    result = ctypes.c_size_t()
    send(0xFFFF, 0x001A, 0, KEY, 0x0002, 5000, ctypes.byref(result))


REG_SZ, REG_EXPAND_SZ = 1, 2                          # winreg's types of a string value


def on_path(folder: str, path_value: str, expand: Callable[[str], str] = ntpath.expandvars) -> bool:
    """folder is one of the Path's entries: case, a final backslash and %VARIABLES% do not count."""
    def norm(p: str) -> str:
        return ntpath.normcase(expand(p.strip().strip('"'))).rstrip("\\")
    return norm(folder) in {norm(p) for p in path_value.split(";") if p.strip()}


def add_to_user_path(folder: str, registry: Optional[Any] = None,
                     notify: Optional[Callable[[], None]] = None) -> bool:
    """folder at the end of the user's Path (HKCU\\Environment), unless it is there; then WM_SETTINGCHANGE. True
    when it was added. A Path of the user's keeps its type; a new one is REG_EXPAND_SZ, as Windows makes it."""
    reg = registry if registry is not None else WinRegistry()
    cur = reg.get_typed("Path")
    value, kind = cur if cur is not None else ("", REG_EXPAND_SZ)
    if on_path(folder, value):
        return False
    reg.set_typed("Path", (value.rstrip(";") + ";" if value.strip() else "") + folder, kind)
    (notify if notify is not None else broadcast)()
    return True


class HkcuEnvironment:
    """The variables in HKCU\\Environment and their record under `home`. cf: the installer's ConfigFiles (save_text,
    short); fs: the Files port; registry: get/set/delete of one value (default: WinRegistry); notify: the
    WM_SETTINGCHANGE call (default: broadcast); dry_run: see the module's text."""

    def __init__(self, cf: Any, fs: Any, home: str, registry: Optional[Any] = None,
                 notify: Optional[Callable[[], None]] = None, dry_run: bool = False) -> None:
        self.cf = cf
        self.fs = fs
        self.home = home
        self.registry = registry if registry is not None else WinRegistry()
        self.notify = notify if notify is not None else broadcast
        self.dry_run = dry_run
        self.planned: List[Tuple[str, ...]] = []

    @property
    def env_path(self) -> str:
        return os.path.join(self.home, ".config", "carl", ENV_FILE)

    def changes(self, text: Optional[str]) -> List[Tuple[str, ...]]:
        """What the registry needs for TEXT: ("set", NAME, VALUE) and ("delete", NAME), in name order."""
        want = env_vars(text)
        had = env_vars(self.fs.read(self.env_path))
        out: List[Tuple[str, ...]] = []
        for name in sorted(set(want) | set(had)):
            cur = self.registry.get(name)
            if name in want:
                if cur != want[name]:
                    out.append(("set", name, want[name]))
            elif cur is not None and cur == had[name]:          # ours, and the user did not change it
                out.append(("delete", name))
        return out

    def apply(self, text: Optional[str], summary: str, profile: bool, report: Any) -> None:
        if not profile:
            want = env_vars(text)
            if want:
                report.add("kept", "your user environment (NO_PROFILE=1). For the tool switches of OpenCode, run "
                                   "these commands:\n" + "\n".join(f"setx {k} {v}" for k, v in sorted(want.items())))
            return
        todo = self.changes(text)
        if self.dry_run:
            self.planned += todo + ([("notify",)] if todo else [])
        else:
            for op in todo:
                if op[0] == "set":
                    self.registry.set(op[1], op[2])
                else:
                    self.registry.delete(op[1])
            if todo:
                self.notify()
            if text is not None:
                self.cf.save_text(self.env_path, text, mode=0o644, backup=False)
            elif self.fs.exists(self.env_path):
                self.fs.remove(self.env_path)
        if todo:
            if text is not None:
                report.add("updated", f"OpenCode tools ({WHERE}): {summary} (open a new terminal)")
            else:
                report.add("removed", f"OpenCode tools ({WHERE}): all off (open a new terminal)")

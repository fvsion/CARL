"""The glossary's names for values the server and config.json keep in their own form
(reference/glossary.md): speculation modes, the context memory type, the network, the download
status, Auto fit's goal and candidates, counts with their plural, and the server's uptime. Config
keys and flags keep their names; the full detail level shows them next to these words. Pure, except the platform's
words (command, computer, computer_cap, the_computer, the_computer_cap, update_hint, other_computer, other_computer_cap,
machine_noun, file_manager, vm_software, os_label, setup_elsewhere, setup_command): they come from the dashboard's platform words (composition/entries.py build_dashboard) when they are
asked for, never at import."""
from __future__ import annotations

from typing import Optional

from ..domain.shared.platform_words import PlatformWords
from ..domain.shared.units import duration, etime_seconds

SPEC_NAMES = {"none": "none", "ngram-mod": "n-gram", "draft-mtp": "MTP", "draft-mtp,ngram-mod": "MTP + n-gram",
              "draft-dflash": "DFlash", "draft-dspark": "DSpark", "draft-eagle3": "EAGLE-3",
              "draft-dflash,ngram-mod": "DFlash + n-gram", "draft-dspark,ngram-mod": "DSpark + n-gram",
              "draft-eagle3,ngram-mod": "EAGLE-3 + n-gram"}
KV_NAMES = {"q4_0": "q4 (small)", "q8_0": "q8 (large)", "f16": "f16 (largest)"}
STATUS_NAMES = {"downloaded": "downloaded", "partial": "partial", "missing": "not downloaded", "bad": "bad file"}
GOAL_NAMES = {"everyday": "everyday (fast first)", "hard-code": "hard code (better code, slower)"}
SCOPE_NAMES = {"catalogue": "catalogue", "downloaded": "downloaded only"}
DEPTH_NAMES = {"quick": "quick", "default": "normal", "long": "long"}
THINKING_NAMES = {"on-off": "on / off", "effort": "levels (low, medium, xhigh) and off", "always": "always on",
                  "none": "no thinking"}


def _words() -> PlatformWords:
    from ..composition.entries import build_dashboard
    return build_dashboard().words


def command() -> str:
    """How the user starts CARL in a text: carl (the platform words), or CARL_CMD."""
    return _words().command


def computer() -> str:
    """This computer in a sentence: this Mac."""
    return _words().machine


def computer_cap() -> str:
    """This computer, as a sentence starts: This Mac."""
    return _words().machine_cap()


def os_and_apps() -> str:
    """What the RAM's reserve is kept free for: macOS and apps, Windows and apps."""
    return _words().os_and_apps


def the_computer() -> str:
    """This computer as a thing that sleeps or fails: the Mac."""
    return _words().the_machine


def the_computer_cap() -> str:
    """The same, as a sentence starts: The Mac."""
    m = the_computer()
    return m[:1].upper() + m[1:]


def update_hint() -> str:
    """How to update llama.cpp on this computer: brew upgrade llama.cpp (the HEALTH card; decision A35)."""
    return _words().update_hint()


def other_computer() -> str:
    """Another computer of its kind: another Mac."""
    a = _words().a_machine
    return "another " + (a[2:] if a.startswith("a ") else a)


def other_computer_cap() -> str:
    """The same, as a sentence starts: Another Mac."""
    c = other_computer()
    return c[:1].upper() + c[1:]


def machine_noun() -> str:
    """This kind of computer as a label: Mac (a Mac), computer (a computer)."""
    a = _words().a_machine
    return a[2:] if a.startswith("a ") else a


def os_label() -> str:
    """This computer's OS in a table: macOS, Windows, Linux."""
    return _words().os_label


def setup_command() -> str:
    """How the client setup starts on this computer: ./setup, or setup.cmd on Windows."""
    return _words().setup_command


def setup_elsewhere() -> str:
    """How the setup starts on the other computer of the client package, in a phrase (./setup (on a Mac, ...))."""
    return _words().setup_elsewhere


def file_manager() -> str:
    """The program that shows files: the Finder (a text says "the {file_manager()}")."""
    return _words().file_manager


def vm_software() -> str:
    """The VM software that --vm's network is for: VMware ("" where there is no --vm)."""
    return _words().vm


def plural(n: float, word: str, many: Optional[str] = None) -> str:
    """'1 slot', '2 slots' (many: an irregular plural)."""
    k = int(n)
    return f"{k} {word}" if k == 1 else f"{k} {many or word + 's'}"


def spec_name(spec: object, n: object = None) -> str:
    """A speculation mode in the glossary's words, with the drafter's guesses: 'MTP + n-gram, 1 guess'. n-gram alone has no
    guesses to set (llama.cpp's ngram-mod drafts a long run it has seen: Phase 23.5.1.8): 'n-gram'."""
    name = SPEC_NAMES.get(str(spec), str(spec))
    if n in (None, "") or "draft-" not in str(spec):
        return name
    return f"{name}, {plural(int(str(n)) if str(n).isdigit() else 1, 'guess', 'guesses')}"


def kv_name(kv: object, short: bool = False) -> str:
    """The context memory type: 'q4 (small)' (short: 'q4')."""
    text = KV_NAMES.get(str(kv), str(kv))
    return text.split(" ")[0] if short else text


def net_name(net: object) -> str:
    """Who can connect: local, vm or one address (config llama.net / llama.host)."""
    v = str(net)
    if v == "local":
        return f"{computer()} only"
    if v == "vm":
        return f"{computer()} and the VM"
    return f"only {v} (other computers)" if v.count(".") == 3 else v


def status_name(status: object) -> str:
    """A download status: downloaded, not downloaded, partial, bad file."""
    return STATUS_NAMES.get(str(status), str(status))


def goal_name(goal: object) -> str:
    return GOAL_NAMES.get(str(goal), str(goal))


def scope_name(scope: object) -> str:
    return SCOPE_NAMES.get(str(scope), str(scope))


def uptime(etime: Optional[str]) -> str:
    """The server's uptime as '1 h 12 min' ('' when unknown)."""
    s = etime_seconds(etime)
    return duration(s) if s is not None else ""

"""The words a text uses for the computer CARL runs on (rule R13): the command word, the computer's name, what the
memory reserve is kept for, how to update llama.cpp, the file manager, the VM software, and the client's service
managers and setup (the help of carl-sync.py). Pure.

The texts take a PlatformWords; the composition root picks one for the platform (composition/profiles.py), and
CARL_CMD replaces its command word. The macOS values are the words the texts have always used, except the command
word: it is `carl` on every OS (plan decision 2). These values are the one place the words are written.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

COMMAND = "carl"


@dataclass(frozen=True)
class PlatformWords:
    """The words for one platform. command: how the user starts CARL (carl, or CARL_CMD); machine: the computer in
    a sentence (this Mac); a_machine: another one of its kind (a Mac with 24 GB of RAM); os_and_apps: what the RAM
    reserve is kept free for; update: how to update llama.cpp ("{command}" stands for the command word: update_hint
    fills it in); file_manager: the program that shows the files (a text says "the {file_manager}", so no article here); vm: the VM software whose network --vm uses (""
    where there is no --vm); the_machine: the computer as a thing that can fail (a second model can stop the Mac);
    installer: what the client's texts call the program that writes the configs (the setup);
    service_managers: what keeps the client's sync service running; setup_command: how the client setup starts;
    shell: the shell a command in a text is written for (sh, or cmd on Windows: del, double quotes);
    host_label: this computer as the client's provider name has it (llama.cpp (Mac host)); os_label: the OS in a
    table; setup_elsewhere: how the setup starts on the other computer of the Connect tab's steps (the client
    package's folder), a phrase of one sentence; server_machine: the server's computer in a client's text (the server
    Mac), as a sentence part.
    Each OS names the service manager it has: launchd, Task Scheduler or systemd --user. The script install.sh is
    gone, so every OS names the setup (decisions A26, A32, A65).
    """
    command: str
    machine: str
    a_machine: str
    os_and_apps: str
    update: str
    file_manager: str
    vm: str = ""
    the_machine: str = "the computer"
    installer: str = "the setup"
    service_managers: str = "launchd"
    setup_command: str = "./setup"
    shell: str = "sh"
    host_label: str = "host"
    os_label: str = ""
    setup_elsewhere: str = "./setup"

    def with_command(self, command: str) -> PlatformWords:
        """The same words with another command word (CARL_CMD); an empty one keeps this one."""
        return replace(self, command=command) if command else self

    def update_hint(self) -> str:
        """How to update llama.cpp: brew upgrade llama.cpp."""
        return self.update.replace("{command}", self.command)

    def server_machine(self) -> str:
        """The server's computer in a client's text: the server Mac (the machine word with "server" after "the")."""
        noun = self.the_machine[4:] if self.the_machine.startswith("the ") else self.the_machine
        return f"the server {noun}"

    def machine_cap(self) -> str:
        """The machine with a capital: This Mac."""
        return self.machine[:1].upper() + self.machine[1:]


MACOS = PlatformWords(COMMAND, "this Mac", "a Mac", "macOS and apps", "brew upgrade llama.cpp", "Finder", "VMware",
                      "the Mac", host_label="Mac host", os_label="macOS",
                      setup_elsewhere="./setup (on a Mac, double-click setup.command)")
# Without Homebrew: CARL installs the llama.cpp build it is tested with (W6.2), and the same command installs a newer one.
RELEASE_UPDATE = "{command} engine install"
WINDOWS = PlatformWords(COMMAND, "this computer", "a computer", "Windows and apps", RELEASE_UPDATE, "File Explorer",
                        service_managers="Task Scheduler", setup_command="setup.cmd", shell="cmd",
                        host_label="Windows host", os_label="Windows",
                        setup_elsewhere="setup.cmd (on Windows) or ./setup (on macOS and Linux)")
LINUX = PlatformWords(COMMAND, "this computer", "a computer", "the system and apps", RELEASE_UPDATE,
                      "file manager", installer="the setup", service_managers="systemd --user", host_label="Linux host",
                      os_label="Linux")

BY_OS = {"macos": MACOS, "windows": WINDOWS, "linux": LINUX}


def for_os(os_name: str) -> PlatformWords:
    """The words of an OS (macos, windows, linux); another name gets the Linux words."""
    return BY_OS.get(os_name, LINUX)

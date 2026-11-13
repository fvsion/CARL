"""The sync service on Linux (the ServiceManager port): a systemd --user unit, carl-sync.service, that runs
carl-sync.py watch and restarts it. The unit text is the bash setup's, byte for byte. systemctl is called by its
name."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, List

UNIT = "carl-sync"


class SystemdUser:
    """home: the user's home folder (the unit goes to ~/.config/systemd/user); name: the unit's name without
    .service (tests use a test name); run: subprocess.run (tests pass a recorder)."""

    def __init__(self, home: str, name: str = UNIT,
                 run: Callable[..., "subprocess.CompletedProcess[bytes]"] = subprocess.run) -> None:
        self.home = home
        self.service = f"{name}.service"
        self.run = run

    @property
    def title(self) -> str:
        return f"systemd --user {self.service[:-len('.service')]}"

    @property
    def unit(self) -> str:
        return os.path.join(self.home, ".config", "systemd", "user", self.service)

    def unit_text(self, python: str, script: str) -> str:
        return ("[Unit]\n"
                "Description=CARL client config sync (applies the client config that the CARL dashboard sends)\n"
                "After=network-online.target\n"
                "\n"
                "[Service]\n"
                f'ExecStart="{python.replace("%", "%%")}" "{script.replace("%", "%%")}" watch\n'
                "Restart=always\n"
                "RestartSec=30\n"
                "\n"
                "[Install]\n"
                "WantedBy=default.target\n")

    def systemctl(self, args: List[str], out: bool = True, err: bool = True) -> int:
        """systemctl --user ARGS; out / err False: that stream goes nowhere (as the bash setup's >/dev/null and
        2>/dev/null)."""
        return self.run(["systemctl", "--user", *args], stdin=subprocess.DEVNULL,
                        stdout=None if out else subprocess.DEVNULL, stderr=None if err else subprocess.DEVNULL).returncode

    def available(self) -> bool:
        """systemd runs a user manager for this user (not in every container or WSL)."""
        return shutil.which("systemctl") is not None and self.systemctl(["show-environment"], out=False, err=False) == 0

    def install(self, python: str, script: str) -> bool:
        """Write the unit, enable it and restart it (not enable --now: a running service would keep the carl-sync.py
        it started with). False when a step failed."""
        os.makedirs(os.path.dirname(self.unit), exist_ok=True)
        with open(self.unit, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.unit_text(python, script))
        return (self.systemctl(["daemon-reload"]) == 0 and self.systemctl(["enable", self.service], out=False) == 0
                and self.systemctl(["restart", self.service]) == 0)

    def remove(self) -> bool:
        if not os.path.isfile(self.unit):
            return False
        self.systemctl(["disable", "--now", self.service], err=False)
        os.remove(self.unit)
        return True

    def installed(self) -> bool:
        return os.path.isfile(self.unit)

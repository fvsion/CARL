"""The sync service on Windows (the ServiceManager port): a Task Scheduler task, carl-sync, that runs carl-sync.py
watch with pythonw.exe (no console window) when this user logs on, restarts it when it fails, and needs no admin
rights (the task is this user's, at this user's logon, with the least privilege). schtasks is called by its name, as
launchctl and systemctl are (the test suites put a recording fake first on PATH; plan 7.3). The task's XML is kept
in ~/.config/carl/carl-sync.task.xml, the file that schtasks /Create reads.

Dry run: the calls that change something (create, run, end, delete) and the XML file are only noted in `planned`;
the read-only call (/Query) runs. W7.3 checked the adapter this way on Windows; W7.5 made the real task on the Windows test PC.

A watch that ends with an error is not started again by RestartOnFailure (that only covers a start that failed), so
a time trigger starts it every 5 minutes from the install on (only while the user is logged on: the task runs
with the user's interactive token); MultipleInstancesPolicy IgnoreNew leaves a watch that runs alone."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, List, Tuple

TASK = "carl-sync"
REPEAT = "PT5M"                # a time trigger repeats: a watch that ended starts again


def xml(text: str) -> str:
    """Text in the task's XML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def quoted(arg: str) -> str:
    """One argument of the task's command line (the programs it starts split it as the C runtime does)."""
    return '"' + arg.replace('"', '\\"') + '"' if (not arg or any(c in arg for c in ' \t"')) else arg


def windowless(python: str) -> str:
    """pythonw.exe next to python.exe when it is there (no console window at logon); else python itself."""
    folder, name = os.path.split(python)
    if name.lower() == "python.exe":
        w = os.path.join(folder, "pythonw.exe")
        if os.path.isfile(w):
            return w
    return python


class TaskScheduler:
    """home: the user's home folder (the XML goes to ~/.config/carl); user: DOMAIN\\USER, whose logon starts the task
    (the setup passes the environment's); name: the task's name (tests use a test name); run: subprocess.run (tests
    pass a recorder); dry_run: see the module's text; now: the local time (the time trigger's start)."""

    def __init__(self, home: str, user: str, name: str = TASK,
                 run: Callable[..., "subprocess.CompletedProcess[bytes]"] = subprocess.run,
                 dry_run: bool = False, now: Callable[[], "time.struct_time"] = time.localtime) -> None:
        self.home = home
        self.now = now
        self.user = user
        self.name = name
        self.run = run
        self.dry_run = dry_run
        self.planned: List[Tuple[str, ...]] = []

    @property
    def title(self) -> str:
        return f"Task Scheduler {self.name}"

    @property
    def task_file(self) -> str:
        return os.path.join(self.home, ".config", "carl", f"{self.name}.task.xml")

    def unit_text(self, python: str, script: str) -> str:
        user = xml(self.user)
        return ('<?xml version="1.0" encoding="UTF-16"?>\n'
                '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
                '  <RegistrationInfo><Description>CARL client config sync: applies the client config that the '
                'CARL dashboard sends.</Description></RegistrationInfo>\n'
                # at logon, and every 5 min from now on: RestartOnFailure does not start a run again that ended with
                # an error (checked on the Windows test PC, W7.5); IgnoreNew leaves a run that goes on alone
                f'  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>'
                f'<TimeTrigger><Repetition><Interval>{REPEAT}</Interval><StopAtDurationEnd>false</StopAtDurationEnd>'
                f'</Repetition><StartBoundary>{time.strftime("%Y-%m-%dT%H:%M:%S", self.now())}</StartBoundary>'
                '<Enabled>true</Enabled></TimeTrigger></Triggers>\n'
                f'  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType>'
                '<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n'
                '  <Settings>\n'
                '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
                '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
                '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
                '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
                '    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>\n'
                '    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd></IdleSettings>\n'
                '    <Enabled>true</Enabled>\n'
                '  </Settings>\n'
                '  <Actions Context="Author"><Exec>'
                f'<Command>{xml(windowless(python))}</Command>'
                f'<Arguments>{xml(quoted(script))} watch</Arguments>'
                f'<WorkingDirectory>{xml(os.path.dirname(script))}</WorkingDirectory>'
                '</Exec></Actions>\n'
                '</Task>\n')

    def schtasks(self, args: List[str], quiet: bool = False) -> int:
        """schtasks ARGS; quiet: its output goes nowhere. In a dry run only /Query runs."""
        if self.dry_run and args[0] != "/Query":
            self.planned.append(("schtasks", *args))
            return 0
        try:
            return self.run(["schtasks", *args], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL if quiet or args[0] == "/Query" else None,
                            stderr=subprocess.DEVNULL if quiet or args[0] == "/Query" else None).returncode
        except OSError:                                    # no schtasks: not Windows
            return 127

    def available(self) -> bool:
        """Task Scheduler answers for this user (schtasks runs)."""
        return bool(self.user) and self.schtasks(["/Query"]) == 0

    def install(self, python: str, script: str) -> bool:
        """Write the XML, register the task again (/F: it replaces the old one) and start it now (a running task
        ends first, so it starts the new carl-sync.py). False when Task Scheduler refused it."""
        text = self.unit_text(python, script)
        if self.dry_run:
            self.planned.append(("write", self.task_file))
        else:
            os.makedirs(os.path.dirname(self.task_file), exist_ok=True)
            with open(self.task_file, "w", encoding="utf-16", newline="\r\n") as f:
                f.write(text)
        if self.schtasks(["/Create", "/TN", self.name, "/XML", self.task_file, "/F"]) != 0:
            return False
        self.schtasks(["/End", "/TN", self.name], quiet=True)
        return self.schtasks(["/Run", "/TN", self.name]) == 0

    def remove(self) -> bool:
        if not self.installed():
            return False
        self.schtasks(["/End", "/TN", self.name], quiet=True)
        self.schtasks(["/Delete", "/TN", self.name, "/F"], quiet=True)
        if self.dry_run:
            self.planned.append(("remove", self.task_file))
        elif os.path.isfile(self.task_file):
            os.remove(self.task_file)
        return True

    def installed(self) -> bool:
        return self.schtasks(["/Query", "/TN", self.name]) == 0

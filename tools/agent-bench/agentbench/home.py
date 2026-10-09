"""The harness HOME: OpenCode and Pi installed from a CARL client package into a folder of the harness, never
the user's HOME. prepare() installs them once; refresh() writes their configs again for the model that the
server runs now (the package lists the downloaded models only). Both run the package's ./setup with --coder auto
by default, the setup's own rule (the coder on with 2 or more slots), or on / off (bench.py --coder)."""
from __future__ import annotations

import glob
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .clients import client_env, client_version
from .proc import run, tail

MARKER = ".agent-bench.json"
OPENCODE_PKG = "opencode-ai"
PI_PKG = "@earendil-works/pi-coding-agent"
DEFAULT_OPENCODE = "1.18.34"       # the versions of the baseline (the same for every run)
DEFAULT_PI = "1.0.2"
# The coder subagent, as ./setup --coder takes it. auto (the default): the setup's own rule, on when the server runs
# 2 or more slots (with 1 slot a subagent takes the main session's slot). on / off: forced.
CODER_CHOICES = ("auto", "on", "off")
DEFAULT_CODER = "auto"
_CODER_LINE = re.compile(r"^Coder subagent: (on|off)\.", re.M)


class HomeError(Exception):
    pass


def check_home_path(home: str, real_home: Optional[str] = None) -> str:
    """The harness HOME as an absolute path. It must not be the user's HOME, nor a folder that holds it."""
    path = os.path.realpath(os.path.abspath(os.path.expanduser(home)))
    real = os.path.realpath(real_home or os.path.expanduser("~"))
    if path == real or real.startswith(path.rstrip("/") + "/") or path == "/":
        raise HomeError(f"{path} is (or holds) your own HOME: give the harness a folder of its own")
    return path


def is_harness_home(home: str) -> bool:
    return os.path.isfile(os.path.join(home, MARKER))


def require_harness_home(home: str) -> str:
    path = check_home_path(home)
    if not is_harness_home(path):
        raise HomeError(f"{path} is not a harness HOME (no {MARKER}): run bench.py prepare --home {home} first")
    return path


def read_marker(home: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(home, MARKER), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write_marker(home: str, info: Mapping[str, Any]) -> None:
    doc = {**read_marker(home), **info, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(home, MARKER), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


@dataclass
class PackageSource:
    """Where the client package comes from: the repo and the server's work folder (its client/ and conf/)."""
    repo: str
    client_dir: str
    conf_dir: str
    out_dir: str
    models_dir: Optional[str] = None

    def env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env.update(CARL_CLIENT_DIR=self.client_dir, CARL_CONF_DIR=self.conf_dir, COLUMNS="100")
        if self.models_dir:
            env["MODELS_DIR"] = self.models_dir
        return env


def make_package(src: PackageSource) -> str:
    """./carl.sh package --anyway (the server listens on 127.0.0.1): the zip's path."""
    os.makedirs(src.out_dir, exist_ok=True)
    before = set(glob.glob(os.path.join(src.out_dir, "*.zip")))
    for z in before:
        os.remove(z)
    code, out = run(["./carl.sh", "package", "--anyway", "--out", src.out_dir], src.repo, src.env(), 300)
    zips = sorted(glob.glob(os.path.join(src.out_dir, "*.zip")), key=os.path.getmtime)
    if code != 0 or not zips:
        raise HomeError(f"./carl.sh package failed (code {code}):\n{tail(out)}")
    return zips[-1]


def setup_env(home: str) -> Dict[str, str]:
    env = client_env(home)
    env.update(NO_SYNC_SERVICE="1", NO_PROFILE="1", COLUMNS="100")
    return env


def check_coder(coder: str) -> str:
    if coder not in CODER_CHOICES:
        raise HomeError(f"--coder takes {', '.join(CODER_CHOICES)}, not {coder!r}")
    return coder


def coder_state(setup_output: str) -> str:
    """What the setup did with the coder ("on" or "off"; "" when its output does not say): its "Coder subagent:
    on." line (client/install.sh)."""
    found = _CODER_LINE.findall(setup_output.replace("\r", ""))
    return found[-1] if found else ""


def unpack_and_setup(zip_path: str, home: str, install: bool, timeout: float = 1800,
                     coder: str = DEFAULT_CODER) -> str:
    """Unzip the package into the harness HOME and run its ./setup --yes --coder CODER (auto: the setup's rule, on
    with 2 or more slots). The setup's output; the coder's choice and state go into the HOME marker."""
    check_coder(coder)
    os.makedirs(home, exist_ok=True)
    code, out = run(["unzip", "-o", "-q", zip_path, "-d", home], home, setup_env(home), 300)
    if code != 0:
        raise HomeError(f"unzip failed:\n{tail(out)}")
    folder = os.path.join(home, "carl-client")
    argv = [os.path.join(folder, "setup"), "--yes", "--coder", coder] + ([] if install else ["--no-install"])
    code, out = run(argv, folder, setup_env(home), timeout)
    if code != 0:
        raise HomeError(f"./setup failed (code {code}):\n{tail(out, 40)}")
    if is_harness_home(home):
        write_marker(home, {"coder": coder, "coder_state": coder_state(out)})
    return out


def coder_of(home: str) -> Dict[str, str]:
    """The HOME's coder, from its marker: {"coder": auto | on | off, "coder_state": on | off} ("" when not known:
    a HOME of an older harness ran ./setup --coder on)."""
    doc = read_marker(home)
    return {"coder": str(doc.get("coder") or ""), "coder_state": str(doc.get("coder_state") or "")}


def pin_versions(home: str, opencode: Optional[str], pi: Optional[str]) -> Dict[str, str]:
    """Install these client versions into the harness HOME when others are there (None: keep what is there)."""
    env = setup_env(home)
    want = {"opencode": (OPENCODE_PKG, opencode), "pi": (PI_PKG, pi)}
    pkgs = []
    for client, (pkg, version) in want.items():
        if version and client_version(client, env, home) != version:
            pkgs.append(f"{pkg}@{version}")
    if pkgs:
        code, out = run(["npm", "install", "-g", "--prefix", os.path.join(home, ".local"), *pkgs], home, env, 900)
        if code != 0:
            raise HomeError(f"npm install {' '.join(pkgs)} failed:\n{tail(out)}")
    return {c: client_version(c, env, home) for c in want}


def prepare(src: PackageSource, home: str, opencode: Optional[str] = DEFAULT_OPENCODE,
            pi: Optional[str] = DEFAULT_PI, coder: str = DEFAULT_CODER) -> Dict[str, str]:
    """A new harness HOME: the client package, ./setup with the install (--coder CODER), the pinned client
    versions."""
    check_coder(coder)
    home = check_home_path(home)
    if os.path.isdir(home) and os.listdir(home) and not is_harness_home(home):
        raise HomeError(f"{home} is not empty and is not a harness HOME: give an empty or a new folder")
    os.makedirs(home, exist_ok=True)
    write_marker(home, {"created_by": "tools/agent-bench/bench.py prepare", "repo": src.repo})
    unpack_and_setup(make_package(src), home, install=True, coder=coder)
    found = pin_versions(home, opencode, pi)
    write_marker(home, {"versions": found})
    return found


def refresh(src: PackageSource, home: str, coder: str = DEFAULT_CODER) -> str:
    """Write the client configs again (./setup --no-install --coder CODER) from a package of the running server,
    so they list its model (and, with auto, the coder follows its slots). The variant is applied after this."""
    check_coder(coder)
    home = require_harness_home(home)
    return unpack_and_setup(make_package(src), home, install=False, coder=coder)

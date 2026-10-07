"""The harness HOME: OpenCode and Pi installed from a CARL client package into a folder of the harness, never
the user's HOME. prepare() installs them once; refresh() writes their configs again for the model that the
server runs now (the package lists the downloaded models only)."""
from __future__ import annotations

import glob
import json
import os
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


def unpack_and_setup(zip_path: str, home: str, install: bool, timeout: float = 1800) -> str:
    """Unzip the package into the harness HOME and run its ./setup --yes (the coder on). The setup's output."""
    os.makedirs(home, exist_ok=True)
    code, out = run(["unzip", "-o", "-q", zip_path, "-d", home], home, setup_env(home), 300)
    if code != 0:
        raise HomeError(f"unzip failed:\n{tail(out)}")
    folder = os.path.join(home, "carl-client")
    argv = [os.path.join(folder, "setup"), "--yes", "--coder", "on"] + ([] if install else ["--no-install"])
    code, out = run(argv, folder, setup_env(home), timeout)
    if code != 0:
        raise HomeError(f"./setup failed (code {code}):\n{tail(out, 40)}")
    return out


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
            pi: Optional[str] = DEFAULT_PI) -> Dict[str, str]:
    """A new harness HOME: the client package, ./setup with the install, the pinned client versions."""
    home = check_home_path(home)
    if os.path.isdir(home) and os.listdir(home) and not is_harness_home(home):
        raise HomeError(f"{home} is not empty and is not a harness HOME: give an empty or a new folder")
    os.makedirs(home, exist_ok=True)
    write_marker(home, {"created_by": "tools/agent-bench/bench.py prepare", "repo": src.repo})
    unpack_and_setup(make_package(src), home, install=True)
    found = pin_versions(home, opencode, pi)
    write_marker(home, {"versions": found})
    return found


def refresh(src: PackageSource, home: str) -> str:
    """Write the client configs again (./setup --no-install) from a package of the running server, so they
    list its model. The variant is applied after this."""
    home = require_harness_home(home)
    return unpack_and_setup(make_package(src), home, install=False)

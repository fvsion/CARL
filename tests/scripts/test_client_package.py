"""Phase 22, the client package: ./carl.sh package writes dist/carl-client-VERSION-HOST.zip (the client folder from
a file list, the server's address and key, the installed models, VERSION, the entry points setup and
setup.command); client/setup installs OpenCode and Pi from it and writes their configs.

Nothing here touches the real home folder, ~/.config/carl, ~/.local or the server on port 8080: every run has a
temporary HOME (and CARL_CONF_DIR), a fake server on a free port, and fake node, npm, opencode and pi (no download
from npm or nodejs.org). The sync service is off (NO_SYNC_SERVICE=1): launchd and systemd would run it for the
real user. tests/integration/test_sync_docker.py installs from the zip in a Linux container."""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from typing import Any, Dict, List, Optional

from _paths import CLIENT, REPO

from carl_core.adapters import client_package as cp
from carl_core.domain import package as pk

KEY = "pkg-test-key-0123456789"


def models(*ids: str) -> Dict[str, Any]:
    return {"schema": 1, "default": ids[0], "models": [{"id": i, "label": i.upper(), "ctx": 65536, "thinking": "on-off"}
                                                       for i in ids]}


def git_files() -> List[str]:
    out = subprocess.run(["git", "-C", REPO, "ls-files", "--", "client/"], capture_output=True, text=True,
                         check=True).stdout
    return [x[len("client/"):] for x in out.splitlines()]


class FakeServer:
    """The llama.cpp server as install.sh sees it: /props and /v1/models, with the key."""

    def __init__(self, model: str = "test-a") -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:                      # noqa: N802 (the http.server name)
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    self.send_response(401)
                    self.end_headers()
                    return
                body: Any = {"/props": {"default_generation_settings": {"n_ctx": 65536}, "total_slots": 2,
                                        "model_alias": outer.model},
                             "/v1/models": {"data": [{"id": outer.model}]}}.get(self.path)
                self.send_response(200 if body else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body or {}).encode())

            def log_message(self, *a: object) -> None:
                pass

        self.model = model
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


FAKE_NODE = """#!/bin/bash
case "$1" in -p) echo 22.20.0 ;; -v|--version) echo v22.20.0 ;; esac
"""
# npm view PKG version: FAKE_NPM_LATEST (no answer when it is empty); npm install -g --prefix P PKG@latest...:
# writes P/bin/opencode and P/bin/pi that print FAKE_NPM_LATEST. Every call goes into FAKE_NPM_LOG.
FAKE_NPM = """#!/bin/bash
echo "npm $*" >> "$FAKE_NPM_LOG"
if [[ "$1" == view ]]; then [[ -n "${FAKE_NPM_LATEST:-}" ]] || exit 1; echo "$FAKE_NPM_LATEST"; exit 0; fi
[[ "$1" == install ]] || exit 0
shift; prefix=""; pkgs=()
while [[ $# -gt 0 ]]; do case "$1" in -g) ;; --prefix) prefix="$2"; shift ;; *) pkgs+=("$1") ;; esac; shift; done
mkdir -p "$prefix/bin"
for p in "${pkgs[@]}"; do
  case "$p" in opencode-ai@*) b=opencode ;; @earendil-works/pi-coding-agent@*) b=pi ;; *) continue ;; esac
  printf '#!/bin/bash\\necho %s\\n' "${FAKE_NPM_LATEST:-1.2.0}" > "$prefix/bin/$b"; chmod +x "$prefix/bin/$b"
done
"""


def write_exe(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o755)


class PackageCase(unittest.TestCase):
    """A temporary folder: the server's client files (remote.json, api-key), the dist folder, a HOME."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="carl-pkg-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.server_files = os.path.join(self.tmp, "server-client")
        self.dist = os.path.join(self.tmp, "dist")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.server_files)
        os.makedirs(self.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def server_writes(self, host: str = "192.168.42.1", port: int = 8080, version: str = "") -> None:
        """What host/common.sh write_client_package writes at a server start."""
        doc: Dict[str, Any] = {"host": host, "port": port, "cache_api": f"http://{host}:{port + 1}"}
        if version:
            doc["version"] = version
        with open(os.path.join(self.server_files, "remote.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        with open(os.path.join(self.server_files, "api-key"), "w", encoding="utf-8") as f:
            f.write(KEY)
        os.chmod(os.path.join(self.server_files, "api-key"), 0o600)

    def package(self, mods: Optional[Dict[str, Any]] = None, anyway: bool = False,
                cfg: Optional[Dict[str, Any]] = None, repo: str = REPO) -> pk.Outcome:
        return cp.make_package(repo, self.server_files, self.dist, mods or models("test-a"), cfg or {}, "./carl.sh",
                               anyway)

    def unzip(self, path: str, dest: str, over: bool = False) -> None:
        if not shutil.which("unzip"):
            self.skipTest("unzip is not installed")
        os.makedirs(dest, exist_ok=True)
        subprocess.run(["unzip", "-q", *(["-o"] if over else []), path, "-d", dest], check=True)


# ====================================================================== the zip
class ZipTest(PackageCase):
    def test_file_list_and_modes(self) -> None:
        self.server_writes(version="1.6.0")
        out = self.package()
        self.assertEqual(out.error, "", out.notes)
        version = cp.carl_version(REPO)
        self.assertEqual(os.path.basename(out.path), f"carl-client-{version}-{pk.safe_host(cp.host_name())}.zip")
        self.assertEqual(stat.S_IMODE(os.stat(out.path).st_mode), 0o600)              # the zip holds the key
        with zipfile.ZipFile(out.path) as z:
            infos = {i.filename: i for i in z.infolist()}
            want = {f"carl-client/{r}" for r in git_files()} | {f"carl-client/{r}" for r in pk.ENTRY_POINTS} \
                | {f"carl-client/{r}" for r in pk.GENERATED}
            self.assertEqual(set(infos), want)
            self.assertEqual(out.files, len(want))
            mode = {n[len("carl-client/"):]: (i.external_attr >> 16) & 0o777 for n, i in infos.items()}
            self.assertEqual(z.read("carl-client/VERSION").decode(), version + "\n")
            self.assertEqual(z.read("carl-client/api-key").decode(), KEY)
            self.assertEqual(json.loads(z.read("carl-client/remote.json"))["host"], "192.168.42.1")
            self.assertEqual(json.loads(z.read("carl-client/installed-models.json"))["models"][0]["id"], "test-a")
        self.assertEqual((mode["api-key"], mode["remote.json"]), (0o600, 0o600))
        for script in ("setup", "setup.command", "install.sh", "install-clients.sh", "configure.py"):
            self.assertEqual(mode[script], 0o755, script)
        self.assertEqual(mode["shared/carl-panel.js"], 0o644)
        self.assertFalse([n for n in infos if re.search(r"__pycache__|\.pyc$|\.bak\.|\.DS_Store|\.tmp$", n)])
        # unzip keeps the modes (the key stays private, the scripts run)
        self.unzip(out.path, os.path.join(self.tmp, "u"))
        got = os.path.join(self.tmp, "u", "carl-client")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(got, "api-key")).st_mode), 0o600)
        self.assertTrue(os.access(os.path.join(got, "setup"), os.X_OK))
        self.assertTrue(os.access(os.path.join(got, "setup.command"), os.X_OK))

    def test_untracked_and_cache_files_stay_out(self) -> None:
        """The file list is git's: a file that git does not track never goes in (Z1 found a test folder in the
        share zip); without a CHANGELOG, the version comes from git describe --tags."""
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "client", "__pycache__"))
        for rel in ("setup", "setup.command", "install.sh", "a.js", "untracked.txt", "__pycache__/x.pyc", "b.js.bak.1"):
            write_exe(os.path.join(repo, "client", rel), "x\n")
        g = ["git", "-C", repo, "-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run([*g, "init", "-q"], check=True)
        subprocess.run([*g, "add", "client/setup", "client/install.sh", "client/a.js", "client/b.js.bak.1"], check=True)
        subprocess.run([*g, "commit", "-qm", "x"], check=True)
        subprocess.run([*g, "tag", "v9.8.7"], check=True)
        self.server_writes()
        out = self.package(repo=repo)
        self.assertEqual(out.error, "")
        self.assertIn("carl-client-9.8.7-", out.path)
        with zipfile.ZipFile(out.path) as z:
            names = sorted(n[len("carl-client/"):] for n in z.namelist())
        self.assertEqual(names, sorted(["setup", "setup.command", "install.sh", "a.js", *pk.GENERATED]))

    def test_version_and_host_name(self) -> None:
        self.assertEqual(pk.version_from_changelog("# Changelog\n\n## Unreleased\n\n## 1.7.0 - 2026-10-05\n\n## 1.6.0"),
                         "1.7.0")
        self.assertIsNone(pk.version_from_changelog("# Changelog\n\n## Unreleased\n"))
        self.assertEqual(pk.version_from_describe("v1.6.0-3-gabc1234\n"), "1.6.0-3-gabc1234")
        self.assertIsNone(pk.version_from_describe("abc1234"))
        self.assertEqual(pk.safe_host("Brennon's MacBook Pro.local"), "Brennon-s-MacBook-Pro")
        self.assertEqual(pk.safe_host("192.168.42.1"), "192.168.42.1")
        self.assertEqual(pk.safe_host("fe80::1"), "fe80-1")
        self.assertEqual(pk.safe_host("../.."), "server")
        self.assertEqual(pk.zip_name("1.6.0", "mac.lan"), "carl-client-1.6.0-mac.zip")
        # the launcher (remote.json "version") and the package agree
        p = subprocess.run(["bash", "-c", f"source {os.path.join(REPO, 'host', 'common.sh')}; carl_version"],
                           capture_output=True, text=True, check=True)
        self.assertEqual(p.stdout, cp.carl_version(REPO))

    def test_no_package_before_the_first_start(self) -> None:
        out = self.package()
        self.assertEqual(out.path, "")
        self.assertIn("has no remote.json and api-key", out.error)
        self.assertIn("Start the server one time (./carl.sh)", out.notes[0])
        self.assertFalse(os.path.exists(self.dist))


class ShareZipTest(unittest.TestCase):
    def test_share_zip_takes_the_files_that_git_tracks(self) -> None:
        """tools/make-share-zip.sh: git ls-files, nothing untracked (Z1: a test folder went in), never a key."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "share.zip")
            p = subprocess.run([os.path.join(REPO, "tools", "make-share-zip.sh"), out], capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(p.stdout.startswith(out + "  ("), p.stdout)
            self.assertIn("files, without development notes)", p.stdout)
            with zipfile.ZipFile(out) as z:
                names = {n[len("CARL/"):] for n in z.namelist() if not n.endswith("/")}
        tracked = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True, text=True,
                                 check=True).stdout.splitlines()
        want = {f for f in tracked if os.path.isfile(os.path.join(REPO, f))}
        self.assertEqual(names, want)
        self.assertFalse([n for n in names if re.search(r"(^|/)(api-key|remote\.json|installed-models\.json)$|"
                                                        r"__pycache__|^docs/|^dist/", n)])


# ====================================================================== ./carl.sh package
class PackageCliTest(PackageCase):
    def carl(self, *args: str, cfg: Optional[Dict[str, Any]] = None) -> subprocess.CompletedProcess[str]:
        conf = os.path.join(self.tmp, "conf")
        os.makedirs(conf, exist_ok=True)
        # a fixed model: with llama.model = auto, the model list's default needs Auto fit, which reads
        # catalogue headers from Hugging Face (a test must not need the network)
        doc: Dict[str, Any] = {"schema": 1, **(cfg or {})}
        doc["llama"] = {"model": "qwen3.8-9b", **(doc.get("llama") or {})}
        with open(os.path.join(conf, "config.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.makedirs(os.path.join(self.tmp, "models"), exist_ok=True)
        env = {**{k: v for k, v in os.environ.items() if k not in ("API_KEY_FILE",)}, "HOME": self.home,
               "CARL_CONF_DIR": conf, "CARL_CLIENT_DIR": self.server_files, "MODELS_DIR": os.path.join(self.tmp, "models"),
               "CARL_CMD": "./carl.sh", "COLUMNS": "80"}
        return subprocess.run([os.path.join(REPO, "carl.sh"), "package", "--out", self.dist, *args], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, env=env, timeout=120)

    def test_a_server_for_this_mac_only_gets_no_package(self) -> None:
        self.server_writes(host="127.0.0.1")
        p = self.carl()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout, "")
        text = " ".join(p.stderr.split())
        for part in ("error: the server serves only this Mac (127.0.0.1). Other computers cannot reach it",
                     "CARL changed nothing", "Settings > Server > Network", "./carl.sh config set llama.net vm",
                     "start the server again", "--anyway"):
            self.assertIn(part, text)
        self.assertFalse(os.path.exists(self.dist))
        with open(os.path.join(self.server_files, "remote.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["host"], "127.0.0.1")                       # nothing changed
        # the setting is vm already (the server did not start again): say that
        p = self.carl(cfg={"llama": {"net": "vm"}})
        self.assertIn("Your settings already use another network (llama.net = vm)", " ".join(p.stderr.split()))
        # --anyway: the package, for this Mac
        p = self.carl("--anyway")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Only this Mac can reach this address.", " ".join(p.stdout.split()))
        self.assertEqual(len(os.listdir(self.dist)), 1)

    def test_the_package_and_what_carl_prints(self) -> None:
        self.server_writes(host="192.168.42.1")
        p = self.carl()
        self.assertEqual(p.returncode, 0, p.stderr)
        path = os.path.join(self.dist, os.listdir(self.dist)[0])
        lines = p.stdout.splitlines()
        self.assertTrue(lines[0].startswith("CARL made the client package ("), lines[0])
        self.assertEqual(lines[1], f"  {path}")
        text = " ".join(p.stdout.split())
        for part in ("CAUTION: the package holds the API key of the server", "Keep the zip secret", f"rm {path}",
                     f"unzip {os.path.basename(path)}", "cd carl-client && ./setup", "setup.command", "unzip -o",
                     "your setting llama.net is local"):
            self.assertIn(part, text)
        long = [x for x in lines if len(x) > 80 and not x.startswith("  ")]   # commands and the path stay whole
        self.assertEqual(long, [])
        self.assertNotIn(KEY, p.stdout + p.stderr)

    def test_without_the_server_files(self) -> None:
        p = self.carl()
        self.assertEqual(p.returncode, 1)
        self.assertIn("has no remote.json and api-key", " ".join(p.stderr.split()))
        p = self.carl("--bogus")
        self.assertEqual(p.returncode, 1)
        self.assertIn("package does not know '--bogus'", p.stderr)


# ====================================================================== client/setup
class SetupCase(PackageCase):
    """A package unzipped in the temporary HOME, a fake server, fake node / npm / opencode / pi."""

    def setUp(self) -> None:
        super().setUp()
        self.srv = FakeServer()
        self.addCleanup(self.srv.close)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        write_exe(os.path.join(self.bin, "node"), FAKE_NODE)
        write_exe(os.path.join(self.bin, "npm"), FAKE_NPM)
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        self.npm_log = os.path.join(self.tmp, "npm.log")
        with open(os.path.join(self.home, ".zshrc"), "w", encoding="utf-8") as f:
            f.write("# mine\n")
        self.folder = os.path.join(self.home, "carl-client")

    def install_package(self, *ids: str, over: bool = False) -> str:
        """./carl.sh package for the fake server (--anyway: it listens on 127.0.0.1), unzipped in HOME."""
        self.server_writes(host="127.0.0.1", port=self.srv.port, version="1.6.0")
        out = self.package(models(*(ids or ("test-a",))), anyway=True)
        self.assertEqual(out.error, "")
        self.unzip(out.path, self.home, over)
        return out.path

    def setup_run(self, *args: str, latest: str = "1.2.0", env: Optional[Dict[str, str]] = None,
                  script: str = "setup") -> subprocess.CompletedProcess[str]:
        keep = ("LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")
        run_env = {k: v for k, v in os.environ.items() if k in keep}
        run_env.update(HOME=self.home, PATH=f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin", COLUMNS="100",
                       NO_SYNC_SERVICE="1", FAKE_NPM_LOG=self.npm_log, FAKE_NPM_LATEST=latest, **(env or {}))
        return subprocess.run([os.path.join(self.folder, script), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=run_env, timeout=180)

    def read(self, rel: str) -> str:
        with open(os.path.join(self.home, rel), encoding="utf-8") as f:
            return f.read()

    def npm_calls(self) -> List[str]:
        try:
            with open(self.npm_log, encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def backups(self, folder: str) -> List[str]:
        return [n for n in os.listdir(os.path.join(self.home, folder)) if n.endswith(".bak")]   # one per file (23.3)


class SetupTest(SetupCase):
    def test_fresh_install_with_the_defaults(self) -> None:
        self.install_package()
        p = self.setup_run("--yes")
        out = " ".join(p.stdout.split())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        for part in ("CARL client setup (client package", f"The server: 127.0.0.1:{self.srv.port}",
                     "Clients: both. Options: coder auto, browser on, web search exa, LSP on.",
                     "Web search sends the search queries to exa, outside this computer.",
                     "OpenCode: installed (version 1.2.0).", "Pi: installed (version 1.2.0).",
                     f"Smoke test: OK: the server answers at http://127.0.0.1:{self.srv.port}/v1. It has test-a.",
                     "Next: open a new terminal. Go to your project folder and run: opencode (or: pi)"):
            self.assertIn(part, out)
        self.assertNotIn("1. Which clients", p.stdout)                           # --yes: no question
        self.assertEqual([c for c in self.npm_calls() if c.startswith("npm install")],
                         [f"npm install -g --prefix {self.home}/.local opencode-ai@latest "
                          "@earendil-works/pi-coding-agent@latest"])
        oc = json.loads(self.read(".config/opencode/opencode.json"))
        self.assertEqual(list(oc["provider"]["llamacpp"]["models"]), ["test-a"])
        self.assertIn(f"127.0.0.1:{self.srv.port}", oc["provider"]["llamacpp"]["options"]["baseURL"])
        self.assertEqual([m["id"] for m in json.loads(self.read(".pi/agent/models.json"))["providers"]["llamacpp"]["models"]],
                         ["test-a"])
        self.assertEqual(self.read(".config/carl/api-key"), KEY)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.home, ".config/carl/api-key")).st_mode), 0o600)
        self.assertIn("CLIENTS=both", self.read(".config/carl/client-install.env").splitlines())
        self.assertIn("carl-vm-client", self.read(".zshrc"))                    # ~/.local/bin on PATH
        self.assertNotIn(KEY, p.stdout + p.stderr)
        # running it again changes nothing: the clients are up to date, no new install
        p = self.setup_run("--yes")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("OpenCode: version 1.2.0, up to date.", " ".join(p.stdout.split()))
        self.assertEqual(len([c for c in self.npm_calls() if c.startswith("npm install")]), 1)

    def test_an_update_keeps_your_config_and_makes_backups(self) -> None:
        self.install_package()
        self.assertEqual(self.setup_run("--yes", "--web-search", "off", "--coder", "off").returncode, 0)
        # your own settings, between the two setups
        oc_path = os.path.join(self.home, ".config/opencode/opencode.json")
        oc = json.loads(self.read(".config/opencode/opencode.json"))
        oc["provider"]["mine"] = {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "http://x/v1"}}
        oc["theme"] = "my-theme"
        with open(oc_path, "w", encoding="utf-8") as f:
            json.dump(oc, f)
        # a new package (a new model on the server, which now runs it), unzipped over the old folder; newer clients
        self.srv.model = "test-b"
        self.install_package("test-a", "test-b", over=True)
        p = self.setup_run("--yes", latest="1.3.0")
        out = " ".join(p.stdout.split())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OpenCode: updated from version 1.2.0 to 1.3.0.", out)
        self.assertIn("Pi: updated from version 1.2.0 to 1.3.0.", out)
        self.assertIn("coder off, browser on, web search off", out)            # the choices of the last setup
        self.assertNotIn("Web search sends", out)
        oc = json.loads(self.read(".config/opencode/opencode.json"))
        self.assertEqual(oc["theme"], "my-theme")
        self.assertIn("mine", oc["provider"])
        # single-model mode (Phase 23.4.4 item 13): only the model the server runs
        self.assertEqual(sorted(oc["provider"]["llamacpp"]["models"]), ["test-b"])
        self.assertEqual(oc["model"], "llamacpp/test-b")
        self.assertNotIn("coder", oc.get("agent", {}))
        self.assertTrue(self.backups(".config/opencode"), "no backup of opencode.json")
        self.assertIn("Backups of the changed files", out)
        env = self.read(".config/carl/client-install.env").splitlines()
        self.assertIn("WEB_SEARCH=off", env)
        self.assertIn("NO_CODER=1", env)

    def test_one_client_and_options_from_the_command_line(self) -> None:
        self.install_package()
        p = self.setup_run("pi", "--yes", "--browser", "off", "--web-search", "parallel", "--no-install")
        out = " ".join(p.stdout.split())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Clients: pi. Options: coder auto, browser off, web search parallel.", out)
        self.assertIn("Pi: not changed (--no-install).", out)
        self.assertIn("run: pi", out)
        self.assertEqual(self.npm_calls(), [])                                   # nothing installed
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/opencode")))   # OpenCode: not chosen
        mcp = json.loads(self.read(".pi/agent/mcp.json"))["mcpServers"]
        self.assertIn("parallel", mcp["carl-web-search"]["url"])
        self.assertNotIn("carl-browser", mcp)
        self.assertIn("CLIENTS=pi", self.read(".config/carl/client-install.env").splitlines())

    def test_the_questions_one_time(self) -> None:
        """In a terminal, setup asks each question one time; Enter keeps the default, a bad answer asks again."""
        import select
        import time
        self.install_package()
        master, slave = os.openpty()
        proc = subprocess.Popen([os.path.join(self.folder, "setup"), "--no-install"], stdin=slave, stdout=slave,
                                stderr=slave, start_new_session=True,
                                env={"HOME": self.home, "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
                                     "COLUMNS": "100", "NO_SYNC_SERVICE": "1", "FAKE_NPM_LOG": self.npm_log,
                                     "FAKE_NPM_LATEST": "1.2.0", "TERM": "dumb"})
        os.close(slave)
        answers = iter(["opencode\n", "maybe\n", "off\n", "\n", "off\n", "n\n"])
        out, end = b"", time.time() + 120
        try:
            while time.time() < end:
                if select.select([master], [], [], 0.2)[0]:
                    try:
                        data = os.read(master, 4096)
                    except OSError:                         # the terminal closed: setup ended
                        break
                    if not data:
                        break
                    out += data
                    if out.endswith(b"] "):                    # a question waits: "  [default] "
                        os.write(master, next(answers, "\n").encode())
                elif proc.poll() is not None:
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(30)
            os.close(master)
        text = " ".join(out.decode(errors="replace").split())
        self.assertEqual(proc.returncode, 0, text)
        for part in ("1. Which clients: both, opencode or pi?", "Type one of the words in the question",
                     "Clients: opencode. Options: coder off, browser on, web search off, LSP off."):
            self.assertIn(part, text)
        self.assertEqual(text.count("1. Which clients"), 1)
        oc = json.loads(self.read(".config/opencode/opencode.json"))
        self.assertNotIn("lsp", oc)

    def test_setup_command_runs_setup(self) -> None:
        self.install_package()
        p = self.setup_run("--yes", "--no-install", script="setup.command")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("CARL setup is done.", p.stdout)

    def test_modes_are_fixed_first(self) -> None:
        self.install_package()
        os.chmod(os.path.join(self.folder, "api-key"), 0o644)                 # a copy that lost the modes
        os.chmod(os.path.join(self.folder, "install.sh"), 0o644)
        p = self.setup_run("--yes", "--no-install")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.folder, "api-key")).st_mode), 0o600)
        self.assertTrue(os.access(os.path.join(self.folder, "install.sh"), os.X_OK))

    def test_bad_options(self) -> None:
        self.install_package()
        for args, msg in ((["--clients", "emacs"], "--clients does not take 'emacs'"),
                          (["--frobnicate"], "setup does not know '--frobnicate'"),
                          (["--no-install", "--no-config"], "leave nothing to do")):
            p = self.setup_run("--yes", *args)
            self.assertEqual(p.returncode, 2, args)
            self.assertIn(msg, p.stderr)
        p = self.setup_run("--yes", env={"WEB_SEARCH": "google"})
        self.assertEqual(p.returncode, 2)
        self.assertIn("WEB_SEARCH does not take 'google'", p.stderr)


# ====================================================================== the help pages
class HelpTest(unittest.TestCase):
    def test_setup_help(self) -> None:
        for cols in ("80", "60", "132"):
            p = subprocess.run(["bash", os.path.join(CLIENT, "setup"), "--help"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, env={**os.environ, "COLUMNS": cols}, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Usage: ./setup [OPTION...]", p.stdout)
            self.assertEqual([x for x in p.stdout.splitlines() if len(x) > int(cols)], [], cols)
        for part in ("--yes", "--clients", "--coder", "--browser", "--web-search", "--lsp", "--no-install",
                     "--no-config", "setup.command", "unzip -o", "leave this computer"):
            self.assertIn(part, " ".join(p.stdout.split()))

    def test_the_internal_parts_point_to_setup(self) -> None:
        p = subprocess.run(["bash", os.path.join(CLIENT, "install.sh"), "--help"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, env={**os.environ, "COLUMNS": "80"}, timeout=30)
        self.assertIn("Run ./setup instead.", p.stdout.split("Usage:")[0])
        self.assertIn("CLIENTS=WHICH", p.stdout)
        p = subprocess.run(["bash", os.path.join(CLIENT, "install-clients.sh"), "--help"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, env={**os.environ, "COLUMNS": "80"}, timeout=30)
        self.assertIn("Run ./setup instead.", p.stdout.split("Usage:")[0])

    def test_carl_install_runs_the_setup(self) -> None:
        """./carl.sh install is client/setup: its options reach it (an unknown one stops it before any work)."""
        with tempfile.TemporaryDirectory() as home:
            p = subprocess.run([os.path.join(REPO, "carl.sh"), "install", "--frobnicate"], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=30,
                               env={**os.environ, "HOME": home, "CARL_CONF_DIR": os.path.join(home, "conf")})
        self.assertEqual(p.returncode, 2)
        self.assertIn("setup does not know '--frobnicate'", p.stderr)


if __name__ == "__main__":
    unittest.main()

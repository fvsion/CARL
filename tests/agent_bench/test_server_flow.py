"""The server and client setup flow with no model: CarlServer with a fake launcher (start, /health, stop by the
process group's PID), and the real `./carl.sh package` plus the package's `./setup --no-install` against the
fake server (the configs the clients get)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

import _support as sp

import fakeserver as fs
from agentbench import clients as cl, home as hm, proc, server

FAKE_LAUNCHER = """#!/bin/bash
# a stand-in for ./carl.sh: it notes its arguments and environment, then serves the fake server on PORT
printf '%s\\n' "$@" > "$CARL_CONF_DIR/args"
env > "$CARL_CONF_DIR/env"
exec "{python}" "{fakeserver}" --port "$PORT"
"""


class FakeLauncherTest(unittest.TestCase):
    def test_start_ready_stop(self) -> None:
        tmp = tempfile.mkdtemp(prefix="ab-launch-")
        self.addCleanup(shutil.rmtree, tmp)
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        launcher = os.path.join(repo, "carl.sh")
        with open(launcher, "w") as f:
            f.write(FAKE_LAUNCHER.format(python=sys.executable, fakeserver=os.path.join(sp.BENCH, "fakeserver.py")))
        os.chmod(launcher, stat.S_IRWXU)
        port = cl.free_port()
        setup = server.ServerSetup(repo, "gemma-4-e4b", os.path.join(tmp, "work"), port=port, models_dir="/m")
        srv = server.CarlServer(setup)
        pid = srv.start()
        try:
            self.assertGreater(srv.wait_ready(60), 0)
            self.assertEqual(srv.health(), 200)
        finally:
            srv.stop()
        self.assertFalse(proc.group_alive(pid))
        self.assertFalse(server.port_in_use(port))
        with open(os.path.join(setup.conf_dir, "args")) as f:
            self.assertEqual(f.read().split(), ["--model", "gemma-4-e4b", "--local", "--slots", "2"])
        with open(os.path.join(setup.conf_dir, "env")) as f:
            env = dict(line.split("=", 1) for line in f.read().splitlines() if "=" in line)
        self.assertEqual(env["MONITOR"], "0")
        self.assertEqual(env["PORT"], str(port))
        self.assertEqual(env["CARL_CLIENT_DIR"], setup.client_dir)
        self.assertEqual(env["API_KEY_FILE"], setup.key_file)
        self.assertEqual(env["MODELS_DIR"], "/m")
        self.assertEqual(env["HOME"], setup.home_dir)
        self.assertTrue(env["LOG_FILE"].startswith(setup.log_dir))

    def test_a_launcher_that_fails(self) -> None:
        tmp = tempfile.mkdtemp(prefix="ab-launch-")
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, "repo"))
        with open(os.path.join(tmp, "repo", "carl.sh"), "w") as f:
            f.write("#!/bin/bash\necho 'error: the model file does not exist.' >&2\nexit 1\n")
        os.chmod(os.path.join(tmp, "repo", "carl.sh"), stat.S_IRWXU)
        srv = server.CarlServer(server.ServerSetup(os.path.join(tmp, "repo"), "m", os.path.join(tmp, "w"),
                                                   port=cl.free_port()))
        srv.start()
        with self.assertRaises(server.ServerError) as e:
            srv.wait_ready(30)
        self.assertIn("the model file does not exist", str(e.exception))
        srv.stop()


class PackageSetupTest(unittest.TestCase):
    def test_package_and_config_setup(self) -> None:
        tmp = tempfile.mkdtemp(prefix="ab-pkg-")
        self.addCleanup(shutil.rmtree, tmp)
        port = cl.free_port()
        srv = fs.FakeServer(fs.Script.from_json({"models": [sp.MODEL]}), port).start()
        self.addCleanup(srv.stop)
        # what the launcher writes at a server start: the client folder's remote.json and api-key
        client = os.path.join(tmp, "client")
        os.makedirs(client)
        key = server.ensure_key(os.path.join(tmp, "api-key"))
        with open(os.path.join(client, "remote.json"), "w") as f:
            json.dump({"host": "127.0.0.1", "port": port, "cache_api": f"http://127.0.0.1:{port + 1}",
                       "version": "1.7.0"}, f)
        with open(os.path.join(client, "api-key"), "w") as f:
            f.write(key)
        os.makedirs(os.path.join(tmp, "models"))
        src = hm.PackageSource(sp.REPO, client, os.path.join(tmp, "conf"), os.path.join(tmp, "dist"),
                               os.path.join(tmp, "models"))
        z = hm.make_package(src)
        self.assertTrue(os.path.basename(z).startswith("carl-client-"))
        h = os.path.join(tmp, "home")
        os.makedirs(h)
        hm.write_marker(h, {"test": True})
        out = hm.unpack_and_setup(z, h, install=False)
        self.assertIn(f"the server answers at http://127.0.0.1:{port}/v1", out)
        with open(os.path.join(h, ".config", "opencode", "opencode.json")) as f:
            oc = json.load(f)
        self.assertEqual(oc["provider"]["llamacpp"]["options"]["baseURL"], f"http://127.0.0.1:{port}/v1")
        self.assertIn("coder", oc["agent"])                     # --coder auto with 2 slots: on
        self.assertEqual(hm.coder_of(h), {"coder": "auto", "coder_state": "on"})
        self.assertTrue(hm.read_marker(h)["test"])              # the marker keeps its other keys
        self.assertTrue(os.path.isfile(os.path.join(h, ".pi", "agent", "agents", "coder.md")))
        with open(os.path.join(h, ".config", "carl", "api-key")) as f:
            self.assertEqual(f.read().strip(), key)
        self.assertEqual(cl.client_env(h)["OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS"], "1")
        self.assertFalse(os.path.exists(os.path.join(h, ".zshrc")))   # NO_PROFILE=1
        # the setup's coder rule: 1 slot = the coder off (auto); --coder on forces it
        srv.slots = 1
        out = hm.unpack_and_setup(z, h, install=False)
        self.assertIn("Coder subagent: off.", out)
        self.assertEqual(hm.coder_of(h), {"coder": "auto", "coder_state": "off"})
        with open(os.path.join(h, ".config", "opencode", "opencode.json")) as f:
            self.assertNotIn("coder", json.load(f).get("agent", {}))
        hm.unpack_and_setup(z, h, install=False, coder="on")
        self.assertEqual(hm.coder_of(h), {"coder": "on", "coder_state": "on"})
        with self.assertRaises(hm.HomeError):
            hm.unpack_and_setup(z, h, install=False, coder="yes")


if __name__ == "__main__":
    unittest.main()

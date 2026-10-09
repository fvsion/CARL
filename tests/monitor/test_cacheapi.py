"""The cache API (tools/monitor/cacheapi.py): settings, claims and records for clients on other
computers, over the same files the local clients use; the key and the inputs are checked."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor.cacheapi import CacheApi, CacheState, handle
from monitor.diskcache import CacheConfig

CONF = CacheConfig(disk_gb=10, save="switch")


class HandleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = CacheState(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, method: str, path: str, body: dict | None = None, **query: str) -> tuple:
        return handle(self.state, lambda: CONF, method, path, query, body or {})

    def test_settings(self) -> None:
        self.assertEqual(self.call("GET", "/carl/cache/settings"),
                         (200, {"prefix": True, "sessions": True, "save": "switch", "auto_s": 120, "disk_gb": 10,
                                "move": "off", "gate": 0}))

    def test_settings_carry_the_new_file_gate(self) -> None:
        """config.json delegation.gate goes to carl-delegation on other computers (Phase 23.4)."""
        from monitor.diskcache import config_of
        self.assertEqual(config_of({"delegation": {"gate": 3}}).gate, 3)
        for bad in (-1, 100, "2", True, None):
            self.assertEqual(config_of({"delegation": {"gate": bad}}).gate, 0, bad)
        conf = config_of({"cache": {"save": "switch"}, "delegation": {"gate": 2}})
        code, body = handle(self.state, lambda: conf, "GET", "/carl/cache/settings", {}, {})
        self.assertEqual((code, body["gate"]), (200, 2))

    def test_claims_are_the_local_clients_files(self) -> None:
        slot = {"model": "qwen/3.6", "slot": 1}
        self.assertEqual(self.call("POST", "/carl/cache/claim", slot)[0], 200)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, ".claim+qwen_3.6+1")))
        self.assertEqual(self.call("POST", "/carl/cache/claim", slot)[0], 409)
        self.call("POST", "/carl/cache/release", slot)
        self.assertEqual(self.call("POST", "/carl/cache/claim", slot)[0], 200)

    def test_records(self) -> None:
        rec = {"model": "m", "slot": 0, "file": "carl-session+m+0123456789+ses_1.bin", "task": 5, "base": 8000}
        self.assertEqual(self.call("POST", "/carl/cache/record", rec)[0], 200)
        self.assertEqual(self.call("GET", "/carl/cache/record", model="m", file=rec["file"]),
                         (200, {"slot": 0, "task": 5, "base": 8000}))
        self.call("POST", "/carl/cache/unrecord", {"model": "m", "slot": 0})
        self.assertEqual(self.call("GET", "/carl/cache/record", model="m", file=rec["file"])[0], 404)

    def test_turns(self) -> None:
        """A remote client's turn marks: the same files as the local clients'; only its own mark goes;
        an old mark counts as gone."""
        run = {"model": "m", "slot": 1, "session": "ses_a", "running": True}
        self.assertEqual(self.call("POST", "/carl/cache/turn", run)[0], 200)
        self.assertEqual(self.state.turns("m"), [1])
        self.assertEqual(self.call("GET", "/carl/cache/turns", model="m"), (200, {"turns": [{"slot": 1, "session": "ses_a"}]}))
        self.call("POST", "/carl/cache/turn", {**run, "session": "ses_b", "running": False})
        self.assertEqual(self.state.turns("m"), [1])                     # another session's end: kept
        self.assertEqual(self.state.turns("m", now=time.time() + 601), [])
        self.call("POST", "/carl/cache/turn", {**run, "running": False})
        self.assertEqual(self.state.turns("m"), [])
        self.assertEqual(self.call("POST", "/carl/cache/turn", {**run, "running": "yes"})[0], 400)

    def test_bad_inputs(self) -> None:
        for body in ({"model": "m", "slot": -1}, {"model": "m", "slot": 99}, {"model": "", "slot": 0},
                     {"model": "m", "slot": True}):
            self.assertEqual(self.call("POST", "/carl/cache/claim", body)[0], 400)
        bad = {"model": "m", "slot": 0, "file": "../../etc/passwd", "task": 1, "base": 0}
        self.assertEqual(self.call("POST", "/carl/cache/record", bad)[0], 400)
        self.assertEqual(self.call("GET", "/carl/cache/record", model="m", file="x/../y.bin")[0], 400)
        self.assertEqual(self.call("DELETE", "/carl/cache/claim")[0], 404)


class HttpTest(unittest.TestCase):
    def test_the_key_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            api = CacheApi("127.0.0.1", port, lambda: "k3y", d, lambda: CONF)
            self.assertIsNone(api.start())
            try:
                url = f"http://127.0.0.1:{port}/carl/cache/settings"
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(url, timeout=5)
                self.assertEqual(e.exception.code, 401)
                req = urllib.request.Request(url, headers={"Authorization": "Bearer k3y"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    self.assertEqual(json.loads(r.read())["save"], "switch")
                self.assertIn("port", CacheApi("127.0.0.1", port, lambda: "", d, lambda: CONF).start() or "")
            finally:
                api.stop()


if __name__ == "__main__":
    unittest.main()


class RegistryTest(unittest.TestCase):
    def test_clients_are_checked_counted_and_kept(self) -> None:
        from monitor.cacheapi import Registry, parse_client
        self.assertIsNone(parse_client('{"id": "../x"}'))
        self.assertIsNone(parse_client("not json"))
        c = parse_client('{"id": "0123456789ab", "host": "vm\\u0007-1", "user": "u", "applied": "v1", "mode": "service"}')
        assert c is not None
        self.assertEqual((c.host, c.applied, c.mode, c.auto_apply), ("vm-1", "v1", "service", True))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clients.json")
            reg = Registry(path)
            reg.seen(c, "192.168.42.128", connected=1)
            self.assertEqual(reg.list()[0].connected, 1)
            reg.seen(c, "192.168.42.128", connected=-1)
            again = Registry(path).list()                            # kept across dashboard restarts
            self.assertEqual([(x.host, x.address, x.connected) for x in again], [("vm-1", "192.168.42.128", 0)])

    def test_the_coder_model_a_client_reports(self) -> None:
        """Phase 23.4.5: X-Carl-Client's coder_model ("main" or the client's PROVIDER/MODEL) is kept, also across
        dashboard restarts; a value that is not one is left out; a change is saved at once."""
        from monitor.cacheapi import Registry, parse_client
        head = '{"id": "0123456789ab", "host": "vm", "applied": "v1", "mode": "service", "coder_model": %s}'
        c = parse_client(head % '"openrouter/qwen/qwen3-coder:free"')
        assert c is not None
        self.assertEqual(c.coder_model, "openrouter/qwen/qwen3-coder:free")
        for bad in ('"a b/c"', '"x"', '"a/\\u0007"', "3", '"%s"' % ("a/" + "x" * 200)):
            got = parse_client(head % bad)
            assert got is not None
            self.assertEqual(got.coder_model, "", bad)
        old = parse_client('{"id": "0123456789ab", "host": "vm"}')                  # an older client package
        assert old is not None
        self.assertEqual(old.coder_model, "")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clients.json")
            reg = Registry(path)
            reg.seen(c, "192.168.42.128")
            main = parse_client(head % '"main"')
            assert main is not None
            reg.seen(main, "192.168.42.128")                         # changed: saved at once
            self.assertEqual([x.coder_model for x in Registry(path).list()], ["main"])

    def test_a_client_that_leaves_is_seen_at_once_and_keeps_what_it_reported(self) -> None:
        """The event stream ends: the client counts as gone within ~2 s, and the config it applied while
        connected stays (not the value from when the stream started)."""
        with tempfile.TemporaryDirectory() as d:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            api = CacheApi("127.0.0.1", port, lambda: "k3y", d, lambda: CONF, carl_version="1.7.0")
            self.assertIsNone(api.start())
            try:
                who = '{"id": "0123456789ab", "host": "vm", "applied": "v1", "mode": "service"}'
                req = urllib.request.Request(f"http://127.0.0.1:{port}/carl/client/events",
                                             headers={"Authorization": "Bearer k3y", "X-Carl-Client": who})
                r = urllib.request.urlopen(req, timeout=5)
                self.assertEqual(r.headers.get("X-Carl-Version"), "1.7.0")          # /carl on the client reads it
                time.sleep(0.3)
                self.assertEqual(api.registry.list()[0].connected, 1)
                cfg = urllib.request.Request(f"http://127.0.0.1:{port}/carl/client/config",
                                             headers={"Authorization": "Bearer k3y", "X-Carl-Client": who.replace("v1", "v2")})
                with self.assertRaises(urllib.error.HTTPError):             # nothing published: 404, but seen
                    urllib.request.urlopen(cfg, timeout=5)
                r.close()
                t0 = time.time()
                while api.registry.list()[0].connected and time.time() - t0 < 5:
                    time.sleep(0.1)
                self.assertLess(time.time() - t0, 3.5)
                self.assertEqual(api.registry.list()[0].applied, "v2")
            finally:
                api.stop()


class UnpackTest(unittest.TestCase):
    def test_a_remote_client_asks_for_a_patch_to_be_made_whole(self) -> None:
        from monitor import slotpack
        if not slotpack.zstd():
            self.skipTest("zstd is not installed")
        from test_slotpack import PROMPT, SESSION, state
        with tempfile.TemporaryDirectory() as d:
            shared = os.urandom(800_000)
            for name, data in ((PROMPT, state(list(range(50)), shared)),
                               (SESSION, state(list(range(50)) + [1], shared + os.urandom(100_000)))):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(data)
            self.assertTrue(slotpack.pack(d, SESSION))
            st = CacheState(d)
            call = lambda f: handle(st, lambda: CONF, "POST", "/carl/cache/unpack", {}, {"file": f})[0]   # noqa: E731
            self.assertEqual(call(SESSION), 200)
            self.assertTrue(os.path.exists(os.path.join(d, SESSION)))
            self.assertEqual(call("carl-session+m+0123456789+nope.bin"), 404)
            self.assertEqual(call("../x.bin"), 400)


class SecurityTest(unittest.TestCase):
    """The state files are 0600; no key means nobody gets in; a bad body size is refused."""

    def test_state_files_are_private(self) -> None:
        import stat
        with tempfile.TemporaryDirectory() as d:
            st = CacheState(d)
            self.assertTrue(st.claim("m", 1))
            st.record("m", 1, "carl-session+m+0123456789+ses_1.bin", 5, 0)
            st.turn("m", 1, "ses_1", True)
            for name in (".claim+m+1", ".resident+m+1.json", ".turn+m+1"):
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, name)).st_mode), 0o600, name)
            self.assertEqual(sorted(os.listdir(d)), [".claim+m+1", ".resident+m+1.json", ".turn+m+1"])   # no .tmp left

    def test_no_key_and_bad_bodies_are_refused(self) -> None:
        import http.client
        from monitor.cacheapi import authorized, body_length
        self.assertFalse(authorized("", ""))                   # no key known: fail closed
        self.assertFalse(authorized("k3y", "Bearer k3"))
        self.assertTrue(authorized("k3y", "Bearer k3y"))
        self.assertEqual((body_length(None), body_length("12"), body_length("-1"), body_length("x")), (0, 12, None, None))
        with tempfile.TemporaryDirectory() as d:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            api = CacheApi("127.0.0.1", port, lambda: "", d, lambda: CONF)
            self.assertIsNone(api.start())
            try:
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/carl/cache/settings", timeout=5)
                self.assertEqual(e.exception.code, 401)
            finally:
                api.stop()
            api = CacheApi("127.0.0.1", port, lambda: "k3y", d, lambda: CONF)
            self.assertIsNone(api.start())
            try:
                for length, code in (("-1", 400), ("abc", 400), ("99999", 413)):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    conn.putrequest("POST", "/carl/cache/claim")
                    conn.putheader("Authorization", "Bearer k3y")
                    conn.putheader("Content-Length", length)
                    conn.endheaders()
                    self.assertEqual(conn.getresponse().status, code, length)
                    conn.close()
            finally:
                api.stop()

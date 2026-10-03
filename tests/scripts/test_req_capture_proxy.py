"""tools/req-capture-proxy.py between a client and a fake upstream: forwarding,
the log line (no messages, no headers), the 0600 log file, SSE counting."""
from __future__ import annotations

import http.server
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

from _paths import TOOLS, load_script

SCRIPT = os.path.join(TOOLS, "req-capture-proxy.py")
proxy = load_script(SCRIPT, "req_capture_proxy")

SSE = (b'data: {"choices":[{"delta":{"reasoning_content":"abc"}}]}\n'
       b'data: {"choices":[{"delta":{"content":"hello"}}]}\n'
       b"data: [DONE]\n")


class Upstream(http.server.BaseHTTPRequestHandler):
    seen_auth: list[str] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length") or 0))
        Upstream.seen_auth.append(self.headers.get("Authorization") or "")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(SSE)))
        self.end_headers()
        self.wfile.write(SSE)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class ReplyCharsTests(unittest.TestCase):
    def test_counts_sse_and_ignores_junk(self) -> None:
        c = proxy.ReplyChars()
        for line in SSE.split(b"\n") + [b"data: not json", b"data: [1, 2]", b'data: {"choices": ["x"]}']:
            c.add_sse_line(line)
        self.assertEqual((c.reasoning, c.content), (3, 5))

    def test_plain_body(self) -> None:
        c = proxy.ReplyChars()
        c.add_plain_body(b'{"choices":[{"message":{"content":"four"}}]}')
        c.add_plain_body(b'{"error": "x"}')
        self.assertEqual((c.reasoning, c.content), (0, 4))

    def test_record_drops_messages(self) -> None:
        rec = proxy.request_record("POST", "/v1/chat/completions",
                                   json.dumps({"model": "m", "messages": [{"role": "user", "content": "secret"}],
                                               "tools": [{}], "stream": True}).encode())
        self.assertEqual(rec["req"], {"model": "m", "stream": True})
        self.assertEqual((rec["n_messages"], rec["n_tools"]), (1, 1))


class ProxyEndToEnd(unittest.TestCase):
    def test_forwards_and_logs(self) -> None:
        up_port, px_port = free_port(), free_port()
        up = http.server.HTTPServer(("127.0.0.1", up_port), Upstream)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "cap.jsonl")
            p = subprocess.Popen([sys.executable, SCRIPT, f"127.0.0.1:{px_port}", f"127.0.0.1:{up_port}", log],
                                 stdout=subprocess.PIPE, text=True)
            try:
                assert p.stdout is not None
                self.assertIn("capture proxy", p.stdout.readline())
                body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{px_port}/v1/chat/completions", data=body,
                                             headers={"Authorization": "Bearer k123", "content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    self.assertEqual(r.read(), SSE)
                line = ""
                for _ in range(100):                 # the proxy logs after the reply is sent
                    if os.path.exists(log):
                        with open(log, encoding="utf-8") as f:
                            line = f.readline()
                        if line.endswith("\n"):
                            break
                    time.sleep(0.05)
                rec = json.loads(line)
                self.assertEqual(stat.S_IMODE(os.stat(log).st_mode), 0o600)
            finally:
                p.terminate()
                p.wait(5)
                assert p.stdout is not None
                p.stdout.close()
                up.shutdown()
                up.server_close()
        self.assertEqual(Upstream.seen_auth, ["Bearer k123"])
        self.assertEqual((rec["status"], rec["reasoning_chars"], rec["content_chars"]), (200, 3, 5))
        self.assertNotIn("k123", json.dumps(rec))
        self.assertNotIn("hi", json.dumps(rec.get("req")))


if __name__ == "__main__":
    unittest.main()

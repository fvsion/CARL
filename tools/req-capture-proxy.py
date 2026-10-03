#!/usr/bin/env python3
"""Logging pass-through proxy for debugging what clients actually send.

Listens on LISTEN (default 192.168.42.1:8080), forwards every request verbatim
to UPSTREAM (default 127.0.0.1:8081), streams the response back, and appends
one JSON line per request to LOG with the request's non-message fields
(model, reasoning_effort, chat_template_kwargs, stream, max_tokens, ...), the
message count, and the reasoning/content chars the upstream returned. Headers
(the API key) are forwarded but never logged. LOG is created 0600.

  HOST=127.0.0.1 PORT=8081 ./host/serve.sh llama &  # server behind the proxy
  python3 tools/req-capture-proxy.py [LISTEN] [UPSTREAM] [LOG] [--bodies DIR]

--bodies DIR also saves each chat request's full body (system prompt, tools, messages) as
DIR/chat-NNN.json (0600): tools/prompt-size.py counts its tokens.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import socketserver
import sys
import time
from dataclasses import dataclass
from typing import Any, ClassVar

JsonObj = dict[str, Any]
WILDCARDS = frozenset({"", "0.0.0.0", "::", "[::]", "*"})
HOP_BY_HOP_REQ = ("host", "content-length", "connection")
HOP_BY_HOP_RESP = ("transfer-encoding", "connection", "content-length")


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    @classmethod
    def parse(cls, s: str) -> Endpoint:
        host, sep, port = s.rpartition(":")
        if not sep or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {s!r}")
        return cls(host, int(port))


@dataclass
class ReplyChars:
    """Reasoning and content characters in a (streamed or plain) chat reply."""
    reasoning: int = 0
    content: int = 0

    def add_message(self, m: object) -> None:
        if not isinstance(m, dict):
            return
        self.reasoning += len(m.get("reasoning_content") or "")
        self.content += len(m.get("content") or "")

    def add_sse_line(self, line: bytes) -> None:
        if not line.startswith(b"data:") or b"[DONE]" in line:
            return
        try:
            choices = json.loads(line[5:]).get("choices") or []
        except (ValueError, AttributeError):
            return                                   # not a JSON event: nothing to count
        for ch in choices:
            if isinstance(ch, dict):
                self.add_message(ch.get("delta") or ch.get("message") or {})

    def add_plain_body(self, body: bytes) -> None:
        try:
            self.add_message(json.loads(body)["choices"][0]["message"])
        except (ValueError, KeyError, IndexError, TypeError):
            return                                   # not a chat completion


def request_record(method: str, path: str, body: bytes | None) -> JsonObj:
    """The log line for one request: everything but the messages and tools."""
    rec: JsonObj = {"t": time.strftime("%H:%M:%S"), "method": method, "path": path}
    if body and path.endswith("/chat/completions"):
        try:
            d = json.loads(body)
            rec["req"] = {k: v for k, v in d.items() if k not in ("messages", "tools")}
            rec["n_messages"] = len(d.get("messages", []))
            rec["n_tools"] = len(d.get("tools") or [])
        except (ValueError, AttributeError) as e:
            rec["parse_error"] = str(e)
    return rec


def append_log(path: str, rec: JsonObj) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def save_body(folder: str, n: int, body: bytes) -> str:
    """A request body as folder/chat-NNN.json (0600: it holds the conversation)."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, f"chat-{n:03d}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(body)
    return path


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream: ClassVar[Endpoint]
    log_path: ClassVar[str]
    bodies: ClassVar[str | None] = None
    count: ClassVar[int] = 0

    def log_message(self, format: str, *args: Any) -> None:
        return                                       # one JSON line per request instead

    def _go(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        rec = request_record(self.command, self.path, body)
        if self.bodies and body and self.path.endswith("/chat/completions"):
            Proxy.count += 1
            rec["body_file"] = save_body(self.bodies, Proxy.count, body)
        c = http.client.HTTPConnection(self.upstream.host, self.upstream.port, timeout=7200)
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP_REQ}
        c.request(self.command, self.path, body=body, headers=hdrs)
        r = c.getresponse()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in HOP_BY_HOP_RESP:
                self.send_header(k, v)
        self.send_header("transfer-encoding", "chunked")
        self.send_header("connection", "close")
        self.end_headers()
        chars = ReplyChars()
        buf = b""
        while True:
            chunk = r.read1(65536)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                chars.add_sse_line(line)
        if buf.strip().startswith(b"{"):
            chars.add_plain_body(buf)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        rec.update(status=r.status, reasoning_chars=chars.reasoning, content_chars=chars.content)
        append_log(self.log_path, rec)

    do_GET = do_POST = do_DELETE = do_PUT = _go


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Logging pass-through proxy: what clients send to the server.")
    ap.add_argument("listen", nargs="?", default="192.168.42.1:8080", type=Endpoint.parse,
                    help="HOST:PORT to listen on (default 192.168.42.1:8080; never a wildcard address)")
    ap.add_argument("upstream", nargs="?", default="127.0.0.1:8081", type=Endpoint.parse,
                    help="HOST:PORT of the real server (default 127.0.0.1:8081)")
    ap.add_argument("log", nargs="?", default="/tmp/req-capture.jsonl", help="JSON-lines log (default /tmp/req-capture.jsonl)")
    ap.add_argument("--bodies", metavar="DIR", help="also save each chat request's full body in DIR (0600)")
    a = ap.parse_args(argv)
    Proxy.bodies = a.bodies
    listen: Endpoint = a.listen
    if listen.host in WILDCARDS:
        raise SystemExit(f"error: refusing to listen on {listen.host or 'all addresses'} (would expose the server to the LAN)")
    Proxy.upstream, Proxy.log_path = a.upstream, a.log
    print(f"capture proxy {listen.host}:{listen.port} -> {a.upstream.host}:{a.upstream.port}, log {a.log}", flush=True)
    Server((listen.host, listen.port), Proxy).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

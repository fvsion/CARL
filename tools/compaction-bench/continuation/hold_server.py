#!/usr/bin/env python3
"""A stand-in for llama-server that holds a streamed reply, as carl-compaction would while it compacts mid-reply
(Phase 23.5.2; user, 2026-10-13: "run the keep-alive test"). No model: a request whose newest user message holds
"HOLDTEST <seconds> [KA]" and that offers tools gets a streamed reply that sends "Part one." then goes silent for
<seconds> (with KA: an SSE comment ": keep-alive" every 10 s) and then sends "Part two." and the end. Any other chat
request gets "OK" at once. Each held request's start, its end, and a client that hung up (when) go to the log.

    python3 hold_server.py --port 8097 --log hold.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

LOG = ""


def log(doc: Dict[str, Any]) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(doc, t=round(time.time(), 1))) + "\n")


def chunk(model: str, delta: Dict[str, Any], finish: Any = None) -> bytes:
    doc = {"id": "hold", "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(doc)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a: Any) -> None:
        pass

    def _json(self, doc: Any, status: int = 200) -> None:
        body = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/v1/models"):
            self._json({"data": [{"id": "qwen3.6-35b-a3b-iq3"}, {"id": "cache-matrix-alias"}]})
        elif self.path.startswith("/health"):
            self._json({"status": "ok"})
        else:
            self._json({"error": "not here"}, 404)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not self.path.startswith("/v1/chat/completions"):
            self._json({"error": "not here"}, 404)
            return
        model = str(body.get("model") or "m")
        users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
        last = users[-1].get("content") if users else ""
        text = last if isinstance(last, str) else " ".join(str(p.get("text", "")) for p in last or [] if isinstance(p, dict))
        m = re.search(r"HOLDTEST (\d+)( KA)?", text)
        hold = int(m.group(1)) if m and body.get("tools") else 0
        keep = bool(m and m.group(2))
        if not body.get("stream"):
            self._json({"id": "x", "object": "chat.completion", "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        t0 = time.time()
        if hold:
            log({"e": "start", "hold": hold, "keepalive": keep})
        try:
            self.wfile.write(chunk(model, {"role": "assistant", "content": ""}))
            self.wfile.write(chunk(model, {"content": "Part one." if hold else "OK"}))
            self.wfile.flush()
            while time.time() - t0 < hold:
                time.sleep(min(10, hold - (time.time() - t0)))
                if keep and time.time() - t0 < hold:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            if hold:
                self.wfile.write(chunk(model, {"content": " Part two."}))
            self.wfile.write(chunk(model, {}, "stop"))
            usage = {"id": "hold", "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                     "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
            self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
            if hold:
                log({"e": "done", "hold": hold, "keepalive": keep, "seconds": round(time.time() - t0, 1)})
        except (BrokenPipeError, ConnectionResetError) as e:
            log({"e": "client hung up", "hold": hold, "keepalive": keep, "after": round(time.time() - t0, 1),
                 "error": type(e).__name__})


def main() -> int:
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--log", default="hold.jsonl")
    a = ap.parse_args()
    LOG = a.log
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

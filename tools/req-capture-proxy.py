#!/usr/bin/env python3
"""Logging pass-through proxy for debugging what clients actually send.

Listens on LISTEN (default 192.168.42.1:8080), forwards every request verbatim
to UPSTREAM (default 127.0.0.1:8081), streams the response back, and appends
one JSON line per request to LOG with the request's non-message fields
(model, reasoning_effort, chat_template_kwargs, stream, max_tokens, ...), the
message count, and the reasoning/content chars the upstream returned.

  HOST=127.0.0.1 PORT=8081 ./host/serve.sh llama &  # server behind the proxy
  python3 tools/req-capture-proxy.py [LISTEN] [UPSTREAM] [LOG]
"""
import http.client, http.server, json, socketserver, sys, time

LISTEN = sys.argv[1] if len(sys.argv) > 1 else "192.168.42.1:8080"
UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1:8081"
LOG = sys.argv[3] if len(sys.argv) > 3 else "/tmp/req-capture.jsonl"
uh, up = UPSTREAM.split(":")

class P(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _go(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        rec = {"t": time.strftime("%H:%M:%S"), "method": self.command, "path": self.path}
        if body and self.path.endswith("/chat/completions"):
            try:
                d = json.loads(body)
                rec["req"] = {k: v for k, v in d.items() if k not in ("messages", "tools")}
                rec["n_messages"] = len(d.get("messages", [])); rec["n_tools"] = len(d.get("tools") or [])
            except Exception as e:
                rec["parse_error"] = str(e)
        c = http.client.HTTPConnection(uh, int(up), timeout=7200)
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")}
        c.request(self.command, self.path, body=body, headers=hdrs)
        r = c.getresponse()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("transfer-encoding", "chunked"); self.send_header("connection", "close"); self.end_headers()
        reasoning = content = 0; buf = b""
        while True:
            chunk = r.read1(65536)
            if not chunk: break
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk)); self.wfile.flush()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.startswith(b"data:") and b"[DONE]" not in line:
                    try:
                        for ch in json.loads(line[5:]).get("choices") or []:
                            dl = ch.get("delta") or ch.get("message") or {}
                            reasoning += len(dl.get("reasoning_content") or ""); content += len(dl.get("content") or "")
                    except Exception: pass
        if buf.strip().startswith(b"{"):
            try:
                m = json.loads(buf)["choices"][0]["message"]
                reasoning += len(m.get("reasoning_content") or ""); content += len(m.get("content") or "")
            except Exception: pass
        self.wfile.write(b"0\r\n\r\n"); self.wfile.flush()
        rec.update(status=r.status, reasoning_chars=reasoning, content_chars=content)
        with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
    do_GET = do_POST = do_DELETE = do_PUT = _go

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True
lh, lp = LISTEN.split(":")
print(f"capture proxy {LISTEN} -> {UPSTREAM}, log {LOG}", flush=True)
S((lh, int(lp)), P).serve_forever()

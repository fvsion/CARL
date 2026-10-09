#!/usr/bin/env python3
"""A fake OpenAI-compatible server for the agent bench: it gives scripted replies, so the tests can run the real
OpenCode and Pi with no model.

    python3 fakeserver.py --port 8197 --script script.json [--log requests.jsonl]

Endpoints: GET /health, /props, /v1/models, /slots; POST /v1/chat/completions (with and without "stream"); POST
/tokenize (a fake count: one token for each word of "content"; the bearer key is checked when the server has one).
It listens on 127.0.0.1 only.

The script (JSON) is a list of rules; the first rule that matches a request gives the reply:

    {"models": ["fake-model"],
     "rules": [
       {"system_contains": "You are **coder**", "reply": {"text": "## Result\\nDone."}},
       {"last_role": "tool", "reply": {"text": "Done."}},
       {"user_contains": "Explain", "reply": {"reasoning": "Let me think.", "text": "It parses the args."}},
       {"reply": {"tool": "task", "arguments": {"subagent_type": "coder", "prompt": "..."}}}
     ],
     "default": {"text": "OK"}}

A rule matches when each condition it has is true: "system_contains" (a part of the system messages, or a
list of parts that must all be there),
"user_contains" (a part of the newest user message), "last_role" (the role of the newest message: user, tool or
assistant), "has_tool" (the request offers a tool of this name), "tool_results" (the number of tool results in
the request). A reply has "text", or "tool" with "arguments",
and an optional "reasoning" (sent as reasoning_content, as llama.cpp does with --reasoning-format deepseek);
or "error" (an HTTP status) with "text" as the error message.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class Reply:
    """What the fake model answers: a text, or one tool call; reasoning first when it is set."""
    text: str = ""
    tool: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    error: int = 0                  # an HTTP error status instead of a reply (the text is its message)

    @staticmethod
    def from_json(doc: Mapping[str, Any]) -> "Reply":
        args = doc.get("arguments", {})
        return Reply(text=str(doc.get("text", "")), tool=str(doc.get("tool", "")),
                     arguments=args if isinstance(args, dict) else {}, reasoning=str(doc.get("reasoning", "")),
                     error=int(doc.get("error", 0)))


@dataclass(frozen=True)
class Rule:
    reply: Reply
    system_contains: Sequence[str] = ()
    user_contains: str = ""
    last_role: str = ""
    has_tool: str = ""
    tool_results: int = -1

    @staticmethod
    def from_json(doc: Mapping[str, Any]) -> "Rule":
        reply = doc.get("reply", {})
        sc = doc.get("system_contains", [])
        parts = [sc] if isinstance(sc, str) else [str(s) for s in sc] if isinstance(sc, list) else []
        return Rule(Reply.from_json(reply if isinstance(reply, dict) else {}), tuple(p for p in parts if p),
                    str(doc.get("user_contains", "")), str(doc.get("last_role", "")), str(doc.get("has_tool", "")),
                    int(doc.get("tool_results", -1)))

    def matches(self, req: Mapping[str, Any]) -> bool:
        msgs = [m for m in req.get("messages", []) if isinstance(m, dict)]
        system = system_text(msgs)
        if any(s not in system for s in self.system_contains):
            return False
        if self.user_contains and self.user_contains not in last_user_text(msgs):
            return False
        if self.last_role and (not msgs or msgs[-1].get("role") != self.last_role):
            return False
        if self.has_tool and self.has_tool not in tool_names(req):
            return False
        if self.tool_results >= 0 and sum(1 for m in msgs if m.get("role") == "tool") != self.tool_results:
            return False
        return True


@dataclass(frozen=True)
class Script:
    models: Sequence[str] = ("fake-model",)
    rules: Sequence[Rule] = ()
    default: Reply = Reply(text="OK")

    @staticmethod
    def from_json(doc: Mapping[str, Any]) -> "Script":
        models = doc.get("models") or ["fake-model"]
        rules = [Rule.from_json(r) for r in doc.get("rules", []) if isinstance(r, dict)]
        default = doc.get("default")
        return Script(tuple(str(m) for m in models), tuple(rules),
                      Reply.from_json(default) if isinstance(default, dict) else Reply(text="OK"))

    def reply_for(self, req: Mapping[str, Any]) -> Reply:
        for rule in self.rules:
            if rule.matches(req):
                return rule.reply
        return self.default


def content_text(content: Any) -> str:
    """A message's content as text (a string, or a list of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def system_text(msgs: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(content_text(m.get("content")) for m in msgs if m.get("role") in ("system", "developer"))


def last_user_text(msgs: Sequence[Mapping[str, Any]]) -> str:
    for m in reversed(msgs):
        if m.get("role") == "user":
            return content_text(m.get("content"))
    return ""


def tool_names(req: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for t in req.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            out.append(fn["name"])
    return out


def _words(text: str) -> List[str]:
    """The text in small pieces, as a model streams it (the spaces stay)."""
    parts: List[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if ch == " " and len(cur) > 3:
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts


def stream_chunks(reply: Reply, model: str, cid: str, usage: bool) -> Iterator[JsonDict]:
    """The chat.completion.chunk objects of one streamed reply, as llama-server sends them."""
    created = int(time.time())

    def chunk(delta: JsonDict, finish: Optional[str] = None) -> JsonDict:
        return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    yield chunk({"role": "assistant", "content": None})
    for w in _words(reply.reasoning):
        yield chunk({"reasoning_content": w})
    if reply.tool:
        args = json.dumps(dict(reply.arguments))
        half = len(args) // 2
        yield chunk({"tool_calls": [{"index": 0, "id": f"call_{cid[-8:]}", "type": "function",
                                     "function": {"name": reply.tool, "arguments": args[:half]}}]})
        yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[half:]}}]})
        finish = "tool_calls"
    else:
        for w in _words(reply.text):
            yield chunk({"content": w})
        finish = "stop"
    yield chunk({}, finish)
    if usage:
        out = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [],
               "usage": token_usage(reply)}
        yield out


def token_usage(reply: Reply) -> JsonDict:
    reasoning = len(_words(reply.reasoning))
    completion = reasoning + len(_words(reply.text)) + (8 if reply.tool else 0)
    return {"prompt_tokens": 1000, "completion_tokens": completion, "total_tokens": 1000 + completion,
            "completion_tokens_details": {"reasoning_tokens": reasoning}}


def full_reply(reply: Reply, model: str, cid: str) -> JsonDict:
    msg: JsonDict = {"role": "assistant", "content": reply.text or None}
    if reply.reasoning:
        msg["reasoning_content"] = reply.reasoning
    if reply.tool:
        msg["tool_calls"] = [{"id": f"call_{cid[-8:]}", "type": "function",
                              "function": {"name": reply.tool, "arguments": json.dumps(dict(reply.arguments))}}]
    return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if reply.tool else "stop"}],
            "usage": token_usage(reply)}


class _QuietServer(ThreadingHTTPServer):
    """No traceback when a client goes away in the middle of a reply (the harness stops clients early)."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class FakeServer:
    """The server in a thread: start() it, use .port, stop() it. requests holds every chat request body."""

    def __init__(self, script: Script, port: int = 0, log_path: str = "", ctx: int = 98304, slots: int = 2,
                 api_key: str = "") -> None:
        self.script = script
        self.requests: List[JsonDict] = []
        self.api_key = api_key                       # checked on /tokenize only
        self.tokenized: List[str] = []               # the texts of /tokenize
        self.log_path = log_path
        self.ctx = ctx
        self.slots = slots
        self._lock = threading.Lock()
        self._seq = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (the base class's name)
                return

            def _json(self, code: int, body: Any) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/health":
                    self._json(200, {"status": "ok"})
                elif path == "/props":
                    self._json(200, outer.props())
                elif path in ("/v1/models", "/models"):
                    self._json(200, {"object": "list", "data": [
                        {"id": m, "object": "model", "owned_by": "fake", "created": 0} for m in outer.script.models]})
                elif path == "/slots":
                    self._json(200, [{"id": i, "is_processing": False} for i in range(outer.slots)])
                else:
                    self._json(404, {"error": {"message": "not found", "code": 404}})

            def do_POST(self) -> None:
                path = self.path.split("?", 1)[0]
                size = int(self.headers.get("Content-Length") or 0)
                try:
                    req = json.loads(self.rfile.read(size) or b"{}")
                except ValueError:
                    self._json(400, {"error": {"message": "bad JSON"}})
                    return
                if path == "/tokenize" and isinstance(req, dict):
                    if outer.api_key and self.headers.get("Authorization") != f"Bearer {outer.api_key}":
                        self._json(401, {"error": {"message": "Invalid API Key", "code": 401}})
                        return
                    outer.tokenized.append(str(req.get("content", "")))
                    self._json(200, {"tokens": list(range(len(str(req.get("content", "")).split())))})
                    return
                if path not in ("/v1/chat/completions", "/chat/completions") or not isinstance(req, dict):
                    self._json(404, {"error": {"message": "not found", "code": 404}})
                    return
                reply, cid = outer.record(req)
                model = str(req.get("model") or outer.script.models[0])
                if reply.error:
                    self._json(reply.error, {"error": {"code": reply.error, "message": reply.text or "error",
                                                       "type": "invalid_request_error"}})
                    return
                if not req.get("stream"):
                    self._json(200, full_reply(reply, model, cid))
                    return
                usage = bool((req.get("stream_options") or {}).get("include_usage"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for c in stream_chunks(reply, model, cid, usage):
                        self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

        self._httpd = _QuietServer(("127.0.0.1", port), Handler)
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self._thread: Optional[threading.Thread] = None

    def props(self) -> JsonDict:
        return {"default_generation_settings": {"n_ctx": self.ctx}, "total_slots": self.slots,
                "model_alias": self.script.models[0], "model_path": f"/fake/{self.script.models[0]}.gguf",
                "build_info": "fake"}

    def record(self, req: JsonDict) -> "tuple[Reply, str]":
        with self._lock:
            self._seq += 1
            cid = f"chatcmpl-fake{self._seq:08d}"
            self.requests.append(req)
            reply = self.script.reply_for(req)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"seq": self._seq, "request": req,
                                        "reply": {"text": reply.text, "tool": reply.tool,
                                                  "arguments": dict(reply.arguments)}}) + "\n")
        return reply, cid

    def start(self) -> "FakeServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="A fake OpenAI-compatible server with scripted replies (127.0.0.1).")
    ap.add_argument("--port", type=int, default=8197)
    ap.add_argument("--script", help="the rules (JSON); default: every request gets the text OK")
    ap.add_argument("--log", default="", help="append each request and its reply to this JSONL file")
    a = ap.parse_args(argv)
    if a.port == 8080:
        ap.error("port 8080 is the user's CARL server: use another port")
    script = Script()
    if a.script:
        with open(a.script, encoding="utf-8") as f:
            script = Script.from_json(json.load(f))
    srv = FakeServer(script, a.port, a.log)
    print(f"fake server on http://127.0.0.1:{srv.port}", flush=True)
    try:
        srv._httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

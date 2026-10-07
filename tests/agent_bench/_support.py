"""Shared helpers of the agent-bench tests: the import path, the captured events, and a copy of a client HOME
(real OpenCode and Pi, installed by CARL's client setup) that points to a fake server.

The integration test and capture_events.py need such a HOME: AGENT_BENCH_CLIENT_HOME, else the Phase 22 test
HOME below when it exists. Without one, those tests are skipped. The HOME is copied first; the copy is changed,
never the original."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Mapping, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BENCH = os.path.join(REPO, "tools", "agent-bench")
EVENTS = os.path.join(HERE, "events")
DATA = os.path.join(HERE, "data")
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

from agentbench import events as ev  # noqa: E402  (the limits of the measure)

SCRATCH_HOME = ("/private/tmp/claude-501/-Users-fvsion-Documents-Dev-CARL/346912e4-1717-4dda-8d73-49a9b13c42fd/"
                "scratchpad/e2e22/home")
# The files of a client HOME that hold the HOME's path or the server's address.
CONFIG_FILES = (".config/opencode/opencode.json", ".config/opencode/carl.json", ".config/opencode/tui.json",
                ".pi/agent/carl.json", ".pi/agent/models.json", ".config/carl/client-sync.json",
                "carl-client/remote.json")


def client_home() -> Optional[str]:
    """A client HOME with the opencode and pi binaries, or None."""
    for cand in (os.environ.get("AGENT_BENCH_CLIENT_HOME", ""), SCRATCH_HOME):
        if cand and all(os.path.exists(os.path.join(cand, ".local", "bin", b)) for b in ("opencode", "pi")):
            return cand
    return None


def old_port(home: str) -> int:
    with open(os.path.join(home, ".config", "opencode", "carl.json"), encoding="utf-8") as f:
        url = json.load(f)["base_url"]
    m = re.search(r":(\d+)/", url)
    if not m:
        raise ValueError(f"no port in {url}")
    return int(m.group(1))


def clone_home(src: str, dst: str, port: int) -> str:
    """Copy a client HOME (an APFS clone on macOS: fast) and point its configs to 127.0.0.1:port. The copy's path is
    resolved first: on macOS a temp folder is /var/folders/..., a link to /private/var/folders/..., and OpenCode
    resolves the link but not the "file:" plugin paths in opencode.json, so its links to CARL's plugins pointed one
    folder off and no CARL plugin loaded in a copy there (found 2026-10-06)."""
    dst = os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(dst))), os.path.basename(dst))
    if os.path.exists(dst):
        raise FileExistsError(dst)
    if sys.platform == "darwin":
        subprocess.run(["cp", "-Rc", src, dst], check=True)
    else:
        shutil.copytree(src, dst, symlinks=True)
    src_real = os.path.realpath(src)
    was = old_port(src)
    for rel in CONFIG_FILES:
        path = os.path.join(dst, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for s in {src, src_real}:
            text = text.replace(s, dst)
        text = text.replace(f"127.0.0.1:{was}", f"127.0.0.1:{port}").replace(f"127.0.0.1:{was + 1}",
                                                                            f"127.0.0.1:{port + 1}")
        text = text.replace(f'"port": {was}', f'"port": {port}')
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    with open(os.path.join(dst, ".agent-bench.json"), "w", encoding="utf-8") as f:
        json.dump({"created_by": "tests/agent_bench (a copy of a client HOME)", "source": src}, f)
    return dst


def read_events(name: str) -> List[Dict[str, object]]:
    """A captured event stream (tests/agent_bench/events/NAME.jsonl)."""
    out: List[Dict[str, object]] = []
    with open(os.path.join(EVENTS, name + ".jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def read_log(name: str) -> List[str]:
    path = os.path.join(EVENTS, name + ".log")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


# The fake server's scripts: one for each kind of decision. The rules pick each client's own tool names and
# parameters: OpenCode offers the task tool (paths in filePath), Pi the subagent tool (paths in path).
# tool_results: the number of tool results in the request, so the same rule list gives a sequence of calls.
def _oc_pi(n: int, oc: Mapping[str, object], pi: Mapping[str, object],
           reasoning: str = "") -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for has, reply in (("task", oc), ("subagent", pi)):
        r = dict(reply)
        if reasoning:
            r["reasoning"] = reasoning
        out.append({"has_tool": has, "tool_results": n, "reply": r})
    return out


CODER_DONE: Dict[str, object] = {"system_contains": "You are **coder**", "reply": {"text": "## Result\nDone."}}
DELEGATE_OC: Dict[str, object] = {"tool": "task", "arguments": {"description": "Readability module",
                                             "prompt": "Write textstats/readability.py with tests.",
                                             "subagent_type": "coder", "background": True}}
DELEGATE_PI: Dict[str, object] = {"tool": "subagent", "arguments": {"agent": "coder", "task": "Write textstats/readability.py with tests.",
                                                 "background": True}}
READ_OC: Dict[str, object] = {"tool": "read", "arguments": {"filePath": "README.md"}}
READ_PI: Dict[str, object] = {"tool": "read", "arguments": {"path": "README.md"}}
WRITE_OC: Dict[str, object] = {"tool": "write", "arguments": {"filePath": "notes/due.py", "content": "DUE = None\n"}}
WRITE_PI: Dict[str, object] = {"tool": "write", "arguments": {"path": "notes/due.py", "content": "DUE = None\n"}}
HEREDOC = "cat > notes/due.py <<'EOF'\nprint(1 > 0)\nEOF"
DONE_TEXT: Dict[str, object] = {"reply": {"text": "Done."}}
SCENARIOS: Dict[str, Dict[str, object]] = {
    "delegated": {"rules": [CODER_DONE, *_oc_pi(0, DELEGATE_OC, DELEGATE_PI, "A new module with tests: delegate."),
                            DONE_TEXT]},
    "self": {"rules": [CODER_DONE, *_oc_pi(0, READ_OC, READ_PI, "One file. I do it."),
                       {"reply": {"text": "Renamed."}}]},
    "answer": {"rules": [{"reply": {"reasoning": "A question: I answer it.",
                                    "text": "store.py keeps the notes in a JSON file."}}]},
    "error": {"rules": [{"reply": {"error": 400, "text": "the request exceeds the available context size, "
                                                        "try increasing it"}}]},
    "look-delegate": {"rules": [CODER_DONE, *_oc_pi(0, READ_OC, READ_PI, "Let me look first."),
                                *_oc_pi(1, DELEGATE_OC, DELEGATE_PI, "Large: delegate."), DONE_TEXT]},
    "look-write": {"rules": [CODER_DONE, *_oc_pi(0, READ_OC, READ_PI),
                             *_oc_pi(1, WRITE_OC, WRITE_PI, "I write it myself."), DONE_TEXT]},
    "bash-write": {"rules": [CODER_DONE,
                             *_oc_pi(0, {"tool": "bash", "arguments": {"command": "ls notes", "description": "List"}},
                                     {"tool": "bash", "arguments": {"command": "ls notes"}}),
                             *_oc_pi(1, {"tool": "bash", "arguments": {"command": HEREDOC, "description": "Write"}},
                                     {"tool": "bash", "arguments": {"command": HEREDOC}}), DONE_TEXT]},
    "undecided": {"rules": [CODER_DONE, *[r for n in range(ev.MAX_TOOLS + 4) for r in _oc_pi(
        n, {"tool": "bash", "arguments": {"command": f"echo look {n}", "description": f"Look {n}"}},
        {"tool": "bash", "arguments": {"command": f"echo look {n}"}})], DONE_TEXT]},
}
# name: (the strict decision, the practical decision, the looks before it)
EXPECT: Dict[str, Tuple[str, str, int]] = {
    "delegated": ("delegated", "delegated", 0), "self": ("self", "answer", 1), "answer": ("answer", "answer", 0),
    "error": ("error", "error", 0), "look-delegate": ("self", "delegated", 1), "look-write": ("self", "self-write", 1),
    "bash-write": ("self", "self-write", 1), "undecided": ("self", "undecided", ev.MAX_TOOLS)}
PROMPTS = {"delegated": "Add a readability module to textstats, with tests.",
           "self": "Rename the helper _next_id in notes/store.py to _new_note_id.",
           "answer": "How does notes/store.py save the notes?",
           "error": "How does notes/store.py save the notes?",
           "look-delegate": "Add due dates to notes, with tests.",
           "look-write": "Add due dates to notes, with tests.",
           "bash-write": "Add due dates to notes, with tests.",
           "undecided": "Add due dates to notes, with tests."}
MODEL = "gemma-4-e4b"


def scenario_script(name: str) -> Dict[str, object]:
    return {"models": [MODEL], **SCENARIOS[name], "default": {"text": "OK"}}

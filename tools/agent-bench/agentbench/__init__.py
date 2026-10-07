"""agent-bench: does the main agent of OpenCode and Pi hand coding work to the coder subagent?

Pure parts: prompts, events (the JSON event streams and the decision), matrix, results (the JSONL lines), report,
models (the fetch and drop rules). I/O parts: proc (child processes), clients (OpenCode and Pi), fixtures (the
project copies and the hidden tests), server (CARL's llama.cpp server), home (the harness HOME), library (model
copies), variant (the variants/ folder). bench.py is the CLI that connects them.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.dirname(HERE)                      # tools/agent-bench
REPO = os.path.dirname(os.path.dirname(BENCH_DIR))     # the CARL repository

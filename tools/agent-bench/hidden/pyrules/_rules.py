"""Helpers of the rule checks (Phase 23.1): did the agent follow the project's AGENTS.md?"""
from __future__ import annotations

import ast
import os
from typing import Dict, Iterator, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def functions(rel: str) -> Dict[str, ast.FunctionDef]:
    """Every function and method of a source file, by name."""
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}  # type: ignore[misc]


def does_doc(fn: ast.FunctionDef) -> bool:
    doc = ast.get_docstring(fn)
    return bool(doc) and doc.strip().splitlines()[0].startswith("Does:")


def test_files() -> Iterator[str]:
    for name in sorted(os.listdir(os.path.join(ROOT, "tests"))):
        if name.startswith("test_") and name.endswith(".py") and not name.startswith("test_hidden"):
            yield os.path.join("tests", name)


def source(fn: ast.FunctionDef, rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return ast.get_source_segment(f.read(), fn) or ""


ORIGINAL_TESTS = {"setUp", "tearDown", "test_no_file_means_no_notes", "test_add_then_load", "test_mark_done",
                  "run_cli", "test_add_list_done"}


def new_tests_about(word: str) -> List[str]:
    """The new test methods (not in the fixture) whose code names `word`."""
    out: List[str] = []
    for rel in test_files():
        for name, fn in functions(rel).items():
            if name.startswith("test") and name not in ORIGINAL_TESTS and word in source(fn, rel):
                out.append(name)
    return out


def find(rel: str, name: str) -> Optional[ast.FunctionDef]:
    return functions(rel).get(name)

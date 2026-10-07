"""The commands: add, list, done."""
from __future__ import annotations

import argparse
from typing import List, Optional

from notes import store


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="notes", description="Keep short notes.")
    sub = p.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a note")
    add.add_argument("text")
    add.add_argument("--tag", action="append", default=[], help="a tag (repeat for more)")
    ls = sub.add_parser("list", help="list the open notes")
    ls.add_argument("--tag", help="only notes with this tag")
    ls.add_argument("--all", action="store_true", help="also the notes that are done")
    done = sub.add_parser("done", help="mark a note as done")
    done.add_argument("id", type=int)
    return p


def format_note(n: store.Note) -> str:
    mark = "x" if n.done else " "
    tags = f"  [{', '.join(n.tags)}]" if n.tags else ""
    return f"[{mark}] {n.id:3d}  {n.text}{tags}"


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "add":
        note = store.add_note(args.text, args.tag)
        print(f"added {note.id}")
        return 0
    if args.command == "list":
        for n in store.load_notes():
            if n.done and not args.all:
                continue
            if args.tag and args.tag not in n.tags:
                continue
            print(format_note(n))
        return 0
    if args.command == "done":
        if store.mark_done(args.id):
            print(f"done {args.id}")
            return 0
        print(f"no note {args.id}")
        return 1
    return 2

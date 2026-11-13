"""Command line.

    clausewise ask "how many casual leaves do I get?" --role employee
    clausewise audit                     # what changed between versions, contradictions
    clausewise bench                     # run the benchmark
Use --policies DIR for your own documents (Markdown with front matter, PDF or Word).
"""

from __future__ import annotations

import argparse
from datetime import date

from .conflicts import changes, conflicts
from .service import build


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clausewise")
    ap.add_argument("--policies", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--role", default="employee", choices=["employee", "manager", "hr"])
    a.add_argument("--as-of", default=None)
    sub.add_parser("audit")
    sub.add_parser("bench")
    args = ap.parse_args(argv)
    if args.cmd == "bench":
        from .evaluate import main as bench

        return bench([])
    desk = build(args.policies)
    if args.cmd == "audit":
        docs = list(desk.docs.values())
        for f in changes(docs) + conflicts(docs):
            print(f"[{f.kind}] {f.topic}\n  {f.a}\n  {f.b}")
        return 0
    ans = desk.ask(args.question, args.role, date.fromisoformat(args.as_of) if args.as_of else None)
    print(f"[{ans.status}] {ans.text}")
    if ans.note:
        print(f"  note: {ans.note}")
    return 0 if ans.status == "answered" else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Benchmark Clausewise against a typical "chat with your documents" RAG.

    python -m clausewise.evaluate --gate

Typical RAG: fixed 350-character chunks over every file (old versions and HR-only files
included), best keyword match returned as the answer, never refuses. Both are scored on
the same questions: answered right (correct value *and* the right current clause), honest
refusals, restricted content leaked to the wrong role, and outdated versions used.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from .conflicts import changes, conflicts
from .corpus import Doc, load_dir
from .desk import Desk

HERE = Path(__file__).parent
SAMPLE = HERE / "sample_policies"
AS_OF = date(2025, 11, 1)
PLANTED_CHANGES = {"2.1", "2.3", "2.4"}  # leave v1.0 -> v2.0 clauses whose numbers changed
PLANTED_CONFLICTS = {frozenset({"leave-policy-2025#4.3", "holidays-2025#1.1"})}


def load_questions(name: str) -> list[dict]:
    return yaml.safe_load((HERE / "bench" / f"questions_{name}.yaml").read_text())


def _as_list(x):
    return x if isinstance(x, list) else [x]


# -- typical RAG baseline --------------------------------------------------------------
class NaiveRAG:
    def __init__(self, docs: list[Doc], size: int = 350, overlap: int = 50):
        self.chunks = []
        for d in docs:
            text = " ".join(f"{c.clause_id} {c.text}" for c in d.clauses)
            for s in range(0, max(1, len(text)), size - overlap):
                self.chunks.append((d, text[s : s + size]))
        self.tok = [self._t(c) for _, c in self.chunks]
        n = len(self.tok)
        df = Counter(t for d in self.tok for t in set(d))
        self.idf = {t: math.log(1 + (n - v + 0.5) / (v + 0.5)) for t, v in df.items()}
        self.avg = sum(map(len, self.tok)) / n

    @staticmethod
    def _t(s: str) -> list[str]:
        return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2]

    def ask(self, q: str) -> tuple[Doc, str]:
        qt = self._t(q)

        def bm25(i):
            tf = Counter(self.tok[i])
            dl = len(self.tok[i])
            return sum(
                self.idf.get(t, 0) * tf[t] * 2.4 / (tf[t] + 1.4 * (0.4 + 0.6 * dl / self.avg))
                for t in qt
                if tf[t]
            )

        best = max(range(len(self.chunks)), key=bm25)
        return self.chunks[best]


# -- scoring ---------------------------------------------------------------------------
def run(name: str, docs: list[Doc], desk: Desk | None = None) -> dict:
    qs = load_questions(name)
    desk = desk or Desk(docs)
    naive = NaiveRAG(docs)
    res = {
        k: {
            "answered_right": 0,
            "refused_right": 0,
            "restricted_right": 0,
            "wrong": 0,
            "leaks": 0,
            "outdated": 0,
            "false_refusals": 0,
        }
        for k in ("clausewise", "naive")
    }
    counts = Counter()
    misses = []
    for item in qs:
        q, role = item["q"], item.get("role", "employee")
        as_of = item.get("as_of", AS_OF)
        as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
        exp = item["expect"]
        current = {d.doc_id for d in desk.current(as_of).values()}
        kind = exp if exp in ("refuse", "restricted") else "answer"
        counts[kind] += 1
        # clausewise
        a = desk.ask(q, role, as_of)
        r = res["clausewise"]
        cited = {f"{desk.docs[c.doc_id].policy_id}/{c.clause_id}" for c in a.citations}
        if any(not desk.docs[c.doc_id].visible_to(role) for c in a.citations):
            r["leaks"] += 1
        if any(c.doc_id not in current for c in a.citations):
            r["outdated"] += 1
        if kind == "refuse":
            ok = a.status == "not_covered"
            r["refused_right" if ok else "wrong"] += 1
        elif kind == "restricted":
            ok = a.status == "restricted"
            r["restricted_right" if ok else "wrong"] += 1
        else:
            ok = (
                a.status == "answered"
                and cited & set(_as_list(exp))
                and any(v in a.text for v in _as_list(item["value"]))
            )
            r["answered_right" if ok else "wrong"] += 1
            if a.status != "answered":
                r["false_refusals"] += 1
        if not ok:
            misses.append(
                {
                    "q": q,
                    "role": role,
                    "expect": exp,
                    "got": a.status,
                    "cited": sorted(cited),
                    "confidence": round(a.confidence, 2),
                }
            )
        # typical RAG
        d, chunk = naive.ask(q)
        r = res["naive"]
        if not d.visible_to(role):
            r["leaks"] += 1
        if d.doc_id not in current:
            r["outdated"] += 1
        if kind == "answer":
            ok = (
                d.policy_id in {e.split("/")[0] for e in _as_list(exp)}
                and d.doc_id in current
                and any(v in chunk for v in _as_list(item["value"]))
            )
            r["answered_right" if ok else "wrong"] += 1
        else:
            r["wrong"] += 1  # never refuses, never says "restricted"
    for r in res.values():
        r["accuracy"] = round(
            (r["answered_right"] + r["refused_right"] + r["restricted_right"]) / len(qs), 4
        )
    return {"questions": len(qs), "mix": dict(counts), **res, "clausewise_misses": misses}


def audit(docs: list[Doc]) -> dict:
    ch = changes(docs)
    found_changes = {f.b.split("§")[1].split(":")[0] for f in ch if f.b.startswith("Leave Policy")}
    cf = conflicts(docs, AS_OF)
    keys = set()
    for f in cf:
        a = re.search(r"§([\w.]+)", f.a).group(1)
        b = re.search(r"§([\w.]+)", f.b).group(1)
        da = next(d for d in docs if f.a.startswith(f"{d.title} v{d.version}"))
        db = next(d for d in docs if f.b.startswith(f"{d.title} v{d.version}"))
        keys.add(frozenset({f"{da.doc_id}#{a}", f"{db.doc_id}#{b}"}))
    return {
        "changes_planted": len(PLANTED_CHANGES),
        "changes_found": len(found_changes & PLANTED_CHANGES),
        "changes_extra": len(found_changes - PLANTED_CHANGES),
        "conflicts_planted": len(PLANTED_CONFLICTS),
        "conflicts_found": len(keys & PLANTED_CONFLICTS),
        "conflicts_extra": len(keys - PLANTED_CONFLICTS),
    }


def _row(label: str, sets: list[dict], key: str) -> str:
    cells = " | ".join(f"{x[key]['accuracy'] * 100:.0f}%" for x in sets)
    leaks = " / ".join(str(x[key]["leaks"]) for x in sets)
    old = " / ".join(str(x[key]["outdated"]) for x in sets)
    return f"| {label} | {cells} | {leaks} | {old} |"


def report(r: dict) -> str:
    sets = [r["dev"], r["held_out"], r["held_out_2"]]
    lines = [
        "| Questions answered right | Dev | Held-out 1 | Held-out 2 | Leaks (dev / 1 / 2) | "
        "Outdated (dev / 1 / 2) |",
        "|---|---|---|---|---|---|",
        _row("Typical RAG (chunks, top match)", sets, "naive"),
        _row("**Clausewise** (keyword search)", sets, "clausewise"),
    ]
    if "semantic" in r:
        sm = r["semantic"]
        lines.append(
            _row(
                f"**Clausewise** + embeddings (threshold {sm['threshold']} from dev)",
                [sm["dev"], sm["heldout"], sm["heldout2"]],
                "clausewise",
            )
        )
    detail = []
    for name, x in (
        ("Dev", r["dev"]),
        ("Held-out 1", r["held_out"]),
        ("Held-out 2", r["held_out_2"]),
    ):
        c, m = x["clausewise"], x["mix"]
        detail.append(
            f"- {name}: answered right {c['answered_right']}/{m.get('answer', 0)}, "
            f"not covered right {c['refused_right']}/{m.get('refuse', 0)}, restricted right "
            f"{c['restricted_right']}/{m.get('restricted', 0)}, wrongly refused "
            f"{c['false_refusals']}"
        )
    au = r["audit"]
    return (
        "\n".join(
            lines
            + [
                "",
                "Keyword search detail:",
                *detail,
                "",
                f"Policy audit: {au['changes_found']}/{au['changes_planted']} version changes "
                "found "
                f"({au['changes_extra']} extra), {au['conflicts_found']}/{au['conflicts_planted']} "
                f"contradictions found ({au['conflicts_extra']} extra).",
            ]
        )
        + "\n"
    )


def cached(embed):
    memo: dict[str, object] = {}

    def f(texts):
        import numpy as np

        missing = [t for t in texts if t not in memo]
        if missing:
            for t, v in zip(missing, embed(missing), strict=True):
                memo[t] = v
        return np.asarray([memo[t] for t in texts])

    return f


def semantic_runs(docs: list[Doc], embed) -> dict:
    """Pick the similarity threshold on dev only, then score every set once with it."""
    embed = cached(embed)
    grid = [round(0.30 + 0.05 * i, 2) for i in range(10)]
    scores = {
        t: run("dev", docs, Desk(docs, embed=embed, sem_threshold=t))["clausewise"]["accuracy"]
        for t in grid
    }
    best = max(grid, key=lambda t: (scores[t], t))
    out = {"threshold": best, "dev_accuracy_by_threshold": scores}
    for name in ("dev", "heldout", "heldout2"):
        out[name] = run(name, docs, Desk(docs, embed=embed, sem_threshold=best))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark Clausewise")
    p.add_argument("--out", default="eval_results")
    p.add_argument("--gate", action="store_true")
    p.add_argument("--semantic", action="store_true", help="also score with sentence embeddings")
    a = p.parse_args(argv)
    docs = load_dir(SAMPLE)
    r = {
        "dev": run("dev", docs),
        "held_out": run("heldout", docs),
        "held_out_2": run("heldout2", docs),
        "audit": audit(docs),
    }
    if a.semantic:
        from .semantic import sentence_transformer

        r["semantic"] = semantic_runs(docs, sentence_transformer())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(r, indent=2, default=str))
    md = report(r)
    (out / "report.md").write_text(md)
    print(md)
    if a.gate:
        d = r["dev"]["clausewise"]
        if d["accuracy"] < 0.95 or d["leaks"] or d["outdated"] or r["audit"]["conflicts_found"] < 1:
            print("gate failed", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

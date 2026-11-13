"""What changed between policy versions, and where current policies contradict each other."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .corpus import Doc
from .search import tokens

NUM = re.compile(
    r"(?<![vV§\d.,])₹?\d[\d,]*(?:\.\d+)?\s*(?:%|days?|weeks?|months?|hours?|years?|km|characters)?"
)


@dataclass
class Finding:
    kind: str  # changed | conflict
    topic: str
    a: str  # "Leave Policy v1.0 §2.1: 18 days"
    b: str

    def as_dict(self) -> dict:
        return self.__dict__


def _nums(text: str) -> set[str]:
    return {
        re.sub(r"\s+", " ", m.group(0).strip()) for m in NUM.finditer(text) if m.group(0).strip()
    }


def _concepts(text: str) -> set[str]:
    return {t for t in tokens(text) if t.startswith("@") and t not in ("@leave", "@year", "@month")}


def _similar(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def changes(docs: list[Doc]) -> list[Finding]:
    """Clause-by-clause number changes between consecutive versions of the same policy."""
    out = []
    by_policy: dict[str, list[Doc]] = {}
    for d in docs:
        by_policy.setdefault(d.policy_id, []).append(d)
    for versions in by_policy.values():
        versions.sort(key=lambda d: d.effective)
        for old, new in zip(versions, versions[1:], strict=False):
            for c_new in new.clauses:
                match = max(old.clauses, key=lambda c: _similar(c.text, c_new.text), default=None)
                if match is None or _similar(match.text, c_new.text) < 0.45:
                    continue
                if _nums(match.text) != _nums(c_new.text):
                    out.append(
                        Finding(
                            "changed",
                            c_new.section or new.title,
                            f"{old.title} v{old.version} §{match.clause_id}: {match.text}",
                            f"{new.title} v{new.version} §{c_new.clause_id}: {c_new.text}",
                        )
                    )
    return out


def conflicts(docs: list[Doc], as_of: date | None = None) -> list[Finding]:
    """Current clauses in *different* policies on the same topic with different numbers."""
    as_of = as_of or date.today()
    cur: dict[str, Doc] = {}
    for d in docs:
        if d.effective <= as_of and (
            d.policy_id not in cur or d.effective > cur[d.policy_id].effective
        ):
            cur[d.policy_id] = d
    live = list(cur.values())
    out = []
    for i, da in enumerate(live):
        for db in live[i + 1 :]:
            for ca in da.clauses:
                for cb in db.clauses:
                    shared = _concepts(ca.text) & _concepts(cb.text)
                    if not shared or _similar(ca.text, cb.text) < 0.25:
                        continue
                    na, nb = _nums(ca.text), _nums(cb.text)
                    if na and nb and not (na & nb):
                        out.append(
                            Finding(
                                "conflict",
                                ", ".join(sorted(s[1:] for s in shared)),
                                f"{da.title} v{da.version} §{ca.clause_id}: {ca.text}",
                                f"{db.title} v{db.version} §{cb.clause_id}: {cb.text}",
                            )
                        )
    return out

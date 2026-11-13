"""The helpdesk: answer from the current policy the asker may see, or say why not.

Every answer quotes the clause word for word with document, section, version and date.
Three outcomes, never a guess:
  answered    a current clause the asker may see covers the question
  restricted  the answer is in a document this role may not see (content is not shown)
  not_covered nothing in the policies answers it; logged as a policy gap for HR
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from .corpus import Clause, Doc
from .search import STOP, Index, tokens

MIN_COVERAGE = 0.55  # share of the question's meaning a clause must cover to be used


@dataclass
class Citation:
    doc_title: str
    doc_id: str
    clause_id: str
    section: str
    version: str
    effective: str
    text: str

    def label(self) -> str:
        return (
            f"{self.doc_title} v{self.version}, §{self.clause_id} ({self.section}), "
            f"effective {self.effective}"
        )


@dataclass
class Answer:
    question: str
    status: str  # answered | restricted | not_covered
    text: str
    citations: list[Citation] = field(default_factory=list)
    note: str = ""
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "status": self.status,
            "answer": self.text,
            "citations": [c.__dict__ | {"label": c.label()} for c in self.citations],
            "note": self.note,
            "confidence": round(self.confidence, 3),
        }


class Desk:
    def __init__(
        self,
        docs: list[Doc],
        rephrase: Callable[[str, list[str]], str | None] | None = None,
        embed=None,
        sem_threshold: float = 0.5,
    ):
        self.docs = {d.doc_id: d for d in docs}
        self.clauses: list[Clause] = [c for d in docs for c in d.clauses]
        self.index = Index(self.clauses, self._text)
        self.rephrase = rephrase
        self.semantic = None
        self.sem_threshold = sem_threshold
        if embed is not None:
            from .semantic import Semantic

            self.semantic = Semantic([self._text(c) for c in self.clauses], embed)
        self.log: list[Answer] = []

    def _candidates(
        self, question: str, ids: set[int], k: int = 5, sims=None
    ) -> list[tuple[int, float, float]]:
        """[(clause index, rank score, confidence)] for clauses passing the answer gate, best first.

        Keyword coverage must reach MIN_COVERAGE, or (with embeddings) meaning similarity must
        reach the calibrated threshold. Confidence is reported on a 0-1 scale.
        """
        found = {i: (s, cov) for i, s, cov in self.index.search(question, ids, k=k)}
        if sims is not None:
            for i in sorted(ids, key=lambda i: -sims[i])[:k]:
                found.setdefault(i, (0.0, self.index.coverage(tokens(question, consume=True), i)))
        out = []
        for i, (s, cov) in found.items():
            sim = float(sims[i]) if sims is not None else 0.0
            passes = cov >= MIN_COVERAGE or (sims is not None and sim >= self.sem_threshold)
            if passes:
                conf = max(cov, sim) if sims is not None else cov
                out.append((i, round(cov, 2) + sim, conf, s))
        out.sort(key=lambda x: (-x[1], -x[3], -x[2]))  # ties: stronger keyword match first
        return [(i, score, conf) for i, score, conf, _ in out]

    def _best_any(self, question: str, ids: set[int], sims=None) -> float:
        """Best confidence among *all* clauses, passing or not (for the not-covered message)."""
        res = self.index.search(question, ids, k=1)
        best = res[0][2] if res else 0.0
        if sims is not None and ids:
            best = max(best, max(float(sims[i]) for i in ids))
        return best

    def _text(self, c: Clause) -> str:
        d = self.docs[c.doc_id]
        return f"{d.title}. {c.section}. {c.text}"

    def current(self, as_of: date | None = None) -> dict[str, Doc]:
        """Latest version in force per policy: policy_id -> Doc."""
        as_of = as_of or date.today()
        cur: dict[str, Doc] = {}
        for d in self.docs.values():
            if d.effective <= as_of and (
                d.policy_id not in cur or d.effective > cur[d.policy_id].effective
            ):
                cur[d.policy_id] = d
        return cur

    def _cite(self, c: Clause) -> Citation:
        d = self.docs[c.doc_id]
        return Citation(
            d.title, d.doc_id, c.clause_id, c.section, d.version, str(d.effective), c.text
        )

    def ask(self, question: str, role: str = "employee", as_of: date | None = None) -> Answer:
        cur = self.current(as_of)
        live = {d.doc_id for d in cur.values()}
        allowed = {
            i
            for i, c in enumerate(self.clauses)
            if c.doc_id in live and self.docs[c.doc_id].visible_to(role)
        }
        everything = {i for i, c in enumerate(self.clauses) if c.doc_id in live}
        sims = self.semantic.scores(question) if self.semantic else None
        ok = self._candidates(question, allowed, sims=sims)
        anyone = self._candidates(question, everything, sims=sims)

        # restricted: the best answer overall sits in a document this role cannot see
        if anyone and anyone[0][0] not in allowed and (not ok or anyone[0][1] > ok[0][1] + 0.1):
            ans = Answer(
                question,
                "restricted",
                "This is covered in a document that is not "
                "available to your role. Please contact HR.",
                confidence=anyone[0][2],
            )
            self.log.append(ans)
            return ans
        if not ok:
            ans = Answer(
                question,
                "not_covered",
                "The company policies do not cover this. Your question has been logged for HR.",
                confidence=self._best_any(question, allowed, sims),
            )
            self.log.append(ans)
            return ans

        best = ok[0]
        picks = [best]
        if len(ok) > 1:  # a second clause from the same section when it is nearly as good
            i2 = ok[1]
            c1, c2 = self.clauses[best[0]], self.clauses[i2[0]]
            if c2.doc_id == c1.doc_id and c2.section == c1.section and i2[1] >= best[1] - 0.05:
                picks.append(i2)
        cites = [self._cite(self.clauses[i]) for i, _, _ in picks]
        text = " ".join(f"“{c.text}” [{c.label()}]" for c in cites)
        d = self.docs[cites[0].doc_id]
        older = [
            x
            for x in self.docs.values()
            if x.policy_id == d.policy_id and x.effective < d.effective
        ]
        note = (
            f"Updated in v{d.version} (effective {d.effective}); earlier versions no longer apply."
            if older
            else ""
        )
        if self.rephrase:
            draft = self.rephrase(question, [c.text for c in cites])
            if draft and grounded(draft, [c.text for c in cites]):
                text = f"{draft} Source: " + "; ".join(c.label() for c in cites)
        ans = Answer(question, "answered", text, cites, note, best[2])
        self.log.append(ans)
        return ans

    def gaps(self, top: int = 10) -> list[tuple[str, int, list[str]]]:
        """Topics employees asked about that the policies do not cover: (topic, count, examples)."""
        groups: dict[str, list[str]] = {}
        for a in self.log:
            if a.status != "not_covered":
                continue
            words = [t for t in tokens(a.question) if not t.startswith("@") and t not in STOP]
            key = max(words, key=lambda w: self.index.idf_of(w)) if words else "other"
            groups.setdefault(key, []).append(a.question)
        ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top]
        return [(k, len(v), v[:3]) for k, v in ranked]

    def stats(self) -> dict:
        c = Counter(a.status for a in self.log)
        return {"questions": len(self.log), **c}


def grounded(text: str, sources: list[str]) -> bool:
    """A rephrased answer may only use numbers and amounts that appear in the quoted clauses."""
    import re

    nums = re.findall(r"₹?\d[\d,]*(?:\.\d+)?%?", text)
    src = " ".join(sources)
    return all(n in src for n in nums)

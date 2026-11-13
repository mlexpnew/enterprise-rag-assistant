"""Clause search: BM25 over words plus HR concepts, fused with character n-grams for typos.

The glossary maps the many ways employees ask (PL, earned leave, annual leave, "kitni
chhutti") to one concept, so wording does not decide whether a policy is found. Grade
ranges in policies ("G4 to G6") also match the grade in a question ("G5").
"""

from __future__ import annotations

import math
import re
from collections import Counter

GLOSSARY: dict[str, list[str]] = {
    "privilege_leave": [
        "privilege leave",
        "pl",
        "earned leave",
        "el",
        "annual leave",
        "paid leave",
        "vacation",
        "vacation days",
    ],
    "casual_leave": ["casual leave", "cl"],
    "sick_leave": ["sick leave", "sl", "medical leave", "sick days", "sick", "illness", "ill"],
    "lop": [
        "loss of pay",
        "lop",
        "unpaid leave",
        "leave without pay",
        "lwp",
        "absent",
        "absence",
        "without approval",
    ],
    "medical_certificate": [
        "medical certificate",
        "doctor's note",
        "doctors note",
        "doctor note",
        "sick note",
        "doctor certificate",
    ],
    "carry_forward": [
        "carry forward",
        "carried forward",
        "carry over",
        "roll over",
        "rollover",
        "accumulate",
        "next year",
    ],
    "encash": ["encashment", "encash", "encashed", "cash out", "paid out"],
    "maternity": ["maternity", "new mother", "new mothers", "pregnancy", "pregnant"],
    "paternity": ["paternity", "new father", "new fathers", "father"],
    "bereavement": ["bereavement", "passes away", "passed away", "death", "demise", "funeral"],
    "wfh": ["work from home", "wfh", "remote work", "remote", "hybrid", "work remotely"],
    "internet": ["internet", "broadband", "wifi", "wi-fi", "data plan"],
    "home_office": ["home office", "chair", "desk", "home setup"],
    "hotel": ["hotel", "accommodation", "lodging", "stay"],
    "per_diem": ["per diem", "daily allowance", "food allowance", "meal allowance"],
    "flight": ["flight", "fly", "air travel", "airfare", "air ticket"],
    "travel": ["travel", "tour", "business trip", "trip", "outstation"],
    "claim": ["expense claim", "claims", "claim", "reimbursement", "reimburse"],
    "receipt": ["receipt", "receipts", "bill", "bills", "invoice"],
    "password": ["password", "passwords"],
    "mfa": ["mfa", "multi-factor", "multi factor", "2fa", "two factor", "otp"],
    "usb": ["usb", "usb drive", "pen drive", "pendrive", "flash drive"],
    "incident": [
        "security incident",
        "phishing",
        "lost laptop",
        "stolen laptop",
        "hacked",
        "lost my laptop",
    ],
    "public_ai": ["public ai tools", "chatgpt", "ai tools", "ai tool"],
    "posh": ["posh", "sexual harassment", "harassment"],
    "internal_committee": ["internal committee", "ic"],
    "notice": ["notice period", "notice", "resignation", "resign", "quit", "serving notice"],
    "buyout": ["buyout", "buy out"],
    "fnf": ["full and final", "f&f", "fnf", "final settlement"],
    "gratuity": ["gratuity"],
    "holiday": ["holiday", "holidays", "festival", "public holiday"],
    "salary_band": [
        "salary band",
        "ctc",
        "pay band",
        "salary range",
        "compensation band",
        "pay range",
        "salary",
        "tankhwah",
    ],
    "increment": ["increment", "hike", "raise", "pay rise"],
    "rating": ["rating distribution", "bell curve", "ratings", "rating", "expectations"],
    "pip": ["performance improvement plan", "pip"],
    "metro": [
        "metro",
        "mumbai",
        "delhi",
        "new delhi",
        "bengaluru",
        "bangalore",
        "chennai",
        "kolkata",
        "hyderabad",
    ],
    "core_hours": ["core hours", "office hours", "office timings", "working hours"],
    "leave": ["leave", "leaves", "chhutti", "chutti", "chhuti", "time off", "day off", "days off"],
    "year": ["year", "annual", "yearly", "saal"],
    "month": ["month", "monthly", "mahina", "per month"],
}
STOP = set(
    """a an the of for in on at to is are was be do does did i my me we our us you your
it this that what whats what's how many much can could should would when where which who
whom will with by from as or and if any there their per get gets got allowed rule rules
policy company employee employees kitni kitna milti milta hai mein ka ki ke kya please
tell about there's need do i'm im use using""".split()
)
# Words that say what *kind* of answer is wanted, not what it is about: they never count as
# meaning the policy failed to cover.
ASKING = set(
    """happen happens deadline minimum maximum max min length long limit limits time
take taking submit submitting early quickly soon fast eligible eligibility duration amount
number total much allowed allow apply applies applicable mandatory required need needed
process procedure kind type exactly also still""".split()
)

_WORD = re.compile(r"[a-z0-9₹%&']+")
_GRADE_RANGE = re.compile(r"\bg(\d+)\s+to\s+g(\d+)\b")
_GRADE_UP = re.compile(r"\bg(\d+)\s+and\s+above\b")
_PHRASES = sorted(((p, c) for c, ps in GLOSSARY.items() for p in ps), key=lambda x: -len(x[0]))


def _stem(w: str) -> str:
    w = _stem1(w)
    return w[:-1] if len(w) > 3 and w.endswith("e") else w  # file/filed, leave/leaves


def _stem1(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
        return w[:-1] if len(w) > 3 and w[-1] == w[-2] else w
    if len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
        return w[:-1] if len(w) > 3 and w[-1] == w[-2] else w
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str, consume: bool = False) -> list[str]:
    """Words (stemmed, no stopwords) + concept tokens + expanded grade ranges.

    consume=True (questions): words inside a matched glossary phrase are carried by the
    concept alone, so "new mothers" means maternity, not two unknown words.
    """
    t = " " + text.lower().replace("’", "'") + " "
    out = []
    for m in _GRADE_RANGE.finditer(t):
        out += [f"g{i}" for i in range(int(m.group(1)), int(m.group(2)) + 1)]
    for m in _GRADE_UP.finditer(t):
        out += [f"g{i}" for i in range(int(m.group(1)), 13)]
    seen = set()
    for phrase, concept in _PHRASES:  # longest phrases first
        pat = re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?:s|es)?(?![a-z0-9])")
        if pat.search(t):
            if concept not in seen:
                out.append(f"@{concept}")
                seen.add(concept)
            if consume:
                t = pat.sub(" ", t)
    out += [_stem(w) for w in _WORD.findall(t) if w not in STOP and len(w) > 1]
    return out


ASKING_STEMS = {_stem(w) for w in ASKING}


class Index:
    """BM25 over clause tokens (title and section included) + char n-gram cosine."""

    def __init__(self, items: list, text_of, k1: float = 1.4, b: float = 0.6):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.items = items
        self.docs = [tokens(text_of(x)) for x in items]
        self.tf = [Counter(d) for d in self.docs]
        n = len(items)
        df = Counter(t for d in self.docs for t in set(d))
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.max_idf = math.log(1 + (n + 0.5) / 0.5) if n else 1.0
        self.avg = sum(map(len, self.docs)) / n if n else 1.0
        self.k1, self.b = k1, b
        self.chars = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.char_m = self.chars.fit_transform([text_of(x).lower() for x in items]) if n else None

    def idf_of(self, t: str) -> float:
        return self.idf.get(t, self.max_idf)  # words the policies never use weigh the most

    def bm25(self, q: list[str], i: int) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in q:
            f = tf.get(t, 0)
            if f:
                s += (
                    self.idf[t]
                    * f
                    * (self.k1 + 1)
                    / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
                )
        return s

    def coverage(self, q: list[str], i: int) -> float:
        """Share of the question's meaning (IDF-weighted) that this clause covers."""
        if not q:
            return 0.0
        have = self.tf[i]
        qs = {t for t in q if t not in ASKING_STEMS} or set(q)
        tot = sum(self.idf_of(t) for t in qs)
        return sum(self.idf_of(t) for t in qs if t in have) / tot if tot else 0.0

    def search(
        self, question: str, allowed: set[int] | None = None, k: int = 5
    ) -> list[tuple[int, float, float]]:
        """[(item index, fused score, coverage)] best first."""
        q = tokens(question, consume=True)
        idx = [i for i in range(len(self.items)) if allowed is None or i in allowed]
        if not idx or not q:
            return []
        bm = {i: self.bm25(q, i) for i in idx}
        sims = (self.char_m[idx] @ self.chars.transform([question.lower()]).T).toarray().ravel()
        ch = dict(zip(idx, sims, strict=True))
        r_bm = {i: r for r, i in enumerate(sorted(idx, key=lambda i: -bm[i]))}
        r_ch = {i: r for r, i in enumerate(sorted(idx, key=lambda i: -ch[i]))}
        fused = {i: 1 / (20 + r_bm[i]) + 0.5 / (20 + r_ch[i]) for i in idx}
        best = sorted(idx, key=lambda i: (-fused[i], -bm[i]))[:k]
        return [(i, fused[i], self.coverage(q, i)) for i in best if bm[i] > 0 or ch[i] > 0.2]

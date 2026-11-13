from datetime import date

import numpy as np

from clausewise.conflicts import changes, conflicts
from clausewise.corpus import load_dir, parse
from clausewise.desk import Desk, grounded
from clausewise.search import tokens
from clausewise.service import SAMPLE

DOCS = load_dir(SAMPLE)


def test_parse_numbered_and_plain_paragraphs():
    title, cl = parse(
        "# Policy\n## 1. Leave\n1.1 First rule\ncontinues here.\n\nA plain note.", "x"
    )
    assert title == "Policy"
    assert [(c.clause_id, c.section) for c in cl] == [("1.1", "Leave"), ("p2", "Leave")]
    assert cl[0].text == "First rule continues here."


def test_word_and_pdf_free_loading(tmp_path):
    import docx

    doc = docx.Document()
    doc.add_paragraph("1.1 Shift allowance is ₹300 per night.")
    doc.save(tmp_path / "shift.docx")
    (tmp_path / "policies.yaml").write_text("shift.docx: {policy_id: shift, audience: [all]}\n")
    d = load_dir(tmp_path)[0]
    assert d.policy_id == "shift" and d.clauses[0].text.startswith("Shift allowance")


def test_glossary_grades_and_question_consumption():
    t = tokens("Hotel limit for grades G4 to G6 in metro cities")
    assert {"g4", "g5", "g6", "@hotel", "@metro"} <= set(t)
    q = tokens("leave for new mothers", consume=True)
    assert "@maternity" in q and "mother" not in q and "new" not in q
    assert "@privilege_leave" in tokens("kitni PL milti hai saal mein")


def test_answer_quotes_current_clause_with_version_note():
    a = Desk(DOCS).ask("How many casual leaves per year?", as_of=date(2025, 11, 1))
    assert a.status == "answered" and "8 days" in a.text
    assert a.citations[0].version == "2.0" and "Updated in v2.0" in a.note


def test_old_version_used_for_old_dates():
    a = Desk(DOCS).ask("How many privilege leaves do I get in a year?", as_of=date(2025, 1, 1))
    assert "18 days" in a.text and a.citations[0].version == "1.0"


def test_restricted_and_not_covered_and_gaps():
    desk = Desk(DOCS)
    r = desk.ask("What is the salary band for G5?", "employee", date(2025, 11, 1))
    assert r.status == "restricted" and "22,00,000" not in r.text and not r.citations
    hr = desk.ask("What is the salary band for G5?", "hr", date(2025, 11, 1))
    assert hr.status == "answered" and "₹22,00,000" in hr.text
    n = desk.ask("Do we get a gym membership reimbursement?", "employee", date(2025, 11, 1))
    assert n.status == "not_covered"
    assert desk.gaps()[0][1] == 1 and desk.stats()["not_covered"] == 1


def test_audit_finds_changes_and_contradiction():
    ch = changes(DOCS)
    assert any("18 days" in f.a and "21 days" in f.b for f in ch)
    cf = conflicts(DOCS, date(2025, 11, 1))
    assert len(cf) == 1 and "12 fixed holidays" in cf[0].a + cf[0].b


def test_rephrase_must_be_grounded():
    src = ["Employees get 8 days of casual leave per year."]
    assert grounded("You get 8 days of casual leave a year.", src)
    assert not grounded("You get 10 days of casual leave.", src)
    desk = Desk(DOCS, rephrase=lambda q, s: "You get 12 casual leaves.")
    a = desk.ask("How many casual leaves per year?", as_of=date(2025, 11, 1))
    assert "8 days" in a.text and "12 casual" not in a.text  # ungrounded phrasing discarded


def test_semantic_path_with_stand_in_embedder():
    vocab = {}

    def embed(texts):
        rows = []
        for t in texts:
            v = np.zeros(512)
            for w in tokens(t):
                v[vocab.setdefault(w, len(vocab) % 512)] += 1
            rows.append(v / (np.linalg.norm(v) or 1))
        return np.array(rows)

    desk = Desk(DOCS, embed=embed, sem_threshold=0.99)
    a = desk.ask("How many casual leaves per year?", as_of=date(2025, 11, 1))
    assert a.status == "answered" and "8 days" in a.text

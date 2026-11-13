"""Clausewise dashboard.  streamlit run app.py"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from clausewise.conflicts import changes, conflicts
from clausewise.service import build

st.set_page_config(page_title="Clausewise", page_icon="📘", layout="wide")


@st.cache_resource
def desk():
    return build()


d = desk()
st.title("Clausewise")
st.caption(
    "Answers employees from your own policies, quoting the exact clause. "
    "Says so when a policy is silent, and never shows a document to the wrong role."
)
role = st.sidebar.selectbox("Asking as", ["employee", "manager", "hr"])
as_of = st.sidebar.date_input("Policies in force on", date.today())
st.sidebar.caption(f"{len(d.docs)} documents, {len(d.clauses)} clauses loaded.")

ask_tab, gaps_tab, audit_tab, lib_tab = st.tabs(["Ask", "Policy gaps", "Policy audit", "Library"])
with ask_tab:
    examples = [
        "How many casual leaves do I get?",
        "Hotel limit in Mumbai for G2",
        "What is the salary band for G5?",
        "Is there a gym reimbursement?",
    ]
    q = st.text_input("Question", placeholder=examples[0])
    st.caption("Try: " + " · ".join(examples))
    if q:
        a = d.ask(q, role, as_of)
        if a.status == "answered":
            for c in a.citations:
                st.success(f"“{c.text}”")
                st.caption(c.label())
            if a.note:
                st.info(a.note)
        elif a.status == "restricted":
            st.warning(a.text)
        else:
            st.error(a.text)
with gaps_tab:
    st.write(
        "Questions the policies could not answer, grouped by topic. Use it to decide what "
        "to write next."
    )
    g = d.gaps()
    st.dataframe(
        pd.DataFrame(g, columns=["topic", "times asked", "examples"]) if g else pd.DataFrame(),
        hide_index=True,
        use_container_width=True,
    )
with audit_tab:
    docs = list(d.docs.values())
    st.subheader("Contradictions between current policies")
    for f in conflicts(docs, as_of):
        st.error(f"{f.a}\n\n{f.b}")
    st.subheader("What changed between versions")
    for f in changes(docs):
        st.write(f"**{f.topic}**: {f.a} → {f.b}")
with lib_tab:
    cur = {x.doc_id for x in d.current(as_of).values()}
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "title": x.title,
                    "version": x.version,
                    "effective": x.effective,
                    "audience": ", ".join(x.audience),
                    "clauses": len(x.clauses),
                    "in force": x.doc_id in cur,
                }
                for x in d.docs.values()
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )

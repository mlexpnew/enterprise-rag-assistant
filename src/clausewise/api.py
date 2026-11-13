"""HTTP API for a Slack, Teams or intranet front end.

    uvicorn clausewise.api:app
Roles come from API keys, never from the request:  CLAUSEWISE_KEYS="k1:employee,k2:manager,k3:hr"
Without keys (local demo) the request may pass a role.
"""

from __future__ import annotations

import os
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .conflicts import changes, conflicts
from .service import build

app = FastAPI(
    title="Clausewise",
    version="1.0.0",
    description="Answers employees from your own policies, quoting the clause.",
)
DESK = build()
ROLES = {"employee", "manager", "hr"}


def _keys() -> dict[str, str]:
    pairs = [p.split(":", 1) for p in os.getenv("CLAUSEWISE_KEYS", "").split(",") if ":" in p]
    return {k.strip(): r.strip() for k, r in pairs}


def caller_role(x_api_key: str | None = Header(default=None)) -> str | None:
    keys = _keys()
    if not keys:
        return None  # demo mode: role taken from the request
    if x_api_key not in keys:
        raise HTTPException(401, "missing or invalid X-API-Key")
    return keys[x_api_key]


class Question(BaseModel):
    question: str
    role: str = "employee"
    as_of: date | None = None


@app.get("/health")
def health():
    return {"status": "ok", "documents": len(DESK.docs), "clauses": len(DESK.clauses)}


@app.post("/v1/ask")
def ask(q: Question, role: str | None = Depends(caller_role)):
    r = role or q.role
    if r not in ROLES:
        raise HTTPException(422, f"role must be one of {sorted(ROLES)}")
    return DESK.ask(q.question, r, q.as_of).as_dict()


@app.get("/v1/gaps")
def gaps(role: str | None = Depends(caller_role)):
    if role not in (None, "hr"):
        raise HTTPException(403, "HR only")
    return [{"topic": t, "count": n, "examples": ex} for t, n, ex in DESK.gaps()]


@app.get("/v1/audit")
def audit(role: str | None = Depends(caller_role)):
    if role not in (None, "hr"):
        raise HTTPException(403, "HR only")
    docs = list(DESK.docs.values())
    return {
        "changes": [f.as_dict() for f in changes(docs)],
        "conflicts": [f.as_dict() for f in conflicts(docs)],
    }

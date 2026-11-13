import pytest

from clausewise.evaluate import main


def test_benchmark_gate(tmp_path):
    assert main(["--out", str(tmp_path), "--gate"]) == 0
    assert "Clausewise" in (tmp_path / "report.md").read_text()


def test_api_roles_come_from_keys(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from clausewise.api import app

    monkeypatch.setenv("CLAUSEWISE_KEYS", "e1:employee,h1:hr")
    c = TestClient(app)
    assert c.get("/health").json()["documents"] == 10
    q = {"question": "What is the salary band for G5?", "role": "hr", "as_of": "2025-11-01"}
    emp = c.post("/v1/ask", json=q, headers={"X-API-Key": "e1"}).json()
    assert emp["status"] == "restricted"  # the body says hr, the key says employee
    hr = c.post("/v1/ask", json=q, headers={"X-API-Key": "h1"}).json()
    assert hr["status"] == "answered"
    assert c.get("/v1/gaps", headers={"X-API-Key": "e1"}).status_code == 403
    assert len(c.get("/v1/audit", headers={"X-API-Key": "h1"}).json()["conflicts"]) == 1
    assert c.post("/v1/ask", json=q).status_code == 401


def test_dashboard():
    pytest.importorskip("streamlit")
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=60).run()
    assert not at.exception

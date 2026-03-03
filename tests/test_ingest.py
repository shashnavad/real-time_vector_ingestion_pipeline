import time
from fastapi.testclient import TestClient

from app.main import app
from app.vector_store import default_vector_store


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ingest_and_get():
    payload = {"id": "test-doc-1", "text": "This is a test document.", "metadata": {"source": "unit-test"}}
    r = client.post("/ingest", json=payload)
    assert r.status_code == 200
    assert r.json()["id"] == payload["id"]

    # stored immediately in MVP implementation
    got = default_vector_store.get(payload["id"])
    assert got is not None
    assert got["id"] == payload["id"]
    assert "vector" in got


def test_query_returns_ingested_doc():
    # query by similar text and expect the previously ingested doc to be top result
    q = {"text": "This is a test document.", "top_k": 3}
    r = client.post("/query", json=q)
    assert r.status_code == 200
    res = r.json().get("results", [])
    assert len(res) > 0
    ids = [r["id"] for r in res]
    assert "test-doc-1" in ids

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

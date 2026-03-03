from app.vector_store import InMemoryVectorStore


def test_inmemory_upsert_and_get():
    s = InMemoryVectorStore()
    v = [1.0, 0.0, 0.0]
    s.upsert("id1", v, {"k": "v"})
    got = s.get("id1")
    assert got is not None
    assert got["id"] == "id1"
    assert got["metadata"]["k"] == "v"


def test_query_returns_ranked_results():
    s = InMemoryVectorStore()
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    s.upsert("a", v1, {})
    s.upsert("b", v2, {})
    res = s.query([1.0, 0.0, 0.0], top_k=2)
    assert len(res) == 2
    # highest score should be the nearest (a)
    assert res[0][0] == "a"
    assert res[0][1] >= res[1][1]

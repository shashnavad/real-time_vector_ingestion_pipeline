from app.embeddings import Embeddings, default_embeddings


def test_encode_returns_vectors():
    texts = ["hello", "world"]
    vecs = default_embeddings.encode(texts)
    assert isinstance(vecs, list)
    assert len(vecs) == 2
    assert all(isinstance(v, list) for v in vecs)
    assert all(len(v) > 0 for v in vecs)


def test_custom_embeddings_fallback():
    # instantiate with a dummy model name to force fallback if sentence-transformers absent
    e = Embeddings(model_name="non-existent-model-for-test")
    vecs = e.encode(["test"])
    assert isinstance(vecs, list)
    assert len(vecs[0]) > 0

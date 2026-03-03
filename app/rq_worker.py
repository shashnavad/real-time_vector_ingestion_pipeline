"""RQ worker functions.

This module exposes `process_document` which is referenced by enqueued jobs.
When running `rq worker` the function must be importable as `app.rq_worker.process_document`.
"""
from typing import Any, Dict


def process_document(id: str, text: str, metadata: Dict[str, Any] | None = None):
    """Process a single document: compute embedding and upsert to vector store.

    This is intentionally simple. In production you might add logging, retries,
    instrumentation, and error handling.
    """
    from .embeddings import default_embeddings
    from .vector_store import default_vector_store

    vectors = default_embeddings.encode([text])
    vector = vectors[0]
    default_vector_store.upsert(id, vector, metadata)
    return True

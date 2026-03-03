"""FastAPI ingestion service for the MVP.

Provides a single `/ingest` endpoint that accepts a document id, text, and
optional metadata, computes an embedding, and stores it in the local vector store.
This is intentionally simple so it runs in a developer environment without external
infrastructure.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from .embeddings import default_embeddings
from .vector_store import default_vector_store


class IngestRequest(BaseModel):
    id: str = Field(..., description="Unique document identifier")
    text: str = Field(..., description="Text to embed")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional metadata")


app = FastAPI(title="Real-Time Vector Ingestion MVP")


@app.post("/ingest")
def ingest(req: IngestRequest):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text must be non-empty")
    try:
        vectors = default_embeddings.encode([req.text])
        vector = vectors[0]
        default_vector_store.upsert(req.id, vector, req.metadata)
        return {"status": "ok", "id": req.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}

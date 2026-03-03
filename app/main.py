"""FastAPI ingestion service for the MVP.

Provides a single `/ingest` endpoint that accepts a document id, text, and
optional metadata, computes an embedding, and stores it in the local vector store.
This is intentionally simple so it runs in a developer environment without external
infrastructure.
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette import status
import os
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from .embeddings import default_embeddings
from .vector_store import default_vector_store
from .queue import default_queue


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
        # If a Redis-backed queue is available (REDIS_URL), enqueue the processing job
        if default_queue.enabled:
            default_queue.enqueue("app.rq_worker.process_document", req.id, req.text, req.metadata)
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "queued", "id": req.id})

        # Otherwise process inline for local dev / tests
        vectors = default_embeddings.encode([req.text])
        vector = vectors[0]
        default_vector_store.upsert(req.id, vector, req.metadata)
        return {"status": "ok", "id": req.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}

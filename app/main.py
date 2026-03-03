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
from .queue import default_messenger


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
        # Produce message into Kafka topic 'raw-documents' when available.
        payload = {"id": req.id, "text": req.text, "metadata": req.metadata}
        prod_res = default_messenger.produce("raw-documents", payload)

        # If Kafka/RQ was used the produce call will return a non-dict (send result or job).
        if prod_res is not None and not isinstance(prod_res, dict):
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "produced", "id": req.id})

        # Inline fallback: process synchronously
        vectors = default_embeddings.encode([req.text])
        vector = vectors[0]
        default_vector_store.upsert(req.id, vector, req.metadata)
        return {"status": "ok", "id": req.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}


class QueryRequest(BaseModel):
    text: Optional[str] = Field(None, description="Text to query")
    top_k: int = Field(5, description="Number of nearest neighbors to return")


@app.post("/query")
def query(req: QueryRequest):
    """Query the vector store by text. Returns top_k nearest documents.

    If sentence-transformers is installed this will compute an embedding for the
    query text; otherwise it uses the deterministic fallback embedding.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text must be non-empty")
    try:
        qvec = default_embeddings.encode([req.text])[0]
        results = default_vector_store.query(qvec, top_k=req.top_k)
        # results are tuples (id, score, metadata)
        out = []
        for rid, score, metadata in results:
            out.append({"id": rid, "score": float(score), "metadata": metadata})
        return {"results": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

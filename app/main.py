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
import hashlib
import time
import json


class IngestRequest(BaseModel):
    # id is optional from the caller; we'll compute a deterministic id if omitted
    id: Optional[str] = Field(None, description="Unique document identifier (optional)")
    text: str = Field(..., description="Text to embed")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional metadata")


app = FastAPI(title="Real-Time Vector Ingestion MVP")


@app.post("/ingest")
def ingest(req: IngestRequest):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text must be non-empty")
    try:
        # Deterministic ID: if client provided an id use it, otherwise compute sha256 of text+metadata
        meta_json = json.dumps(req.metadata, sort_keys=True, default=str) if req.metadata else ""
        base = (req.id or "") + "|" + req.text + "|" + meta_json
        det_id = hashlib.sha256(base.encode("utf-8")).hexdigest()

        # Sequence/version: high-precision timestamp in milliseconds
        ingest_ts = int(time.time() * 1000)

        # Compose the canonical payload (source-of-truth schema)
        payload = {
            "id": det_id,
            "original_id": req.id,
            "text": req.text,
            "metadata": req.metadata or {},
            "version": ingest_ts,
            "ingest_ts": ingest_ts,
        }
        prod_res = default_messenger.produce("raw-documents", payload)

        # If Kafka/RQ was used the produce call will return a non-dict (send result or job).
        if prod_res is not None and not isinstance(prod_res, dict):
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "produced", "id": det_id})

        # Inline fallback: process synchronously (also record metrics if Redis available)
        vectors = default_embeddings.encode([req.text])
        vector = vectors[0]
        default_vector_store.upsert(det_id, vector, req.metadata)

        # record metric to Redis if configured
        try:
            redis_url = os.environ.get("REDIS_URL")
            if redis_url:
                import redis as _redis
                r = _redis.from_url(redis_url)
                now = int(time.time() * 1000)
                latency = now - ingest_ts
                r.incr("metrics:count", 1)
                # INCRBYFLOAT may not exist in some clients; use incrbyfloat if available
                try:
                    r.incrbyfloat("metrics:total_latency", float(latency))
                except Exception:
                    # fallback: get existing total and set
                    try:
                        prev = float(r.get("metrics:total_latency") or 0)
                        r.set("metrics:total_latency", prev + float(latency))
                    except Exception:
                        pass
                r.set("metrics:last_latency", latency)
        except Exception:
            pass

        return {"status": "ok", "id": det_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    """Return simple ingestion metrics (avg processing latency in ms and count).

    Metrics are aggregated in Redis by the streaming job or inline fallback.
    """
    try:
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            return {"count": 0, "avg_latency_ms": None, "last_latency_ms": None}
        import redis as _redis
        r = _redis.from_url(redis_url)
        count = int(r.get("metrics:count") or 0)
        total = float(r.get("metrics:total_latency") or 0)
        last = r.get("metrics:last_latency")
        last_val = int(last) if last is not None else None
        avg = (total / count) if count > 0 else None
        return {"count": count, "avg_latency_ms": avg, "last_latency_ms": last_val}
    except Exception:
        return {"count": 0, "avg_latency_ms": None, "last_latency_ms": None}


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

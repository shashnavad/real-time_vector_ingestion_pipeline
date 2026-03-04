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
        # If client provided an explicit id, respect it. Otherwise compute a
        # deterministic id from text+metadata so we can deduplicate and do
        # idempotent upserts downstream.
        base = (req.id or "") + "|" + req.text + "|" + meta_json
        if req.id:
            det_id = req.id
        else:
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
        # Persist a small metadata sidecar into Redis (source registry) so we can
        # later audit/verify the sink. This happens before producing to Kafka so
        # the registry reflects the intended source state.
        try:
            redis_url = os.environ.get("REDIS_URL")
            if redis_url:
                import redis as _redis
                r = _redis.from_url(redis_url)
                # store JSON blob under a single hash for simplicity
                try:
                    r.hset("source:registry", det_id, json.dumps({"version": ingest_ts, "hash": hashlib.sha256(base.encode("utf-8")).hexdigest(), "original_id": req.id}))
                    r.sadd("source:ids", det_id)
                except Exception:
                    pass
        except Exception:
            pass

        prod_res = default_messenger.produce("raw-documents", payload)

        # If Kafka/RQ was used the produce call will return a non-dict (send result or job).
        if prod_res is not None and not isinstance(prod_res, dict):
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "produced", "id": det_id})

        # Inline fallback: process synchronously (also record metrics if Redis available)
        vectors = default_embeddings.encode([req.text])
        vector = vectors[0]
        # Ensure we persist the version in the sink metadata so auditors can verify
        sink_meta = dict(req.metadata or {})
        sink_meta["_version"] = ingest_ts
        sink_meta["_original_id"] = req.id
        default_vector_store.upsert(det_id, vector, sink_meta)

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


@app.get("/audit")
def audit(sample_size: int = 10):
    """Audit a random sample of source IDs and verify the sink has the expected version.

    Returns a small report: total sampled, missing_in_sink, version_mismatches.
    Requires `REDIS_URL` to be configured (the source registry).
    """
    try:
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            raise HTTPException(status_code=400, detail="REDIS_URL not configured; audit requires a source registry")
        import redis as _redis
        r = _redis.from_url(redis_url)
        # sample IDs from the set
        ids = r.srandmember("source:ids", sample_size) or []
        # decode bytes if necessary
        ids = [i.decode("utf-8") if isinstance(i, (bytes, bytearray)) else i for i in ids]

        missing = []
        version_mismatch = []
        checked = 0
        qdrant_url = os.environ.get("QDRANT_URL")
        collection = os.environ.get("QDRANT_COLLECTION", "documents")

        for _id in ids:
            checked += 1
            reg = r.hget("source:registry", _id)
            if not reg:
                missing.append({"id": _id, "reason": "no-registry-entry"})
                continue
            try:
                reg_obj = json.loads(reg)
            except Exception:
                reg_obj = None
            expected_version = reg_obj.get("version") if reg_obj else None

            found = None
            # try Qdrant first if configured
            if qdrant_url:
                try:
                    try:
                        from qdrant_client import QdrantClient
                        client = QdrantClient(url=qdrant_url)
                        # try to retrieve point payload (method may vary by client version)
                        try:
                            point = client.get_point(collection_name=collection, id=_id)
                            # client.get_point may return a dict with 'payload' or similar
                            payload = None
                            if isinstance(point, dict):
                                payload = point.get("payload") or point.get("result") or None
                            elif hasattr(point, "payload"):
                                payload = getattr(point, "payload")
                            if payload is not None:
                                found = {"meta": payload}
                        except Exception:
                            # some client versions don't expose get_point; fall through to None
                            found = None
                    except Exception:
                        found = None
                except Exception:
                    found = None

            # fallback to local vector store check
            if found is None:
                try:
                    res = default_vector_store.get(_id)
                    if res is None:
                        missing.append({"id": _id, "reason": "not-in-sink"})
                        continue
                    found = {"meta": res.get("metadata")}
                except Exception:
                    missing.append({"id": _id, "reason": "error-checking-sink"})
                    continue

            # compare version
            sink_meta = found.get("meta") or {}
            sink_version = sink_meta.get("_version")
            if expected_version is None:
                version_mismatch.append({"id": _id, "expected": expected_version, "found": sink_version})
            else:
                try:
                    if int(sink_version or 0) != int(expected_version or 0):
                        version_mismatch.append({"id": _id, "expected": expected_version, "found": sink_version})
                except Exception:
                    version_mismatch.append({"id": _id, "expected": expected_version, "found": sink_version})

        return {"sampled": checked, "missing": missing, "version_mismatch": version_mismatch}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dashboard")
def dashboard():
    """Return a tiny HTML dashboard that polls /metrics and shows basic values.

    This is intentionally lightweight so it works in environments without a
    frontend framework. It uses a tiny client-side fetch to update values.
    """
    html = """
    <!doctype html>
    <html>
    <head><meta charset="utf-8"><title>Ingestion Dashboard</title></head>
    <body>
    <h2>Ingestion Metrics (live)</h2>
    <div id="metrics">Loading...</div>
    <script>
    async function fetchMetrics(){
      try{
        const r = await fetch('/metrics');
        const j = await r.json();
        document.getElementById('metrics').innerText = JSON.stringify(j, null, 2);
      }catch(e){ document.getElementById('metrics').innerText = 'error: '+e }
    }
    fetchMetrics(); setInterval(fetchMetrics, 2000);
    </script>
    </body>
    </html>
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html, status_code=200)


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

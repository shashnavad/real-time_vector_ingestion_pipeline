# Project status — Real-Time Vector Ingestion Pipeline (MVP)

This file tracks the current implementation status of the MVP and what still
needs to be done.

## Overview

1. What

   A lightweight, streaming-first MVP that receives text documents via an
   HTTP ingest API, computes embeddings, and keeps a vector database (Qdrant)
   in sync with the source using a streaming pipeline. The project prefers
   free/open-source components and provides safe local fallbacks for quick
   iteration.

2. Core Flow

   The canonical flow is illustrated below. The ingestion API writes a small
   metadata sidecar to a source registry (Redis) to enable CDC-style auditing
   and then produces a canonical JSON message to a Kafka topic (`raw-documents`).

   ASCII Architecture Diagram

   ```text
   +------------+     +------------+     +-----------+     +------------+
   |  Producer  | --> | Ingest API | --> |  Kafka    | --> |  Spark     |
   | (client)   |     | (FastAPI)  |     | (Redpanda)|     | Structured |
   +------------+     +------------+     +-----------+     | Streaming  |
          |                 |                |             +-----+------+
          |                 |                |                   |
          |                 |                |                   v
          |                 |                |             +------------+
          |                 |                |             |  Qdrant     |
          |                 |                |             |  (Vector DB)|
          |                 |                |             +------------+
          |                 |                |                   ^
          |                 |                |                   |
          |                 |                |             +-----+------+
          |                 |                |             |  Redis      |
          |                 |                |             | (source     |
          |                 |                |             |  registry,  |
          |                 |                |             |  metrics)   |
          |                 |                |             +------------+
          |                 |                |
          |                 |                +--> (RQ fallback worker)
          |                 |                          (local in-memory fallback)
          |                 v
   (client can also query)--> [Query API] --> default_vector_store (Chroma / in-memory)
   ```

3. Critical Components

   - FastAPI ingestion service (`app/main.py`) — produces canonical messages and
     writes a Redis metadata sidecar for CDC.
   - Kafka-compatible broker (Redpanda) — transport for raw-document stream.
   - Spark Structured Streaming job (`app/streaming.py`) — batches messages,
     computes embeddings, deduplicates by id/version, and upserts to Qdrant.
   - Qdrant — production vector sink. Chroma / in-memory used as developer
     fallback via `app/vector_store.py`.
   - Embeddings (`app/embeddings.py`) — default: 768-d BERT-style sentence-transformers; fallback: deterministic 768-d hash vectors.
   - Redis — source registry for CDC, and simple metrics storage for latency/counts.

4. Key Decisions

   - Streaming-first architecture (Kafka + Spark) to allow batching of
     expensive embedding inference and independent scaling of ingestion vs
     embedding compute.
   - Deterministic producer ids when client does not provide one; respects
     client-provided ids when present. Producer writes a version timestamp
     so sinks can perform idempotent upserts.
   - Use Redis as a lightweight source registry sidecar to enable auditing and
     reconciliation without requiring a full upstream database.
   - Keep local, dependency-light fallbacks (in-memory store, deterministic
     embeddings) so contributors can run the project without large downloads.

5. Failure Points to Monitor

   - Kafka backlog / Redpanda availability: monitor topic lag and consumer
     progress (Spark offsets). Backpressure is controlled via
     `SPARK_MAX_OFFSETS_PER_TRIGGER` and `SPARK_PROCESSING_TIME`.
   - Qdrant upsert failures: streaming job logs should surface client/API
     errors; audit endpoint can detect missing or stale points.
   - Redis availability: source registry and metrics rely on Redis; if
     Redis is down auditing and metrics will be limited.
   - Model availability: if `sentence-transformers` is not installed or the
     chosen model fails to download, the deterministic fallback will be
     active (good for dev but reduces semantic quality).

6. Performance Profile

   - Target: sub-200ms end-to-end for small payloads in low-load conditions when
     the streaming job processes micro-batches frequently (e.g., 200ms
     trigger). Realistic throughput and latency depend strongly on model
     latency (embedding inference) and Spark batch size.
   - Controls available:
     - `SPARK_PROCESSING_TIME` (default `200ms`) — lower for lower tail latency,
       higher for better throughput efficiency.
     - `SPARK_MAX_OFFSETS_PER_TRIGGER` — limits per-trigger message volume to
       control backpressure and inference batching sizes.


Implemented:
- Read and parsed the project checklist (`Real-Time Vector Ingestion Pipeline.md`).
- Chosen free-tier-first stack (Python, FastAPI, sentence-transformers, Chroma/FAISS fallback).
 - Project scaffold with core modules:
  - `app/main.py` - FastAPI endpoints (`/ingest`, `/query`, `/health`). The `/ingest`
    endpoint now produces to a Kafka topic (`raw-documents`).
  - `app/embeddings.py` - Embedding wrapper with sentence-transformers and deterministic fallback.
  - `app/vector_store.py` - Vector store wrapper (Chroma backend when available, in-memory fallback).
  - `app/queue.py` - Messaging wrapper that prefers Kafka (via `KAFKA_BOOTSTRAP_SERVERS`),
    falls back to Redis+RQ, and otherwise executes inline for local dev/tests.
  - `app/rq_worker.py` - RQ worker function used by enqueued jobs (fallback path).
  - `app/streaming.py` - Spark Structured Streaming job (reads Kafka `raw-documents`, computes
    embeddings, sinks to Qdrant or local store). Streaming improvements:
    - Configurable `SPARK_PROCESSING_TIME` (default 200ms) and
      `SPARK_MAX_OFFSETS_PER_TRIGGER` to control backpressure and latency.
    - Deduplication within micro-batches by `id` + `version` (keep latest per id).
    - Idempotent upserts to Qdrant using deterministic `id` produced by the API.
    - Emits simple latency metrics into Redis (keys: `metrics:count`, `metrics:total_latency`, `metrics:last_latency`) so the web API `/metrics` can report average processing latency and ingestion counts.
  - Qdrant is included in `docker-compose.yml` as the default vector DB for the
    compose environment; the streaming job will upsert vectors to Qdrant when
    `QDRANT_URL` is present (set to `http://qdrant:6333` by compose).
  - `Dockerfile`, `docker-compose.yml` for local durable setup (Redpanda + Redis + web + worker).
  - Metadata sidecar and CDC support:
    - `app/main.py` now writes a small source registry into Redis (`source:registry` and `source:ids`) when `REDIS_URL` is configured.
    - This enables CDC-style auditing and reconciliation.
  - Consistency auditor and dashboard:
    - Added `/audit` endpoint to sample IDs from the source registry and verify the sink contains the expected `_version`.
    - Added a lightweight `/dashboard` HTML page that polls `/metrics` for live ingestion latency and counts.
  - Embeddings model: switched default to a 768-dimensional BERT-style sentence-transformers model (`sentence-transformers/bert-base-nli-mean-tokens`) and made the fallback deterministic generator produce 768-d vectors.
- Tests and CI:
  - Unit tests under `tests/` cover embeddings, vector store, ingest and query flows.
  - GitHub Actions workflow `/.github/workflows/ci.yml` runs tests on push/PR. The latest CI run (after fixing the Python matrix) completed successfully: https://github.com/shashnavad/real-time_vector_ingestion_pipeline/actions/runs/22611192680
- Repository initialized and pushed to GitHub (branch `main`).

Next work (short-term):
- Add durable background worker behaviors (retries, logging, monitoring) to `app/rq_worker.py`.
- Add a small sample client for bulk ingestion and example data.
- Add integration tests that exercise Docker Compose (Redis + worker) — optional (CI cost/time tradeoff).
- Provide deployment instructions for a chosen free-tier host (Render, Railway, Fly.io) with a Docker image.

Smoke test / Quick verification (local)
1. Run the API locally (inline queue / no Redis):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r test-requirements.txt
uvicorn app.main:app --reload
```

2. Ingest a sample document:

```bash
curl -X POST "http://127.0.0.1:8000/ingest" -H "Content-Type: application/json" -d '{"id":"doc1","text":"Hello world","metadata":{"source":"local"}}'
```

3. Query for nearest neighbors:

```bash
curl -X POST "http://127.0.0.1:8000/query" -H "Content-Type: application/json" -d '{"text":"Hello world","top_k":3}'
```


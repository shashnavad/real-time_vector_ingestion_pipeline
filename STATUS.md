# Project status — Real-Time Vector Ingestion Pipeline (MVP)

This file tracks the current implementation status of the MVP and what still
needs to be done.

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
    embeddings, sinks to Qdrant or local store).
  - Qdrant is included in `docker-compose.yml` as the default vector DB for the
    compose environment; the streaming job will upsert vectors to Qdrant when
    `QDRANT_URL` is present (set to `http://qdrant:6333` by compose).
  - `Dockerfile`, `docker-compose.yml` for local durable setup (Redpanda + Redis + web + worker).
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


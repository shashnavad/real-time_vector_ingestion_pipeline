# Project status — Real-Time Vector Ingestion Pipeline (MVP)

This file tracks the current implementation status of the MVP and what still
needs to be done.

Implemented:
- Read and parsed the project checklist (`Real-Time Vector Ingestion Pipeline.md`).
- Chosen free-tier-first stack (Python, FastAPI, sentence-transformers, Chroma/FAISS fallback).
- Project skeleton created with:
  - `app/main.py` - FastAPI ingestion endpoint (`/ingest`).
  - `app/embeddings.py` - Embedding wrapper with deterministic fallback.
  - `app/vector_store.py` - Vector store wrapper with Chroma fallback and in-memory fallback.
  - `requirements.txt`, `README.md`, `.gitignore`, `STATUS.md`.
- Basic pytest-compatible test added (see `tests/`).

Next work (short-term):
- Add background worker / durable queue (Redis + RQ) — optional: keep as background tasks for v1.
- Add CI (GitHub Actions) to run tests on push.
- Add more integration tests and a sample client script to ingest bulk data.
- Provide deployment guide for a free-tier host.

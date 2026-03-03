# Real-Time Vector Ingestion Pipeline — MVP (Free-tier first)

This repository contains an MVP for a real-time vector ingestion pipeline that is
designed to be runnable locally and on free tiers. The aim is to provide a
minimal end-to-end flow: receive text, compute embeddings, and store vectors in
a local vector store.

Key components
- FastAPI HTTP API (/ingest)
- Embeddings using `sentence-transformers` if available; otherwise a deterministic fallback
- Vector store that uses Chroma if installed, otherwise an in-memory fallback

Quick start (local)

1. Create Python virtualenv and activate it

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies (optional — the code can run without heavy packages thanks to fallbacks)

```bash
pip install -r requirements.txt
```

3. Run the API

```bash
uvicorn app.main:app --reload
```

4. Ingest a document

```bash
curl -X POST "http://127.0.0.1:8000/ingest" -H "Content-Type: application/json" -d '{"id":"doc1","text":"Hello world","metadata":{"source":"test"}}'
```

Notes
- The project has safe fallbacks so you don't need to install large ML models to iterate.
- To enable true embeddings, install `sentence-transformers` (and optionally `chromadb`).
- For production, replace in-memory fallbacks with Chroma/Milvus/Pinecone and add background workers/queueing.

Next steps implemented in repo
- Basic ingestion API (POST /ingest)
- Embeddings wrapper with fallback
- Vector store wrapper with Chroma fallback to in-memory store
- Simple test harness and status file

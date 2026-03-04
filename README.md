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

Querying (nearest neighbors)

After ingesting documents you can query the vector store by text. Example:

```bash
curl -X POST "http://127.0.0.1:8000/query" -H "Content-Type: application/json" -d '{"text":"Hello world","top_k":5}'
```

The response format is:

```json
{ "results": [ { "id": "doc1", "score": 0.98, "metadata": {...} }, ... ] }
```

Source-of-truth message schema (producer)

The `/ingest` endpoint now produces a canonical JSON message to Kafka topic
`raw-documents`. Each message uses a deterministic ID and includes a version
timestamp and ingest timestamp. Example fields:

```json
{
	"id": "<deterministic-sha256>",
	"original_id": "<optional-client-id>",
	"text": "...",
	"metadata": { ... },
	"version": 1670000000000,
	"ingest_ts": 1670000000000
}
```

This enables idempotent upserts in the streaming layer.

Notes
- The project has safe fallbacks so you don't need to install large ML models to iterate.
- To enable true embeddings, install `sentence-transformers` (and optionally `chromadb`).
- For production, replace in-memory fallbacks with Chroma/Milvus/Pinecone and add background workers/queueing.

Next steps implemented in repo
- Basic ingestion API (POST /ingest)
- Embeddings wrapper with fallback
- Vector store wrapper with Chroma fallback to in-memory store
- Simple test harness and status file

Running with Redpanda (Kafka) + Spark streaming (recommended)

The project now uses a streaming-first architecture: the `/ingest` endpoint
produces JSON messages onto a Kafka topic (`raw-documents`). A separate Spark
Structured Streaming job reads that topic, computes embeddings (using the same
embedding helper), and sinks vectors into Qdrant (if configured) or the local
vector store as a fallback.

Docker Compose includes Redpanda (a lightweight Kafka-compatible broker), Redis
and the web/worker images. To run the streaming stack locally:

1. Build and start services (Redpanda + Redis + web + worker):

```bash
docker compose build --pull
docker compose up -d
```

2. The web API will be available at http://localhost:8000. By default the app
	will attempt to produce to `redpanda:9092` (configured via
	`KAFKA_BOOTSTRAP_SERVERS`).

3. Start the Spark streaming job to consume `raw-documents` and sink vectors.
	You can run the stream inside the web image (it contains the Python deps):

```bash
# run streaming job in the web container
docker compose run --rm web python -m app.streaming
```

	Alternatively run it locally after installing `pyspark` and other optional
	deps:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.streaming
```

4. Verify Qdrant and streaming sink

If you ran `docker compose up` with the provided compose file, a Qdrant instance
is available on http://localhost:6333 and the streaming job (when started) will
upsert vectors into the default collection (or `QDRANT_COLLECTION` if you set
that environment variable). Example:

```bash
# list collections
curl http://localhost:6333/collections

# query a collection (replace <collection>)
curl http://localhost:6333/collections/<collection>/points/scroll -H 'Content-Type: application/json' -d '{"limit": 5}'
```

5. (Optional) View worker logs (the RQ worker is still present as a fallback):

```bash
docker compose logs -f worker
```

6. Tear down the services:

```bash
docker compose down
```

Notes about the RQ fallback
- The code keeps a Redis+RQ worker as a fallback processing path when Kafka is
	not available. However the primary, recommended flow is stream-based using
	Kafka + Spark + Qdrant so you can scale and batch embeddings independently of the
	ingestion API.

Source registry & auditing
- When a document is ingested the service (if `REDIS_URL` is configured)
	writes a small sidecar record in Redis (`source:registry` and `source:ids`) containing the
	deterministic id and version. This lets the `/audit` endpoint sample source IDs
	and verify that the sink (Qdrant or local store) contains the same version. See `/audit`.

Notes about embeddings
- The project now prefers a 768-dimensional BERT-style sentence-transformers
	model (default `sentence-transformers/bert-base-nli-mean-tokens`). If that model
	is not installed the code falls back to a deterministic 768-d hash->vector
	generator so the project remains runnable without large downloads.

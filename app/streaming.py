"""Spark Structured Streaming job to read from Kafka, compute embeddings, and sink to Qdrant or local store.

This script is intended to be run as a separate process. It reads JSON messages
from the `raw-documents` Kafka topic, batches them, computes embeddings using the
Embeddings helper (which will use sentence-transformers if available), and then
writes vectors to Qdrant if `QDRANT_URL` is configured, otherwise falls back to
the local vector store for development.
"""
import os
import json
from typing import List


def start_stream(kafka_bootstrap_servers: str = None, topic: str = "raw-documents"):
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import col, from_json, udf
    from pyspark.sql.types import StringType, StructType, StructField, MapType

    spark = SparkSession.builder.appName("vector-ingest-stream").getOrCreate()

    # Read from Kafka
    # Configure Kafka source with a cap per trigger to help backpressure handling
    df = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", kafka_bootstrap_servers or os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
        .option("subscribe", topic)
        .option("startingOffsets", "earliest")
        .option("maxOffsetsPerTrigger", os.environ.get("SPARK_MAX_OFFSETS_PER_TRIGGER", "1000"))
        .load()
    )

    # Assume value is UTF-8 JSON
    df2 = df.selectExpr("CAST(value AS STRING) as value")

    from pyspark.sql.types import LongType

    schema = StructType([
        StructField("id", StringType()),
        StructField("original_id", StringType()),
        StructField("text", StringType()),
        StructField("metadata", MapType(StringType(), StringType())),
        StructField("version", LongType()),
        StructField("ingest_ts", LongType()),
    ])

    parsed = df2.withColumn("json", from_json(col("value"), schema)).select("json.*")

    # UDF to compute embeddings using app.embeddings.default_embeddings
    def embed_text(s: str) -> List[float]:
        from app.embeddings import Embeddings, default_embeddings

        return default_embeddings.encode([s])[0]

    embed_udf = udf(lambda x: embed_text(x), StringType())

    # We'll use foreachBatch to handle batches and sink using Python client (Qdrant or local)
    def foreach_batch(batch_df, epoch_id):
        rows = batch_df.collect()

        # Deduplicate within the micro-batch: keep only the latest version per id
        latest = {}
        for r in rows:
            rid = r["id"]
            ver = r["version"] if r["version"] is not None else 0
            if rid not in latest or ver >= (latest[rid]["version"] or 0):
                latest[rid] = {"row": r, "version": ver}

        ids = []
        texts = []
        metadatas = []
        versions = []
        ingest_ts_list = []
        for rid, info in latest.items():
            row = info["row"]
            ids.append(row["id"])
            texts.append(row["text"])
            metadatas.append(row["metadata"])
            versions.append(row["version"])
            ingest_ts_list.append(row["ingest_ts"])    

        # compute embeddings in Python using the same helper (batch)
        from app.embeddings import default_embeddings

        vectors = default_embeddings.encode(texts)

        # Try Qdrant sink first
        qdrant_url = os.environ.get("QDRANT_URL")
        if qdrant_url:
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.http.models import PointStruct

                client = QdrantClient(url=qdrant_url)
                # ensure collection exists
                collection_name = os.environ.get("QDRANT_COLLECTION", "documents")
                try:
                    client.get_collection(collection_name)
                except Exception:
                    client.recreate_collection(collection_name, vector_size=len(vectors[0]))

                points = []
                for _id, vec, meta in zip(ids, vectors, metadatas):
                    # use deterministic id provided by producer to allow idempotent upserts
                    points.append(PointStruct(id=_id, vector=vec, payload=meta or {}))

                client.upsert(collection_name=collection_name, points=points)

                # record latencies into Redis (if configured)
                try:
                    import redis as _redis
                    redis_url = os.environ.get("REDIS_URL")
                    if redis_url:
                        r = _redis.from_url(redis_url)
                        now_ms = int(__import__("time").time() * 1000)
                        for ingest_ts in ingest_ts_list:
                            try:
                                latency = now_ms - int(ingest_ts or 0)
                                r.incr("metrics:count", 1)
                                try:
                                    r.incrbyfloat("metrics:total_latency", float(latency))
                                except Exception:
                                    prev = float(r.get("metrics:total_latency") or 0)
                                    r.set("metrics:total_latency", prev + float(latency))
                                r.set("metrics:last_latency", latency)
                            except Exception:
                                pass
                except Exception:
                    pass

                return
            except Exception:
                # fall back to local vector store
                pass

        # Fallback: upsert to local store
        from app.vector_store import default_vector_store

        for _id, vec, meta in zip(ids, vectors, metadatas):
            default_vector_store.upsert(_id, vec, meta)

    # configure trigger interval for low-latency processing and a checkpoint
    processing_time = os.environ.get("SPARK_PROCESSING_TIME", "200ms")
    checkpoint = os.environ.get("SPARK_CHECKPOINT_LOCATION", "/tmp/spark_checkpoints")
    query = (
        parsed.writeStream.foreachBatch(foreach_batch)
        .trigger(processingTime=processing_time)
        .option("checkpointLocation", checkpoint)
        .start()
    )
    query.awaitTermination()


if __name__ == "__main__":
    start_stream()

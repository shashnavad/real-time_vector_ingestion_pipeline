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
    df = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", kafka_bootstrap_servers or os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
        .option("subscribe", topic)
        .option("startingOffsets", "earliest")
        .load()
    )

    # Assume value is UTF-8 JSON
    df2 = df.selectExpr("CAST(value AS STRING) as value")

    schema = StructType([
        StructField("id", StringType()),
        StructField("text", StringType()),
        StructField("metadata", MapType(StringType(), StringType())),
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
        texts = [r["text"] for r in rows]
        ids = [r["id"] for r in rows]
        metadatas = [r["metadata"] for r in rows]

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
                    points.append(PointStruct(id=_id, vector=vec, payload=meta or {}))

                client.upsert(collection_name=collection_name, points=points)
                return
            except Exception:
                # fall back to local vector store
                pass

        # Fallback: upsert to local store
        from app.vector_store import default_vector_store

        for _id, vec, meta in zip(ids, vectors, metadatas):
            default_vector_store.upsert(_id, vec, meta)

    query = parsed.writeStream.foreachBatch(foreach_batch).start()
    query.awaitTermination()


if __name__ == "__main__":
    start_stream()

"""Messaging wrapper supporting Kafka (preferred) and Redis+RQ fallback.

This module exposes `default_messenger` which attempts to connect to Kafka when
`KAFKA_BOOTSTRAP_SERVERS` is provided. If Kafka is not configured or unavailable,
it falls back to Redis+RQ (if `REDIS_URL` present). If neither is available, it
falls back to inline processing for local development and tests.
"""
import json
import os
from typing import Any, Dict, Optional


class Messenger:
    def __init__(self):
        self._kafka_producer = None
        self._rq_queue = None
        self._redis = None
        # Try Kafka first
        kafka_url = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
        if kafka_url:
            try:
                from kafka import KafkaProducer  # type: ignore

                self._kafka_producer = KafkaProducer(bootstrap_servers=kafka_url.split(","), value_serializer=lambda v: json.dumps(v).encode("utf-8"))
            except Exception:
                self._kafka_producer = None

        # If Kafka not configured/available, try Redis+RQ
        if self._kafka_producer is None:
            redis_url = os.environ.get("REDIS_URL")
            if redis_url:
                try:
                    from redis import Redis  # type: ignore
                    from rq import Queue  # type: ignore

                    self._redis = Redis.from_url(redis_url)
                    self._redis.ping()
                    self._rq_queue = Queue("default", connection=self._redis)
                except Exception:
                    self._rq_queue = None

    def produce(self, topic: str, payload: Dict[str, Any]) -> Optional[Any]:
        """Produce a JSON payload to Kafka topic `topic` if Kafka is available.

        If Kafka isn't available but RQ is, enqueue a worker job that will process
        the payload inline. If neither is available, return the payload directly
        (inline processing by caller).
        """
        if self._kafka_producer is not None:
            # async send
            future = self._kafka_producer.send(topic, payload)
            try:
                result = future.get(timeout=10)
                return result
            except Exception:
                # on failure, fall through to other methods
                pass

        if self._rq_queue is not None:
            # enqueue worker to process document (worker must import process_document)
            return self._rq_queue.enqueue("app.rq_worker.process_document", payload.get("id"), payload.get("text"), payload.get("metadata"))

        # Inline fallback: return payload to caller for synchronous processing
        return payload


# module-level default messenger
default_messenger = Messenger()

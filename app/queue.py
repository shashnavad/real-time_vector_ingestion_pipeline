"""Queue wrapper that uses RQ/Redis when available and falls back to inline execution.

This allows running the project locally without Redis while enabling a durable
queue when `REDIS_URL` is provided (for Docker Compose / production).
"""
import os
from typing import Any


class QueueWrapper:
    def __init__(self):
        self.enabled = False
        self._queue = None
        self._redis = None
        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            try:
                from redis import Redis  # type: ignore
                from rq import Queue  # type: ignore

                self._redis = Redis.from_url(redis_url)
                # simple health check
                self._redis.ping()
                self._queue = Queue("default", connection=self._redis)
                self.enabled = True
            except Exception:
                # Redis not reachable; remain disabled and fall back to inline
                self.enabled = False

    def enqueue(self, func_path: str, *args: Any, **kwargs: Any):
        """Enqueue a job by function import path (e.g. 'app.rq_worker.process_document').

        If Redis is enabled, pushes to RQ and returns the job object. Otherwise,
        imports the function and calls it inline (useful for local development and tests).
        """
        if self.enabled and self._queue is not None:
            return self._queue.enqueue(func_path, *args, **kwargs)

        # Inline fallback: import and call function synchronously
        modulename, fname = func_path.rsplit(".", 1)
        module = __import__(modulename, fromlist=[fname])
        fn = getattr(module, fname)
        return fn(*args, **kwargs)


# module-level default queue
default_queue = QueueWrapper()

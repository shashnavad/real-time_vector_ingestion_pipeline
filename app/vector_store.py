"""Vector store wrapper with optional Chroma backend and in-memory fallback.

Provides a minimal API: upsert(id, vector, metadata), get(id), and query(vector, top_k).
This lets the project run locally without needing an external vector DB.
"""
from typing import Any, Dict, List, Optional, Tuple
import threading
import math


class InMemoryVectorStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._vectors: Dict[str, List[float]] = {}
        self._metas: Dict[str, Dict[str, Any]] = {}

    def upsert(self, id: str, vector: List[float], metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._vectors[id] = vector
            self._metas[id] = metadata or {}

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if id not in self._vectors:
                return None
            return {"id": id, "vector": self._vectors[id], "metadata": self._metas.get(id, {})}

    def query(self, vector: List[float], top_k: int = 5) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Return top_k by cosine similarity as (id, score, metadata).
        Score is in [0,1] with 1 being identical.
        """
        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            norma = math.sqrt(sum(x * x for x in a))
            normb = math.sqrt(sum(y * y for y in b))
            if norma == 0 or normb == 0:
                return 0.0
            return dot / (norma * normb)

        with self._lock:
            scores = []
            for id, v in self._vectors.items():
                sc = cosine(vector, v)
                scores.append((id, sc, self._metas.get(id, {})))
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:top_k]


class VectorStore:
    def __init__(self, persist_directory: Optional[str] = None):
        self._impl = None
        try:
            import chromadb

            client = chromadb.Client(chromadb.config.Settings(chroma_db_impl="duckdb+parquet", persist_directory=persist_directory))
            self._collection = client.get_or_create_collection(name="documents")
            self._impl = "chroma"
        except Exception:
            # fallback to in-memory implementation
            self._impl = "memory"
            self._mem = InMemoryVectorStore()

    def upsert(self, id: str, vector: List[float], metadata: Optional[Dict[str, Any]] = None) -> None:
        if self._impl == "chroma":
            self._collection.add(ids=[id], embeddings=[vector], metadatas=[metadata or {}])
        else:
            self._mem.upsert(id, vector, metadata)

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        if self._impl == "chroma":
            res = self._collection.get(ids=[id])
            if not res or len(res.get("ids", [])) == 0:
                return None
            return {"id": res["ids"][0], "vector": res.get("embeddings", [None])[0], "metadata": res.get("metadatas", [None])[0]}
        else:
            return self._mem.get(id)

    def query(self, vector: List[float], top_k: int = 5) -> List[Tuple[str, float, Dict[str, Any]]]:
        if self._impl == "chroma":
            res = self._collection.query(query_embeddings=[vector], n_results=top_k)
            out = []
            for ids, scores, metadatas in zip(res.get("ids", []), res.get("distances", []), res.get("metadatas", [])):
                for i, _id in enumerate(ids):
                    out.append((_id, 1.0 - scores[i] if isinstance(scores[i], float) else 1.0 - scores[i][0], metadatas[i]))
            return out
        else:
            return self._mem.query(vector, top_k)


# module-level store
default_vector_store = VectorStore(persist_directory="chroma_db")

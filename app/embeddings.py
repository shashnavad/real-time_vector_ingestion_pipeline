"""Embedding utilities with a lightweight fallback for local/dev use.

Tries to use sentence-transformers if available. If not, falls back to a
deterministic hash->vector conversion so the project is runnable without
large downloads.
"""
from typing import List
import hashlib


class Embeddings:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(model_name)
        except Exception:
            # Fallback: no heavy deps available; use hashing-based vectors
            self._model = None

    def encode(self, texts: List[str]) -> List[List[float]]:
        """Encode a list of texts into vectors.

        If a real model is available this returns model embeddings (list of floats).
        Otherwise returns a deterministic pseudovector based on SHA256.
        """
        if self._model is not None:
            # sentence-transformers returns numpy arrays; convert to Python lists
            vectors = self._model.encode(texts, show_progress_bar=False)
            return [v.tolist() if hasattr(v, "tolist") else list(map(float, v)) for v in vectors]

        # Fallback: deterministic hash-based vector of fixed small dimension
        out = []
        dim = 128
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            # expand hash to dim floats by re-hashing
            vec = []
            seed = h
            while len(vec) < dim:
                seed = hashlib.sha256(seed).digest()
                for i in range(0, len(seed), 4):
                    if len(vec) >= dim:
                        break
                    chunk = seed[i : i + 4]
                    val = int.from_bytes(chunk, "big", signed=False)
                    vec.append((val % 1000) / 1000.0)  # normalize to [0,1)
            out.append(vec)
        return out


# module-level default
default_embeddings = Embeddings()

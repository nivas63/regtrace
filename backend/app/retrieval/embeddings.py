"""Local embedding and reranking models (zero API cost).

- FastEmbedEmbedder: BAAI/bge-base-en-v1.5 via ONNX Runtime (CPU friendly, no torch).
- FastEmbedReranker: BAAI/bge-reranker-base cross-encoder.
- HashingEmbedder: deterministic feature-hashing embedder used in tests and as an
  explicit offline fallback. It captures lexical overlap only, and is labelled as
  such wherever it is used.

Model inference is CPU-bound, so it runs in a worker thread to keep the event loop
responsive.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
from collections.abc import Sequence
from typing import Any, Protocol

from app.config import Settings
from app.db.base import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+")
# bge models expect this instruction prefix on queries (not on passages).
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    name: str
    dim: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    name: str

    async def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class HashingEmbedder:
    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall(text.lower())
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        for gram in grams:
            digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir: str, batch_size: int = 32) -> None:
        self.name = model_name
        self.dim = EMBEDDING_DIM
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # heavy import, deferred

                self._model = TextEmbedding(model_name=self.name, cache_dir=self._cache_dir)
        return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        return [[float(x) for x in v] for v in model.embed(texts, batch_size=self._batch_size)]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self._embed, [_BGE_QUERY_PREFIX + text])
        return vectors[0]


class FastEmbedReranker:
    def __init__(self, model_name: str, cache_dir: str) -> None:
        self.name = model_name
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(model_name=self.name, cache_dir=self._cache_dir)
        return self._model

    def _score(self, query: str, passages: list[str]) -> list[float]:
        raw = list(self._load().rerank(query, passages))
        # Cross-encoder logits -> (0, 1) so thresholds are interpretable.
        return [1.0 / (1.0 + math.exp(-float(s))) for s in raw]

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        return await asyncio.to_thread(self._score, query, list(passages))


class LexicalReranker:
    """Fallback when no cross-encoder is available: token-overlap (Jaccard-like) score."""

    name = "lexical-overlap"

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        q = set(_TOKEN.findall(query.lower()))
        scores = []
        for passage in passages:
            p = set(_TOKEN.findall(passage.lower()))
            scores.append(len(q & p) / math.sqrt(max(1, len(q)) * max(1, len(p))))
        return scores


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "hashing":
        return HashingEmbedder()
    return FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir)


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_backend == "none":
        return LexicalReranker()
    return FastEmbedReranker(settings.reranker_model, settings.model_cache_dir)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)

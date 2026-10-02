"""Vector store for policy text.

Qdrant when configured, an in-process store otherwise. Both behind one
interface so the retriever, the ingest path and the tests do not branch on
which backend is live.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict[str, str | int | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic bag-of-words embedding used when no API key is set.

    Not competitive with a real model, but it is honest about what it is: a
    lexical fallback so retrieval runs offline. Retrieval quality with this is
    measured and reported separately in the evaluation harness, never claimed as
    semantic performance.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = tokenize(text)
        for tok in toks:
            # Signed hashing keeps unrelated collisions from all pushing the
            # same direction, which is what makes pure counts drift toward the
            # mean vector on longer documents.
            h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        # Also index adjacent token pairs so multi-word terms ("annual fee")
        # match, not just single tokens.
        for a, b in zip(toks, toks[1:], strict=False):
            h = int(hashlib.blake2b(f"{a}_{b}".encode(), digest_size=8).hexdigest(), 16)
            vec[h % self.dim] += 0.5 if (h >> 63) & 1 else -0.5
        return _normalize(vec)


class OpenAIEmbedder:
    """OpenAI embeddings via plain HTTP, batched.

    Deliberately not the OpenAI SDK: one HTTP dependency we already hold keeps
    the deployment surface smaller.
    """

    def __init__(self, api_key: str, model: str, dim: int = 1536) -> None:
        self._key = api_key
        self._model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        out: list[list[float]] = []
        with httpx.Client(timeout=30.0) as client:
            for i in range(0, len(texts), 64):
                batch = texts[i : i + 64]
                r = client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={"model": self._model, "input": batch},
                )
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                out.extend(d["embedding"] for d in data)
        self.dim = len(out[0])
        return out


def get_embedder() -> Embedder:
    s = get_settings()
    if s.has_remote_embeddings:
        return OpenAIEmbedder(s.openai_api_key, s.openai_embedding_model)
    return HashingEmbedder(dim=s.embed_dim)


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------
class VectorStore(Protocol):
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> int: ...

    def search(
        self,
        vector: list[float],
        limit: int = 5,
        must: dict[str, str] | None = None,
    ) -> list[tuple[Chunk, float]]: ...

    def count(self) -> int: ...


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class InMemoryStore:
    """Exhaustive cosine search. Fine for the four policy docs we ingest."""

    name = "memory"

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, list[float]] = {}

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        for c, v in zip(chunks, vectors, strict=True):
            self._chunks[c.id] = c
            self._vectors[c.id] = v
        return len(chunks)

    def search(
        self, vector: list[float], limit: int = 5, must: dict[str, str] | None = None
    ) -> list[tuple[Chunk, float]]:
        scored: list[tuple[Chunk, float]] = []
        for cid, vec in self._vectors.items():
            chunk = self._chunks[cid]
            if must and any(chunk.metadata.get(k) != v for k, v in must.items()):
                continue
            scored.append((chunk, cosine(vector, vec)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def count(self) -> int:
        return len(self._chunks)


class QdrantStore:
    name = "qdrant"

    def __init__(self, dim: int) -> None:
        from qdrant_client import QdrantClient

        s = get_settings()
        if s.qdrant_url:
            self._client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
        else:
            # Local persistent or in-memory mode, used when only a path is set.
            self._client = QdrantClient(path=s.qdrant_local_path or None)
        self._collection = s.qdrant_collection
        self._ensure(dim)

    def _ensure(self, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection in existing:
            # A dimension change would silently break inserts, so verify it.
            info = self._client.get_collection(self._collection)
            params = info.config.params.vectors
            have = params.size if params and not isinstance(params, dict) else None
            if have is not None and have != dim:
                self._client.delete_collection(self._collection)
                existing.discard(self._collection)
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(id=i, vector=v, payload={"text": c.text, **c.metadata})
            for i, (c, v) in enumerate(zip(chunks, vectors, strict=True))
        ]
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def search(
        self, vector: list[float], limit: int = 5, must: dict[str, str] | None = None
    ) -> list[tuple[Chunk, float]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        flt = None
        if must:
            flt = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v)) for k, v in must.items()
                ]
            )
        hits = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=flt,
            with_payload=True,
        ).points
        out = []
        for h in hits:
            payload = h.payload or {}
            out.append(
                (
                    Chunk(
                        id=str(h.id),
                        text=str(payload.pop("text", "")),
                        metadata=payload,
                    ),
                    float(h.score),
                )
            )
        return out

    def count(self) -> int:
        return int(self._client.count(self._collection).count)


_STORE: VectorStore | None = None


def get_store() -> VectorStore:
    global _STORE
    if _STORE is None:
        s = get_settings()
        _STORE = QdrantStore(s.embed_dim) if s.has_qdrant else InMemoryStore()
    return _STORE


def set_store(store: VectorStore | None) -> None:
    """Tests swap the store; nothing else should call this."""
    global _STORE
    _STORE = store

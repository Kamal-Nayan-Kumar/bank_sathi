"""Vector store for policy text.

Qdrant when configured, an in-process store otherwise. Both behind one
interface so the retriever, the ingest path and the tests do not branch on
which backend is live.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings

log = logging.getLogger(__name__)

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


class FastEmbedEmbedder:
    """all-MiniLM-L6-v2, run locally through Qdrant's fastembed wrapper.

    ~22M parameters, 384 dimensions, and fast enough on CPU that there is no
    reason to pay for a hosted embedding API here. It also means retrieval
    quality does not depend on a third-party key being present in production,
    which is worth more than the marginal quality of a larger hosted model.
    """

    def __init__(self, dim: int = 384) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [_normalize(list(map(float, v))) for v in self._model.embed(texts)]
        if vectors:
            self.dim = len(vectors[0])
        return vectors


class HashingEmbedder:
    """Deterministic bag-of-words embedding, for tests and offline CI.

    Not competitive with a real model, and not claimed to be: it exists so the
    retrieval path, the chunking and the graph can be exercised with no model
    download. `ENV EMBED_BACKEND=hash` forces it even where fastembed exists.
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
    """Pick an embedder, degrading rather than failing.

    Order: fastembed (local MiniLM, no key) -> OpenAI (if a key is set and
    fastembed is unavailable) -> hashing (tests, offline CI). Whichever is
    chosen, its dimension is reported so the Qdrant collection is created to
    match on the first call rather than failing on the first insert.
    """
    s = get_settings()
    if s.embed_backend != "auto":
        if s.embed_backend == "hash":
            return HashingEmbedder(dim=s.embed_dim)
        if s.embed_backend == "openai" and s.has_remote_embeddings:
            return OpenAIEmbedder(s.openai_api_key, s.openai_embedding_model)
        return FastEmbedEmbedder()
    try:
        return FastEmbedEmbedder()
    except Exception as exc:  # noqa: BLE001
        log.info("fastembed unavailable (%s); falling back", exc)
    if s.has_remote_embeddings:
        try:
            return OpenAIEmbedder(s.openai_api_key, s.openai_embedding_model)
        except Exception as exc:  # noqa: BLE001
            log.info("OpenAI embeddings unavailable (%s); falling back", exc)
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

    # Fields the retriever filters on. Both must be keyword-indexed or Qdrant
    # rejects the query.
    _PAYLOAD_INDEXES = ("card_id", "document_type")

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
        self._ensure_payload_indexes()

    def _ensure_payload_indexes(self) -> None:
        """Create the keyword index `card_id` filtering depends on.

        Retrieval filters every card's evidence query by `card_id`. Qdrant
        rejects an unindexed filter at query time with a 400, so without this the
        whole evidence node fails on the first real request. It only appears once
        a remote Qdrant is actually in use, which is why it is created here
        rather than left to a manual setup step.
        """
        from qdrant_client.models import PayloadSchemaType

        for field in self._PAYLOAD_INDEXES:
            try:
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:  # noqa: BLE001
                # Already exists is the normal case on every boot after the first.
                if "already exists" not in str(exc).lower():
                    log.warning("could not index %s on %s: %s", field, self._collection, exc)

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
    """Pick a vector store and size it to the embedder actually in use.

    The dimension comes from the embedder rather than from config, because a
    mismatch between collection size and vector length fails on the first insert
    with an opaque error.
    """
    global _STORE
    if _STORE is None:
        s = get_settings()
        if s.has_qdrant:
            embedder = get_embedder()
            _STORE = QdrantStore(embedder.dim)
        else:
            _STORE = InMemoryStore()
    return _STORE


def set_store(store: VectorStore | None) -> None:
    """Tests swap the store; nothing else should call this."""
    global _STORE
    _STORE = store

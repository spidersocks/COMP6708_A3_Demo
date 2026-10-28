"""Chroma vector store wrapper (persistent on disk)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import chromadb
from chromadb.config import Settings as ChromaSettings

from config import SETTINGS
from rag.chunking import DocumentChunk


class VectorStore:
    def __init__(self) -> None:
        SETTINGS.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(SETTINGS.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=SETTINGS.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def count(self) -> int:
        return int(self._collection.count())

    def reset(self) -> None:
        try:
            self._client.delete_collection(SETTINGS.collection_name)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=SETTINGS.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert_chunks(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        if not chunks:
            return 0
        assert len(chunks) == len(embeddings)
        batch = 64
        n = 0
        for i in range(0, len(chunks), batch):
            sl = slice(i, i + batch)
            ids = [c.id for c in chunks[sl]]
            docs = [c.text for c in chunks[sl]]
            metas = [c.metadata for c in chunks[sl]]
            embs = [list(e) for e in embeddings[sl]]
            # chroma metadata values must be scalar
            clean_metas: List[Dict[str, Any]] = []
            for m in metas:
                clean_metas.append(
                    {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in m.items()}
                )
            self._collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=clean_metas,
                embeddings=embs,
            )
            n += len(ids)
        return n

    def query(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where = {"language": language} if language else None
        kwargs: Dict[str, Any] = {
            "query_embeddings": [list(embedding)],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        try:
            res = self._collection.query(**kwargs)
        except Exception:
            # Retry without language filter if metadata filter fails / empty
            kwargs.pop("where", None)
            res = self._collection.query(**kwargs)

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        out: List[Dict[str, Any]] = []
        for i, doc in enumerate(docs):
            dist = dists[i] if i < len(dists) else None
            score = None if dist is None else max(0.0, 1.0 - float(dist))
            out.append(
                {
                    "id": ids[i] if i < len(ids) else f"hit-{i}",
                    "text": doc or "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "score": score,
                }
            )
        return out


_STORE: Optional[VectorStore] = None


def get_store() -> VectorStore:
    global _STORE
    if _STORE is None:
        _STORE = VectorStore()
    return _STORE

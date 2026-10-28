"""Embedding providers: native DashScope SDK (preferred) or local hash fallback."""
from __future__ import annotations

import hashlib
import math
import struct
from typing import List, Sequence

from config import SETTINGS
from rag.dashscope_native import configure_dashscope

# Local fallback dimensionality (deterministic pseudo-embeddings)
_FALLBACK_DIM = 384


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _hash_embed(text: str, dim: int = _FALLBACK_DIM) -> List[float]:
    """Deterministic bag-of-tokens embedding when DashScope is unavailable."""
    vec = [0.0] * dim
    tokens = (text or "").lower().split()
    if not tokens:
        tokens = ["_empty_"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, min(len(digest), dim // 4 * 4), 4):
            idx = struct.unpack_from(">I", digest, i)[0] % dim
            sign = 1.0 if digest[i] % 2 == 0 else -1.0
            vec[idx] += sign
    return _l2_normalize(vec)


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    if not texts:
        return []
    if SETTINGS.has_dashscope:
        try:
            return _dashscope_embed_native(list(texts))
        except Exception as e:
            print(f"[EMBED] DashScope failed ({e}); using local fallback.", flush=True)
    return [_hash_embed(t) for t in texts]


def embed_query(text: str) -> List[float]:
    return embed_texts([text])[0]


def _dashscope_embed_native(texts: List[str]) -> List[List[float]]:
    """Native DashScope TextEmbedding.call → /api/v1/services/embeddings/..."""
    from dashscope import TextEmbedding

    configure_dashscope()
    out: List[List[float]] = []
    # text-embedding-v3/v4: up to 10 inputs per request
    batch_size = 10
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = TextEmbedding.call(
            model=SETTINGS.embedding_model,
            input=batch,
        )
        status = getattr(resp, "status_code", 200)
        if status != 200:
            raise RuntimeError(
                f"TextEmbedding status={status} "
                f"code={getattr(resp, 'code', '')} message={getattr(resp, 'message', resp)}"
            )
        # SDK returns output with embeddings list
        output = getattr(resp, "output", None) or {}
        if isinstance(output, dict):
            embeddings = output.get("embeddings") or []
        else:
            embeddings = getattr(output, "embeddings", None) or []

        if not embeddings and isinstance(resp, dict):
            embeddings = ((resp.get("output") or {}).get("embeddings")) or []

        # Sort by text_index when present
        def _idx(item):
            if isinstance(item, dict):
                return item.get("text_index", 0)
            return getattr(item, "text_index", 0)

        embeddings = sorted(list(embeddings), key=_idx)
        for item in embeddings:
            emb = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
            if not emb:
                raise RuntimeError("Empty embedding in DashScope TextEmbedding response")
            out.append(list(emb))

        if len(embeddings) != len(batch):
            raise RuntimeError(f"Expected {len(batch)} embeddings, got {len(embeddings)}")

    if len(out) != len(texts):
        raise RuntimeError(f"Expected {len(texts)} embeddings, got {len(out)}")
    return out

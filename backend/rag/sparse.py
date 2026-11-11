"""In-process BM25 sparse index + Reciprocal Rank Fusion with dense hits.

No cloud search service — rebuilt on ingest / cold start from the same KB chunks.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

_WORD_RE = re.compile(r"[a-z0-9]+", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> List[str]:
    """English words + CJK unigrams/bigrams (BrightNest is multilingual)."""
    raw = text or ""
    low = raw.lower()
    toks = _WORD_RE.findall(low)
    cjk = _CJK_RE.findall(raw)
    toks.extend(cjk)
    toks.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return toks


class SparseIndex:
    """BM25 Okapi over chunk texts. Language filter uses metadata.language."""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._ids: List[str] = []
        self._texts: List[str] = []
        self._metas: List[Dict[str, Any]] = []
        self._tok_docs: List[List[str]] = []
        self._doc_len: List[int] = []
        self._avgdl = 0.0
        self._df: Dict[str, int] = {}
        self._N = 0

    @property
    def count(self) -> int:
        return self._N

    def clear(self) -> None:
        self.__init__(k1=self.k1, b=self.b)

    def rebuild(self, rows: Sequence[Dict[str, Any]]) -> int:
        """rows: {id, text, metadata}."""
        self.clear()
        df: Counter[str] = Counter()
        for row in rows:
            text = str(row.get("text") or "")
            toks = tokenize(text)
            if not toks:
                toks = ["_empty_"]
            self._ids.append(str(row.get("id") or f"sparse-{len(self._ids)}"))
            self._texts.append(text)
            self._metas.append(dict(row.get("metadata") or {}))
            self._tok_docs.append(toks)
            self._doc_len.append(len(toks))
            for t in set(toks):
                df[t] += 1
        self._N = len(self._ids)
        self._df = dict(df)
        self._avgdl = (sum(self._doc_len) / self._N) if self._N else 0.0
        return self._N

    def _idf(self, term: str) -> float:
        # BM25+ style idf (smooth)
        n = self._df.get(term, 0)
        return math.log(1.0 + (self._N - n + 0.5) / (n + 0.5))

    def query(
        self,
        question: str,
        *,
        top_k: int = 20,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._N == 0 or top_k <= 0:
            return []
        q_toks = tokenize(question)
        if not q_toks:
            return []
        scores: List[Tuple[float, int]] = []
        lang = (language or "").strip()
        for i in range(self._N):
            if lang:
                meta_lang = str((self._metas[i] or {}).get("language") or "")
                if meta_lang and meta_lang != lang:
                    continue
            tf = Counter(self._tok_docs[i])
            dl = self._doc_len[i] or 1
            score = 0.0
            for t in q_toks:
                if t not in tf:
                    continue
                freq = tf[t]
                denom = freq + self.k1 * (1.0 - self.b + self.b * dl / (self._avgdl or 1.0))
                score += self._idf(t) * (freq * (self.k1 + 1.0)) / (denom or 1.0)
            if score > 0:
                scores.append((score, i))
        scores.sort(key=lambda x: x[0], reverse=True)
        out: List[Dict[str, Any]] = []
        for score, i in scores[:top_k]:
            out.append(
                {
                    "id": self._ids[i],
                    "text": self._texts[i],
                    "metadata": self._metas[i],
                    "score": float(score),
                    "channel": "sparse",
                }
            )
        return out


def _channel_preview(
    hits: Sequence[Dict[str, Any]],
    *,
    channel: str,
    question: str = "",
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """Inspector rows for dense/sparse pools (expandable text + BM25 token matches)."""
    q_toks = tokenize(question) if question else []
    q_set = set(q_toks)
    preview: List[Dict[str, Any]] = []
    for i, h in enumerate(list(hits)[: max(0, limit)]):
        meta = h.get("metadata") or {}
        text = str(h.get("text") or "")
        row: Dict[str, Any] = {
            "rank": i + 1,
            "id": h.get("id"),
            "source": meta.get("source"),
            "title": meta.get("title") or meta.get("canonical"),
            "score": h.get("score"),
            "snippet": text[:280],
            "text": text[:4000],
            "char_count": len(text),
            "channel": channel,
        }
        if channel == "sparse" and q_set:
            doc_toks = set(tokenize(text))
            matched = sorted(q_set & doc_toks, key=lambda t: (-len(t), t))
            row["query_tokens"] = sorted(set(q_toks), key=lambda t: (-len(t), t))
            row["matched_tokens"] = matched
        preview.append(row)
    return preview


def rrf_fuse(
    dense: Sequence[Dict[str, Any]],
    sparse: Sequence[Dict[str, Any]],
    *,
    rrf_k: int = 60,
    limit: int = 20,
    question: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank_i).
    Prefer denser body when merging duplicates; annotate fusion for the inspector.
    """
    k = max(1, int(rrf_k))
    fused_score: Dict[str, float] = {}
    by_id: Dict[str, Dict[str, Any]] = {}
    dense_rank: Dict[str, int] = {}
    sparse_rank: Dict[str, int] = {}

    def _key(h: Dict[str, Any]) -> str:
        meta = h.get("metadata") or {}
        return str(h.get("id") or meta.get("source") or "")

    for rank, h in enumerate(dense, start=1):
        kid = _key(h)
        if not kid:
            continue
        dense_rank[kid] = rank
        fused_score[kid] = fused_score.get(kid, 0.0) + 1.0 / (k + rank)
        row = dict(h)
        row["channel"] = "dense"
        by_id[kid] = row

    for rank, h in enumerate(sparse, start=1):
        kid = _key(h)
        if not kid:
            continue
        sparse_rank[kid] = rank
        fused_score[kid] = fused_score.get(kid, 0.0) + 1.0 / (k + rank)
        if kid in by_id:
            # Keep denser text/meta; mark both channels
            by_id[kid]["channel"] = "both"
            by_id[kid]["sparse_score"] = h.get("score")
        else:
            row = dict(h)
            row["channel"] = "sparse"
            by_id[kid] = row

    ordered = sorted(fused_score.items(), key=lambda kv: kv[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for kid, rrf in ordered[: max(1, limit)]:
        h = dict(by_id[kid])
        channels = h.get("channel") or "dense"
        h["fusion"] = {
            "rrf": round(float(rrf), 6),
            "dense_rank": dense_rank.get(kid),
            "sparse_rank": sparse_rank.get(kid),
            "channels": channels,
            "contrib": {
                "dense": round(1.0 / (k + dense_rank[kid]), 6)
                if kid in dense_rank
                else 0.0,
                "sparse": round(1.0 / (k + sparse_rank[kid]), 6)
                if kid in sparse_rank
                else 0.0,
            },
        }
        # Keep vector score when present; else leave BM25 score
        if channels == "sparse" and "score" in h:
            h["sparse_score"] = h.get("score")
        out.append(h)

    debug = {
        "mode": "hybrid_rrf",
        "rrf_k": k,
        "dense_count": len(dense),
        "sparse_count": len(sparse),
        "fused_count": len(out),
        "dense_only": sum(1 for h in out if (h.get("fusion") or {}).get("channels") == "dense"),
        "sparse_only": sum(1 for h in out if (h.get("fusion") or {}).get("channels") == "sparse"),
        "both": sum(1 for h in out if (h.get("fusion") or {}).get("channels") == "both"),
        "how": [
            "Dense Chroma ranks chunks by embedding similarity.",
            "BM25 ranks the same KB by keyword / CJK token overlap.",
            f"RRF score = 1/({k}+dense_rank) + 1/({k}+BM25_rank); both-lists boost.",
            "Lexical re-rank (age / path / folder / overlap) then keep top_k for the LLM.",
        ],
        "dense_top": _channel_preview(dense, channel="dense", question=question),
        "sparse_top": _channel_preview(sparse, channel="sparse", question=question),
        "fused_preview": [
            {
                "rank": i + 1,
                "source": (h.get("metadata") or {}).get("source"),
                "title": (h.get("metadata") or {}).get("title")
                or (h.get("metadata") or {}).get("canonical"),
                "rrf": (h.get("fusion") or {}).get("rrf"),
                "dense_rank": (h.get("fusion") or {}).get("dense_rank"),
                "sparse_rank": (h.get("fusion") or {}).get("sparse_rank"),
                "channels": (h.get("fusion") or {}).get("channels"),
                "contrib": (h.get("fusion") or {}).get("contrib"),
            }
            for i, h in enumerate(out[:12])
        ],
    }
    return out, debug


_SPARSE: Optional[SparseIndex] = None


def get_sparse_index() -> SparseIndex:
    global _SPARSE
    if _SPARSE is None:
        _SPARSE = SparseIndex()
    return _SPARSE

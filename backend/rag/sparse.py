"""In-process BM25 sparse index + Reciprocal Rank Fusion with dense hits.

No cloud search service — rebuilt on ingest / cold start from the same KB chunks.

Tokenization follows standard Okapi-style IR practice: case-fold, split on
non-alphanumerics, drop stopwords / 1-char Latin noise. Chinese uses character
bigrams (unigram only when the query is a single CJK character) so a term like
「拼」 does not flood every document.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

_WORD_RE = re.compile(r"[a-z0-9]+", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# Classic IR stop list (Lucene / BM25 practice). Kept small and English-only —
# CJK content words are never stopped here.
_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "when",
    "at",
    "by",
    "for",
    "with",
    "about",
    "against",
    "between",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "to",
    "from",
    "up",
    "down",
    "in",
    "out",
    "on",
    "off",
    "over",
    "under",
    "again",
    "further",
    "once",
    "here",
    "there",
    "all",
    "any",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "can",
    "will",
    "just",
    "don",
    "should",
    "now",
    "i",
    "me",
    "my",
    "myself",
    "we",
    "our",
    "ours",
    "ourselves",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "itself",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
    "what",
    "which",
    "who",
    "whom",
    "this",
    "that",
    "these",
    "those",
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "doing",
    "would",
    "could",
    "ought",
    "im",
    "youre",
    "hes",
    "shes",
    "its",
    "were",
    "theyre",
    "ive",
    "youve",
    "weve",
    "theyve",
    "id",
    "youd",
    "hed",
    "shed",
    "wed",
    "theyd",
    "ill",
    "youll",
    "hell",
    "shell",
    "well",
    "theyll",
    "isnt",
    "arent",
    "wasnt",
    "werent",
    "hasnt",
    "havent",
    "hadnt",
    "doesnt",
    "dont",
    "didnt",
    "wont",
    "wouldnt",
    "shouldnt",
    "cant",
    "cannot",
    "couldnt",
    "mustnt",
    "lets",
    "thats",
    "whos",
    "whats",
    "heres",
    "theres",
    "whens",
    "wheres",
    "whys",
    "hows",
    "please",
    "also",
    "as",
    "of",
    "via",
}


def tokenize(text: str) -> List[str]:
    """BM25 tokens: stopped Latin words (≥2 chars) + CJK character bigrams."""
    raw = text or ""
    toks: List[str] = []
    for w in _WORD_RE.findall(raw.lower()):
        if len(w) < 2:
            continue
        if w in _STOPWORDS:
            continue
        toks.append(w)
    cjk = _CJK_RE.findall(raw)
    if len(cjk) == 1:
        # Lone character query (rare) — keep the unigram
        toks.append(cjk[0])
    elif len(cjk) >= 2:
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


def _hit_key(h: Dict[str, Any]) -> str:
    meta = h.get("metadata") or {}
    return str(h.get("id") or meta.get("source") or "")


def channel_pack(
    dense: Sequence[Dict[str, Any]],
    sparse: Sequence[Dict[str, Any]],
    *,
    dense_n: int = 3,
    keep_k: int = 5,
    question: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Dense primary + BM25 supplements (no RRF, no multi-flag lexical sort).

    Keep policy: take dense ranks 1..dense_n, then fill remaining slots from
    BM25 (skip duplicates). If still short, continue down the dense list.
    """
    keep = max(1, int(keep_k))
    take_dense = max(0, min(int(dense_n), keep))
    dense_rank: Dict[str, int] = {}
    sparse_rank: Dict[str, int] = {}
    for rank, h in enumerate(dense, start=1):
        kid = _hit_key(h)
        if kid:
            dense_rank[kid] = rank
    for rank, h in enumerate(sparse, start=1):
        kid = _hit_key(h)
        if kid:
            sparse_rank[kid] = rank

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    dense_kept = 0
    sparse_added = 0
    skipped_dupes = 0

    def _append(h: Dict[str, Any], *, slot: str, kind: str) -> bool:
        nonlocal dense_kept, sparse_added, skipped_dupes
        kid = _hit_key(h)
        if not kid:
            return False
        if kid in seen:
            skipped_dupes += 1
            return False
        seen.add(kid)
        in_dense = kid in dense_rank
        in_sparse = kid in sparse_rank
        channels = (
            "both"
            if in_dense and in_sparse
            else ("sparse" if in_sparse and not in_dense else "dense")
        )
        row = dict(h)
        row["channel"] = channels
        row["score_kind"] = kind
        bits = [slot.replace("_", " ")]
        if in_dense:
            bits.append(f"dense #{dense_rank[kid]}")
        if in_sparse:
            bits.append(f"BM25 #{sparse_rank[kid]}")
        row["pack"] = {
            "slot": slot,
            "dense_rank": dense_rank.get(kid),
            "sparse_rank": sparse_rank.get(kid),
            "in_both_pools": bool(in_dense and in_sparse),
        }
        # Keep fusion-shaped fields so the inspector channel labels still work.
        row["fusion"] = {
            "channels": channels,
            "dense_rank": dense_rank.get(kid),
            "sparse_rank": sparse_rank.get(kid),
        }
        row["rerank"] = {
            "why": " · ".join(bits),
            "score_kind": kind,
            "score": h.get("score"),
        }
        out.append(row)
        if slot == "dense":
            dense_kept += 1
        else:
            sparse_added += 1
        return True

    for h in list(dense)[:take_dense]:
        if len(out) >= keep:
            break
        _append(h, slot="dense", kind="dense")

    if len(out) < keep:
        for h in sparse:
            if len(out) >= keep:
                break
            _append(h, slot="sparse_supplement", kind="BM25")

    if len(out) < keep:
        for h in list(dense)[take_dense:]:
            if len(out) >= keep:
                break
            _append(h, slot="dense_fill", kind="dense")

    debug = {
        "mode": "channel_pack",
        "dense_n": take_dense,
        "keep_k": keep,
        "dense_count": len(dense),
        "sparse_count": len(sparse),
        "packed_count": len(out),
        "dense_kept": dense_kept,
        "sparse_added": sparse_added,
        "skipped_dupes": skipped_dupes,
        "dense_only": sum(
            1 for h in out if (h.get("fusion") or {}).get("channels") == "dense"
        ),
        "sparse_only": sum(
            1 for h in out if (h.get("fusion") or {}).get("channels") == "sparse"
        ),
        "both": sum(
            1 for h in out if (h.get("fusion") or {}).get("channels") == "both"
        ),
        "how": [
            "Dense Chroma ranks chunks by embedding similarity (paraphrase).",
            "BM25 ranks the same KB by keyword / CJK bigram overlap (exact terms).",
            f"Pack keep_k={keep}: take dense ranks 1–{take_dense}, then fill from BM25 (skip duplicates).",
            "Age-band files may still be injected when the parent names an age.",
        ],
        "dense_top": _channel_preview(dense, channel="dense", question=question),
        "sparse_top": _channel_preview(sparse, channel="sparse", question=question),
        "packed_preview": [
            {
                "rank": i + 1,
                "source": (h.get("metadata") or {}).get("source"),
                "title": (h.get("metadata") or {}).get("title")
                or (h.get("metadata") or {}).get("canonical"),
                "slot": (h.get("pack") or {}).get("slot"),
                "dense_rank": (h.get("fusion") or {}).get("dense_rank"),
                "sparse_rank": (h.get("fusion") or {}).get("sparse_rank"),
                "channels": (h.get("fusion") or {}).get("channels"),
                "why": (h.get("rerank") or {}).get("why"),
            }
            for i, h in enumerate(out)
        ],
        # Alias for older inspector code that looked for fused_preview
        "fused_preview": [
            {
                "rank": i + 1,
                "source": (h.get("metadata") or {}).get("source"),
                "title": (h.get("metadata") or {}).get("title")
                or (h.get("metadata") or {}).get("canonical"),
                "slot": (h.get("pack") or {}).get("slot"),
                "dense_rank": (h.get("fusion") or {}).get("dense_rank"),
                "sparse_rank": (h.get("fusion") or {}).get("sparse_rank"),
                "channels": (h.get("fusion") or {}).get("channels"),
            }
            for i, h in enumerate(out)
        ],
    }
    return out, debug


_SPARSE: Optional[SparseIndex] = None


def get_sparse_index() -> SparseIndex:
    global _SPARSE
    if _SPARSE is None:
        _SPARSE = SparseIndex()
    return _SPARSE

"""Tiny prototype intent classifier (lexical + optional embedding).

Hard safety/weather/escalation vetoes stay in intent_router; this module
supplies soft scores that blend with regex rules.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

_FACTS = Path(__file__).resolve().parent.parent / "facts" / "intent_prototypes.json"
_TOKEN_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]{2,}")


@lru_cache(maxsize=1)
def _prototypes() -> Dict[str, List[str]]:
    if not _FACTS.is_file():
        return {}
    return json.loads(_FACTS.read_text(encoding="utf-8"))


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cosine(u: List[float], v: List[float]) -> float:
    if not u or not v or len(u) != len(v):
        return 0.0
    return sum(a * b for a, b in zip(u, v))


@lru_cache(maxsize=1)
def _proto_embeddings() -> Dict[str, List[List[float]]]:
    """Cache prototype embeddings (local hash or DashScope)."""
    try:
        from rag.embeddings import embed_texts
    except Exception:
        return {}
    protos = _prototypes()
    out: Dict[str, List[List[float]]] = {}
    flat: List[str] = []
    index: List[Tuple[str, int]] = []
    for route, examples in protos.items():
        for i, ex in enumerate(examples):
            flat.append(ex)
            index.append((route, i))
    if not flat:
        return {}
    try:
        vecs = embed_texts(flat)
    except Exception:
        return {}
    for (route, _), vec in zip(index, vecs):
        out.setdefault(route, []).append(vec)
    return out


def score_prototypes(message: str) -> Dict[str, float]:
    """Return route → [0,1] similarity to best prototype."""
    msg = (message or "").strip()
    if not msg:
        return {}
    scores: Dict[str, float] = {}
    protos = _prototypes()
    emb_cache = _proto_embeddings()
    q_emb = None
    if emb_cache:
        try:
            from rag.embeddings import embed_query

            q_emb = embed_query(msg)
        except Exception:
            q_emb = None

    for route, examples in protos.items():
        best_lex = max((_jaccard(msg, ex) for ex in examples), default=0.0)
        best_emb = 0.0
        if q_emb and route in emb_cache:
            for vec in emb_cache[route]:
                best_emb = max(best_emb, _cosine(q_emb, vec))
            # hash embeddings are soft; scale
            best_emb = max(0.0, (best_emb - 0.15) / 0.85) if best_emb else 0.0
        scores[route] = max(best_lex, 0.65 * best_emb + 0.35 * best_lex)
    return scores


def blend_scores(
    rule: Dict[str, float],
    proto: Dict[str, float],
    *,
    rule_w: float = 0.55,
    proto_w: float = 0.45,
) -> Dict[str, float]:
    keys = set(rule) | set(proto)
    out: Dict[str, float] = {}
    for k in keys:
        r = float(rule.get(k) or 0.0)
        p = float(proto.get(k) or 0.0)
        blended = rule_w * r + proto_w * p
        if r >= 0.5 and p >= 0.25:
            blended = min(1.0, blended + 0.12)
        out[k] = blended
    return out

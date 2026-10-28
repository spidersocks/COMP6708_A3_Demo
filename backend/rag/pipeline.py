"""Retrieve-then-generate RAG pipeline."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import SETTINGS
from rag.embeddings import embed_query
from rag.generator import generate_answer_meta
from rag.ingest import ingest
from rag.store import get_store

# Cosine / distance scores from Chroma vary; treat very weak tops as gaps.
# Local-hash embeddings often score ~0.25–0.40 even on off-topic FAQ hits.
_WEAK_SCORE = 0.28
_WEAK_SCORE_LOCAL = 0.42
_STOP = {
    "a",
    "an",
    "the",
    "do",
    "you",
    "offer",
    "for",
    "to",
    "is",
    "are",
    "of",
    "and",
    "or",
    "in",
    "on",
    "at",
    "we",
    "our",
    "any",
    "can",
    "i",
    "my",
    "me",
    "what",
    "how",
    "when",
    "where",
    "does",
    "did",
    "have",
    "has",
    "with",
    "about",
    "please",
    "need",
    "now",
    "lesson",
    "lessons",
    "class",
    "classes",
    "course",
    "courses",
    "session",
    "sessions",
    "child",
    "children",
    "kids",
    "programme",
    "program",
    "谢谢",
    "請問",
    "请问",
}
_SOFT_GAP_RE = re.compile(
    r"("
    r"staff will (check|follow|confirm|get back)|"
    r"don'?t have (that|this) (info|information|on file)|"
    r"not (sure|in our (records|faqs?|knowledge))|"
    r"check with (the )?teacher|"
    r"無法|未能|冇呢方面|没有这方面|需要职员|需要職員"
    r")",
    re.I,
)
_GREETING_RE = re.compile(
    r"^[\s]*(?:"
    r"hi|hii+|hello|hey|howdy|yo|hiya|"
    r"good\s*(?:morning|afternoon|evening|day)|"
    r"greetings|morning|afternoon|evening|"
    r"你好|您好|嗨|哈囉|早上好|下午好|晚上好"
    r")[\s!?.！？。]*$",
    re.I,
)


def _content_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[A-Za-z\u4e00-\u9fff]{3,}", (text or "").lower())
        if t not in _STOP
    }


def _off_topic(question: str, hit_text: str) -> bool:
    """True when the parent question shares almost no content words with the top hit."""
    q = _content_tokens(question)
    if len(q) < 2:
        return False
    h = _content_tokens(hit_text)
    if not h:
        return True
    overlap = q & h
    # Require at least one distinctive overlap; scuba/diving vs adult classes → 0
    return len(overlap) == 0


def is_greeting(text: str) -> bool:
    t = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", (text or "").strip())
    if _GREETING_RE.match(t):
        return True
    # Bare CJK greetings (2-char) sometimes miss the main regex under odd encodings
    return bool(re.fullmatch(r"(?:你好|您好|嗨|哈囉|哈罗)+[!?.！？。\s]*", t))


def _is_greeting_hit(hit: Optional[Dict[str, Any]]) -> bool:
    if not hit:
        return False
    meta = hit.get("metadata") or {}
    blob = " ".join(
        str(x or "")
        for x in (
            meta.get("canonical"),
            meta.get("title"),
            meta.get("source"),
            hit.get("text"),
        )
    ).lower()
    return "greeting" in blob or "welcome" in blob


def ensure_index() -> None:
    store = get_store()
    if store.count == 0:
        print("[RAG] Empty index — running ingest…", flush=True)
        ingest(reset=True)


def retrieve(
    question: str,
    *,
    lang: Optional[str] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    ensure_index()
    emb = embed_query(question)
    return get_store().query(
        emb,
        top_k=top_k or SETTINGS.rag_top_k,
        language=lang,
    )


def _lexical_rerank(question: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prefer chunks that share distinctive words with the question (helps local-hash RAG)."""
    q = _content_tokens(question)
    if not q or not hits:
        return hits

    def key(h: Dict[str, Any]) -> tuple:
        meta = h.get("metadata") or {}
        blob = " ".join(
            str(meta.get(k) or "")
            for k in ("title", "source", "canonical", "aliases")
        )
        blob = f"{blob} {h.get('text') or ''}"
        overlap = len(q & _content_tokens(blob))
        try:
            score = float(h.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        return (overlap, score)

    return sorted(hits, key=key, reverse=True)


def chat_with_rag(
    message: str,
    *,
    language: Optional[str] = None,
    debug: bool = False,
    history_context: Optional[str] = None,
) -> Dict[str, Any]:
    lang = language or "en"
    lang_fallback = False
    hits = retrieve(message, lang=lang)
    # If language filter returned nothing, retry without filter
    if not hits and lang:
        hits = retrieve(message, lang=None)
        lang_fallback = True
    hits = _lexical_rerank(message, hits)

    gen = generate_answer_meta(
        message,
        hits,
        lang=lang,
        history_context=history_context,
    )
    answer = gen.get("answer") or ""
    citations = []
    for h in hits:
        meta = h.get("metadata") or {}
        citations.append(
            {
                "source": meta.get("source"),
                "title": meta.get("title"),
                "canonical": meta.get("canonical"),
                "score": h.get("score"),
                "snippet": (h.get("text") or "")[:280],
            }
        )

    top_score = None
    if hits:
        try:
            top_score = float(hits[0].get("score"))
        except (TypeError, ValueError):
            top_score = None

    gap_reason = None
    provider = gen.get("provider") or ""
    top_hit = hits[0] if hits else None
    top_hit_text = (top_hit.get("text") if top_hit else "") or ""
    weak_cut = _WEAK_SCORE_LOCAL if provider == "local_fallback" else _WEAK_SCORE
    greeting_ok = is_greeting(message) and _is_greeting_hit(top_hit)

    if greeting_ok:
        # "Hello" matched the greeting FAQ — never treat as an admin gap
        gap_reason = None
    elif not hits:
        gap_reason = "no_retrieval"
    elif hits and _off_topic(message, top_hit_text):
        # Prefer off-topic over raw score — local embeddings often "match" unrelated FAQs
        gap_reason = "off_topic_retrieval"
    elif top_score is not None and top_score < weak_cut and provider != "local_fallback":
        # Local-hash scores are noisy; don't spam admin when the hit is on-topic
        gap_reason = "weak_retrieval"
    elif gen.get("had_no_answer_token"):
        gap_reason = "model_no_answer"
    elif gen.get("admin_gap"):
        gap_reason = "model_admin_gap"
    elif gen.get("used_fallback") and (
        not hits or (top_score is not None and top_score < 0.45)
    ):
        gap_reason = "generic_fallback"

    # Soft keyword cues — only on short replies (FAQ dumps often contain
    # “check with the teacher” example lines and must not spam admin to-dos)
    if (
        _SOFT_GAP_RE.search(answer or "")
        and not gap_reason
        and not greeting_ok
        and len(answer or "") < 360
    ):
        gap_reason = "soft_hand_off"

    out: Dict[str, Any] = {
        "answer": answer,
        "citations": citations,
        "provider": gen.get("provider")
        or ("dashscope" if SETTINGS.has_dashscope else "local_fallback"),
    }
    # Always attach RAG trace for the COMP6708 technical dashboard
    out["debug"] = {
        "hit_count": len(hits),
        "language": lang,
        "language_fallback": lang_fallback,
        "index_size": get_store().count,
        "top_k": SETTINGS.rag_top_k,
        "history_chars": len(history_context or ""),
        "embedding_mode": "dashscope" if SETTINGS.has_dashscope else "local_hash",
        "hits": citations,
        "top_score": top_score,
        "knowledge_gap": bool(gap_reason),
        "gap_reason": gap_reason,
        "admin_gap_note": gen.get("admin_gap"),
    }
    return out

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
    # Geographic / calendar words that falsely "ground" off-topic asks in hours FAQs
    "hong",
    "kong",
    "central",
    "saturday",
    "sunday",
    "monday",
    "friday",
    "public",
    "holiday",
    "holidays",
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
    # Latin words ≥3; CJK runs ≥2 (港鐵, 候補, etc.)
    toks = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", (text or "").lower())
    return {t for t in toks if t not in _STOP}


def _hit_blob(hit: Dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return " ".join(
        str(meta.get(k) or "")
        for k in ("title", "source", "canonical", "aliases", "folder")
    ) + " " + (hit.get("text") or "")


def _overlap_count(question: str, hit_text: str) -> int:
    q = _content_tokens(question)
    h = _content_tokens(hit_text)
    if not q or not h:
        return 0
    return len(q & h)


def _off_topic(question: str, hit: Dict[str, Any] | str) -> bool:
    """True when the parent question is not grounded in the hit (need ≥2 token overlap)."""
    blob = _hit_blob(hit) if isinstance(hit, dict) else (hit or "")
    q = _content_tokens(question)
    if len(q) < 2:
        return _overlap_count(question, blob) < 1
    return _overlap_count(question, blob) < 2


_FOLDER_PRIORS = [
    (re.compile(r"\b(teachers?|tutors?|qualifications?)\b|老師|老师|教师", re.I), "institution"),
    (re.compile(r"\b(pick[\s-]?up|drop[\s-]?off)\b|接送", re.I), "faq"),
    (re.compile(r"\b(wait(?:ing)?\s*list|waitlist|class\s+full)\b|候補|候补|滿班", re.I), "faq"),
    (re.compile(r"\b(sibling|family\s+discount)\b|兄弟姊妹|兄弟姐妹", re.I), "faq"),
    (re.compile(r"\b(make[\s-]?up|absence|reschedul)\b|補課|补课", re.I), "policies"),
    (re.compile(r"\b(address|location|mtr|directions?)\b|地址|港鐵|喺邊", re.I), "institution"),
    (re.compile(r"\b(allerg|peanut|nut)\b|過敏|过敏", re.I), "faq"),
    (re.compile(r"\b(fee|tuition|price|cost|discount)\b|學費", re.I), "faq"),
    (re.compile(r"\b(bilingual|classroom\s+language)\b|雙語|双语", re.I), "faq"),
    (re.compile(r"\b(christmas|cny|holiday\s+hours|term\s+break)\b|聖誕|平安夜|農曆", re.I), "faq"),
    (re.compile(r"\b(pets?|dogs?|cats?)\b|寵物|宠物", re.I), "policies"),
]


def _preferred_folder(question: str) -> Optional[str]:
    for pat, folder in _FOLDER_PRIORS:
        if pat.search(question or ""):
            return folder
    return None


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
    """Prefer path/alias keyword hits, folder prior + token overlap, then score."""
    q = _content_tokens(question)
    if not hits:
        return hits
    prefer = _preferred_folder(question)
    q_low = (question or "").lower()

    def key(h: Dict[str, Any]) -> tuple:
        meta = h.get("metadata") or {}
        source = str(meta.get("source") or "").replace("\\", "/").lower()
        folder = str(meta.get("folder") or "")
        blob = _hit_blob(h)
        blob_low = blob.lower()
        overlap = len(q & _content_tokens(blob)) if q else 0
        # Strong boost when filename/aliases echo query keywords
        path_hit = 0
        for stem in (
            "sibling",
            "waiting",
            "waitlist",
            "teachers",
            "pickup",
            "dropoff",
            "allergy",
            "fees",
            "bilingual",
            "term_break",
            "pets",
            "trial",
        ):
            if stem in q_low.replace(" ", "") or stem in q_low:
                if stem in source or stem in blob_low:
                    path_hit = 1
                    break
        folder_hit = 0
        if prefer and (prefer in source or prefer == folder):
            folder_hit = 1
        try:
            score = float(h.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        return (path_hit, folder_hit, overlap, score)

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

    top_hit = hits[0] if hits else None
    off_topic = bool(hits and top_hit is not None and _off_topic(message, top_hit))

    # Citation gate: do not generate from clearly off-topic chunks (esp. extractive dumps)
    gen_hits = [] if off_topic else hits
    gen = generate_answer_meta(
        message,
        gen_hits,
        lang=lang,
        history_context=history_context,
    )
    answer = gen.get("answer") or ""
    citations = []
    for h in (hits if not off_topic else []):
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
    weak_cut = _WEAK_SCORE_LOCAL if provider == "local_fallback" else _WEAK_SCORE
    greeting_ok = is_greeting(message) and _is_greeting_hit(top_hit)

    if greeting_ok:
        gap_reason = None
    elif not hits:
        gap_reason = "no_retrieval"
    elif off_topic:
        gap_reason = "off_topic_retrieval"
        answer = (
            "I don't have a solid answer for that in our centre materials yet. "
            "If you'd like, ask to speak with our team and somebody will reach out "
            "during working hours."
        )
        provider = "citation_gate"
    elif top_score is not None and top_score < weak_cut and provider != "local_fallback":
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
        "provider": provider
        or gen.get("provider")
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

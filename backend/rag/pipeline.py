"""Retrieve-then-generate RAG pipeline."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import SETTINGS
from rag.chunking import parse_markdown
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
    # Latin words ≥3; age digits; CJK runs ≥2 (港鐵, 候補, etc.)
    # Digits matter for “5 year old” ↔ age_4-5 / age_5-6 FAQ grounding.
    toks = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}|\d{1,2}", (text or "").lower())
    out: set[str] = set()
    for t in toks:
        if t in _STOP:
            continue
        if t in ("years", "year", "yrs", "yr"):
            out.add("year")
            continue
        if t in ("olds", "old"):
            out.add("old")
            continue
        out.add(t)
    return out


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
    (
        re.compile(
            r"\b\d{1,2}\s*(?:-\s*\d{1,2}\s*)?(?:year|yr)s?\s*old|"
            r"\b(?:for|age[d]?)\s+\d{1,2}\b|"
            r"\b(?:K[1-3]|PN|P[1-6])\b|歲|岁",
            re.I,
        ),
        "faq",
    ),
]
_AGE_QUERY_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-\s*\d{1,2}\s*)?(?:year|yr)s?\s*old|"
    r"\b(?:for|age[d]?)\s+(\d{1,2})\b|"
    r"(\d{1,2})\s*(?:year|yr)s?\b",
    re.I,
)


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


def _query_age_digits(question: str) -> set[str]:
    digits: set[str] = set()
    for m in _AGE_QUERY_RE.finditer(question or ""):
        digits.update(g for g in m.groups() if g)
    return digits


def _lexical_rerank(question: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prefer path/alias keyword hits, folder prior + token overlap, then score."""
    q = _content_tokens(question)
    if not hits:
        return hits
    prefer = _preferred_folder(question)
    q_low = (question or "").lower()
    age_digits = _query_age_digits(question)

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
            "phonics",
            "pinyin",
            "clevercal",
        ):
            if stem in q_low.replace(" ", "") or stem in q_low:
                if stem in source or stem in blob_low:
                    path_hit = 1
                    break
        age_hit = 0
        # Match age digits against the filename only — body text often mentions
        # broader ranges like "2–5 yrs" and would falsely promote age_3-4 for "5".
        if age_digits and ("/age/" in source or "age_" in source):
            src_digits = set(re.findall(r"\d{1,2}", source))
            if age_digits & src_digits:
                age_hit = 1
        folder_hit = 0
        if prefer and (prefer in source or prefer == folder):
            folder_hit = 1
        try:
            score = float(h.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        return (age_hit, path_hit, folder_hit, overlap, score)

    return sorted(hits, key=key, reverse=True)


def _age_path_digits(stem: str) -> set[str]:
    """Filename digits for age_4-5_k2 / age_5-6_k3 (ignore month-only stems)."""
    m = re.search(r"age_(\d+)-(\d+)", stem or "", re.I)
    if m:
        return {m.group(1), m.group(2)}
    m = re.search(r"age_(\d+)m-(\d+)", stem or "", re.I)
    if m:
        # e.g. age_28m-3y → treat as ages around 2–3
        return {m.group(2)}
    return set()


def _age_faq_overlay(question: str, lang: str) -> List[Dict[str, Any]]:
    """
    Local-hash embeddings often miss the right age band FAQ entirely.
    When the parent names an age, inject matching faq/age/*.md as grounded hits.
    """
    digits = _query_age_digits(question)
    if not digits:
        return []
    lang_key = (lang or "en").split("-")[0]
    candidates = [
        SETTINGS.content_dir / lang / "faq" / "age",
        SETTINGS.content_dir / lang_key / "faq" / "age",
        SETTINGS.content_dir / "en" / "faq" / "age",
    ]
    age_dir = next((p for p in candidates if p.exists()), None)
    if not age_dir:
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(age_dir.glob("age_*.md")):
        file_digits = _age_path_digits(path.stem)
        if not (digits & file_digits):
            continue
        meta, body = parse_markdown(path)
        if not body:
            continue
        try:
            rel = str(path.relative_to(SETTINGS.content_dir)).replace("\\", "/")
        except ValueError:
            rel = path.name
        title = None
        for line in body.splitlines():
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                break
        out.append(
            {
                "id": f"age-overlay::{rel}",
                "text": body[:1200],
                "metadata": {
                    "source": rel,
                    "title": title or meta.get("canonical") or path.stem,
                    "canonical": meta.get("canonical"),
                    "folder": meta.get("folder") or "faq",
                    "aliases": meta.get("aliases") or "",
                    "language": meta.get("language") or lang,
                },
                # Strong enough to clear local weak-score / citation theatre
                "score": 0.62,
            }
        )
    return out


def _merge_hits(
    primary: List[Dict[str, Any]], extra: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    merged: List[Dict[str, Any]] = []
    for h in list(extra) + list(primary):
        meta = h.get("metadata") or {}
        key = str(meta.get("source") or h.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(h)
    return merged


def chat_with_rag(
    message: str,
    *,
    language: Optional[str] = None,
    debug: bool = False,
    history_context: Optional[str] = None,
) -> Dict[str, Any]:
    lang = language or "en"
    lang_fallback = False
    age_ask = bool(_query_age_digits(message))
    # Age/course questions need a wider vector pool — correct band often ranks ~15–25
    pool_k = max(SETTINGS.rag_top_k, 20) if age_ask else SETTINGS.rag_top_k
    hits = retrieve(message, lang=lang, top_k=pool_k)
    # If language filter returned nothing, retry without filter
    if not hits and lang:
        hits = retrieve(message, lang=None, top_k=pool_k)
        lang_fallback = True
    hits = _merge_hits(hits, _age_faq_overlay(message, lang))
    hits = _lexical_rerank(message, hits)[: SETTINGS.rag_top_k]

    top_hit = hits[0] if hits else None
    # Demo bias: weak lexical overlap is an admin signal, not a parent soft-refusal.
    # Awkward “I don’t have a solid answer” looks worse than a grounded LLM try.
    off_topic = bool(hits and top_hit is not None and _off_topic(message, top_hit))

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
    weak_cut = _WEAK_SCORE_LOCAL if provider == "local_fallback" else _WEAK_SCORE
    greeting_ok = is_greeting(message) and _is_greeting_hit(top_hit)

    if greeting_ok:
        gap_reason = None
    elif not hits:
        gap_reason = "no_retrieval"
    elif off_topic:
        # Keep the model answer for the parent; flag quietly for the demo inspector
        gap_reason = "off_topic_retrieval"
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

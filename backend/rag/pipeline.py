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
# Above this, trust Chroma even if lexical overlap looks thin (stopwords ate "lessons")
_GROUNDED_RETRIEVAL_SCORE = 0.42
_PROGRAMME_CUES = {
    "phonic",
    "phonics",
    "mandarin",
    "putonghua",
    "writing",
    "writ",
    "math",
    "maths",
    "numeracy",
    "playgroup",
    "trial",
    "assessment",
    "tuition",
    "fee",
    "fees",
    "teacher",
    "teachers",
    "booking",
    "book",
    "cancel",
    "hours",
    "holiday",
    "typhoon",
    "allergy",
    "pickup",
    "brightnest",
}
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
        # "Where are you located?" must ground against Location.md
        if t.startswith("locat"):
            out.add("location")
            continue
        if t in ("address", "directions", "mtr"):
            out.add(t)
            out.add("location")
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
    """True when the parent question is not grounded in the hit.

    One strong cue (programme name / long token) is enough — stopwords include
    lesson/class, so \"phonics lessons\" must not look ungrounded against a
    Phonics chunk.
    """
    blob = _hit_blob(hit) if isinstance(hit, dict) else (hit or "")
    q = _content_tokens(question)
    h = _content_tokens(blob)
    if not q:
        return True
    shared = q & h
    if len(shared) >= 2:
        return False
    if len(shared) == 1:
        tok = next(iter(shared))
        if len(tok) >= 5 or tok in _PROGRAMME_CUES or tok.startswith("phonic"):
            return False
    if len(q) < 2:
        return len(shared) < 1
    return True


_FOLDER_PRIORS = [
    (re.compile(r"\b(teachers?|tutors?|qualifications?)\b|老師|老师|教师", re.I), "centre"),
    (re.compile(r"\b(pick[\s-]?up|drop[\s-]?off)\b|接送", re.I), "attendance"),
    (re.compile(r"\b(wait(?:ing)?\s*list|waitlist|class\s+full)\b|候補|候补|滿班", re.I), "admissions"),
    (re.compile(r"\b(sibling|family\s+discount)\b|兄弟姊妹|兄弟姐妹", re.I), "fees"),
    (re.compile(r"\b(make[\s-]?up|absence|reschedul)\b|補課|补课", re.I), "attendance"),
    (re.compile(r"\b(address|location|mtr|directions?)\b|地址|港鐵|喺邊", re.I), "centre"),
    (re.compile(r"\b(allerg|peanut|nut)\b|過敏|过敏", re.I), "policies"),
    (re.compile(r"\b(fee|tuition|price|cost|discount)\b|學費", re.I), "fees"),
    (re.compile(r"\b(bilingual|classroom\s+language)\b|雙語|双语", re.I), "classroom"),
    (re.compile(r"\b(christmas|cny|holiday\s+hours|term\s+break)\b|聖誕|平安夜|農曆", re.I), "centre"),
    (re.compile(r"\b(pets?|dogs?|cats?)\b|寵物|宠物", re.I), "policies"),
    (re.compile(r"\b(trial|assessment|enrol|enroll|placement)\b|試堂|評估|报名|報名", re.I), "admissions"),
    (
        re.compile(
            r"\b(phonics|mandarin|pinyin|playgroup|writing|math|clevercal|alludio|"
            r"smart\s*spell|sound\s*explorers?)\b|拼音|拼音班|語音|數學|游戏",
            re.I,
        ),
        "programmes",
    ),
    (
        re.compile(
            r"\b\d{1,2}\s*(?:-\s*\d{1,2}\s*)?(?:year|yr)s?\s*old|"
            r"\b(?:for|age[d]?)\s+\d{1,2}\b|"
            r"\b(?:K[1-3]|PN|P[1-6])\b|歲|岁",
            re.I,
        ),
        "age_bands",
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
    """
    Prefer path/alias keyword hits, folder prior + token overlap, then vector score.

    Order is NOT pure similarity: age/path/folder/overlap beat score when they fire.
    Each hit gets a `rerank` annotation for the inspector.
    """
    q = _content_tokens(question)
    if not hits:
        return hits
    prefer = _preferred_folder(question)
    q_low = (question or "").lower()
    age_digits = _query_age_digits(question)

    annotated: List[Dict[str, Any]] = []
    for h in hits:
        meta = h.get("metadata") or {}
        source = str(meta.get("source") or "").replace("\\", "/").lower()
        folder = str(meta.get("folder") or "")
        blob = _hit_blob(h)
        blob_low = blob.lower()
        overlap = len(q & _content_tokens(blob)) if q else 0
        path_hit = 0
        path_stem = ""
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
                    path_stem = stem
                    break
        age_hit = 0
        if age_digits and ("/age/" in source or "age_" in source or "/age_bands/" in source):
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
        why_bits = []
        if age_hit:
            why_bits.append("age filename match")
        if path_hit:
            why_bits.append(f"keyword “{path_stem}”")
        if folder_hit:
            why_bits.append(f"folder “{prefer}”")
        if overlap:
            why_bits.append(f"{overlap} token overlap")
        why_bits.append(f"vector {score:.3f}")
        row = dict(h)
        row["rerank"] = {
            "age_hit": age_hit,
            "path_hit": path_hit,
            "folder_hit": folder_hit,
            "overlap": overlap,
            "score": score,
            "why": " · ".join(why_bits),
        }
        annotated.append(row)

    def key(h: Dict[str, Any]) -> tuple:
        r = h.get("rerank") or {}
        return (
            int(r.get("age_hit") or 0),
            int(r.get("path_hit") or 0),
            int(r.get("folder_hit") or 0),
            int(r.get("overlap") or 0),
            float(r.get("score") or 0),
        )

    return sorted(annotated, key=key, reverse=True)


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
    Local-hash embeddings often miss the right age band doc entirely.
    When the parent names an age, inject matching age_bands/*.md as grounded hits.
    """
    digits = _query_age_digits(question)
    if not digits:
        return []
    lang_key = (lang or "en").split("-")[0]
    candidates = [
        SETTINGS.content_dir / lang / "age_bands",
        SETTINGS.content_dir / lang_key / "age_bands",
        SETTINGS.content_dir / "en" / "age_bands",
        # legacy paths (pre-reorg)
        SETTINGS.content_dir / lang / "faq" / "age",
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
                    "folder": meta.get("folder") or "age_bands",
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


def _citation_from_hit(rank: int, h: Dict[str, Any]) -> Dict[str, Any]:
    meta = h.get("metadata") or {}
    full_text = h.get("text") or ""
    return {
        "rank": rank,
        "source": meta.get("source"),
        "title": meta.get("title"),
        "canonical": meta.get("canonical"),
        "score": h.get("score"),
        "snippet": full_text[:280],
        "text": full_text,
        "char_count": len(full_text),
        "rerank": h.get("rerank"),
    }


def _score_stats(hits: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    scores: List[float] = []
    for h in hits:
        try:
            scores.append(float(h.get("score")))
        except (TypeError, ValueError):
            continue
    if not scores:
        return {"rank1_score": None, "max_score": None}
    return {"rank1_score": scores[0], "max_score": max(scores)}


def retrieve_for_enrich(
    message: str,
    *,
    language: Optional[str] = None,
    top_k: Optional[int] = None,
    history_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lightweight retrieve for grounded-compose (structured fact + KB excerpts).
    Does not generate — caller composes with the privileged fact card.
    """
    lang = language or "en"
    k = int(top_k or SETTINGS.grounding_enrich_top_k or SETTINGS.rag_top_k)
    rewrite = _maybe_rewrite_query(message, history_context, lang=lang)
    search_q = rewrite.get("query") or message
    lang_fallback = False
    pool_k = max(k, 8)
    hits = retrieve(search_q, lang=lang, top_k=pool_k)
    if not hits and lang:
        hits = retrieve(search_q, lang=None, top_k=pool_k)
        lang_fallback = True
    pool_before = len(hits)
    hits = _merge_hits(hits, _age_faq_overlay(search_q, lang))
    hits = _lexical_rerank(search_q, hits)[:k]
    citations = [_citation_from_hit(rank, h) for rank, h in enumerate(hits, start=1)]
    stats = _score_stats(hits)
    return {
        "hits": hits,
        "citations": citations,
        "debug": {
            "mode": "grounded_enrich",
            "hit_count": len(hits),
            "language": lang,
            "language_fallback": lang_fallback,
            "index_size": get_store().count,
            "top_k": k,
            "pool_k": pool_k,
            "pool_returned": pool_before,
            "order_rule": (
                "lexical then vector score — age/path/folder/overlap beat raw similarity"
            ),
            "retrieval_query": search_q,
            "query_rewrite": rewrite,
            "embedding_mode": "dashscope" if SETTINGS.has_dashscope else "local_hash",
            "hits": citations,
            "top_score": stats["rank1_score"],
            "rank1_score": stats["rank1_score"],
            "max_score": stats["max_score"],
            "knowledge_gap": False,
            "gap_reason": None,
        },
    }


def _maybe_rewrite_query(
    message: str,
    history_context: Optional[str],
    *,
    lang: str,
) -> Dict[str, Any]:
    try:
        from services import demo_polish

        return demo_polish.rewrite_retrieval_query(
            message, history_context, lang=lang
        )
    except Exception as e:
        print(f"[RAG] query rewrite failed: {e}", flush=True)
        return {
            "rewritten": False,
            "called": False,
            "needed": False,
            "reason": "error",
            "original": message,
            "query": message,
        }


def chat_with_rag(
    message: str,
    *,
    language: Optional[str] = None,
    debug: bool = False,
    history_context: Optional[str] = None,
) -> Dict[str, Any]:
    lang = language or "en"
    lang_fallback = False
    rewrite = _maybe_rewrite_query(message, history_context, lang=lang)
    search_q = rewrite.get("query") or message
    age_ask = bool(_query_age_digits(search_q) or _query_age_digits(message))
    # Ask Chroma for a pool, then keep top_k after lexical re-rank.
    # Age asks need a wider pool — the right band often sits ~15–25 by raw vector.
    keep_k = SETTINGS.rag_top_k
    pool_k = max(keep_k, 20) if age_ask else max(keep_k, 8)
    hits = retrieve(search_q, lang=lang, top_k=pool_k)
    # If language filter returned nothing, retry without filter
    if not hits and lang:
        hits = retrieve(search_q, lang=None, top_k=pool_k)
        lang_fallback = True
    pool_returned = len(hits)
    hits = _merge_hits(hits, _age_faq_overlay(search_q, lang))
    hits = _lexical_rerank(search_q, hits)[:keep_k]

    top_hit = hits[0] if hits else None
    # Off-topic check uses the parent's words + rewritten query cues
    off_topic = bool(
        hits
        and top_hit is not None
        and (_off_topic(message, top_hit) and _off_topic(search_q, top_hit))
    )

    gen = generate_answer_meta(
        message,
        hits,
        lang=lang,
        history_context=history_context,
    )
    answer = gen.get("answer") or ""
    citations = [_citation_from_hit(rank, h) for rank, h in enumerate(hits, start=1)]
    stats = _score_stats(hits)
    # Gap / grounding still keys off rank-1 after lexical order (what we feed the LLM)
    top_score = stats["rank1_score"]

    gap_reason = None
    provider = gen.get("provider") or ""
    weak_cut = _WEAK_SCORE_LOCAL if provider == "local_fallback" else _WEAK_SCORE
    greeting_ok = is_greeting(message) and _is_greeting_hit(top_hit)
    admin_note = (gen.get("admin_gap") or "") if isinstance(gen.get("admin_gap"), str) else ""
    model_flagged_off = "off_topic" in admin_note.lower()

    retrieval_strong = top_score is not None and top_score >= _GROUNDED_RETRIEVAL_SCORE

    if greeting_ok:
        gap_reason = None
    elif not hits:
        gap_reason = "no_retrieval"
    elif (off_topic or model_flagged_off) and not retrieval_strong:
        # Unrelated to centre KB — refuse; do not lecture from world knowledge
        gap_reason = "off_topic_retrieval"
    elif top_score is not None and top_score < weak_cut and provider != "local_fallback":
        gap_reason = "weak_retrieval"
    elif gen.get("had_no_answer_token"):
        gap_reason = "model_no_answer"
    elif gen.get("admin_gap") and "off_topic" not in admin_note.lower():
        gap_reason = "model_admin_gap"
    elif gen.get("used_fallback") and (
        not hits or (top_score is not None and top_score < 0.45)
    ):
        gap_reason = "generic_fallback"

    # Hard refuse only when retrieval is weak *and* ungrounded — never when
    # Chroma already found a solid centre chunk (e.g. phonics ask → Phonics.md).
    if gap_reason == "off_topic_retrieval" and not greeting_ok and not retrieval_strong:
        from rag.generator import _OFF_KB_REPLY  # noqa: PLC0415

        answer = _OFF_KB_REPLY.get(lang, _OFF_KB_REPLY["en"])

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
        "top_k": keep_k,
        "pool_k": pool_k,
        "pool_returned": pool_returned,
        "keep_rule": (
            f"Ask Chroma for pool_k={pool_k}; keep up to top_k={keep_k} after "
            "lexical re-rank. Fewer hits ⇒ language filter / index had fewer matches "
            "(not a random subset)."
        ),
        "order_rule": (
            "Not sorted by similarity alone. Order: age filename match → "
            "path/keyword → preferred folder → token overlap → vector score."
        ),
        "retrieval_query": search_q,
        "query_rewrite": rewrite,
        "history_chars": len(history_context or ""),
        "embedding_mode": "dashscope" if SETTINGS.has_dashscope else "local_hash",
        "hits": citations,
        "top_score": top_score,
        "rank1_score": stats["rank1_score"],
        "max_score": stats["max_score"],
        "knowledge_gap": bool(gap_reason),
        "gap_reason": gap_reason,
        "admin_gap_note": gen.get("admin_gap"),
    }
    return out

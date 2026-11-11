"""Retrieve-then-generate RAG pipeline."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import SETTINGS
from rag.chunking import parse_markdown
from rag.embeddings import embed_query
from rag.generator import generate_answer_meta
from rag.ingest import ingest
from rag.sparse import get_sparse_index, rrf_fuse
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
        return
    # Cold start: Chroma persisted but in-memory BM25 empty → rebuild sparse
    sparse = get_sparse_index()
    if SETTINGS.rag_hybrid_enabled and sparse.count == 0 and store.count > 0:
        rows = store.export_rows()
        n = sparse.rebuild(rows)
        print(f"[RAG] Rebuilt BM25 sparse index from Chroma ({n} docs).", flush=True)


def retrieve(
    question: str,
    *,
    lang: Optional[str] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Dense-only retrieve (used internally / tests). Prefer retrieve_hybrid."""
    ensure_index()
    emb = embed_query(question)
    return get_store().query(
        emb,
        top_k=top_k or SETTINGS.rag_top_k,
        language=lang,
    )


def retrieve_hybrid(
    question: str,
    *,
    lang: Optional[str] = None,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Dense (Chroma) + sparse (BM25) → Reciprocal Rank Fusion.
    Falls back to dense-only when hybrid disabled or sparse empty.
    """
    ensure_index()
    k = int(top_k or SETTINGS.rag_top_k)
    emb = embed_query(question)
    dense_pool = max(k, SETTINGS.rag_sparse_pool_k)
    dense = get_store().query(emb, top_k=dense_pool, language=lang)
    for h in dense:
        h["channel"] = "dense"

    if not SETTINGS.rag_hybrid_enabled:
        return {
            "hits": dense[:k],
            "debug": {
                "mode": "dense_only",
                "reason": "hybrid_disabled",
                "dense_count": len(dense),
                "sparse_count": 0,
                "fused_count": min(len(dense), k),
            },
        }

    sparse = get_sparse_index().query(
        question,
        top_k=max(k, SETTINGS.rag_sparse_pool_k),
        language=lang,
    )
    if not sparse:
        return {
            "hits": dense[:k],
            "debug": {
                "mode": "dense_only",
                "reason": "sparse_empty",
                "dense_count": len(dense),
                "sparse_count": 0,
                "fused_count": min(len(dense), k),
            },
        }

    fused, fuse_dbg = rrf_fuse(
        dense,
        sparse,
        rrf_k=SETTINGS.rag_rrf_k,
        limit=max(k, dense_pool),
        question=question,
    )
    return {"hits": fused[: max(k, dense_pool)], "debug": fuse_dbg}


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
        "fusion": h.get("fusion"),
        "channel": (h.get("fusion") or {}).get("channels") or h.get("channel"),
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


def _pool_and_keep(
    query: str,
    *,
    lang: str,
    keep_k: int,
    pool_k: int,
) -> Dict[str, Any]:
    """Hybrid retrieve (dense+BM25→RRF) + language fallback + age overlay + lexical keep."""
    lang_fallback = False
    pack = retrieve_hybrid(query, lang=lang, top_k=pool_k)
    hits = pack.get("hits") or []
    hybrid_debug = pack.get("debug") or {}
    if not hits and lang:
        pack = retrieve_hybrid(query, lang=None, top_k=pool_k)
        hits = pack.get("hits") or []
        hybrid_debug = pack.get("debug") or {}
        lang_fallback = True
    pool_returned = len(hits)
    hits = _merge_hits(hits, _age_faq_overlay(query, lang))
    hits = _lexical_rerank(query, hits)[:keep_k]
    return {
        "hits": hits,
        "lang_fallback": lang_fallback,
        "pool_returned": pool_returned,
        "pool_k": pool_k,
        "keep_k": keep_k,
        "query": query,
        "hybrid": hybrid_debug,
    }


def _grade_retrieval(
    message: str,
    hits: List[Dict[str, Any]],
    *,
    local_embeddings: bool = False,
) -> Dict[str, Any]:
    """
    CRAG-style confidence: Correct / Ambiguous / Incorrect.
    Lightweight heuristics (no extra LLM) — rewrite LLM runs only on non-Correct.
    """
    weak_cut = _WEAK_SCORE_LOCAL if local_embeddings else _WEAK_SCORE
    if not hits:
        return {
            "grade": "incorrect",
            "why": "no hits",
            "rank1_score": None,
            "max_score": None,
        }
    stats = _score_stats(hits)
    rank1 = stats["rank1_score"]
    max_s = stats["max_score"]
    top = hits[0]
    if _off_topic(message, top) and (rank1 is None or rank1 < _GROUNDED_RETRIEVAL_SCORE):
        return {
            "grade": "incorrect",
            "why": "top hit looks off-topic / weakly grounded",
            "rank1_score": rank1,
            "max_score": max_s,
        }
    if rank1 is not None and rank1 < weak_cut:
        return {
            "grade": "incorrect",
            "why": f"rank-1 vector {rank1:.3f} < weak cut {weak_cut}",
            "rank1_score": rank1,
            "max_score": max_s,
        }
    if rank1 is not None and rank1 >= _GROUNDED_RETRIEVAL_SCORE:
        return {
            "grade": "correct",
            "why": f"rank-1 vector {rank1:.3f} ≥ grounded cut",
            "rank1_score": rank1,
            "max_score": max_s,
        }
    return {
        "grade": "ambiguous",
        "why": f"mid scores (rank-1={rank1})",
        "rank1_score": rank1,
        "max_score": max_s,
    }


def _hit_titles(hits: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for h in hits[:5]:
        meta = h.get("metadata") or {}
        out.append(str(meta.get("title") or meta.get("source") or "")[:80])
    return out


def _best_hit_pack(
    a: Dict[str, Any],
    b: Dict[str, Any],
) -> Dict[str, Any]:
    """Prefer the pack with stronger max vector score (then more hits)."""
    sa = _score_stats(a.get("hits") or [])
    sb = _score_stats(b.get("hits") or [])
    ma = sa["max_score"] if sa["max_score"] is not None else -1.0
    mb = sb["max_score"] if sb["max_score"] is not None else -1.0
    if mb > ma + 0.02:
        return b
    if ma > mb + 0.02:
        return a
    if len(b.get("hits") or []) > len(a.get("hits") or []):
        return b
    return a


def _corrective_retrieve(
    message: str,
    *,
    lang: str,
    keep_k: int,
    pool_k: int,
    prior_context: Optional[str],
) -> Dict[str, Any]:
    """
    Pass 1: retrieve with parent text.
    Grade (CRAG Correct/Ambiguous/Incorrect).
    If not Correct and prior chat exists → rewrite once → Pass 2 retrieve.
    """
    local = not SETTINGS.has_dashscope
    pass1 = _pool_and_keep(message, lang=lang, keep_k=keep_k, pool_k=pool_k)
    grade = _grade_retrieval(message, pass1["hits"], local_embeddings=local)
    rewrite: Dict[str, Any] = {
        "rewritten": False,
        "called": False,
        "needed": False,
        "reason": "retrieval_ok" if grade["grade"] == "correct" else "pending",
        "original": message,
        "query": message,
        "grade": grade["grade"],
        "grade_why": grade["why"],
        "protocol": "CRAG-lite",
        "pass": 1,
        "retried": False,
    }
    chosen = pass1
    pass2: Optional[Dict[str, Any]] = None
    if grade["grade"] != "correct":
        try:
            from services import demo_polish

            rewrite = demo_polish.rewrite_retrieval_query(
                message,
                prior_context,
                lang=lang,
                grade=grade["grade"],
                hit_titles=_hit_titles(pass1["hits"]),
                force=True,
            )
            rewrite["grade"] = grade["grade"]
            rewrite["grade_why"] = grade["why"]
            rewrite["protocol"] = "CRAG-lite"
            rewrite["pass"] = 1
            rewrite["retried"] = False
            if rewrite.get("rewritten") and rewrite.get("query"):
                pass2 = _pool_and_keep(
                    str(rewrite["query"]),
                    lang=lang,
                    keep_k=keep_k,
                    pool_k=pool_k,
                )
                chosen = _best_hit_pack(pass1, pass2)
                rewrite["retried"] = True
                rewrite["pass"] = 2 if chosen is pass2 else 1
                rewrite["pass2_query"] = rewrite["query"]
                rewrite["kept"] = "pass2" if chosen is pass2 else "pass1"
        except Exception as e:
            print(f"[RAG] corrective rewrite failed: {e}", flush=True)
            rewrite["reason"] = "error"
    else:
        from services import demo_polish as _dp  # noqa: PLC0415

        rewrite["reason"] = "retrieval_ok"
        rewrite["rule"] = getattr(_dp, "_PROTOCOL_RULE", None) or (
            "CRAG-lite: first retrieve graded Correct — no rewrite/retry."
        )
        rewrite["needed"] = False

    return {
        "pack": chosen,
        "pass1": pass1,
        "pass2": pass2,
        "grade": grade,
        "rewrite": rewrite,
        "lang_fallback": bool(
            chosen.get("lang_fallback") or pass1.get("lang_fallback")
        ),
    }


def _pack_citations(pack: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not pack:
        return []
    return [
        _citation_from_hit(rank, h)
        for rank, h in enumerate(pack.get("hits") or [], start=1)
    ]


def _retrieval_debug_fields(corr: Dict[str, Any], *, message: str) -> Dict[str, Any]:
    """Shared inspector fields: pass-1 always; pass-2 only after corrective retry."""
    pack = corr["pack"]
    pass1 = corr["pass1"]
    pass2 = corr.get("pass2")
    rewrite = corr["rewrite"]
    pass1_hits = _pack_citations(pass1)
    pass2_hits = _pack_citations(pass2) if pass2 else []
    final_hits = _pack_citations(pack)
    # Prefer the retrieve pack that was kept for generation
    hybrid = (pack.get("hybrid") if isinstance(pack, dict) else None) or pass1.get(
        "hybrid"
    )
    return {
        "pass1_query": message,
        "pass1_hits": pass1_hits,
        "pass1_hit_count": len(pass1_hits),
        "pass2_query": (rewrite.get("pass2_query") or rewrite.get("query"))
        if rewrite.get("retried")
        else None,
        "pass2_hits": pass2_hits or None,
        "pass2_hit_count": len(pass2_hits) if pass2_hits else 0,
        "final_pass": rewrite.get("kept")
        or ("pass2" if rewrite.get("retried") and pack is pass2 else "pass1"),
        "retrieval_query": pack.get("query") or message,
        "query_rewrite": rewrite,
        "retrieval_grade": corr["grade"],
        "hybrid": hybrid,
        "retrieve_mode": (hybrid or {}).get("mode") or "dense_only",
        "hits": final_hits,
    }


def retrieve_for_enrich(
    message: str,
    *,
    language: Optional[str] = None,
    top_k: Optional[int] = None,
    history_context: Optional[str] = None,
    prior_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lightweight retrieve for grounded-compose (structured fact + KB excerpts).
    Does not generate — caller composes with the privileged fact card.
    """
    lang = language or "en"
    k = int(top_k or SETTINGS.grounding_enrich_top_k or SETTINGS.rag_top_k)
    pool_k = max(k, SETTINGS.rag_sparse_pool_k)
    prior = prior_context if prior_context is not None else history_context
    corr = _corrective_retrieve(
        message, lang=lang, keep_k=k, pool_k=pool_k, prior_context=prior
    )
    pack = corr["pack"]
    hits = pack["hits"]
    stats = _score_stats(hits)
    fields = _retrieval_debug_fields(corr, message=message)
    return {
        "hits": hits,
        "citations": fields["hits"],
        "debug": {
            "mode": "grounded_enrich",
            "hit_count": len(hits),
            "language": lang,
            "language_fallback": corr["lang_fallback"],
            "index_size": get_store().count,
            "top_k": k,
            "pool_k": pool_k,
            "pool_returned": pack.get("pool_returned"),
            "order_rule": (
                "Hybrid RRF (dense+BM25) pool, then lexical re-rank "
                "(age → path → folder → overlap → score)."
            ),
            "keep_rule": (
                f"Pass 1: raw parent → dense+BM25→RRF (pool_k={pool_k}). "
                f"Pass 2 only if grade ≠ Correct and a rewrite fires (top_k={k})."
            ),
            "embedding_mode": "dashscope" if SETTINGS.has_dashscope else "local_hash",
            "sparse_index_size": get_sparse_index().count,
            "top_score": stats["rank1_score"],
            "rank1_score": stats["rank1_score"],
            "max_score": stats["max_score"],
            "knowledge_gap": False,
            "gap_reason": None,
            **fields,
        },
    }


def chat_with_rag(
    message: str,
    *,
    language: Optional[str] = None,
    debug: bool = False,
    history_context: Optional[str] = None,
    prior_context: Optional[str] = None,
) -> Dict[str, Any]:
    lang = language or "en"
    age_ask = bool(_query_age_digits(message))
    keep_k = SETTINGS.rag_top_k
    pool_k = max(keep_k, SETTINGS.rag_sparse_pool_k)
    prior = prior_context if prior_context is not None else history_context
    corr = _corrective_retrieve(
        message, lang=lang, keep_k=keep_k, pool_k=pool_k, prior_context=prior
    )
    pack = corr["pack"]
    hits = pack["hits"]
    search_q = pack.get("query") or message
    rewrite = corr["rewrite"]
    lang_fallback = corr["lang_fallback"]
    pool_returned = pack.get("pool_returned")

    top_hit = hits[0] if hits else None
    off_topic = bool(hits and top_hit is not None and _off_topic(message, top_hit))

    gen = generate_answer_meta(
        message,
        hits,
        lang=lang,
        history_context=history_context,
    )
    answer = gen.get("answer") or ""
    stats = _score_stats(hits)
    top_score = stats["rank1_score"]
    fields = _retrieval_debug_fields(corr, message=message)
    citations = fields["hits"]

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

    if gap_reason == "off_topic_retrieval" and not greeting_ok and not retrieval_strong:
        from rag.generator import _OFF_KB_REPLY  # noqa: PLC0415

        answer = _OFF_KB_REPLY.get(lang, _OFF_KB_REPLY["en"])

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
    out["debug"] = {
        "hit_count": len(hits),
        "language": lang,
        "language_fallback": lang_fallback,
        "index_size": get_store().count,
        "top_k": keep_k,
        "pool_k": pool_k,
        "pool_returned": pool_returned,
        "keep_rule": (
            f"Pass 1: raw parent → dense (Chroma) + BM25 → RRF "
            f"(pool_k={pool_k}, keep top_k={keep_k}). "
            "Pass 2 only after a failed grade + rewrite."
        ),
        "order_rule": (
            "Hybrid RRF first (dense rank + sparse rank). Then lexical re-rank: "
            "age filename → path/keyword → folder → token overlap → score."
        ),
        "history_chars": len(history_context or ""),
        "embedding_mode": "dashscope" if SETTINGS.has_dashscope else "local_hash",
        "sparse_index_size": get_sparse_index().count,
        "top_score": top_score,
        "rank1_score": stats["rank1_score"],
        "max_score": stats["max_score"],
        "knowledge_gap": bool(gap_reason),
        "gap_reason": gap_reason,
        "admin_gap_note": gen.get("admin_gap"),
        **fields,
    }
    return out

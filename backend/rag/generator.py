"""Answer generation via native DashScope HTTP API, with extractive fallback."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import SETTINGS
from rag.dashscope_native import (
    auth_headers,
    generation_parameters,
    http_client,
    native_api_base,
)
from services import llm_io

# Identity + language only. Shared rules live in _HTTP_SYSTEM (one copy per call).
PERSONA = {
    "en": (
        "BrightNest Learning Centre (亮巢學習中心) admin — HK bilingual kids' centre "
        "(demo). First person we/our. Warm, short replies."
    ),
    "zh-HK": (
        "「亮巢學習中心」（BrightNest）行政助理（示範）。第一人稱「我哋」。友善簡潔。"
    ),
    "zh-CN": (
        "「亮巢学习中心」（BrightNest）行政助理（演示）。第一人称「我们」。友善简洁。"
    ),
}

_GREETING_OPENER_RE = re.compile(
    r"^\s*((?:hello|hi|hey|good\s+(?:morning|afternoon|evening))"
    r"(?:\s*[,!.，！。…]*)\s*|"
    r"(?:你好|您好|哈囉|哈罗)(?:\s*[,!.，！。…]*)\s*)",
    re.I,
)

_OFF_KB_REPLY = {
    "en": (
        "That’s outside what our BrightNest materials cover — we’re a kids’ learning centre "
        "(programmes, fees, hours, booking, and centre policies), not a general science tutor.\n\n"
        "Ask me about phonics, Mandarin, writing, trials, seats, or opening hours and I’ll help."
    ),
    "zh-HK": (
        "呢啲唔屬於亮巢學習中心資料可以回答嘅範圍——"
        "我哋幫家長處理課程、學費、時間、預約同中心政策，唔係一般科學導師。\n\n"
        "你可以問拼音、普通話、寫作、試堂、學位或營業時間。"
    ),
    "zh-CN": (
        "这不属于亮巢学习中心资料可以回答的范围——"
        "我们协助家长处理课程、学费、时间、预约与中心政策，不是一般科学辅导。\n\n"
        "你可以问拼音、普通话、写作、试听、学位或营业时间。"
    ),
}

_NO_ANSWER_RE = re.compile(r"\[?\s*NO[_\s-]?ANSWER\s*\]?", re.IGNORECASE)
_ADMIN_GAP_RE = re.compile(
    r"\[\[\s*ADMIN_GAP\s*:\s*(.*?)\s*\]\]",
    re.IGNORECASE | re.DOTALL,
)

_FALLBACK = {
    "en": (
        "Happy to help. Based on what we usually share with parents: we run bilingual programmes "
        "in Central (playgroup, phonics, Mandarin, writing) with published guide fees, and we can "
        "book a 45-minute Trial Assessment for HK$180. Tell me which age/programme you’re looking "
        "at, or ask about hours, location, or fees."
    ),
    "zh-HK": (
        "我可以幫到你。一般家長會問課程、學費、營業時間同試堂——"
        "我哋喺中環有遊戲班、拼音、普通話同寫作，試堂評估 45 分鐘 HK$180。"
        "你可以講小朋友年齡／想讀邊科，或者問地址同營業時間。"
    ),
    "zh-CN": (
        "我可以帮忙。家长常问课程、学费、营业时间与试听——"
        "我们在中环提供游戏班、拼音、普通话与写作，试听评估 45 分钟 HK$180。"
        "请告诉我孩子年龄／想读的课程，或询问地址与营业时间。"
    ),
}

# Models that must use multimodal-generation (see Model Studio error-code docs).
_MULTIMODAL_MARKERS = (
    "qwen3.8",
    "qwen3-vl",
    "qwen3.7-plus",
    "qwen-vl",
    "omni",
)


def _is_multimodal_model(model: str) -> bool:
    m = (model or "").lower()
    return any(tok in m for tok in _MULTIMODAL_MARKERS)


def sanitize_answer(text: str, lang: str = "en") -> str:
    """Parent-facing text only (strips NO_ANSWER / ADMIN_GAP tags)."""
    return sanitize_answer_meta(text, lang)["answer"]


def _strip_midthread_greeting(text: str) -> str:
    """Drop a leading Hello/Hi when continuing an ongoing chat."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    stripped = _GREETING_OPENER_RE.sub("", cleaned, count=1).lstrip()
    return stripped or cleaned


def sanitize_answer_meta(
    text: str,
    lang: str = "en",
    *,
    strip_greeting: bool = False,
) -> Dict[str, Any]:
    """Sanitize reply and extract admin-gap signals for the to-do list."""
    raw = (text or "").strip()
    meta: Dict[str, Any] = {
        "answer": "",
        "had_no_answer_token": bool(_NO_ANSWER_RE.search(raw)),
        "admin_gap": None,
        "used_fallback": False,
    }
    if not raw:
        meta["answer"] = _FALLBACK.get(lang, _FALLBACK["en"])
        meta["used_fallback"] = True
        return meta

    gap_match = _ADMIN_GAP_RE.search(raw)
    if gap_match:
        meta["admin_gap"] = (gap_match.group(1) or "").strip()
        raw = _ADMIN_GAP_RE.sub("", raw).strip()

    cleaned = _NO_ANSWER_RE.sub("", raw).strip()
    if strip_greeting:
        cleaned = _strip_midthread_greeting(cleaned)
    if not cleaned or cleaned in {".", "-", "—", "…"}:
        meta["answer"] = _FALLBACK.get(lang, _FALLBACK["en"])
        meta["used_fallback"] = True
        return meta
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    meta["answer"] = cleaned
    if cleaned == _FALLBACK.get(lang, _FALLBACK["en"]):
        meta["used_fallback"] = True
    return meta


def _build_prompt(
    question: str,
    contexts: List[Dict[str, Any]],
    lang: str,
    history_context: Optional[str] = None,
) -> str:
    blocks = []
    for i, c in enumerate(contexts, 1):
        src = (c.get("metadata") or {}).get("source", "unknown")
        blocks.append(f"[{i}] (source: {src})\n{c.get('text', '')}")
    ctx = "\n\n".join(blocks) if blocks else "(no retrieved documents)"
    persona = PERSONA.get(lang, PERSONA["en"])
    history_block = ""
    no_hello = ""
    if history_context and history_context.strip():
        history_block = (
            f"Chat:\n{history_context.strip()}\n\n"
        )
        no_hello = "Follow-up — no Hello/Hi/你好 opener.\n\n"
    return (
        f"{persona}\n\n"
        f"{history_block}"
        f"{no_hello}"
        f"KB excerpts:\n{ctx}\n\n"
        f"Parent: {question}\n"
        "Answer (excerpts only; off-KB → brief refuse + "
        "[[ADMIN_GAP: off_topic — not in centre KB]] on its own last line):"
    )


def _extractive_fallback(
    question: str,
    contexts: List[Dict[str, Any]],
    lang: str,
) -> str:
    if not contexts:
        return _FALLBACK.get(lang, _FALLBACK["en"])

    top = contexts[0]
    text = (top.get("text") or "").strip()
    # Drop assistant-only / NO_ANSWER lines and raw markdown chrome from extractive surface
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if _NO_ANSWER_RE.search(ln):
            continue
        if s.lower().startswith("assistant notes"):
            continue
        if s.startswith("[") and s.endswith("]"):
            continue
        if s.startswith("#"):
            # Keep heading text without hashes if it looks like content
            s = s.lstrip("#").strip()
            if not s or s.lower() in {"keywords", "see also"}:
                continue
        if s.startswith("---") or s.startswith("```"):
            continue
        if s.lower().startswith("keywords:"):
            continue
        lines.append(s)
    body = "\n".join(lines).strip()
    body = sanitize_answer(body, lang)
    # Prefer answer-shaped lines when the chunk is a Q&A dump
    if "A:" in body or "答：" in body or "答:" in body:
        prefer = []
        for ln in body.splitlines():
            if re.match(r"^(A:|答[:：])", ln.strip()):
                prefer.append(re.sub(r"^(A:|答[:：])\s*", "", ln.strip()))
            elif prefer and not re.match(r"^(Q:|問[:：]|问[:：])", ln.strip()):
                prefer.append(ln.strip())
        if prefer:
            body = "\n".join(prefer[:8]).strip()
    if len(body) > 700:
        body = body[:700].rstrip() + "…"
    if len(body) < 40:
        return _FALLBACK.get(lang, _FALLBACK["en"])
    src = (top.get("metadata") or {}).get("source", "")
    footer = {
        "en": f"\n\n(Source: {src})" if src else "",
        "zh-HK": f"\n\n（資料來源：{src}）" if src else "",
        "zh-CN": f"\n\n（资料来源：{src}）" if src else "",
    }.get(lang, "")
    return f"{body}{footer}"


_TOOL_OK_RE = re.compile(r"\[\[\s*TOOL_OK\s*\]\]", re.I)
_TOOL_SKIP_RE = re.compile(r"\[\[\s*TOOL_SKIP\s*\]\]", re.I)


def _strip_tool_gate(text: str) -> tuple[str, Optional[bool]]:
    """Parse trailing [[TOOL_OK]] / [[TOOL_SKIP]]; return (clean_text, invoke_or_none)."""
    raw = (text or "").strip()
    invoke: Optional[bool] = None
    if _TOOL_OK_RE.search(raw):
        invoke = True
    elif _TOOL_SKIP_RE.search(raw):
        invoke = False
    cleaned = _TOOL_OK_RE.sub("", raw)
    cleaned = _TOOL_SKIP_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, invoke


def compose_grounded_fact(
    question: str,
    fact_card: str,
    contexts: List[Dict[str, Any]],
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
    route: str = "centre_facts",
    live_doc: str = "",
    ask_tool_ok: bool = False,
    tool_label: str = "availability / booking tool",
) -> Dict[str, Any]:
    """
    Tier-2 reply: privileged live/structured docs + optional RAG enrichment.
    Optionally ask the model whether to expose a downstream tool (e.g. seat cards).
    """
    card = (fact_card or "").strip()
    live = (live_doc or "").strip()
    privileged_parts = []
    if live:
        privileged_parts.append(f"LIVE DOC (highest priority — reference this):\n{live}")
    if card:
        privileged_parts.append(f"STRUCTURED FACT / REGISTER CARD:\n{card}")
    privileged = "\n\n".join(privileged_parts).strip() or card
    out: Dict[str, Any] = {
        "answer": card or live,
        "provider": "passthrough",
        "composed": False,
        "adapted": False,
        "used_rag": bool(contexts),
        "invoke_tool": True if ask_tool_ok else None,
        "had_live_doc": bool(live),
    }
    if not privileged or not (question or "").strip():
        return out
    if not SETTINGS.has_dashscope:
        return out
    if not contexts and not ask_tool_ok and not live:
        adapted = adapt_structured_fact(
            question, card, lang=lang, history_context=history_context, route=route
        )
        adapted["composed"] = False
        adapted["used_rag"] = False
        adapted["invoke_tool"] = out["invoke_tool"]
        return adapted

    persona = PERSONA.get(lang, PERSONA["en"])
    history_block = ""
    no_hello = ""
    has_history = bool(history_context and history_context.strip())
    if has_history:
        history_block = (
            f"Chat:\n{history_context.strip()}\n\n"
        )
        no_hello = "Follow-up — no Hello/Hi/你好 opener.\n\n"
    kb_bits = []
    for i, h in enumerate(contexts[:5], start=1):
        meta = h.get("metadata") or {}
        title = meta.get("title") or meta.get("source") or f"chunk-{i}"
        body = (h.get("text") or h.get("snippet") or "").strip()
        if not body:
            continue
        kb_bits.append(f"[{i}] {title}\n{body[:900]}")
    kb_block = "\n\n".join(kb_bits) if kb_bits else "(no KB excerpts)"
    tool_block = ""
    if ask_tool_ok:
        tool_block = (
            f"Last line exactly [[TOOL_OK]] if interactive {tool_label} seat cards "
            f"should show, else [[TOOL_SKIP]]. Never invent seats.\n"
        )
    prompt = (
        f"{persona}\n\n"
        "Priority: (1) LIVE/CARD facts — never contradict or invent numbers/seats; "
        "(2) KB — elaborate only when consistent with (1).\n"
        "Short spoken parent reply. No meta / ADMIN_GAP / NO_ANSWER.\n"
        f"{tool_block}"
        f"{history_block}"
        f"{no_hello}"
        f"Route: {route}\n"
        f"LIVE/CARD:\n{privileged}\n\n"
        f"KB (secondary):\n{kb_block}\n\n"
        f"Parent: {question}\n"
        "Reply:"
    )
    try:
        raw = _dashscope_generation_http(
            prompt,
            max_tokens=min(360, max(SETTINGS.gen_max_tokens, 300)),
            temperature=0.25,
            role="grounded_compose",
        )
        meta = sanitize_answer_meta(raw, lang, strip_greeting=has_history)
        composed = (meta.get("answer") or "").strip()
        invoke: Optional[bool] = None
        if ask_tool_ok:
            composed, invoke = _strip_tool_gate(composed)
            if invoke is None:
                # Default: expose tool when we have a non-empty live/register card
                invoke = bool(card.strip())
        if not composed or meta.get("used_fallback") or len(composed) < 20:
            fb = adapt_structured_fact(
                question,
                privileged,
                lang=lang,
                history_context=history_context,
                route=route,
            )
            fb["composed"] = False
            fb["used_rag"] = bool(contexts)
            fb["compose_fallback"] = "adapt"
            fb["invoke_tool"] = True if ask_tool_ok and invoke is not False else invoke
            fb["had_live_doc"] = bool(live)
            return fb
        out["answer"] = composed
        out["provider"] = "dashscope"
        out["composed"] = True
        out["adapted"] = True
        out["used_rag"] = bool(contexts)
        out["invoke_tool"] = invoke
        return out
    except Exception as e:
        print(f"[GEN] compose_grounded_fact failed ({e}); adapt fallback.", flush=True)
        fb = adapt_structured_fact(
            question,
            privileged,
            lang=lang,
            history_context=history_context,
            route=route,
        )
        fb["composed"] = False
        fb["used_rag"] = bool(contexts)
        fb["compose_fallback"] = f"error:{e}"
        fb["invoke_tool"] = True if ask_tool_ok else None
        fb["had_live_doc"] = bool(live)
        return fb


def adapt_structured_fact(
    question: str,
    fact_card: str,
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
    route: str = "centre_facts",
) -> Dict[str, Any]:
    """
    Demo-friendly rewrite: take a deterministic fact card and answer the actual
    parent question naturally. Prefer a fluent reply over a rigid template dump.
    """
    card = (fact_card or "").strip()
    out: Dict[str, Any] = {
        "answer": card,
        "provider": "passthrough",
        "adapted": False,
    }
    if not card or not (question or "").strip():
        return out
    if not SETTINGS.has_dashscope:
        return out

    persona = PERSONA.get(lang, PERSONA["en"])
    history_block = ""
    no_hello = ""
    has_history = bool(history_context and history_context.strip())
    if has_history:
        history_block = (
            f"Chat:\n{history_context.strip()}\n\n"
        )
        no_hello = "Follow-up — no Hello/Hi/你好 opener.\n\n"
    prompt = (
        f"{persona}\n\n"
        "Rewrite the fact card into a short spoken parent reply. "
        "Answer their ask first; keep fees/names/times faithful; drop irrelevant rows; "
        "no inventing; no meta / ADMIN_GAP / NO_ANSWER.\n"
        f"{history_block}"
        f"{no_hello}"
        f"Route: {route}\n"
        f"Card:\n{card}\n\n"
        f"Parent: {question}\n"
        "Reply:"
    )
    try:
        raw = _dashscope_generation_http(
            prompt,
            max_tokens=min(220, SETTINGS.gen_max_tokens),
            temperature=0.25,
            role="fact_adapt",
        )
        meta = sanitize_answer_meta(raw, lang, strip_greeting=has_history)
        adapted = (meta.get("answer") or "").strip()
        if not adapted or meta.get("used_fallback") or len(adapted) < 20:
            return out
        out["answer"] = adapted
        out["provider"] = "dashscope"
        out["adapted"] = True
        return out
    except Exception as e:
        print(f"[GEN] adapt_structured_fact failed ({e}); raw card.", flush=True)
        return out


def generate_answer(
    question: str,
    contexts: List[Dict[str, Any]],
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
) -> str:
    return generate_answer_meta(
        question, contexts, lang=lang, history_context=history_context
    )["answer"]


def generate_answer_meta(
    question: str,
    contexts: List[Dict[str, Any]],
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
) -> Dict[str, Any]:
    has_history = bool(history_context and history_context.strip())
    if SETTINGS.has_dashscope and contexts:
        try:
            raw = _dashscope_generation_http(
                _build_prompt(question, contexts, lang, history_context=history_context),
                role="rag_generate",
            )
            meta = sanitize_answer_meta(raw, lang, strip_greeting=has_history)
            meta["provider"] = "dashscope"
            return meta
        except Exception as e:
            print(f"[GEN] DashScope Generation failed ({e}); extractive fallback.", flush=True)
    fb = _extractive_fallback(question, contexts, lang)
    meta = sanitize_answer_meta(fb, lang, strip_greeting=has_history)
    meta["provider"] = "local_fallback"
    if not contexts:
        meta["used_fallback"] = True
    return meta


def _dashscope_generation_http(
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    role: str = "main_generate",
) -> str:
    import time

    model = SETTINGS.llm_model
    multimodal = _is_multimodal_model(model)
    path = (
        "services/aigc/multimodal-generation/generation"
        if multimodal
        else "services/aigc/text-generation/generation"
    )
    url = f"{native_api_base()}/{path}"
    tok = int(max_tokens if max_tokens is not None else SETTINGS.gen_max_tokens)
    temp = float(temperature if temperature is not None else SETTINGS.gen_temperature)

    # Shared across RAG / compose / adapt — path-specific rules stay in the user prompt.
    system = (
        "BrightNest centre admin. Use only provided excerpts/cards. "
        "No invented fees or seats. No Hello/Hi/你好 on follow-ups. "
        "Never mention ADMIN_GAP tags, tools, or spreadsheets to the parent."
    )
    if multimodal:
        messages = [
            {"role": "system", "content": [{"text": system}]},
            {"role": "user", "content": [{"text": prompt}]},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    payload = {
        "model": model,
        "input": {"messages": messages},
        "parameters": generation_parameters(max_tokens=tok, temperature=temp),
    }
    t0 = time.perf_counter()
    print(
        f"[GEN] POST {url} model={model} multimodal={multimodal} "
        f"think=off tokens≤{tok}",
        flush=True,
    )
    resp = http_client(timeout=60.0).post(url, headers=auth_headers(), json=payload)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Generation HTTP {resp.status_code}: {resp.text[:500]}") from None

    code = data.get("code")
    request_ok = resp.status_code == 200 and (not code or str(code).lower() in ("success", "null"))
    if resp.status_code == 200 and data.get("output"):
        request_ok = True
    if not request_ok:
        raise RuntimeError(
            f"Generation HTTP {resp.status_code} code={code} "
            f"message={data.get('message') or data} url={url}"
        )

    ms = int((time.perf_counter() - t0) * 1000)
    print(f"[GEN] ok {ms}ms", flush=True)
    text = _extract_text(data)
    llm_io.record(
        role=role,
        model=model,
        system=system,
        user=prompt,
        response=text,
        provider="dashscope",
        meta={
            "latency_ms": ms,
            "max_tokens": tok,
            "temperature": temp,
            "multimodal": multimodal,
        },
    )
    return text


def _extract_text(data: Dict[str, Any]) -> str:
    output = data.get("output") or {}
    choices = output.get("choices") or []
    if choices:
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts).strip()
        if isinstance(content, str):
            return content.strip()
    if isinstance(output.get("text"), str):
        return output["text"].strip()
    return str(output)

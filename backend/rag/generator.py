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

PERSONA = {
    "en": (
        "You are the helpful admin assistant for BrightNest Learning Centre "
        "(亮巢學習中心), a fictional Hong Kong bilingual kids' learning centre used for a live demo. "
        "Answer in the first person using 'we'/'our'. Be warm, clear, and concise. "
        "Prefer answering from the knowledge excerpts even when coverage is partial — "
        "summarise what you can and invite a trial booking or staff follow-up for the rest. "
        "NEVER invent exact unpublished fees; if a guide range is in the excerpts, share it. "
        "Use short bullets when helpful. "
        "If the excerpts clearly do not contain what the parent needs, still give a brief helpful "
        "reply, then on its own last line write exactly: "
        "[[ADMIN_GAP: <what staff must look up>]] "
        "Never mention internal tools, admin to-do lists, spreadsheets, or that tag to the parent. "
        "If a human is needed, invite them to ask to speak with our team (someone reaches out in working hours)."
    ),
    "zh-HK": (
        "你係「亮巢學習中心」（BrightNest Learning Centre）嘅行政助理（課堂示範用虛構中心）。"
        "用第一人稱（「我哋」）友善簡潔作答。"
        "即使資料唔完整都要儘量根據知識庫回答，再提議試堂或職員跟進。"
        "唔好虛構未有列出嘅確實學費；如果有參考價就講參考價。"
        "如果知識庫明顯冇家長需要嘅資料，仍然要簡短回覆，然後喺最後一行單獨寫："
        "[[ADMIN_GAP: <職員需要查嘅資料>]]"
        "唔好向家長提及內部待辦、表格或該標記；如需真人，請邀請佢哋要求同團隊傾，工作時間會有人聯絡。"
    ),
    "zh-CN": (
        "你是「亮巢学习中心」（BrightNest Learning Centre）的行政助理（课堂演示用虚构中心）。"
        "用第一人称（「我们」）友善简洁作答。"
        "即使资料不完整也要根据知识库尽量回答，并提议试听或职员跟进。"
        "不要虚构未列出的确切学费；如有参考价请说明。"
        "如果知识库明显没有家长需要的资料，仍要简短回复，并在最后一行单独写："
        "[[ADMIN_GAP: <职员需要查阅的资料>]]"
        "不要向家长提及内部待办、表格或该标记；如需真人，请邀请他们要求与团队沟通，工作时间会有人联络。"
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


def sanitize_answer_meta(text: str, lang: str = "en") -> Dict[str, Any]:
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
    if history_context and history_context.strip():
        history_block = (
            "Recent conversation (use for follow-ups; do not invent facts not in the KB):\n"
            f"{history_context.strip()}\n\n"
        )
    return (
        f"{persona}\n\n"
        f"{history_block}"
        f"Internal knowledge base excerpts:\n{ctx}\n\n"
        f"Parent question: {question}\n\n"
        f"Answer:"
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


def compose_grounded_fact(
    question: str,
    fact_card: str,
    contexts: List[Dict[str, Any]],
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
    route: str = "centre_facts",
) -> Dict[str, Any]:
    """
    Tier-2 reply: structured fact card is privileged truth; RAG chunks may enrich
    tone / background only. Must not override fees, times, or policy in the card.
    """
    card = (fact_card or "").strip()
    out: Dict[str, Any] = {
        "answer": card,
        "provider": "passthrough",
        "composed": False,
        "adapted": False,
        "used_rag": bool(contexts),
    }
    if not card or not (question or "").strip():
        return out
    if not contexts:
        # No retrieve hits → fall back to tone-only adapt
        adapted = adapt_structured_fact(
            question, card, lang=lang, history_context=history_context, route=route
        )
        adapted["composed"] = False
        adapted["used_rag"] = False
        return adapted
    if not SETTINGS.has_dashscope:
        return out

    persona = PERSONA.get(lang, PERSONA["en"])
    history_block = ""
    if history_context and history_context.strip():
        history_block = (
            "Recent conversation (keep continuity):\n"
            f"{history_context.strip()}\n\n"
        )
    kb_bits = []
    for i, h in enumerate(contexts[:5], start=1):
        meta = h.get("metadata") or {}
        title = meta.get("title") or meta.get("source") or f"chunk-{i}"
        body = (h.get("text") or h.get("snippet") or "").strip()
        if not body:
            continue
        kb_bits.append(f"[{i}] {title}\n{body[:900]}")
    kb_block = "\n\n".join(kb_bits) if kb_bits else "(no KB excerpts)"
    prompt = (
        f"{persona}\n\n"
        "You are answering with TWO sources. Priority is absolute:\n"
        "1) STRUCTURED FACT CARD — verified centre ops truth. Fees, times, policy "
        "lines, and named rules here MUST appear unchanged if you mention them. "
        "Never contradict or invent over this card.\n"
        "2) KB EXCERPTS — optional colour / background / related policy. Use only "
        "to elaborate when consistent with the card. If a KB line conflicts with "
        "the card, ignore the KB line.\n"
        "Write a warm, spoken parent reply. No template/meta talk. "
        "No [[ADMIN_GAP]] / NO_ANSWER.\n\n"
        f"{history_block}"
        f"Route: {route}\n\n"
        f"STRUCTURED FACT CARD (privileged):\n---\n{card}\n---\n\n"
        f"KB EXCERPTS (secondary):\n---\n{kb_block}\n---\n\n"
        f"Parent question: {question}\n\n"
        "Reply:"
    )
    try:
        raw = _dashscope_generation_http(
            prompt,
            max_tokens=min(320, max(SETTINGS.gen_max_tokens, 280)),
            temperature=0.25,
        )
        meta = sanitize_answer_meta(raw, lang)
        composed = (meta.get("answer") or "").strip()
        if not composed or meta.get("used_fallback") or len(composed) < 20:
            return adapt_structured_fact(
                question, card, lang=lang, history_context=history_context, route=route
            ) | {"composed": False, "used_rag": True, "compose_fallback": "adapt"}
        out["answer"] = composed
        out["provider"] = "dashscope"
        out["composed"] = True
        out["adapted"] = True
        out["used_rag"] = True
        return out
    except Exception as e:
        print(f"[GEN] compose_grounded_fact failed ({e}); adapt fallback.", flush=True)
        fb = adapt_structured_fact(
            question, card, lang=lang, history_context=history_context, route=route
        )
        fb["composed"] = False
        fb["used_rag"] = True
        fb["compose_fallback"] = f"error:{e}"
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
    if history_context and history_context.strip():
        history_block = (
            "Recent conversation (keep continuity):\n"
            f"{history_context.strip()}\n\n"
        )
    prompt = (
        f"{persona}\n\n"
        "BrightNest is a COMP6708 live demo — sound like a sharp centre admin, "
        "not a policy PDF. You are given a verified fact card.\n"
        "Goals:\n"
        "- Answer the parent's actual question first (narrow when they named a "
        "programme, age, or detail).\n"
        "- Stay faithful to the card for fees, names, IDs, and times you mention; "
        "do not invent new published numbers or staff.\n"
        "- Drop irrelevant roster/fee rows; add a short helpful next step when natural.\n"
        "- Warm, spoken, concise. No template/meta talk. No [[ADMIN_GAP]] / NO_ANSWER.\n\n"
        f"{history_block}"
        f"Route: {route}\n\n"
        f"Fact card:\n---\n{card}\n---\n\n"
        f"Parent question: {question}\n\n"
        "Reply:"
    )
    try:
        raw = _dashscope_generation_http(
            prompt,
            max_tokens=min(220, SETTINGS.gen_max_tokens),
            temperature=0.25,
        )
        meta = sanitize_answer_meta(raw, lang)
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
    if SETTINGS.has_dashscope and contexts:
        try:
            raw = _dashscope_generation_http(
                _build_prompt(question, contexts, lang, history_context=history_context)
            )
            meta = sanitize_answer_meta(raw, lang)
            meta["provider"] = "dashscope"
            return meta
        except Exception as e:
            print(f"[GEN] DashScope Generation failed ({e}); extractive fallback.", flush=True)
    fb = _extractive_fallback(question, contexts, lang)
    meta = sanitize_answer_meta(fb, lang)
    meta["provider"] = "local_fallback"
    if not contexts:
        meta["used_fallback"] = True
    return meta


def _dashscope_generation_http(
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
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

    system = (
        "You are a helpful centre admin assistant. "
        "Never output [NO_ANSWER]. Always give a useful parent-facing reply."
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
    return _extract_text(data)


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

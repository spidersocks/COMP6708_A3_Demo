"""Shared cheap DashScope calls for demo arbiters (turbo, tiny max_tokens)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import httpx

from config import SETTINGS
from rag.dashscope_native import auth_headers, native_api_base

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def enabled() -> bool:
    return bool(SETTINGS.has_dashscope and SETTINGS.intent_arbiter_enabled)


def complete_text(
    prompt: str,
    *,
    system: str = "Be brief and follow instructions exactly.",
    max_tokens: int = 120,
    temperature: float = 0.2,
) -> Optional[str]:
    if not enabled():
        return None
    model = SETTINGS.intent_arbiter_model or "qwen-turbo"
    url = f"{native_api_base()}/services/aigc/text-generation/generation"
    payload = {
        "model": model,
        "input": {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        },
        "parameters": {
            "result_format": "message",
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    }
    try:
        with httpx.Client(timeout=SETTINGS.intent_arbiter_timeout_s) as client:
            resp = client.post(url, headers=auth_headers(), json=payload)
            data = resp.json()
        if resp.status_code != 200:
            return None
        return _extract_text(data) or None
    except Exception as e:
        print(f"[CHEAP_LLM] failed: {e}", flush=True)
        return None


def complete_json(
    prompt: str,
    *,
    system: str = "Return only one compact JSON object. No markdown.",
    max_tokens: int = 100,
) -> Optional[Dict[str, Any]]:
    text = complete_text(prompt, system=system, max_tokens=max_tokens, temperature=0.0)
    if not text:
        return None
    return parse_json_object(text)


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = _JSON_RE.search(raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


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
    return str(output or "")
